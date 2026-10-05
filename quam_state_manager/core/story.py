"""The calibration story: one run = one card, whoever ran it (docs/173 S1).

Since hub S6 (docs/281) the spine is the chip LEDGER, read one project-zone
day at a time through ``hub_query.timeline``:

  * a run event -> a run card. What it changed is the event's exact rows
    (the run's saved state vs the ledger state just before it), each with
    its op; the ledger's first event is the starting state, counted only.
  * an SM write that landed -> a write card: who, the door, every entry SM
    recorded at the door, undo links named by door and time, UNDONE flags.
  * an agent run that left no run folder (``agent_runs`` index rows merged
    with Registry metas) -> its own card (C-17).

Onto a run card the story attaches, as before:

  * journal lines (``core/journal.py``): by ``#run`` when the writer named
    it, else by time window ``[run_start-60s, run_end+300s]`` plus target
    overlap -- so a hook line that never learned the run id still lands.
  * the parameters that changed vs the previous run of the same node.
  * the deterministic gate verdict, anchored on the run's OWN saved state
    (``<run>/quam_state/state.json``) so the answer is a property of the run
    and does not drift as the chip moves on.
  * the author: a person's claim, else certain when SM ran the node
    (``agent_runs/index.jsonl``), else inferred from a hook event in the
    window, else the event's actor, else ``unknown`` -- never ``qualibrate``
    by default, because a terminal `python node.py` looks the same from here.

The undo journal is not history (it holds working-copy saves): it never
makes a write card; edits it holds from before the ledger recorded SM writes
are only counted. The unbound DatasetStore path (no ledger) remains for
standalone callers; it is never the active chip's history source.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Mapping
from functools import lru_cache
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from quam_state_manager.core.loader import natural_key
from quam_state_manager.core import journal as journal_mod
from quam_state_manager.core import run_time

logger = logging.getLogger(__name__)

GATES_REV = "2026-09-06a"          # bump when gates.py / families.py change what a verdict means
_BEFORE_S = 60.0
_AFTER_S = 300.0
_ENTRY = re.compile(r"^- \*\*(\d{2}:\d{2}:\d{2})\*\* `([^`]+)` ?(.*)$")
_RUN_TOKEN = re.compile(r"\s*·\s*run #(\d+)")
_PATH_TOKEN = re.compile(r"`([A-Za-z_][\w-]*(?:\.[\w-]+)+)`")
_NODE_RE = re.compile(r"python[^\s]*\s+(?:-m\s+\S+\s+)?(?:\"[^\"]*[\\/])?([^\s;&|\"']+)\.py\b")
_HUB_GATES = OrderedDict()
_HUB_GATES_LOCK = threading.Lock()


# ------------------------------------------------------------ journal side

def parse_journal(text: str, day: str) -> list[dict]:
    """The bullets of one day file -> entries with epoch timestamps."""
    out: list[dict] = []
    cur: dict | None = None
    for raw in (text or "").splitlines():
        m = _ENTRY.match(raw)
        if m:
            hhmmss, kind, body = m.group(1), m.group(2), m.group(3)
            run_id = None
            rm = _RUN_TOKEN.search(body)
            if rm:
                run_id = int(rm.group(1))
                body = body[:rm.start()] + body[rm.end():]
            paths = list(dict.fromkeys(_PATH_TOKEN.findall(body)))   # a path named in prose AND in the tail counts once
            # the trailing " · `a.b`, `c.d`" segment is metadata, not prose
            if paths:
                tail = body.rfind(" · `")
                if tail >= 0 and all(t in body[tail:] for t in paths):
                    body = body[:tail]
            try:
                ts = datetime.strptime(f"{day} {hhmmss}", "%Y-%m-%d %H:%M:%S").timestamp()
            except (ValueError, OSError, OverflowError):
                # docs/191 A02: `.timestamp()` raises OSError on Windows for a
                # local date before the epoch, and every caller here already
                # treats an unparseable time as "no time" rather than an error.
                ts = 0.0
            cur = {"ts": ts, "time": hhmmss, "kind": kind, "text": body.strip(), "run_id": run_id,
                   "paths": paths, "because": None}
            out.append(cur)
            continue
        if cur is None:
            continue
        s = raw.strip()
        if s.startswith("- because:"):
            cur["because"] = s[len("- because:"):].strip()
        elif raw.startswith("  ") and s and not s.startswith("- "):
            cur["text"] = (cur["text"] + "\n" + s).strip()
    return out


def _targets_in(entry: dict, targets: list[str]) -> bool:
    hay = (entry.get("text") or "") + " " + " ".join(entry.get("paths") or [])
    return any(re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", hay) for t in targets)


# --------------------------------------------------------------- run side

# docs/262: a run's time is read ONE way -- ``run_time`` (timefmt.run_instant,
# docs/256). The folder digits (``date`` + ``time``) are the acquisition PC's
# wall clock: read in the SERVER's zone they were 13 h off for a -04:00
# archive on a +09:00 machine, against hook stamps, journal lines and undo
# units that are all true epochs. Every helper below answers in epoch
# seconds / UTC microseconds of the run's INSTANT.

def _run_info(ds: Any, run_id: Any) -> Any:
    """The store's ``RunInfo`` behind a row dict (it carries the instant the
    DatasetStore resolved, archive offset included), or ``None``."""
    runs = getattr(ds, "runs", None) if ds is not None else None
    if not isinstance(runs, Mapping) or run_id is None:
        return None
    try:
        return runs.get(int(run_id))
    except (TypeError, ValueError):
        return None


def _clock_folder(day: Any, hms: Any) -> str | None:
    """``<YYYY-MM-DD>/_<HHMMSS>``: a row's folder clock in the run-folder shape
    ``timefmt`` reads, for a row that carries ``date``/``time`` but no folder."""
    if not isinstance(day, str) or not isinstance(hms, str):
        return None
    digits = hms.replace(":", "")
    return f"{day}/_{digits}" if len(digits) == 6 and digits.isdigit() else None


def row_instant(run: Any, ds: Any = None) -> tuple[int | None, str]:
    """``(utc_us, quality)`` of a run however the caller holds it (docs/262):

    * a ``RunInfo`` (any non-mapping) -> :func:`run_time.instant_of`;
    * a row dict whose ``run_id`` the store ``ds`` knows -> that RunInfo;
    * a row that already carries ``instant_us`` (:func:`with_instants`);
    * else :func:`run_time.resolve` over the row's ``created_at`` /
      ``run_end`` / ``folder_path``; a row with only ``date`` + ``time``
      reads that folder clock in the machine zone (``assumed_local``) --
      [derived] with no folder there is no archive to take an offset from,
      which is the run_instant rule's own last rung.
    """
    if not isinstance(run, Mapping):
        return run_time.instant_of(run)
    info = _run_info(ds, run.get("run_id"))
    if info is not None:
        return run_time.instant_of(info)
    if "instant_us" in run:
        try:
            v = run["instant_us"]
            return (int(v), str(run.get("instant_q") or "offset")) if v is not None \
                else (None, str(run.get("instant_q") or "none"))
        except (TypeError, ValueError):
            pass
    folder = run.get("folder_path")
    if folder:
        return run_time.resolve(run.get("created_at"), run.get("run_end"), folder)
    return run_time.resolve(run.get("created_at"), run.get("run_end"),
                            _clock_folder(run.get("date"), run.get("time")),
                            offset_hint=None, read_node=False)


def run_epoch(run: Any, ds: Any = None) -> float | None:
    """Epoch seconds of the run's instant (:func:`row_instant`), or ``None``."""
    utc_us, _q = row_instant(run, ds)
    return None if utc_us is None else utc_us / 1_000_000


def _iso_epoch(text: Any) -> float | None:
    """Epoch seconds of ONE node.json ISO field (``run_start``/``run_end``):
    :func:`run_time.iso_instant` -- an offset decides, a naive one is read in
    the machine zone (what ``datetime.fromisoformat(..).timestamp()`` did)."""
    utc_us, _q = run_time.iso_instant(text)
    return None if utc_us is None else utc_us / 1_000_000


def start_epoch(run: Any, ds: Any = None) -> float | None:
    """When the run STARTED, for the windows that care about the start (the
    journal attach window ``[run_start-60s, run_end+300s]``, the claim line):
    its own ``run_start``, else the run's instant. [derived] the fallback
    used to be the folder digits read in the server's zone; the instant is
    the same clock every other surface dates the run by (docs/262)."""
    rs = run.get("run_start") if isinstance(run, Mapping) else getattr(run, "run_start", None)
    start = _iso_epoch(rs)
    return start if start is not None else run_epoch(run, ds)


def with_instants(rows: list[dict], ds: Any) -> list[dict]:
    """``ds.list_runs()`` rows + ``instant_us`` / ``instant_q`` from the store,
    for a consumer that only receives the rows (the agent run engine)."""
    out = []
    for row in rows or []:
        utc_us, q = row_instant(row, ds)
        out.append({**row, "instant_us": utc_us, "instant_q": q})
    return out


