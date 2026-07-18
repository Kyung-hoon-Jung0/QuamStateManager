"""Tests for core/state_env_schema.py — harvest, version-keyed cache with
superset hits, the cached_only no-subprocess rule, LRU prune, and the
missing-interpreter pre-flight. The child subprocess is monkeypatched
throughout (the real script is covered by test_probe_state_schema.py and the
live env test)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import state_env_schema as ses
from quam_state_manager.core.loader import QuamStore


# ---------------------------------------------------------------------------
# harvest
# ---------------------------------------------------------------------------

def _corpus_shaped_state() -> dict:
    return {
        "__class__": "quam_config.my_quam.Quam",
        "qubits": {"qA1": {
            "__class__": "quam.FluxTunableTransmon",
            "xy": {"__class__": "quam.XYDriveMW",
                   "operations": {"x180": {"__class__": "quam.components.pulses.DragCosinePulse"}}},
        }},
        "qubit_pairs": {"p": {
            "__class__": "qb.FluxTunableTransmonPair",
            "macros": {"cz": {"__class__": "qb.CZGate"},
                       "cz2": {"__class__": "qb.CZGate"}},   # dup — dedup'd
        }},
        "__package_versions__": {"quam": "0.6.0", "quam_builder": "0.4.0"},
    }


class TestHarvest:
    def test_root_first_dedup(self):
        got = ses.harvest_classes(_corpus_shaped_state())
        assert got[0] == "quam_config.my_quam.Quam"
        assert len(got) == len(set(got))
        assert "qb.CZGate" in got and got.count("qb.CZGate") == 1

    def test_cap(self):
        state = {"items": {str(i): {"__class__": f"m.C{i}"} for i in range(400)}}
        assert len(ses.harvest_classes(state, cap=50)) == 50

    def test_package_versions_stamp(self):
        assert ses.package_versions_stamp(_corpus_shaped_state()) == {
            "quam": "0.6.0", "quam_builder": "0.4.0"}
        assert ses.package_versions_stamp({}) is None


# ---------------------------------------------------------------------------
# probe wrapper + cache
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """A fake interpreter file + monkeypatched versions/signature/subprocess."""
    py = tmp_path / "python"
    py.write_text("", encoding="utf-8")
    inst = tmp_path / "instance"
    inst.mkdir()

    calls = {"probe": 0}
    versions = {"quam": "0.6.0", "quam_builder": "0.4.0", "quam_builder_commit": "abc"}
    signature = ["sig-1"]

    monkeypatch.setattr(ses, "_env_versions", lambda p: dict(versions))
    monkeypatch.setattr(ses, "_env_signature", lambda p: signature[0])

    def fake_run(argv, work_dir, timeout, outcome, **kw):
        calls["probe"] += 1
        spec = json.loads((work_dir / "_classes.json").read_text(encoding="utf-8"))
        outcome["ok"] = True
        outcome["result"] = {
            "status": "ok", "versions": dict(versions),
            "classes": {c: {"importable": not c.startswith("otherlab"),
                            "canonical": c, "bases": [], "is_dataclass": True,
                            "fields": {}, "error": None}
                        for c in spec["classes"]},
            "pulse_roster": {"SquarePulse": {"homes": ["quam.components.pulses"]}},
        }

    monkeypatch.setattr(ses, "_run_script_outcome", fake_run)
    return {"py": str(py), "inst": str(inst), "calls": calls,
            "versions": versions, "signature": signature}


class TestProbeCache:
    def test_probe_then_superset_hit(self, fake_env):
        r1 = ses.probe_state_schema(fake_env["py"], ["a.B", "c.D"], fake_env["inst"])
        assert r1["ok"] and not r1["cached"] and fake_env["calls"]["probe"] == 1
        # subset request → cache hit, no new probe
        r2 = ses.probe_state_schema(fake_env["py"], ["a.B"], fake_env["inst"])
        assert r2["ok"] and r2["cached"] and fake_env["calls"]["probe"] == 1
        # new class → union probe
        r3 = ses.probe_state_schema(fake_env["py"], ["e.F"], fake_env["inst"])
        assert r3["ok"] and not r3["cached"] and fake_env["calls"]["probe"] == 2
        assert set(r3["classes"]) == {"a.B", "c.D", "e.F"}   # monotone union

    def test_version_flip_misses(self, fake_env):
        ses.probe_state_schema(fake_env["py"], ["a.B"], fake_env["inst"])
        fake_env["versions"]["quam"] = "0.7.0"
        r = ses.probe_state_schema(fake_env["py"], ["a.B"], fake_env["inst"])
        assert not r["cached"] and fake_env["calls"]["probe"] == 2

    def test_missing_interpreter_preflight(self, fake_env):
        r = ses.probe_state_schema(str(Path(fake_env["py"]) / "gone"), ["a.B"],
                                   fake_env["inst"])
        assert not r["ok"] and "reselect" in r["error"]
        assert fake_env["calls"]["probe"] == 0

    def test_missing_classes_derived(self, fake_env):
        r = ses.probe_state_schema(
            fake_env["py"], ["a.B", "otherlab_tools.X"], fake_env["inst"])
        assert r["missing_classes"] == ["otherlab_tools.X"]
        assert r["by_leaf"].get("B") == ["a.B"]

    def test_lru_prune(self, fake_env, tmp_path, monkeypatch):
        for i in range(ses._MAX_CACHED_ENVS + 2):
            py = tmp_path / f"py{i}"
            py.write_text("", encoding="utf-8")
            ses.probe_state_schema(str(py), ["a.B"], fake_env["inst"])
        cache = ses._load_cache(fake_env["inst"])
        assert len(cache) == ses._MAX_CACHED_ENVS


class TestManifestForStore:
    def _store(self):
        return QuamStore.from_dicts(
            {"__class__": "a.B", "qubits": {"q": {"__class__": "c.D", "f_01": 1.0}}},
            {"wiring": {}})

    def test_cached_only_cold_returns_none_and_never_probes(self, fake_env):
        store = self._store()
        assert ses.manifest_for_store(store, fake_env["py"], fake_env["inst"],
                                      cached_only=True) is None
        assert fake_env["calls"]["probe"] == 0

    def test_cached_only_warm_after_probe(self, fake_env):
        store = self._store()
        ses.probe_state_schema(fake_env["py"], ["a.B", "c.D"], fake_env["inst"])
        m = ses.manifest_for_store(store, fake_env["py"], fake_env["inst"],
                                   cached_only=True)
        assert m is not None and set(m["classes"]) == {"a.B", "c.D"}
        assert m["by_leaf"]["D"] == ["c.D"]
        assert fake_env["calls"]["probe"] == 1     # cached_only spawned nothing

    def test_cached_only_goes_cold_on_signature_flip(self, fake_env):
        store = self._store()
        ses.probe_state_schema(fake_env["py"], ["a.B", "c.D"], fake_env["inst"])
        fake_env["signature"][0] = "sig-2"          # pip install happened
        assert ses.manifest_for_store(store, fake_env["py"], fake_env["inst"],
                                      cached_only=True) is None

    def test_no_env_returns_none(self, fake_env):
        assert ses.manifest_for_store(self._store(), None, fake_env["inst"]) is None
        assert ses.manifest_for_store(self._store(), fake_env["py"], None) is None
