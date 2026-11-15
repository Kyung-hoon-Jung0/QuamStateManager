"""Pure template checks and optional subprocess checks with installed QM."""
import ast
import itertools
import json
import os
import re
import subprocess
from pathlib import Path

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
    assert "AMP_SCALE_LIMIT" not in source
    assert (f"< {T.AMP_SCALE_LIMIT!r}" in source) == spectators
    if not spectators:
        assert repr(T.AMP_SCALE_LIMIT) not in source
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
        assert "amp(level / unit)" in source and f"duration=length_ns // {T.LENGTH_STEP_NS})" in source
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


def rows(length=16, level=0.15, ref="#/qubits/q1", **kw):
    args = dict(qubits={"s": ref}, levels={"s": level}, lengths={"s": length},
                control="#/qubits/q2", target="q3", unit_amplitudes={"s": 0.1})
    args.update(kw)
    return args


def test_plain_data_counter_example():
    assert T.spectator_rows_problem({"s": "#/qubits/q1"}, {"s": 0.1}, {"s": 16}, control="#/qubits/q2",
                                    target="q3", unit_amplitudes={"s": 0.2}) is None


@pytest.mark.parametrize("length", [16, 52, 52.0])
def test_valid_spectator_rows(length):
    args = rows(length)
    assert T.spectator_rows_problem(**args) is None
    assert args["lengths"]["s"] is length
    assert T.spectator_rows_problem({}, {}, {}) is None
    assert T.spectator_rows_problem(**rows(length, ref="q1")) is None


@pytest.mark.parametrize("length", [0, -4, 8, 12, 18, 52.9, "20", True, float("nan"), float("inf")])
def test_invalid_spectator_row_length(length):
    problem = T.spectator_rows_problem(**rows(length))
    assert "'s'" in problem and repr(length) in problem and str(T.MIN_LENGTH_NS) in problem


def test_min_length_matches_shared_rule():
    from quam_state_manager.core.cz_gate_build import length_rule
    assert length_rule({"length": T.MIN_LENGTH_NS}) is None
    assert length_rule({"length": T.MIN_LENGTH_NS - 4}) is not None
    assert all(length_rule({"length": n}) is None for n in range(T.MIN_LENGTH_NS, 200, 4))


@pytest.mark.parametrize("key", ["qubits", "levels", "lengths"])
def test_spectator_row_key_sets(key):
    args = rows()
    args[key] = {"extra": next(iter(args[key].values()))}
    problem = T.spectator_rows_problem(**args)
    assert "keys differ" in problem and "extra" in problem and "'s'" in problem


@pytest.mark.parametrize("role", ["control", "target"])
@pytest.mark.parametrize("own", ["#/qubits/q1", "q1"])
@pytest.mark.parametrize("ref", ["#/qubits/q1", "q1"])
def test_spectator_row_own_qubit(role, own, ref):
    problem = T.spectator_rows_problem(**rows(ref=ref, **{role: own}))
    assert problem and "control or target" in problem


@pytest.mark.parametrize("units", [{}, {"s": None}, None])
def test_spectator_row_unit_missing(units):
    problem = T.spectator_rows_problem(**rows(unit_amplitudes=units))
    assert problem and "missing" in problem and T.SPECTATOR_UNIT_OP in problem and "'s'" in problem


@pytest.mark.parametrize("change", [dict(qubits=[("s", "q1")]), dict(ref=5), dict(levels=None),
    dict(lengths="x"), dict(control=7), dict(target=["q1"]), dict(unit_amplitudes=[0.1]),
    dict(ref=None), dict(qubits={("a",): "q1"}, levels={("a",): 0}, lengths={("a",): 16}),
    dict(unit_amplitudes={"s": "0.1"}), dict(level=[1])])
def test_spectator_rows_never_raise(change):
    ref = change.pop("ref", "#/qubits/q1")
    level = change.pop("level", 0.15)
    problem = T.spectator_rows_problem(**rows(ref=ref, level=level, **change))
    assert isinstance(problem, str) and problem and "could not be read" not in problem
    for key, value in change.items():
        if key in ("qubits", "levels", "lengths", "unit_amplitudes") and not isinstance(value, dict):
            assert key.replace("_", " ") in problem


