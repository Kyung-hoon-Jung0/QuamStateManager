"""Shared ledger read API, with warming exceptions instead of partial answers."""

from __future__ import annotations

import base64
import bisect
from datetime import date
import hashlib
import gzip
import json
from pathlib import Path

from quam_state_manager.core import search_query
from quam_state_manager.core.hub_index import context, require_day_zone, snapshot
from quam_state_manager.core.hub_store import OPS, SM_KINDS, value

_OP_NAMES = {number: name for name, number in OPS.items()}


def _events(conn, eids, index=None):
    """``{eid: event dict}``; read through a folder view (*index* with a
    lane, S10 C1.5) each dict carries the lane's flags / base hash and, for
    another folder's event, its ``_source``."""
    result = {}
    ids = list(eids)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        sql = "SELECT * FROM events WHERE eid IN (" + ",".join("?" for _ in chunk) + ")"
        result.update((row["eid"], dict(row)) for row in conn.execute(sql, chunk))
    lane = getattr(index, "lane", None)
    if lane is not None:
        result = {eid: lane.event(ev) for eid, ev in result.items()}
    return result


def _change(row):
    out = {"path": row["path"], "old": value(row["old_num"], row["old_txt"]),
           "new": value(row["num"], row["txt"]), "op": _OP_NAMES[row["op"]],
           "proven": bool(row["proven"])}
    if isinstance(row, dict) and row.get("held"):
        # S10 C1.5: this folder held the value when SM wrote; writer unknown
        out["held"] = True
    if isinstance(row, dict) and row.get("start"):
        # the folder's starting state at its first SM write: first recorded
        out["start"] = True
    return out


def event_rows(conn, index, eid):
    """S10 C1.5: one event's change rows as a folder view reads them (each a
    mapping with ``path``): a seam's rows within the lane, else the stored
    rows."""
    lane = getattr(index, "lane", None)
    got = lane.rows(eid) if lane is not None else None
    if got is not None:
        return sorted(got, key=lambda r: r["path"])
    return conn.execute("SELECT c.*, p.path FROM changes c JOIN paths p USING(pid) WHERE c.eid=? "
                        "ORDER BY p.path", (eid,)).fetchall()