def _outcome(outcomes: dict | None) -> str | None:
    if not outcomes:
        return None
    vals = [str(v).lower() for v in outcomes.values()]
    if any("fail" in v for v in vals):
        return "failed"
    if all("success" in v or v == "ok" for v in vals):
        return "ok"
    return "mixed"


def _folder_key(folder) -> str:
    return hashlib.sha1(str(Path(folder).resolve()).lower().encode("utf-8")).hexdigest()[:12]


def _node_of(summary: str) -> str | None:
    m = _NODE_RE.search(summary or "")
    return m.group(1).split("/")[-1].split("\\")[-1] if m else None


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


# ------------------------------------------------------------- the author

def agent_runs_index_path(instance_path) -> Path:
    return Path(instance_path) / "agent_runs" / "index.jsonl"


def record_agent_run(instance_path, rec: dict) -> None:
    """SM ran this node itself (run_node): the one certain attribution."""
    p = agent_runs_index_path(instance_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def load_agent_runs(instance_path, chip: str | None = None) -> dict[int, dict]:
    """``{run_id: record}``; with ``chip`` only that chip's rows (review R3-2:
    run ids are per data folder, so another chip's #104 must never claim this
    chip's #104). Rows with no run_id are skipped here -- see
    ``unattributed_agent_runs``."""
    out: dict[int, dict] = {}
    try:
        for line in agent_runs_index_path(instance_path).read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if chip is not None and r.get("chip") not in (chip, None):
                continue
            if r.get("run_id") is not None:
                out[int(r["run_id"])] = r
    except OSError:
        pass
    return out


def unattributed_agent_runs(instance_path, chip: str | None = None, *, since: float = 0.0) -> list[dict]:
    """The agent's runs that produced no run id (the writeback landed late):
    a run folder that MATCHES one of these by node + time is the agent's, not
    a person's (review R3-2: the agent was blocking itself as human_active)."""
    out: list[dict] = []
    try:
        for line in agent_runs_index_path(instance_path).read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("run_id") is not None or float(r.get("ts") or 0) < since:
                continue
            if chip is not None and r.get("chip") not in (chip, None):
                continue
            out.append(r)
    except OSError:
        pass
    return out


def claims_path(instance_path, chip: str) -> Path:
    return Path(instance_path) / "story_claims" / (journal_mod._safe_key(chip) + ".json")


def load_claims(instance_path, chip: str) -> dict[str, dict]:
    try:
        return json.loads(claims_path(instance_path, chip).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_KEEP = object()


def claim_run(instance_path, chip: str, run_id: int, *, author: str, note=_KEEP, uid: str | None = None) -> dict:
    """A person says "I ran this one" (or corrects the author).

    A correction REPLACES the record, so an omitted ``note`` used to delete the
    note the last claim left -- a person fixing a misspelt name lost the sentence
    they had written, and nothing on screen said so. A caller that says nothing
    about the note now keeps it; only an explicitly empty note clears one, which
    is a person emptying a box they could see (docs/120). The previous record is
    returned as ``prev`` so the journal line can name what it overrode.
    """
    p = claims_path(instance_path, chip)
    p.parent.mkdir(parents=True, exist_ok=True)
    claims = load_claims(instance_path, chip)
    # docs/281 review: a run number is per data folder -- the card's own
    # identity ("<folder key>:<number>") keys the claim when the page has one
    key = str(uid) if uid else str(int(run_id))
    prev = claims.get(key) or None
    if note is _KEEP:
        kept = (prev or {}).get("note")
    else:
        kept = (note or "").strip() or None
    rec = {"author": author, "note": kept,
           "ts": datetime.now().isoformat(timespec="seconds")}
    claims[key] = rec
    p.write_text(json.dumps(claims, indent=1, ensure_ascii=False), encoding="utf-8")
    return dict(rec, prev=prev)


def _author_of(run: dict, start: float | None, end: float | None, *, agent_runs: dict, events: list[dict],
               claims: dict) -> tuple[str, str, dict | None]:
    """(author, certainty, agent-run record). ``by_<backend>`` / ``human:<name>``
    / ``unknown``. Certainty: certain | inferred | claimed | none."""
    rid = int(run["run_id"])
    if str(rid) in claims and claims[str(rid)].get("author"):
        return str(claims[str(rid)]["author"]), "claimed", agent_runs.get(rid)
    ar = agent_runs.get(rid)
    if ar:
        return ar.get("actor") or f"by_{ar.get('backend') or 'agent'}", "certain", ar
    if start is not None:
        lo = start - _BEFORE_S
        hi = (end if end is not None else start) + _AFTER_S
        want = _norm(run.get("experiment_name") or "")
        for e in events:
            if e.get("hook_event_name") not in ("PostToolUse", "PostToolUseFailure") or e.get("tool_name") != "Bash":
                continue
            ts = float(e.get("ts") or 0)
            if not (lo <= ts <= hi):
                continue
            node = _node_of(e.get("summary") or "")
            if node and want and (_norm(node) in want or want in _norm(node)):
                return f"by_{e.get('backend') or 'agent'}", "inferred", None
    return "unknown", "none", None


# -------------------------------------------------------------- the gate

def _walk(doc: Any, dotted: str) -> Any:
    node = doc
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise KeyError(dotted)
    if isinstance(node, str) and node.startswith("#/"):
        return _walk(doc, node[2:].replace("/", "."))
    return node


def run_anchor_state(run: dict) -> dict | None:
    """The state the run itself saved -- the only anchor that never moves."""
    folder = Path(run.get("folder_path") or "")
    for cand in (folder / "quam_state" / "state.json", folder / "state.json"):
        try:
            if cand.exists():
                return json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def gate_for_run(instance_path, run: dict, *, folder_key: str, compute: Callable | None = None) -> dict:
    """``{verdict, reason, family, anchor, rev}``; cached per (run, GATES_REV).
    ``verdict`` is pass / suspect / fail, or ``unknown`` with the reason when
    the run carries no state to anchor on or no family applies."""
    cache = Path(instance_path) / "story_cache" / folder_key / f"{int(run['run_id'])}.json"
    try:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("rev") == GATES_REV:
            return c
    except (OSError, ValueError):
        pass
    result = (compute or _compute_gate)(run)
    result["rev"] = GATES_REV
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, default=str), encoding="utf-8")
    except OSError:
        pass
    return result


def _compute_gate(run: dict) -> dict:
    from quam_state_manager.core.autofit import families as fam_mod
    from quam_state_manager.core.autofit import gates
    fam = fam_mod.family_for(run.get("experiment_name") or "")
    if fam is None:
        return {"verdict": "unknown", "reason": "no autofit family for this node", "family": None, "anchor": None}
    doc = run_anchor_state(run)
    if doc is None:
        return {"verdict": "unknown", "reason": "the run saved no state to anchor on",
                "family": getattr(fam, "key", None), "anchor": None}
    run_obj = dict(run)
    run_obj["folder_path"] = Path(run["folder_path"])
    targets = list(run.get("qubits") or []) + list(run.get("qubit_pairs") or [])
    verdicts = gates.evaluate_run(run_obj, fam, targets, current_value_of=lambda p: _walk(doc, p))
    per = {t: {"verdict": v.verdict, "failure_mode": v.failure_mode, "reasons": list(v.reasons)}
           for t, v in verdicts.items()}
    order = {"fail": 3, "suspect": 2, "pass": 1}
    worst = max(per.values(), key=lambda x: order.get(x["verdict"], 0), default=None)
    if worst is None:
        return {"verdict": "unknown", "reason": "no targets", "family": getattr(fam, "key", None), "anchor": "run"}
    reason = (worst["reasons"][0] if worst["reasons"] else worst.get("failure_mode")) or ""
    return {"verdict": worst["verdict"], "reason": reason, "family": getattr(fam, "key", None),
            "anchor": "run", "per_target": per}


# --------------------------------------------------------------- the day

def _params_diff(cur: dict | None, prev: dict | None) -> list[dict]:
    if not isinstance(cur, dict) or not isinstance(prev, dict):
        return []
    out = []
    for k in sorted(set(cur) | set(prev)):
        a, b = prev.get(k), cur.get(k)
        if a != b and k not in ("qubits", "qubit_pairs", "targets_name"):
            out.append({"key": k, "old": a, "new": b})
    return out


def _writes_for_run(hm, active_path, run_id: int) -> list[dict]:
    """The chip values this run changed: the change points of its snapshot."""
    if hm is None or not active_path:
        return []
    try:
        snaps = [s for s in hm.list_snapshots(active_path) if s.run_id == run_id]
        if not snaps:
            return []
        groups = hm.leaf_change_groups(active_path, limit_snaps=1, rows_per_snap=200, at_ts=snaps[0].timestamp)
    except Exception:  # noqa: BLE001 -- a missing index costs the writes line only
        logger.debug("writes_for_run failed", exc_info=True)
        return []
    rows = []
    for g in groups:
        for r in g.get("rows") or []:
            rows.append({"path": r.get("path"), "old": r.get("previous"), "new": r.get("value"),
                         "is_first": bool(r.get("is_first")),
                         "op": "first" if r.get("is_first") else "set"})
    return rows