@pytest.mark.parametrize("level", [0.2, True, float("nan")])
def test_spectator_row_level(level):
    problem = T.spectator_rows_problem(**rows(level=level))
    assert "'s'" in problem and "level" in problem


def test_unit_op_one_constant():
    source = T.render(T.SlotSpec("ExampleGate", T.SLOTS, True))
    constant, = [n.value.value for n in ast.parse(source).body
                 if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "SPECTATOR_UNIT_OP" for t in n.targets)]
    assert constant == T.SPECTATOR_UNIT_OP
    module = (Path(T.__file__)).read_text(encoding="utf-8")
    assert module.count(repr(T.SPECTATOR_UNIT_OP)) + module.count(f'"{T.SPECTATOR_UNIT_OP}"') == 1
    assert f"or value < {T.MIN_LENGTH_NS}:" in source


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
@pytest.mark.parametrize("length", [16, 52, 0, -4, 8, 12, 18, 52.9, "20", True, float("nan")])
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


ACCEPTED_REFS = ["#/qubits/q1", "q1", " q1 ", " #/qubits/q1 "]
REFUSED_REFS = ["#/qubits/", "", "  ", "#/qubit_pairs/x", "#/qubits/q1/z", "a/b", "#./q1", "#../q1", "#q1",
                "#/qubits//", 5, None]


@pytest.mark.parametrize("ref", ACCEPTED_REFS)
def test_qubit_name_accepts(ref):
    assert T.qubit_name(ref) == "q1"
    assert T.spectator_rows_problem(**rows(ref=ref)) is None


@pytest.mark.parametrize("ref", REFUSED_REFS)
@pytest.mark.parametrize("where", ["spectator", "control", "target"])
def test_qubit_name_refuses(ref, where):
    assert T.qubit_name(ref) is None
    args = rows() if where != "spectator" else rows(ref=ref)
    if where != "spectator":
        args[where] = ref
    problem = T.spectator_rows_problem(**args)
    if where == "control" and ref is None or where == "target" and ref is None:
        assert problem is None  # no role given
        return
    assert isinstance(problem, str) and "must be a reference string or name" in problem, problem
    assert ("'s' qubit" if where == "spectator" else where) in problem


def test_other_collection_is_not_the_own_qubit():
    problem = T.spectator_rows_problem(**rows(ref="#/qubit_pairs/q1", control="#/qubits/q1"))
    assert "must be a reference string or name" in problem and "control or target" not in problem
    assert T.spectator_rows_problem(**rows(ref="#/qubits/q4", control="#/qubits/q1")) is None


def test_spectator_of_another_pair_is_accepted():
    # q2 controls some other pair; here it is only a spectator.
    assert T.spectator_rows_problem(**rows(ref="#/qubits/q4", control="#/qubits/q1", target="q5")) is None


@pytest.mark.parametrize("unit", [0, True, "0.1", float("nan"), float("inf"), 10**400])
def test_unit_problem_blames_the_unit(unit):
    problem = T.spectator_rows_problem(**rows(unit_amplitudes={"s": unit}))
    assert problem == T.UNIT_VALUE_PROBLEM.format(name="s", value=unit)
    assert "'s'" in problem and T.SPECTATOR_UNIT_OP in problem and "level" not in problem
    assert problem.count(".") - repr(unit).count(".") == 1  # one sentence


@pytest.mark.parametrize("level", [None, "0.1", True, float("nan"), float("inf"), 0.2, -0.2, 10**400])
def test_level_problem_is_neutral(level):
    problem = T.spectator_rows_problem(**rows(level=level))
    assert problem == T.LEVEL_PROBLEM.format(name="s", value=level)
    assert "must be a finite number" in problem and "boolean" not in problem
    for value in (level, 1):
        message = T.level_scale_problem(value, 0.1) if value is level else T.level_scale_problem(1, None)
        assert message and "boolean" not in message


