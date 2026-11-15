"""Re-generate keeps the source chip's cores.

Every element present in both the source chip and the rebuild keeps its
effective core name (``core``, else ``thread``) and its ``core`` key presence.
Only elements new in the output follow the Generate rules, and the rebuilt spec
starts with shared cores off.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from quam_state_manager.core import regen_merge, regenerate

REPO = Path(__file__).resolve().parents[1]
INFO = "Core names moved from thread to core (the new quam has no thread field)"


# ---------------------------------------------------------------- unit: the step
def _el(**kw):
    return dict(kw)


def test_old_core_key_is_left_alone():
    old = {"qubits": {"q1": {"xy": _el(core="a")}}}
    merged = {"qubits": {"q1": {"xy": _el(core="a")}}}
    assert regen_merge.drop_new_core_keys(old, merged) == ([], [])
    assert merged["qubits"]["q1"]["xy"] == {"core": "a"}


def test_old_null_core_stays_null():
    old = {"qubits": {"q1": {"xy": _el(core=None)}}}
    merged = {"qubits": {"q1": {"xy": _el(core=None)}}}
    regen_merge.drop_new_core_keys(old, merged)
    assert "core" in merged["qubits"]["q1"]["xy"] and merged["qubits"]["q1"]["xy"]["core"] is None


def test_thread_only_element_loses_the_new_core_key():
    old = {"qubits": {"q1": {"xy": _el(thread="t1")}}}
    merged = {"qubits": {"q1": {"xy": _el(thread="t1", core="q1_core")}}}
    assert regen_merge.drop_new_core_keys(old, merged) == (["qubits.q1.xy"], [])
    assert merged["qubits"]["q1"]["xy"] == {"thread": "t1"}


def test_thread_dropped_by_schema_moves_into_core():
    old = {"qubits": {"q1": {"xy": _el(thread="t1")}}}
    merged = {"qubits": {"q1": {"xy": _el(core="q1_core")}}}
    assert regen_merge.drop_new_core_keys(old, merged) == ([], ["qubits.q1.xy"])
    assert merged["qubits"]["q1"]["xy"] == {"core": "t1"}


@pytest.mark.parametrize("old_xy", [{}, {"thread": None}])
def test_no_core_and_no_thread_loses_the_new_core_key(old_xy):
    old = {"qubits": {"q1": {"xy": old_xy}}}
    merged = {"qubits": {"q1": {"xy": {"core": "q1_core"}}}}
    assert regen_merge.drop_new_core_keys(old, merged) == (["qubits.q1.xy"], [])
    assert merged["qubits"]["q1"]["xy"] == {}


def test_elements_new_in_the_output_are_untouched():
    old = {"qubits": {"q1": {"xy": {}}}}
    merged = {"qubits": {"q1": {"xy": {"core": None}}, "q2": {"xy": {"core": "q2_core"}}}}
    regen_merge.drop_new_core_keys(old, merged)
    assert merged["qubits"]["q2"]["xy"] == {"core": "q2_core"}


def test_info_line_matches_the_spec_text():
    spec = Path("D:/work_laptop/study/2026-11-14_qsm-core-layout-spec/decisions.md")
    if not spec.exists():
        pytest.skip("spec not on this machine")
    m = re.search(r'"(Core names moved from thread to core[^"]*): <paths>"',
                  spec.read_text(encoding="utf-8"))
    assert m and m.group(1) == INFO
    assert INFO in (REPO / "quam_state_manager/core/regenerate.py").read_text(encoding="utf-8")


# ------------------------------------------------------- reconstruct_from_folder
def _write(folder, state, wiring):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state))
    (folder / "wiring.json").write_text(json.dumps(wiring))


def test_reconstruct_without_sidecar_sets_shared_cores_off(tmp_path):
    _write(tmp_path / "c", {"qubits": {"q1": {}}}, {"wiring": {}, "network": {}})
    rec = regenerate.reconstruct_from_folder(tmp_path / "c")
    assert not rec.exact
    assert rec.spec["populate"]["options"]["pin_cores"] is False


def test_reconstruct_from_sidecar_holding_true_sets_shared_cores_off(tmp_path, monkeypatch):
    from quam_state_manager.core import regen_spec
    _write(tmp_path / "c", {"qubits": {"q1": {}}}, {"wiring": {}, "network": {}})
    sidecar = {"qubits": ["q1"], "populate": {"options": {"pin_cores": True}}}
    monkeypatch.setattr(regen_spec, "load_spec_sidecar",
                        lambda cand, s, w: copy.deepcopy(sidecar))
    rec = regenerate.reconstruct_from_folder(tmp_path / "c")
    assert rec.exact
    assert rec.spec["populate"]["options"]["pin_cores"] is False


# --------------------------------------------- run_regenerate with a fake build
def _fake_regen(tmp_path, monkeypatch, old_state, fresh, **kw):
    _write(tmp_path / "old", old_state, {"wiring": {}, "network": {}})

    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        _write(Path(out_dir), fresh, {"wiring": {}, "network": {}})
        return {"ok": True, "status": "ok", "error": None, "result": {}}

    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
    out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1}, tmp_path / "new",
                                    source_probe=lambda *a, **k: {"ok": False}, **kw)
    assert out["ok"], out
    return out, json.loads((tmp_path / "new" / "state.json").read_text())


def test_call_site_keeps_thread_only_and_reports_paths(tmp_path, monkeypatch):
    old = {"qubits": {"q1": {"xy": {"thread": "t1"}, "resonator": {}}}}
    fresh = {"qubits": {"q1": {"xy": {"thread": None, "core": "q1_core"},
                               "resonator": {"core": "q1_core"}}}}
    out, merged = _fake_regen(tmp_path, monkeypatch, old, fresh)
    assert "core" not in merged["qubits"]["q1"]["xy"]
    assert merged["qubits"]["q1"]["xy"]["thread"] == "t1"
    assert "core" not in merged["qubits"]["q1"]["resonator"]
    assert sorted(out["merge"]["core_keys_dropped"]) == ["qubits.q1.resonator", "qubits.q1.xy"]
    assert out["merge"]["core_keys_from_thread"] == []
    assert not any(INFO in w for w in (out["result"].get("warnings") or []))


def test_call_site_moves_schema_dropped_thread_and_says_so(tmp_path, monkeypatch):
    old = {"qubits": {"q1": {"xy": {"thread": "t1"}}}}
    fresh = {"qubits": {"q1": {"xy": {"core": "q1_core"}}}}
    schemas_out = {}

    def drop_thread(old_state, new_state, **kw):
        r = real_merge(old_state, new_state, **kw)
        r.merged["qubits"]["q1"]["xy"].pop("thread", None)   # as a schema without thread
        schemas_out["called"] = True
        return r
    real_merge = regen_merge.merge_states
    monkeypatch.setattr(regen_merge, "merge_states", drop_thread)
    out, merged = _fake_regen(tmp_path, monkeypatch, old, fresh)
    assert schemas_out
    assert merged["qubits"]["q1"]["xy"] == {"core": "t1"}
    assert out["merge"]["core_keys_from_thread"] == ["qubits.q1.xy"]
    assert f"{INFO}: qubits.q1.xy" in out["result"]["warnings"]


def test_call_site_uses_the_renamed_source(tmp_path, monkeypatch):
    old = {"qubits": {"q1": {"xy": {"thread": "t1"}}}}
    fresh = {"qubits": {"r1": {"xy": {"thread": None, "core": "r1_core"}}}}
    out, merged = _fake_regen(tmp_path, monkeypatch, old, fresh,
                              qubit_sources={"r1": "q1"})
    assert merged["qubits"]["r1"]["xy"] == {"thread": "t1"}
    assert out["merge"]["core_keys_dropped"] == ["qubits.r1.xy"]


# ------------------------------------------------------------ the wizard checkbox
def test_wizard_checkbox_follows_the_reconstructed_spec_value():
    js = (REPO / "quam_state_manager/web/static/generate.js").read_text(encoding="utf-8")
    # a stored boolean is kept (the reconstructed false shows unchecked) ...
    assert 'if (typeof opt.pin_cores !== "boolean") opt.pin_cores = opt.pin_cores !== false;' in js
    assert "chk.checked = ensurePinCores(state.spec);" in js
    # ... and a loaded spec keeps its own populate (only missing keys get defaults)
    assert "if (spec[k] == null) spec[k] = fresh[k];" in js


# ------------------------------------------------------------ real rebuilds
def _env():
    from tests.test_script_emitter_live import _first_usable_qm_env
    python = _first_usable_qm_env()
    if not python:
        pytest.skip("no usable QM env found")
    return python


def _spec(qs):
    lines = []
    for q in qs:
        lines.append({"element": q, "line": "resonator", "group": "f",
                      "channel": {"kind": "mw_fem"}})
        lines.append({"element": q, "line": "drive", "channel": {"kind": "mw_fem"}})
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}],
                        "opx_plus": [], "octaves": []},
        "qubits": list(qs), "qubit_pairs": [], "twpas": [], "pair_gate": "none",
        "lines": lines,
        "populate": {
            "qubit": {q: {"RF_freq": 5.0e9 + 0.05e9 * i, "LO_frequency": 5.1e9}
                      for i, q in enumerate(qs)},
            "resonator": {q: {"RF_freq": 7.2e9 + 0.02e9 * i, "LO_frequency": 7.35e9}
                          for i, q in enumerate(qs)},
        },
    }


def _cores(state):
    found = {}

    def walk(node, path):
        if isinstance(node, dict):
            if "core" in node:
                found[path] = node["core"]
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
    walk(state, "")
    return found


@pytest.fixture(scope="module")
def built_source(tmp_path_factory):
    from quam_state_manager.core.config_generator import run_generator
    python = _env()
    out = tmp_path_factory.mktemp("src") / "chip"
    outcome = run_generator(python, "build", _spec(["q1", "q2"]), out, timeout=600)
    assert outcome.get("ok"), outcome
    return python, out


def _source_copy(built_source, tmp_path, edit=None):
    python, src = built_source
    state = json.loads((src / "state.json").read_text(encoding="utf-8"))
    wiring = json.loads((src / "wiring.json").read_text(encoding="utf-8"))
    if edit:
        edit(state)
    _write(tmp_path / "old", state, wiring)
    return python, tmp_path / "old", state


def _regen(python, folder, tmp_path, spec=None, **kw):
    if spec is None:
        spec = regenerate.reconstruct_from_folder(folder).spec
    out = regenerate.run_regenerate(python, folder, spec, tmp_path / "new", timeout=600, **kw)
    assert out.get("ok"), out
    return out, json.loads((tmp_path / "new" / "state.json").read_text(encoding="utf-8"))


def test_real_unchanged_rebuild_keeps_every_core(built_source, tmp_path):
    python, folder, state = _source_copy(built_source, tmp_path)
    before = _cores(state)
    assert before["qubits.q1.xy"] == "q1_core"
    _, merged = _regen(python, folder, tmp_path)
    assert _cores(merged) == before


def test_real_null_cores_stay_null(built_source, tmp_path):
    def nulls(st):
        for q in st["qubits"].values():
            q["xy"]["core"] = None
            q["resonator"]["core"] = None
    python, folder, state = _source_copy(built_source, tmp_path, nulls)
    _, merged = _regen(python, folder, tmp_path)
    for q in ("q1", "q2"):
        for el in ("xy", "resonator"):
            assert "core" in merged["qubits"][q][el]
            assert merged["qubits"][q][el]["core"] is None


def test_real_hand_names_and_thread_only_elements(built_source, tmp_path):
    def hand(st):
        st["qubits"]["q1"]["xy"]["core"] = "a"
        st["qubits"]["q2"]["xy"]["core"] = "a"
        r1 = st["qubits"]["q1"]["resonator"]
        r1.pop("core", None)
        r1["thread"] = "t1"
        r2 = st["qubits"]["q2"]["resonator"]
        r2.pop("core", None)
        r2.pop("thread", None)
    python, folder, state = _source_copy(built_source, tmp_path, hand)
    out, merged = _regen(python, folder, tmp_path)
    assert merged["qubits"]["q1"]["xy"]["core"] == "a"
    assert merged["qubits"]["q2"]["xy"]["core"] == "a"
    assert "core" not in merged["qubits"]["q1"]["resonator"]
    assert merged["qubits"]["q1"]["resonator"]["thread"] == "t1"
    assert "core" not in merged["qubits"]["q2"]["resonator"]
    assert not merged["qubits"]["q2"]["resonator"].get("thread")
    assert "qubits.q1.resonator" in out["merge"]["core_keys_dropped"]
    assert "qubits.q2.resonator" in out["merge"]["core_keys_dropped"]


def test_real_rename_keeps_cores_under_the_new_name(built_source, tmp_path):
    def hand(st):
        st["qubits"]["q1"]["xy"]["core"] = "a"
        st["qubits"]["q1"]["resonator"].pop("core", None)
        st["qubits"]["q1"]["resonator"]["thread"] = "t1"
    python, folder, state = _source_copy(built_source, tmp_path, hand)
    spec = json.loads(json.dumps(regenerate.reconstruct_from_folder(folder).spec)
                      .replace('"q1"', '"qr1"'))
    _, merged = _regen(python, folder, tmp_path, spec=spec, qubit_sources={"qr1": "q1"})
    assert "qr1" in merged["qubits"]
    assert merged["qubits"]["qr1"]["xy"]["core"] == "a"
    assert "core" not in merged["qubits"]["qr1"]["resonator"]
    assert merged["qubits"]["qr1"]["resonator"]["thread"] == "t1"


def _with_new_qubit(spec, pin):
    base = _spec(["q1", "q2", "q3"])
    spec = copy.deepcopy(spec)
    spec["qubits"] = list(spec["qubits"]) + ["q3"]
    # the new qubit rides the source feedline and the next free drive port
    spec["lines"] = list(spec["lines"]) + [
        {"element": "q3", "line": "resonator", "group": "feedline1",
         "channel": {"kind": "mw_fem", "con": 1, "slot": 1, "in_port": 1, "out_port": 1}},
        {"element": "q3", "line": "drive",
         "channel": {"kind": "mw_fem", "con": 1, "slot": 1, "out_port": 4}}]
    for grp in ("qubit", "resonator"):
        spec["populate"].setdefault(grp, {})["q3"] = base["populate"][grp]["q3"]
    spec["populate"]["options"]["pin_cores"] = pin
    return spec


@pytest.mark.parametrize("pin", [True, False])
def test_real_new_qubit_follows_the_checkbox(built_source, tmp_path, pin):
    def hand(st):
        st["qubits"]["q1"]["xy"]["core"] = None
        st["qubits"]["q2"]["resonator"].pop("core", None)
    python, folder, state = _source_copy(built_source, tmp_path, hand)
    before = _cores(state)
    spec = _with_new_qubit(regenerate.reconstruct_from_folder(folder).spec, pin)
    _, merged = _regen(python, folder, tmp_path, spec=spec)
    after = _cores(merged)
    assert {p: v for p, v in after.items() if not p.startswith("qubits.q3")} == before
    want = "q3_core" if pin else None
    assert merged["qubits"]["q3"]["xy"]["core"] == want
    assert merged["qubits"]["q3"]["resonator"]["core"] == want
