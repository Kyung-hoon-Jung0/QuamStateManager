"""Pure template checks and optional subprocess checks with installed QM."""
import ast
import itertools
import json
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import slot_gate_template as T

LAB_PY = Path(os.environ.get("SM_LAB_ENV_PYTHON") or "__no_lab_env__")
lab = pytest.mark.skipif(not LAB_PY.is_file(), reason="SM_LAB_ENV_PYTHON not set or absent")
COMBINATIONS = [frozenset(c) for n in (1, 2, 3) for c in itertools.combinations(T.SLOTS, n)]


@pytest.mark.parametrize("slots", COMBINATIONS)
@pytest.mark.parametrize("spectators", [False, True])
def test_round_trip_and_source(slots, spectators):
    spec = T.SlotSpec("ExampleGate", slots, spectators)
    source = T.render(spec)
    parsed = T.parse(source)
    assert parsed.ok and parsed.spec == spec and parsed.version == 1
    assert T.render(parsed.spec) == source
    assert source.splitlines()[0] == T.HEADER_MARK + ", version 1."
    tree = ast.parse(source)
    assert not any(isinstance(n, ast.Name) and n.id == "chip" for n in ast.walk(tree))
    assert not re.search(r"\bchip\b", source)
    assert "AMP_SCALE_LIMIT" not in source and "2.0" not in source
    for slot in T.SLOTS:
        assert (f"    {slot}_flux_pulse:" in source) == (slot in slots)
        assert (f"self.{slot}_flux_pulse_label," in source) == (slot in slots)
    assert ("spectator_qubits: dict" in source) == spectators
    if not spectators:
        assert "SPECTATOR_UNIT_OP" not in source
        assert "self.spectator_" not in source
        assert "Spectator keys differ" not in source
    assert "slots accept operation-name strings" in ast.get_docstring(tree.body[-1])


def test_presets():
    assert list(T.PRESETS.values()) == [frozenset({"coupler"}), frozenset({"control", "target"}), frozenset(T.SLOTS)]
    for slots in T.PRESETS.values():
        for container in (set, tuple, list, frozenset):
            spec = T.SlotSpec("ExampleGate", container(slots))
            assert T.parse(T.render(spec)).spec == spec


@pytest.mark.parametrize("change", [lambda s: s.replace(T.REGION_END, ""),
    lambda s: s + T.REGION_BEGIN + "\n", lambda s: s.replace(T.REGION_BEGIN, T.REGION_END),
    lambda s: s.replace("version 1.", "version 7."), lambda s: s.replace("\n", "\r\n"),
    lambda s: s.replace("duration=self.duration", "duration=4")])
def test_broken_text_is_not_editable(change):
    assert not T.parse(change(T.render(T.SlotSpec("ExampleGate", {"coupler"})))).ok


@pytest.mark.parametrize("text", [None, 7, "", "x", "\ud800"])
def test_parse_never_raises(text):
    assert not T.parse(text).ok


@pytest.mark.parametrize("spec", [T.SlotSpec("bad", {"control"}), T.SlotSpec("CON", {"control"}),
    T.SlotSpec("ExampleGate", set()), T.SlotSpec("ExampleGate", {"other"}),
    T.SlotSpec("ExampleGate", {"control"}, 1), T.SlotSpec("Pulse", {"control"})])
def test_validate(spec):
    assert T.validate(spec)
    with pytest.raises(ValueError):
        T.render(spec)
    assert T.validate(T.SlotSpec("SPECTATOR_UNIT_OP", {"coupler"}, True))


@pytest.mark.parametrize("level,unit", [(2, 1), (-2, 1), (1, 0), (float("nan"), 1),
    (1, float("inf")), (True, 1), (1, False), (1e308, 1e-308), (10**400, 1)])
def test_invalid_scale(level, unit):
    assert T.level_scale_problem(level, unit)


def test_scale_edges_and_range():
    for unit in (-0.1, 0.1, 1):
        assert T.level_scale_problem(1.999 * unit, unit) is None
        assert T.level_scale_problem(2 * unit, unit)
        assert T.reachable_level(unit) == 2 * abs(unit)


@pytest.mark.parametrize("value,valid", [(16, True), (52.0, True), (18, False),
    (12, False), (True, False), (float("nan"), False), ("#./length", False)])
def test_spectator_length(value, valid):
    assert (T.spectator_length_problem(value) is None) == valid


def test_shared_unit_operation_across_gates():
    # Two prospective macro records reference the same qubit operation.
    operations = {}
    for name in ("FirstGate", "SecondGate"):
        source = T.render(T.SlotSpec(name, {"coupler"}, True))
        constant, = [n.value.value for n in ast.parse(source).body
                     if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SPECTATOR_UNIT_OP" for t in n.targets)]
        assert constant == "spectator_unit"
        operations.setdefault("spectator", {}).setdefault(constant, {"amplitude": 0.1})
        assert name not in constant
        assert "amp(level / unit)" in source and "duration=length_ns // 4" in source
        assert not any(isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                       and any(isinstance(t, ast.Subscript) for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))
                       for n in ast.walk(ast.parse(source)))
    assert list(operations["spectator"]) == ["spectator_unit"]


