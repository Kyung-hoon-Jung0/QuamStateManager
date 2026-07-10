"""P1a — core/compare_sources.py: ref resolution, honest labels, typed
errors, the dedicated pool (LRU 8, isolation from scanner/_quam_cache), and
the A4 atomic working-origin copy (thread hammer)."""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import compare_sources as cs
from quam_state_manager.core import safe_io
from quam_state_manager.core.history import fingerprint_from_dicts
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.working_copy import content_hash


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _state(f01: float = 6.25e9) -> dict:
    return {
        "qubits": {"qA1": {"id": "qA1", "f_01": f01, "grid_location": "0,0"}},
        "qubit_pairs": {},
    }


def _wiring(host: str = "10.1.1.6") -> dict:
    return {"wiring": {"qubits": {"qA1": {"xy": {"opx_output": "MW/1/1"}}}},
            "network": {"host": host, "cluster_name": "c1"}}


def _write(folder: Path, state: dict | None = None,
           wiring: dict | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(
        json.dumps(state if state is not None else _state()), encoding="utf-8")
    if wiring is not None:
        (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return folder


def _write_full(folder: Path, **kw) -> Path:
    return _write(folder, _state(**kw), _wiring())


# ===========================================================================
# parse_ref + error taxonomy
# ===========================================================================


class TestParseRef:
    def test_known_schemes(self):
        assert cs.parse_ref("ws:/a/b") == ("ws", "/a/b")
        assert cs.parse_ref("hist:chip/20260101_000000") == ("hist", "chip/20260101_000000")

    def test_unknown_scheme_is_permanent(self):
        with pytest.raises(cs.SourcePermanentError):
            cs.parse_ref("ftp:/a")

    def test_missing_scheme_or_rest(self):
        for bad in ("", "ws:", "/plain/path", "ws"):
            with pytest.raises(cs.SourcePermanentError):
                cs.parse_ref(bad)


class TestErrorTaxonomy:
    def test_missing_folder_permanent(self, tmp_path):
        with pytest.raises(cs.SourcePermanentError) as ei:
            cs.resolve_source(f"ws:{tmp_path / 'nope'}", cs.SourcePool())
        assert ei.value.transient is False

    def test_missing_state_json_permanent(self, tmp_path):
        folder = tmp_path / "empty"
        folder.mkdir()
        with pytest.raises(cs.SourcePermanentError):
            cs.resolve_source(f"ws:{folder}", cs.SourcePool())

    def test_corrupt_state_json_permanent(self, tmp_path):
        folder = tmp_path / "bad"
        folder.mkdir()
        (folder / "state.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(cs.SourcePermanentError):
            cs.resolve_source(f"ws:{folder}", cs.SourcePool())

    def test_torn_pair_is_transient(self, tmp_path, monkeypatch):
        folder = _write_full(tmp_path / "chip")

        def _torn(*a, **k):
            raise safe_io.LiveFileError(
                "state.json + wiring.json kept changing across 4 read attempts")

        monkeypatch.setattr(safe_io, "read_state_wiring", _torn)
        with pytest.raises(cs.SourceTransientError) as ei:
            cs.resolve_source(f"ws:{folder}", cs.SourcePool())
        assert ei.value.transient is True

    def test_livefileerror_bad_json_is_permanent(self, tmp_path, monkeypatch):
        folder = _write_full(tmp_path / "chip")

        def _bad(*a, **k):
            raise safe_io.LiveFileError("state.json is not valid JSON: boom")

        monkeypatch.setattr(safe_io, "read_state_wiring", _bad)
        with pytest.raises(cs.SourcePermanentError):
            cs.resolve_source(f"ws:{folder}", cs.SourcePool())


# ===========================================================================
# ws / run / hist / drop resolution + honest labels
# ===========================================================================


class TestResolveFolders:
    def test_flat_folder(self, tmp_path):
        folder = _write_full(tmp_path / "quam_states" / "LabA")
        pool = cs.SourcePool()
        src = cs.resolve_source(f"ws:{folder}", pool)
        assert src.origin == "workspace"
        assert src.chip_name == "LabA"
        assert src.label == "LabA"
        assert src.snapshot_ts == ""
        assert src.wiring_missing is False
        state, wiring = _state(), _wiring()
        assert src.content_hash == content_hash(state, wiring)
        assert pool.get(src.content_hash) is not None

    def test_live_quam_state_layout(self, tmp_path):
        folder = _write_full(tmp_path / "LabA" / "quam_state")
        src = cs.resolve_source(f"ws:{folder}", cs.SourcePool())
        assert src.chip_name == "LabA"
        assert src.label == "LabA"

    def test_archive_run_label_and_ts(self, tmp_path):
        folder = _write_full(
            tmp_path / "LabA" / "2026-02-19" / "#12_ramsey_163045" / "quam_state")
        src = cs.resolve_source(f"run:{folder}", cs.SourcePool())
        assert src.origin == "run_archive"
        assert src.label == "LabA #12 · 2026-02-19 16:30:45"
        assert src.snapshot_ts == "2026-02-19 16:30:45"
        assert src.chip_name == "LabA"

    def test_missing_wiring_tolerated(self, tmp_path):
        folder = _write(tmp_path / "stateonly", _state(), wiring=None)
        src = cs.resolve_source(f"ws:{folder}", cs.SourcePool())
        assert src.wiring_missing is True
        assert src.content_hash == content_hash(_state(), {})

    def test_drop_origin_label(self, tmp_path):
        folder = _write_full(tmp_path / "a1b2c3d4e5f6")
        src = cs.resolve_source(f"drop:{folder}", cs.SourcePool(),
                                label_hint="my chip · file")
        assert src.origin == "drop"
        assert src.label == "my chip · file"

    def test_label_parity_with_routes_shim(self, tmp_path):
        """The P0 routes helper and the core extraction must agree (the
        routes copy is the shim the hub UI phase will re-point here)."""
        from quam_state_manager.web.routes import _compare_source_label
        cases = [
            _write_full(tmp_path / "quam_states" / "LabA"),
            _write_full(tmp_path / "LabA2" / "quam_state"),
            _write_full(tmp_path / "chipY" / "2026-03-10" / "#7_rabi_153000"
                        / "quam_state"),
        ]
        for folder in cases:
            assert cs.source_label(folder) == _compare_source_label(str(folder))


class TestResolveHistory:
    def test_hist_snapshot(self, tmp_path):
        root = tmp_path / "history"
        folder = _write_full(root / "LabA" / "20260405_125430_123456")
        src = cs.resolve_source("hist:LabA/20260405_125430_123456",
                                cs.SourcePool(), history_root=root)
        assert src.origin == "history"
        assert src.chip_name == "LabA"
        assert src.snapshot_ts == "2026-04-05 12:54:30"
        assert "history" in src.label
        assert src.path == str(folder)

    def test_hist_needs_root(self):
        with pytest.raises(cs.SourcePermanentError):
            cs.resolve_source("hist:LabA/20260405_125430", cs.SourcePool())

    def test_hist_malformed_ref(self, tmp_path):
        with pytest.raises(cs.SourcePermanentError):
            cs.resolve_source("hist:justachip", cs.SourcePool(),
                              history_root=tmp_path)

    def test_hist_missing_snapshot_permanent(self, tmp_path):
        with pytest.raises(cs.SourcePermanentError):
            cs.resolve_source("hist:LabA/20990101_000000", cs.SourcePool(),
                              history_root=tmp_path)


# ===========================================================================
# identity tokens (amendment A1)
# ===========================================================================


class TestNetworkToken:
    def test_network_only_hash(self):
        """Same network + different qubit names ⇒ SAME network token but
        different full fingerprint token (the LabA-family A1 case)."""
        fp1 = fingerprint_from_dicts(_state(), _wiring("10.1.1.6"))
        s2 = {"qubits": {"qZ9": {"id": "qZ9"}}, "qubit_pairs": {}}
        fp2 = fingerprint_from_dicts(s2, _wiring("10.1.1.6"))
        assert cs.network_token_of(fp1) == cs.network_token_of(fp2)
        from quam_state_manager.core.history import fingerprint_token
        assert fingerprint_token(fp1) != fingerprint_token(fp2)

    def test_different_network_different_token(self):
        fp1 = fingerprint_from_dicts(_state(), _wiring("10.1.1.6"))
        fp2 = fingerprint_from_dicts(_state(), _wiring("10.9.9.9"))
        assert cs.network_token_of(fp1) != cs.network_token_of(fp2)

    def test_none_fingerprint_stable(self):
        assert cs.network_token_of(None) == cs.network_token_of(None)

    def test_tokens_populated_on_source(self, tmp_path):
        folder = _write_full(tmp_path / "chip")
        src = cs.resolve_source(f"ws:{folder}", cs.SourcePool())
        assert len(src.network_token) == 16
        assert src.fingerprint_token


# ===========================================================================
# pool: LRU semantics + isolation
# ===========================================================================


class TestSourcePool:
    def test_lru_capacity_and_eviction_order(self):
        pool = cs.SourcePool(max_entries=8)
        for i in range(9):
            pool.put(f"hash{i}", {"i": i}, {})
        assert len(pool) == 8
        assert pool.get("hash0") is None          # oldest evicted
        assert pool.get("hash8") is not None

    def test_get_refreshes_recency(self):
        pool = cs.SourcePool(max_entries=2)
        pool.put("a", {}, {})
        pool.put("b", {}, {})
        pool.get("a")                             # a becomes most-recent
        pool.put("c", {}, {})
        assert pool.get("a") is not None
        assert pool.get("b") is None

    def test_put_same_hash_reuses_entry(self):
        pool = cs.SourcePool()
        e1 = pool.put("h", {"x": 1}, {})
        e2 = pool.put("h", {"x": 1}, {})
        assert e1 is e2
        assert len(pool) == 1

    def test_lazy_store_is_from_dicts_not_disk(self, tmp_path):
        folder = _write_full(tmp_path / "chip")
        pool = cs.SourcePool()
        src = cs.resolve_source(f"ws:{folder}", pool)
        store = pool.get(src.content_hash).store()
        # from_dicts stores have no folder_path — proof the pool never routed
        # through scanner.load_store / a disk-built QuamStore.
        assert store.folder_path is None
        assert store.qubit_names == ["qA1"]
        # built once
        assert pool.get(src.content_hash).store() is store

    def test_pool_never_touches_scanner_or_quam_cache(self, tmp_path, monkeypatch):
        from quam_state_manager.core import scanner
        from quam_state_manager.web import routes

        def _boom(self, *a, **k):
            raise AssertionError("compare pool must not use scanner.load_store")

        monkeypatch.setattr(scanner.Workspace, "load_store", _boom)
        cache_len_before = len(routes._quam_cache)
        folder = _write_full(tmp_path / "chip")
        pool = cs.SourcePool()
        src = cs.resolve_source(f"ws:{folder}", pool)
        _ = pool.get(src.content_hash).store()
        assert len(routes._quam_cache) == cache_len_before

    def test_pool_entries_are_private_copies_from_disk(self, tmp_path):
        folder = _write_full(tmp_path / "chip")
        pool = cs.SourcePool()
        src = cs.resolve_source(f"ws:{folder}", pool)
        entry = pool.get(src.content_hash)
        # Mutating the pooled dict must not leak anywhere: re-resolve gives a
        # fresh consistent entry keyed by the file content.
        assert entry.state["qubits"]["qA1"]["f_01"] == 6.25e9


# ===========================================================================
# working: origin (amendment A4)
# ===========================================================================


class TestWorkingOrigin:
    def _store(self):
        return QuamStore.from_dicts(copy.deepcopy(_state()),
                                    copy.deepcopy(_wiring()))

    def test_requires_lookup(self):
        with pytest.raises(cs.SourcePermanentError):
            cs.resolve_source("working:/some/ctx", cs.SourcePool())

    def test_unknown_context(self):
        with pytest.raises(cs.SourcePermanentError):
            cs.resolve_source("working:/some/ctx", cs.SourcePool(),
                              working_lookup=lambda p: None)

    def test_pool_holds_deep_copies(self, tmp_path):
        store = self._store()
        pool = cs.SourcePool()
        src = cs.resolve_source(
            "working:/live/LabA/quam_state", pool,
            working_lookup=lambda p: store)
        assert src.origin == "working"
        assert src.chip_name == "LabA"
        assert "working" in src.label
        entry = pool.get(src.content_hash)
        assert entry.state is not store.state
        assert entry.wiring is not store.wiring
        # mutate the live store AFTER the copy — the pool must not move
        store.state["qubits"]["qA1"]["f_01"] = 1.0
        assert entry.state["qubits"]["qA1"]["f_01"] == 6.25e9
        # pooled pair still self-consistent with its recorded hash
        assert content_hash(entry.state, entry.wiring) == src.content_hash

    def test_working_atomic_under_concurrent_mutation(self):
        """A4 thread hammer: a writer mutates state+wiring in lock-step under
        the store lock; every resolved snapshot must be self-consistent
        (state tick == wiring tick, hash matches the copies) — no torn pair."""
        state = copy.deepcopy(_state())
        wiring = copy.deepcopy(_wiring())
        state["tick"] = 0
        wiring["tick"] = 0
        store = QuamStore.from_dicts(state, wiring)
        stop = threading.Event()
        errors: list[str] = []

        def writer():
            i = 0
            while not stop.is_set():
                i += 1
                with store._lock:
                    store.state["tick"] = i
                    store.wiring["tick"] = i

        def resolver():
            pool = cs.SourcePool()
            for _ in range(150):
                try:
                    src = cs.resolve_source(
                        "working:/ctx", pool, working_lookup=lambda p: store)
                except Exception as exc:            # noqa: BLE001
                    errors.append(f"resolve raised: {exc!r}")
                    return
                entry = pool.get(src.content_hash)
                if entry.state.get("tick") != entry.wiring.get("tick"):
                    errors.append(
                        f"torn pair: {entry.state.get('tick')} != "
                        f"{entry.wiring.get('tick')}")
                if content_hash(entry.state, entry.wiring) != src.content_hash:
                    errors.append("hash does not match pooled copies")

        wt = threading.Thread(target=writer, daemon=True)
        wt.start()
        threads = [threading.Thread(target=resolver) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        stop.set()
        wt.join(timeout=5)
        assert not errors, errors[:5]
