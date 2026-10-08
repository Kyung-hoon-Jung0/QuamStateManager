"""Read-side contract pins, using only generic synthetic ledgers."""

from array import array
from contextlib import contextmanager
import json
import math
import sqlite3

import pytest

from quam_state_manager.core import hub_build, hub_index, hub_query as query, hub_rules as rules
from quam_state_manager.core import hub_sync, project_time, search_query
from quam_state_manager.core.hub_store import HubStore
from quam_state_manager.core.ramcache import Warming


@pytest.fixture(autouse=True)
def read_handles():
    yield
    hub_index.close_readers()


@pytest.fixture
def ledger(tmp_path):
    with HubStore(tmp_path / "ledger") as store:
        root = store.register_root(tmp_path / "archive", "+00:00")
        previous = {}

        def append(doc, *, experiment="scan", targets=None, actor="human:user-a",
                   kind="run", instant=None, proven=None):
            nonlocal previous
            rank = store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] + 1
            flat = rules.flatten(doc)
            event = {"kind": kind, "ord": rank, "root_id": root, "rel_path": f"run-{rank}",
                     "run_id": rank, "experiment": experiment, "actor": actor,
                     "targets": json.dumps(targets), "flags": rank,
                     "t_utc_us": instant if instant is not None else 1767225600000000 + rank * 1000000,
                     "state_hash": str(rank), "base_hash": str(rank - 1), "status": "finished"}
            eid = store.append(event, rules.diff(previous, flat), doc, flat, proven=proven)
            previous = flat
            return eid

        store.add = append
        yield store


def ids(page):
    return [event["eid"] for event in page["events"]]


def test_timeline_newest_first_exact_rows_and_zero_change_events(ledger):
    a = ledger.add({"v": 1, "flag": True})
    b = ledger.add({"v": 2, "flag": True}, proven={"v"})
    c = ledger.add({"v": 2, "flag": True})
    page = query.timeline(ledger)
    assert ids(page) == [c, b, a] and page["cursor"] is None
    assert page["events"][0]["changes"] == []
    assert page["events"][1]["changes"] == [
        {"path": "v", "old": 1, "new": 2, "op": "set", "proven": True}]
    assert [row["path"] for row in page["events"][2]["changes"]] == ["flag", "v"]


def test_series_old_new_operations_proof_and_latest_limit(ledger):
    a = ledger.add({"v": None})
    b = ledger.add({"v": True}, proven={"v"})
    c = ledger.add({})
    rows = query.series(ledger, "v")
    assert [row[0]["eid"] for row in rows] == [a, b, c]
    assert [row[1:] for row in rows] == [(None, None, "add", False),
                                       (None, True, "set", True), (True, None, "gone", False)]
    assert query.series(ledger, "v", limit=2) == rows[-2:]
    assert query.series(ledger, "v", limit=0) == []


def test_series_many_snapshot_and_missing_paths(ledger):
    ledger.add({"x": 1, "y": 2})
    rows = query.series_many(ledger, ["x", "y", "missing"])
    assert set(rows) == {"x", "y", "missing"}
    assert rows == {path: query.series(ledger, path) for path in rows}
    assert rows["x"][0][2] == 1 and rows["y"][0][2] == 2 and rows["missing"] == []


def test_newest_change_is_latest_row_not_latest_event(ledger):
    ledger.add({"v": 1})
    b = ledger.add({"v": 2})
    ledger.add({"v": 2})
    row = query.newest_change(ledger, "v")
    assert row[0]["eid"] == b and row[1:] == (1, 2, "set", False)
    assert query.newest_change(ledger, "missing") is None


def test_writer_of_inclusive_boundary_actor_kind_run_and_unknown(ledger):
    a = ledger.add({"v": 1}, actor="human:user-a")
    b = ledger.add({"v": 2}, actor="autofit", kind="autofit")
    c = ledger.add({"v": 2})
    writer = query.writer_of(ledger, "v", b)
    assert writer["eid"] == b and writer["actor"] == "autofit"
    assert writer["kind"] == "autofit" and writer["run_id"] == 2
    assert query.writer_of(ledger, "v", a)["eid"] == a
    assert query.writer_of(ledger, "v", c)["eid"] == b
    assert query.writer_of(ledger, "missing", c) is None
    with pytest.raises(KeyError):
        query.writer_of(ledger, "v", 999)


