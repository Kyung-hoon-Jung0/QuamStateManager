"""The history of one value, read from the chip's change ledger (docs/282).

The value drawer (the clock button on a cell), Column History and the agent's
field-history API all call :func:`read`; Chip Status Trends calls it for a
typed path that crosses a pointer. This module is the ONE place a per-value
history is built from the ledger: it reads through the S6a reader
(``hub_index.snapshot`` + ``hub_query``'s series), resolves a requested path
through the one pointer resolver (``pointer_path.resolve_field_target``) and
applies the one equality (``hub_rules.same``).

Paths (DESIGN 3.2): the ledger keys a value by the literal holder where it is
stored. A requested alias path (``...operations.x180.amplitude`` where ``x180``
is ``"#./x180_DragCosine"``) is resolved ONCE, now, to its holder; the answer
carries ``via`` (every pointer hop) and ``retargets`` (every ledger row of each
hop's pointer holder). A value row older than the row that gave a hop its
current pointer is marked ``before_via``: the alias did not name this holder
then. Nothing is stitched across a retarget.

Provenance (the "never show wrong provenance" rule): a run is named as the
writer of a value only when the ledger proves it (``proven``: the run's own
``node.json`` patch set exactly this leaf to exactly this value). Every other
row says what the ledger does know -- "saved in run #N, writer not proven",
"first recorded in run #N", or the SM write's own actor and kind.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterable

from quam_state_manager.core import hub_index, hub_query
from quam_state_manager.core import hub_rules as rules
from quam_state_manager.core.hub_rules import _segment
from quam_state_manager.core.hub_store import (
    CHIP_UNCERTAIN, NODE_UNREADABLE, OVERLAPS_SM_WRITE, PARTLY_UNDONE, REVERTS_TO_EARLIER,
    REWRITTEN, SM_KINDS, SOURCE_GONE, TIME_ASSUMED, UNDONE, segments)
from quam_state_manager.core.pointer_path import resolve_field_target
from quam_state_manager.core.pointer_resolver import is_pointer

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

#: flag bit -> the short name a surface shows (and the agent API returns)
FLAG_NAMES = (
    (SOURCE_GONE, "source_gone"),
    (REWRITTEN, "rewritten"),
    (OVERLAPS_SM_WRITE, "overlaps_sm_write"),
    (REVERTS_TO_EARLIER, "reverts_to_earlier"),
    (NODE_UNREADABLE, "node_unreadable"),
    (TIME_ASSUMED, "time_assumed"),
    (CHIP_UNCERTAIN, "chip_uncertain"),
)

#: a scalar list longer than this is ONE S2 holder (docs/269)
_SHORT_LIST = 16

_ABSENT = object()
#: "no value to compare" for :func:`notes` (distinct from a stored null)
ABSENT = _ABSENT


def iso_z(t_us: int | None) -> str | None:
    if t_us is None:
        return None
    d = _EPOCH + timedelta(microseconds=int(t_us))
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def holder_spelling(dot_path: str) -> str:
    """A plain dot-path in the S2 holder spelling (keys escaped)."""
    if not dot_path:
        return ""
    return ".".join(_segment(s) for s in dot_path.split("."))


def _walk(doc: Any, parts: list[str]) -> tuple[bool, Any]:
    for p in parts:
        if isinstance(doc, dict):
            if p not in doc:
                return False, None
            doc = doc[p]
        elif isinstance(doc, list):
            try:
                doc = doc[int(p)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, doc


def _long_list(value: Any) -> bool:
    return (isinstance(value, list) and len(value) > _SHORT_LIST
            and all(not isinstance(v, (dict, list)) for v in value))


def target(merged: dict, dot_path: str) -> dict:
    """Resolve *dot_path* once, now, through the one resolver.

    Returns ``{"path", "holder", "via", "current", "has_current",
    "resolvable", "array", "element"}``. ``holder`` is the S2 spelling of
    where the value is stored; ``array``/``element`` name a long-array holder
    and the index when the value is one element of a long scalar list."""
    try:
        ft = resolve_field_target(merged, dot_path)
    except Exception:  # noqa: BLE001 -- the resolver never raises; belt and braces
        ft = {"resolvable": False, "candidates": [], "chain": [], "resolved_value": None}
    cands = ft.get("candidates") or []
    last = cands[-1] if cands else None
    chain = list(ft.get("chain") or [])
    if ft.get("resolvable"):
        plain, current, has_current = ft["resolved_path"], ft.get("resolved_value"), True
        # resolve_field_target reports scalars only: read a container from the doc
        found, val = _walk(merged, plain.split(".")) if plain else (True, merged)
        if found and isinstance(val, (dict, list)):
            current = val
            ptr = cands[-2] if len(cands) >= 2 else None
            if not _long_list(val) and ptr is not None and ptr.get("is_pointer"):
                # a pointer to a whole object (``x180 = "#./x180_DragCosine"``):
                # the VALUE this path holds is the pointer string, so its
                # history is the pointer holder's (its retargets)
                plain, current = ptr["path"], ptr.get("value")
                chain = [h for h in chain if h["from_path"] != plain]
    elif last is not None and last.get("is_pointer"):
        # a pointer that names nothing (a runtime alias): its history is the
        # history of the pointer string itself
        plain, current, has_current = last["path"], last.get("value"), True
        chain = [h for h in chain if h["from_path"] != plain]
    else:
        plain, current, has_current = dot_path, None, False
    via = [{"from": holder_spelling(h["from_path"]), "from_path": h["from_path"],
            "pointer": h["pointer"], "to": holder_spelling(h["to_path"]),
            "to_path": h["to_path"]} for h in chain]
    out = {"path": dot_path, "holder": holder_spelling(plain), "holder_path": plain,
           "via": via, "current": current, "has_current": has_current,
           "resolvable": bool(ft.get("resolvable")), "array": None, "element": None}
    # an element of a long scalar list: the S2 holder is the list
    parts = plain.split(".") if plain else []
    if len(parts) >= 2:
        found, parent = _walk(merged, parts[:-1])
        if found and _long_list(parent):
            try:
                idx = int(parts[-1])
            except ValueError:
                idx = None
            if idx is not None:
                out["array"] = holder_spelling(".".join(parts[:-1]))
                out["element"] = idx
    return out


def comparable(tgt: dict) -> Any:
    """The target's current value in the ledger's own terms, for comparing it
    with the newest recorded value: a long scalar list as its S2 marker; a
    container (not one value) as :data:`ABSENT`."""
    current = tgt.get("current")
    if tgt.get("array") is None and _long_list(current):
        return rules.flatten(current)[""]
    if isinstance(current, (dict, list)):
        return _ABSENT
    return current


# ----------------------------------------------------------------------
# reading
# ----------------------------------------------------------------------

def _reader(chip_dir) -> SimpleNamespace:
    # hub_index.snapshot needs only ``.directory``; never a write-capable
    # HubStore per read (its constructor may write meta, docs/279 hand-over)
    return SimpleNamespace(directory=chip_dir)


class _Blobs:
    """Array blobs of one read snapshot, decoded once each."""

    def __init__(self, conn):
        self.conn = conn
        self.memo: dict[str, Any] = {}

    def get(self, digest: str) -> Any:
        if digest not in self.memo:
            row = self.conn.execute("SELECT gz FROM blobs WHERE hash=?", (digest,)).fetchone()
            if row is None:
                self.memo[digest] = None
            else:
                payload = gzip.decompress(row[0])
                if hashlib.sha1(payload).hexdigest() != digest:
                    raise ValueError(f"corrupt ledger blob {digest}")
                self.memo[digest] = json.loads(payload)
        return self.memo[digest]


def _element(marker: Any, idx: int, blobs: _Blobs) -> Any:
    if marker is _ABSENT or not isinstance(marker, dict) or "_hash" not in marker:
        return _ABSENT
    arr = blobs.get(marker["_hash"])
    if not isinstance(arr, list) or idx >= len(arr):
        return _ABSENT
    return arr[idx]


def _raw_rows(conn, index, path: str, limit: int | None = None) -> list[tuple]:
    """``(event, old, new, op, proven)`` oldest first, through S6a's reader.
    An op whose side is absent decodes to None: the op tells absent from null."""
    return hub_query._series(conn, index, path, limit)


def _side(op: str, which: str, value: Any) -> Any:
    if which == "old" and op == "add":
        return _ABSENT
    if which == "new" and op == "gone":
        return _ABSENT
    return value


def _element_rows(conn, index, tgt: dict, blobs: _Blobs) -> list[tuple]:
    """One element of a long array: the array holder's versions sliced, merged
    per event with the element's own holder rows (while the list was short)."""
    pos = index.positions
    by_event: dict[int, dict] = {}
    for ev, old, new, op, proven in _raw_rows(conn, index, tgt["array"]):
        slot = by_event.setdefault(ev["eid"], {"ev": ev})
        slot["array"] = (_element(_side(op, "new", new), tgt["element"], blobs), proven)
    for ev, old, new, op, proven in _raw_rows(conn, index, tgt["holder"]):
        slot = by_event.setdefault(ev["eid"], {"ev": ev})
        slot["own"] = (_side(op, "new", new), proven)
    out = []
    value: Any = _ABSENT
    for eid in sorted(by_event, key=pos.__getitem__):
        slot = by_event[eid]
        if "array" in slot and slot["array"][0] is not _ABSENT:
            new, proven = slot["array"]
        elif "own" in slot:
            new, proven = slot["own"]
        elif "array" in slot:
            new, proven = _ABSENT, slot["array"][1]
        else:
            continue
        if (value is _ABSENT and new is _ABSENT) or (
                value is not _ABSENT and new is not _ABSENT and rules.same(value, new)):
            continue
        op = "add" if value is _ABSENT else ("gone" if new is _ABSENT else "set")
        out.append((slot["ev"], None if value is _ABSENT else value,
                    None if new is _ABSENT else new, op, proven))
        value = new
    return out


