"""Which run WROTE a charted value -- not merely which run's save captured it.

Customer report (2026-09-29, a 5-qubit CZ chip): a Trends IRB point said its run
was ``20 Flux short`` and T1 points named runs that were not T1 runs. The
cause is structural. A Trends point is a CHANGE POINT between two consecutive
history snapshots, and its provenance was the snapshot's own run -- the run
whose saved ``quam_state`` the snapshot copied. A run folder's state is the
WHOLE machine at that run's save time, so it carries every value written
before it: by earlier runs the history never captured (the chip has ~4,100
runs behind ~650 snapshots), by an out-of-band edit, or -- for an external
snapshot that ``_enrich_run_fields`` later linked to a run by content hash --
by nothing that run did at all (it started after the snapshot was taken).

This module answers, per point, from the run folders themselves:

* **proven** -- the run's own ``node.json`` ``patches`` set exactly this leaf
  to exactly this value;
* **consistent** -- the run is of the metric's own family (T1 for ``T1``,
  2Q IRB for ``InterleavedRB``, ... the family vocabulary is
  :func:`story._short_family`'s, never a second spelling), it targeted this
  qubit/pair (and CZ variant), its outcome for it was not a failure, it
  finished before the snapshot recorded the value, and -- for a run other
  than the one captured -- its own saved state holds the value;
* for a leaf with no family vocabulary, a run is named only when its save
  INTRODUCED the value (its predecessor run's saved state differs).

When the captured run fails, the runs between the previous change point and
this one are searched for the one that passes. When none does the point says
``captured with run #N`` -- the honest statement -- never a confident wrong
run. Run folders are immutable, so every read here is cached process-wide.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from quam_state_manager.core.story import _short_family

logger = logging.getLogger(__name__)

# A snapshot timestamp is UTC (history._now_ts); a run records its own end
# time with a zone. Two clocks, so a small slack either way.
_CLOCK_SLACK = timedelta(seconds=120)
# node.json reads (~14 KB each) the search spends before giving up when
# nothing bounds it (a value's FIRST appearance has no earlier change point).
_SEARCH_CAP = 400
# Saved-state reads per point while walking a stretch of family runs that
# all hold the value (one each, cached process-wide), before giving up.
_STATE_READS_PER_POINT = 40

# The leaf's last segment -> the node families (story._short_family labels)
# that measure it. Evidence, a customer archive (4,121 runs): every recorded
# patch of qubits.*.T1 came from 25_T1, of T2ramsey from 12_ramsey, of T2echo
# from 26_echo, of InterleavedRB from 37b_two_qubit_interleaved_cz_rb and of
# StandardRB from 37a_two_qubit_standard_rb. The vs-flux sweeps write their
# results under extras (T2star_vs_flux_*, T2echo_vs_flux_*), so they are NOT
# writers of the top-level T2s; T1 vs flux is kept (its enriched save on this
# chip carried the T1 value it measured).
_LEAF_FAMILIES: dict[str, frozenset[str]] = {
    "T1": frozenset({"T1", "T1/flux"}),
    "T2ramsey": frozenset({"Ramsey"}),
    "T2echo": frozenset({"Echo"}),
    "InterleavedRB": frozenset({"2Q IRB"}),
    "InterleavedRB_alpha": frozenset({"2Q IRB"}),
    "StandardRB": frozenset({"2Q SRB", "SRB"}),
    "StandardRB_alpha": frozenset({"2Q SRB", "SRB"}),
    # a QUBIT's gate fidelity (the only recorded patches of it on the customer chip
    # came from 27_single_qubit_randomized_benchmarking); a pair's
    # fidelity.averaged is a different leaf and keeps no vocabulary
    "gate_fidelity.averaged": frozenset({"1Q RB", "1Q IRB"}),
    "gate_fidelity.x180": frozenset({"1Q RB", "1Q IRB"}),
    "gate_fidelity.x90": frozenset({"1Q RB", "1Q IRB"}),
}

_RUN_DIR = re.compile(r"^#(\d+)_(.+?)(?:_(\d{6}))?$")


def families_for(leaf: str) -> frozenset[str] | None:
    """The node families that measure *leaf*, or None when SM has no
    vocabulary for it (then only patches or an introducing save can name a
    run)."""
    segs = str(leaf).split(".")
    # the longest dotted suffix first: "gate_fidelity.averaged" is a 1Q RB
    # result on a qubit, while a bare "averaged" names nothing
    for n in range(min(len(segs), 3), 0, -1):
        hit = _LEAF_FAMILIES.get(".".join(segs[-n:]))
        if hit is not None:
            if n == 2 and segs[0] != "qubits":
                continue
            return hit
    return None


def node_family(node: str | None) -> str:
    return _short_family(None, None, node or "") if node else ""


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-12 * max(1.0, abs(a), abs(b))
    return a == b


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.strptime(str(ts)[:15], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        return None            # a zone-less clock cannot be compared honestly
    return d.astimezone(timezone.utc)


def _walk(doc: Any, segs: list[str]) -> tuple[bool, Any]:
    for s in segs:
        if isinstance(doc, dict) and s in doc:
            doc = doc[s]
        elif isinstance(doc, list) and s.isdigit() and int(s) < len(doc):
            doc = doc[int(s)]
        else:
            return False, None
    return True, doc


class _LRU:
    def __init__(self, cap: int):
        self.cap = cap
        self.d: OrderedDict = OrderedDict()
        self.lock = threading.Lock()

    def get(self, k, default=None):
        with self.lock:
            if k in self.d:
                self.d.move_to_end(k)
                return self.d[k]
        return default

    def put(self, k, v):
        with self.lock:
            self.d[k] = v
            self.d.move_to_end(k)
            while len(self.d) > self.cap:
                self.d.popitem(last=False)


_NODE_CACHE = _LRU(20000)       # folder -> node summary (a few hundred bytes)
_STATE_CACHE = _LRU(50000)      # (folder, leaf) -> (found, value)
_LIST_CACHE = _LRU(64)          # root -> (signature, runs, checked at)
_LIST_FRESH_S = 5.0
_MISSING = object()


def clear_caches() -> None:
    for c in (_NODE_CACHE, _STATE_CACHE, _LIST_CACHE, _RESULT_CACHE):
        with c.lock:
            c.d.clear()


def node_summary(folder: Path) -> dict | None:
    """What a run's node.json says about what it did -- cached (immutable)."""
    key = str(folder)
    hit = _NODE_CACHE.get(key, _MISSING)
    if hit is not _MISSING:
        return hit
    out = None
    try:
        with open(folder / "node.json", encoding="utf-8") as f:
            d = json.load(f)
        md = d.get("metadata") or {}
        data = d.get("data") or {}
        model = ((data.get("parameters") or {}).get("model") or {}) if isinstance(data, dict) else {}
        patches = d.get("patches")
        if isinstance(patches, list):
            pl = []
            for p in patches:
                if isinstance(p, dict) and isinstance(p.get("path"), str):
                    pl.append((p["path"], p.get("value")))
        else:
            pl = None
        out = {
            "name": md.get("name"),
            "end": md.get("run_end"),
            "status": md.get("status"),
            "qubits": model.get("qubits") if isinstance(model.get("qubits"), list) else None,
            "pairs": model.get("qubit_pairs") if isinstance(model.get("qubit_pairs"), list) else None,
            "operation": model.get("operation") if isinstance(model.get("operation"), str) else None,
            "outcomes": data.get("outcomes") if isinstance(data.get("outcomes"), dict) else None,
            "patches": pl,
        }
    except (OSError, ValueError, AttributeError, TypeError):
        out = None
    _NODE_CACHE.put(key, out)
    return out


