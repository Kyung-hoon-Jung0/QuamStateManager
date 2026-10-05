"""The JSON door a terminal agent uses (docs/172).

SM is the agent's eyes and hands: every read here comes from the same store
the GUI renders, every write goes through the same staged edit -> Review
tray -> apply-to-live door the human uses, so the person watching the
window sees exactly what the agent did and can Ctrl+Z it.

Mounted at ``/api/agent``. Behind the app-wide CSRF guard like everything
else (an out-of-process caller sends ``Origin: http://<host:port>``). Only
JSON in, only JSON out -- the htmx fragments the GUI routes answer with are
not for a machine to parse.

Also here: the two ends of the LIVE strip -- ``POST /event`` (fed by the
Claude Code hook script, which RECORDS and never interprets) and ``GET /now``
(what the agent is doing, right now, for the topbar). SM is the ONE writer
of the journal: a journal line is derived from an event here, on the POST or
at the next start from the hook's jsonl, deduplicated by event identity.
"""

from __future__ import annotations

import collections
import base64
import json
import os
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from quam_state_manager.core import journal as journal_mod
from quam_state_manager.core.loader import natural_key
from quam_state_manager.core.pointer_path import resolve_field_target

def _unknown_targets_msg(bad: list) -> str:
    """"unknown targets ['q0']" was a Python list repr, brackets and quotes
    included, shown verbatim to the person who typed it."""
    names = ", ".join(str(b) for b in bad)
    one = len(bad) == 1
    return ("no qubit or pair called %s on this chip" % names if one
            else "no qubit or pair called: %s" % names)


def _ascii_actor(s: str) -> str:
    """A name as a header can carry it (user directive: SM works in English;
    what you SAY to the agent is the exception, and that is a JSON body)."""
    return "".join(c for c in str(s or "") if 0x20 <= ord(c) <= 0x7E).strip()


logger = logging.getLogger(__name__)

agent_bp = Blueprint("agent", __name__, url_prefix="/api/agent")

_EVENT_RING = 800
_events_lock = threading.Lock()
_LIVE_WINDOW_S = 15 * 60          # a session with no sign of life for this long is 'stalled'
_NODE_RE = re.compile(r"python[^\s]*\s+(?:-m\s+\S+\s+)?(?:\"[^\"]*[\\/])?([^\s;&|\"']+)\.py\b")
_UNASSIGNED = "unassigned"


# ------------------------------------------------------------ small helpers

def _r():
    """The routes module, imported late (it is large and imports us first)."""
    from quam_state_manager.web import routes
    return routes


def _chip_name() -> str:
    r = _r()
    try:
        ident = r._active_chip_identity()
        if ident and ident.get("name"):
            return str(ident["name"])
    except Exception:  # noqa: BLE001
        pass
    p = r._active_path()
    return Path(p).name if p else "chip"


def _chip_key() -> str:
    """The key of the open chip's MACHINE records (session, approvals, plans,
    limits, runs): the working copy's ``<name>-<path hash>``. Review R3-1/R2-4:
    keyed by the display NAME, a backup folder called the same shared the arm
    token, the plans and the approvals of the live chip. The journal and the
    SM_CHIP pin keep the name -- they are for people."""
    r = _r()
    p = r._active_path()
    if not p:
        return "chip"
    try:
        from quam_state_manager.core import working_copy as wc_mod
        return wc_mod.key_for(p)
    except Exception:  # noqa: BLE001
        return _chip_name()


def _declared_chip_name(store) -> str | None:
    """``extras.chip_name`` -- the name a lab DECLARED for this chip (the top
    of the chip-identity ladder, docs/20 v2), or None."""
    try:
        v = ((store.state or {}).get("extras") or {}).get("chip_name")
    except Exception:  # noqa: BLE001
        return None
    return str(v).strip() or None if isinstance(v, str) else None


def _lock_for(kind: str) -> threading.Lock:
    """One lock per (kind, chip) for check-then-act routes (review R3-3 / R1-M3)."""
    locks = current_app.config.setdefault("agent_locks", {})
    k = f"{kind}:{_chip_key()}"
    lk = locks.get(k)
    if lk is None:
        lk = locks[k] = threading.Lock()
    return lk


def _live_flag() -> bool:
    """Have the live files moved outside SM? Answered FRESH for the agent:
    the page's refresher is throttled (30 s) and raise-only on a dirty working
    copy (QA diagnostics-r2-12 -- a human with staged edits gets the banner,
    not a pull), which is exactly when an agent that just ran a node would
    read stale values.
    One hash of two files per agent read is the price."""
    r = _r()
    ctx = r._active_ctx()
    if not ctx:
        return False
    wc = ctx.get("working_copy")
    if wc is not None:
        try:
            from quam_state_manager.core import working_copy as wc_mod
            fresh = wc_mod.live_diverged_now(wc)
            if fresh is not None:
                return bool(fresh)
        except Exception:  # noqa: BLE001
            logger.debug("live_diverged_now failed", exc_info=True)
    try:
        r._refresh_live_diverged(ctx)
    except Exception:  # noqa: BLE001
        pass
    return bool(ctx.get("live_diverged"))


def _jsonable(v: Any) -> Any:
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "__dataclass_fields__"):
        return {k: _jsonable(getattr(v, k)) for k in v.__dataclass_fields__}
    return v


def _err(msg: str, code: int = 400, **extra):
    return jsonify(ok=False, error=msg, **extra), code


def _version() -> str:
    try:
        from quam_state_manager import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- the chip

@agent_bp.route("/ping")
def ping():
    """Cheap liveness for the hook: no chip work, no live-hash recheck."""
    return jsonify(ok=True, sm_version=_version())


@agent_bp.route("/chip")
def chip():
    """What is open, and how the agent should address it."""
    r = _r()
    store = r._store()
    ctx = r._active_ctx()
    if not store or not ctx:
        return jsonify(ok=True, loaded=False, sm_version=_version(), now=_now_state())
    diverged = _live_flag()
    return jsonify(ok=True, loaded=True, sm_version=_version(),
                   path=r._active_path(), name=_chip_name(), chip_key=_chip_key(),
                   chip_token=r._active_chip_token() or "",
                   # docs/246 A-06: the declared identity, and what SM_CHIP should be
                   declared_name=_declared_chip_name(store), pin=_chip_key(),
                   qubits=list(store.qubit_names), pairs=list(store.qubit_pair_names),
                   pending=r._change_count(),
                   live_diverged=diverged,
                   stale_since=_stale_since() if diverged else None,
                   live_readonly=bool(ctx.get("live_readonly")),
                   waiting=_waiting_count(),
                   run_active=_run_active_view(),
                   plan=_plan_brief(),
                   now=_now_state())


# --------------------------------------------------------------- the state

_MAX_SUBTREE_CHARS = 60_000


@agent_bp.route("/state")
def state_get():
    """One value (raw + pointer-resolved) or a bounded subtree. Every answer
    says whether the live files have moved outside SM (a node's own write),
    because the working copy never adopts that by itself on this path -- and
    carries the notes people and agents pinned on that path or the entity
    above it (B-01, docs/252: "do not touch, fridge warming" must be read
    before the value is changed)."""
    r = _r()
    store = r._store()
    if not store:
        return _err("no chip loaded", 409)
    diverged = _live_flag()
    stale_since = _stale_since() if diverged else None
    path = (request.args.get("path") or "").strip().strip(".")
    if not path:
        keys = sorted(k for k in store.merged.keys())
        return jsonify(ok=True, path="", kind="container", keys=keys, live_diverged=diverged, stale_since=stale_since,
                       notes=_notes_touching(None))
    target = resolve_field_target(store.merged, path)
    pointer_meta = {"is_pointer": target["is_pointer"],
                    "resolved_path": target["resolved_path"] if target["resolvable"] else None}
    try:
        # The first candidate is the requested leaf before its own pointer
        # hops, after following any aliases crossed on the way to that leaf.
        raw_path = target["candidates"][0]["path"] if target["resolvable"] else path
        raw = store.get_value(raw_path)
    except (KeyError, IndexError, TypeError, ValueError):
        return _err(f"no such path: {path}", 404)
    if isinstance(raw, (dict, list)):
        text = json.dumps(_jsonable(raw), default=str)
        keys = list(raw.keys()) if isinstance(raw, dict) else list(range(len(raw)))
        if len(text) > _MAX_SUBTREE_CHARS:
            return jsonify(ok=True, path=path, kind="container", keys=[str(k) for k in keys],
                           **pointer_meta,
                           truncated=True, size_chars=len(text), live_diverged=diverged, stale_since=stale_since,
                           hint="ask for a deeper path; this subtree is too large to return whole",
                           notes=_notes_touching(path))
        return jsonify(ok=True, path=path, kind="container", keys=[str(k) for k in keys],
                       **pointer_meta,
                       value=_jsonable(raw), live_diverged=diverged, stale_since=stale_since,
                       notes=_notes_touching(path))
    resolved = raw
    if target["is_pointer"]:
        try:
            resolved = store.resolve_value(path)
        except Exception:  # noqa: BLE001
            resolved = raw
    try:
        src = store.source_file_for(raw_path)
    except Exception:  # noqa: BLE001
        src = None
    return jsonify(ok=True, path=path, kind="leaf", value=_jsonable(raw),
                   resolved=_jsonable(resolved), source_file=src,
                   **pointer_meta,
                   live_diverged=diverged, stale_since=stale_since,
                   notes=_notes_touching(path))


@agent_bp.route("/tray")
def tray():
    """The staged, not-yet-applied edits -- what the human sees in Review,
    each with who staged it."""
    r = _r()
    ctx0 = r._active_ctx()
    mod = r._modifier()
    if not mod:
        return _err("no chip loaded", 409)
    log = mod.get_change_log()
    rows = [{"index": i, "path": c.dot_path, "old": _jsonable(c.old_value), "new": _jsonable(c.new_value),
             "source": c.source_file, "created": c.created, "deleted": c.deleted,
             "group": c.group_id, "actor": getattr(c, "actor", "human")} for i, c in enumerate(log)]
    # docs/246 A-07: the picture is OF a chip. The bridge declares this key,
    # token and set signature back at apply, so a chip switched in between
    # is refused instead of applying the other chip's tray.
    sig = r._change_log_sig_of(log)
    key, token = _chip_key(), r._active_chip_token() or ""
    if r._active_ctx() is not ctx0:
        # a /load landed between reading the log and naming its chip: the
        # rows and the key would describe two chips
        return _err("the open chip changed while the tray was read -- read it again", 409,
                    conflict="chip_switched")
    return jsonify(ok=True, count=len(rows), seen_changes=len(rows), entries=rows,
                   seen_sig=sig, chip_key=key, chip_token=token,
                   agent_count=sum(1 for x in rows if str(x["actor"]).startswith("by_")),
                   human_count=sum(1 for x in rows if str(x["actor"]).startswith("human")),
                   live_diverged=_live_flag())


# ------------------------------------------------------------- the history

@agent_bp.route("/versions")
def versions():
    r = _r()
    path = r._active_path()
    if not path:
        return _err("no chip loaded", 409)
    try:
        n = int(request.args.get("n", "30"))
    except ValueError:
        return _err("n must be a positive integer")
    if n <= 0:
        return _err("n must be a positive integer")
    n = min(n, 500)
    hm = r._history()
    try:
        snaps = hm.list_snapshots(path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"history unavailable: {exc}", 500)
    try:   # docs/250 (B-03): which folder recorded each version -- never another folder's row as this one's
        srcs, others = hm.snapshot_sources(path, snaps), hm.other_folder_summary(path, snaps)
    except Exception:  # noqa: BLE001
        srcs, others = {}, []
    rows = []
    for s in list(snaps)[:n]:
        rows.append({"timestamp": s.timestamp, "trigger": s.trigger, "kind": getattr(s, "kind", None),
                     "label": getattr(s, "label", None), "pinned": getattr(s, "pinned", False),
                     "run_id": s.run_id, "experiment": s.experiment_name,
                     "diff": _jsonable(s.diff_summary),
                     "source": srcs.get(s.timestamp) or {"kind": "unknown", "folder": None,
                                                         "label": None, "lineage": "unknown"}})
    return jsonify(ok=True, count=len(rows), versions=rows, other_folders=others)


@agent_bp.route("/field-history")
def field_history():
    r = _r()
    path = r._active_path()
    if not path:
        return _err("no chip loaded", 409)
    dot = (request.args.get("path") or "").strip()
    if not dot:
        return _err("path required")
    target = resolve_field_target(r._store().merged, dot)
    raw_path = target["candidates"][0]["path"] if target["resolvable"] else dot
    try:
        r._store().get_value(raw_path)
    except (KeyError, IndexError, TypeError, ValueError):
        return _err(f"no such path: {dot}", 404)
    # docs/282: the person's value drawer and this answer are ONE history --
    # the same routes._value_history read, the same words for who set a value.
    ctx = r._active_ctx()
    try:
        ans = r._value_history(ctx, {"value": dot}, limit=r._VH_DRAWER_LIMIT)
        if ans["mode"] in ("building", "preparing"):
            st = ans.get("status") or {}
            return jsonify(ok=True, path=dot, source=ans["mode"], history=None,
                           note=r._vh_wait_message(ans),
                           building={"done": st.get("done"), "total": st.get("total")})
        if ans["mode"] == "ledger":
            return jsonify(ok=True, path=dot, source="ledger",
                           history=_jsonable(r._vh_agent_view(ans, "value")))
        data, _current, _chart = r._legacy_field_history(ctx, dot)
    except Exception as exc:  # noqa: BLE001
        return _err(f"field history unavailable: {exc}", 500)
    return jsonify(ok=True, path=dot, source="snapshots", note=ans.get("fallback_note"),
                   history=_jsonable(data))


# ---------------------------------------------------------------- the runs

def _ds():
    r = _r()
    try:
        return r._dataset_store()
    except Exception:  # noqa: BLE001
        logger.debug("dataset store unavailable", exc_info=True)
        return None


def _uid(ds, run) -> str | None:
    r = _r()
    try:
        folder = getattr(ds, "folder_path", None)      # DatasetStore's own attribute (docs/173 S2 found the miss)
        if folder is None:
            return None
        return f"{r._folder_key(folder)}:{run['run_id'] if isinstance(run, dict) else run.run_id}"
    except Exception:  # noqa: BLE001
        return None


@agent_bp.route("/runs")
def runs():
    from difflib import SequenceMatcher
    from quam_state_manager.core.search_query import groups, matches_hay

    day = request.args.get("date") or None
    if day is not None:
        try:
            if not journal_mod.is_day(day):
                raise ValueError
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return _err("date must be a valid YYYY-MM-DD day")
    ds = _ds()
    if not ds:
        return jsonify(ok=True, count=0, runs=[], note="no dataset folder is open in SM")
    n = max(1, min(int(request.args.get("n") or 20), 500))
    query = (request.args.get("experiment") or "").strip()
    grps = groups(query)
    qubit = request.args.get("qubit") or None
    all_rows = ds.list_runs()
    if qubit:
        available = sorted({q for row in all_rows for q in row.get("qubits") or []}, key=natural_key)
        if qubit not in available:
            return _err(f"no qubit called {qubit} in the open dataset folder", 404, available=available)
    matches = [row for row in all_rows if matches_hay((row.get("experiment_name") or "").lower(), grps)]
    rows = [row for row in matches if (not day or row.get("date") == day)
            and (not qubit or qubit in (row.get("qubits") or []))][:n]
    out = []
    for row in rows:
        d = {k: _jsonable(row.get(k)) for k in ("run_id", "experiment_name", "date", "time", "qubits",
                                                  "qubit_pairs", "outcomes", "status", "duration_s",
                                                  "parent_id", "description")
             if k in row}
        d["uid"] = row.get("uid") or _uid(ds, row)
        out.append(d)
    hint = {}
    if query and not matches:
        names = sorted({row["experiment_name"] for row in all_rows if row.get("experiment_name")}, key=natural_key)
        closest = sorted(names, key=lambda name: SequenceMatcher(None, query.lower(), name.lower()).ratio(),
                         reverse=True)[:5]
        hint = {"closest": closest, "hint": f"no experiment matches {query!r}"
                + (f"; closest: {', '.join(closest)}" if closest else "; no experiment names are available")}
    return jsonify(ok=True, count=len(out), runs=out, **hint)


@agent_bp.route("/run/<int:run_id>")
def run_detail(run_id: int):
    ds = _ds()
    if not ds:
        return _err("no dataset folder is open in SM", 409)
    run = ds.get_run(run_id)
    if not run:
        return _err(f"no run #{run_id} in the open dataset folder", 404)
    folder = Path(run["folder_path"])
    figures = []
    for index, name in enumerate(run.get("figure_names") or []):
        p = ds.get_figure_path(run_id, name)
        figures.append({"index": index, "name": name, "path": str(p) if p else None})
    files = {}
    for fn in ("node.json", "data.json", "ds_raw.h5", "ds_fit.h5", "state.json", "wiring.json"):
        cand = folder / fn
        if not cand.exists():
            cand = folder / "quam_state" / fn
        if cand.exists():
            files[fn] = str(cand)
    d = _jsonable({k: run.get(k) for k in ("run_id", "experiment_name", "date", "time", "description",
                                            "qubits", "qubit_pairs", "outcomes", "parameters",
                                            "parent_id", "run_start", "run_end", "duration_s",
                                            "status", "fit_results")})
    d.update(uid=_uid(ds, run), folder=str(folder), figures=figures, files=files,
             figure_help="Call run_figure with this run_id and a figure name or zero-based index to view a PNG.")
    return jsonify(ok=True, run=d)