def test_search_shared_and_or_grammar_and_literal_pipes(ledger, monkeypatch):
    a = ledger.add({}, experiment="scan rabi amplitude")
    b = ledger.add({}, experiment="scan length")
    c = ledger.add({}, experiment="rabi length")
    d = ledger.add({}, experiment="scan |e> a|b |")
    seen = []
    original = search_query.groups
    monkeypatch.setattr(search_query, "groups", lambda text: (seen.append(text), original(text))[1])
    assert query.search(ledger, "scan amplitude | length") == [b, a]
    assert query.search(ledger, "RABI length") == [c]
    assert query.search(ledger, "scan |e>") == [d]
    assert query.search(ledger, "a|b") == [d]
    assert query.search(ledger, "| scan") == [d]
    assert query.search(ledger, "scan |") == [d]
    assert query.search(ledger, "scan | | length") == []
    assert seen and query.search(ledger, "") == [d, c, b, a]


def test_pair_members_and_macro_targets_and_changed_segments(ledger):
    a = ledger.add({}, targets={"qubit_pairs": ["qA2-qA1"], "cz_macro_name": "cz_SNZ"})
    b = ledger.add({"qubit_pairs": {"qA3-qA4": {"cz_ALT": {"amplitude": 1}}}})
    c = ledger.add({}, targets={"qubits": ["qA10"]})
    assert query.search(ledger, "qA2-qA1 qA1 qA2 cz_SNZ") == [a]
    assert query.search(ledger, "qA3 cz_ALT") == [c, b]
    assert query.search(ledger, "qA1") == [a]
    assert query.search(ledger, "qA10") == [c]
    assert ids(query.timeline(ledger, entity="qA4")) == [c, b]


