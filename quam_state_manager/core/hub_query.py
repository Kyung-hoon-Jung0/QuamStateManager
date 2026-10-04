"""Shared ledger read API, with warming exceptions instead of partial answers."""

from __future__ import annotations

import base64
from datetime import date
import hashlib
import json

from quam_state_manager.core import search_query
from quam_state_manager.core.hub_index import context, require_day_zone, snapshot
from quam_state_manager.core.hub_store import OPS, value

_OP_NAMES = {number: name for name, number in OPS.items()}


def _events(conn, eids):
    result = {}
    ids = list(eids)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        sql = "SELECT * FROM events WHERE eid IN (" + ",".join("?" for _ in chunk) + ")"
        result.update((row["eid"], dict(row)) for row in conn.execute(sql, chunk))
    return result


def _change(row):
    return {"path": row["path"], "old": value(row["old_num"], row["old_txt"]),
            "new": value(row["num"], row["txt"]), "op": _OP_NAMES[row["op"]],
            "proven": bool(row["proven"])}


def search(store, text):
    """Matching insertion ids in newest canonical event order."""
    with snapshot(store) as (_, index):
        found = _search(store, index, text)
        return [eid for eid in reversed(index.eids) if eid in found]


def _search(store, index, text):
    if any(index.classify(token) == "day" for token in search_query.tokens(text)):
        require_day_zone(store)
    return index.search(text)


def _bound(day):
    if day is None:
        return None
    return date.fromisoformat(day).isoformat()


def _encode(payload):
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _decode(cursor):
    try:
        data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (not isinstance(data, dict) or data.get("v") != 1
                or not isinstance(data.get("high"), int) or data["high"] < 0
                or not isinstance(data.get("last"), list) or len(data["last"]) != 6
                or any(not isinstance(data["last"][i], int) for i in (0, 2, 5))
                or any(not isinstance(data["last"][i], str) for i in (1, 3, 4))):
            raise ValueError
        return data
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError("invalid ledger timeline cursor") from exc


def timeline(store, q=None, kinds=None, entity=None, path=None, day_from=None,
             day_to=None, cursor=None, limit=50):
    """Return {events: [... with changes], cursor: str | None}, newest first.

    The cursor freezes the initial max eid, excluding subsequent appends even
    when they arrive late. Its canonical key survives rank renumbering. Changes
    to existing events remain live, rather than pretending to be a saved view.
    """
    with snapshot(store) as (conn, index):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("timeline limit must be a positive integer")
        lo, hi = _bound(day_from), _bound(day_to)
        if lo or hi:
            require_day_zone(store)
        if lo and hi and lo > hi:
            raise ValueError("day_from must not follow day_to")
        kinds = sorted(set([kinds] if isinstance(kinds, str) else kinds or []))
        filters = (q, kinds, entity, path, lo, hi)
        signature = hashlib.sha256(json.dumps(filters, separators=(",", ":")).encode()).hexdigest()
        high = max(index.eids, default=0)
        last = None
        if cursor is not None:
            data = _decode(cursor)
            if (data.get("ledger") != index.ledger_id or data.get("zone") != index.zone
                    or data.get("filters") != signature):
                raise ValueError("timeline cursor belongs to another ledger, zone, or query")
            high, last = data["high"], tuple(data["last"])
        found = _search(store, index, q)
        if kinds:
            allowed = set()
            for kind in kinds:
                allowed.update(index.postings["kind"].get(kind.lower(), ()))
            found.intersection_update(allowed)
        if entity is not None:
            found.intersection_update(index.postings["entity"].get(entity.lower(), ()))
        if path is not None:
            found.intersection_update(index.path_postings.get(index.paths.get(path), ()))
        if lo or hi:
            allowed = {eid for day, ids in index.postings["day"].items()
                       if (lo is None or day >= lo) and (hi is None or day <= hi) for eid in ids}
            found.intersection_update(allowed)
        selected = []
        for eid in reversed(index.eids):
            if eid in found and eid <= high and (last is None or index.keys[eid] < last):
                selected.append(eid)
                if len(selected) > limit:
                    break
        more = len(selected) > limit
        selected = selected[:limit]
        events = _events(conn, selected)
        for event in events.values():
            event["changes"] = []
        for start in range(0, len(selected), 500):
            chunk = selected[start:start + 500]
            sql = ("SELECT c.*,p.path FROM changes c JOIN paths p USING(pid) WHERE eid IN ("
                   + ",".join("?" for _ in chunk) + ") ORDER BY p.path")
            for row in conn.execute(sql, chunk):
                events[row["eid"]]["changes"].append(_change(row))
        next_cursor = _encode({"v": 1, "ledger": index.ledger_id, "zone": index.zone,
                               "filters": signature, "high": high,
                               "last": index.keys[selected[-1]]}) if more else None
        return {"events": [events[eid] for eid in selected], "cursor": next_cursor}


def _series(conn, index, path, limit=None, before=None):
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0):
        raise ValueError("series limit must be a nonnegative integer")
    pid = index.paths.get(path)
    if pid is None:
        return []
    ids = index.path_postings[pid]
    if before is not None:
        # eid is insertion identity; the boundary is the event's canonical order.
        ids = [eid for eid in ids if index.positions[eid] <= before]
    ids = sorted(ids, key=index.positions.__getitem__)
    if limit is not None:
        ids = ids[-limit:] if limit else []
    if not ids:
        return []
    events = _events(conn, ids)
    changes = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        sql = ("SELECT * FROM changes WHERE pid=? AND eid IN ("
               + ",".join("?" for _ in chunk) + ")")
        for row in conn.execute(sql, [pid, *chunk]):
            changes[row["eid"]] = row
    return [(events[eid], value(changes[eid]["old_num"], changes[eid]["old_txt"]),
             value(changes[eid]["num"], changes[eid]["txt"]),
             _OP_NAMES[changes[eid]["op"]], bool(changes[eid]["proven"])) for eid in ids]


def series(store, path, limit=None):
    """Exact holder changes, oldest first; limit retains the newest N rows."""
    with snapshot(store) as (conn, index):
        return _series(conn, index, path, limit)


def series_many(store, paths):
    """All requested series from one read snapshot, keyed by exact holder path."""
    with snapshot(store) as (conn, index):
        return {path: _series(conn, index, path) for path in paths}


def newest_change(store, path):
    """Newest (event, old, new, op, proven) row, or None."""
    with snapshot(store) as (conn, index):
        rows = _series(conn, index, path, limit=1)
        return rows[0] if rows else None


def writer_of(store, path, eid):
    """Last writer event at or before this event in canonical order, or None."""
    with snapshot(store) as (conn, index):
        if eid not in index.positions:
            raise KeyError(eid)
        rows = _series(conn, index, path, limit=1, before=index.positions[eid])
        return rows[0][0] if rows else None
