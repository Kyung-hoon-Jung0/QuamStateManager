"""The calibration story: one run = one card, whoever ran it (docs/173 S1).

The Calibration log is NOT a rendering of the journal. Its spine is the
DatasetStore -- every run folder in the open data folder, whether an agent
made it through SM, a person made it in the QUAlibrate GUI, or nobody knows.
Onto that spine the story attaches:

  * journal lines (``core/journal.py``): by ``#run`` when the writer named
    it, else by time window ``[run_start-60s, run_end+300s]`` plus target
    overlap -- so a hook line that never learned the run id still lands.
  * the values the run WROTE to the chip: the change points of the history
    snapshot stamped with that run id (``leaf_index``), never a guess.
  * the parameters that changed vs the previous run of the same node.
  * the deterministic gate verdict, anchored on the run's OWN saved state
    (``<run>/quam_state/state.json``) so the answer is a property of the run
    and does not drift as the chip moves on; cached per (run, GATES_REV).
  * the author: certain when SM ran the node (``agent_runs/index.jsonl``),
    inferred from a hook event in the window, claimed by a person
    ("이건 내가 돌렸어요"), else ``unknown`` -- never ``qualibrate`` by default,
    because a terminal `python node.py` looks the same from here.

Write cards come from the undo journal: every applied unit, each entry with
its actor, so "who changed it on Tuesday" has a record behind it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from quam_state_manager.core import journal as journal_mod

logger = logging.getLogger(__name__)

GATES_REV = "2026-09-06a"          # bump when gates.py / families.py change what a verdict means
_BEFORE_S = 60.0
_AFTER_S = 300.0
_ENTRY = re.compile(r"^- \*\*(\d{2}:\d{2}:\d{2})\*\* `([^`]+)` ?(.*)$")
_RUN_TOKEN = re.compile(r"\s*·\s*run #(\d+)")
_PATH_TOKEN = re.compile(r"`([A-Za-z_][\w-]*(?:\.[\w-]+)+)`")
_NODE_RE = re.compile(r"python[^\s]*\s+(?:-m\s+\S+\s+)?(?:\"[^\"]*[\\/])?([^\s;&|\"']+)\.py\b")


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
            except ValueError:
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

def _epoch(iso: str | None, day: str | None = None, hms: str | None = None) -> float | None:
    if iso:
        try:
            return datetime.fromisoformat(iso).timestamp()
        except ValueError:
            pass
    if day and hms:
        try:
            return datetime.strptime(f"{day} {hms}", "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return None
    return None


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


def load_agent_runs(instance_path) -> dict[int, dict]:
    out: dict[int, dict] = {}
    try:
        for line in agent_runs_index_path(instance_path).read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("run_id") is not None:
                out[int(r["run_id"])] = r
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


def claim_run(instance_path, chip: str, run_id: int, *, author: str, note: str | None = None) -> dict:
    """A person says "이건 내가 돌렸어요" (or corrects the author)."""
    p = claims_path(instance_path, chip)
    p.parent.mkdir(parents=True, exist_ok=True)
    claims = load_claims(instance_path, chip)
    rec = {"author": author, "note": (note or "").strip() or None,
           "ts": datetime.now().isoformat(timespec="seconds")}
    claims[str(int(run_id))] = rec
    p.write_text(json.dumps(claims, indent=1, ensure_ascii=False), encoding="utf-8")
    return rec


def _author_of(run: dict, start: float | None, end: float | None, *, agent_runs: dict, events: list[dict],
               claims: dict) -> tuple[str, str, dict | None]:
    """(author, certainty, agent-run record). ``by_<backend>`` / ``human:<name>``
    / ``unknown``. Certainty: certain | inferred | claimed | none."""
    rid = int(run["run_id"])
    if str(rid) in claims and claims[str(rid)].get("author"):
        return str(claims[str(rid)]["author"]), "claimed", agent_runs.get(rid)
    ar = agent_runs.get(rid)
    if ar:
        return f"by_{ar.get('backend') or 'agent'}", "certain", ar
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
                         "is_first": bool(r.get("is_first"))})
    return rows


def _family_label(name: str) -> tuple[str | None, str]:
    try:
        from quam_state_manager.core.autofit import families as fam_mod
        fam = fam_mod.family_for(name or "")
        if fam is not None:
            return getattr(fam, "key", None), getattr(fam, "label", name)
    except Exception:  # noqa: BLE001
        pass
    return None, name


def build_day(instance_path, chip: str, day: str, *, ds, hm=None, active_path=None,
              events: list[dict] | None = None, uid_of: Callable | None = None,
              gate_compute: Callable | None = None, with_gates: bool = True) -> dict:
    """Everything the Calibration log renders for one chip and one day."""
    events = events or []
    text = journal_mod.read(instance_path, chip, day)
    entries = parse_journal(text, day)
    agent_runs = load_agent_runs(instance_path)
    claims = load_claims(instance_path, chip)
    rows = ds.list_runs(date=day) if ds is not None else []
    folder_key = _folder_key(getattr(ds, "folder_path", "")) if ds is not None else "none"

    cards: list[dict] = []
    used: set[int] = set()
    for row in sorted(rows, key=lambda r: (r.get("time") or "")):
        run = ds.get_run(int(row["run_id"])) or dict(row)
        rid = int(run["run_id"])
        start = _epoch(run.get("run_start"), run.get("date"), run.get("time"))
        end = _epoch(run.get("run_end")) or start
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
            "unassigned": unassigned, "counts": _counts(cards)}


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
    lo, hi = d0.timestamp(), (d0 + timedelta(days=1)).timestamp()
    out = []
    for u in units:
        ts = float(u.get("ts") or 0)
        if not (lo <= ts < hi):
            continue
        entries = [{"path": e.get("path"), "old": e.get("old"), "new": e.get("new"),
                    "actor": e.get("actor") or "human"} for e in u.get("entries") or []]
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
    return dict(sorted(out.items()))


def _counts(cards: list[dict]) -> dict:
    runs = [c for c in cards if c.get("kind") == "run"]
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
            "writes": sum(len(w["entries"]) for w in writes), "biggest_write": biggest,
            "authors": sorted({c.get("author") for c in cards if c.get("author")})}