_SHORT_FAMILY = {
    "time_of_flight": "ToF", "resonator_spectroscopy": "Res spec", "resonator_spectroscopy_vs_power": "Res/power",
    "resonator_spectroscopy_vs_flux": "Res/flux", "qubit_spectroscopy": "Qubit spec",
    "qubit_spectroscopy_vs_power": "Qubit/power", "qubit_spectroscopy_vs_flux": "Qubit/flux", "power_rabi": "Rabi",
    "ramsey": "Ramsey", "t1": "T1", "echo": "Echo", "readout_frequency_optimization": "RO freq",
    "readout_power_optimization": "RO power", "iq_blobs": "IQ blobs", "chevron_11_02": "CZ chevron",
    "cz_conditional_phase": "CZ phase", "drag": "DRAG",
}
_SHORT_BY_NODE = [
    # Scanned IN ORDER, first match wins, so every SPECIFIC family sits above
    # the generic one it would otherwise be swallowed by. The list is derived
    # from the 165 distinct node names this app has actually recorded across
    # the archives on this machine -- not invented. Before the specific rows
    # existed, five different resonator families all read "Res spec" and an
    # interleaved two-qubit RB run read "CZ", which is exactly the information
    # a Trends hover exists to give.
    ("time_of_flight", "ToF"),
    # -- spectroscopy: the sweep variants before the plain ones
    ("resonator_spectroscopy_vs_coupler_flux", "Res/coupler"),
    ("resonator_spectroscopy_vs_power", "Res/power"),
    ("resonator_spectroscopy_vs_flux", "Res/flux"),
    ("qubit_spectroscopy_vs_coupler_flux", "Qubit/coupler"),
    ("qubit_spectroscopy_vs_power", "Qubit/power"),
    ("qubit_spectroscopy_vs_flux", "Qubit/flux"),
    ("qubit_spectroscopy_e_to_f", "Qubit spec ef"),
    ("qubit_spectroscopy_ef", "Qubit spec ef"),
    # -- readout
    ("readout_freq", "RO freq"), ("readout_frequency", "RO freq"),
    ("readout_weights", "RO weights"), ("readout_power", "RO power"),
    ("readout_amp", "RO amp"), ("readout_chain", "RO chain"),
    ("iq_blob", "IQ blobs"), ("twpa", "TWPA"),
    ("fullscale_dbm", "FSP adjust"),
    # -- benchmarking. "interleaved" before "standard" before the bare word,
    # and all three above ("cz", "CZ") -- an interleaved CZ RB run is an RB
    # measurement, not a CZ calibration.
    ("two_qubit_interleaved", "2Q IRB"), ("interleaved_cz_rb", "2Q IRB"),
    ("two_qubit_standard_rb", "2Q SRB"),
    ("two_qubit_confusion", "2Q confusion"), ("2q_confusion", "2Q confusion"),
    ("single_qubit_randomized_benchmarking_interleaved", "1Q IRB"),
    ("single_qubit_randomized_benchmarking", "1Q RB"),
    ("rb_success_exit", "RB exit"), ("standard_rb", "SRB"),
    ("xeb", "XEB"), ("bell_state", "Bell state"), ("all_xy", "AllXY"),
    # -- coherence, sweep variants first
    ("t1_vs_flux", "T1/flux"), ("t2star_vs_flux", "T2*/flux"),
    ("echo_vs_flux", "Echo/flux"),
    ("ramsey_vs_coupler_flux", "Ramsey/coupler"), ("ramsey_vs_flux", "Ramsey/flux"),
    # an e-f Ramsey writes T2ramsey_ef, not T2ramsey (value_writer reads
    # this label to decide which runs measure which leaf)
    ("ramsey_ef", "Ramsey ef"),
    # -- flux / distortion / delays
    ("cryoscope", "Cryoscope"),
    ("coupler_flux_long_distortion", "Coupler flux long"),
    ("coupler_flux_short_distortion", "Coupler flux short"),
    ("flux_long_distortion", "Flux long"), ("flux_short_distortion", "Flux short"),
    ("flux_amplitude_to_frequency", "Flux to freq"),
    ("coupler_zero_point", "Coupler zero"),
    ("xy_coupler_delay", "XY-coupler delay"), ("xyz_delay", "XYZ delay"),
    # -- two-qubit gate work
    ("rabi_chevron", "Rabi chevron"), ("cz_chevron", "CZ chevron"),
    ("chevron_11_02", "CZ chevron"), ("chevron_1102", "CZ chevron"),
    ("leakage", "Leakage"), ("jazz", "JAZZ"), ("snz", "SNZ"), ("zz_off", "ZZ off"),
    ("phase_compensation", "CZ phase comp"),
    ("conditional_phase", "CZ phase"),
    # -- cross-resonance: these carry "rabi" in their names and were reading
    # as plain Rabi runs, which is a different experiment on a different chip.
    ("cr_hamiltonian", "CR tomography"), ("cr_correction", "CR phase"),
    ("cr_pulse_rabi", "CR rabi"), ("cr_time_rabi", "CR rabi"),
    ("crosstalk", "Crosstalk"), ("drag", "DRAG"),
    # -- the generic tail
    ("t2echo", "Echo"), ("echo", "Echo"), ("t1", "T1"), ("cz", "CZ"),
    ("rabi", "Rabi"), ("ramsey", "Ramsey"),
    ("qubit_spec", "Qubit spec"), ("resonator_spec", "Res spec"),
]


def _short_family(fam_key: str | None, label: str | None, node: str | None) -> str:
    """The family's SHORT name for the per-target timeline (customer feedback
    2026-09-08: the strip named every run in full and became a wall). A known
    family maps to a fixed short form; a node no family knows yet is matched
    by its own name; anything else drops the numeric prefix and is cut at 18."""
    if fam_key and fam_key in _SHORT_FAMILY:
        return _SHORT_FAMILY[fam_key]
    low = str(node or label or "").lower()
    for needle, short in _SHORT_BY_NODE:
        if needle in low:
            return short
    s = str(label or node or "").strip()
    s = re.sub(r"^\d+[a-z]?_", "", s).replace("_", " ")
    return s if len(s) <= 18 else s[:17] + "…"


# A node's leading id segments: a number with an optional letter -- ``05``,
# ``08b``, ``71a``. The ``1Q`` / ``2Q`` scope token some labs prefix is the
# SAME shape (a digit and a letter), so it needs no alternative of its own;
# a branch spelling it out separately was dead, and case-insensitivity is
# what makes the token match at all.
_ID_SEG = re.compile(r"^\d+[a-z]?$", re.IGNORECASE)


def node_label(node: str | None) -> str:
    """``"05_resonator_spectroscopy_vs_power"`` -> ``"05 Res/power"``.

    THE NUMBER IS THE IDENTITY (customer, 2026-09-09). A calibration set holds
    five resonator families and four qubit-spectroscopy families; the short
    family name alone says which KIND of measurement a point came from, and the
    number is what says WHICH ONE -- so a hover that means to send a person to
    the actual run has to carry it.

    Deliberately NOT :func:`_short_family` itself. That one feeds the journal's
    per-target strip, which merges consecutive runs of one family into
    ``Res spec 159 160 161``; give it the number and every run becomes its own
    segment and the strip is a wall again, which is the exact complaint that
    created it the day before. Two readers, two labels, one family vocabulary.

    Only a LEADING id counts -- the scan stops at the first segment that is
    not one, so ``13_drag_calibration_180_minus_180`` is ``13 DRAG`` and not
    ``13 180 180 DRAG``. A scope token the family name already says is
    dropped (``2Q_37b_two_qubit_interleaved_cz_rb`` -> ``37b 2Q IRB``, not
    ``2Q 37b 2Q IRB``). A node with no number keeps just its family name --
    invented numbering would be worse than none.
    """
    if not node:
        return ""
    fam = _short_family(None, None, node)
    ident: list[str] = []
    for seg in str(node).split("_"):
        if _ID_SEG.match(seg):
            ident.append(seg)
        else:
            break
    while ident and fam.lower().startswith(ident[0].lower()):
        ident.pop(0)
    return (" ".join(ident) + " " + fam).strip() if ident else fam


def _timeline(cards: list[dict]) -> dict[str, list[dict]]:
    """Per target, in time order: consecutive runs of ONE family merged into a
    segment ``{family (short), full, steps}`` so the strip reads
    ``Res spec 159 160 161 · ToF 165 166`` instead of one pill per run."""
    out: dict[str, list[dict]] = {}
    for c in cards:
        if c.get("kind") != "run":
            continue
        short = c.get("family_short") or _short_family(c.get("family"), c.get("family_label"), c.get("node"))
        full = c.get("family_label") or c.get("node") or ""
        step = {"run_id": c["run_id"], "card_id": c.get("card_id") or f"card-{c['run_id']}", "family": full, "outcome": c.get("outcome"),
                "gate": (c.get("gate") or {}).get("verdict"), "author": c.get("author")}
        for t in c.get("targets") or []:
            segs = out.setdefault(t, [])
            if segs and segs[-1]["family"] == short:
                segs[-1]["steps"].append(step)
            else:
                segs.append({"family": short, "full": full, "steps": [step]})
    return dict(sorted(out.items(), key=lambda kv: natural_key(kv[0])))