def test_limit_has_one_owner_and_no_bare_ratio_limit():
    package = Path(__file__).resolve().parents[1] / "quam_state_manager"
    owners = []
    for path in package.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if re.search(r"^AMP_SCALE_LIMIT\s*=", source, re.M):
            owners.append(path.name)
        tree = ast.parse(source)
        ratio_names = {n.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
                       and any(isinstance(v, ast.BinOp) and isinstance(v.op, ast.Div) for v in ast.walk(node.value))
                       and any(isinstance(v, ast.Name) and "level" in v.id for v in ast.walk(node.value))
                       for target in node.targets for n in ast.walk(target) if isinstance(n, ast.Name)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and any(isinstance(n, ast.Name) and ("level" in n.id or n.id in ratio_names) for n in ast.walk(node)):
                assert not any(isinstance(n, ast.Constant) and n.value == 2 for n in ast.walk(node)), str(path)
    assert owners == ["slot_gate_template.py"]
    source = (package / "core" / owners[0]).read_text(encoding="utf-8")
    assert "open range (-2, 2)" in source and "error.md line 174" in source
    assert "commented-out line in the docs source" in source
    assert "starting with %, not published" in source
    assert "confirmed by the user (2026-11-14)" in source
    assert "the QM docs state" not in source


def run_check(tmp_path, source, **kwargs):
    from quam_state_manager.core.config_generator import _script_path
    file = tmp_path / "gate.py"
    file.write_text(source, encoding="utf-8")
    inp, out = tmp_path / "in.json", tmp_path / "out.json"
    inp.write_text(json.dumps(dict(file=str(file.resolve()), class_name="ExampleGate", **kwargs)), encoding="utf-8")
    proc = subprocess.run([str(LAB_PY), "-B", str(_script_path("run_slot_gate_check.py")),
                           "--in", str(inp), "--out", str(out)], capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    return json.loads(out.read_text(encoding="utf-8"))


@lab
@pytest.mark.parametrize("slots", COMBINATIONS)
@pytest.mark.parametrize("spectators", [False, True])
def test_lab_plays(tmp_path, slots, spectators):
    rows = {"spectator_a": [0.15, 52.0, 0.1], "spectator_b": [-0.05, 20, 0.1]} if spectators else {}
    result = run_check(tmp_path, T.render(T.SlotSpec("ExampleGate", slots, spectators)), spectators=rows,
                       apply_kwargs={**{f"amplitude_scale_{s}": 0.75 for s in slots},
                                     "duration": 12, "phase_shift_control": 0.125, "phase_shift_target": -0.25})
    assert result["status"] == "ok" and result["ok"], json.dumps(result)
    assert result["imports"] == {"ok": True, "error": None}
    assert result["slots_declared"] == result["slots_played"] == [s for s in T.SLOTS if s in slots]
    for play in result["slot_plays"]:
        assert play["amplitude_scale"] == 0.75 and play["duration"] == 12
        assert play["label"] == play["slot"] + "_op"
        if play["slot"] == "coupler":
            assert play["validate"] is False
    assert result["rotations"] == [["control_xy", 0.125], ["target_xy", -0.25]]
    assert len(result["alignments"]) == 3
    for channels in result["alignments"]:
        assert {"control_xy", "target_xy", "control_z", "target_z"} <= set(channels)
        assert ("coupler" in channels) == ("coupler" in slots)
        assert {n + "_z" for n in rows} <= set(channels)
    assert len(result["spectator_plays"]) == len(rows)
    for play in result["spectator_plays"]:
        level, length, unit = rows[play["name"]]
        assert play["scale"] == pytest.approx(level / unit)
        assert play["duration"] == length // 4


@lab
@pytest.mark.parametrize("failure", ["syntax", "runtime"])
def test_lab_error_line(tmp_path, failure):
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}))
    bad = "        broken syntax !" if failure == "syntax" else "        raise RuntimeError('example failure')"
    source = source.replace("        pair = self.qubit_pair", bad, 1 if failure == "syntax" else 2)
    result = run_check(tmp_path, source)
    assert result["status"] == "ok" and not result["ok"]
    assert result["line"] in [i for i, line in enumerate(source.splitlines(), 1) if line == bad]
    assert result["imports"]["ok"] == (failure == "runtime")


def test_duplicate_slots_before_normalizing():
    spec = T.SlotSpec("ExampleGate", ["control", "control"])
    assert any("duplicates" in problem for problem in T.validate(spec))
    with pytest.raises(ValueError, match="duplicates"):
        T.render(spec)


def spectator():
    return SimpleNamespace(z=SimpleNamespace(operations={
        "spectator_unit": SimpleNamespace(amplitude=0.1)}))


