import hashlib
import json
import math

import pytest

from quam_state_manager.core import hub_rules as rules
from quam_state_manager.core.hub_store import HubStore, OPS, json_bytes


def append(store, doc, before=None, **extra):
    root_id = store.register_root(store.directory / "archive", "+00:00")
    count = store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    event = dict(kind="run", t_utc_us=count + 1, ord=count + 1, root_id=root_id,
                 rel_path=f"run{count}", run_id=count, experiment="scan", state_hash=f"hash{count}",
                 flags=0, error=None)
    event.update(extra)
    return store.append(event, rules.diff(rules.flatten(before or {}), rules.flatten(doc)), doc, rules.flatten(doc))


def test_schema_wal_and_versions(tmp_path):
    with HubStore(tmp_path / "ledger") as store:
        assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert store.meta("schema_version") == "1"
        assert store.meta("rule_version") == "269-1"
        assert store.meta("checkpoint_interval") == "250"
        tables = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"events", "paths", "changes", "blobs", "checkpoints", "roots", "meta", "locations"} <= tables
        store.set_meta("rule_version", "future")
        store.conn.commit()
    with pytest.raises(ValueError, match="incompatible ledger"):
        HubStore(tmp_path / "ledger")


def test_blob_gzip_content_address_and_corruption(tmp_path):
    import gzip

    with HubStore(tmp_path / "ledger") as store:
        payload = json_bytes([1, True, "text"])
        digest = store.put_blob(payload)
        assert digest == hashlib.sha1(payload).hexdigest()
        assert store.put_blob(payload) == digest
        assert store.conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] == 1
        assert store.blob(digest) == [1, True, "text"]
        gz = store.conn.execute("SELECT gz FROM blobs").fetchone()[0]
        assert gzip.decompress(gz) == payload
        with pytest.raises(ValueError, match="disagrees"):
            store.put_blob(payload, expected_hash="wrong")
        store.conn.execute("UPDATE blobs SET gz=?", (gzip.compress(b"{}"),))
        with pytest.raises(ValueError, match="corrupt ledger blob"):
            store.blob(digest)


def test_lossless_numbers_and_null_absence(tmp_path):
    first = {"nan": float("nan"), "pos": float("inf"), "neg": -float("inf"),
             "huge": 2**90 + 1, "precise": 2**53 + 1, "null": None, "bool": True, "text": "word"}
    second = {"nan": float("nan"), "huge": 2**90 + 2, "bool": False, "added": None}
    with HubStore(tmp_path / "ledger") as store:
        a = append(store, first)
        b = append(store, second, first)
        assert rules.same(store.state_at(a), first)
        assert rules.same(store.state_at(b), second)
        assert math.isnan(store.state_at(a)["nan"])
        rows = {r["path"]: r for r in store.conn.execute("SELECT * FROM changes JOIN paths USING(pid) WHERE eid=?", (b,))}
        assert rows["null"]["op"] == OPS["gone"] and rows["null"]["old_txt"] == "null"
        assert rows["added"]["txt"] == "null" and rows["added"]["old_txt"] is None
        assert json.loads(rows["huge"]["old_txt"]) == first["huge"]
        assert "nan" not in rows


def test_arrays_shapes_and_escaped_paths_survive_source_loss(tmp_path):
    first = {"a.b": {"": [1.0] * 17}, "a": {"b": 3}, "\\e": {"0": []}, "empty": {}}
    second = {"a.b": {"": [True] * 18}, "a": {"b": []}, "\\e": [None, {}], "empty": []}
    with HubStore(tmp_path / "ledger") as store:
        a = append(store, first)
        b = append(store, second, first)
        assert rules.same(store.state_at(a), first)
        assert rules.same(store.state_at(b), second)
        marker = rules.flatten(first)["a\\.b.\\e"]
        assert store.blob(marker["_hash"]) == [1] * 17
        assert len([c for c in store.diff(a, b) if c.path == "a\\.b.\\e"]) == 1


def test_checkpoint_cadence_and_bounded_replay(tmp_path):
    with HubStore(tmp_path / "ledger", checkpoint_interval=2) as store:
        docs = [{"v": i, "array": [i] * 17} for i in range(5)]
        eids = [append(store, doc, docs[i - 1] if i else {}) for i, doc in enumerate(docs)]
        cps = list(store.conn.execute("SELECT eid,hash FROM checkpoints ORDER BY eid"))
        assert [r["eid"] for r in cps] == [2, 4]
        assert rules.same(store.blob(cps[0]["hash"]), docs[1])
        for eid, doc in zip(eids, docs):
            assert rules.same(store.state_at(eid), doc)
        assert store.diff(eids[0], eids[-1]) == rules.diff(rules.flatten(docs[0]), rules.flatten(docs[-1]))
        with pytest.raises(KeyError):
            store.state_at(999)
    with HubStore(tmp_path / "ledger", checkpoint_interval=2) as store:
        assert rules.same(store.state_at(5), docs[-1])


def test_event_transaction_rolls_back_all_projection_data(tmp_path):
    with HubStore(tmp_path / "ledger", checkpoint_interval=1) as store:
        store.conn.execute("CREATE TRIGGER fail_event BEFORE INSERT ON changes BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with pytest.raises(Exception, match="injected"):
            append(store, {"v": 1})
        for table in ("events", "changes", "locations", "paths", "blobs", "checkpoints"):
            assert store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert store.meta("watermark:1") is None
        assert store._pids == {}
        store.conn.execute("DROP TRIGGER fail_event")
        eid = append(store, {"v": 2})
        assert store.state_at(eid) == {"v": 2}


def test_error_event_is_not_a_reconstructed_run_state(tmp_path):
    with HubStore(tmp_path / "ledger") as store:
        eid = append(store, {}, error="missing state.json", status="error")
        with pytest.raises(ValueError, match="no saved state"):
            store.state_at(eid)
        assert store.event(eid)["status"] == "error"