_FIGURE_MAX_BYTES = 2 * 1024 * 1024


@agent_bp.route("/run/<int:run_id>/figure")
def run_figure(run_id: int):
    """Return a bounded PNG from a declared figure in a known run folder."""
    ds = _ds()
    if ds is None:
        return _err("no dataset folder is open in SM", 409)
    run = ds.get_run(run_id)
    if not run:
        return _err("unknown run id", 404)
    names = run.get("figure_names") or []
    name = request.args.get("name")
    index = request.args.get("index")
    if (name is None) == (index is None):
        return _err("supply exactly one figure name or zero-based index", 400)
    if index is not None:
        try:
            i = int(index)
        except ValueError:
            return _err("figure index must be a non-negative integer", 400)
        if i < 0 or i >= len(names):
            return _err("unknown figure index", 404)
        name = names[i]
    if not name or any(part in name for part in ("..", "/", "\\", ":")):
        return _err("figure name must not be a path", 400)
    if name not in names:
        return _err("unknown figure name", 404)
    path = ds.get_figure_path(run_id, name)
    if path is None:
        return _err("figure file is unavailable", 404)
    try:
        path = Path(path).resolve()
        if not path.is_relative_to(Path(run["folder_path"]).resolve()):
            return _err("figure file is outside its run folder", 403)
        if path.suffix.lower() != ".png" or not path.is_file():
            return _err("only PNG image files are supported", 415)
        if path.stat().st_size > _FIGURE_MAX_BYTES:
            return _err("figure exceeds the 2 MB image limit", 413)
        with path.open("rb") as stream:
            data = stream.read(_FIGURE_MAX_BYTES + 1)
        if len(data) > _FIGURE_MAX_BYTES:
            return _err("figure exceeds the 2 MB image limit", 413)
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return _err("figure file is not a PNG image", 415)
    except (OSError, ValueError):
        return _err("figure file is unavailable", 404)
    return jsonify(ok=True, image={"type": "image", "data": base64.b64encode(data).decode("ascii"),
                                   "mimeType": "image/png"})


# ---------------------------------------------------------- diagnostics

@agent_bp.route("/diagnostics")
def diagnostics_json():
    r = _r()
    store = r._store()
    if not store:
        return _err("no chip loaded", 409)
    from quam_state_manager.core import diagnostics as diag
    findings = r._active_chip_findings(store)
    rows = [{"severity": f.severity, "category": f.category, "location": f.location,
             "message": f.message, "detail": f.detail, "path": f.jump_path}
            for f in findings if not getattr(f, "acknowledged", False)]
    return jsonify(ok=True, summary=diag.summarize(findings), findings=rows[:400],
                   truncated=len(rows) > 400)


# ------------------------------------------------- the calibration knowledge

@agent_bp.route("/families")
def families():
    """The families SM knows. A node NAME is matched by ``family_for`` at call
    time; there is no per-family list of node names to show (a field that
    claimed one was empty for every family and read as 'no nodes')."""
    from quam_state_manager.core.autofit import families as fam_mod
    from quam_state_manager.core.autofit import knowledge
    out = []
    for key, fam in sorted(fam_mod.FAMILIES.items()):
        out.append({"family": key, "label": getattr(fam, "label", key),
                    "kind": getattr(fam, "kind", None),
                    "value_key": getattr(fam, "value_key", None),
                    "manual": knowledge.pack_path(key).exists()})
    return jsonify(ok=True, families=out,
                   note="pass a node name to check_fit / family_for; SM matches it to a family itself")


@agent_bp.route("/manual/<family>")
def manual(family: str):
    """A family's case manual: the lab knowledge a terminal agent does not
    have -- what each figure shape MEANS and what to do about it. Never a
    number: the pack lint drops absolute-scale rules at load."""
    from quam_state_manager.core.autofit import knowledge
    pack = knowledge.load_family(family)
    if not pack:
        return _err(f"no manual for family {family!r}", 404)
    cases = [{k: c.get(k) for k in ("id", "name", "kind", "geometry", "prescription", "seen_count")}
             for c in pack.get("cases") or []]
    return jsonify(ok=True, family=family, physics=pack.get("physics"),
                   cases=cases, rules=pack.get("rules"), closure_rules=pack.get("closure_rules"),
                   signal_map=pack.get("signal_map"))


@agent_bp.route("/check-fit/<int:run_id>")
def check_fit(run_id: int):
    """The deterministic gates over one saved run: outcome, physical bands,
    raw-data feature presence, metric consistency. No model, no number
    invented -- the same code the autofit engine trusted."""
    r = _r()
    store = r._store()
    ds = _ds()
    if not ds:
        return _err("no dataset folder is open in SM", 409)
    run = ds.get_run(run_id)
    if not run:
        return _err(f"no run #{run_id}", 404)
    from quam_state_manager.core.autofit import families as fam_mod
    from quam_state_manager.core.autofit import gates
    fam = fam_mod.family_for(run.get("experiment_name") or "")
    if fam is None:
        return jsonify(ok=True, run_id=run_id, family=None,
                       note="no autofit family registered for this node -- only the node's own outcome applies",
                       outcomes=_jsonable(run.get("outcomes")))
    run_obj = dict(run)
    run_obj["folder_path"] = Path(run["folder_path"])
    targets = list(run.get("qubits") or []) + list(run.get("qubit_pairs") or [])

    def current_value_of(dotted: str):
        if not store:
            raise KeyError(dotted)
        return store.get_value(dotted)

    verdicts = gates.evaluate_run(run_obj, fam, targets, current_value_of=current_value_of)
    return jsonify(ok=True, run_id=run_id, family=getattr(fam, "key", None) or str(fam),
                   verdicts={t: _jsonable(v) for t, v in verdicts.items()})


# ------------------------------------------------------------------ notes

def _note_subject(store, subject: str) -> str:
    """A bare qubit / pair name is the entity's dot path (B-01, docs/252): an
    agent pinned ``qA4``, which no grid row matched and no read could find.
    A dot path, or a name the chip does not have, is kept as written."""
    s = str(subject or "").strip()
    if not s or "." in s or store is None:
        return s
    if s in set(store.qubit_names):
        return f"qubits.{s}"
    if s in set(store.qubit_pair_names):
        return f"qubit_pairs.{s}"
    return s


def _notes_touching(path: str | None) -> list[dict]:
    """The notes pinned on the open chip that concern ``path`` -- on it, on an
    entity above it, or under it (``entity_notes.touches``); every note for no
    path. B-01: a "do not touch, fridge warming" note an agent pinned was
    readable by no tool, and the next agent said there were no notes."""
    r = _r()
    live = r._active_path()
    if not live:
        return []
    try:
        from quam_state_manager.core import entity_notes
        store = r._store()
        raw = entity_notes.load(current_app.instance_path, live)
        merged = store.merged if store is not None and isinstance(store.merged, dict) else None
        items = entity_notes.classify(merged, raw)
    except Exception:  # noqa: BLE001 -- a note read never fails a state read
        logger.debug("notes read failed", exc_info=True)
        return []
    want = str(path or "").strip().strip(".")
    out = []
    for subject, rec in items.items():
        addr = _note_subject(store, subject)                 # a bare-name note written before the fix
        if want and not entity_notes.touches([addr], [want]):
            continue
        out.append({"subject": subject, "text": rec.get("text"), "author": rec.get("author") or None,
                    "updated_at": rec.get("updated_at"), "hand_tuned": bool(rec.get("hand_tuned")),
                    **({"orphan": rec["orphan"]} if "orphan" in rec and addr == subject else {})})
    out.sort(key=lambda x: natural_key(x["subject"]))
    return out


@agent_bp.route("/notes")
def notes_get():
    """Every note on the open chip, or those touching ``?path=``."""
    if not _r()._active_path():
        return _err("no chip loaded", 409)
    path = (request.args.get("path") or "").strip()
    if path:
        store = _r()._store()
        path = _note_subject(store, path)
        target = resolve_field_target(store.merged, path)
        raw_path = target["candidates"][0]["path"] if target["resolvable"] else path
        try:
            store.get_value(raw_path)
        except (KeyError, IndexError, TypeError, ValueError):
            return _err(f"no such path: {path}", 404)
    notes = _notes_touching(path or None)
    return jsonify(ok=True, path=path or None, count=len(notes), notes=notes)


@agent_bp.route("/note", methods=["POST"])
def note_set():
    r = _r()
    path = r._active_path()
    if not path:
        return _err("no chip loaded", 409)
    data = request.get_json(silent=True) or request.form.to_dict()
    subject = _note_subject(r._store(), str(data.get("subject") or "").strip())
    text = str(data.get("text") or "")
    if not subject:
        return _err("subject required (a qubit, pair, or dot path)")
    from quam_state_manager.core import entity_notes
    from quam_state_manager.web import callers
    # B-04 (docs/252): the author is the CALLER -- by_claude / by_codex from the
    # bridge's header, a person from their window -- never the payload's word
    # (every client used to be recorded as "claude-code").
    agent = bool((request.headers.get("X-SM-Agent") or "").strip())
    author = r._request_actor() if (agent or callers.from_person()) else "unverified"
    try:
        rec = entity_notes.save(current_app.instance_path, path, subject, text, author=author,
                                chip_token=r._active_chip_token() or "")
    except entity_notes.NoteConflict as exc:
        return _err("note changed underneath you", 409, stored=_jsonable(exc.stored))
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(ok=True, note=_jsonable(rec))


# ---------------------------------------------------------------- journal

@agent_bp.route("/journal", methods=["GET"])
def journal_get():
    chip = request.args.get("chip") or _chip_name()
    # The journal PAGE spells it `day=`; this door spelt it `date=` only, so a
    # caller copying the page's own URL was answered with TODAY under a `date`
    # field naming today -- honest, but a question nobody asked. Both spellings.
    day = request.args.get("date") or request.args.get("day") or None
    # docs/191 H05: this went into a file path unchecked. A day is a day.
    if day is not None:
        try:
            if not journal_mod.is_day(day):
                raise ValueError
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return _err("date must be a valid YYYY-MM-DD day")
    text = journal_mod.read(current_app.instance_path, chip, day)
    return jsonify(ok=True, chip=chip, date=day or datetime.now().strftime("%Y-%m-%d"),
                   days=journal_mod.list_days(current_app.instance_path, chip),
                   chips=journal_mod.list_chips(current_app.instance_path),
                   file=str(journal_mod.day_file(current_app.instance_path, chip, day)),
                   text=text)


@agent_bp.route("/journal", methods=["POST"])
def journal_append():
    """The agent's own words. ``reason`` is REQUIRED for kind=agent: a log of
    what ran without why is what the customer already has. The actor is
    stamped from the caller, never from the payload: a request without the
    bridge header cannot claim to be the agent, and the agent cannot claim to
    be the human."""
    data = request.get_json(silent=True) or request.form.to_dict()
    kind = str(data.get("kind") or "agent")
    needs_reason = kind == "agent"
    if request.headers.get("X-SM-Agent"):
        # docs/252 (A-09): an agent's line is signed with the agent's own name,
        # whatever kind it asked for -- never `sm` (SM's bookkeeping, "armed by
        # human:Kim"), `human`, or the other CLI. Only the bridge's bookkeeping
        # of its own acts (`sm`: "applied 2 edits") needs no reason.
        needs_reason = kind != "sm"
        actor = _r()._request_actor()
        kind = actor if actor in journal_mod.KINDS else "agent"
    else:
        from quam_state_manager.web import callers
        needs_reason = False
        if not callers.from_person():
            kind = "unverified"           # no agent header and no person's window: SM cannot say who
        elif kind == "agent":
            kind = "human"
    text = str(data.get("text") or "").strip()
    reason = data.get("reason")
    if not text:
        return _err("text required")
    if needs_reason and not (reason and str(reason).strip()):
        return _err("reason required: say WHY (what you saw, what you changed, what you expect)")
    run_id = data.get("run_id")
    try:
        run_id = int(run_id) if run_id not in (None, "") else None
    except (TypeError, ValueError):
        return _err("run_id must be an integer")
    paths = data.get("paths") or []
    if isinstance(paths, str):
        paths = [p.strip() for p in paths.split(",") if p.strip()]
    chip = str(data.get("chip") or _chip_name())
    rec = journal_mod.append(current_app.instance_path, chip, text, kind=kind,
                             reason=str(reason) if reason else None, run_id=run_id, paths=list(paths))
    _bump()
    return jsonify(ok=True, entry=rec)


@agent_bp.route("/journal/root", methods=["GET", "POST"])
def journal_root():
    if request.method == "POST":
        # docs/252 (D-08 / C-15): a person's press only (callers.PERSON_ONLY); the
        # day files come along and the move is journaled in both folders
        data = request.get_json(silent=True) or request.form.to_dict()
        r = _r()
        try:
            moved = journal_mod.move_root(current_app.instance_path, data.get("root"), who=r._request_actor(),
                                          chip=_chip_name() if r._active_path() else None)
            root = moved["root"]
        except (OSError, ValueError) as exc:
            # docs/191 H06: a NUL in the path raises ValueError from the OS
            # call, not OSError, and answered 500 -- the same uncaught-kind
            # mistake `journal.read` made in H05.
            return _err(f"cannot use that folder: {exc}")
        if "agent_says" in data or "claude_says" in data:
            v = data.get("agent_says", data.get("claude_says"))
            journal_mod.set_agent_says(current_app.instance_path, str(v).lower() in ("1", "true", "on"))
        st = journal_mod.settings(current_app.instance_path)
        return jsonify(ok=True, root=str(root), agent_says=st["agent_says"], claude_says=st["agent_says"],
                       carried=moved.get("carried") or {})
    st = journal_mod.settings(current_app.instance_path)
    return jsonify(ok=True, root=str(journal_mod.root(current_app.instance_path)),
                   default=str(Path(current_app.instance_path) / "journal"),
                   agent_says=st["agent_says"], claude_says=st["agent_says"])


# ------------------------------------------------------- the live strip

def _events() -> collections.deque:
    ev = current_app.config.get("agent_events")
    if ev is None:
        ev = collections.deque(maxlen=_EVENT_RING)
        current_app.config["agent_events"] = ev
        current_app.config["agent_journaled"] = _load_journaled()
        _replay(ev)
    return ev


def _events_dir() -> Path:
    return Path(current_app.instance_path) / "agent_events"


def _journaled_file() -> Path:
    return _events_dir() / "journaled.txt"


def _load_journaled() -> set:
    try:
        return set(l.strip() for l in _journaled_file().read_text(encoding="utf-8").splitlines() if l.strip())
    except OSError:
        return set()


def _mark_journaled(key: str) -> None:
    current_app.config.setdefault("agent_journaled", set()).add(key)
    try:
        _events_dir().mkdir(parents=True, exist_ok=True)
        with open(_journaled_file(), "a", encoding="utf-8") as f:
            f.write(key + "\n")
    except OSError:
        pass


def _event_key(rec: dict) -> str:
    return f"{rec.get('session_id')}|{rec.get('tool_use_id')}|{rec.get('hook_event_name')}|{rec.get('ts')}"


def _replay(ev: collections.deque) -> None:
    """After a restart, the morning-after view still knows last night: the
    hook wrote every event to disk before it ever talked to us. Yesterday
    AND today -- a session that started at 22:00 crosses midnight."""
    today = datetime.now()
    # review R3-4: a night older than yesterday was never journaled; seven days, oldest first
    for day in [today - timedelta(days=k) for k in range(6, -1, -1)]:
        f = _events_dir() / (day.strftime("%Y-%m-%d") + ".jsonl")
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines[-_EVENT_RING:]:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ev.append(rec)
    # journal lines the hook could not write (SM was closed) -- derived now
    for rec in list(ev):
        try:
            _absorb(rec, from_replay=True)
        except Exception:  # noqa: BLE001
            logger.debug("replay absorb failed", exc_info=True)


def _bump() -> None:
    current_app.config["agent_seq"] = int(current_app.config.get("agent_seq") or 0) + 1


def _chip_for_event(rec: dict) -> str:
    """Which chip's journal an event belongs to. Named by the session's own
    state path when it says one (matching the open chip -> the open chip's
    name), else the open chip, else 'unassigned' -- never an invented name."""
    if rec.get("origin") in ("chat", "ask") and rec.get("chip") and rec.get("chip") != _UNASSIGNED:
        return str(rec["chip"])                  # the chat knows which chip it was started on
    r = _r()
    active = r._active_path()
    sp = rec.get("quam_state_path")
    if sp:
        try:
            if active and Path(sp).resolve() == Path(active).resolve():
                return _chip_name()
        except OSError:
            pass
        p = Path(sp)
        return p.parent.name if p.name.lower() in ("quam_state", "state.json") else p.name
    return _chip_name() if active else _UNASSIGNED


def _node_of(summary: str) -> str | None:
    m = _NODE_RE.search(summary or "")
    return m.group(1).split("/")[-1].split("\\")[-1] if m else None