@pytest.mark.parametrize("length", [16, 52, 52.0])
def test_valid_spectator_rows(length):
    qubit = spectator()
    lengths = {"spectator": length}
    assert T.spectator_rows_problem({"spectator": qubit}, {"spectator": 0.15}, lengths,
                                    control=spectator(), target=spectator()) is None
    assert lengths["spectator"] is length
    assert T.spectator_rows_problem({}, {}, {}) is None
    # A different pair's control is allowed.
    assert T.spectator_rows_problem({"other_control": qubit}, {"other_control": 0},
                                    {"other_control": length}) is None


@pytest.mark.parametrize("length", [18, 52.9, "20", True, float("nan"), float("inf"), 12])
def test_invalid_spectator_row_length(length):
    problem = T.spectator_rows_problem({"spectator": spectator()}, {"spectator": 0}, {"spectator": length})
    assert "spectator" in problem and str(length) in problem
    if length == 12:
        assert "16" in problem


@pytest.mark.parametrize("key", ["qubits", "levels", "lengths"])
def test_spectator_row_key_sets(key):
    rows = dict(qubits={"spectator": spectator()}, levels={"spectator": 0}, lengths={"spectator": 16})
    rows[key] = {"extra": next(iter(rows[key].values()))}
    problem = T.spectator_rows_problem(**rows)
    assert "keys differ" in problem and "extra" in problem and "spectator" in problem


@pytest.mark.parametrize("role", ["control", "target"])
def test_spectator_row_own_qubit(role):
    qubit = spectator()
    assert "spectator" in T.spectator_rows_problem({"spectator": qubit}, {"spectator": 0},
                                                   {"spectator": 16}, **{role: qubit})


@pytest.mark.parametrize("level", [0.2, True, float("nan")])
def test_spectator_row_level(level):
    problem = T.spectator_rows_problem({"spectator": spectator()}, {"spectator": level}, {"spectator": 16})
    assert "spectator" in problem and "level" in problem


@lab
def test_lab_real_slot_value_probes(tmp_path):
    result = run_check(tmp_path, T.render(T.SlotSpec("ExampleGate", T.SLOTS)), probe_slot_values=True)
    assert result["ok"], result
    assert len(result["slot_value_probes"]) == 6
    for probe in result["slot_value_probes"]:
        assert probe["channel_play_called"], probe
        assert probe["ok"] == (probe["kind"] == "string" or probe["slot"] == "coupler"), probe
        if not probe["ok"]:
            assert "not found in channel" in probe["error"]


@lab
@pytest.mark.parametrize("slot", T.SLOTS)
def test_lab_inline_slot_policy(tmp_path, slot):
    result = run_check(tmp_path, T.render(T.SlotSpec("ExampleGate", {slot})), inline_slots=[slot])
    assert result["ok"] == (slot == "coupler"), result
    if slot != "coupler":
        assert slot in result["error"] and "operation-name strings" in result["error"]
        assert result["slots_played"] == []


@lab
@pytest.mark.parametrize("length", [16, 52, 18, 52.9, "20", True, float("nan")])
def test_lab_spectator_length_guard(tmp_path, length):
    result = run_check(tmp_path, T.render(T.SlotSpec("ExampleGate", T.SLOTS, True)),
                       spectators={"spectator": [0.05, length, 0.1]})
    assert result["program_build_started"]
    assert result["ok"] == (type(length) is int and length in (16, 52)), result
    if not result["ok"]:
        assert "spectator" in result["error"] and repr(length) in result["error"]
        assert result["slots_played"] == result["spectator_plays"] == result["alignments"] == []
    else:
        assert result["spectator_plays"][0]["duration"] == length // 4


@lab
@pytest.mark.parametrize("key", ["qubits", "levels", "lengths"])
def test_lab_spectator_key_guard(tmp_path, key):
    result = run_check(tmp_path, T.render(T.SlotSpec("ExampleGate", T.SLOTS, True)),
                       spectators={"spectator": [0, 16, 0.1]}, spectator_keys={key: []})
    assert not result["ok"] and "keys differ" in result["error"] and "spectator" in result["error"], result
    assert result["program_build_started"]
    assert result["slots_played"] == result["spectator_plays"] == result["alignments"] == []


@lab
@pytest.mark.parametrize("role", ["control", "target"])
def test_lab_spectator_own_guard(tmp_path, role):
    result = run_check(tmp_path, T.render(T.SlotSpec("ExampleGate", T.SLOTS, True)),
                       spectators={"spectator": [0, 16, 0.1]}, spectator_own={"spectator": role})
    assert not result["ok"] and "spectator" in result["error"] and "control or target" in result["error"], result
    assert result["program_build_started"]
    assert result["slots_played"] == result["spectator_plays"] == result["alignments"] == []


@lab
@pytest.mark.parametrize("key", ["levels", "lengths"])
def test_lab_spectator_extra_key_guard(tmp_path, key):
    result = run_check(tmp_path, T.render(T.SlotSpec("ExampleGate", T.SLOTS, True)),
                       spectators={"spectator": [0, 16, 0.1]},
                       spectator_keys={key: ["spectator", "extra"]})
    assert not result["ok"] and "keys differ" in result["error"] and "extra" in result["error"], result
    assert result["program_build_started"]
    assert result["slots_played"] == result["spectator_plays"] == result["alignments"] == []