def path_rows(conn, index, pid):
    """S10 C1.5: ``(eid, op, num, txt, old_num, old_txt)`` of every row of
    holder *pid* the (view) index reads: its events' stored rows, a seam's
    own rows in their place."""
    lane = getattr(index, "lane", None)
    out = []
    if pid is not None and pid >= 0:
        for eid, op, num, txt, onum, otxt in conn.execute(
                "SELECT eid, op, num, txt, old_num, old_txt FROM changes WHERE pid=?", (pid,)):
            if lane is not None and eid in lane.seams:
                continue
            out.append((eid, op, num, txt, onum, otxt))
    if lane is not None:
        for eid, rows in lane.seams.items():
            r = rows.get(pid)
            if r is not None:
                out.append((eid, r["op"], r["num"], r["txt"], r["old_num"], r["old_txt"]))
    return out


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
             day_to=None, cursor=None, limit=50, include_runs=False,
             include_ambiguous=False, path_prefix=False, event_id=None, changed_only=False,
             lineage=None, era=(), foreign=None):
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
    ``include_ambiguous`` adds ``ambiguous_run_ids``: the run numbers that
    two data folders both hold (run numbers are per folder, so such a number
    names no single run by itself).
    docs/283 (Param History Changes): ``path_prefix`` treats ``path`` as a
    case-insensitive prefix of the holder path; ``event_id`` keeps one event;
    ``changed_only`` keeps events that changed at least one value.
    docs/296: ``lineage`` / ``era`` (the chip's rename lineage and today's
    era) -- every row is spelled in today's ids (``recorded_as`` keeps the
    spelling it was saved under), the event where a rename came into force
    has its rows recomputed qubit by qubit (``renamed_here``), and ``path``
    is matched in each event's own era.

    S10 C1.5: read through a folder view, the events are the folder's lane
    (each another folder's earlier event carrying its ``_source``) and a
    seam's rows are its difference within the lane. ``foreign="label"`` (a
    LISTING: Versions, State History) lists every event of the ledger; one
    outside the lane carries its ``_source`` and ``foreign`` True and
    withholds its rows (``changes`` empty, ``n_changes`` None) -- except an
    SM write, which shows its own entries only.
    """
    with snapshot(store) as (conn, view):
        lane = getattr(view, "lane", None)
        index = lane.base if (lane is not None and foreign == "label") else view
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("timeline limit must be a positive integer")
        lo, hi = _bound(day_from), _bound(day_to)
        if lo or hi:
            require_day_zone(store)
        if lo and hi and lo > hi:
            raise ValueError("day_from must not follow day_to")
        kinds = sorted(set([kinds] if isinstance(kinds, str) else kinds or []))
        filters = (q, kinds, entity, path, lo, hi)
        if path_prefix or event_id is not None or changed_only:
            filters += (bool(path_prefix), event_id, bool(changed_only))
        if lane is not None:
            # S10 C1.5: a cursor of one folder's view never pages another's
            filters += (lane.digest, foreign)
        signature = hashlib.sha256(json.dumps(filters, separators=(",", ":")).encode()).hexdigest()
        high = max(index.eids, default=0)
        last = None
        if cursor is not None:
            data = _decode(cursor)
            if (data.get("ledger") != index.ledger_id or data.get("zone") != index.zone
                    or data.get("filters") != signature):
                raise ValueError("timeline cursor belongs to another ledger, zone, or query")
            high, last = data["high"], tuple(data["last"])
        ren = None
        if lineage is not None and (lineage.active or era):
            from quam_state_manager.core.hub_eras import Renamer
            ren = Renamer(conn, index, lineage, era)
            if not ren.active:
                ren = None
        found = _search(store, index, q)
        if kinds:
            allowed = set()
            for kind in kinds:
                allowed.update(index.postings["kind"].get(kind.lower(), ()))
            found.intersection_update(allowed)
        if entity is not None:
            found.intersection_update(index.postings["entity"].get(entity.lower(), ()))
        if path is not None and ren is not None:
            from quam_state_manager.core.hub_eras import path_filter
            found.intersection_update(path_filter(index, ren, path, bool(path_prefix)))
        elif path is not None:
            if path_prefix:
                # docs/283: Param History Changes' filter -- a case-insensitive
                # prefix of the holder path (the change-point index's LIKE 'p%')
                low = path.lower()
                allowed = set()
                for spelling, pid in index.paths.items():
                    if spelling.lower().startswith(low):
                        allowed.update(index.path_postings.get(pid, ()))
                found.intersection_update(allowed)
            else:
                found.intersection_update(index.path_postings.get(index.paths.get(path), ()))
        if event_id is not None:
            found.intersection_update((event_id,))
        if changed_only:
            changed = {row[0] for row in conn.execute("SELECT eid FROM events WHERE n_changes>0")}
            if lane is not None:
                for eid, rows in lane.seams.items():
                    if rows:
                        changed.add(eid)
                    else:
                        changed.discard(eid)
            found.intersection_update(changed)
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
        events = _events(conn, selected, view if lane is not None else index)
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
        # The first event that HAS a state has no earlier state to compare
        # with: its rows are the whole starting state, not values it added.
        # Leading events with no readable state (an error) do not count.
        first = conn.execute("SELECT eid FROM events WHERE error IS NULL ORDER BY ord LIMIT 1").fetchone()
        first = first[0] if first else None
        if lane is not None:
            first = lane.first
        for event in events.values():
            event["root_path"] = roots.get(event["root_id"])
            event["first"] = event["eid"] == first
            event["changes"] = []
        seamed = set(lane.seams) if lane is not None else set()
        withheld = set()
        if lane is not None:
            for eid in selected:
                if eid in lane.hidden:
                    events[eid]["foreign"] = True
                    if events[eid]["kind"] not in SM_KINDS:
                        withheld.add(eid)       # a foreign run / observed state: no change count
                        events[eid]["n_changes"] = None
        stored = [e for e in selected if e not in seamed and e not in withheld]
        for start in range(0, len(stored), 500):
            chunk = stored[start:start + 500]
            sql = ("SELECT c.*,p.path FROM changes c JOIN paths p USING(pid) WHERE eid IN ("
                   + ",".join("?" for _ in chunk) + ") ORDER BY p.path")
            for row in conn.execute(sql, chunk):
                events[row["eid"]]["changes"].append(_change(row))
        for eid in selected:
            if eid in seamed and eid not in withheld:
                rows = sorted(lane.seams[eid].values(), key=lambda r: r["path"])
                events[eid]["changes"] = [_change(r) for r in rows]
                events[eid]["n_changes"] = len(rows)
        if foreign != "label":
            # P0-1: a run's value the chip never kept is listed apart, and the
            # run that still found the old value did not change it. A LISTING
            # of saved states (Versions, State History) keeps every row.
            witness_rows(conn, view, [events[eid] for eid in selected])
        next_cursor = _encode({"v": 1, "ledger": index.ledger_id, "zone": index.zone,
                               "filters": signature, "high": high,
                               "last": index.keys[selected[-1]]}) if more else None
        _taken_back(conn, events)
        if ren is not None:
            from quam_state_manager.core.hub_eras import annotate
            annotate(conn, index, ren, [events[eid] for eid in selected])
            if path is not None:
                # the event where a rename came into force is listed by the rows
                # it has once each qubit is compared with itself
                low = path.lower()
                selected = [eid for eid in selected if any(
                    (c["path"].lower().startswith(low) if path_prefix else c["path"] == path)
                    for c in events[eid]["changes"])]
        result = {"events": [events[eid] for eid in selected], "cursor": next_cursor}
        if lane is not None:
            # S10 C1.5: what the folder's view left out, counted
            result["left_out"] = lane.left_out
        if include_ambiguous:
            result["ambiguous_run_ids"] = {row[0] for row in conn.execute(
                "SELECT run_id FROM events WHERE kind='run' AND run_id IS NOT NULL "
                "GROUP BY run_id HAVING COUNT(DISTINCT root_id) > 1")}
        if include_runs:
            result["runs"] = [dict(row, root_path=roots.get(row["root_id"])) for row in conn.execute(
                "SELECT eid,run_id,experiment,root_id,rel_path,run_start_us,run_end_us,t_utc_us "
                "FROM events WHERE kind='run'")]
        return result


def _taken_back(conn, events):
    """docs/281: which paths of an SM write a later undo took back -- the
    entries (else the ledger rows) of every ``undo`` event that names it, so a
    partly undone write can mark its rows."""
    from quam_state_manager.core.hub_store import PARTLY_UNDONE, UNDONE
    wanted = {e["sm_id"]: e for e in events.values()
              if e.get("sm_id") and (e.get("flags") or 0) & (UNDONE | PARTLY_UNDONE)}
    if not wanted:
        return
    for event in wanted.values():
        event["taken_back"] = set()
    rows = conn.execute("SELECT s.eid, s.undoes, s.entries FROM sm_events s JOIN events e USING(eid) "
                        "WHERE e.kind='undo' AND s.undoes IS NOT NULL").fetchall()
    for row in rows:
        targets = [link["event"] for link in _undo_links(row["undoes"]) if link["event"] in wanted]
        if not targets:
            continue
        paths = set()
        try:
            if row["entries"] is not None:
                paths = {str(e.get("path")) for e in json.loads(gzip.decompress(row["entries"]))}
        except (OSError, ValueError, EOFError):
            paths = set()
        if not paths:
            paths = {r[0] for r in conn.execute(
                "SELECT p.path FROM changes c JOIN paths p USING(pid) WHERE c.eid=?", (row["eid"],))}
        for sm_id in targets:
            wanted[sm_id]["taken_back"] |= paths


def previous_in_folder(store, eids):
    """docs/281: for each event, the nearest earlier run of the same node in
    the same data folder on an overlapping target -- the run "Parameters
    changed vs" names for a run of a folder Datasets has not open.
    ``{eid: (run_id, folder)}``, one read snapshot for all of them."""
    out = {}
    if not eids:
        return out
    with snapshot(store) as (conn, _index):
        roots = {row[0]: row[1] for row in conn.execute("SELECT root_id, path FROM roots")}
        # docs/291: one read per (folder, node), not one per run -- a day of
        # 3,000 runs was 3,000 queries. Each run still looks at the 50 runs of
        # its folder and node just before it, nearest first.
        ids = list(eids)
        cur_of = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            sql = ("SELECT eid, root_id, experiment, ord, targets FROM events WHERE eid IN ("
                   + ",".join("?" for _ in chunk) + ")")
            cur_of.update((row["eid"], row) for row in conn.execute(sql, chunk))
        span = {}
        for cur in cur_of.values():
            if cur["root_id"] is None:
                continue
            key = (cur["root_id"], cur["experiment"])
            lo, hi = span.get(key, (cur["ord"], cur["ord"]))
            span[key] = (min(lo, cur["ord"]), max(hi, cur["ord"]))
        before = {}
        for (root_id, experiment), (lo, hi) in span.items():
            edge = conn.execute("SELECT ord FROM events WHERE kind='run' AND root_id=? AND experiment=? AND ord<? "
                                "ORDER BY ord DESC LIMIT 1 OFFSET 49", (root_id, experiment, lo)).fetchone()
            sql = ("SELECT run_id, rel_path, targets, ord FROM events WHERE kind='run' AND root_id=? "
                   "AND experiment=? AND ord<?" + (" AND ord>=?" if edge else "") + " ORDER BY ord")
            rows = conn.execute(sql, (root_id, experiment, hi) + ((edge[0],) if edge else ())).fetchall()
            before[(root_id, experiment)] = ([row["ord"] for row in rows], rows)
        names, folders = {}, {}

        def targets_of(text):
            hit = names.get(text)
            if hit is None:
                hit = names[text] = set(_target_names(text))
            return hit
        for eid in eids:
            cur = cur_of.get(eid)
            if cur is None or cur["root_id"] is None:
                continue
            ords, rows = before[(cur["root_id"], cur["experiment"])]
            at = bisect.bisect_left(ords, cur["ord"])
            mine = targets_of(cur["targets"])
            for row in reversed(rows[max(0, at - 50):at]):
                theirs = targets_of(row["targets"])
                if not mine or not theirs or mine & theirs:
                    base = folders.get(cur["root_id"])
                    if base is None:
                        base = folders[cur["root_id"]] = Path(roots[cur["root_id"]])
                    out[eid] = (row["run_id"], str(base / (row["rel_path"] or "")))
                    break
    return out


def _target_names(text):
    try:
        value = json.loads(text) if text else []
    except ValueError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def _series(conn, index, path, limit=None, before=None, *, witness=True):
    """``(event, old, new, op, proven)`` of holder *path*, oldest first.

    P0-1 (``hub_witness``): a run change the chip never kept (contradicted,
    with its witness's restoring row) is left out together with that row, so
    the value stays the old one across both -- and so is an excursion
    (unconfirmed saves that came back exactly to the value the chip held,
    with the save that came back); every other judged run change
    carries its verdict on the event dict (``_witness``). ``witness=False``
    reads what each event SAVED (Column History's By run)."""
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0):
        raise ValueError("series limit must be a nonnegative integer")
    pid = index.paths.get(path)
    if pid is None:
        return []
    ids = index.path_postings[pid]
    verdicts = None
    if witness:
        from quam_state_manager.core import hub_witness
        verdicts = hub_witness.of(conn, index)
        drop = verdicts.drop(pid)
        if drop:
            ids = [eid for eid in ids if eid not in drop]
    if before is not None:
        # eid is insertion identity; the boundary is the event's canonical order.
        ids = [eid for eid in ids if index.positions[eid] <= before]
    ids = sorted(ids, key=index.positions.__getitem__)
    if limit is not None:
        ids = ids[-limit:] if limit else []
    if not ids:
        return []
    events = _events(conn, ids, index)
    changes = {}
    lane = getattr(index, "lane", None)
    stored = ids
    if lane is not None:
        # S10 C1.5: a seam's row is its difference within the lane
        stored = []
        for eid in ids:
            if eid not in lane.seams:
                stored.append(eid)
                continue
            row = lane.seams[eid].get(pid)
            if row is not None:
                changes[eid] = row
                if row.get("held"):
                    # this folder held it when SM wrote: the write is not
                    # its writer
                    events[eid] = dict(events[eid], _held=True)
                elif row.get("start"):
                    # the folder's starting state: first recorded, writer unknown
                    events[eid] = dict(events[eid], _start=True)
    for start in range(0, len(stored), 500):
        chunk = stored[start:start + 500]
        sql = ("SELECT * FROM changes WHERE pid=? AND eid IN ("
               + ",".join("?" for _ in chunk) + ")")
        for row in conn.execute(sql, [pid, *chunk]):
            changes[row["eid"]] = row
    if verdicts is not None:
        for eid in ids:
            code = verdicts.code(eid, pid)
            if code is not None and eid in events:
                events[eid]["_witness"] = code      # this call's own dict
    return [(events[eid], value(changes[eid]["old_num"], changes[eid]["old_txt"]),
             value(changes[eid]["num"], changes[eid]["txt"]),
             _OP_NAMES[changes[eid]["op"]], bool(changes[eid]["proven"])) for eid in ids
            if eid in changes]


def not_kept(conn, index, path, rows=None):
    """P0-1: the run changes of holder *path* the chip never kept (the rows
    :func:`_series` leaves out), oldest first, as ``(event, old, new, op,
    proven, witness event)`` -- the witness being the event that read the chip
    and still found the old value (its own restoring row is the pair's other
    half). *rows*: the holder's saved rows when the caller has read them
    (``_series(..., witness=False)``)."""
    from quam_state_manager.core import hub_witness
    pid = index.paths.get(path)
    if pid is None:
        return []
    pairs = hub_witness.of(conn, index).pairs_of(pid)
    if not pairs:
        return []
    rows = _series(conn, index, path, witness=False) if rows is None else rows
    rows = {r[0]["eid"]: r for r in rows if r[0]["eid"] in pairs}
    wit = _events(conn, list(pairs.values()), index)
    return [rows[eid] + (wit.get(pairs[eid]),) for eid in sorted(rows, key=index.positions.__getitem__)]


def witness_rows(conn, index, events):
    """P0-1 for a listing of events (the timeline): each event's ``changes``
    split by the witness verdicts (``hub_witness``) -- what is not chip
    history moves to the event's ``not_kept``: a contradicted change (with
    ``witness``: the event that read the chip and still found the old value)
    and a point of an excursion (``excursion`` True; ``witness``: the save
    that came back, ``held``: the value it came back to); the witness's own
    restoring row and an excursion's return are left out (that event did not
    change the chip). A change that stays but is not confirmed on the chip
    carries ``doubt`` (its verdict) -- never a plain change. Rows are matched
    to holders by their path in this index."""
    from quam_state_manager.core import hub_witness
    v = hub_witness.of(conn, index)
    v.prime({index.paths.get(c["path"]) for ev in events for c in ev.get("changes") or ()})
    named: dict = {}
    for ev in events:
        changes = ev.get("changes")
        if not changes:
            continue
        eid = ev["eid"]
        kept, out = [], []
        for c in changes:
            pid = index.paths.get(c["path"])
            why = v.left_out_as(eid, pid)
            if why == "contradicted":
                out.append((c, v.pairs_of(pid)[eid], None))
            elif why == "excursion":
                back = next(r for _a, pts, r in v.excursions_of(pid) if eid in pts)
                out.append((c, back, pid))
            elif why is None:
                code = v.code(eid, pid) if ev.get("kind") == "run" and not ev.get("first") else None
                kept.append(dict(c, doubt=code) if code in hub_witness.UNCONFIRMED else c)
        if len(kept) == len(changes) and all(a is b for a, b in zip(kept, changes)):
            continue
        ev["changes"] = kept
        if out:
            need = [w for _c, w, _p in out if w not in named]
            named.update(_events(conn, need, index))
            ev["not_kept"] = []
            for c, w, pid in out:
                row = dict(c, witness=hub_witness.describe(index, named.get(w), w))
                if pid is not None:
                    got = conn.execute("SELECT num, txt FROM changes WHERE pid=? AND eid=?",
                                       (pid, w)).fetchone()
                    row.update(excursion=True, held=value(got[0], got[1]) if got else None)
                ev["not_kept"].append(row)


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