def _pre_ts_of(rec: dict) -> float | None:
    tid = rec.get("tool_use_id")
    if not tid:
        return None
    for e in reversed(list(current_app.config.get("agent_events") or [])):
        if e.get("tool_use_id") == tid and e.get("hook_event_name") == "PreToolUse":
            try:
                return float(e.get("ts") or 0)
            except (TypeError, ValueError):
                return None
    return None


def _newest_run_since(ts: float | None) -> int | None:
    """The run folder that appeared after a node command started -- the
    stamp that turns 'ran power_rabi' into 'ran power_rabi -> #2711'."""
    if ts is None:
        return None
    ds = _ds()
    if not ds:
        return None
    from quam_state_manager.core import story
    try:
        for row in ds.list_runs()[:5]:
            # docs/262: the run's instant vs the hook's epoch, not the folder digits
            # read in this machine's zone (13 h off for a -04:00 lab on a +09:00 SM)
            when = story.run_epoch(row, ds)
            if when is not None and when >= ts - 5:
                return int(row["run_id"])
    except Exception:  # noqa: BLE001
        return None
    return None


def _author_kind(rec: dict) -> str:
    """docs/173 S8: the author a journal line is stamped with. A backend the
    event names is the agent that did it (by_claude / by_codex); a terminal
    hook SM cannot name stays `hook`; a node SM ran itself is the driving
    session's backend, filled by the caller."""
    b = (rec.get("backend") or "").lower()
    if b in ("claude", "codex"):
        return f"by_{b}"
    return "hook"


# D-17: every name a CLI gives its shell tool (Claude Code: Bash / PowerShell; Codex:
# exec_command / shell_command / shell) -- one list, read by the journal, the feed and the strip
_SHELL_TOOLS = ("Bash", "PowerShell", "exec_command", "shell_command", "shell")


def _journal_line(rec: dict) -> tuple[str | None, int | None, str]:
    """What of an event belongs in the human's notes, and WHO. A node run (with
    its run id and failure), a .py edit, and -- default ON, labelled -- what the
    agent said. Returns (line, run_id, author_kind)."""
    h = rec.get("hook_event_name")
    tool = rec.get("tool_name") or ""
    s = rec.get("summary") or ""
    who = _author_kind(rec)
    if h == "Stop":
        if rec.get("stopped"):
            return None, None, who                # the human's Stop is journaled by the door that pressed it
        if s and journal_mod.settings(current_app.instance_path)["agent_says"]:
            # docs/173 S8: the label IS the kind now (`by_claude` said …), not a "Claude:" prefix
            if len(s) > 600:
                marker = "\n[truncated]"
                head = s[:600 - len(marker)]
                boundary = head.rfind("\n")
                if boundary > 0:
                    head = head[:boundary]
                s = head + marker
            return s, None, (who if who != "hook" else "by_claude")
        return None, None, who
    if h not in ("PostToolUse", "PostToolUseFailure"):
        return None, None, who
    failed = bool(rec.get("failed"))
    err = (rec.get("error") or "").replace("\n", " ")[-200:]
    if tool.rsplit(".", 1)[-1] in _SHELL_TOOLS:
        node = _node_of(s)
        if not node:
            return None, None, who
        run_id = None if failed else _newest_run_since(_pre_ts_of(rec) or (float(rec.get("ts") or 0) - 3600))
        line = f"ran `{node}`"
        if failed:
            line = f"✗ `{node}` failed" + (f": {err}" if err else "")
        return line, run_id, who
    if tool in ("Edit", "Write", "MultiEdit") and s.endswith(".py"):
        return (f"✗ edit of `{s}` failed" if failed else f"edited `{s}`"), None, who
    return None, None, who


def _absorb(rec: dict, *, from_replay: bool = False) -> None:
    """Derive the journal line + notification for one event, exactly once."""
    key = _event_key(rec)
    done: set = current_app.config.setdefault("agent_journaled", set())
    if key in done:
        return
    line, run_id, who = _journal_line(rec)
    if line:
        when = None
        try:
            when = datetime.fromtimestamp(float(rec.get("ts")))   # review R3-4: the event's own time, not now
        except (TypeError, ValueError, OSError):
            when = None
        journal_mod.append(current_app.instance_path, _chip_for_event(rec), line, kind=who, run_id=run_id, when=when)
    _mark_journaled(key)
    if rec.get("failed") and not from_replay:
        _notify("agent_failure", {"tool": rec.get("tool_name"), "summary": rec.get("summary"),
                                  "error": rec.get("error"), "session": rec.get("session_id")})


def _notify(event: str, payload: dict) -> None:
    try:
        from quam_state_manager.core.autofit import notify
        notify.notify(current_app.instance_path, event, payload)
    except Exception:  # noqa: BLE001
        logger.debug("notify failed", exc_info=True)


#: Fields only SM's own in-process chat recorder writes (chat_api._record).
_CHAT_ONLY_FIELDS = ("origin", "n", "ask_id", "readonly", "owner", "chip", "who")


@agent_bp.route("/event", methods=["POST"])
def event_post():
    """One hook event. The script has already appended it to disk; here it
    becomes the live wake, a journal line (once), and a notification."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _err("json object required")
    # docs/252 (A-09): the hook proved itself (callers.HOOK_ONLY). What it
    # cannot carry is the in-process marks SM puts on its OWN chat events -- an
    # `origin` would put the record into the Agent panel's feed as the agent's
    # words, and its `chip` would be trusted as the journal's chip.
    for k in _CHAT_ONLY_FIELDS:
        data.pop(k, None)
    data.setdefault("ts", time.time())
    with _events_lock:
        _events().append(data)
    try:
        _absorb(data)
    except Exception:  # noqa: BLE001
        logger.debug("absorb failed", exc_info=True)
    _bump()
    _wake()
    return jsonify(ok=True)


def _wake() -> None:
    """One wake for the whole page: the run watcher's tick is what every
    open tab's live-wake long-poll waits on (docs/141 §4p)."""
    try:
        w = current_app.config.get("run_watcher")
        if w is not None:
            w.bump("agent")
    except Exception:  # noqa: BLE001
        logger.debug("wake failed", exc_info=True)


def _relevant(session_events: list[dict]) -> bool:
    """A Claude Code session that never touched SM or a calibration node is
    someone's paper-writing session: it must not light the strip."""
    from quam_state_manager.core.agent_link import is_sm_mcp_tool
    for e in session_events:
        if is_sm_mcp_tool(e.get("tool_name") or ""):
            return True
        if (e.get("tool_name") or "").rsplit(".", 1)[-1] in _SHELL_TOOLS and _node_of(e.get("summary") or ""):
            return True
    return False


def _alive(session_events: list[dict], now: float) -> bool:
    """Signs of life: an event inside the window, or the session's own
    transcript file still growing (Claude Code appends to it while alive)."""
    last = max((float(e.get("ts") or 0) for e in session_events), default=0.0)
    if now - last < _LIVE_WINDOW_S:
        return True
    tp = next((e.get("transcript_path") for e in reversed(session_events) if e.get("transcript_path")), None)
    if tp:
        try:
            return now - Path(tp).stat().st_mtime < _LIVE_WINDOW_S
        except OSError:
            return False
    return False


_HUMAN_RECENT_S = 30 * 60


def _mode_and_limits() -> dict:
    from quam_state_manager.core import limits
    try:
        return limits.load(current_app.instance_path, _chip_key())
    except Exception:  # noqa: BLE001
        return dict(limits.DEFAULTS)


def _session() -> dict | None:
    from quam_state_manager.core import agent_session
    try:
        return agent_session.load(current_app.instance_path, _chip_key())
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------ docs/253: arming is a grant for ONE plan, ONE driver

def _request_driver(rec: dict | None) -> dict:
    """Which agent sent this request (agent_grant.request_driver over Flask's request)."""
    from quam_state_manager.core import agent_grant
    return agent_grant.request_driver(request.headers, _r()._request_actor(), request.remote_addr, rec)


def _app_open(secret: str | None) -> bool:
    """Is SM's in-app session that holds ``secret`` still open in THIS process?"""
    mgr = current_app.config.get("agent_chat")
    if mgr is None or not secret:
        return False
    cur = mgr.get(_chip_key())
    if cur is None or getattr(cur, "secret", None) != secret:
        return False
    from quam_state_manager.web import chat_api
    return chat_api.session_open(cur)


def _reconcile_grant() -> str | None:
    """End the open chip's grant if it no longer holds (its plan ended, SM
    restarted, its driver is gone). Every reader calls this first, so no
    surface ever shows -- and no run_node ever passes -- a grant that ended."""
    if not _r()._active_path():
        return None
    from quam_state_manager.core import agent_grant
    try:
        why = agent_grant.reconcile(current_app.instance_path, _chip_key(), app_open=_app_open, name=_chip_name())
    except Exception:  # noqa: BLE001
        logger.warning("grant reconcile failed", exc_info=True)
        return None
    if why:
        _bump()
        _wake()
    return why


def watch_grants_once(app) -> list[str]:
    """docs/261: the lazy half of the arming lifecycle (``reconcile``) runs on every read -- but a
    night with nobody reading (the laptop closed, the agent between steps) would notice 06:00 only
    in the morning, and the plan_done alert with it. This pass reconciles every grant THIS process
    armed (another live window's grant is that window's), whoever has a chip open. Returns the
    keys whose grant ended."""
    from quam_state_manager.core import agent_grant, agent_session
    from quam_state_manager.web import chat_api
    inst = app.instance_path
    out: list[str] = []
    try:
        files = sorted(agent_session._dir(inst).glob("*.json"))
    except OSError:
        return out
    for f in files:
        rec = agent_session.read_record(f)
        if not isinstance(rec, dict) or not rec.get("start_token"):
            continue
        g = rec.get("grant") or {}
        if not g or g.get("sm_pid") != os.getpid() or g.get("sm_boot") != agent_grant.BOOT:
            continue
        key = str(rec.get("chip") or f.stem)

        def app_open(secret, _key=key):
            mgr = app.config.get("agent_chat")
            cur = mgr.get(_key) if mgr is not None and secret else None
            return bool(cur is not None and getattr(cur, "secret", None) == secret and chat_api.session_open(cur))
        try:
            with app.app_context():
                why = agent_grant.reconcile(inst, key, app_open=app_open, name=g.get("name"))
        except Exception:  # noqa: BLE001
            logger.warning("grant watch failed for %s", key, exc_info=True)
            continue
        if why:
            out.append(key)
            try:
                with app.app_context():
                    _bump()
                    _wake()
            except Exception:  # noqa: BLE001
                logger.debug("grant watch wake failed", exc_info=True)
    return out


def start_grant_watch(app, every_s: float) -> threading.Thread | None:
    """Run :func:`watch_grants_once` every ``every_s`` seconds on a daemon thread (0 = off)."""
    if not every_s or every_s <= 0:
        return None

    def loop():
        while True:
            time.sleep(every_s)
            try:
                watch_grants_once(app)
            except Exception:  # noqa: BLE001
                logger.warning("grant watch pass failed", exc_info=True)
    t = threading.Thread(target=loop, name="sm-grant-watch", daemon=True)
    t.start()
    return t


def _end_grant(why: str, *, driver_kind: str | None = None, after_run: bool = True) -> dict | None:
    """End the open chip's grant by a person's hand (Disarm, End session,
    Cancel): no journal line of its own -- the caller's line says it -- and a
    plan it armed is closed (a step still running finishes and reports)."""
    from quam_state_manager.core import agent_grant
    inst, key = current_app.instance_path, _chip_key()
    g = agent_grant.end(inst, key, why=why, driver_kind=driver_kind, journal=False)
    if g:
        agent_grant.close_plan(inst, key, g, why=why, restarted=not after_run)
    return g


def _human_ran_recently(now: float, agent_runs: dict, ev: list[dict]) -> dict | None:
    """The newest run folder with no agent origin inside the window: a person
    (or a session SM cannot see) is on the OPX. A FACT, past tense."""
    ds = _ds()
    if ds is None:
        return None
    try:
        rows = ds.list_runs()[:3]
    except Exception:  # noqa: BLE001
        return None
    from quam_state_manager.core import story
    try:
        mine = story.unattributed_agent_runs(current_app.instance_path, _chip_key(), since=now - 6 * 3600)
    except Exception:  # noqa: BLE001
        mine = []
    for row in rows:
        # docs/262: the run's instant vs ``now`` and the hook stamps; the returned
        # ``ts`` is that instant too (the run adapter compares it with time.time())
        when = story.run_epoch(row, ds)
        if when is None:
            continue
        if now - when > _HUMAN_RECENT_S:
            continue
        rid = int(row["run_id"])
        if rid in agent_runs:
            continue
        # review R3-2: a run the agent made whose id arrived late is the agent's, not a person's
        want = story._norm(row.get("experiment_name") or "")
        if any(story._norm(m.get("node") or "") and want.startswith(story._norm(m.get("node") or ""))
               and abs(float(m.get("ts") or 0) - when) < 900 for m in mine):
            continue
        want = story._norm(row.get("experiment_name") or "")
        hooked = any((e.get("tool_name") or "").rsplit(".", 1)[-1] in _SHELL_TOOLS
                     and e.get("hook_event_name") in ("PostToolUse", "PreToolUse")
                     and abs(float(e.get("ts") or 0) - when) < 600
                     and (story._node_of(e.get("summary") or "") or "") and want
                     and (story._norm(story._node_of(e.get("summary") or "")) in want) for e in ev)
        if hooked:
            continue
        return {"run_id": rid, "node": row.get("experiment_name"), "ts": when,
                "targets": list(row.get("qubits") or [])}
    return None


def _clock_now() -> float:
    """The wall clock the live strip judges by. A seam: the stalled-pre pin
    freezes it (the real clock made that pin red for the first two hours after
    midnight, when "two hours ago" fell on yesterday)."""
    return time.time()


def _clock_today() -> datetime:
    """Local calendar time for the same judgement (``events_today``)."""
    return datetime.now()


_RUN_METAS_MEMO: dict = {}


