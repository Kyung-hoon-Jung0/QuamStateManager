"""Core layout in a Re-generate: the result carries the same core review as a
Generate result, and a pair channel new in the output rides the core its
control qubit's xy HAS after the merge (the source core is kept)."""
from __future__ import annotations

import copy
import json
import sys

from quam_state_manager.core import config_generator, core_layout, regen_merge, regenerate


# --- pair channel follows the kept control core ------------------------------
def _pair_chip(xy_core, cr_core=None, with_cr=True, xy_keys=("core", "thread")):
    xy = {"opx_output": "#/ports/mw_outputs/con1/1/1"}
    if "core" in xy_keys:
        xy["core"] = xy_core
    if "thread" in xy_keys:
        xy["thread"] = None
    pair = {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2"}
    if with_cr:
        pair["cross_resonance"] = {"opx_output": "#/ports/mw_outputs/con1/1/2",
                                   "core": cr_core, "thread": None}
    return {"qubits": {"q1": {"xy": xy}, "q2": {"xy": {"core": None}}},
            "qubit_pairs": {"q1-2": pair}}


def _merged(old, new):
    """What the merge leaves: the fresh output with the source's xy core kept."""
    m = copy.deepcopy(new)
    m["qubits"]["q1"]["xy"] = copy.deepcopy(old["qubits"]["q1"]["xy"])
    return m


def test_new_cr_takes_the_kept_control_core():
    old = _pair_chip("q1_con1_slot1", with_cr=False)
    new = _pair_chip("q1_core", cr_core="q1_core")
    merged = _merged(old, new)
    assert regen_merge.realign_new_pair_cores(old, new, merged) == ["qubit_pairs.q1-2.cross_resonance"]
    assert merged["qubit_pairs"]["q1-2"]["cross_resonance"]["core"] == "q1_con1_slot1"


def test_new_cr_with_a_null_core_control_gets_its_own_core():
    old = _pair_chip(None, with_cr=False)
    new = _pair_chip("q1_core", cr_core="q1_core")
    merged = _merged(old, new)
    regen_merge.realign_new_pair_cores(old, new, merged)
    assert merged["qubit_pairs"]["q1-2"]["cross_resonance"]["core"] is None


def test_new_cr_follows_a_thread_only_control():
    old = _pair_chip(None, with_cr=False, xy_keys=("thread",))
    old["qubits"]["q1"]["xy"]["thread"] = "t1"
    new = _pair_chip("q1_core", cr_core="q1_core")
    merged = _merged(old, new)
    regen_merge.realign_new_pair_cores(old, new, merged)
    assert merged["qubit_pairs"]["q1-2"]["cross_resonance"]["core"] == "t1"


def test_cr_in_the_source_and_unpinned_cr_are_left_alone():
    old = _pair_chip("old_core", cr_core="cr_own")
    new = _pair_chip("q1_core", cr_core="q1_core")
    merged = _merged(old, new)
    merged["qubit_pairs"]["q1-2"]["cross_resonance"]["core"] = "cr_own"   # merge kept it
    assert regen_merge.realign_new_pair_cores(old, new, merged) == []
    # a fresh CR the layout rule did not put on its control's core stays as built
    old2 = _pair_chip("old_core", with_cr=False)
    new2 = _pair_chip("q1_core", cr_core=None)
    merged2 = _merged(old2, new2)
    assert regen_merge.realign_new_pair_cores(old2, new2, merged2) == []
    assert merged2["qubit_pairs"]["q1-2"]["cross_resonance"]["core"] is None


def test_new_cr_on_a_new_control_keeps_the_fresh_core():
    old = {"qubits": {}, "qubit_pairs": {}}
    new = _pair_chip("q1_core", cr_core="q1_core")
    merged = copy.deepcopy(new)
    assert regen_merge.realign_new_pair_cores(old, new, merged) == []
    assert merged["qubit_pairs"]["q1-2"]["cross_resonance"]["core"] == "q1_core"


def test_renamed_control_uses_the_output_names():
    # the source qubit q9 is renamed to q1 in this re-generate; the merge reads
    # the source under its new name, so every lookup is by the output pointer
    old = _pair_chip("q9_con1_slot1", with_cr=False)
    new = _pair_chip("q1_core", cr_core="q1_core")
    merged = _merged(old, new)
    regen_merge.realign_new_pair_cores(old, new, merged)
    assert merged["qubit_pairs"]["q1-2"]["cross_resonance"]["core"] == "q9_con1_slot1"


def test_realigned_cr_shares_the_control_core_in_the_counter():
    old = _pair_chip("keep", with_cr=False)
    new = _pair_chip("q1_core", cr_core="q1_core")
    merged = _merged(old, new)
    regen_merge.realign_new_pair_cores(old, new, merged)
    merged["ports"] = {"mw_outputs": {"con1": {"1": {"1": {}, "2": {}}}}}
    merged["qubits"]["q1"]["xy"]["intermediate_frequency"] = 1e8
    merged["qubit_pairs"]["q1-2"]["cross_resonance"]["intermediate_frequency"] = 1e8
    del merged["qubits"]["q2"]
    f = core_layout.core_budget(merged)["fems"][0]
    assert f["cores"]["count"] == 1.0


# --- the Re-generate result carries the core review --------------------------
def _over_limit_chip():
    """Five MW qubits, each element on its own core: 10 / 8 on one MW-FEM."""
    state = {"qubits": {}, "ports": {"mw_outputs": {"con1": {"1": {}}},
                                     "mw_inputs": {"con1": {"1": {}}}}}
    for i in range(5):
        for role, n in (("xy", 2 * i + 1), ("resonator", 2 * i + 2)):
            state["ports"]["mw_outputs"]["con1"]["1"][str(n)] = {}
            el = {"core": None, "thread": None, "intermediate_frequency": 1e8,
                  "opx_output": f"#/ports/mw_outputs/con1/1/{n}"}
            if role == "resonator":
                state["ports"]["mw_inputs"]["con1"]["1"][str(i + 1)] = {}
                el["opx_input"] = f"#/ports/mw_inputs/con1/1/{i + 1}"
            state["qubits"].setdefault(f"q{i}", {})[role] = el
    return state, {"wiring": {}}


def _client(tmp_path, monkeypatch, built):
    from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
    from quam_state_manager.web.app import create_app

    manifest = {"ok": True, "cached": False, "error": None, "versions": {},
                "capabilities": {c: {"available": True, "detail": ""} for c in CATALOG_IDS}}
    monkeypatch.setattr(config_generator, "probe_capabilities", lambda *a, **k: manifest)

    def fake_run(py, src, spec, out_dir, timeout=300, **kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        s, w = built
        (out_dir / "state.json").write_text(json.dumps(s))
        (out_dir / "wiring.json").write_text(json.dumps(w))
        return {"ok": True, "status": "ok", "error": None, "merge": None, "result": {}}

    monkeypatch.setattr(regenerate, "run_regenerate", fake_run)
    client = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    client.post("/generate/select-env", json={"python": sys.executable})
    src = tmp_path / "src"
    src.mkdir()
    s, w = built
    (src / "state.json").write_text(json.dumps(s))
    (src / "wiring.json").write_text(json.dumps(w))
    spec = {"network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
            "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}],
                            "opx_plus": [], "octaves": []},
            "qubits": [f"q{i}" for i in range(5)], "qubit_pairs": [], "twpas": [], "lines": []}
    return client, {"spec": spec, "output_path": str(tmp_path / "out"), "source_folder": str(src)}


def test_regenerate_result_carries_the_core_warning(tmp_path, monkeypatch):
    built = _over_limit_chip()
    client, body = _client(tmp_path, monkeypatch, built)
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True, res
    cl = res.get("core_layout")
    assert cl, "the Re-generate result has no core review"
    # one implementation: the same summary a Generate result / Load check computes
    root = dict(built[0])
    root.update(built[1])
    assert cl == json.loads(json.dumps(core_layout.review_summary(root)))
    assert "10 / 8 cores on con1 FEM 1 (MW-FEM)" in json.dumps(cl["findings"])


def test_regenerate_within_limit_shows_the_table_without_a_warning(tmp_path, monkeypatch):
    s, w = _over_limit_chip()
    for q in ("q3", "q4"):
        del s["qubits"][q]
    client, body = _client(tmp_path, monkeypatch, (s, w))
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True
    assert res["core_layout"]["table"] is not None and res["core_layout"]["findings"] == []


def test_cr_in_the_source_on_the_fresh_name_stays():
    # the source CR already sits on a core spelled like the fresh pin name
    old = _pair_chip("other", cr_core="q1_core")
    new = _pair_chip("q1_core", cr_core="q1_core")
    merged = _merged(old, new)
    assert regen_merge.realign_new_pair_cores(old, new, merged) == []
    assert merged["qubit_pairs"]["q1-2"]["cross_resonance"]["core"] == "q1_core"


def test_call_site_realigns_a_new_cr(tmp_path, monkeypatch):
    from pathlib import Path

    def write(folder, state):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "state.json").write_text(json.dumps(state))
        (folder / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))

    write(tmp_path / "old", _pair_chip("kept_core", with_cr=False))

    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        write(Path(out_dir), _pair_chip("q1_core", cr_core="q1_core"))
        return {"ok": True, "status": "ok", "error": None, "result": {}}

    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
    out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1}, tmp_path / "new",
                                    source_probe=lambda *a, **k: {"ok": False})
    assert out["ok"], out
    merged = json.loads((tmp_path / "new" / "state.json").read_text())
    assert merged["qubits"]["q1"]["xy"]["core"] == "kept_core"
    assert merged["qubit_pairs"]["q1-2"]["cross_resonance"]["core"] == "kept_core"