def test_day_comes_from_project_zone_not_folder_or_pc(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    for rid, clock in enumerate(("2026-01-01T10:59:59-04:00", "2026-01-01T11:00:00-04:00"), 1):
        folder = root / "2025-12-31" / f"#{rid}_scan_120000"
        (folder / "quam_state").mkdir(parents=True)
        (folder / "node.json").write_text(json.dumps({"created_at": clock}), encoding="utf-8")
        (folder / "quam_state" / "state.json").write_text("{}", encoding="utf-8")
        (folder / "quam_state" / "wiring.json").write_text("{}", encoding="utf-8")
    hub_build.build(root, out)
    instance = tmp_path / "instance"
    project_time.set_zone(instance, "project", "Asia/Seoul")
    with HubStore(out) as store:
        bound = query.context(store, instance=instance, project="project")
        assert query.search(bound, "2026-01-01") == [1]
        assert query.search(bound, "2026-01-02") == [2]
        assert ids(query.timeline(bound, day_from="2026-01-02", day_to="2026-01-02")) == [2]
        assert query.search(bound, "2025-12-31") == []
        project_time.set_zone(instance, "project", "UTC")
        assert query.search(bound, "2026-01-01") == [2, 1]


def test_day_filters_inclusive_open_ends_and_dst(ledger):
    # New York's spring transition makes this local day only 23 hours long.
    a = ledger.add({}, instant=1772946000000000)
    b = ledger.add({}, instant=1773028799999999)
    c = ledger.add({}, instant=1773028800000000)
    bound = query.context(ledger, zone="America/New_York")
    assert ids(query.timeline(bound, day_from="2026-03-08", day_to="2026-03-08")) == [b, a]
    assert ids(query.timeline(bound, day_from="2026-03-09")) == [c]
    assert ids(query.timeline(bound, day_to="2026-03-08")) == [b, a]


def test_missing_project_zone_fails_and_offline_utc_is_explicit(ledger, tmp_path):
    ledger.add({})
    assert query.search(query.context(ledger, zone="UTC"), "2026-01-01") == [1]
    for call in (lambda: query.search(ledger, "2026-01-01"),
                 lambda: query.timeline(ledger, day_from="2026-01-01"),
                 lambda: query.timeline(ledger, q="2026-01-01")):
        with pytest.raises(ValueError, match="time zone"):
            call()
    # docs/285: a lab that set no project zone gets this PC's zone (the same
    # fallback the report and the log's "today" use) -- the refusal this line
    # used to pin left the Calibration log empty on every such chip. Only when
    # the PC's zone is unknown too does the bound context still refuse.
    from quam_state_manager.core import project_time
    real_pc_zone = project_time.pc_zone
    try:
        project_time.pc_zone = lambda: "UTC"
        assert query.search(query.context(ledger, instance=tmp_path, project="unset"), "2026-01-01") == [1]
        project_time.pc_zone = lambda: None
        with pytest.raises(ValueError, match="time zone"):
            query.search(query.context(ledger, instance=tmp_path, project="unset"), "")
    finally:
        project_time.pc_zone = real_pc_zone
    with pytest.raises(ValueError):
        query.context(ledger, instance=tmp_path, zone="UTC")


def test_timeline_combines_all_filters(ledger):
    a = ledger.add({"qubits": {"qA1": {"frequency": 1}}}, experiment="scan")
    ledger.add({"qubits": {"qA1": {"frequency": 2}}}, experiment="scan", kind="autofit")
    page = query.timeline(query.context(ledger, zone="UTC"), q="scan human:user-a", kinds="run", entity="qA1",
                          path="qubits.qA1.frequency", day_from="2026-01-01", day_to="2026-01-01")
    assert ids(page) == [a]
    assert query.search(ledger, "frequency") == [2, 1]
    assert query.search(ledger, "qubits.qA1.frequency") == [2, 1]
    assert ids(query.timeline(ledger, kinds=["unknown"])) == []


def test_cursor_stable_on_new_and_late_appends_and_rank_renumbering(ledger):
    original = [ledger.add({"v": i}) for i in range(6)]
    page = query.timeline(ledger, limit=2)
    assert ids(page) == original[-2:][::-1] and page["cursor"]
    ledger.add({"v": 99})
    late = ledger.add({"v": 100}, instant=1767225602500000)
    with ledger.conn:
        ledger.conn.execute("UPDATE events SET ord=1000+ord")
        ledger.conn.execute("UPDATE events SET ord=1002.5 WHERE eid=?", (late,))
        ledger.conn.execute("UPDATE events SET ord=ord*2")
    collected = ids(page)
    while page["cursor"]:
        page = query.timeline(ledger, cursor=page["cursor"], limit=1)
        collected.extend(ids(page))
    assert collected == original[::-1]


def test_cursor_rejects_other_query_zone_ledger_and_malformed(ledger, tmp_path):
    ledger.add({})
    ledger.add({})
    cursor = query.timeline(ledger, limit=1)["cursor"]
    for kwargs in ({"q": "scan"}, {"day_from": "2026-01-01"}):
        with pytest.raises(ValueError, match="cursor"):
            query.timeline(query.context(ledger, zone="UTC"), cursor=cursor, **kwargs)
    with pytest.raises(ValueError, match="cursor"):
        query.timeline(query.context(ledger, zone="Asia/Seoul"), cursor=cursor)
    with HubStore(tmp_path / "other") as other:
        with pytest.raises(ValueError, match="cursor"):
            query.timeline(other, cursor=cursor)
    for invalid in ("!", "e30=", hub_query_cursor({"v": 1, "high": 2, "last": []})):
        with pytest.raises(ValueError, match="cursor"):
            query.timeline(ledger, cursor=invalid)


def hub_query_cursor(payload):
    return query._encode(payload)


@pytest.mark.parametrize("call", [
    lambda s: query.timeline(s), lambda s: query.series(s, "v"),
    lambda s: query.series_many(s, ["v"]), lambda s: query.newest_change(s, "v"),
    lambda s: query.writer_of(s, "v", 1), lambda s: query.search(s, "scan")])
def test_every_api_raises_existing_warming_even_on_cached_index(ledger, monkeypatch, call):
    ledger.add({"v": 1})
    query.search(ledger, "scan")
    monkeypatch.setattr(hub_sync, "status", lambda directory: {"state": "building", "done": 1, "total": 2})
    with pytest.raises(hub_sync.Building) as caught:
        call(ledger)
    assert isinstance(caught.value, Warming)


def test_sync_starting_during_query_raises_warming(ledger, monkeypatch):
    ledger.add({"v": 1})
    original = query._events
    def events(*args):
        result = original(*args)
        monkeypatch.setattr(hub_sync, "status", lambda directory: {"state": "building", "done": 1, "total": 2})
        return result
    monkeypatch.setattr(query, "_events", events)
    with pytest.raises(Warming):
        query.timeline(ledger)


def test_alias_free_escaped_and_case_distinct_holder_paths(ledger):
    ledger.add({"a.b": 1, "a": {"b": 2}, "alias": "#./a/b", "A": 3})
    ledger.add({"a.b": 4, "a": {"b": 5}, "alias": "#./A", "A": 6})
    assert [row[2] for row in query.series(ledger, r"a\.b")] == [1, 4]
    assert [row[2] for row in query.series(ledger, "a.b")] == [2, 5]
    assert [row[2] for row in query.series(ledger, "A")] == [3, 6]
    assert query.series(ledger, "alias")[-1][1:] == ("#./a/b", "#./A", "retarget", False)
    assert query.series(ledger, "alias.b") == []
    assert query.search(ledger, r"a\.b") == [2, 1]


def test_payloads_nonfinite_large_numbers_long_array_and_raw_pointer(ledger):
    doc = {"n": float("nan"), "i": 2**70, "inf": float("inf"), "array": list(range(17)),
           "pointer": "#./i", "flag": False, "text": "value"}
    ledger.add(doc)
    number = query.series(ledger, "n")[0][2]
    assert isinstance(number, float) and math.isnan(number)
    assert query.series(ledger, "i")[0][2] == 2**70
    assert query.series(ledger, "inf")[0][2] == float("inf")
    marker = query.series(ledger, "array")[0][2]
    assert isinstance(marker, dict)
    assert marker["_array"] == 17 and len(marker["_hash"]) == 40
    assert query.series(ledger, "array.0") == []
    assert query.series(ledger, "pointer")[0][2] == "#./i"
    assert query.series(ledger, "flag")[0][2] is False
    assert query.series(ledger, "text")[0][2] == "value"


def test_cache_reuses_index_and_invalidates_same_connection_rewrites(ledger):
    ledger.add({"v": 1})
    assert query.search(ledger, "scan") == [1]
    slot = hub_index.INDEX_CACHE.slots()[-1]
    initial = hub_index.INDEX_CACHE.peek(slot)[1]
    query.search(ledger, "scan")
    assert hub_index.INDEX_CACHE.peek(slot)[1] is initial
    with ledger.conn:
        ledger.conn.execute("UPDATE events SET experiment='sweep', actor='autofit' WHERE eid=1")
    assert query.search(ledger, "scan") == []
    assert query.search(ledger, "sweep autofit") == [1]
    assert hub_index.INDEX_CACHE.peek(slot)[1] is not initial


def test_cache_external_changes_rows_and_sparse_eids(ledger):
    ledger.add({"v": 1})
    ledger.add({"v": 2})
    query.search(ledger, "scan")
    with sqlite3.connect(ledger.directory / "ledger.sqlite") as writer:
        writer.execute("UPDATE changes SET num=3 WHERE eid=2")
        writer.execute("UPDATE events SET experiment='sweep' WHERE eid=2")
    assert query.search(ledger, "sweep") == [2]
    assert query.series(ledger, "v")[-1][2] == 3
    # The index must not assume contiguous event identities.
    with ledger.conn:
        ledger.conn.execute("BEGIN")
        ledger.conn.execute("PRAGMA defer_foreign_keys=ON")
        ledger.conn.execute("UPDATE changes SET eid=10 WHERE eid=2")
        ledger.conn.execute("UPDATE locations SET eid=10 WHERE eid=2")
        ledger.conn.execute("UPDATE events SET eid=10 WHERE eid=2")
    assert query.search(ledger, "sweep") == [10]
    with hub_index.snapshot(ledger) as (_, index):
        assert index.positions[10] == 1


def test_columnar_arrays_postings_footprint_and_read_only_sql(ledger):
    ledger.add({"qubits": {"qA1": {"v": 1}}}, targets={"qubits": ["qA1"]})
    with hub_index.snapshot(ledger) as (conn, index):
        assert all(isinstance(getattr(index, name), array)
                   for name in ("eids", "t", "kind", "root", "run_id", "experiment", "flags"))
        assert index.eids.tolist() == [1] and index.flags.tolist() == [1]
        assert index.eids.typecode == "I" and index.t.typecode == "q"
        assert index.root.tolist() == [1] and index.run_id.tolist() == [1]
        assert index.kind.tolist() == [0] and index.experiment.tolist() == [0]
        assert index.t.tolist() == [1767225601000000]
        assert all(ids.typecode == "I" for table in index.postings.values() for ids in table.values())
        assert all(ids.typecode == "I" for ids in index.path_postings.values())
        assert hub_index.footprint(index) > sum(a.buffer_info()[1] * a.itemsize
                                               for a in (index.eids, index.t, index.flags))
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE events SET flags=0")


def test_writer_and_series_follow_canonical_order_not_eid(ledger):
    a = ledger.add({"v": 1})
    b = ledger.add({"v": 2})
    c = ledger.add({"v": 3}, instant=1767225601500000)
    with ledger.conn:
        ledger.conn.execute("UPDATE events SET ord=1.5 WHERE eid=?", (c,))
    assert [row[0]["eid"] for row in query.series(ledger, "v")] == [a, c, b]
    assert query.newest_change(ledger, "v")[0]["eid"] == b
    assert query.writer_of(ledger, "v", b)["eid"] == b
    assert ids(query.timeline(ledger)) == [b, c, a]


def test_concurrent_commit_raises_warming_instead_of_mixed_result(ledger, monkeypatch):
    ledger.add({"v": 1})
    original = query._events
    def events(*args):
        with ledger.conn:
            ledger.conn.execute("UPDATE events SET experiment='sweep'")
        return original(*args)
    monkeypatch.setattr(query, "_events", events)
    with pytest.raises(Warming):
        query.timeline(ledger)


def test_input_limits_days_empty_results_and_sql_literals(ledger):
    assert query.timeline(ledger) == {"events": [], "cursor": None}
    assert query.series(ledger, "missing") == []
    for limit in (-1, 0, True, 1.5):
        with pytest.raises(ValueError):
            query.timeline(ledger, limit=limit)
    for limit in (-1, True, 1.5):
        with pytest.raises(ValueError):
            query.series(ledger, "missing", limit=limit)
    for kwargs in ({"day_from": "invalid"}, {"day_from": "2026-01-02", "day_to": "2026-01-01"}):
        with pytest.raises(ValueError):
            query.timeline(ledger, **kwargs)
    ledger.add({"' OR 1=1 --": 1})
    assert query.series(ledger, "' OR 1=1 --")[0][2] == 1


def test_large_pages_and_many_series_batch_sql_parameters(ledger):
    for i in range(505):
        ledger.add({"v": i})
    assert len(query.timeline(ledger, limit=505)["events"]) == 505
    assert len(query.series(ledger, "v")) == 505


def test_root_empty_key_and_backslash_holder_paths(ledger):
    ledger.add({"": 1, "a\\b": 2})
    assert query.series(ledger, r"\e")[0][2] == 1
    assert query.series(ledger, r"a\\b")[0][2] == 2
    ledger.add(4)
    assert [row[2] for row in query.series(ledger, "")] == [4]


def test_macro_classifier_and_actor_class_and_family_are_distinct(ledger):
    a = ledger.add({"qubits": {"qA1": {"frequency": 1}}}, experiment="cz_FAKE qA9")
    b = ledger.add({}, targets={"cz_macro_name": "cz_REAL"}, actor="by_agent")
    assert query.search(ledger, "cz_FAKE") == []
    assert query.search(ledger, "qA9") == []
    assert query.search(ledger, "cz_REAL by_agent") == [b]
    assert query.search(ledger, "frequency human:user-a") == [a]


def test_timeline_each_filter_excludes_unrelated_events(ledger):
    a = ledger.add({"qubits": {"qA1": {"v": 1}}}, experiment="scan")
    b = ledger.add({"qubits": {"qA1": {"v": 1}}, "other": 2}, experiment="sweep",
                   actor="autofit", kind="autofit", targets={"qubits": ["qA2"]})
    assert ids(query.timeline(ledger, q="scan")) == [a]
    assert ids(query.timeline(ledger, kinds=["run"])) == [a]
    assert ids(query.timeline(ledger, entity="qA1")) == [a]
    assert ids(query.timeline(ledger, path="qubits.qA1.v")) == [a]
    assert ids(query.timeline(ledger, q="autofit")) == [b]


def test_series_many_uses_one_snapshot(ledger, monkeypatch):
    ledger.add({"x": 1, "y": 2})
    original = query._series
    connections = []
    snapshots = []
    original_snapshot = query.snapshot
    @contextmanager
    def snapshot(store):
        snapshots.append(store)
        with original_snapshot(store) as result:
            yield result
    def series(conn, *args, **kwargs):
        connections.append(conn)
        return original(conn, *args, **kwargs)
    monkeypatch.setattr(query, "_series", series)
    monkeypatch.setattr(query, "snapshot", snapshot)
    query.series_many(ledger, ["x", "y"])
    assert len(connections) == 2 and connections[0] is connections[1]
    assert connections[0] is not ledger.conn
    assert len(snapshots) == 1


def test_reader_handles_are_bounded_and_close_drops_index(tmp_path):
    for i in range(10):
        with HubStore(tmp_path / str(i)) as store:
            query.search(store, "")
    assert len(hub_index._READERS) == 8
    directory = tmp_path / "9"
    hub_index.close_readers(directory)
    slot = str(directory.resolve()).lower()
    assert slot not in hub_index._READERS and not hub_index.INDEX_CACHE.has(slot)