@lru_cache(maxsize=4096)
def _family_label(name: str) -> tuple[str | None, str]:
    try:
        from quam_state_manager.core.autofit import families as fam_mod
        fam = fam_mod.family_for(name or "")
        if fam is not None:
            return getattr(fam, "key", None), getattr(fam, "label", name)
    except Exception:  # noqa: BLE001
        pass
    return None, name


def _empty_day(chip, day, history, instance_path=None):
    def lines(key):
        return parse_journal(journal_mod.read(instance_path, key, day), day) if instance_path else []
    return {"chip": chip, "day": day, "cards": [], "loose": lines(chip), "unassigned": lines("unassigned"),
            "digest": {}, "timeline": {}, "counts": _counts([]), "history": history}


def _hub_clock(instant, ledger, fmt="%H:%M:%S"):
    from quam_state_manager.core.hub_index import _binding
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(instant / 1e6, ZoneInfo(_binding(ledger)[1])).strftime(fmt)


def entry_op(row: Mapping) -> str:
    """What one change row did: ``add`` / ``set`` / ``gone`` / ``retarget``.

    Ledger rows carry their ``op``; an SM entry marks a key it created or
    deleted (``hub_entries.entry_of``). A missing value is never shown as
    ``None``: the op says whether the key existed before or after."""
    op = row.get("op")
    if op:
        return str(op)
    if row.get("created"):
        return "add"
    if row.get("deleted"):
        return "gone"
    if row.get("is_first"):
        return "first"
    return "set"


# ------------------------------------------------------- the ledger path (docs/281)

#: An SM write's door (``src``), else its kind, in plain words.
_DOOR_LABEL = {
    "apply": "Applied to the chip", "keep_mine": "Kept mine, overwrote live",
    "auto_apply": "Auto-apply session", "pull_apply": "Pull & apply (merge)",
    "apply_staged": "Applied a staged version", "revert_last_apply": "Reverted the last apply",
    "dataset_apply": "Applied a run's state", "cli_set": "Set from the command line",
    "restore_live": "Restored a saved version", "ctrl_z": "Undo (Ctrl+Z)",
    "ctrl_shift_z": "Redo (Ctrl+Shift+Z)", "approval": "Approved agent write",
}
_KIND_LABEL = {"sm_apply": "Applied to the chip", "agent": "Agent write", "autofit": "Auto Calibrate",
               "restore": "Restored a saved version", "undo": "Undo", "redo": "Redo"}


def write_label(kind: str | None, src: str | None) -> str:
    """One compact English name for an SM write (docs/281: never raw door ids)."""
    src = str(src or "")
    if kind == "undo":
        return "Undo (Ctrl+Z)" if src == "ctrl_z" else "Undo"
    if kind == "redo":
        return "Redo (Ctrl+Shift+Z)" if src == "ctrl_shift_z" else "Redo"
    if src.startswith("autofit_"):
        return "Auto Calibrate " + src.split(":", 1)[0][len("autofit_"):]
    return _DOOR_LABEL.get(src) or _KIND_LABEL.get(str(kind or "")) or "SM write"


#: An agent run's failure class, in words (docs/249 classes).
_CLASS_TEXT = {
    "hardware_contention": "the instrument was busy", "host_unreachable": "the QM host was unreachable",
    "node_error": "the node failed", "timeout": "it timed out", "cancelled": "it was stopped",
    "skipped": "it was skipped", "interrupted": "SM stopped before it ended",
    "unattributed": "its run folder was not found", "ok": "it finished, but its run folder was not found",
}
_CLASS_SHORT = {"hardware_contention": "busy", "host_unreachable": "unreachable", "node_error": "failed",
                "timeout": "timed out", "cancelled": "stopped", "skipped": "skipped",
                "interrupted": "interrupted", "unattributed": "no folder", "ok": "no folder"}
#: Classes whose node never ran (docs/253 gate refusals are recorded the same way).
_NOT_RUN = {"skipped"}
#: Only these may be matched to a run folder by node and time: an attempt that
#: failed, was stopped or was refused keeps its own card (docs/281 review).
_MAY_HAVE_A_FOLDER = ("ok", "unattributed")


def _agent_text(card: dict) -> str:
    who, node = card["author"], card["node"]
    on = " on " + " ".join(card["targets"]) if card["targets"] else ""
    phrase = _CLASS_TEXT.get(card["classification"]) or f"it was refused ({card['classification']})"
    if card["outcome"] == "not run":
        return f"{who} did not run {node}{on}: {phrase}."
    if card.get("missing_run_id") is not None:
        return f"{who} ran {node}{on}. Its run folder #{card['missing_run_id']} is not in the chip history."
    return f"{who} ran {node}{on}. No run folder: {phrase}."


def _flags_of(event: Mapping) -> list[dict]:
    """The ledger's facts about one run event, each a badge and one line."""
    from quam_state_manager.core import hub_store as hs
    flags = int(event.get("flags") or 0)
    out = []
    if event.get("error"):
        out.append({"key": "no-state", "label": "no state",
                    "note": f"Its saved state could not be read ({event['error']})."})
    elif flags & hs.CHIP_UNCERTAIN:
        out.append({"key": "other-chip", "label": "other chip?",
                    "note": "Its saved state may be another chip's: its identity does not match this "
                            "chip's history. Its rows are not listed and it is not counted with the day's runs."})
    for bit, key, label, note in (
            (hs.REVERTS_TO_EARLIER, "repeats", "repeats earlier",
             "Its saved state repeats an earlier state in the history."),
            (hs.OVERLAPS_SM_WRITE, "over-sm-write", "saved over an SM write",
             "It started before an SM write and saved after it, so its save went over that write."),
            (hs.TIME_ASSUMED, "time-assumed", "time assumed",
             "Its time is assumed: the run's record carries no time zone."),
            (hs.REWRITTEN, "rewritten", "rewritten",
             "Its saved files were rewritten after the history first read them."),
            (hs.SOURCE_GONE, "folder-gone", "folder gone",
             "Its run folder is gone; the history keeps what it saved."),
            (hs.NODE_UNREADABLE, "node-unreadable", "node.json unreadable",
             "Its node.json could not be read; its time comes from the folder name.")):
        if flags & bit:
            out.append({"key": key, "label": label, "note": note})
    return out


def _norm_folder(path) -> str:
    # string-only (no filesystem walk per run, docs/281): absolute, normalised, case-folded
    return os.path.normcase(os.path.abspath(str(path))) if path else ""


def _file_sig(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


#: What a run card reads from its own folder, per folder and both files'
#: (mtime, size): a day re-renders from RAM, a rewritten file is re-read.
_RUN_FACTS: OrderedDict = OrderedDict()
_RUN_FACTS_LOCK = threading.Lock()
_RUN_FACTS_MAX = 50_000


def _read_json_dict(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _run_facts(folder: str) -> dict:
    """``node.json`` + ``data.json`` of a run folder, read the way the
    Datasets scanner reads them (one reading of targets, parameters and
    figures)."""
    from quam_state_manager.core.dataset import _calc_duration, _extract_figure_names
    from quam_state_manager.core.scanner import _with_pair_qubits, node_parameters
    node_p, data_p = os.path.join(folder, "node.json"), os.path.join(folder, "data.json")
    token = (_file_sig(node_p), _file_sig(data_p))
    with _RUN_FACTS_LOCK:
        hit = _RUN_FACTS.get(folder)
        if hit is not None and hit[0] == token:
            _RUN_FACTS.move_to_end(folder)
            return hit[1]
    node, payload = _read_json_dict(node_p), _read_json_dict(data_p)
    meta, data = node.get("metadata") or {}, node.get("data") or {}
    raw = data.get("parameters") or {}
    model = raw.get("model", {}) if isinstance(raw, dict) else {}
    parameters = node_parameters(data)
    qubits = model.get("qubits") or parameters.get("qubits") or []
    qubits = [qubits] if isinstance(qubits, str) else (qubits if isinstance(qubits, list) else [])
    qubits, pairs = _with_pair_qubits(qubits, model.get("qubit_pairs") or parameters.get("qubit_pairs"))
    facts = {"run_start": meta.get("run_start"), "run_end": meta.get("run_end"),
             "duration_s": _calc_duration(meta.get("run_start"), meta.get("run_end")),
             "parameters": parameters, "qubits": qubits, "qubit_pairs": pairs,
             "outcomes": data.get("outcomes") or {}, "status": meta.get("status") or "",
             "figure_names": _extract_figure_names(payload), "fit_results": payload.get("fit_results") or {}}
    with _RUN_FACTS_LOCK:
        _RUN_FACTS[folder] = (token, facts)
        while len(_RUN_FACTS) > _RUN_FACTS_MAX:
            _RUN_FACTS.popitem(last=False)
    return facts


def _hub_run(event, ds):
    """One run event plus what its folder says. A run Datasets holds (same
    folder) is read from the Datasets index in RAM; any other is read from its
    own folder, cached (docs/281: never a path resolve per run)."""
    folder = os.path.join(event["root_path"] or "", event["rel_path"] or "")
    info = None
    runs = getattr(ds, "runs", None) if ds is not None else None
    if isinstance(runs, Mapping):
        cand = runs.get(event["run_id"])
        if cand is not None and _norm_folder(getattr(cand, "folder_path", None)) == _norm_folder(folder):
            info = cand
    elif ds is not None and hasattr(ds, "get_run"):
        cand = ds.get_run(event["run_id"])
        if cand and _norm_folder(cand.get("folder_path")) == _norm_folder(folder):
            info = SimpleNamespace(**{k: cand.get(k) for k in (
                "run_start", "run_end", "parameters", "qubits", "qubit_pairs", "outcomes", "status",
                "figure_names", "time")}, run_duration_s=cand.get("duration_s"), folder_path=cand.get("folder_path"))
    if info is not None:
        run = {"run_start": info.run_start, "run_end": info.run_end, "duration_s": info.run_duration_s,
               "parameters": info.parameters or {}, "qubits": list(info.qubits or []),
               "qubit_pairs": list(info.qubit_pairs or []), "outcomes": info.outcomes or {},
               "status": info.status, "figure_names": list(info.figure_names or [])}
    else:
        run = dict(_run_facts(folder))
    run.update(run_id=event["run_id"], experiment_name=event["experiment"], folder_path=folder,
               instant_us=event["t_utc_us"], _hub=event, _from_ds=info is not None)
    if not run.get("qubits") and not run.get("qubit_pairs"):
        try:
            targets = json.loads(event.get("targets") or "[]") or []
        except ValueError:
            targets = []
        run["qubits"] = [str(t) for t in targets] if isinstance(targets, list) else []
    return run


# -------------------------------------------------------------- gates, async

_GATE_JOBS: OrderedDict = OrderedDict()
_GATE_LOCK = threading.Lock()
_GATE_WAKE = threading.Event()
_GATE_THREAD: threading.Thread | None = None


def _gate_path(instance, ledger_key, eid, digest) -> Path:
    return Path(instance) / "story_cache" / f"hub-{ledger_key}" / f"{eid}-{digest}.json"


def _gate_store(token, path, value) -> dict:
    value = dict(value, rev=GATES_REV)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, default=str), encoding="utf-8")
    except OSError:
        pass
    with _HUB_GATES_LOCK:
        _HUB_GATES[token] = value
        while len(_HUB_GATES) > 4096:
            _HUB_GATES.popitem(last=False)
    return value