def _persisted_run_metas() -> dict[str, dict]:
    """Every ``agent_runs/<key>/meta.json``, re-read only when the folder gains or
    loses a run (the pill polls; a full scan per poll would grow with history).
    A meta rewritten in place is current in the in-process registry instead."""
    root = Path(current_app.instance_path) / "agent_runs"
    try:
        st = root.stat()
        sig = (str(root), st.st_mtime_ns, sum(1 for _ in root.iterdir()))
    except OSError:
        return {}
    if _RUN_METAS_MEMO.get("sig") == sig:
        return _RUN_METAS_MEMO["records"]
    records = {}
    for path in root.glob("*/meta.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(record, dict) and record.get("key"):
                records[record["key"]] = record
        except (OSError, ValueError):
            continue
    _RUN_METAS_MEMO.update(sig=sig, records=records)
    return records


def _failed_runs_today(day_start: float, day_end: float, sessions: dict[str, list[dict]]) -> list[float]:
    """C-10: the end times of today's FAILED NODE RUNS on the open chip -- what a
    person cares about. Two sources, never a tool-call error: run_node's own runs
    (failure classes of docs/249, the chip's machine key) and a node a terminal
    agent ran through its shell that failed (the journal's own "failed" line, on
    the open chip's journal)."""
    from quam_state_manager.core.agent_overnight import FAIL_CLASSES
    chip = _chip_key()
    records = dict(_persisted_run_metas())
    registry = current_app.config.get("agent_run_registry")
    if registry is not None:
        records.update(registry.runs)
    ends = [float(record.get("ended") or 0) for record in records.values()
            if record.get("chip") == chip
            and (record.get("result") or {}).get("classification") in FAIL_CLASSES
            and (record.get("result") or {}).get("status") != "refused"
            and day_start <= float(record.get("ended") or 0) < day_end]
    journal_chip = None
    for es in sessions.values():
        for e in es:
            ts = float(e.get("ts") or 0)
            if not (e.get("failed") and day_start <= ts < day_end):
                continue
            if (e.get("tool_name") or "").rsplit(".", 1)[-1] not in _SHELL_TOOLS or not _node_of(e.get("summary") or ""):
                continue
            if journal_chip is None:
                journal_chip = _chip_name() if _r()._active_path() else _UNASSIGNED
            if _chip_for_event(e) == journal_chip:
                ends.append(ts)
    return ends


def _now_state() -> dict:
    """The pill's one state, in the precedence order of docs/173 §3.1:
    waiting > limited > stalled > failed > running > between > human-ran > idle."""
    from quam_state_manager.core import agent_plans, agent_session, story
    from quam_state_manager.core.agent_runs import run_mode
    _reconcile_grant()                                # docs/253: never show a grant that ended
    now = _clock_now()
    with _events_lock:
        ev = list(_events())
    seq = int(current_app.config.get("agent_seq") or 0)
    lim = _mode_and_limits()
    sess = _session()
    # C-11: the mode the next run obeys, by the run's own rule (agent_runs.run_mode) --
    # an armed plan's mode, not the chip's default
    armed = bool((sess or {}).get("start_token"))
    plan = None
    if armed and sess.get("plan_id"):
        plan = agent_plans.get(current_app.instance_path, _chip_key(), sess["plan_id"])
        if plan is not None and plan.get("status") not in ("running", "stopping"):
            plan = None
    mode = run_mode(plan, sess if armed else None, lim)
    base = {"seq": seq, "mode": mode, "session": agent_session.summary(sess),
            "events_today": 0, "failures_today": 0, "waiting": _waiting_count()}
    agent_runs = story.load_agent_runs(current_app.instance_path, _chip_key())
    by_session: dict[str, list[dict]] = collections.defaultdict(list)
    for e in ev:
        by_session[str(e.get("session_id"))].append(e)
    sessions = {sid: es for sid, es in by_session.items() if _relevant(es)}
    today = _clock_today().replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = today.timestamp()
    failed_ends = _failed_runs_today(day_start, (today + timedelta(days=1)).timestamp(), sessions)
    failures = len(failed_ends)
    recent_failure = any(0 <= now - t < 3600 for t in failed_ends)
    base["failures_today"] = failures
    limited_until = (sess or {}).get("limited_until")
    for es in sessions.values():
        for e in es:
            if e.get("limited") and now - float(e.get("ts") or 0) < 6 * 3600:
                limited_until = e.get("limited_until") or limited_until or True
    if base["waiting"]:
        return {**base, "state": "waiting"}
    lim_ts, lim_text = _limit_until(limited_until)
    if lim_ts is True or (lim_ts and lim_ts > now):
        resets = lim_text if lim_ts is True else datetime.fromtimestamp(lim_ts).strftime("%H:%M")
        return {**base, "state": "limited", "limited_resets": resets}
    human = _human_ran_recently(now, agent_runs, ev)
    if not sessions:
        state = "failed" if recent_failure else "human-ran" if human else "idle"
        return {**base, "state": state, "human_ran": human,
                "note": "events seen, none from a calibration session" if ev and not human else None}
    sid, es = max(sessions.items(), key=lambda kv: max(float(e.get("ts") or 0) for e in kv[1]))
    open_tools: dict[str, dict] = {}
    last_stop = None
    for e in es:
        h = e.get("hook_event_name")
        tid = e.get("tool_use_id")
        if h == "PreToolUse" and tid:
            open_tools[tid] = e
        elif h in ("PostToolUse", "PostToolUseFailure") and tid:
            open_tools.pop(tid, None)
        elif h == "Stop":
            open_tools.clear()
            last_stop = e
    alive = _alive(es, now) or agent_session.alive(sess)
    # A session a person STOPPED is not "thinking". The event window above is
    # a guess for sessions SM cannot see into; for its own session SM KNOWS:
    # the stop is on record and the process is gone. Measured before this: the
    # pill and the strip said "thinking · by_claude" for the window's whole
    # 15 min after Stop now had killed the agent (QA agents round). A sign of
    # life after the stop (a resumed session) still counts.
    # The same holds for SM's own chat-window session whose recorded process
    # is gone: Arm clears the stop, and the strip went back to "thinking" (with
    # Stop buttons for nothing) over a process that no longer exists.
    stop = (sess or {}).get("agent_stop") or {}
    own = bool(sess) and str(sess.get("session_id") or "") == str(sid) and not agent_session.alive(sess)
    if alive and own and stop.get("at"):
        stop_at = float(stop["at"])
        if not any(float(e.get("ts") or 0) > stop_at and e.get("hook_event_name") != "Stop" for e in es):
            alive = False
    elif alive and own and sess.get("window") == "chat" and (sess.get("pid") or sess.get("worker_pid")):
        alive = False
    running = None
    if open_tools and alive:
        e = sorted(open_tools.values(), key=lambda x: float(x.get("ts") or 0))[-1]
        node = story._node_of(e.get("summary") or "")
        running = {"tool": e.get("tool_name"), "summary": e.get("summary"), "node": node,
                   "since": e.get("ts"), "session": e.get("session_id"), "backend": e.get("backend"),
                   "typical_s": _typical_duration(node)}
    last = es[-1]
    if running:
        state = "running"
    elif open_tools and not alive:
        state = "stalled"
    elif recent_failure:
        state = "failed"
    elif alive:
        state = "between"
    elif human:
        state = "human-ran"
    else:
        state = "idle"
    if state == "running" and failures:
        pass                                    # a running agent outranks the day's count; the count rides along
    return {**base, "state": state, "running": running, "session_id": sid, "alive": alive,
            "last": {"tool": last.get("tool_name"), "event": last.get("hook_event_name"),
                     "summary": last.get("summary"), "ts": last.get("ts"), "failed": bool(last.get("failed")),
                     "backend": last.get("backend")},
            "last_message": (last_stop or {}).get("summary") if last_stop else None,
            "events_today": sum(1 for e in es if float(e.get("ts") or 0) >= day_start),
            "human_ran": human, "chip": _chip_for_event(last)}


def _limit_until(v):
    """(timestamp | True | None, text). A float is a reset time; True means
    limited with no known reset; a clock string the CLI printed is parsed,
    and kept as text when it cannot be."""
    if not v:
        return None, None
    if v is True:
        return True, None
    try:
        return float(v), None
    except (TypeError, ValueError):
        pass
    from quam_state_manager.core import agent_backend
    ts = agent_backend.reset_timestamp(str(v))
    return (ts, None) if ts else (True, str(v))


def _waiting_count() -> int:
    """Approval cards waiting for a human (docs/173 S5: a held write lives
    OUTSIDE the tray, in core/approvals.py -- the working copy applies whole)."""
    from quam_state_manager.core import approvals
    if not _r()._active_path():
        return 0
    try:
        return len(approvals.pending(current_app.instance_path, _chip_key()))
    except Exception:  # noqa: BLE001
        return 0


def _stale_since() -> float | None:
    """When the live files last moved, for an answer that says live_diverged
    (docs/173 S5 ``stale_since``): the newest mtime of the two live files."""
    r = _r()
    ctx = r._active_ctx()
    if not ctx or not ctx.get("path"):
        return None
    best = None
    for name in ("state.json", "wiring.json"):
        try:
            m = (Path(ctx["path"]) / name).stat().st_mtime
            best = m if best is None else max(best, m)
        except OSError:
            continue
    return best


def _typical_duration(node: str | None) -> float | None:
    """The family's typical run length from the archive -- for 'usually ~N min'."""
    if not node:
        return None
    ds = _ds()
    if ds is None:
        return None
    try:
        rows = [r for r in ds.list_runs(experiment=node)[:12] if isinstance(r.get("duration_s"), (int, float))]
        if len(rows) < 2:
            return None
        vals = sorted(float(r["duration_s"]) for r in rows)
        return vals[len(vals) // 2]
    except Exception:  # noqa: BLE001
        return None


@agent_bp.route("/now")
def now():
    return jsonify(ok=True, **_now_state())


@agent_bp.route("/limits", methods=["GET", "POST"])
def limits_route():
    """Per-chip Limits + the default mode (docs/173 S3b). A mode change is
    journaled with who pressed it."""
    from quam_state_manager.core import limits
    from quam_state_manager.web import routes as r
    chip, key = _chip_name(), _chip_key()      # records by KEY (what run_node reads), journal by NAME
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form.to_dict()
        if "max_delta" in data and isinstance(data["max_delta"], str):
            try:
                data["max_delta"] = json.loads(data["max_delta"] or "{}")
            except ValueError:
                return _err("max_delta must be JSON")
        try:
            cur = limits.save(current_app.instance_path, key, data, who=r._request_actor(), journal_chip=chip)
        except limits.LimitError as exc:
            return _err(str(exc))
        _bump()
        _wake()
        # docs/191 C02: `limits.validate` ignores a key it does not know (an
        # older SM must survive a newer patch, and that is pinned). The door
        # answered a plain 200 for it, so `{"max_runs": 5}` -- or a typo like
        # `max_writes` for `max_writes_per_plan` -- read as saved and changed
        # nothing. These are the numbers the run gates read; the answer names
        # what it did not take.
        ignored = sorted(k for k in (data or {}) if k not in limits.DEFAULTS)
        out = {"ok": True, "chip": chip, "limits": cur}
        if ignored:
            out["ignored"] = ignored
            out["note"] = ("not saved -- there is no limit called "
                           + ", ".join(ignored) + "; the ones there are: "
                           + ", ".join(sorted(limits.DEFAULTS)))
        return jsonify(**out)
    return jsonify(ok=True, chip=chip, limits=limits.load(current_app.instance_path, key),
                   modes=list(limits.MODES))


@agent_bp.route("/session", methods=["GET"])
def session_get():
    from quam_state_manager.core import agent_session
    _reconcile_grant()
    # the record is keyed by the chip's KEY (every writer's), not its display name -- by the name
    # this door answered null for every session there was (stream D, P3)
    rec = agent_session.load(current_app.instance_path, _chip_key())
    return jsonify(ok=True, chip=_chip_name(), session=agent_session.summary(rec))


@agent_bp.route("/session/stop", methods=["POST"])
def session_stop():
    """Stop, recorded first (docs/173 §3.3): run_node reads the flag before
    anything else; S4 adds the process kill for 'now'."""
    from quam_state_manager.core import agent_session
    from quam_state_manager.web import routes as r
    data = request.get_json(silent=True) or request.form.to_dict()
    mode = "now" if str(data.get("mode") or "") == "now" else "after_run"
    key = _chip_key()
    before = agent_session.load(current_app.instance_path, key)
    if before is None:
        return _err("no agent session on this chip", 409)
    already = (before.get("agent_stop") or {}).get("mode") == mode
    rec = agent_session.request_stop(current_app.instance_path, key, who=r._request_actor(), mode=mode)
    mgr = current_app.config.get("agent_chat")
    killed_here = False
    if mgr is not None:
        try:
            killed_here = mgr.get(key) is not None and mgr.get(key).alive()
            mgr.stop(key, now=(mode == "now"))   # recorded first (above), killed second
        except Exception:  # noqa: BLE001
            logger.debug("chat stop failed", exc_info=True)
    if mode == "now" and not killed_here and agent_session.pid_alive(before.get("pid")):
        # review R3-6: the session belongs to ANOTHER SM process on this instance dir --
        # "Stop now" from this window still means the process dies
        try:
            from quam_state_manager.core import agent_backend
            agent_backend.kill_tree(int(before["pid"]))
        except Exception:  # noqa: BLE001
            logger.debug("cross-process kill failed", exc_info=True)
    try:
        # docs/173 S6: the plan card closes too -- "Stopped by <who>", pending steps cancelled
        from quam_state_manager.core import agent_plans
        running_plan = agent_plans.running(current_app.instance_path, key)
        if running_plan is not None:
            agent_plans.stop(current_app.instance_path, key, running_plan["id"], who=r._request_actor(),
                             how="stop now" if mode == "now" else "stop after this run")
            if mode == "now":
                agent_session.save(current_app.instance_path, key, mode=_mode_and_limits().get("mode"))
    except Exception:  # noqa: BLE001
        logger.debug("plan stop failed", exc_info=True)
    if not already:                              # review R3-8: a double click is one line
        g = before.get("grant") if before.get("start_token") else None
        journal_mod.append(current_app.instance_path, _chip_name(),
                           f"Stop ({'now' if mode == 'now' else 'after this run'}) pressed by {r._request_actor()}"
                           + (f" -- disarmed (plan `{g.get('title')}`)" if g else ""), kind="sm")
    _bump()
    _wake()
    return jsonify(ok=True, session=agent_session.summary(rec))


@agent_bp.route("/events")
def events_list():
    n = max(1, min(int(request.args.get("n") or 50), _EVENT_RING))
    with _events_lock:
        ev = list(_events())[-n:]
    return jsonify(ok=True, count=len(ev), events=ev)


# ================================================================ S5: run_node
# SM runs the node (core/agent_runs.py); these routes are the agent's door,
# the human's Arm / approve / reject clicks, and the adapter that hands the
# engine everything it needs from the app.

def _registry():
    from quam_state_manager.core import agent_runs
    app = current_app._get_current_object()
    reg = app.config.get("agent_run_registry")
    if reg is None:
        reg = agent_runs.Registry(app.instance_path)
        app.config["agent_run_registry"] = reg
    return reg


def _run_active_view() -> dict | None:
    try:
        if not _r()._active_path():
            return None
        m = _registry().active_for(_chip_key())          # the registry keys runs by the chip's records KEY
        return {k: m.get(k) for k in ("key", "node", "targets", "since", "actor")} if m else None
    except Exception:  # noqa: BLE001
        return None


_STATUS_P = re.compile(r"<p>(.*?)</p>", re.S)


def _status_text(body) -> str | None:
    """verifier P2: the door answers a failed LIVE write with the ``_status``
    HTML fragment, not JSON -- read its message (``Apply to live failed:
    <WinError 32 ...>``) instead of reporting a bare "HTTP 500"."""
    try:
        raw = body.get_data(as_text=True) if hasattr(body, "get_data") else str(body or "")
    except Exception:  # noqa: BLE001
        return None
    m = _STATUS_P.search(raw or "")
    if not m:
        return None
    import html as _html
    txt = _html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    return " ".join(txt.split())[:400] or None


def _pre_door_state(r, ctx) -> dict:
    """The facts a push refused after its save has to put back: the outgoing
    log, the working copy's dirty flag + re-apply stash, and which undo
    journal units existed (read from the sidecar, never the RAM mirror, which
    may not be loaded yet)."""
    import copy as _copy
    from quam_state_manager.core import undo_journal
    try:
        ids = {str(u.get("id")) for u in undo_journal.load(
            undo_journal.sidecar_path(current_app.instance_path, ctx["path"]))}
    except Exception:  # noqa: BLE001
        ids = None
    return {"log": list(ctx["store"].change_log), "dirty": bool(ctx.get("working_dirty")),
            "reapply": _copy.deepcopy(ctx.get("pending_reapply")),
            "reapply_orig": _copy.deepcopy(ctx.get("pending_reapply_orig")), "units": ids}


def _take_back_saved(r, ctx, staged: list, before: dict) -> str | None:
    """Undo a group the door SAVED into the working copy but could not push:
    write each leaf's old value back, save the working copy, drop the journal
    unit the save recorded, and restore the dirty flag + re-apply stash. None
    on success, else why it could not (the values then stay, and say so)."""
    from quam_state_manager.core import undo_journal
    store, mod, saver = ctx["store"], ctx["modifier"], ctx["saver"]
    ours = {e.dot_path for e in staged}
    try:
        with store._lock:
            for e in reversed(staged):
                # docs/255 P3: the old value goes back VERBATIM -- a coercing write turned an
                # int field widened by a float back into 7126044234.0, which then reached live
                inv = mod.set_value(e.dot_path, e.old_value, _defer_hooks=True, coerce=False,
                                    group_id=f"{e.group_id}:takeback")
                inv.actor = getattr(e, "actor", "human")
            store._clear_pointer_cache()
            if store.search_index is not None:
                for e in staged:
                    store.search_index.update_entry(e.dot_path, e.old_value)
        with r._active_wc_lock(ctx):
            saver.save()                        # clears the inverse entries from the log
    except Exception as exc:  # noqa: BLE001
        logger.warning("taking a refused approval back out of the working copy failed", exc_info=True)
        return f"{type(exc).__name__}: {str(exc)[:160]}"
    only_ours = all(e.dot_path in ours for e in before["log"])
    if only_ours:
        ctx["working_dirty"] = before["dirty"]
        ctx["pending_reapply"] = before["reapply"]
        if before["reapply_orig"] is None:
            ctx.pop("pending_reapply_orig", None)
        else:
            ctx["pending_reapply_orig"] = before["reapply_orig"]
    else:
        # another agent group rode the same push: its values stay saved-unapplied
        # (as before); only this group's paths leave the stash
        for k in ("pending_reapply", "pending_reapply_orig"):
            if isinstance(ctx.get(k), dict):
                prev = before.get(k[len("pending_"):]) or {}
                ctx[k] = {p: v for p, v in ctx[k].items() if p not in ours or p in prev}
    if before.get("units") is not None:
        try:
            path = undo_journal.sidecar_path(current_app.instance_path, ctx["path"])
            new = [u for u in undo_journal.load(path)
                   if str(u.get("id")) not in before["units"]
                   and u.get("entries") and all(en.get("path") in ours for en in u["entries"])]
            if new:
                units = undo_journal.drop_units(path, [u["id"] for u in new])
                ctx["undo_units"] = units
                ctx["undo_cursor"] = undo_journal.load_state(path)[1]
                ctx["undo_sidecar_mtime"] = undo_journal.sidecar_mtime(path)
        except Exception:  # noqa: BLE001 -- the journal is advisory
            logger.warning("dropping a refused approval's journal unit failed", exc_info=True)
    return None


def _stage_writes(app, live: str, writes: list[dict], gid: str, actor: str, plan_id: str | None,
                  apply: bool, *, presser: str | None = None) -> dict:
    """Stage ``writes`` onto the chip's working copy as ONE group with the
    agent's actor and, when ``apply``, push them through the ONE door
    (``/state/apply-to-live``) as the presser's own press -- the agent's
    (X-SM-Agent) or, for an approval, the human's (X-SM-Actor). Refused by
    the door => the group is un-staged again so the caller can park it."""
    r = _r()
    ctx = r._find_quam_ctx_by_path(live)
    if ctx is None or ctx.get("type") != "quam":
        return {"group_id": gid, "staged": 0, "applied": False, "error": "the chip is no longer loaded",
                "unstaged": [{"path": w["path"], "why": "chip not loaded"} for w in writes[:50]]}
    mod, store = ctx["modifier"], ctx["store"]
    if presser and not str(presser).startswith("by_") and store.change_log:
        # review R1-M1: a person's approval press consents to THESE writes, never to whatever
        # else the tray holds (another window's edit would ride along under this name)
        return {"group_id": gid, "staged": 0, "applied": False, "unstaged": [],
                "error": f"the tray holds {len(store.change_log)} staged edit(s); apply or discard them in the window "
                         "first, then approve"}
    staged, unstaged = [], []
    with store._lock:
        for w in writes:
            if w.get("created") or w.get("deleted"):
                unstaged.append({"path": w["path"], "why": "new/removed key -- SM stages existing leaves only; "
                                                            "apply the run's state from Datasets → Apply to chip"})
                continue
            try:
                # review R2-2: a pointer alias (x180 -> #./x180_DragCosine) is edited at its target,
                # the way /field/edit does it
                target = r._resolve_edit_path(store, w["path"]) or w["path"]
                e = mod.set_value(target, w["new"], _defer_hooks=True, group_id=gid)
                e.actor = actor
                staged.append(e)
            except Exception as exc:  # noqa: BLE001
                unstaged.append({"path": w["path"], "why": f"{type(exc).__name__}: {str(exc)[:160]}"})
        if unstaged and staged:
            # all or nothing: a partial write would put half a run on the chip
            while store.change_log and store.change_log[-1].group_id == gid:
                mod.undo_group()
            staged = []
        store._clear_pointer_cache()
        if store.search_index is not None:
            for e in staged:
                store.search_index.update_entry(e.dot_path, e.new_value)
    out = {"group_id": gid, "staged": len(staged), "unstaged": unstaged, "applied": False, "error": None}
    if unstaged:
        out["error"] = f"{len(unstaged)} of {len(writes)} value(s) cannot be staged ({unstaged[0]['path']}: " \
                       f"{unstaged[0]['why'][:80]}); nothing was written"
        return out
    if not staged or not apply:
        return out
    if r._active_ctx() is not ctx:
        out["error"] = "another chip is active in the window; staged only"
        return out

    def _take_back():
        with store._lock:                      # the group comes back out of the tray
            while store.change_log and store.change_log[-1].group_id == gid:
                mod.undo_group()
    # The door SAVES the log into the working copy before it writes the chip and
    # only then notices a moved chip (docs/65's re-apply stash) -- a refusal there
    # would leave the agent's values half-way, in SM but not on the chip. Ask first.
    try:
        from quam_state_manager.core import working_copy as wc_mod
        if wc_mod.live_diverged_now(ctx["working_copy"]):
            _take_back()
            out["error"] = "stale_live: the live files moved outside SM since the last sync (take live first)"
            return out
    except Exception:  # noqa: BLE001
        logger.debug("live_diverged_now failed", exc_info=True)
    n = len(store.change_log)
    # verifier P0 (w7/agentsqa): what SM looked like before the door, so a push
    # refused AFTER the door's save can be taken back to exactly this
    before = _pre_door_state(r, ctx)
    headers = {"Accept": "application/json"}
    who = presser or actor
    if str(who).startswith("by_"):
        headers["X-SM-Agent"] = str(who)[3:]
    elif ":" in str(who):
        # English only, like the browser: this is an HTTP header, and a
        # non-ISO-8859-1 value is not sendable at all.
        headers["X-SM-Actor"] = _ascii_actor(str(who).split(":", 1)[1])
    if plan_id:
        headers["X-SM-Plan"] = str(plan_id)
    # docs/246 A-07: the door applies the ACTIVE chip; these writes were staged
    # on `ctx`. The check above can be overtaken by a /load before the door
    # runs -- naming the chip makes the door itself refuse the other one.
    door = {"seen_changes": str(n)}
    try:
        from quam_state_manager.core import working_copy as wc_mod
        door["expect_chip_key"] = wc_mod.key_for(ctx["path"])
    except Exception:  # noqa: BLE001 -- no key: the door's count gate as before
        logger.debug("key_for failed", exc_info=True)
    try:
        with app.test_request_context("/state/apply-to-live", method="POST",
                                      data=door, headers=headers):
            resp = r.state_apply_to_live()
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent apply failed")
        resp = (jsonify(ok=False, error=f"{type(exc).__name__}: {exc}"), 500)
    status = resp[1] if isinstance(resp, tuple) else getattr(resp, "status_code", 200)
    body = resp[0] if isinstance(resp, tuple) else resp
    if status != 200:
        js = body.get_json(silent=True) if hasattr(body, "get_json") else None
        out["error"] = ((js or {}).get("message") or (js or {}).get("conflict") or (js or {}).get("error")
                        or _status_text(body) or f"HTTP {status}")
        if store.change_log:
            _take_back()                       # refused BEFORE the save: the group comes back out
        else:
            # refused AFTER the save (the live write itself failed -- a reader
            # holding state.json open on Windows). The values used to stay in
            # the working copy as unapplied edits that no approval owned: a
            # Reject left them there and the NEXT approval's push carried them
            # to the chip under its own name. Take them back out, so a refused
            # press leaves SM exactly where it found it.
            why = _take_back_saved(r, ctx, staged, before)
            if why is None:
                out["error"] += " -- nothing was written; the approval's values were taken back out of SM"
            else:
                out["saved_in_working_copy"] = True
                out["error"] += (" -- the values are saved in SM's working copy (unapplied) and could not be "
                                 f"taken back ({why}); a human decides in the window")
        return out
    if r._change_count() == 0 and not ctx.get("live_diverged"):
        out["applied"] = True
        # docs/261: the door took a pre-apply version first; the summary names it beside the undo
        out["pre_apply_ts"] = (ctx.get("last_apply") or {}).get("pre_ts")
    else:
        out["error"] = "SM did not clear the tray"
    return out


def _run_adapter():
    """The engine's view of this app for the chip open NOW, captured on the
    request thread; every callable re-enters an app context on the driver."""
    from quam_state_manager.core import agent_runs, limits, scheduler, story
    app = current_app._get_current_object()
    r = _r()
    ctx = r._active_ctx()
    chip = _chip_key()                          # the records' key
    name = _chip_name()                         # the journal's name
    live = str(ctx["path"])
    wc = ctx["working_copy"]
    scope = r._sched_inst()
    inst = app.instance_path

    def settings():
        return scheduler.load_settings(scope)

    def human_recent(window_min: float):
        with app.app_context():
            try:
                with _events_lock:
                    ev = list(_events())
                h = _human_ran_recently(time.time(), story.load_agent_runs(inst, chip), ev)
                if h and time.time() - float(h.get("ts") or 0) <= float(window_min) * 60:
                    return h
            except Exception:  # noqa: BLE001
                logger.debug("human_recent failed", exc_info=True)
            return None

    def list_runs():
        with app.app_context():
            ds = _ds()
            if ds is None:
                return None                     # no dataset store: nothing to attribute against
            try:
                ds.rescan_if_stale()
            except Exception:  # noqa: BLE001
                pass
            try:
                # docs/262: each row carries the run's instant -- the engine's
                # attribution window is a time.time() epoch
                rows = story.with_instants(ds.list_runs()[:60], ds)
                # docs/281 review: run numbers are per data folder -- the
                # record names the folder it attributed
                runs = getattr(ds, "runs", None) or {}
                for row in rows:
                    info = runs.get(row.get("run_id")) if hasattr(runs, "get") else None
                    if info is not None and getattr(info, "folder_path", None) is not None:
                        row["folder_path"] = str(info.folder_path)
                return rows
            except Exception:  # noqa: BLE001
                return []

    def stage(writes, gid, actor, plan_id, apply):
        with app.app_context():
            return _stage_writes(app, live, writes, gid, actor, plan_id, apply)

    def journal(text, kind="agent", reason=None, run_id=None, paths=None):
        with app.app_context():
            journal_mod.append(inst, name, text, kind=kind, reason=reason, run_id=run_id, paths=paths or [])

    def wake():
        with app.app_context():
            _bump()
            _wake()

    def set_lock(info):
        app.config["agent_edit_lock"] = ({**info, "chip": name, "chip_key": chip, "path": live} if info else None)

    def notify(event, payload):
        with app.app_context():
            try:
                limits.notify(inst, chip, event, payload)
            except Exception:  # noqa: BLE001
                logger.debug("notify failed", exc_info=True)

    def queue_state():
        with scheduler._QLOCK:
            return scheduler.load_queue(scope)

    def live_diverged():
        with app.app_context():
            try:
                from quam_state_manager.core import working_copy as wc_mod
                return wc_mod.live_diverged_now(wc)
            except Exception:  # noqa: BLE001
                return None

    return agent_runs.RunAdapter(
        instance_path=inst, chip=chip, scope=scope, live_folder=live, working_folder=str(wc.working_folder),
        settings=settings, human_recent=human_recent, list_runs=list_runs, stage=stage, journal=journal,
        wake=wake, set_lock=set_lock, notify=notify,
        queue_state=queue_state, own_runner_alive=lambda: scheduler.is_running(scope), live_diverged=live_diverged,
        chip_name=name)


def _run_view(m: dict) -> dict:
    res = m.get("result") or {}
    out = {"key": m.get("key"), "status": m.get("status"), "node": m.get("node"), "targets": m.get("targets"),
           "params": m.get("params") or {},
           "since": m.get("since"), "ended": m.get("ended"), "actor": m.get("actor"), "plan_id": m.get("plan_id"),
           "simulated": bool(res.get("simulated", m.get("simulated"))), "result": res}
    if m.get("status") in ("starting", "running"):
        out["how"] = f"still running; call run_wait with key {m.get('key')}"
    elif (res.get("failure") or {}).get("how"):
        # docs/249: the run's own failure text says what failed and whether to retry (host
        # unreachable is not contention; contention is not a node error)
        out["how"] = res["failure"]["how"]
    elif res.get("approval"):
        out["how"] = f"{len(res.get('writes') or [])} write(s) wait for the human's approval ({res.get('why_held')}); " \
                     "do not re-run this node on these targets until it is decided"
    elif res.get("classification") == "unattributed":
        out["how"] = "the node finished but no run folder appeared under its name; check the log tail"
    elif res.get("apply_error"):
        out["how"] = f"applied: no ({res.get('apply_error')})"
    out["driver"] = m.get("driver")              # docs/253 (C-05): which agent asked -- in-app or terminal
    return out


def _no_targets_refusal():
    """A-13 / D-12 (docs/254): ``targets=[]`` passed every gate -- the per-target
    approval gate keys on targets, so writes waiting on q1 did not stop a run
    that the node's own defaults pointed at q1 -- and the node then ran on
    those defaults, which no card showed. The plan door (docs/191 B01) and the
    ``/run`` line (docs/247 C-08) already require a target; this is the third
    door, held to the same rule."""
    st = _r()._store()
    known = sorted(set(st.qubit_names) | set(st.qubit_pair_names), key=natural_key) if st else []
    return _err("targets required: name the qubits or pairs this node runs on (an empty list would run the node "
                "on its own defaults, which no card shows)", 400, refused="no_targets", known=known[:80])


def _request_refusal(node_info, targets: list, params: dict, store) -> dict | None:
    """The run as a card will show it, checked before any gate (docs/254):
    SM-owned params, a target of the wrong kind, a replay of another node's
    run. A node SM cannot run at all is the gates' to name."""
    from quam_state_manager.core import run_terms
    if node_info is None or getattr(node_info, "kind", None) != "node" or not getattr(node_info, "has_hook", False):
        return None

    def get_run(rid: int):
        ds = _ds()
        if ds is None:
            return None
        try:
            ds.rescan_if_stale()
        except Exception:  # noqa: BLE001
            pass
        return ds.get_run(rid)
    return run_terms.request_refusal(node_info, targets, params,
                                     qubits=list(store.qubit_names) if store else [],
                                     pairs=list(store.qubit_pair_names) if store else [], get_run=get_run)


def _file_run_request(inst, chip, name, node_name, req, plan) -> dict:
    """File (or find) the ask-all request for exactly this run; journal it once."""
    from quam_state_manager.core import agent_plans, approvals, run_terms
    step = req.step
    if step is None and plan and plan.get("status") in ("running", "stopping"):
        st = agent_plans.step_for(plan, step=None, node=node_name, targets=req.targets)
        step = st.get("i") if st else None
    ap, created = approvals.file_run_request(inst, chip, node=node_name, targets=req.targets, params=req.params,
                                             reason=req.reason, actor=req.actor, plan_id=req.plan_id, step=step)
    if created:
        journal_mod.append(inst, name, f"asked to run `{node_name}` on {' '.join(req.targets)} "
                                       f"({run_terms.params_text(req.params)}) -- waiting for approval (mode ask-all)",
                           kind="agent", reason=req.reason)
        # docs/261 (D-14): an armed plan now waits for a person -- the phone hears it
        from quam_state_manager.core import agent_overnight
        agent_overnight.needs_human_run_request(inst, chip, ap, plan)
        _bump()
        _wake()
    return ap


def _approval_mismatch(inst, chip, name, node_name, req, plan, ap, differs):
    """D-05 (docs/254): the approval covers the run the person saw, not this one.
    The allowed approval stays unused (it still covers exactly that run); what
    was asked becomes its own request, on its own card."""
    from quam_state_manager.core import approvals
    new = _file_run_request(inst, chip, name, node_name, req, plan)
    said = "; ".join(f"{d['field']}: allowed {json.dumps(d['allowed'], default=str)}, "
                     f"asked {json.dumps(d['asked'], default=str)}" for d in differs[:6])
    return jsonify(ok=False, refused="awaiting_approval", needs="run", approval=approvals.summary(new),
                   not_covered_by=approvals.summary(ap), differs=differs,
                   how=f"approval {ap.get('id')} allowed a different run ({said}). An approval covers exactly the "
                       f"node, targets and params the person saw; SM filed request {new.get('id')} for what you "
                       f"asked. Call run_node again with approval_id={new.get('id')} once the human allows it, or "
                       f"with exactly what {ap.get('id')} allowed."), 409


@agent_bp.route("/run-node", methods=["POST"])
def run_node():
    """The agent's ONE way to run a calibration node. Gates answer as data
    (docs/173 §3.4); the node runs on a scratch copy; the writes go through
    the door or into an approval; the run is attributed to the agent."""
    from quam_state_manager.core import agent_plans, agent_runs, agent_session, approvals, limits
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    data = request.get_json(silent=True) or {}
    node = str(data.get("node") or "").strip()
    if not node:
        return _err("node required")
    targets = data.get("targets") or []
    if isinstance(targets, str):
        targets = targets.replace(",", " ").split()
    targets = [str(t).strip() for t in targets if str(t).strip()]
    reason = str(data.get("reason") or "").strip()
    if not reason:
        return _err("reason required: say WHY this node now (it goes into the human's journal)")
    params = data.get("params") or {}
    if not isinstance(params, dict):
        return _err("params must be an object")
    actor = r._request_actor()
    if not actor.startswith("by_"):
        return _err("run_node is the agent's door; a person runs nodes from the QUAlibrate GUI", 403)
    if not targets:
        return _no_targets_refusal()             # A-13 / D-12 (docs/254)
    store = r._store()
    known = set(store.qubit_names) | set(store.qubit_pair_names)
    bad = [t for t in targets if t not in known]
    if bad:
        return _err(_unknown_targets_msg(bad), 400, known=sorted(known, key=natural_key)[:80])
    inst, chip, name = current_app.instance_path, _chip_key(), _chip_name()
    adapter = _run_adapter()
    reg = _registry()
    _reconcile_grant()                           # docs/253: a grant that ended never passes the gate below
    settings = adapter.settings()
    with _SCAN_LOCK:                             # review R1 minor: two run_node calls raced the scan cache write
        node_info, available = agent_runs.resolve_node(settings.get("calibrations_folder"), node, instance_path=inst)
    bad_req = _request_refusal(node_info, targets, params, store)     # docs/254: the run the card will show
    if bad_req is not None:
        return jsonify(ok=False, **bad_req), 400
    session = agent_session.load(inst, chip)
    driver = _request_driver(session)
    lim = limits.load(inst, chip)
    try:
        timeout_s = float(data.get("timeout_s")) if data.get("timeout_s") else None
    except (TypeError, ValueError):
        timeout_s = None
    req = agent_runs.RunRequest(node=node, targets=targets, params=params, reason=reason, timeout_s=timeout_s,
                                plan_id=request.headers.get("X-SM-Plan") or data.get("plan_id") or None,
                                actor=actor, approval_id=data.get("approval_id") or None,
                                # docs/253 (C-05): the run names the agent that ASKED -- SM's in-app CLI session
                                # only for that session's own bridge; never a dead in-app session's id
                                session_id=((session or {}).get("session_id") if driver["kind"] == "app"
                                            else "terminal:" + (driver.get("id") or actor)),
                                # docs/253: step 0 is a step (`0 or ""` read it as "no step")
                                step=int(data["step"]) if str("" if data.get("step") is None else data["step"])
                                .lstrip("-").isdigit() else None)
    pend = approvals.pending(inst, chip)
    human = adapter.human_recent(float(lim.get("human_recent_min") or 30))
    plan = agent_plans.get(inst, chip, req.plan_id) if req.plan_id else None
    # docs/253 (D-06, D-09): the arming covers ONE plan's pending steps, exactly as the card shows them,
    # for the ONE agent that drives it. Anything else meets no start token at the gate, and the refusal
    # says which of these it was
    cover, step_i = _grant_covers(session, plan, req, node_info, driver, settings)
    if cover is None and step_i is not None:
        req.step = step_i                        # the run reports into the step it matched, not the first look-alike
    gate_session = session if cover is None else {**(session or {}), "start_token": None}
    with _lock_for("run"):                       # review R1-M3: the gates and the start are one step
        refusal = agent_runs.check_gates(req, session=gate_session, lim=lim, settings=settings, pending=pend, human=human,
                                         queue_state=adapter.queue_state(), own_running=adapter.own_runner_alive(),
                                         run_active=reg.active_for(chip), node_info=node_info, available=available,
                                         plan=plan if plan and plan.get("status") in ("running", "stopping") else None,
                                         live_diverged=adapter.live_diverged())
        if refusal is not None and refusal.get("refused") == "no_start_token" and cover is not None:
            refusal = dict(cover)
        if refusal is not None and refusal.get("refused") == "target_halted":
            _journal_halted_refusal(inst, name, node_info.name if node_info is not None else node, req, refusal)
        if refusal is not None:
            if refusal.pop("file_request", False) and node_info is not None:
                # review R1-M2: the same ask twice is one request -- "the same" is run_terms (docs/254)
                ap = _file_run_request(inst, chip, name, node_info.name, req, plan)
                refusal["approval"] = approvals.summary(ap)
            return jsonify(ok=False, **refusal), 409
        mode = agent_runs.run_mode(plan if plan and plan.get("status") in ("running", "stopping") else None, session, lim)
        ap = None
        if mode == "ask-all":
            ap = approvals.get(inst, chip, req.approval_id or "")
            if not ap or ap.get("status") != "approved" or ap.get("kind") != "run" or ap.get("used_by_run"):
                return jsonify(ok=False, refused="awaiting_approval", needs="run",
                               how="approval_id must name an APPROVED, not yet used, run request for this node and "
                                   "these targets"), 409
            differs = approvals.run_differences(ap, node=node_info.name, targets=targets, params=params,
                                                plan_id=req.plan_id, step=req.step)
            if differs:                          # D-05 (docs/254): any difference is a new approval
                return _approval_mismatch(inst, chip, name, node_info.name, req, plan, ap, differs)
        try:
            meta = reg.start(req, adapter, node_info=node_info, session=session, lim=lim)
        except RuntimeError:
            act = reg.active_for(chip) or {}
            return jsonify(ok=False, refused="run_active", run={k: act.get(k) for k in ("key", "node", "targets", "since")},
                           how="one node at a time on one chip; call run_wait on that key"), 409
        if ap is not None:
            approvals.mark_used(inst, chip, ap["id"], meta["key"])   # review R1-M2: consumed once
    from quam_state_manager.core import agent_grant
    reg._set(meta, driver=agent_grant.public(driver))   # docs/253: which agent's run this is, on its record
    try:
        wait_s = float(data.get("wait_s") or agent_runs.DEFAULT_WAIT_S)
    except (TypeError, ValueError):
        wait_s = agent_runs.DEFAULT_WAIT_S
    m = reg.wait(meta["key"], max(0.0, min(wait_s, 3600.0)))
    return jsonify(ok=True, **_run_view(m or meta))


_SCAN_LOCK = threading.Lock()
_HALTED_SAID: set = set()


def _journal_halted_refusal(inst, name, node_name, req, refusal) -> None:
    """docs/261: a step on a target the plan's stop-loss halted is refused, with one journal line
    per (plan, step, node, targets) -- an agent asking again does not repeat it."""
    k = (req.plan_id, req.step, node_name, tuple(sorted(req.targets)))
    if k in _HALTED_SAID:
        return
    _HALTED_SAID.add(k)
    why = "; ".join(f"{t}: {w}" for t, w in (refusal.get("halted") or {}).items())
    try:
        journal_mod.append(inst, name, f"refused `{node_name}` on {' '.join(req.targets)}: "
                                       f"{', '.join(refusal.get('targets') or [])} halted by the plan's stop-loss "
                                       f"({why[:240]})", kind="sm")
    except Exception:  # noqa: BLE001
        logger.debug("halted refusal journal line failed", exc_info=True)


def _grant_covers(session, plan, req, node_info, driver, settings) -> tuple[dict | None, int | None]:
    """agent_grant.covers for this request: ``(None, step)`` or ``(refusal, None)``."""
    from quam_state_manager.core import agent_grant, agent_runs
    inst = current_app.instance_path
    folder = settings.get("calibrations_folder")

    def resolve(name: str) -> str | None:
        with _SCAN_LOCK:
            info, _ = agent_runs.resolve_node(folder, name, instance_path=inst)
        return info.name if info is not None else None
    node = node_info.name if node_info is not None else req.node
    return agent_grant.covers(session, plan, plan_id=req.plan_id, driver=driver, node=node, targets=req.targets,
                              params=req.params, step=req.step, resolve=resolve)


@agent_bp.route("/queue/clear", methods=["POST"])
def queue_clear():
    """A person clears the chassis queue's non-running rows (review R1-C1:
    run_node refuses while the queue holds anyone's rows; the Experiment
    Runner page is hidden in 1.0, so this is the door)."""
    from quam_state_manager.core import scheduler
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    if r._request_actor().startswith("by_"):
        return _err("only a person clears the queue", 403)
    scope = r._sched_inst()
    with scheduler._QLOCK:
        st = scheduler.load_queue(scope)
        kept = [it for it in st.get("queue") or [] if it.get("status") == "running"]
        dropped = [{"name": it.get("name"), "label": it.get("label"), "status": it.get("status")}
                   for it in st.get("queue") or [] if it.get("status") != "running"]
        st["queue"] = kept
        scheduler.save_queue(scope, st)
    if dropped:
        journal_mod.append(current_app.instance_path, _chip_name(),
                           f"{r._request_actor()} cleared {len(dropped)} row(s) from the run queue", kind="sm")
    return jsonify(ok=True, dropped=dropped, kept=len(kept))


@agent_bp.route("/run/<key>")
def run_wait(key: str):
    try:
        wait_s = float(request.args.get("wait_s") or 0)
    except ValueError:
        wait_s = 0.0
    m = _registry().wait(key, max(0.0, min(wait_s, 3600.0)))
    if m is None:
        return _err("unknown run key", 404)
    return jsonify(ok=True, **_run_view(m))


@agent_bp.route("/runs/agent")
def runs_agent():
    """This process's run_node runs, newest first (the cards read these)."""
    reg = _registry()
    chip = _chip_key() if _r()._active_path() else None
    rows = [_run_view(m) for m in reg.runs.values() if chip is None or m.get("chip") == chip]
    rows.sort(key=lambda x: float(x.get("since") or 0), reverse=True)
    return jsonify(ok=True, runs=rows[:50])


@agent_bp.route("/session/arm", methods=["POST"])
def session_arm():
    """Rule 0: hardware starts only by a human click. This IS the click."""
    from quam_state_manager.core import agent_session
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    actor = r._request_actor()
    if actor.startswith("by_"):
        return _err("only a person's click arms a session (rule 0)", 403)
    # docs/253 (D-06, C-05): a session-wide Arm was a blank check -- it armed whatever agent asked next,
    # for as long as nobody pressed Stop, across the plan's end, End session and a restart. Arming is
    # now the plan card's Start: ONE plan (a /run line is a one-step plan), ONE driving agent
    return jsonify(ok=False, refused="arm_is_per_plan",
                   error="Arm is per plan: press Start on the plan card. That click arms the agent for that "
                         "plan's own steps only; a single run is a /run line (a one-step plan)."), 409


@agent_bp.route("/session/disarm", methods=["POST"])
def session_disarm():
    from quam_state_manager.core import agent_session
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    inst, chip = current_app.instance_path, _chip_key()
    before = agent_session.load(inst, chip)
    if before is None:
        return _err("no agent session on this chip", 409)
    # docs/253: the grant ends and the plan it armed closes (a step still running finishes and reports)
    g = _end_grant(f"disarmed by {r._request_actor()}")
    if g is None:
        agent_session.save(inst, chip, start_token=None)
    rec = agent_session.load(inst, chip)
    if before.get("start_token"):
        journal_mod.append(inst, _chip_name(), f"disarmed by {r._request_actor()}"
                           + (f" (plan `{g.get('title')}` stopped)" if g and g.get("title") else ""), kind="sm")
    _bump()
    _wake()
    return jsonify(ok=True, session=agent_session.summary(rec))


@agent_bp.route("/approvals")
def approvals_list():
    from quam_state_manager.core import approvals
    r = _r()
    if not r._active_path():
        return jsonify(ok=True, pending=[], recent=[])
    rows = approvals.load(current_app.instance_path, _chip_key())
    pend = [x for x in rows if x.get("status") == "pending"]
    recent = [approvals.summary(x) for x in rows if x.get("status") != "pending"][-20:]
    return jsonify(ok=True, pending=pend, recent=recent, waiting=len(pend))


@agent_bp.route("/approvals/<aid>/<verb>", methods=["POST"])
def approvals_decide(aid: str, verb: str):
    """approve / reject, by a person. Approving ``writes`` stages them as the
    agent's rows and pushes them through the door as THIS person's press."""
    from quam_state_manager.core import approvals
    r = _r()
    if verb not in ("approve", "reject"):
        return _err("verb must be approve or reject", 404)
    if not r._active_path():
        return _err("open a chip first", 409)
    actor = r._request_actor()
    if actor.startswith("by_"):
        return _err("only a person decides an approval", 403)
    data = request.get_json(silent=True) or {}
    inst, chip = current_app.instance_path, _chip_key()
    writes = data.get("writes") if isinstance(data.get("writes"), list) else None
    with _lock_for("approve"):                   # review R3-3: two presses are one decision
        cur = approvals.get(inst, chip, aid)
        if cur is None or cur.get("status") != "pending":
            return _err("no pending approval with that id", 404)
        if verb == "approve" and writes is not None:
            # docs/191 A06: the client edits VALUES -- the paths come from the
            # approval itself. The door took the list verbatim, so a request
            # naming a different path wrote THAT path to the chip and recorded
            # it as the approval's own (measured: approving a "qubits.q1.T1"
            # approval with "qubits.q2.f_01" moved q2 and left the record, and
            # so the journal line, saying `05_power_rabi` proposed it). The
            # presser could edit that field directly, so this is provenance
            # rather than permission -- and the Calibration log is the thing
            # built to be trustworthy about who proposed what.
            proposed = {str(w.get("path")): w for w in (cur.get("writes") or [])}
            strangers = [str(w.get("path")) for w in writes
                         if str(w.get("path")) not in proposed]
            if strangers:
                return _err(
                    "an approval decides the writes it proposed: "
                    + ", ".join(strangers[:6])
                    + (" and %d more" % (len(strangers) - 6) if len(strangers) > 6 else "")
                    + " was not among them", 400)
            # keep each proposal's own `old`: the value is the person's to
            # change, the anchor it is compared against is not
            writes = [dict(w, old=proposed[str(w.get("path"))].get("old"))
                      for w in writes]
        out = {"ok": True}
        if verb == "approve" and cur.get("kind") == "writes":
            # the writes go through the door FIRST; a refusal keeps the approval pending
            # (the human sees why, takes live, presses again) instead of recording an
            # approval that never reached the chip. All or nothing (review R2-2).
            res = _stage_writes(current_app._get_current_object(), str(r._active_path()),
                                writes if writes is not None else (cur.get("writes") or []),
                                f"approved:{aid}", cur.get("actor") or "by_agent", cur.get("plan_id"), True, presser=actor)
            out["stage"] = res
            if not res.get("applied"):
                return jsonify(ok=False, error=res.get("error") or "not applied", stage=res,
                               approval=approvals.summary(cur),
                               how=("the approval stays pending; " + ("take live, then approve again"
                                    if "stale_live" in str(res.get("error")) else "see the error"))), 409
        rec = approvals.decide(inst, chip, aid, status="approved" if verb == "approve" else "rejected", who=actor,
                               note=data.get("note"), writes=writes)
    out["approval"] = approvals.summary(rec)
    if verb == "approve" and rec.get("kind") == "writes":
        res = out["stage"]
        rid = rec.get("run_id")
        # C-30 (docs/254): the line names who approved, and every value the person changed before writing
        journal_mod.append(inst, _chip_name(), f"{actor} approved {res.get('staged', 0)} write(s) from `{rec.get('node')}`"
                                       + (f" #{rid}" if rid else "") + f" -- {'applied' if res.get('applied') else 'NOT applied: ' + str(res.get('error'))}"
                                       + _edited_text(rec),
                           kind="sm", run_id=rid, paths=[w.get("path") for w in (rec.get("writes") or [])[:20]])
    elif verb == "approve":
        from quam_state_manager.core import run_terms
        journal_mod.append(inst, _chip_name(), f"{actor} allowed the run of `{rec.get('node')}` on "
                                               f"{' '.join(rec.get('targets') or [])} ({run_terms.params_text(rec.get('params'))})",
                           kind="sm")
        out["agent_told"] = _tell_agent(chip, _allow_run_message(rec, actor), plan_id=rec.get("plan_id"))
        if not out["agent_told"]:
            out["told_note"] = _not_told_note(rec)
    else:
        journal_mod.append(inst, _chip_name(), f"rejected {rec.get('kind')} from `{rec.get('node')}`"
                                       + (f": {data.get('note')}" if data.get("note") else ""), kind="sm")
    _bump()
    _wake()
    return jsonify(**out)


def _allow_run_message(rec: dict, actor: str) -> str:
    """C-04 (docs/254): the message names the exact call -- approval_id and the
    params spelled out -- because the approval now covers exactly that run
    (D-05) and "the same params" from memory was one paraphrase away from a
    new request and a stalled plan."""
    call = {"node": rec.get("node"), "targets": list(rec.get("targets") or []), "params": rec.get("params") or {},
            "approval_id": rec.get("id")}
    if rec.get("plan_id"):
        call["plan_id"] = rec.get("plan_id")
    if rec.get("step") is not None:
        call["step"] = rec.get("step")
    return (f"The human ({actor}) allowed the run of {rec.get('node')} on {' '.join(rec.get('targets') or [])} "
            f"(approval {rec.get('id')}). Call run_node now with exactly these arguments (and your reason): "
            f"{json.dumps(call, default=str)}. The approval covers this node, these targets and these params only; "
            "anything else is a new request.")


def _edited_text(rec: dict) -> str:
    """'; edited before writing: <path> proposed 5 -> written 4' for each value
    the person changed on the card, '' when none (C-30)."""
    from quam_state_manager.core import run_terms
    orig = rec.get("writes_original")
    if not isinstance(orig, list):
        return ""
    before = {str(w.get("path")): w.get("new") for w in orig}
    edits = []
    for w in rec.get("writes") or []:
        pth = str(w.get("path"))
        if pth in before and json.dumps(run_terms.canon(before[pth]), sort_keys=True, default=str) != \
                json.dumps(run_terms.canon(w.get("new")), sort_keys=True, default=str):
            edits.append(f"`{pth}` proposed {_jsonable(before[pth])} -> written {_jsonable(w.get('new'))}")
    if not edits:
        return ""
    more = f" and {len(edits) - 4} more" if len(edits) > 4 else ""
    return "; edited before writing: " + ", ".join(edits[:4]) + more


def _tell_agent(chip_key: str, msg: str, plan_id: str | None = None) -> bool:
    """C-04 (docs/247): an ask-all "Allow run" used to be recorded and nothing else, so the in-app
    agent -- which had been told to wait -- never heard it and the plan sat at RUNNING 0/1. The
    chip's open in-app conversation is told when it DRIVES the armed plan the request belongs to
    (docs/253 D-09, docs/254); a terminal driver reads `approvals` itself, and a plan no longer
    armed is run by nobody."""
    try:
        from quam_state_manager.web import chat_api
        g = (_session() or {}).get("grant") or {}
        if (g.get("driver") or {}).get("kind") != "app":
            return False                         # docs/253 (D-09): no grant, or a terminal agent drives it
        if plan_id and g.get("plan_id") != plan_id:
            return False                         # docs/254: the armed plan is another one -- its run would be refused
        mgr = chat_api._manager()
        cur = mgr.get(chip_key)
        if not chat_api.session_open(cur):
            return False
        return not mgr.send(chip_key, msg).get("error")
    except Exception:  # noqa: BLE001
        logger.debug("tell agent failed", exc_info=True)
        return False


def _not_told_note(rec: dict) -> str:
    """What the person reads when Allow reached no agent (docs/254): who will run it, or that nobody can."""
    from quam_state_manager.core import agent_grant
    g = (_session() or {}).get("grant") or {}
    aid = rec.get("id")
    if not g or (rec.get("plan_id") and g.get("plan_id") != rec.get("plan_id")):
        return f"its plan is not armed now, so no agent runs it (approval {aid})"
    d = g.get("driver") or {}
    if d.get("kind") == "terminal":
        return f"the plan is driven by {agent_grant.describe(d)}, which runs it with approval {aid}"
    return f"SM's in-app session that drives the plan is not open; approval {aid} waits for it"


@agent_bp.route("/undo-mine", methods=["POST"])
def undo_mine():
    """Undo the agent's OWN staged groups from the top of the tray, stopping
    at the first human entry (a person's edit is never undone by an agent)."""
    r = _r()
    mod = r._modifier()
    if not mod:
        return _err("no chip loaded", 409)
    store = mod.store
    reverted: list[str] = []
    stopped_at = None
    me = r._request_actor()
    with store._lock:
        while store.change_log:
            top = store.change_log[-1]
            if not r._owns_row(me, top):         # A-08 (docs/254): another agent's row is not mine either
                stopped_at = {"path": top.dot_path, "actor": getattr(top, "actor", "human")}
                break
            for e in mod.undo_group():
                reverted.append(e.dot_path)
    if reverted:
        journal_mod.append(current_app.instance_path, _chip_name(),
                           f"undid {len(reverted)} of its own staged edit(s)", kind="agent" if r._request_actor().startswith("by_") else "sm",
                           reason="undo_mine", paths=reverted[:20])
    _bump()
    _wake()
    return jsonify(ok=True, reverted=reverted, stopped_at=stopped_at, pending=r._change_count())


@agent_bp.route("/live-diff")
def live_diff():
    """What take_live would change: live leaves that differ from the working
    copy, and which of those the tray also touches (overlap)."""
    from quam_state_manager.core import json_diff, working_copy as wc_mod
    r = _r()
    ctx = r._active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return _err("no chip loaded", 409)
    wc = ctx["working_copy"]
    store = ctx["store"]
    try:
        live_state, live_wiring = wc_mod.read_live(wc)
    except Exception as exc:  # noqa: BLE001
        return _err(f"live files unreadable: {exc}", 502)
    with store._lock:
        work, _ = json_diff.flatten({**store.state, **store.wiring}, cap=250_000)
        tray = {c.dot_path for c in store.change_log}
    live, _ = json_diff.flatten({**live_state, **live_wiring}, cap=250_000)
    changed = []
    # A list index is a NUMBER: a plain sort reads weights_imag.1009 before
    # .101, and the 300-row cap below then reports the WRONG 300 paths
    # (customer report 2026-09-09).
    for p in sorted(set(work) | set(live), key=natural_key):
        a, b = work.get(p, "<absent>"), live.get(p, "<absent>")
        if a == b and type(a) is type(b):
            continue
        changed.append({"path": p, "working": _jsonable(a), "live": _jsonable(b)})
        if len(changed) >= 300:
            break
    overlap = [c["path"] for c in changed if c["path"] in tray]
    # stale_since answers only beside live_diverged, like /chip and /state: a
    # bare mtime on an in-sync chip read as "stale since <time>" to the agent
    diverged = _live_flag()
    return jsonify(ok=True, count=len(changed), changed=changed, overlap=overlap,
                   live_diverged=diverged, stale_since=_stale_since() if diverged else None)


# ================================================================ S6: the card feed + plans
# The Agent home and the floating panel read ONE feed: SM's own record (chat
# events on disk, plans, runs, approvals) -- never the model's claim.

def _plan_brief() -> dict | None:
    from quam_state_manager.core import agent_plans
    try:
        chip = _chip_key()
        rec = agent_plans.running(current_app.instance_path, chip) or agent_plans.latest(current_app.instance_path, chip)
        if not rec:
            return None
        return {"id": rec.get("id"), "title": rec.get("title"), "status": rec.get("status"),
                "counts": agent_plans.counts(rec), "mode": rec.get("mode")}
    except Exception:  # noqa: BLE001
        return None


def _chat_card(e: dict) -> dict | None:
    h = e.get("hook_event_name")
    base = {"n": e.get("n"), "ts": e.get("ts"), "backend": e.get("backend")}
    if e.get("readonly"):
        base["readonly"] = True                 # docs/247: a question answered by the read-only one-shot
    if h == "User":
        return {**base, "kind": "user", "text": e.get("text") or "", "who": e.get("who")}
    if h == "Text":
        txt = e.get("text") or ""
        try:
            html = journal_mod.render(txt)
        except Exception:  # noqa: BLE001
            html = None
        return {**base, "kind": "answer", "text": txt, "html": html}
    if h in ("PreToolUse",):
        tool = e.get("tool_name") or ""
        if not (tool.startswith("mcp__sm") or tool in ("Edit", "Write", "MultiEdit")
                or tool.rsplit(".", 1)[-1] in _SHELL_TOOLS):
            return None
        return {**base, "kind": "tool", "tool": tool, "summary": e.get("summary") or "", "failed": False}
    if h in ("PostToolUseFailure",):
        return {**base, "kind": "tool", "tool": e.get("tool_name") or "", "summary": e.get("summary") or "",
                "failed": True, "error": e.get("error")}
    if h == "Error":
        return {**base, "kind": "error", "error": e.get("error"), "limited": bool(e.get("limited"))}
    if h == "Result" and e.get("limited"):
        return {**base, "kind": "limited", "text": (e.get("error") or e.get("summary") or "")[:200]}
    if h == "Stop" and e.get("stopped"):
        return {**base, "kind": "stop", "text": e.get("summary") or ""}
    return None


def _plan_view(rec: dict, *, with_may_change: bool = False) -> dict:
    from quam_state_manager.core import agent_plans
    out = dict(rec)
    out["counts"] = agent_plans.counts(rec)
    try:
        _step_requests(out)
    except Exception:  # noqa: BLE001
        logger.debug("step requests failed", exc_info=True)
    if with_may_change:
        try:
            out["may_change"], out["may_change_total"] = _may_change(rec.get("steps") or [])
        except Exception:  # noqa: BLE001
            logger.debug("may_change failed", exc_info=True)
            out["may_change"], out["may_change_total"] = [], 0
    return out


def _step_requests(view: dict) -> None:
    """Each plan step a run request (mode ask-all) was filed for carries it as
    ``request``: waiting for a person, or allowed and not yet run. Without it
    the card read RUNNING 0/1 while the plan waited on a person (C-04)."""
    from quam_state_manager.core import approvals
    if view.get("status") not in ("running", "stopping") or not view.get("id"):
        return
    try:
        rows = [a for a in approvals.load(current_app.instance_path, view.get("chip") or _chip_key())
                if a.get("kind") == "run" and a.get("plan_id") == view["id"] and not a.get("used_by_run")
                and a.get("status") in ("pending", "approved")]
    except Exception:  # noqa: BLE001
        return
    if not rows:
        return
    steps = []
    for s in view.get("steps") or []:
        s2 = dict(s)
        if s.get("status") == "pending":
            hit = next((a for a in reversed(rows) if a.get("step") == s.get("i")), None)
            if hit is None:
                hit = next((a for a in reversed(rows) if a.get("step") is None and a.get("node") == s.get("node")
                            and sorted(a.get("targets") or []) == sorted(s.get("targets") or [])), None)
            if hit is not None:
                s2["request"] = {"id": hit.get("id"), "status": hit.get("status"),
                                 "decided_by": hit.get("decided_by"), "params": hit.get("params") or {}}
        steps.append(s2)
    view["steps"] = steps


def _may_change(steps: list[dict], cap: int = 60) -> tuple[list[dict], int]:
    """The values a plan may write, from the families' own update targets
    (run-derived, docs/78 D-14) filled in per target, with the value the chip
    holds NOW. Unknown family => nothing claimed.

    Returns ``(rows, total)``: at most ``cap`` rows, and how many there are.
    The card used to print ``len(rows)`` as the count, so a 40-qubit /run said
    "60 value(s) may change" when 80 would (QA agents round).

    The path is followed through QUAM aliases with the SAME function the
    autofit writer uses (``families.resolve_alias_path``): real chips carry
    ``operations.x180 = "#./x180_DragCosine"``, so the raw
    ``...x180.amplitude`` does not exist and every row read "now: not set" on
    a chip that holds the value (measured on a customer's 5Q chip)."""
    from quam_state_manager.core.autofit import families
    r = _r()
    store = r._store()
    seen: set = set()
    out: list[dict] = []
    total = 0
    for s in steps:
        fam = families.family_for(s.get("node") or "")
        if not fam:
            continue
        params = s.get("params") or {}
        for u in getattr(fam, "updates", None) or []:
            tpl = getattr(u, "path", None) or getattr(u, "state_path", None)
            if not tpl:
                continue
            for t in s.get("targets") or []:
                path = str(tpl).replace("{q}", t).replace("{pair}", t).replace("{p}", t)
                assumed = None
                if "{operation}" in path:
                    # the node names its operation through a run parameter (docs/78 D-14);
                    # the step's own param first, else the node's usual default, SAID so
                    op = params.get("operation")
                    if not op:
                        op, assumed = "x180", "operation x180 assumed (the node's default)"
                    path = path.replace("{operation}", str(op))
                if "{" in path:
                    path = path.split("{")[0].rstrip(".") + " …"
                via = None
                if store is not None and "…" not in path:
                    try:
                        rp = families.resolve_alias_path(path, store.get_value)
                    except Exception:  # noqa: BLE001
                        rp = None
                    if rp and rp != path:
                        via, path = path, rp
                key = (t, path)
                if key in seen:
                    continue
                seen.add(key)
                total += 1
                if len(out) >= cap:
                    continue                      # counted, not listed
                now = None
                if store is not None and "…" not in path:
                    try:
                        now = _jsonable(store.resolve_value(path))
                    except Exception:  # noqa: BLE001
                        now = None
                row = {"target": t, "path": path, "now": now, "family": getattr(fam, "label", None),
                       "label": getattr(u, "label", None) or None, "note": assumed}
                if via:
                    row["via"] = via
                out.append(row)
    return out, total


def _approval_view(ap: dict, store) -> dict:
    """An approval as the card shows it: each write also carries ``now`` --
    the value SM holds for that leaf NOW (through the pointer alias, the way
    the approve door itself edits it). ``old`` is the value the PROPOSAL was
    made from; the card used to print it under "now", so after an outside
    write it still named a value the chip no longer held, while the plan
    card's "now" (docs 89285b0) already meant the value held (verifier P3)."""
    out = dict(ap)
    if store is None or ap.get("kind") != "writes":
        return out
    r = _r()
    rows = []
    for w in ap.get("writes") or []:
        w2 = dict(w)
        if not (w.get("created") or w.get("deleted")):
            try:
                target = r._resolve_edit_path(store, str(w.get("path"))) or str(w.get("path"))
                w2["now"] = _jsonable(store.get_value(target))
                w2["now_known"] = True
            except Exception:  # noqa: BLE001 -- a leaf SM cannot read says "not set", never a guess
                w2["now_known"] = False
        rows.append(w2)
    out["writes"] = rows
    return out


def _with_display(rows: list | None) -> list:
    """docs/272 (C-24): each value row the panel shows carries its display unit
    (``display``) from ``core.units.display_spec`` -- the unit the inspector and
    the qubit/pair tables already show that field in -- so agent.js formats
    without a unit vocabulary of its own. Rows are copied, never mutated."""
    from quam_state_manager.core import units
    out = []
    for w in rows or []:
        spec = units.display_spec(w.get("path")) if isinstance(w, dict) and w.get("path") else None
        out.append({**w, "display": spec} if spec else w)
    return out


def _feed_display(live: dict) -> None:
    """The panel's feed only: approvals' writes, run writes and a plan's
    "may change" rows. The MCP-visible views (``_run_view``, ``_plan_view``)
    stay as they were -- an agent reads stored values, not display text."""
    for p in live.get("plans") or []:
        if p.get("may_change"):
            p["may_change"] = _with_display(p["may_change"])
    for a in live.get("approvals") or []:
        if a.get("writes"):
            a["writes"] = _with_display(a["writes"])
    for rv in live.get("runs") or []:
        res = rv.get("result") or {}
        if res.get("writes"):
            rv["result"] = {**res, "writes": _with_display(res["writes"])}   # the registry's dict stays untouched


@agent_bp.route("/chat/cards")
def chat_cards():
    """The panel's feed: chat cards after ``after`` plus the live objects
    (plans, runs, approvals), the session, the pill's state."""
    from quam_state_manager.core import agent_plans, agent_session, approvals
    r = _r()
    chip = _chip_name() if r._active_path() else None
    key = _chip_key() if chip else None
    try:
        after = int(request.args.get("after") or 0)
    except ValueError:
        return _err("after must be an integer")
    cards: list[dict] = []
    last = after
    more = False
    if chip:
        with _events_lock:
            ev = [e for e in _events() if e.get("origin") == "chat" and int(e.get("n") or 0) > after
                  and e.get("chip") == chip]
        more = len(ev) > 300
        for e in ev[:300]:                       # review R2-13: from the FRONT, so the cursor never skips
            c = _chat_card(e)
            if c:
                cards.append(c)
            last = max(last, int(e.get("n") or 0))
    inst = current_app.instance_path
    live = {"plans": [], "runs": [], "approvals": []}
    file = None
    session = None
    if chip:
        plans = agent_plans.load(inst, key)[-6:]
        live["plans"] = [_plan_view(p, with_may_change=(p.get("status") in ("draft", "running", "stopping"))) for p in plans]
        reg = _registry()
        runs = [_run_view(m) for m in reg.runs.values() if m.get("chip") == key]
        runs.sort(key=lambda x: float(x.get("since") or 0))
        live["runs"] = runs[-20:]
        live["approvals"] = [_approval_view(a, r._store()) for a in approvals.pending(inst, key)]
        file = agent_session.summary(agent_session.load(inst, key))
        mgr = current_app.config.get("agent_chat")
        session = mgr.status(key) if mgr else None
        _feed_display(live)
    store = r._store()
    return jsonify(ok=True, chip=chip, chip_key=key, cards=cards, last=last, more=more, live=live, session=session, file=file,
                   now=_now_state(), waiting=_waiting_count(), agent_seq=int(current_app.config.get("agent_seq") or 0),
                   qubits=len(store.qubit_names) if store else None)


@agent_bp.route("/plans", methods=["GET"])
def plans_list():
    from quam_state_manager.core import agent_plans
    r = _r()
    if not r._active_path():
        return jsonify(ok=True, plans=[])
    rows = agent_plans.load(current_app.instance_path, _chip_key())
    return jsonify(ok=True, plans=[_plan_view(p) for p in rows[-20:]])


@agent_bp.route("/plans/<pid>", methods=["GET"])
def plan_get(pid: str):
    from quam_state_manager.core import agent_plans
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    rec = agent_plans.get(current_app.instance_path, _chip_key(), pid)
    if rec is None:
        return _err("unknown plan", 404)
    return jsonify(ok=True, plan=_plan_view(rec, with_may_change=True))


@agent_bp.route("/plans", methods=["POST"])
def plans_add():
    """A plan CARD: from the agent (plan_propose: title + steps + why) or
    from a person's deterministic ``/run`` line. Nothing starts here."""
    from quam_state_manager.core import agent_plans, agent_runs, agent_session, limits, run_terms
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    data = request.get_json(silent=True) or {}
    inst, chip, name = current_app.instance_path, _chip_key(), _chip_name()
    actor = r._request_actor()
    store = r._store()
    known = set(store.qubit_names) | set(store.qubit_pair_names)
    if data.get("run_line"):
        parsed = agent_plans.parse_run_line(str(data["run_line"]))
        if not parsed or parsed.get("error"):
            return _err((parsed or {}).get("error") or "usage: /run <node> <targets...> [param=value ...]")
        bad = [t for t in parsed["targets"] if t not in known]
        if bad:
            return _err(_unknown_targets_msg(bad), 400, known=sorted(known, key=natural_key)[:80])
        # review R2-16: the node name is checked NOW, not after a real session spun up
        try:
            from quam_state_manager.core import scheduler
            folder = scheduler.load_settings(r._sched_inst()).get("calibrations_folder")
        except Exception:  # noqa: BLE001
            folder = None
        with _SCAN_LOCK:
            info, avail = agent_runs.resolve_node(folder, parsed["node"], instance_path=inst)
        if info is None:
            names, close = run_terms.available_names(avail, parsed["node"])
            return _err(f"no node called {parsed['node']} in the calibrations folder"
                        + (f" (closest: {', '.join(close)})" if close else ""), 400, available=names, closest=close)
        parsed["node"] = info.name
        bad_req = _request_refusal(info, parsed["targets"], parsed["params"], store)   # docs/254
        if bad_req is not None:
            return jsonify(ok=False, error=bad_req["how"], **bad_req), 400
        steps = [{"node": parsed["node"], "targets": parsed["targets"], "params": parsed["params"],
                  "why": "typed as /run (no model involved)"}]
        title = f"/run {parsed['node']} {' '.join(parsed['targets'])}"
        source = "run_cmd"
    else:
        steps = data.get("steps")
        title = str(data.get("title") or "").strip() or "plan"
        source = "agent" if actor.startswith("by_") else "human"
        try:
            norm = agent_plans.normalize_steps(steps)
        except ValueError as exc:
            return _err(str(exc))
        # the normalized targets: a "q1,q2" string was checked letter by letter
        bad = sorted({t for s in norm for t in s["targets"] if t not in known},
                     key=natural_key)
        if bad:
            return _err(_unknown_targets_msg(bad), 400, known=sorted(known, key=natural_key)[:80])
        refusal = _plan_steps_refusal(inst, norm, steps, store)            # docs/254: the card shows runnable steps
        if refusal is not None:
            return refusal
    session = agent_session.load(inst, chip)
    mode = (session or {}).get("mode") or limits.load(inst, chip).get("mode")
    # docs/253 (D-09, C-05): who proposed it is who drives it after Start -- SM's in-app session, or the
    # terminal agent that asked; a person's /run line is driven by the in-app session
    from quam_state_manager.core import agent_grant
    proposer = _request_driver(session) if actor.startswith("by_") else None
    rec = agent_plans.add(inst, chip, title=title, steps=steps, mode=mode, created_by=actor, source=source,
                          reason=data.get("why") or data.get("reason"),
                          session_id=(session or {}).get("session_id") if (proposer or {}).get("kind") == "app" else None,
                          proposer=agent_grant.public(proposer))
    journal_mod.append(inst, name, f"plan `{rec['title']}` proposed ({len(rec['steps'])} step(s)) -- waiting for Start",
                       kind="agent" if actor.startswith("by_") else "sm",
                       reason=(data.get("why") or None) if actor.startswith("by_") else None)
    _bump()
    _wake()
    how = "the card is on the human's screen; nothing runs until a person presses Start. "
    if (proposer or {}).get("kind") == "terminal":
        how += ("When it is started you drive it -- SM's in-app agent will not: call plan_status with this plan_id "
                "until its status is running, then run_node step by step with plan_id and step, each exactly as the "
                "card shows it (node, targets, params).")
    else:
        how += "When told to go, call run_node step by step with plan_id and step."
    return jsonify(ok=True, plan=_plan_view(rec, with_may_change=True), how=how)


def _plan_steps_refusal(inst, norm: list[dict], steps: list, store):
    """Every step of a proposed plan names a node SM can run, with params a
    card can show (P3, docs/254: an unknown node was accepted onto a card and
    failed only after Start). The step keeps the folder's own spelling."""
    from quam_state_manager.core import agent_runs, run_terms, scheduler
    try:
        folder = scheduler.load_settings(_r()._sched_inst()).get("calibrations_folder")
    except Exception:  # noqa: BLE001
        folder = None
    if not folder:
        return None                                  # no folder yet: run_node's own gate names it later
    for s, raw in zip(norm, steps):
        with _SCAN_LOCK:
            info, avail = agent_runs.resolve_node(folder, s["node"], instance_path=inst)
        if info is None:
            names, close = run_terms.available_names(avail, s["node"])
            return _err(f"step {s['i']}: no node called {s['node']} in the calibrations folder"
                        + (f" (closest: {', '.join(close)})" if close else ""), 400,
                        refused="node_not_found", step=s["i"], available=names, closest=close)
        why = _request_refusal(info, s["targets"], s["params"], store)
        if why is not None:
            return jsonify(ok=False, error=f"step {s['i']}: {why['how']}", step=s["i"], **why), 400
        if isinstance(raw, dict):
            raw["node"] = info.name
    return None


@agent_bp.route("/plans/<pid>/mode", methods=["POST"])
def plan_mode(pid: str):
    from quam_state_manager.core import agent_plans, limits
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    if r._request_actor().startswith("by_"):
        return _err("a person chooses the plan's mode", 403)   # review R1 minor
    data = request.get_json(silent=True) or {}
    inst, chip = current_app.instance_path, _chip_key()
    rec = agent_plans.get(inst, chip, pid)
    if rec is None:
        return _err("unknown plan", 404)
    if rec.get("status") != "draft":
        return _err("the mode is chosen before Start", 409)
    mode = str(data.get("mode") or "")
    if mode not in limits.MODES:
        return _err(f"mode must be one of {list(limits.MODES)}")
    if mode != rec.get("mode"):                   # docs/173 S8: a mode change is a journal line
        journal_mod.append(inst, _chip_name(), f"plan `{rec.get('title')}` mode set to {mode} by {r._request_actor()}", kind="sm")
    rec = agent_plans.update(inst, chip, pid, mode=mode)
    # The other window has to SEE this. Measured by the two-windows round:
    # 30201 / 30497 / 30315 / 29558 ms over four trials, because this was the
    # only mutating agent route that never woke the feed — so B's screen
    # showed "mode ask-writes" beside a live Start button while the server
    # held "auto", and mode is what decides whether the agent writes without
    # asking. A new card crosses in 84-236 ms; so does this now.
    _bump()
    _wake()
    return jsonify(ok=True, plan=_plan_view(rec))


@agent_bp.route("/plans/<pid>/start", methods=["POST"])
def plan_start(pid: str):
    """THE click of rule 0. A person presses Start: the session is armed, the
    chip is snapshotted (the whole plan can be reverted to it), the plan's
    mode is applied to the session, and the driving session is told to go --
    started with the plan as its first message when there is none."""
    from quam_state_manager.core import agent_plans, agent_session, limits
    from quam_state_manager.web import chat_api
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    actor = r._request_actor()
    if actor.startswith("by_"):
        return _err("only a person's click starts a plan (rule 0)", 403)
    inst, chip, name = current_app.instance_path, _chip_key(), _chip_name()
    lock = _lock_for("plan")
    if not lock.acquire(timeout=5):
        return _err("busy", 409)
    try:
        return _plan_start_locked(pid, actor, inst, chip, name)
    finally:
        lock.release()


def _plan_start_locked(pid, actor, inst, chip, name):
    from quam_state_manager.core import agent_plans, agent_session, limits
    from quam_state_manager.web import chat_api
    r = _r()
    _reconcile_grant()                           # docs/253: a plan a restart left "running" is closed first
    rec = agent_plans.get(inst, chip, pid)
    if rec is None:
        return _err("unknown plan", 404)
    if rec.get("status") != "draft":
        return _err(f"plan is {rec.get('status')}", 409)
    if agent_plans.running(inst, chip):
        return _err("another plan is running on this chip", 409)
    data = request.get_json(silent=True) or {}
    lim = limits.load(inst, chip)
    mode = rec.get("mode") or lim.get("mode")
    if mode not in limits.MODES:
        return _err(f"mode must be one of {list(limits.MODES)}")
    # docs/261: the overnight envelope. The card showed it before this press; a Start that carries
    # what it showed arms exactly that -- limits changed in between are a new look, never a silent swap
    from quam_state_manager.core import agent_overnight
    env = agent_overnight.envelope(lim, mode=mode, dry_run=_dry_run_on())
    seen = data.get("envelope")
    if isinstance(seen, dict):
        differs = agent_overnight.envelope_differences(seen, env["values"])
        if differs:
            return _err("the envelope changed since the card showed it ("
                        + "; ".join(f"{d['field']}: shown {json.dumps(d['seen'], default=str)}, now "
                                    f"{json.dumps(d['now'], default=str)}" for d in differs[:6])
                        + "): look at it again, then Start", 409, refused="envelope_changed", differs=differs,
                        envelope=env)
    # review R1-M4: the mode is the PLAN's -- never written into the chip's Limits
    # the snapshot the plan can be reverted to
    pre_ts = None
    try:
        hm = r._history()
        ctx = r._active_ctx()
        meta = hm.check_and_snapshot(ctx["path"], "manual", force=True, kind="backup", actor=actor,
                                     defer_index=not current_app.config.get("TESTING"),
                                     project=r._scope_for(ctx["path"], ctx))
        if meta is not None:
            pre_ts = meta.timestamp
            try:
                hm.annotate_snapshot(ctx["path"], pre_ts, label=f"before plan {rec['title'][:40]}")
            except Exception:  # noqa: BLE001
                logger.debug("plan snapshot label failed", exc_info=True)
    except Exception:  # noqa: BLE001
        logger.warning("plan pre-snapshot failed", exc_info=True)
    # arm; the session carries the plan's mode WHILE the plan runs (restored at its end).
    # docs/253: the grant is for THIS plan and names its ONE driver. A terminal agent's plan is driven by
    # that agent (D-09: SM's in-app agent is not told too); a person's /run line, an in-app proposal, or a
    # terminal agent known to have exited is driven by SM's in-app session.
    from quam_state_manager.core import agent_grant
    snap = f", snapshot {pre_ts}" if pre_ts else ""
    prop = rec.get("proposer") or {}
    if prop.get("kind") == "terminal" and agent_grant.terminal_alive(prop):
        driver = {k: prop.get(k) for k in ("kind", "id", "actor", "pid")}
        g = agent_grant.arm_plan(inst, chip, plan=rec, actor=actor, driver=driver, name=name, mode=mode)
        rec = agent_plans.update(inst, chip, pid, status="running", started_by=actor, started_at=g["at"],
                                 pre_ts=pre_ts, mode=mode, driver=agent_grant.public(driver),
                                 envelope=_envelope_at(lim, mode, g["at"]))
        journal_mod.append(inst, name, f"plan `{rec['title']}` STARTED by {actor} (mode {mode}{snap}) -- armed for "
                                       f"this plan only, driven by {agent_grant.describe(driver)}"
                                       + _envelope_line(rec), kind="sm")
        _bump()
        _wake()
        return jsonify(ok=True, plan=_plan_view(rec, with_may_change=True), session_started=False, pre_ts=pre_ts,
                       driver=agent_grant.public(driver),
                       how=f"{agent_grant.describe(driver)} proposed this plan and runs it; SM's in-app agent does not")
    handoff = prop if prop.get("kind") == "terminal" else None
    lines = [f"The human ({actor}) pressed Start on plan {pid} (\"{rec['title']}\"), mode {mode}. "
             "Run it now, step by step, with run_node(plan_id=..., step=i, ...), each step exactly as listed "
             "(node, targets, params) -- nothing else is armed. After every step read the "
             "result; on a refusal or a failure tell the human and stop unless the refusal names a wait. "
             "When every step is done, summarize what changed."]
    for s in rec["steps"]:
        lines.append(f"  step {s['i']}: run_node node={s['node']} targets={s['targets']} params={s['params']}"
                     + (f"  # {s['why']}" if s.get("why") else ""))
    msg = "\n".join(lines)
    mgr = chat_api._manager()
    cur = mgr.get(chip)
    started = False

    def _undo_start(why):
        # review R1 minor: a Start the agent never heard leaves no armed session and no running plan
        agent_plans.update(inst, chip, pid, status="draft", started_by=None, started_at=None, pre_ts=None, driver=None,
                           envelope=None)
        agent_session.save(inst, chip, start_token=None, plan_id=None, grant=None, armed_by=None, armed_at=None,
                           mode=limits.load(inst, chip).get("mode"))
        return _err(f"could not tell the agent: {why}", 502)
    b = None
    try:
        if chat_api.session_open(cur):          # docs/247 C-03: a Codex conversation between turns is open
            backend, secret = cur.backend.name, getattr(cur, "secret", None)
        else:
            backend = str(data.get("backend") or chat_api._setup().get("default_backend") or "claude").lower()
            b = chat_api._build_backend(backend, readonly=False, chip=name, mode=mode, cwd=chat_api._cwd(),
                                        model=data.get("model"))
            secret = getattr(b, "session_secret", None)
    except (RuntimeError, ValueError, OSError) as exc:
        return _err(f"could not tell the agent: {exc}", 502)
    # armed BEFORE the agent hears it: its first run_node may arrive before this request returns
    driver = {"kind": "app", "id": secret, "actor": "by_" + backend, "backend": backend}
    g = agent_grant.arm_plan(inst, chip, plan=rec, actor=actor, driver=driver, name=name, mode=mode)
    rec = agent_plans.update(inst, chip, pid, status="running", started_by=actor, started_at=g["at"],
                             pre_ts=pre_ts, mode=mode, driver=agent_grant.public(driver),
                             envelope=_envelope_at(lim, mode, g["at"]))
    try:
        if b is None:
            res = mgr.send(chip, msg)
            if res.get("error"):
                return _undo_start(res["error"])
        else:
            mgr.start(chip, b, owner=actor, mode=mode, until=None, prompt=msg, resume=None, display=name)
            started = True
    except (RuntimeError, ValueError, OSError) as exc:
        return _undo_start(exc)
    chat_api._record_user(name, f"[Start] plan {rec['title']}", actor, (cur.backend.name if cur else data.get("backend") or "claude"))
    tail = f" -- armed for this plan only, driven by {agent_grant.describe(driver)}"
    if handoff:
        tail += (f"; {agent_grant.describe(handoff)}, which proposed it, has exited")
    journal_mod.append(inst, name, f"plan `{rec['title']}` STARTED by {actor} (mode {mode}{snap})" + tail
                       + _envelope_line(rec), kind="sm")
    _bump()
    _wake()
    return jsonify(ok=True, plan=_plan_view(rec, with_may_change=True), session_started=started, pre_ts=pre_ts,
                   driver=agent_grant.public(driver))


def _dry_run_on() -> bool | None:
    """The Runner's Dry run for the open chip (an auto plan is refused at every step while it is on)."""
    try:
        from quam_state_manager.core import scheduler
        return bool(scheduler.load_settings(_r()._sched_inst()).get("global_simulate", True))
    except Exception:  # noqa: BLE001
        return None


def _envelope_at(lim: dict, mode: str, since: float) -> dict:
    """docs/261: the envelope the Start approved, as the plan keeps it (the deadline counted from
    the Start click itself -- the same instant the grant's stop_by is counted from)."""
    from quam_state_manager.core import agent_overnight
    return agent_overnight.envelope_record(agent_overnight.envelope(lim, mode=mode, since=since))


def _envelope_line(rec: dict) -> str:
    """The envelope on the STARTED journal line, for an auto plan (the overnight case)."""
    env = rec.get("envelope") or {}
    if rec.get("mode") != "auto" or not env:
        return ""
    from quam_state_manager.core import agent_overnight
    md = env.get("max_delta") or {}
    return ("; envelope: stop_by " + (env.get("stop_by") or "none")
            + f", max_writes_per_plan {env.get('max_writes_per_plan')}"
            + ", max_delta " + ("; ".join(f"{k} {agent_overnight._num(v)}" for k, v in md.items()) or "none")
            + f", stop-loss {env.get('stoploss_target')} per target / {env.get('stoploss_plan')} per plan")


@agent_bp.route("/plans/<pid>/envelope", methods=["GET"])
def plan_envelope(pid: str):
    """docs/261: what a Start on this plan would approve -- the envelope's values (sent back with
    the Start, so a press arms what it showed), the deadline its stop_by means from now, the
    plan's steps, and a warning when the Runner would refuse every run of it."""
    from quam_state_manager.core import agent_overnight, agent_plans, limits
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    inst, chip = current_app.instance_path, _chip_key()
    rec = agent_plans.get(inst, chip, pid)
    if rec is None:
        return _err("unknown plan", 404)
    lim = limits.load(inst, chip)
    mode = rec.get("mode") or lim.get("mode")
    env = agent_overnight.envelope(lim, mode=mode, dry_run=_dry_run_on())
    steps = [{"i": s.get("i"), "node": s.get("node"), "targets": s.get("targets"), "params": s.get("params") or {}}
             for s in rec.get("steps") or []]
    return jsonify(ok=True, plan_id=pid, title=rec.get("title"), status=rec.get("status"), mode=mode,
                   envelope=env["values"], deadline=env["deadline"], lines=env["lines"], warnings=env["warnings"],
                   webhook=env["webhook"], steps=steps)


@agent_bp.route("/summary", methods=["GET"])
def summary_json():
    """docs/261: the morning summary of one started plan, read from SM's records on disk."""
    r = _r()
    if not r._active_path():
        return jsonify(ok=True, plan=None, plans=[])
    return jsonify(ok=True, **_jsonable(summary_data(request.args.get("plan") or None)))


def summary_data(plan_id: str | None) -> dict:
    """The morning summary for the open chip (the JSON door and the page both read this)."""
    from quam_state_manager.core import agent_overnight
    r = _r()
    ctx = r._active_ctx() or {}
    store = ctx.get("store")

    def current(path: str):
        target = r._resolve_edit_path(store, str(path)) or str(path)
        return store.get_value(target)
    _reconcile_grant()                              # never summarize a grant that ended as still running
    return agent_overnight.summary(current_app.instance_path, _chip_key(), plan_id=plan_id,
                                   live_folder=ctx.get("path"), current=current if store is not None else None)


@agent_bp.route("/plans/<pid>/cancel", methods=["POST"])
def plan_cancel(pid: str):
    from quam_state_manager.core import agent_plans
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    inst, chip = current_app.instance_path, _chip_key()
    with _lock_for("plan"):                      # docs/254: the status read and the stop are one step
        was = agent_plans.get(inst, chip, pid)
        if was is not None and was.get("status") not in ("draft", "running", "stopping"):
            return _err(f"plan is {was.get('status')}; there is nothing to cancel", 409, plan=_plan_view(was))
        rec = agent_plans.stop(inst, chip, pid, who=r._request_actor(), how="cancelled")
    if rec is None:
        return _err("unknown plan", 404)
    # docs/253: the arming that was for this plan ends with it -- said in this press's one line
    from quam_state_manager.core import agent_grant
    g = agent_grant.end(inst, chip, why=f"plan `{rec.get('title')}` was cancelled by {r._request_actor()}",
                        plan_id=pid, journal=False)
    journal_mod.append(inst, _chip_name(), f"plan `{rec.get('title')}` cancelled by {r._request_actor()}"
                       + (" -- disarmed" if g is not None else ""), kind="sm")
    _bump()
    _wake()
    return jsonify(ok=True, plan=_plan_view(rec))