def test_new_cr_follows_the_thread_when_the_kept_core_is_null_or_empty():
    for empty in (None, ""):
        old = _pair_chip(empty, with_cr=False)
        old["qubits"]["q1"]["xy"]["thread"] = "t1"
        new = _pair_chip("q1_core", cr_core="q1_core")
        merged = _merged(old, new)
        regen_merge.realign_new_pair_cores(old, new, merged)
        cr = merged["qubit_pairs"]["q1-2"]["cross_resonance"]
        assert core_layout.effective_core_name(cr)[0] == "t1"
        assert core_layout.effective_core_name(merged["qubits"]["q1"]["xy"])[0] == "t1"


def _split_chip():
    """The over-limit chip with q4 moved into wiring.json: only a deep merge sees all five."""
    s, w = _over_limit_chip()
    q4 = s["qubits"].pop("q4")
    return s, {"wiring": {}, "qubits": {"q4": q4}}


def test_annotate_core_layout_merges_like_load_check():
    from quam_state_manager.core.loader import merge_state_wiring
    s, w = _split_chip()
    out = {}
    config_generator.annotate_core_layout(out, s, w)
    assert out["core_layout"] == core_layout.review_summary(merge_state_wiring(s, w))
    assert "10 / 8 cores on con1 FEM 1 (MW-FEM)" in json.dumps(out["core_layout"]["findings"])


def test_regenerate_result_reads_the_wiring(tmp_path, monkeypatch):
    from quam_state_manager.core.loader import merge_state_wiring
    built = _split_chip()
    client, body = _client(tmp_path, monkeypatch, built)
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True, res
    cl = res["core_layout"]
    assert cl == json.loads(json.dumps(core_layout.review_summary(merge_state_wiring(*built))))
    assert "10 / 8 cores on con1 FEM 1 (MW-FEM)" in json.dumps(cl["findings"])