def _gate_worker() -> None:
    while True:
        _GATE_WAKE.wait()
        with _GATE_LOCK:
            if not _GATE_JOBS:
                _GATE_WAKE.clear()
                continue
            token, job = _GATE_JOBS.popitem(last=False)
        # one run's state file is parsed while holding the interpreter: leave
        # room between runs so the server keeps answering (docs/281)
        time.sleep(0.01)
        try:
            value = _compute_gate(job["full"]())
        except Exception as exc:  # noqa: BLE001 -- one run's gate never stops the others
            logger.debug("gate failed", exc_info=True)
            value = {"verdict": "unknown", "reason": f"the gate could not be computed ({exc})",
                     "family": None, "anchor": None}
        _gate_store(token, job["path"], value)


def gates_pending() -> int:
    with _GATE_LOCK:
        return len(_GATE_JOBS)


def _hub_gate(instance, run, ledger_key, compute, ds, wait):
    """The deterministic gate of one run event, anchored on its own saved
    state. RAM, then disk (one folder per ledger, keyed by event + state),
    then computed: synchronously when ``wait`` (the report) or a test passes
    ``compute``; otherwise in the background, and the card says it is being
    checked (docs/281: a 551-run day no longer waits on 200 state files)."""
    global _GATE_THREAD
    fact = run["_hub"]
    stable = (fact["eid"], fact.get("state_hash"), GATES_REV,
              json.dumps([run.get("parameters"), run.get("outcomes")], sort_keys=True, default=str))
    token = (str(instance), ledger_key, compute) + stable
    with _HUB_GATES_LOCK:
        hit = _HUB_GATES.get(token)
        if hit is not None:
            _HUB_GATES.move_to_end(token)
            return hit
    digest = hashlib.sha1(repr(stable).encode()).hexdigest()[:12]
    path = _gate_path(instance, ledger_key, fact["eid"], digest)
    if compute is None:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("rev") == GATES_REV:
                with _HUB_GATES_LOCK:
                    _HUB_GATES[token] = cached
                return cached
        except (OSError, ValueError):
            pass

    def full():
        # the gate reads fit results too: the Datasets record when it holds the run
        if run.get("_from_ds") and ds is not None and hasattr(ds, "get_run"):
            got = ds.get_run(int(run["run_id"]))
            if got:
                return dict(got, folder_path=run["folder_path"], experiment_name=run["experiment_name"])
        return {k: v for k, v in run.items() if not k.startswith("_")}

    if wait or compute is not None:
        return _gate_store(token, path, (compute or _compute_gate)(full()))
    with _GATE_LOCK:
        _GATE_JOBS.setdefault(token, {"path": path, "full": full})
    return {"verdict": "pending", "reason": "being checked", "family": None, "anchor": None}


def _start_gates() -> None:
    """Wake the gate worker -- after a day is built, so it never competes
    with the build that queued the work (docs/281)."""
    global _GATE_THREAD
    with _GATE_LOCK:
        if not _GATE_JOBS:
            return
        if _GATE_THREAD is None or not _GATE_THREAD.is_alive():
            _GATE_THREAD = threading.Thread(target=_gate_worker, name="story-gates", daemon=True)
            _GATE_THREAD.start()
        _GATE_WAKE.set()


# ---------------------------------------------------------- agent records

def _agent_records(instance, chip) -> list[dict]:
    """Every ended agent run SM knows of for *chip*: the durable index rows
    (``agent_runs/index.jsonl``) merged by key with the Registry's per-run
    ``meta.json`` (a run whose index row was never written still has one).
    Files are read once per (mtime, size) (docs/281)."""
    records = list(load_agent_runs(instance, chip).values()) + unattributed_agent_runs(instance, chip)
    by_key = {r["key"]: r for r in records if r.get("key")}
    # Read Registry's durable metas without constructing Registry: construction
    # reconciles interrupted workers and would mutate history during a read.
    for path in (Path(instance) / "agent_runs").glob("*/meta.json"):
        meta = _cached_json(str(path))
        if not isinstance(meta, dict) or meta.get("chip") not in (chip, None) or not meta.get("ended"):
            continue
        result = meta.get("result") or {}
        merged = dict(by_key.get(meta.get("key")) or {}, **meta)
        merged.update(result if isinstance(result, dict) else {})
        merged["ts"] = meta["ended"]
        if meta.get("key") in by_key:
            records = [r for r in records if r.get("key") != meta["key"]]
        records.append(merged)
    return records


_JSON_FILES: OrderedDict = OrderedDict()


def _cached_json(path: str):
    sig = _file_sig(path)
    with _RUN_FACTS_LOCK:
        hit = _JSON_FILES.get(path)
        if hit is not None and hit[0] == sig:
            return hit[1]
    value = _read_json_dict(path) if sig is not None else {}
    with _RUN_FACTS_LOCK:
        _JSON_FILES[path] = (sig, value)
        while len(_JSON_FILES) > 20_000:
            _JSON_FILES.popitem(last=False)
    return value