def _flags(bits: int) -> list[str]:
    return [name for bit, name in FLAG_NAMES if bits & bit]


def provenance(ev: dict, proven: bool) -> str:
    """What the ledger can say about who set a row (docs/282 §1.4)."""
    kind = ev.get("kind")
    if kind == "run":
        if ev.get("flags", 0) & CHIP_UNCERTAIN:
            return "run_uncertain_chip"
        if proven:
            return "run_proven"
        if ev.get("base_hash") is None:
            return "first_record"
        return "run_saved"
    if kind in SM_KINDS:
        return "sm"
    return "unknown"


def _point(ev: dict, old: Any, new: Any, op: str, proven: bool, roots: dict,
           sm: dict) -> dict:
    flags = int(ev.get("flags") or 0)
    folder = None
    if ev.get("root_id") is not None and ev.get("rel_path"):
        base = roots.get(ev["root_id"])
        if base:
            folder = base.rstrip("/\\") + "/" + ev["rel_path"]
    info = sm.get(ev["eid"]) or {}
    return {
        "eid": ev["eid"], "t_us": ev["t_utc_us"], "t": iso_z(ev["t_utc_us"]),
        "kind": ev["kind"], "op": op, "value": new, "old": old,
        "removed": op == "gone", "was_absent": op == "add",
        "proven": bool(proven), "provenance": provenance(ev, proven),
        "run_id": ev.get("run_id"), "experiment": ev.get("experiment"),
        "folder": folder, "status": ev.get("status"),
        "actor": ev.get("actor"), "src": ev.get("src"), "plan_id": ev.get("plan_id"),
        "run_uid": info.get("run_uid"), "undoes": bool(info.get("undoes")),
        "flags": _flags(flags),
        "undone": "undone" if flags & UNDONE else ("partly" if flags & PARTLY_UNDONE else None),
        "before_via": False,
    }