_HINT: "OrderedDict[str, None]" = OrderedDict()
_HINT_CAP = 512
_HINT_LOCK = threading.Lock()


def hint(leaves: Iterable[str]) -> None:
    """Leaves a caller is about to ask about: a state file parsed for ONE of
    them caches ALL of them, so a chart of 5 qubits x 3 metrics parses each
    run's 1.6 MB state once instead of fifteen times (measured on the
    customer chip default Trends open: 17.9 s -> 5.6 s cold; the parse is the
    whole cost)."""
    with _HINT_LOCK:
        for lf in leaves:
            _HINT[str(lf)] = None
            _HINT.move_to_end(str(lf))
        while len(_HINT) > _HINT_CAP:
            _HINT.popitem(last=False)


def state_values(folder: Path, leaves: Iterable[str]) -> dict[str, tuple[bool, Any]]:
    """``{leaf: (found, value)}`` from the run's saved ``quam_state`` --
    parsed at most once per (folder, leaf set not yet cached); a parse also
    caches every :func:`hint`-ed leaf."""
    leaves = list(leaves)
    out: dict[str, tuple[bool, Any]] = {}
    need = []
    for lf in leaves:
        hit = _STATE_CACHE.get((str(folder), lf), _MISSING)
        if hit is _MISSING:
            need.append(lf)
        else:
            out[lf] = hit
    if need:
        doc = None
        try:
            with open(folder / "quam_state" / "state.json", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = None
        if doc is not None:
            with _HINT_LOCK:
                extra = [h for h in _HINT if h not in need]
            for lf in extra:
                if _STATE_CACHE.get((str(folder), lf), _MISSING) is _MISSING:
                    _STATE_CACHE.put((str(folder), lf), _walk(doc, lf.split(".")))
        for lf in need:
            got = _walk(doc, lf.split(".")) if doc is not None else (False, _MISSING)
            if got[1] is _MISSING:
                got = (False, None)
                # an unreadable state is not "the leaf is absent": do not cache
                out[lf] = (None, None)
                continue
            _STATE_CACHE.put((str(folder), lf), got)
            out[lf] = got
    return out


def list_runs(root: Path) -> list[tuple[int, str, Path]]:
    """``[(run id, node name, folder)]`` under a dataset root, id-sorted.
    Re-listed only when a date dir's mtime moved."""
    hit = _LIST_CACHE.get(str(root))
    now = time.monotonic()
    if hit and now - hit[2] < _LIST_FRESH_S:
        return hit[1]           # one render asks hundreds of times
    try:
        dates = sorted(p for p in root.iterdir() if p.is_dir())
        sig = tuple((p.name, p.stat().st_mtime_ns) for p in dates)
    except OSError:
        return []
    if hit and hit[0] == sig:
        _LIST_CACHE.put(str(root), (sig, hit[1], now))
        return hit[1]
    runs = []
    for dd in dates:
        try:
            for rd in dd.iterdir():
                m = _RUN_DIR.match(rd.name)
                if m and rd.is_dir():
                    runs.append((int(m.group(1)), m.group(2), rd))
        except OSError:
            continue
    runs.sort(key=lambda t: t[0])
    _LIST_CACHE.put(str(root), (sig, runs, now))
    return runs


def _patch_hit(patches: list, leaf: str) -> tuple[bool, Any]:
    tail = "/" + leaf.replace(".", "/")
    for p, v in patches:
        if p == tail or p.endswith(tail) and p[: -len(tail)] in ("", "/quam"):
            return True, v
    return False, None


def _entity_ok(ns: dict, leaf: str) -> bool:
    parts = leaf.split(".")
    if len(parts) < 2:
        return True
    ent = parts[1]
    if parts[0] == "qubits":
        tg = ns.get("qubits")
        if tg is not None and ent not in tg:
            return False
    elif parts[0] == "qubit_pairs":
        tg = ns.get("pairs")
        if tg is not None and ent not in tg:
            return False
        op = ns.get("operation")
        if op and "macros" in parts:
            i = parts.index("macros")
            if i + 1 < len(parts) and parts[i + 1] != op:
                return False
    oc = ns.get("outcomes")
    if isinstance(oc, dict) and ent in oc and str(oc[ent]).lower() not in ("successful", "success", "true"):
        return False
    return True


def run_verdict(ns: dict | None, name: str | None, leaf: str, value: Any,
                snap_at: datetime | None) -> str:
    """``proven`` / ``consistent`` / ``no`` / ``unknown`` for one run from its
    node.json alone (no state read)."""
    if ns is None:
        ns = {}
    patches = ns.get("patches")
    if patches:
        hit, v = _patch_hit(patches, leaf)
        if hit:
            return "proven" if _same(v, value) else "no"
        return "no"         # it recorded what it updated, and this is not in it
    end = _parse_iso(ns.get("end"))
    if snap_at is not None and end is not None and end > snap_at + _CLOCK_SLACK:
        return "no"         # the value was on disk before this run saved
    fams = families_for(leaf)
    if fams is None:
        return "unknown"
    if node_family(ns.get("name") or name) not in fams:
        return "no"
    if not _entity_ok(ns, leaf):
        return "no"
    return "consistent"


def _predecessor(runs: list, rid: int) -> Path | None:
    lo, hi = 0, len(runs)
    while lo < hi:
        mid = (lo + hi) // 2
        if runs[mid][0] < rid:
            lo = mid + 1
        else:
            hi = mid
    for i in range(lo - 1, -1, -1):
        f = runs[i][2]
        if (f / "quam_state" / "state.json").is_file():
            return f
    return None


def attribute(leaf: str, value: Any, ts: str, captured: dict,
              prev_ts: str | None,
              resolve: Callable[[Any], Path | None]) -> dict:
    """Who wrote *value* at *leaf*, first recorded by snapshot *ts*.

    *captured* is the snapshot's provenance row (``run``, ``node``,
    ``folder``); *resolve* maps a recorded run folder to one on disk (a
    moved/copied dataset root). Returns ``{"verdict": "captured-run"}`` when
    the captured run is the writer, ``{"verdict": "other", "run", "node",
    "folder", "how"}`` for another run, or ``{"verdict": "captured"}`` when no
    run can be shown to have written it."""
    snap_at = _parse_ts(ts)
    prev_at = _parse_ts(prev_ts) if prev_ts else None
    rid = captured.get("run")
    folder = resolve(captured.get("folder")) if captured.get("folder") else None
    if rid is None or folder is None:
        return {"verdict": "unverifiable"}
    ns = node_summary(folder)
    v = run_verdict(ns, captured.get("node"), leaf, value, snap_at)
    if v == "proven":
        return {"verdict": "captured-run", "how": v}
    runs = list_runs(folder.parent.parent)
    if v in ("consistent", "unknown"):
        # A family run on this entity, or a leaf with no vocabulary: the
        # captured run is named only if its own save INTRODUCED the value --
        # its predecessor's saved state differs. A successful IRB rerun that
        # did not update the state CARRIES the value an earlier run wrote
        # (customer chip: 15 successful q3-4 reruns carried #2653's value).
        # the snapshot IS this run's save (experiment trigger, or linked by
        # content hash), so its value is known without a read
        if _introduced(runs, int(rid), folder, leaf, value, mine_known=True):
            return {"verdict": "captured-run", "how": "introduced" if v == "unknown" else v}
    # Search the runs before the captured one, newest first, for the writer.
    fams = families_for(leaf)
    state_reads = 0
    looked = 0
    oldest_holder = None      # the oldest family run seen still holding v
    for r_id, r_name, r_dir in reversed(runs):
        if r_id >= int(rid):
            continue
        # a cheap name filter first (no I/O): with a vocabulary, only its family
        if fams is not None and node_family(r_name) not in fams:
            continue
        looked += 1
        if looked > _SEARCH_CAP:
            oldest_holder = None    # gave up mid-stretch: unproven
            break
        rns = node_summary(r_dir)
        if rns is None:
            continue
        end = _parse_iso(rns.get("end"))
        if prev_at is not None and end is not None and end < prev_at - _CLOCK_SLACK:
            break           # saved before the previous change point: too old
        rv = run_verdict(rns, r_name, leaf, value, snap_at)
        if rv == "proven":
            return {"verdict": "other", "run": r_id, "node": rns.get("name") or r_name,
                    "folder": str(r_dir), "how": "proven"}
        if rns.get("patches") and _patch_hit(rns["patches"], leaf)[0]:
            break           # a later write of a DIFFERENT value: v came after it
        if rv != "consistent":
            continue
        if state_reads >= _STATE_READS_PER_POINT:
            oldest_holder = None    # ran out before the stretch ended: unproven
            break
        state_reads += 1
        got = state_values(r_dir, [leaf]).get(leaf, (None, None))
        if got[0] is None:
            continue
        if not (got[0] and _same(got[1], value)):
            break           # this family run saved something else: v came later
        oldest_holder = (r_id, rns.get("name") or r_name, r_dir)
    if oldest_holder is not None:
        # The oldest family run still holding the value wrote it only if its
        # own save introduced it; otherwise something between the family runs
        # (an edit, another node) did, and no run is named.
        r_id, r_name, r_dir = oldest_holder
        if _introduced(runs, r_id, r_dir, leaf, value):
            return {"verdict": "other", "run": r_id, "node": r_name,
                    "folder": str(r_dir), "how": "consistent"}
    return {"verdict": "captured"}


def _introduced(runs: list, rid: int, folder: Path, leaf: str, value: Any,
                mine_known: bool = False) -> bool:
    """True when *folder*'s saved state holds *value* and the previous run's
    saved state (the one it started from) does not."""
    if not mine_known:
        mine = state_values(folder, [leaf]).get(leaf, (None, None))
        if not (mine[0] and _same(mine[1], value)):
            return False
    pred = _predecessor(runs, rid)
    if pred is None:
        return False
    pv = state_values(pred, [leaf]).get(leaf, (None, None))
    return pv[0] is not None and not (pv[0] and _same(pv[1], value))


_RESULT_CACHE = _LRU(100000)


def attribute_cached(leaf: str, value: Any, ts: str, captured: dict,
                     prev_ts: str | None,
                     resolve: Callable[[Any], Path | None]) -> dict:
    """:func:`attribute`, memoised process-wide: a snapshot and the run
    folders behind it never change, so a new capture (a new history token,
    a re-render of every chart) re-asks only the points it added."""
    key = _result_key(leaf, value, ts, captured, prev_ts)
    hit = _RESULT_CACHE.get(key)
    if hit is not None:
        return hit
    out = attribute(leaf, value, ts, captured, prev_ts, resolve)
    if out.get("verdict") != "unverifiable":
        _RESULT_CACHE.put(key, out)
    return out


# ---- a render never waits for a cold archive --------------------------------
# A cold first answer parses one 1.6 MB state per run it checks (254 parses,
# ~5.6 s, for the default Trends open on a customer chip). A page asks with
# a DEADLINE: what is not answered by then is finished by one background
# worker and reported as pending, and the page asks again (the Trends and
# metric-metadata "updating" re-fetch). Pending is never shown as a run.
_BG_LOCK = threading.Lock()
_BG_QUEUED: set = set()
_BG_EXEC = None


def _result_key(leaf, value, ts, captured, prev_ts):
    return (leaf, repr(value), ts, prev_ts, captured.get("run"), str(captured.get("folder")))


def _bg_run(key, args):
    try:
        attribute_cached(*args)
    except Exception:  # noqa: BLE001 - a background check never raises
        logger.debug("background writer check failed", exc_info=True)
    finally:
        with _BG_LOCK:
            _BG_QUEUED.discard(key)


def attribute_by(deadline: float, leaf: str, value: Any, ts: str, captured: dict,
                 prev_ts: str | None,
                 resolve: Callable[[Any], Path | None]) -> dict:
    """:func:`attribute_cached` if answered before *deadline* (a
    ``time.monotonic()`` instant) -- else ``{"verdict": "pending"}`` and the
    check continues in the background."""
    global _BG_EXEC
    key = _result_key(leaf, value, ts, captured, prev_ts)
    hit = _RESULT_CACHE.get(key)
    if hit is not None:
        return hit
    if time.monotonic() < deadline:
        return attribute_cached(leaf, value, ts, captured, prev_ts, resolve)
    with _BG_LOCK:
        if key not in _BG_QUEUED:
            _BG_QUEUED.add(key)
            if _BG_EXEC is None:
                from concurrent.futures import ThreadPoolExecutor
                _BG_EXEC = ThreadPoolExecutor(max_workers=1,
                                              thread_name_prefix="value-writer")
            _BG_EXEC.submit(_bg_run, key, (leaf, value, ts, captured, prev_ts, resolve))
    return {"verdict": "pending"}
