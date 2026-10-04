"""Shared ledger read API, with warming exceptions instead of partial answers."""

from __future__ import annotations

import base64
from datetime import date
import hashlib
import gzip
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


def _undo_links(text):
    """The stored ``undoes`` column as ``[{"event", "units"}]`` (docs/271)."""
    try:
        raw = json.loads(text) if text else None
    except ValueError:
        return []
    if isinstance(raw, str):
        raw = [raw]
    links = []
    for item in raw or ():
        if isinstance(item, str):
            links.append({"event": item, "units": None})
        elif isinstance(item, dict) and item.get("event"):
            links.append({"event": str(item["event"]), "units": item.get("units")})
    return links


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
             day_to=None, cursor=None, limit=50, include_runs=False):
    """Return {events: [... with changes], cursor: str | None}, newest first.

    The cursor freezes the initial max eid, excluding subsequent appends even
    when they arrive late. Its canonical key survives rank renumbering. Changes
    to existing events remain live, rather than pretending to be a saved view.

    docs/281: every event also carries ``root_path`` and ``first`` (it is the
    ledger's first event, so its rows are the starting state); an SM event
    carries ``sm_id``, ``outcome``, ``undo_links`` (each with the ``target``
    event's instant, kind and door when the ledger holds it) and either
    ``exact_entries`` (the entries recorded at the door) or ``entries_error``.
    ``include_runs`` adds ``runs``: every run event of the ledger, for
    matching agent records against folders on any day.
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
        roots = {row["root_id"]: row["path"] for row in conn.execute("SELECT root_id,path FROM roots")}
        journal = None
        targets = set()
        for start in range(0, len(selected), 500):
            chunk = selected[start:start + 500]
            sql = "SELECT eid,sm_id,outcome,undoes,entries,entries_n FROM sm_events WHERE eid IN (" + ",".join("?" for _ in chunk) + ")"
            for row in conn.execute(sql, chunk):
                fact = {key: row[key] for key in ("sm_id", "outcome", "undoes")}
                try:
                    if row["entries"] is not None:
                        fact["exact_entries"] = json.loads(gzip.decompress(row["entries"]))
                    elif row["entries_n"] and row["outcome"] == "landed":
                        # Entries too large for the projection live in the
                        # primary journal; read its lines once per query.
                        if journal is None:
                            from quam_state_manager.core.hub import Hub
                            raw = store.store if hasattr(store, "store") else store
                            primary = Hub(raw.directory)
                            journal = (primary, {line["id"]: line for line in primary.events()})
                        line = journal[1].get(row["sm_id"])
                        if line is None:
                            raise ValueError("its journal line is missing")
                        fact["exact_entries"] = journal[0].entries_of(line)
                except (OSError, ValueError, EOFError) as exc:
                    # One unreadable entry list withholds that event's
                    # entries only; the reader says so on that event.
                    fact["entries_error"] = f"the exact write entries are unavailable ({exc})"
                links = _undo_links(fact["undoes"])
                fact["undo_links"] = links
                targets.update(link["event"] for link in links)
                events[row["eid"]].update(fact)
        # Name what an undo or redo takes back: its target's instant and door.
        named = {}
        ordered = sorted(targets)
        for start in range(0, len(ordered), 500):
            chunk = ordered[start:start + 500]
            sql = ("SELECT s.sm_id,e.t_utc_us,e.kind,e.src FROM sm_events s JOIN events e USING(eid) "
                   "WHERE s.sm_id IN (" + ",".join("?" for _ in chunk) + ")")
            named.update((row["sm_id"], dict(row)) for row in conn.execute(sql, chunk))
        for event in events.values():
            for link in event.get("undo_links") or ():
                link["target"] = named.get(link["event"])
        for event in events.values():
            event["root_path"] = roots.get(event["root_id"])
            # The ledger's first event has no earlier state: its rows are the
            # whole starting state, not values that event added.
            event["first"] = index.positions.get(event["eid"]) == 0
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
        result = {"events": [events[eid] for eid in selected], "cursor": next_cursor}
        if include_runs:
            result["runs"] = [dict(row) for row in conn.execute(
                "SELECT run_id,experiment,run_start_us,run_end_us,t_utc_us FROM events WHERE kind='run'")]
        return result


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


def sm_writes_recorded(store):
    """``{"first": instant, "units": ids}``: the instant (UTC us) of the
    earliest SM write the ledger holds (None: none yet) -- SM writes made
    before it were never recorded (docs/271) -- and every undo-journal unit
    id a recorded SM event names (its own units, stamped at the door)."""
    with snapshot(store) as (conn, _index):
        row = conn.execute("SELECT MIN(e.t_utc_us) FROM events e JOIN sm_events s USING(eid)").fetchone()
        units = set()
        for (text,) in conn.execute("SELECT units FROM sm_events WHERE units IS NOT NULL"):
            try:
                units.update(str(u) for u in json.loads(text) or ())
            except (ValueError, TypeError):
                continue
        return {"first": row[0] if row else None, "units": units}


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