def _sm_info(conn, eids: Iterable[int]) -> dict:
    ids = sorted(set(eids))
    out: dict[int, dict] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        sql = ("SELECT eid, sm_id, run_uid, undoes FROM sm_events WHERE eid IN ("
               + ",".join("?" for _ in chunk) + ")")
        for row in conn.execute(sql, chunk):
            out[row[0]] = {"sm_id": row[1], "run_uid": row[2], "undoes": row[3]}
    return out


def read(chip_dir, targets: dict[str, dict], *, limit: int | None = None,
         runs: int = 0) -> dict:
    """Every target's history from ONE ledger read snapshot.

    ``targets``: ``{key: target(...)}``. ``limit``: keep the newest N change
    points per key (the total is still reported). ``runs``: also return the
    newest N successful run events with each key's value at that run (Column
    History's By-run tab).

    Raises ``hub_sync.Building`` while the ledger is catching up and
    ``ramcache.Warming`` while its RAM index is being prepared -- a surface
    says so, it never shows a partial history as complete. Returns
    ``{"rows": {key: {...}}, "runs": [...], "ledger": {...}}``.
    """
    with hub_index.snapshot(_reader(chip_dir)) as (conn, index):
        kind_names = {v: k for k, v in index.names["kind"].items()}
        run_kind = index.names["kind"].get("run")
        has_runs = bool(index.postings["kind"].get("run"))
        roots = {r[0]: r[1] for r in conn.execute("SELECT root_id, path FROM roots")}
        blobs = _Blobs(conn)
        raw: dict[str, list] = {}
        totals: dict[str, int] = {}
        hop_rows: dict[str, list] = {}
        for key, tgt in targets.items():
            if tgt.get("array") is not None:
                rows = _element_rows(conn, index, tgt, blobs)
                totals[key] = len(rows)
                if limit is not None and len(rows) > limit:
                    rows = rows[-limit:] if limit else []
            else:
                pid = index.paths.get(tgt["holder"])
                totals[key] = len(index.path_postings.get(pid, ())) if pid is not None else 0
                rows = _raw_rows(conn, index, tgt["holder"], limit)
            raw[key] = rows
            for hop in tgt.get("via") or ():
                if hop["from"] not in hop_rows:
                    hop_rows[hop["from"]] = _raw_rows(conn, index, hop["from"])
        sm_eids = [r[0]["eid"] for rows in list(raw.values()) + list(hop_rows.values())
                   for r in rows if r[0]["kind"] in SM_KINDS]
        sm = _sm_info(conn, sm_eids)

        def points(rows):
            return [_point(ev, old, new, op, proven, roots, sm)
                    for ev, old, new, op, proven in rows]

        hops = {path: points(rows) for path, rows in hop_rows.items()}
        out_rows: dict[str, dict] = {}
        for key, tgt in targets.items():
            pts = points(raw[key])
            retargets = []
            since_pos = None
            for hop in tgt.get("via") or ():
                hp = hops.get(hop["from"]) or []
                # The hop names its holder since its NEWEST row, when that row
                # set the pointer the chip holds now. A hop with no row never
                # changed while the ledger looked. A newest row that set some
                # other string means the current pointer is not recorded yet
                # (an edit not applied): every value row predates it.
                unrecorded = False
                pos = None
                if hp:
                    newest = hp[-1]
                    if (not newest["removed"] and isinstance(newest["value"], str)
                            and newest["value"] == hop["pointer"]):
                        pos = index.positions.get(newest["eid"])
                    else:
                        pos, unrecorded = len(index.eids), True
                if pos is not None and (since_pos is None or pos > since_pos):
                    since_pos = pos
                retargets.append({"from": hop["from"], "from_path": hop["from_path"],
                                  "pointer": hop["pointer"], "to": hop["to"],
                                  "to_path": hop["to_path"], "rows": hp,
                                  "unrecorded": unrecorded})
            if since_pos is not None:
                for p in pts:
                    if index.positions.get(p["eid"], since_pos) < since_pos:
                        p["before_via"] = True
            out_rows[key] = {"points": pts, "total": totals[key],
                             "retargets": retargets,
                             "via_since": (iso_z(index.t[since_pos])
                                           if since_pos is not None and 0 < since_pos < len(index.eids)
                                           else None)}

        by_run: list[dict] = []
        if runs:
            picked = []
            for pos in range(len(index.eids) - 1, -1, -1):
                if index.kind[pos] == run_kind:
                    picked.append(index.eids[pos])
                    if len(picked) >= runs * 3 + 8:
                        break
            evs = hub_query._events(conn, picked)
            good = [evs[e] for e in picked if e in evs and not evs[e].get("error")][:runs]
            for ev in good:
                pos = index.positions[ev["eid"]]
                values = {}
                for key in targets:
                    values[key] = _value_at(raw[key], pos, index.positions)
                folder = None
                if ev.get("root_id") is not None and ev.get("rel_path"):
                    base = roots.get(ev["root_id"])
                    folder = (base.rstrip("/\\") + "/" + ev["rel_path"]) if base else None
                by_run.append({"eid": ev["eid"], "t": iso_z(ev["t_utc_us"]),
                               "run_id": ev.get("run_id"), "experiment": ev.get("experiment"),
                               "folder": folder, "flags": _flags(int(ev.get("flags") or 0)),
                               "values": values})
        ledger = {"events": len(index.eids), "has_runs": has_runs,
                  "first": iso_z(index.t[0]) if index.eids else None,
                  "last": iso_z(index.t[-1]) if index.eids else None,
                  "last_us": index.t[-1] if index.eids else None,
                  "last_run": None, "kinds": sorted(kind_names.values())}
        if run_kind is not None:
            for pos in range(len(index.eids) - 1, -1, -1):
                if index.kind[pos] == run_kind:
                    ledger["last_run"] = iso_z(index.t[pos])
                    break
        return {"rows": out_rows, "runs": by_run, "ledger": ledger}