def test_fallback_returns_a_sentence(monkeypatch):
    def boom(value):
        raise RuntimeError("forced")
    monkeypatch.setattr(T, "spectator_length_problem", boom)
    problem = T.spectator_rows_problem(**rows())
    assert isinstance(problem, str) and "could not be read" in problem and "RuntimeError" in problem


@pytest.mark.parametrize("pair", [("q1", "#/qubits/q1"), ("#/qubits/q1", " q1 "), ("q1", "q1")])
def test_duplicate_spectator_qubit(pair):
    problem = T.spectator_rows_problem({"a": pair[0], "b": pair[1]}, {"a": 0.1, "b": 0.1}, {"a": 16, "b": 16},
                                       unit_amplitudes={"a": 0.1, "b": 0.1})
    assert problem == T.DUPLICATE_PROBLEM.format(first="a", second="b")
    assert T.spectator_rows_problem({"a": "q1", "b": "q4"}, {"a": 0.1, "b": 0.1}, {"a": 16, "b": 16},
                                    unit_amplitudes={"a": 0.1, "b": 0.1}) is None


def test_length_step_matches_shared_rule():
    from quam_state_manager.core.cz_gate_build import length_rule
    step, low = T.LENGTH_STEP_NS, T.MIN_LENGTH_NS
    assert length_rule({"length": low + step}) is None
    assert all(length_rule({"length": low + d}) is not None for d in range(1, step))
    assert str(step) + " ns" in T.LENGTH_PROBLEM
    source = T.render(T.SlotSpec("ExampleGate", T.SLOTS, True))
    assert f"value % {step} or value < {low}:" in source and f"length_ns // {step})" in source
    assert len(re.findall(r"% \d+", source)) == 1 and len(re.findall(r"// \d+", source)) == 1
    assert T.spectator_rows_problem(**rows(low + step)) is None
    assert T.spectator_rows_problem(**rows(low + step // 2))


def _pure_ok(level, unit=0.1):
    return T.spectator_rows_problem(**rows(level=level, unit_amplitudes={"s": unit})) is None


LEVEL_EDGES = [0.0, 0.1999, -0.1999, 0.2, -0.2, 0.25, True, False, float("nan"), float("inf"), 10**400, None, "0.1"]


@lab
@pytest.mark.parametrize("level", LEVEL_EDGES)
def test_lab_level_parity(tmp_path, level):
    result = run_check(tmp_path, T.render(T.SlotSpec("ExampleGate", T.SLOTS, True)),
                       spectators={"spectator": [level, 16, 0.1]})
    assert result["program_build_started"]
    assert result["ok"] == _pure_ok(level), result
    if not result["ok"]:
        assert result["error"] == "ValueError: " + T.LEVEL_PROBLEM.format(name="spectator", value=level), result
        assert result["slots_played"] == result["spectator_plays"] == result["alignments"] == []


@lab
@pytest.mark.parametrize("unit", [0, 0.0, float("nan"), float("inf")])
def test_lab_unit_parity(tmp_path, unit):
    result = run_check(tmp_path, T.render(T.SlotSpec("ExampleGate", T.SLOTS, True)),
                       spectators={"spectator": [0.0, 16, unit]})
    assert not _pure_ok(0.0, unit) and not result["ok"], result
    assert result["error"] == "ValueError: " + T.UNIT_VALUE_PROBLEM.format(name="spectator", value=unit), result


@lab
def test_lab_duplicate_spectator_guard(tmp_path):
    result = run_check(tmp_path, T.render(T.SlotSpec("ExampleGate", T.SLOTS, True)),
                       spectators={"a": [0.0, 16, 0.1], "b": [0.0, 16, 0.1]}, spectator_same={"b": "a"})
    assert not result["ok"], result
    assert result["error"] == "ValueError: " + T.DUPLICATE_PROBLEM.format(first="a", second="b"), result
    assert result["program_build_started"]
    assert result["slots_played"] == result["spectator_plays"] == result["alignments"] == []