def _int_or_none(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _record_times(record) -> tuple[float, float]:
    try:
        end = float(record.get("ended") or record.get("ts") or 0)
    except (TypeError, ValueError):
        end = 0.0
    try:
        since = float(record.get("since") or 0) or end - 900
    except (TypeError, ValueError):
        since = end - 900
    return since, end


def _rel_clock(rel_path: str) -> tuple[str | None, str | None]:
    """``<YYYY-MM-DD>/#N_name_HHMMSS`` -> (day dir, HHMMSS)."""
    rel = str(rel_path or "").replace("\\", "/")
    day = rel.split("/", 1)[0] if "/" in rel else None
    m = re.search(r"_(\d{6})$", rel)
    return day, (m.group(1) if m else None)


def _link_agent_records(records: list[dict], runs: list[dict], ds_root=None) -> tuple[dict, dict, set]:
    """Which run folder each agent record is (docs/281 review, P0-1/P0-2).

    * ``certain``: the record names its run -- by folder (records since this
      review), else by run number narrowed by the run's own date/time, else by
      run number in the folder Datasets showed (an older record was
      attributed against Datasets' runs), else within the attempt's time --
      and exactly one run fits.
    * ``inferred``: an attempt that finished without a run number (``ok`` /
      ``unattributed``: its folder landed late) and the nearest run of that
      node inside ``[since - 60 s, ended + 900 s]``. Each run is taken once.
    * a failed, stopped, refused or skipped attempt is never matched by time:
      it keeps its own card. Two runs a record could name -> neither.

    Returns ``(certain {eid: record}, inferred {eid: record}, consumed {id})``:
    a consumed record is represented by a run card (or names folders it cannot
    tell apart) and gets no card of its own."""
    from bisect import bisect_left, bisect_right
    from quam_state_manager.core import run_terms
    by_id: dict[int, list[dict]] = {}
    by_exp: dict[str, list[dict]] = {}
    for run in runs:
        if run.get("run_id") is not None:
            by_id.setdefault(int(run["run_id"]), []).append(run)
        by_exp.setdefault(run.get("experiment") or "", []).append(run)
    for lst in by_exp.values():
        lst.sort(key=lambda r: r["t_utc_us"])
    times = {exp: [r["t_utc_us"] for r in lst] for exp, lst in by_exp.items()}
    exps_of: dict[str, list[str]] = {}
    certain: dict[int, dict] = {}
    inferred: dict[int, dict] = {}
    consumed: set[int] = set()
    pairs = []
    for rec in records:
        since, end = _record_times(rec)
        rid = _int_or_none(rec.get("run_id"))
        info = rec.get("run") if isinstance(rec.get("run"), dict) else {}
        if rid is not None:
            cands = by_id.get(rid, [])
            folder = rec.get("folder") or info.get("folder")
            if folder:
                cands = [r for r in cands if _norm_folder(os.path.join(r.get("root_path") or "", r.get("rel_path") or ""))
                         == _norm_folder(folder)]
            elif len(cands) > 1 and info.get("date") and info.get("time"):
                hhmmss = str(info["time"]).replace(":", "")
                cands = [r for r in cands if _rel_clock(r.get("rel_path")) == (info["date"], hhmmss)]
            if len(cands) > 1 and not folder and ds_root:
                cands = [r for r in cands if _norm_folder(r.get("root_path")) == _norm_folder(ds_root)] or cands
            if len(cands) > 1 and not folder and end:
                cands = [r for r in cands if since - 300 <= r["t_utc_us"] / 1e6 <= end + 900]
            if len(cands) == 1:
                certain.setdefault(cands[0]["eid"], rec)
            if cands:
                consumed.add(id(rec))
            continue
        if (rec.get("classification") or "unattributed") not in _MAY_HAVE_A_FOLDER or not end:
            continue
        node = rec.get("node") or ""
        if node not in exps_of:
            exps_of[node] = [e for e in by_exp if run_terms.same_node(e, node)]
        lo, hi = int((since - 60) * 1e6), int((end + 900) * 1e6)
        for exp in exps_of[node]:
            ts = times[exp]
            for run in by_exp[exp][bisect_left(ts, lo):bisect_right(ts, hi)]:
                pairs.append((abs(run["t_utc_us"] / 1e6 - end), run["eid"], id(rec), rec))
    taken: set[int] = set(certain)
    for _dist, eid, rid_, rec in sorted(pairs, key=lambda p: (p[0], p[1])):
        if eid in taken or rid_ in consumed:
            continue
        inferred[eid] = rec
        taken.add(eid)
        consumed.add(rid_)
    return certain, inferred, consumed


def _agent_cards(instance, chip, day, ledger, records, consumed) -> list[dict]:
    cards = []
    for order, record in enumerate(records):
        if id(record) in consumed:
            continue
        _since, end = _record_times(record)
        if not end or _hub_clock(int(end * 1e6), ledger, "%Y-%m-%d") != day:
            continue
        if record.get("step") is None and record.get("plan_id") and record.get("key"):
            from quam_state_manager.core import agent_plans
            plan = agent_plans.get(instance, chip, record["plan_id"]) or {}
            step = next((s for s in plan.get("steps") or [] if s.get("run_key") == record["key"]), None)
            if step is not None:
                record["step"] = step["i"]
        failure = record.get("failure") if isinstance(record.get("failure"), dict) else {}
        classification = record.get("classification") or "unattributed"
        outcome = ("not run" if classification in _NOT_RUN else
                   "cancelled" if classification == "cancelled" else
                   None if classification in _MAY_HAVE_A_FOLDER else "failed")
        card = {"kind": "agent_run", "id": record.get("key") or f"record-{order}",
                "ts": end, "time": _hub_clock(int(end * 1e6), ledger),
                "author": record.get("actor") or f"by_{record.get('backend') or 'agent'}",
                "node": record.get("node") or "unknown node", "targets": record.get("targets") or [],
                "plan_id": record.get("plan_id"), "step": record.get("step"),
                "classification": classification, "short": _CLASS_SHORT.get(classification, classification),
                "outcome": outcome,
                # A run id was recorded, so a folder was written: the history
                # has not got it (another folder, or not read yet).
                "missing_run_id": _int_or_none(record.get("run_id")),
                # What went wrong, never the purpose the agent stated.
                "reason": record.get("error") or failure.get("what") or None,
                "purpose": record.get("reason") or None}
        card["sentence"] = _agent_text(card)
        cards.append(card)
    return cards


# ------------------------------------------------------------- the day

def _project_day_lines(instance_path, key, day, ledger) -> list[dict]:
    """docs/281: journal files are named by the server's calendar; a line
    belongs to the PROJECT-zone day of its instant, so the day reads its own
    file and both neighbours. ``on_day`` marks the lines that fall on it (they
    attach by time or stay loose -- never the same line on two days); a line
    that names a run number may attach to that run from a neighbouring day.
    Its clock is shown in the project zone too."""
    d0 = datetime.strptime(day, "%Y-%m-%d")
    out = []
    for k in (-1, 0, 1):
        try:
            file_day = (d0 + timedelta(days=k)).strftime("%Y-%m-%d")
        except (OverflowError, ValueError):
            continue
        for e in parse_journal(journal_mod.read(instance_path, key, file_day), file_day):
            try:
                e["on_day"] = _hub_clock(int(e["ts"] * 1e6), ledger, "%Y-%m-%d") == day
                e["time"] = _hub_clock(int(e["ts"] * 1e6), ledger)
            except (OverflowError, OSError, ValueError):
                e["on_day"] = file_day == day
            if e["on_day"] or e.get("run_id") is not None:
                out.append(e)
    return out


def _hub_author(run, card_uid, rid, eid, *, certain, inferred, claims, ambiguous, events, start, end, fact):
    """(author, certainty, agent record). A person's claim on THIS card (its
    folder and number) wins; a bare-number claim (made before claims named
    the folder) only when no other folder holds that number, or on the run of
    the folder Datasets shows -- the one the old page offered -- never on two
    cards. Then the agent record that names this run (certain),
    the late-folder match (inferred), a hook event in the window (inferred),
    the event's own actor, else unknown (docs/281 review P0-1/P0-2)."""
    claim = claims.get(card_uid) if card_uid else None
    if claim is None and (rid not in ambiguous or run.get("_from_ds")):
        claim = claims.get(str(rid))
    rec = certain.get(eid) or inferred.get(eid)
    if claim and claim.get("author"):
        return str(claim["author"]), "claimed", rec, claim
    if eid in certain:
        return rec.get("actor") or f"by_{rec.get('backend') or 'agent'}", "certain", rec, None
    if eid in inferred:
        return rec.get("actor") or f"by_{rec.get('backend') or 'agent'}", "inferred", rec, None
    author, certainty, _ = _author_of(run, start, end, agent_runs={}, events=events, claims={})
    if certainty == "none" and fact.get("actor"):
        return fact["actor"], "certain", None, None
    return author, certainty, None, None


def _int_like(value, path, int_of):
    """The ledger keeps numbers as REAL (docs/269: 1 == 1.0); a value the open
    chip stores as an int at that path reads as an int (docs/281 review)."""
    if int_of is not None and isinstance(value, float) and value.is_integer() and int_of(path):
        return int(value)
    return value


def _build_day_hub(instance_path, chip, day, *, ds, active_path, events, uid_of, gate_compute,
                   with_gates, ledger, agent_chip, gate_wait=False, int_of=None) -> dict:
    from quam_state_manager.core import hub_query, hub_sync
    from quam_state_manager.core.hub_store import SM_KINDS
    from quam_state_manager.core.ramcache import Warming
    directory = ledger.store.directory
    records = _agent_records(instance_path, agent_chip or chip)
    try:
        history = hub_sync.require_ready(directory)
        page = hub_query.timeline(ledger, day_from=day, day_to=day, limit=1_000_000,
                                  include_runs=bool(records), include_ambiguous=True)
        if page["cursor"]:
            return _empty_day(chip, day, {"state": "unavailable", "note": "This day exceeds the log's display limit."}, instance_path)
    except Warming as exc:
        history = dict(getattr(exc, "status", None) or hub_sync.status(directory), state="building")
        return _empty_day(chip, day, history, instance_path)
    except ValueError as exc:
        if "project time zone" not in str(exc):
            raise
        return _empty_day(chip, day, {"state": "unavailable", "note": str(exc)}, instance_path)
    hub_events = page["events"]
    ambiguous = page.get("ambiguous_run_ids") or set()
    certain, inferred, consumed = _link_agent_records(records, page.get("runs") or [],
                                                      ds_root=getattr(ds, "folder_path", None))
    events = events or []
    claims = load_claims(instance_path, chip)
    entries = _project_day_lines(instance_path, chip, day, ledger)
    ledger_key = _folder_key(directory)

    cards: list[dict] = []
    used: set[int] = set()
    day_runs = [(event, _hub_run(event, ds)) for event in reversed(hub_events) if event["kind"] == "run"]
    root_keys: dict = {}
    # A number two folders hold names, in an older line, the run of the folder
    # Datasets showed when the line was written; otherwise no run at all.
    by_number = {run["run_id"] for _e, run in day_runs if run.get("_from_ds")}

    def numbered_here(e, run) -> bool:
        n = e.get("run_id")
        return n is not None and n == run["run_id"] and (n not in ambiguous or bool(run.get("_from_ds")))

    def unnumbered(e) -> bool:
        n = e.get("run_id")
        return n is None or (n in ambiguous and n not in by_number)

    # "Parameters changed vs": a run of a folder Datasets does not hold is
    # compared within its own folder, all looked up in one read
    try:
        previous = hub_query.previous_in_folder(ledger, [e["eid"] for e, run in day_runs if not run.get("_from_ds")])
    except (Warming, ValueError):
        previous = {}
    for event, run in day_runs:
        rid, eid = int(run["run_id"]), event["eid"]
        uid = uid_of(run) if uid_of else None
        # the card's identity for claims: its data folder + its number, the
        # same whichever surface builds the day
        root_key = root_keys.get(event.get("root_id"))
        if root_key is None:
            root_key = root_keys[event.get("root_id")] = _folder_key(event.get("root_path") or "")
        card_uid = f"{root_key}:{rid}"
        start = start_epoch(run, None)
        end = _iso_epoch(run.get("run_end")) or start
        author, certainty, ar, claim = _hub_author(
            run, card_uid, rid, eid, certain=certain, inferred=inferred, claims=claims,
            ambiguous=ambiguous, events=events, start=start, end=end, fact=event)
        attached = [e for e in entries if numbered_here(e, run)]
        targets = list(run.get("qubits") or []) + list(run.get("qubit_pairs") or [])
        if start is not None:
            lo, hi = start - _BEFORE_S, (end or start) + _AFTER_S
            for e in entries:
                if e["on_day"] and unnumbered(e) and lo <= e["ts"] <= hi \
                        and id(e) not in used \
                        and (not targets or _targets_in(e, targets) or not _mentions_any_target(e)):
                    attached.append(e)
        for e in attached:
            used.add(id(e))
        because = next((e["because"] for e in attached if e.get("because")), None)
        if because is None:
            because = next((e["text"] for e in attached if e["kind"] in ("agent",) or e["kind"].startswith("by_")), None)
        # "Parameters changed vs" compares within the run's own folder
        prev_id = prev = None
        if run.get("_from_ds"):
            try:
                prev_id = ds.get_previous_same_experiment_id(rid)
            except Exception:  # noqa: BLE001
                prev_id = None
            info = (getattr(ds, "runs", None) or {}).get(prev_id) if prev_id else None
            # the in-RAM record (its parameters), not get_run's fit-file resolution
            prev = ({"parameters": info.parameters} if info is not None
                    else ds.get_run(prev_id) if prev_id else None)
        elif previous.get(eid):
            prev_id, folder_prev = previous[eid]
            prev = _run_facts(folder_prev)
        fam_key, fam_label = _family_label(run.get("experiment_name") or "")
        gate = _hub_gate(instance_path, run, ledger_key, gate_compute, ds, gate_wait) if with_gates else None
        figs = list(run.get("figure_names") or [])
        flags = _flags_of(event)
        other_chip = any(f["key"] == "other-chip" for f in flags)
        rows = [] if (event.get("first") or other_chip) else event["changes"]
        if int_of is not None:
            rows = [dict(r, old=_int_like(r.get("old"), r["path"], int_of),
                         new=_int_like(r.get("new"), r["path"], int_of)) for r in rows]
        cards.append({
            "kind": "run", "run_id": rid, "uid": uid, "card_uid": card_uid,
            "ts": event["t_utc_us"] / 1e6, "time": _hub_clock(event["t_utc_us"], ledger),
            "eid": eid, "order": event.get("ord"), "duration_s": run.get("duration_s"),
            "node": run.get("experiment_name"), "family": fam_key, "family_label": fam_label,
            "family_short": _short_family(fam_key, fam_label, run.get("experiment_name")),
            "targets": targets, "outcome": _outcome(run.get("outcomes")), "outcomes": run.get("outcomes") or {},
            "status": run.get("status"), "gate": gate,
            "author": author, "certainty": certainty,
            "plan_id": (ar or {}).get("plan_id"), "step": (ar or {}).get("step"),
            "params": run.get("parameters") or {},
            "params_diff": _params_diff(run.get("parameters"), (prev or {}).get("parameters")),
            "prev_run_id": prev_id,
            # The event's exact rows vs the ledger state before it. The first
            # event with a state is the starting state: counted, not listed.
            "writes": rows,
            "first_state_n": len(event["changes"]) if event.get("first") else None,
            "rows_hidden": len(event["changes"]) if other_chip and not event.get("first") else None,
            "flags": flags, "other_chip": other_chip,
            "because": because, "journal": attached,
            "figure": figs[0] if figs else None, "figures": figs,
            "folder": run["folder_path"],
            "note": (claim or {}).get("note"),
        })

    cards.extend(_hub_write(e, ledger, int_of) for e in reversed(hub_events)
                 if e["kind"] in SM_KINDS and e.get("outcome") == "landed")
    cards.extend(_agent_cards(instance_path, agent_chip or chip, day, ledger, records, consumed))
    # a line names a run number two folders hold: it stays a loose line
    loose = [e for e in entries if id(e) not in used and e["on_day"] and unnumbered(e)]
    # Same instant: the ledger's canonical order decides (docs/275).
    cards.sort(key=lambda c: (c.get("ts") or 0, c.get("order") is None, c.get("order") or 0))
    run_ids = [c["run_id"] for c in cards if c["kind"] == "run"]
    duplicates = {r for r, count in Counter(run_ids).items() if count > 1}
    for card in cards:
        if card["kind"] == "run":
            card["card_id"] = f"card-{card['run_id']}" + (f"-{card['eid']}" if card["run_id"] in duplicates else "")
    # lines of a session that named no chip: by the project-zone day as well
    unassigned = [e for e in _project_day_lines(instance_path, "unassigned", day, ledger) if e["on_day"]]
    out = {"chip": chip, "day": day, "cards": cards, "loose": loose, "digest": _digest(cards),
           "timeline": _timeline(cards), "unassigned": unassigned, "counts": _counts(cards), "history": history,
           "gates_pending": sum(1 for c in cards if c["kind"] == "run" and (c.get("gate") or {}).get("verdict") == "pending")}
    out["unrecorded"] = _unrecorded_edits(instance_path, active_path, day, ledger)
    _start_gates()
    return out


def _hub_write(event, ledger, int_of=None):
    from quam_state_manager.core.hub_store import UNDONE, PARTLY_UNDONE
    flags = event.get("flags") or 0
    author = event.get("actor") or "unknown"
    # The entries SM recorded at the door; the ledger's rows for this event
    # only when those entries cannot be read (said on the card).
    exact = "exact_entries" in event
    rows = event["exact_entries"] if exact else event["changes"]
    taken_back = event.get("taken_back") or set()
    undoes = []
    for link in event.get("undo_links") or ():
        target = link.get("target") or {}
        when = target.get("t_utc_us")
        undoes.append({"event": link["event"], "units": link.get("units"),
                       "kind": target.get("kind"), "src": target.get("src"),
                       "label": write_label(target.get("kind"), target.get("src")) if target else None,
                       "day": _hub_clock(when, ledger, "%Y-%m-%d") if when is not None else None,
                       "time": _hub_clock(when, ledger) if when is not None else None})
    partly = bool(flags & PARTLY_UNDONE)
    entries = []
    for row in rows:
        old, new = row.get("old"), row.get("new")
        if not exact:
            old, new = _int_like(old, row["path"], int_of), _int_like(new, row["path"], int_of)
        entries.append(dict(row, op=entry_op(row), old=old, new=new,
                            actor=row.get("by") or row.get("actor") or author,
                            taken_back=partly and row.get("path") in taken_back))
    return {"kind": "write", "id": event.get("sm_id") or event["eid"], "eid": event["eid"], "order": event.get("ord"),
            "event_kind": event["kind"], "label": write_label(event["kind"], event.get("src")),
            "ts": event["t_utc_us"] / 1e6, "time": _hub_clock(event["t_utc_us"], ledger), "author": author,
            "entries": entries, "entries_error": event.get("entries_error"),
            "src": event.get("src"), "plan_id": event.get("plan_id"),
            "undoes": undoes,
            "undone": bool(flags & UNDONE), "partly_undone": partly}


def build_day(instance_path, chip: str, day: str, *, ds, hm=None, active_path=None,
              events: list[dict] | None = None, uid_of: Callable | None = None,
              gate_compute: Callable | None = None, with_gates: bool = True,
              ledger=None, agent_chip=None, gate_wait: bool = False, int_of=None) -> dict:
    """Everything the Calibration log renders for one chip and one day."""
    if ledger is not None:
        return _build_day_hub(instance_path, chip, day, ds=ds, active_path=active_path, events=events,
                              uid_of=uid_of, gate_compute=gate_compute, with_gates=with_gates,
                              ledger=ledger, agent_chip=agent_chip, gate_wait=gate_wait, int_of=int_of)
    events = events or []
    text = journal_mod.read(instance_path, chip, day)
    entries = parse_journal(text, day)
    agent_runs = load_agent_runs(instance_path, agent_chip or chip)
    claims = load_claims(instance_path, chip)
    rows = ds.list_runs(date=day) if ds is not None else []
    folder_key = _folder_key(getattr(ds, "folder_path", "")) if ds is not None else "none"

    cards: list[dict] = []
    used: set[int] = set()
    for row in sorted(rows, key=lambda r: (r.get("time") or "")):
        run = ds.get_run(int(row["run_id"])) or dict(row)
        rid = int(run["run_id"])
        # docs/262: instants, not the folder digits in the server's zone -- the
        # window is compared with journal ``ts``/hook ``ts`` and the card ``ts``
        # is sorted with undo-unit epochs. ``time`` (shown) stays the folder clock.
        start = start_epoch(run, ds)
        end = _iso_epoch(run.get("run_end")) or start
        author, certainty, ar = _author_of(run, start, end, agent_runs=agent_runs, events=events, claims=claims)
        attached = [e for e in entries if e.get("run_id") == rid]
        targets = list(run.get("qubits") or []) + list(run.get("qubit_pairs") or [])
        if start is not None:
            lo, hi = start - _BEFORE_S, (end or start) + _AFTER_S
            for e in entries:
                if e.get("run_id") is None and lo <= e["ts"] <= hi and id(e) not in used \
                        and (not targets or _targets_in(e, targets) or not _mentions_any_target(e)):
                    attached.append(e)
        for e in attached:
            used.add(id(e))
        because = next((e["because"] for e in attached if e.get("because")), None)
        if because is None:
            because = next((e["text"] for e in attached if e["kind"] in ("agent",) or e["kind"].startswith("by_")), None)
        prev_id = None
        try:
            prev_id = ds.get_previous_same_experiment_id(rid)
        except Exception:  # noqa: BLE001
            pass
        prev = ds.get_run(prev_id) if prev_id else None
        fam_key, fam_label = _family_label(run.get("experiment_name") or "")
        gate = gate_for_run(instance_path, run, folder_key=folder_key, compute=gate_compute) if with_gates else None
        figs = list(run.get("figure_names") or [])
        cards.append({
            "kind": "run", "run_id": rid, "uid": (uid_of(run) if uid_of else None),
            "ts": start, "time": run.get("time"), "duration_s": run.get("duration_s"),
            "node": run.get("experiment_name"), "family": fam_key, "family_label": fam_label,
            "family_short": _short_family(fam_key, fam_label, run.get("experiment_name")),
            "targets": targets, "outcome": _outcome(run.get("outcomes")), "outcomes": run.get("outcomes") or {},
            "status": run.get("status"), "gate": gate,
            "author": author, "certainty": certainty,
            "plan_id": (ar or {}).get("plan_id"),
            "params": run.get("parameters") or {},
            "params_diff": _params_diff(run.get("parameters"), (prev or {}).get("parameters")),
            "prev_run_id": prev_id,
            "writes": _writes_for_run(hm, active_path, rid),
            "because": because,
            "journal": attached,
            "figure": figs[0] if figs else None, "figures": figs,
            "folder": str(run.get("folder_path") or ""),
            "note": (claims.get(str(rid)) or {}).get("note"),
        })

    # write cards: applied undo-journal units of that day
    for u in _units_of_day(instance_path, active_path, day):
        cards.append(u)

    loose = [e for e in entries if id(e) not in used and e.get("run_id") is None]
    cards.sort(key=lambda c: (c.get("ts") or 0))
    unassigned = parse_journal(journal_mod.read(instance_path, "unassigned", day), day)
    return {"chip": chip, "day": day, "cards": cards, "loose": loose, "digest": _digest(cards),
            "timeline": _timeline(cards), "unassigned": unassigned, "counts": _counts(cards)}


def _unrecorded_edits(instance_path, active_path, day, ledger) -> dict | None:
    """Edits in SM's undo journal on *day* (project zone) that predate the
    ledger's first recorded SM write. The undo journal is not history: it
    holds working-copy saves and never says whether one reached the chip, so
    these are only counted, so a day is never shown as if SM wrote nothing
    (docs/281). Any that reached the chip show inside the next run's rows."""
    if not active_path:
        return None
    try:
        from quam_state_manager.core import hub_query, undo_journal
        units = undo_journal.load(undo_journal.sidecar_path(instance_path, active_path))
        units = [u for u in units if float(u.get("ts") or 0)
                 and _hub_clock(int(float(u["ts"]) * 1e6), ledger, "%Y-%m-%d") == day]
        if not units:
            return None
        recorded = hub_query.sm_writes_recorded(ledger)
    except Exception:  # noqa: BLE001 -- a note never breaks the day
        logger.debug("unrecorded-edit note failed", exc_info=True)
        return None
    first = recorded["first"]
    # A unit a recorded write names is that write (its journal stamp can be a
    # few ms before the event's own instant); it is never "unrecorded".
    early = [u for u in units
             if str(u.get("id")) not in recorded["units"] and not (u.get("meta") or {}).get("hub")
             and (first is None or float(u["ts"]) * 1e6 < first)]
    if not early:
        return None
    return {"n": len(early), "since": _hub_clock(first, ledger, "%Y-%m-%d %H:%M:%S") if first is not None else None}


def _mentions_any_target(e: dict) -> bool:
    return bool(re.search(r"(?<![\w-])q[A-Za-z]?\d+(?![\w-])", (e.get("text") or "") + " " + " ".join(e.get("paths") or [])))


def _units_of_day(instance_path, active_path, day: str) -> list[dict]:
    if not active_path:
        return []
    try:
        from quam_state_manager.core import undo_journal
        units = undo_journal.load(undo_journal.sidecar_path(instance_path, active_path))
    except Exception:  # noqa: BLE001
        return []
    try:
        d0 = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return []
    try:
        lo, hi = d0.timestamp(), (d0 + timedelta(days=1)).timestamp()
    except (OSError, OverflowError, ValueError):
        # docs/191 A02: `datetime.timestamp()` raises OSError [Errno 22] on
        # Windows for any local date before the epoch, and the Calibration
        # log's own date picker offers them -- typing 1900-01-01 into it
        # crashed the page with a 500. A day outside the representable range
        # holds no units by definition; that is an empty answer, not an error.
        return []
    out = []
    for u in units:
        ts = float(u.get("ts") or 0)
        if not (lo <= ts < hi):
            continue
        entries = [{"path": e.get("path"), "old": e.get("old"), "new": e.get("new"),
                    "op": entry_op(e), "actor": e.get("actor") or "human"} for e in u.get("entries") or []]
        actors = sorted({e["actor"] for e in entries})
        meta = u.get("meta") or {}
        out.append({"kind": "write", "id": u.get("id"), "ts": ts,
                    "time": datetime.fromtimestamp(ts).strftime("%H:%M:%S"),
                    "author": actors[0] if len(actors) == 1 else ("mixed" if actors else "unknown"),
                    "entries": entries, "plan_id": meta.get("plan_id"), "src": meta.get("src"),
                    "undone": bool(u.get("undone") or meta.get("undone"))})
    return out


def _digest(cards: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in cards:
        if c.get("kind") != "run":
            continue
        for t in c.get("targets") or []:
            out.setdefault(t, []).append({"run_id": c["run_id"], "family": c.get("family_label") or c.get("node"),
                                          "outcome": c.get("outcome"), "gate": (c.get("gate") or {}).get("verdict"),
                                          "author": c.get("author")})
    return dict(sorted(out.items(), key=lambda kv: natural_key(kv[0])))


def _counts(cards: list[dict]) -> dict:
    # docs/281: a run that may be another chip's is counted apart; an agent
    # step that never ran is not a run at all
    runs = [c for c in cards if (c.get("kind") == "run" and not c.get("other_chip"))
            or (c.get("kind") == "agent_run" and c.get("outcome") != "not run")]
    writes = [c for c in cards if c.get("kind") == "write"]
    biggest = None
    for w in writes:
        for e in w["entries"]:
            try:
                d = abs(float(e["new"]) - float(e["old"]))
            except (TypeError, ValueError):
                continue
            if biggest is None or d > biggest["abs_delta"]:
                biggest = {"path": e["path"], "old": e["old"], "new": e["new"], "abs_delta": d}
    return {"runs": len(runs), "failed": sum(1 for c in runs if c.get("outcome") == "failed"),
            "gate_fail": sum(1 for c in runs if (c.get("gate") or {}).get("verdict") == "fail"),
            "writes": sum(len(w["entries"]) for w in writes), "write_cards": len(writes),
            "other_chip": sum(1 for c in cards if c.get("kind") == "run" and c.get("other_chip")),
            "biggest_write": biggest,
            "authors": sorted({c.get("author") for c in cards if c.get("author")})}
