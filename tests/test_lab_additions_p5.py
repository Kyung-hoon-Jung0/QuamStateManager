"""Lab additions, phase 5: one convention for a pair's 2Q gates (the default
alias), the editable recipe reproducing the additions, and the Re-generate
round trip (sidecar keeps them; additions win on their paths; a lab chip's own
objects are never duplicated as additions).

Real builds are skipped unless their interpreter is given (see
``tests/test_lab_additions_p4.py``): ``QSM_OFFICIAL_PYTHON`` (else a conda env
named ``qsm_official``), ``QSM_LAB_ENV_PYTHON``, ``QSM_LAB_TREE_PYTHON`` +
``QSM_LAB_TREE_PYTHONPATH``, ``QSM_QDAC_PYTHON``.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import (additions, config_generator, regen_script, regen_spec,
                                     regenerate, role_manifest, script_emitter)
from quam_state_manager.core.pointer_path import _walk

P = "qubit_pairs.q1-2.macros"
CZ = "quam_builder.architecture.superconducting.custom_gates.flux_tunable_transmon_pair.two_qubit_gates.CZGate"
FLAT = "quam.components.pulses.FlatTopGaussianPulse"


def _spec(adds, gate="cz_tunable", variant=None):
    s = {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "pair_gate": gate,
         "lines": [{"element": q, "line": ln} for q in ("q1", "q2") for ln in ("drive", "flux", "resonator")],
         "additions": adds}
    if variant:
        s["populate"] = {"pairs": {"q1-q2": {"cz_variant": variant}}}
    return s


def _macro(name, pulse="#/qubits/q1/z/operations/p1"):
    return {"kind": "macro", "target": P, "name": name, "class": CZ, "fields": {"flux_pulse_qubit": pulse}}


def _default(to, name="cz"):
    return {"kind": "default_gate", "target": P, "name": name, "to": to}


def _state(macros=None):
    q = lambda: {"z": {"operations": {"p1": {"__class__": FLAT, "length": 40}}}, "xy": {"operations": {}}}
    return {"__class__": "x.Root", "qubits": {"q1": q(), "q2": q()},
            "qubit_pairs": {"q1-2": {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                                     "macros": macros if macros is not None else
                                     {"cz_unipolar": {"__class__": CZ, "flux_pulse_qubit": "#/qubits/q1/z/operations/p1"}}}}}


# ---------------------------------------------------------------------------
# one list of gates per pair
# ---------------------------------------------------------------------------

def test_builder_gate_names_follow_the_pair_gate_and_variant():
    assert additions.builder_gate_names(_spec([]), ["q1", "q2"]) == [
        "cz_unipolar", "cz_flattop", "cz_bipolar", "cz_SNZ", "cz_flattop_erf"]
    assert additions.builder_gate_names(_spec([], variant="SNZ"), ["q1", "q2"]) == ["cz_SNZ"]
    assert additions.builder_gate_names(_spec([], gate="cz_coupler"), ["q1", "q2"]) == ["cz_coupler", "cz"]
    assert additions.builder_gate_names(_spec([], gate="cr"), ["q1", "q2"]) == ["cr"]
    assert additions.builder_gate_names(_spec([], gate=""), ["q1", "q2"]) == []


def test_the_builder_variants_mirror_run_build():
    from quam_state_manager.core.script_emitter import _run_build
    assert tuple(_run_build()._CZ_VARIANTS) == additions._CZ_VARIANTS


def test_an_added_gate_may_not_take_a_builder_name():
    errs = additions.structural_errors(_spec([_macro("cz_unipolar")]))
    assert any("already makes a gate named cz_unipolar" in e for e in errs)
    assert not additions.structural_errors(_spec([_macro("cz_unipolar")], variant="SNZ"))


def test_an_added_gate_may_not_take_the_default_alias_name():
    errs = additions.structural_errors(_spec([_macro("cz"), _default("cz_unipolar")]))
    assert any("default-gate alias" in e for e in errs)


def test_a_default_must_name_one_of_the_pairs_gates():
    assert any("not one of this pair's gates" in e
               for e in additions.structural_errors(_spec([_default("cz_nope")])))
    assert not additions.structural_errors(_spec([_default("cz_flattop")]))
    assert not additions.structural_errors(_spec([_macro("cz_mine"), _default("cz_mine")]))


def test_one_default_per_pair_and_its_shape():
    errs = additions.structural_errors(_spec([_default("cz_flattop"), _default("cz_bipolar")]))
    assert any("two default gates" in e for e in errs)
    errs = additions.structural_errors(_spec([{"kind": "default_gate", "target": "qubits.q1.macros",
                                               "name": "cz", "to": "x"}]))
    assert any("qubit_pairs.<pair>.macros" in e for e in errs)
    errs = additions.structural_errors(_spec([_default("cz", name="cz")]))
    assert any("'to' must name another gate" in e for e in errs)


def test_pair_gate_table_marks_the_default():
    t = additions.pair_gate_table(_spec([_macro("cz_mine"), _default("cz_mine")]))["q1-2"]
    assert t["added"] == ["cz_mine"] and t["default"] == {"name": "cz", "to": "cz_mine", "by": "chosen"}
    t = additions.pair_gate_table(_spec([], gate="cz_coupler"))["q1-2"]
    assert t["default"] == {"name": "cz", "to": "cz_coupler", "by": "builder"}
    assert additions.pair_gate_table(_spec([]))["q1-2"]["default"] is None


def test_the_default_alias_is_written_after_the_macros():
    st = _state()
    rep = additions.apply_additions(st, _spec([_default("cz_mine"), _macro("cz_mine")]), None)
    assert [e["kind"] for e in sorted(rep["added"], key=lambda e: e["index"])] == ["default_gate", "macro"]
    m = st["qubit_pairs"]["q1-2"]["macros"]
    assert m["cz"] == "#./cz_mine" and list(m) == ["cz_unipolar", "cz_mine", "cz"]


def test_the_default_replaces_only_an_alias():
    st = _state({"cz_coupler": {"__class__": CZ}, "cz": "#./cz_coupler", "cz_mine": {"__class__": CZ}})
    rep = additions.apply_additions(st, _spec([_default("cz_mine")], gate="cz_coupler"), None)
    assert rep["added"] and st["qubit_pairs"]["q1-2"]["macros"]["cz"] == "#./cz_mine"
    st = _state({"cz": {"__class__": CZ}, "cz_unipolar": {"__class__": CZ}})
    rep = additions.apply_additions(st, _spec([_default("cz_unipolar")]), None)
    assert "never replaces a gate" in rep["refused"][0]["reason"]
    assert isinstance(st["qubit_pairs"]["q1-2"]["macros"]["cz"], dict)


def test_a_default_on_a_missing_or_refused_gate_is_refused():
    st = _state()
    rep = additions.apply_additions(st, _spec([_default("cz_flattop")]), None)
    assert "no gate named cz_flattop" in rep["refused"][0]["reason"]
    st = _state()
    bad = _macro("cz_mine", pulse="#/qubits/q1/z/operations/nope")
    rep = additions.apply_additions(st, _spec([bad, _default("cz_mine")]), None)
    why = {e["kind"]: e["reason"] for e in rep["refused"]}
    assert "points at nothing" in why["macro"]
    assert "an addition that was refused" in why["default_gate"]
    assert "cz" not in st["qubit_pairs"]["q1-2"]["macros"]


def test_the_env_round_refuses_the_alias_of_a_refused_gate(tmp_path):
    out = tmp_path / "chip"
    out.mkdir()
    (out / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (out / "wiring.json").write_text("{}", encoding="utf-8")

    def verify(py, folders, timeout=0):
        res = {}
        for k, f in folders.items():
            st = json.loads((Path(f["folder"]) / "state.json").read_text(encoding="utf-8"))
            bad = "cz_mine" in st["qubit_pairs"]["q1-2"]["macros"]
            res[k] = {"ok": not bad, "error": "the lab code raised" if bad else None}
        return {"ok": True, "folders": res}
    rep = additions.apply_and_verify("py", out, _spec([_macro("cz_mine"), _default("cz_mine")]), None,
                                     verify=verify)
    why = {e["kind"]: e["reason"] for e in rep["refused"]}
    assert "the lab code raised" in why["macro"] and "was refused" in why["default_gate"]
    assert "cz" not in json.loads((out / "state.json").read_text(encoding="utf-8"))["qubit_pairs"]["q1-2"]["macros"]


def test_a_default_on_a_builder_gate_passes_the_env_round(tmp_path):
    out = tmp_path / "chip"
    out.mkdir()
    (out / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (out / "wiring.json").write_text("{}", encoding="utf-8")
    seen = []

    def verify(py, folders, timeout=0):
        seen.append(sorted(folders))
        return {"ok": True, "folders": {k: {"ok": True} for k in folders}}
    rep = additions.apply_and_verify("py", out, _spec([_default("cz_unipolar")]), None, verify=verify)
    assert [e["path"] for e in rep["added"]] == [f"{P}.cz"] and rep["verified"]["own_code"] == []
    assert json.loads((out / "state.json").read_text(encoding="utf-8"))["qubit_pairs"]["q1-2"]["macros"]["cz"] \
        == "#./cz_unipolar"


# ---------------------------------------------------------------------------
# the recipe
# ---------------------------------------------------------------------------

def _recipe_adds():
    return [
        _default("cz_mine"),          # listed first, placed after the macros
        {"kind": "pulse", "target": "qubits.q2.z.operations", "name": "p2", "class": FLAT,
         "fields": {"length": 48, "amplitude": -0.05, "flat_length": 32}},
        _macro("cz_mine", "#/qubits/q2/z/operations/p2"),
        {"kind": "field", "target": "extras.mode", "value": True},
        {"kind": "macro", "target": "qubits.q1.macros", "name": "m1", "class": CZ, "fields": {}},
    ]


def _exec_recipe(adds, state, tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state), encoding="utf-8")
    ns = {"json": json, "Path": Path}
    exec(additions.recipe_block(adds), ns)
    ns["_apply_additions"](p)
    return p.read_text(encoding="utf-8")


def test_the_recipe_block_writes_what_the_wizard_pass_writes(tmp_path):
    adds = _recipe_adds()
    wiz = _state()
    rep = additions.apply_additions(wiz, _spec(adds), None)
    assert len(rep["added"]) == 5, rep["refused"]
    text = _exec_recipe(additions.kept_additions(_spec(adds), rep), _state(), tmp_path)
    assert text == json.dumps(wiz, indent=4)        # same keys, same order, same bytes


def test_precheck_lists_the_default_after_the_macros():
    kinds = [e["kind"] for e in additions.precheck(_spec(_recipe_adds()), None)]
    assert kinds == ["pulse", "macro", "macro", "default_gate", "field"]


def test_the_recipe_keeps_only_what_the_build_kept(tmp_path):
    adds = _recipe_adds()
    rep = {"added": [{"index": 1}, {"index": 3}]}
    kept = additions.kept_additions(_spec(adds), rep)
    assert [a.get("name") or a["target"] for a in kept] == ["p2", "extras.mode"]
    stamped = additions.stamp(_spec(adds), None, "py")
    assert "env" not in additions.kept_additions({"additions": stamped}, None)[0]


def test_both_emitters_carry_the_additions():
    from tests.test_script_emitter_live import _spec as live
    spec = live()
    spec["additions"] = _recipe_adds()
    rep = {"added": [{"index": i} for i in range(5)]}
    b = script_emitter.emit_bundle(spec, {}, {}, "c", stamp="2026-01-01", additions_report=rep)
    src = b["02_build_machine.py"]
    assert "ADDITIONS = " in src and '_apply_additions(Path(STATE_DIR) / "state.json")' in src
    assert src.index("_link_input_downconverters_to_outputs(\n") < src.rindex("_apply_additions(Path(")
    compile(src, "02", "exec")
    none = script_emitter.emit_bundle(live(), {}, {}, "c", stamp="2026-01-01")
    assert "ADDITIONS" not in none["02_build_machine.py"]
    rs = dict(spec, lines=[ln for ln in spec["lines"] if ln.get("channel")])
    r = regen_script.emit_build_script(rs, "c", additions_report=rep)
    assert "ADDITIONS = " in r and "_apply_additions(" in r
    compile(r, "r", "exec")
    assert "ADDITIONS" not in regen_script.emit_build_script(dict(rs, additions=None), "c")


# ---------------------------------------------------------------------------
# re-generate: sidecar, authority on their paths, env change
# ---------------------------------------------------------------------------

def test_restore_paths_puts_the_addition_back():
    built = _state({"cz_mine": {"__class__": CZ, "flux_pulse_qubit": "#/a"}, "cz": "#./cz_mine"})
    merged = _state({"cz_mine": {"__class__": "lab.Other", "flux_pulse_qubit": "#/old", "extra": 1}})
    ch = additions.restore_paths(merged, built, [f"{P}.cz_mine", f"{P}.cz", "nope.x"])
    assert ch == [f"{P}.cz_mine", f"{P}.cz"]
    assert merged["qubit_pairs"]["q1-2"]["macros"] == {"cz_mine": {"__class__": CZ, "flux_pulse_qubit": "#/a"},
                                                       "cz": "#./cz_mine"}


def test_env_change_notes():
    man = {"versions": {"quam_builder": "0.6.0"}, "classes": {CZ: {"fields": {"a": {"raw": "int"}}}}}
    a = _macro("cz_mine")
    a.update(env={"python": "old.exe", "versions": {"quam_builder": "0.4.0"}}, schema_hash="0000")
    notes = additions.env_change_notes({"additions": [a, copy.deepcopy(a)]}, man, "new.exe")
    assert len(notes) == 3
    assert "another environment" in notes[0] and "0.4.0 when" in notes[1] and "schema 0000 ->" in notes[2]
    same = additions.stamp({"additions": [_macro("cz_mine")]}, man, "new.exe")
    assert additions.env_change_notes({"additions": same}, man, "new.exe") == []


def _fake_build(state, wiring):
    def run_generator(py, mode, spec, out, timeout=0):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (out / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        return {"ok": True, "result": {}}
    return run_generator


def _ok_verify(py, folders, timeout=0):
    return {"ok": True, "folders": {k: {"ok": True} for k in folders}}


def _no_probe(py, classes, ip):
    return {"ok": False, "classes": {}}


def test_regenerate_additions_win_and_are_never_duplicated(tmp_path, monkeypatch):
    wiring = {"wiring": {}, "network": {}}
    fresh = _state()                                  # what the builder writes
    src = tmp_path / "src"
    src.mkdir()
    mine = _macro("cz_mine")
    mine["fields"]["phase_shift_control"] = 0.0
    adds = [mine, _default("cz_mine")]
    spec = _spec(adds)
    first = _state()
    additions.apply_additions(first, spec, None)
    first["qubit_pairs"]["q1-2"]["macros"]["cz_mine"]["phase_shift_control"] = 0.3
    first["qubit_pairs"]["q1-2"]["macros"]["cz_lab"] = {"__class__": "lab.gates.Mine"}
    (src / "state.json").write_text(json.dumps(first), encoding="utf-8")
    (src / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    monkeypatch.setattr(config_generator, "run_generator", _fake_build(fresh, wiring))
    monkeypatch.setattr(config_generator, "run_additions_verify", _ok_verify)
    out = tmp_path / "out"
    o = regenerate.run_regenerate("py", src, spec, out, scripts_enabled=False, source_probe=_no_probe,
                                  additions_probe=lambda *a, **k: {"manifest": None})
    assert o["ok"] and [e["name"] for e in o["additions"]["added"]] == ["cz_mine", "cz"]
    # the addition's own value won over the carried one; nothing doubled
    assert o["additions"]["merge_skipped"] == [f"{P}.cz_mine"]
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    m = st["qubit_pairs"]["q1-2"]["macros"]
    assert m["cz_mine"]["phase_shift_control"] == 0.0 and m["cz"] == "#./cz_mine"
    assert sorted(m) == ["cz", "cz_lab", "cz_mine", "cz_unipolar"]   # the lab gate grafted once
    side = json.loads((out / ".regen" / "generate_spec.json").read_text(encoding="utf-8"))
    assert [a["name"] for a in side["spec"]["additions"]] == ["cz_mine", "cz"]
    assert all("env" in a and "schema_hash" in a for a in side["spec"]["additions"])
    rec = regenerate.reconstruct_from_folder(out)
    assert rec.exact and [a["name"] for a in rec.spec["additions"]] == ["cz_mine", "cz"]


def test_regenerate_a_lab_chip_keeps_its_objects_without_additions(tmp_path, monkeypatch):
    wiring = {"wiring": {}, "network": {}}
    src = tmp_path / "src"
    src.mkdir()
    lab = _state()
    lab["qubit_pairs"]["q1-2"]["macros"]["cz_lab"] = {"__class__": "lab.gates.Mine", "flux_pulse_qubit": "#/x"}
    lab["qubits"]["q1"]["z"]["operations"]["lab_p"] = {"__class__": "lab.pulses.P", "length": 8}
    (src / "state.json").write_text(json.dumps(lab), encoding="utf-8")
    (src / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    rec = regenerate.reconstruct_from_folder(src)
    assert not rec.spec.get("additions")             # never reverse-translated
    monkeypatch.setattr(config_generator, "run_generator", _fake_build(_state(), wiring))
    out = tmp_path / "out"
    o = regenerate.run_regenerate("py", src, _spec(None), out, scripts_enabled=False, source_probe=_no_probe)
    assert "additions" not in o
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert st["qubit_pairs"]["q1-2"]["macros"]["cz_lab"] == lab["qubit_pairs"]["q1-2"]["macros"]["cz_lab"]
    assert st["qubits"]["q1"]["z"]["operations"]["lab_p"] == lab["qubits"]["q1"]["z"]["operations"]["lab_p"]


def test_wizard_selfcheck():
    import shutil
    if not shutil.which("node"):
        pytest.skip("node not installed")
    p = subprocess.run(["node", str(Path(__file__).with_name("gen_additions_p5_selfcheck.cjs"))],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode == 2 and "jsdom not installed" in p.stderr:
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, p.stdout + p.stderr
    assert " 0 failed" in p.stdout


# ---------------------------------------------------------------------------
# real: wizard build vs exported script, and a re-generate round trip
# ---------------------------------------------------------------------------

def _subtree(st, path):
    ok, v = _walk(st, path.split("."))
    return v if ok else KeyError(path)


def _wizard_vs_script(tmp_path, py, spec, modules=(), whole=True):
    """Build + additions the wizard's way (A), then the exported 01/02 (B);
    every kept addition subtree must be equal, and 03 must pass on B."""
    out_a = tmp_path / "a"
    o = config_generator.run_generator(py, "build", spec, out_a, timeout=900)
    assert o.get("ok"), o
    classes = sorted({a["class"] for a in spec["additions"] if a.get("class")})
    man = role_manifest.probe_roles(py, None, state_classes=classes, modules=list(modules))["manifest"]
    rep = additions.apply_and_verify(py, out_a, spec, man)
    res = o.get("result") or {}
    bundle = script_emitter.emit_bundle(spec, res.get("allocation") or {}, res.get("versions") or {},
                                        "fidelity", stamp="2026-01-01", additions_report=rep)
    sdir = tmp_path / "scripts"
    script_emitter.write_bundle(sdir, bundle)
    out_b = tmp_path / "b"
    out_b.mkdir()
    for script in ("01_make_wiring.py", "02_build_machine.py", "03_generate_config.py"):
        r = subprocess.run([py, str(sdir / script), str(out_b)], capture_output=True, text=True,
                           encoding="utf-8", cwd=str(sdir), timeout=900)
        assert r.returncode == 0, f"{script}: {r.stdout}\n{r.stderr}"
    a = json.loads((out_a / "state.json").read_text(encoding="utf-8"))
    b = json.loads((out_b / "state.json").read_text(encoding="utf-8"))
    for e in rep["added"]:
        assert _subtree(a, e["path"]) == _subtree(b, e["path"]), e["path"]
    if whole:
        assert a == b
    (tmp_path / "report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return rep, man, out_a, a


def _official_adds(spec_pair_gate_ok=True):
    return [
        {"kind": "pulse", "target": "qubits.q1.z.operations", "name": "cz_mine_pulse", "class": FLAT,
         "fields": {"length": 48, "amplitude": 0.08, "flat_length": 32}},
        _macro("cz_mine", "#/qubits/q1/z/operations/cz_mine_pulse"),
        _default("cz_mine"),
        {"kind": "field", "target": "extras.recipe_check", "value": True},
    ]


def test_real_official_wizard_vs_script_and_regenerate(tmp_path):
    from tests.test_lab_additions_p2 import _env, _real_spec
    py = _env("QSM_OFFICIAL_PYTHON", "qsm_official")
    if not py:
        pytest.skip("environment absent")
    spec = _real_spec()
    spec["additions"] = _official_adds()
    assert not additions.structural_errors(spec)
    rep, man, out_a, a = _wizard_vs_script(tmp_path, py, spec)
    assert [e["name"] for e in rep["added"]] == ["cz_mine_pulse", "cz_mine", "cz", "recipe_check"], rep["refused"]
    assert a["qubit_pairs"]["q1-2"]["macros"]["cz"] == "#./cz_mine"
    # the CZ plan reads the chosen default
    from quam_state_manager.core import cz_gate_plan as czp
    rows = czp.operations(a)[0]
    assert any(r["operation"] == "cz" or r["path"].endswith(".cz_mine") for r in rows)
    # sidecar, then re-generate from that chip
    st, wr = json.loads((out_a / "state.json").read_text(encoding="utf-8")), \
        json.loads((out_a / "wiring.json").read_text(encoding="utf-8"))
    regen_spec.write_spec_sidecar(out_a, dict(spec, additions=additions.stamp(spec, man, py)), st, wr)
    rec = regenerate.reconstruct_from_folder(out_a)
    assert rec.exact and [x.get("name") for x in rec.spec["additions"]] == \
        [x.get("name") for x in spec["additions"]]
    out_c = tmp_path / "c"
    o = regenerate.run_regenerate(py, out_a, rec.spec, out_c, timeout=900, scripts_enabled=False)
    assert o.get("ok"), o
    assert [e["name"] for e in o["additions"]["added"]] == ["cz_mine_pulse", "cz_mine", "cz", "recipe_check"]
    assert o["additions"]["notes"] == [] or all("manifest" in n for n in o["additions"]["notes"])
    c = json.loads((out_c / "state.json").read_text(encoding="utf-8"))
    for e in rep["added"]:
        assert _subtree(c, e["path"]) == _subtree(a, e["path"]), e["path"]
    # every gate of the first build is there once (a re-generate's populate is
    # re-read from the chip, so the builder may add the other CZ variants)
    assert set(a["qubit_pairs"]["q1-2"]["macros"]) <= set(c["qubit_pairs"]["q1-2"]["macros"])
    prev = config_generator.run_config_preview(py, out_c)
    assert prev.get("ok"), prev
    rec2 = regenerate.reconstruct_from_folder(out_c)
    assert rec2.exact and [x.get("name") for x in rec2.spec["additions"]] == \
        ["cz_mine_pulse", "cz_mine", "cz", None]


def test_real_lab_env_wizard_vs_script_and_lab_chip_regenerate(tmp_path):
    from tests.test_lab_additions_p2 import _env, _real_spec
    py = _env("QSM_LAB_ENV_PYTHON")
    if not py:
        pytest.skip("QSM_LAB_ENV_PYTHON not given")
    mod = os.environ.get("QSM_LAB_READOUT_MODULE", "quam_config.complex_weights_pulse")
    cls = mod + "." + os.environ.get("QSM_LAB_READOUT_CLASS", "ComplexWeightsReadoutPulse")
    n = 200
    spec = _real_spec()
    spec["additions"] = [
        {"kind": "pulse", "target": "qubits.q1.resonator.operations", "name": "readout_cw", "class": cls,
         "fields": {"length": n, "amplitude": 0.05, "weights_real": [0.5] * n, "weights_imag": [0.0] * n}},
        {"kind": "pulse", "target": "qubits.q1.z.operations", "name": "cz_mine_pulse", "class": FLAT,
         "fields": {"length": 48, "amplitude": 0.08, "flat_length": 32}},
        _macro("cz_mine", "#/qubits/q1/z/operations/cz_mine_pulse"),
        _default("cz_mine"),
    ]
    rep, man, out_a, a = _wizard_vs_script(tmp_path, py, spec, modules=[mod])
    assert [e["name"] for e in rep["added"]] == ["readout_cw", "cz_mine_pulse", "cz_mine", "cz"], rep["refused"]
    # a lab chip NOT built by SM (no sidecar): its lab objects come through the
    # merge once, and nothing is turned into an addition
    rec = regenerate.reconstruct_from_folder(out_a)
    assert not rec.exact and not rec.spec.get("additions")
    out_c = tmp_path / "c"
    o = regenerate.run_regenerate(py, out_a, rec.spec, out_c, timeout=900, scripts_enabled=False)
    assert o.get("ok"), o
    assert "additions" not in o
    c = json.loads((out_c / "state.json").read_text(encoding="utf-8"))
    assert _subtree(c, "qubits.q1.resonator.operations.readout_cw") == \
        _subtree(a, "qubits.q1.resonator.operations.readout_cw")
    assert "cz_mine" in c["qubit_pairs"]["q1-2"]["macros"]
    prev = config_generator.run_config_preview(py, out_c)
    assert prev.get("ok"), prev


def test_real_lab_tree_both_movers_wizard_vs_script(tmp_path, monkeypatch):
    from tests.test_lab_additions_p2 import _real_spec
    from tests.test_lab_additions_p4 import _lab_tree_env
    py = _lab_tree_env(monkeypatch)
    module = os.environ.get("QSM_LAB_TREE_BOTH_MOVERS_MODULE", "quam_config.cz_gate_both_movers")
    gate = module + ".CZGateBothMovers"
    spec = _real_spec()      # q1 5.2 GHz above q2 4.9 GHz
    flat = {"length": 48, "amplitude": 0.08, "flat_length": 32}
    spec["additions"] = [
        {"kind": "pulse", "target": "qubits.q1.z.operations", "name": "cz_hi", "class": FLAT, "fields": flat},
        {"kind": "pulse", "target": "qubits.q2.z.operations", "name": "cz_lo", "class": FLAT,
         "fields": dict(flat, amplitude=-0.05)},
        {"kind": "macro", "target": P, "name": "cz_both", "class": gate,
         "fields": {"flux_pulse_high": "#/qubits/q1/z/operations/cz_hi",
                    "flux_pulse_low": "#/qubits/q2/z/operations/cz_lo"}},
        _default("cz_both"),
    ]
    rep, *_ = _wizard_vs_script(tmp_path, py, spec, modules=[module])
    assert [e["name"] for e in rep["added"]] == ["cz_hi", "cz_lo", "cz_both", "cz"], rep["refused"]


def test_real_qdac_chip_wizard_vs_script(tmp_path):
    from tests.test_generate_qdac_live_build import _qdac_test_spec
    from tests.test_lab_additions_p2 import _env
    from quam_state_manager.core import capabilities
    py = _env("QSM_QDAC_PYTHON")
    if not py:
        pytest.skip("QSM_QDAC_PYTHON not given")
    spec = _qdac_test_spec()
    probe = config_generator.probe_capabilities(py, None)
    root = capabilities.qpu_root_check(spec, {"capabilities": probe.get("capabilities"),
                                              "versions": probe.get("versions"),
                                              "qpu_roots": probe.get("qpu_roots")})
    spec["quam_class"] = root["chosen"]
    spec["additions"] = [
        {"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "xy_flat", "class": FLAT,
         "fields": {"length": 48, "amplitude": 0.05, "flat_length": 32}},
        {"kind": "field", "target": "extras.recipe_check", "value": 1},
    ]
    rep, *_ = _wizard_vs_script(tmp_path, py, spec, whole=False)
    assert [e["name"] for e in rep["added"]] == ["xy_flat", "recipe_check"], rep["refused"]