def _value_at(rows: list[tuple], pos: int, positions: dict) -> Any:
    """The value at canonical position *pos*, folded from the rows: the newest
    row at or before it; before the first row, that row's old side (``None``
    when the first row added the value). ``_ABSENT`` maps to None."""
    best = None
    for row in rows:
        p = positions.get(row[0]["eid"])
        if p is not None and p <= pos:
            best = row
        elif p is not None and p > pos:
            break
    if best is not None:
        return None if best[3] == "gone" else best[2]
    if rows:
        first = rows[0]
        return None if first[3] == "add" else first[1]
    return None


def notes(status: dict | None, ledger: dict, *, current: Any = _ABSENT,
          newest: Any = _ABSENT) -> list[dict]:
    """What every surface says beside a ledger answer (docs/282 §1.3)."""
    st = status or {}
    out: list[dict] = []
    state = st.get("state")
    if state == "degraded":
        bad = list(st.get("unreadable") or [])
        failed = int(st.get("failed") or 0)
        parts = []
        if bad:
            parts.append("a data folder cannot be read (" + ", ".join(bad[:2])
                         + (" and more" if len(bad) > 2 else "") + ")")
        if failed:
            parts.append(f"{failed} run{'s' if failed != 1 else ''} could not be read into it")
        out.append({"level": "warning", "code": "degraded",
                    "text": "This history may be missing changes: " + "; ".join(parts or ["see Diagnostics"]) + "."})
    deferred = int(st.get("deferred") or 0)
    if deferred:
        out.append({"level": "info", "code": "deferred",
                    "text": f"{deferred} newest run{'s are' if deferred != 1 else ' is'} still being "
                            "saved and not in this history yet."})
    if state == "idle":
        last = ledger.get("last_run") or ledger.get("last")
        out.append({"level": "info", "code": "idle",
                    "text": "The ledger is not being kept current in this window"
                            + (f"; runs after {last} may be missing." if last else ".")})
    elif not st.get("roots") and state in ("ready", "degraded") and ledger.get("has_runs"):
        out.append({"level": "info", "code": "no_folder",
                    "text": "No data folder is linked to this chip now; newer runs may be missing."})
    if current is not _ABSENT and newest is not _ABSENT and not (
            current is None and newest is None):
        if current is None or newest is None or not rules.same(current, newest):
            out.append({"level": "info", "code": "current_differs",
                        "text": "The value now is not the newest recorded one: an edit not "
                                "applied to the chip yet, or a change made outside SM since "
                                "the last run."})
    return out
