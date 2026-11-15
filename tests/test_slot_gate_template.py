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


@pytest.mark.parametrize("ref", ["#/qubits/ q1", "#/qubits/q 1", "q" + chr(9) + "1", "#/qubits/q1" + chr(10) + "2", "#/qubits/#q1"])
def test_whitespace_or_hash_inside_a_name_is_not_a_qubit(ref):
    assert T.qubit_name(ref) is None
    problem = T.spectator_rows_problem(**rows(ref="#/qubits/q4", control=ref, target="q5"))
    assert isinstance(problem, str) and problem
    # never mistaken for, or hidden from, the own-qubit check
    assert T.spectator_rows_problem(**rows(ref=ref, control="#/qubits/q1", target="q5")) != T.OWN_PROBLEM.format(name="s")


def test_unit_without_an_amplitude_reads_as_missing():
    assert T.spectator_rows_problem(**rows(unit_amplitudes={"s": None})) == T.UNIT_PROBLEM.format(name="s")


@lab
def test_lab_unit_without_an_amplitude_reads_as_missing(tmp_path):
    result = run_check(tmp_path, T.render(T.SlotSpec("ExampleGate", T.SLOTS, True)),
                       spectators={"spectator": [0.0, 16, None]})
    assert not result["ok"], result
    assert result["error"] == "ValueError: " + T.UNIT_PROBLEM.format(name="spectator"), result


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


# Sequence steps placed before and after the gate pulses.

#: sha256 of the rendered source without sequence steps, taken from the template before steps existed.
NO_STEP_SHA256 = {
    ('control', False): 'cd2630a2f87e4d445ca7e408205696da0254432d5fa8bf16f664528b393c34a7',
    ('control', True): '25a708f35d6e487b4171783ce5758581929feec0d9bc6a8c6312e42b605df2aa',
    ('target', False): '4c42e2f82555d47f31fbb0675cc712fa3f4812f77eb32d3fc23fbd10e9497f60',
    ('target', True): 'a1d5fe81673d625555c2ecc0aed62f082ac15d317c1c3ea81c4264996f976e06',
    ('coupler', False): '54e3010f04d1ae5e7625b3a118e0286a3555965f90f43e24b9935e92143956a6',
    ('coupler', True): '2f43c7ef30aeb5cb88a9b15e77e900363eb019637e3ccb05974b45a6f3d79ab2',
    ('control+target', False): '1e6580ab0b40b5c7d08e1000612cf249b1a7ab34bcd19457ffcf01b3fe5a0590',
    ('control+target', True): '3c91b9f729f7aeac074deb8e939281ffa81c90dcb2bb26bac905394dfff4b3a2',
    ('control+coupler', False): '247f937083947fc57fbf59545d36820a8dbff69391f92c939b4214f3f2f76458',
    ('control+coupler', True): 'dc85bcd97676d6b10ef8017010d70b03e2c9fc068758ebd4593b8cc65a6cb153',
    ('target+coupler', False): 'a27da53d5e819b6a822b33efae4585594d2b4b90312d0adf1faf6f701f6cebe2',
    ('target+coupler', True): 'e76cda2a1e27f736d5830cd7953c933f88aeb938b911cad6d01ece9fa1a2f83c',
    ('control+target+coupler', False): '72fa3c11549129c385793cfe09f6db63af09705d8ca54cb95815c2e68965730f',
    ('control+target+coupler', True): 'ec8161612bde80f574debd2a05232e7ef2c4ebff0ac2cbd66861f537500aed70',
}


class IntSub(int):
    pass


class EvilInt(int):
    def __repr__(self):
        return "16) or __import__('os').getcwd() or (16"


class FloatSub(float):
    def __repr__(self):
        return "FloatSub(16.0)\n        BOOM = 1"


class StrSub(str):
    def __repr__(self):
        return "StrSub('x')\n        BOOM = 1"


class DictSub(dict):
    pass


WAIT = {"kind": "wait", "length": 16}
XY = {"kind": "xy", "qubit": "control", "operation": "x180"}
ALIGN = {"kind": "align"}
NASTY = 'x"1\n\\y'


def _sha(source):
    import hashlib
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("slots", COMBINATIONS)
@pytest.mark.parametrize("spectators", [False, True])
@pytest.mark.parametrize("empty", [None, (), []])
def test_no_step_render_is_unchanged(slots, spectators, empty):
    key = ("+".join(s for s in T.SLOTS if s in slots), spectators)
    spec = T.SlotSpec("ExampleGate", slots, spectators) if empty is None else \
        T.SlotSpec("ExampleGate", slots, spectators, empty, empty)
    source = T.render(spec)
    assert _sha(source) == NO_STEP_SHA256[key]
    assert "wait" not in source and "# before" not in source and "Before the gate" not in source
    assert T.parse(source).spec == T.SlotSpec("ExampleGate", slots, spectators)


STEP_CASES = [([WAIT], []), ([XY], []), ([], [ALIGN]), ([WAIT, XY, ALIGN], [XY, WAIT]),
              ([{"kind": "xy", "qubit": "target", "operation": "x90.drag-1"}, {"kind": "wait", "length": 52}],
               [ALIGN])]


def _step_order(source):
    """Return (kind, where) for every step-like statement in apply outside the gate core, in source order."""
    body = source.split("    def apply(", 1)[1]
    lines = body.split("\n")
    first_play = next(i for i, line in enumerate(lines) if ".play(self." in line)
    head, tail = "\n".join(lines[:first_play - 1]), "\n".join(lines[first_play:])
    rest = tail.rsplit("frame_rotation_2pi", 1)[1].split("\n", 1)[1]
    found = []
    for part, where in ((head, "before"), (rest, "after")):
        for line in part.splitlines():
            line = line.strip()
            if line.startswith("wait("):
                found.append(("wait", where))
            elif ".xy.play(" in line:
                found.append(("xy", where))
            elif line == "align(*channels)":
                found.append(("align", where))
    return found


@pytest.mark.parametrize("slots", COMBINATIONS)
@pytest.mark.parametrize("spectators", [False, True])
@pytest.mark.parametrize("before,after", STEP_CASES)
def test_steps_round_trip_and_order(slots, spectators, before, after):
    spec = T.SlotSpec("ExampleGate", slots, spectators, before, after)
    source = T.render(spec)
    parsed = T.parse(source)
    assert parsed.ok, parsed.why
    assert parsed.spec == T.SlotSpec("ExampleGate", slots, spectators, tuple(before), tuple(after))
    assert T.render(parsed.spec) == source
    # The first "after" align is the gate's own closing align after the phase corrections.
    expected = [(s["kind"], "before") for s in before] + [("align", "after")] + [(s["kind"], "after") for s in after]
    assert _step_order(source) == expected
    lengths = [repr(s["length"]) for s in before + after if s["kind"] == "wait"]
    assert re.findall(r"wait\(int\((.*?)\) // ", source) == lengths
    plays = [repr(s["operation"]) for s in before + after if s["kind"] == "xy"]
    assert re.findall(r"\.xy\.play\((.*?)\)\n", source) == plays
    assert ("from qm.qua import align, amp, play, wait\n" in source) == any(
        s["kind"] == "wait" for s in before + after)
    ast.parse(source)
    assert not re.search(r"\bchip\b", source)


#: The exact timing lines of a class with steps; true for a wait as well as for an xy play.
TIMING_WORDS = [
    "Steps are statements in this order. An xy play starts when its own channel is free.",
    "A wait holds each gate channel for that time from the moment that channel is free; it does not",
    "wait for other channels. Steps on different channels overlap unless an align step is between them.",
    "Channels that share a core run one after the other, never together.",
]
#: An xy play, then a wait, then an xy play on the other qubit, all after the gate. The wait is one
#: statement over every gate channel and no align follows it, so the second play is not held until
#: the first play ends; the class docstring says so and the statements show no align between them.
PLAY_WAIT_PLAY = ([], [{"kind": "xy", "qubit": "control", "operation": "x180"}, {"kind": "wait", "length": 16},
                       {"kind": "xy", "qubit": "target", "operation": "y90"}],
                  ["align", "play coupler coupler_op", "align", "align",
                   "play control_xy x180", "wait 4", "play target_xy y90"])


def test_class_docstring_is_true_for_a_wait_between_plays():
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, *PLAY_WAIT_PLAY[:2]))
    doc = ast.get_docstring(ast.parse(source).body[-1]).splitlines()
    steps = ("After the gate pulses: play x180 on the control xy channel, wait 16 ns,"
             " play y90 on the target xy channel.")
    assert doc[-5:] == [steps] + TIMING_WORDS
    lines = source.splitlines()
    first = lines.index("        pair.qubit_control.xy.play('x180')")
    assert lines[first - 1:first + 3] == ["        align(*channels)", "        pair.qubit_control.xy.play('x180')",
                                          "        wait(int(16) // 4, *channels)",
                                          "        pair.qubit_target.xy.play('y90')"]
    assert T.render(T.parse(source).spec) == source


def test_class_docstring_names_the_steps():
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, [WAIT, XY], [ALIGN]))
    doc = ast.get_docstring(ast.parse(source).body[-1]).splitlines()
    assert "Before the gate pulses: wait 16 ns, play x180 on the control xy channel." in doc
    assert "After the gate pulses: align." in doc
    assert " then " not in " ".join(doc)
    assert [line.strip() for line in T.STEP_TIMING_NOTE] == doc[doc.index("After the gate pulses: align.") + 1:]
    assert doc[doc.index("After the gate pulses: align.") + 1:] == TIMING_WORDS
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, [], [ALIGN]))
    assert "Before the gate pulses: nothing." in ast.get_docstring(ast.parse(source).body[-1]).splitlines()


@pytest.mark.parametrize("change", [
    lambda s: s.replace("# before = [{'kind': 'wait', 'length': 16}", "# before = [{'kind': 'wait', 'length': 20}"),
    lambda s: s.replace("        wait(int(16) // 4, *channels)\n", ""),
    lambda s: s.replace("        pair.qubit_control.xy.play('x180')\n", ""),
    lambda s: s.replace("# after = [{'kind': 'align'}]", "# after = []"),
    lambda s: s.replace("# after = [{'kind': 'align'}]", "# after = [{'kind': 'reset'}]"),
    lambda s: s.replace("# after = [{'kind': 'align'}]", "# after = not a list"),
    lambda s: s.replace("    # after = [{'kind': 'align'}]\n", ""),
    lambda s: s.replace("Before the gate pulses: wait 16 ns", "Before the gate pulses: wait 20 ns"),
    lambda s: s.replace("    Channels that share a core run one after the other, never together.\n", ""),
    lambda s: s.replace("one after the other, never together.", "one after the other."),
])
def test_hand_edited_steps_are_refused(change):
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, [WAIT, XY], [ALIGN]))
    edited = change(source)
    assert edited != source
    assert not T.parse(edited).ok


def test_empty_step_lists_written_by_hand_are_refused():
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}))
    edited = source.replace("    # spectators = False\n", "    # spectators = False\n    # before = []\n    # after = []\n")
    assert not T.parse(edited).ok


@pytest.mark.parametrize("steps", [[], (), [ALIGN], [WAIT], [{"kind": "wait", "length": 52}], [XY],
                                   [{"kind": "xy", "qubit": "target", "operation": "a_b.c-9"}]])
def test_valid_steps(steps):
    assert T.sequence_steps_problem(steps) is None


@pytest.mark.parametrize("steps,number,words", [
    ([ALIGN, {"kind": "reset"}], 2, "unknown kind"),
    ([{"kind": 3}], 1, "unknown kind"),
    ([{"length": 16}], 1, "mapping"),
    ([None], 1, "mapping"),
    ([ALIGN, ALIGN, {"kind": "wait", "length": "16"}], 3, "waits"),
    ([{"kind": "wait"}], 1, "waits"),
    ([{"kind": "wait", "length": 12}], 1, "waits"),
    ([{"kind": "wait", "length": 0}], 1, "waits"),
    ([{"kind": "wait", "length": 18}], 1, "waits"),
    ([{"kind": "wait", "length": 52.5}], 1, "waits"),
    ([{"kind": "wait", "length": True}], 1, "waits"),
    ([{"kind": "wait", "length": float("nan")}], 1, "waits"),
    ([{"kind": "wait", "length": float("inf")}], 1, "waits"),
    ([{"kind": "xy", "qubit": "coupler", "operation": "x180"}], 1, "control or target"),
    ([{"kind": "xy", "operation": "x180"}], 1, "control or target"),
    ([{"kind": "xy", "qubit": "control", "operation": ""}], 1, "operation"),
    ([{"kind": "xy", "qubit": "control", "operation": "a b"}], 1, "operation"),
    ([{"kind": "xy", "qubit": "control", "operation": NASTY}], 1, "operation"),
    ([{"kind": "xy", "qubit": "control", "operation": "x180\n"}], 1, "operation"),
    ([{"kind": "xy", "qubit": "control", "operation": "x" + chr(233)}], 1, "operation"),
    ([{"kind": "xy", "qubit": "control", "operation": 5}], 1, "operation"),
    ([{"kind": "wait", "length": 16.0}], 1, "waits 16.0 ns"),
    ([{"kind": "wait", "length": 52.0}], 1, "waits"),
    ([WAIT, {"kind": "wait", "length": IntSub(16)}], 2, "waits"),
    ([{"kind": "wait", "length": EvilInt(16)}], 1, "waits"),
    ([{"kind": "wait", "length": FloatSub(16.0)}], 1, "waits"),
    ([{"kind": "wait", "length": T.MAX_WAIT_NS + T.LENGTH_STEP_NS}], 1, "at most"),
    ([{"kind": "wait", "length": 10 ** 400}], 1, "waits a whole number too large to show ns"),
    ([{"kind": "wait", "length": 10 ** 5000}], 1, "waits a whole number too large to show ns"),
    ([{"kind": "wait", "length": 10 ** 5000 + 1}], 1, "waits"),
    ([{"kind": "wait", "length": -16}], 1, "waits"),
    ([{"kind": "xy", "qubit": "Control", "operation": "x180"}], 1, "control or target"),
    ([{"kind": "xy", "qubit": " control", "operation": "x180"}], 1, "control or target"),
    ([{"kind": "xy", "qubit": StrSub("control"), "operation": "x180"}], 1, "control or target"),
    ([{"kind": "xy", "qubit": "control", "operation": "x'y"}], 1, "operation"),
    ([XY, {"kind": "xy", "qubit": "control", "operation": StrSub("x180")}], 2, "operation"),
    ([{"kind": StrSub("align")}], 1, "unknown kind"),
    ([DictSub(kind="align")], 1, "mapping"),
    ([{"kind": "align", "length": "junk", "qubit": 3}], 1, "the key 'length', which a step of kind align"),
    ([ALIGN, {"kind": "wait", "length": 16, "operation": "zzz"}], 2, "the key 'operation', which a step of kind wait"),
    ([{"kind": "xy", "qubit": "control", "operation": "x180", "length": 16}], 1, "the key 'length'"),
    ([{"kind": "align", 7: 1}], 1, "the key 7"),
    ([{"kind": "align", ("a", 1): 1}], 1, "the key ('a', 1)"),
])
@pytest.mark.parametrize("where", ["before", "after"])
def test_step_problems_name_the_step(steps, number, words, where):
    problem = T.sequence_steps_problem(steps, where)
    assert isinstance(problem, str) and problem.startswith(f"Sequence step {number} {where} the gate"), problem
    assert words in problem
    spec = T.SlotSpec("ExampleGate", {"coupler"}, False, **{where: steps})
    assert problem in T.validate(spec)
    with pytest.raises(ValueError):
        T.render(spec)


@pytest.mark.parametrize("steps", [None, "align", 7, {"kind": "align"}, [[1]],
                                   [{"kind": "wait", "length": object()}]])
def test_step_check_never_raises(steps):
    problem = T.sequence_steps_problem(steps)
    assert isinstance(problem, str) and problem


def test_wait_rule_follows_the_shared_constants():
    low, step = T.MIN_LENGTH_NS, T.LENGTH_STEP_NS
    assert T.sequence_steps_problem([{"kind": "wait", "length": low}]) is None
    assert T.sequence_steps_problem([{"kind": "wait", "length": low - step}])
    assert T.sequence_steps_problem([{"kind": "wait", "length": low + step // 2}])
    assert f"multiple of {step} ns, at least {low} ns" in T.WAIT_PROBLEM
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, [WAIT]))
    assert f"16 % {step} or 16 < {low}):" in source and f"wait(int(16) // {step}, *channels)" in source
    assert T.MAX_WAIT_NS == 2 ** 24 * step and T.MAX_WAIT_NS % step == 0
    assert f"at most {T.MAX_WAIT_NS} ns" in T.WAIT_PROBLEM
    assert f"if (type(16) is not int or 16 > {T.MAX_WAIT_NS} or 16 % {step} or 16 < {low}):" in source
    assert T.sequence_steps_problem([{"kind": "wait", "length": T.MAX_WAIT_NS}]) is None
    assert T.sequence_steps_problem([{"kind": "wait", "length": T.MAX_WAIT_NS + step}])
    assert repr(T.WAIT_PROBLEM) in source


def _forced(monkeypatch, before=(), after=()):
    monkeypatch.setattr(T, "sequence_steps_problem", lambda steps, where="before": None)
    return T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, before, after))


def test_unsafe_names_stay_inert_literals(monkeypatch):
    assert T.sequence_steps_problem([{"kind": "xy", "qubit": "control", "operation": NASTY}])
    payload = "16) or __import__('os') or (1"
    source = _forced(monkeypatch, [{"kind": "xy", "qubit": "control", "operation": NASTY}],
                     [{"kind": "wait", "length": payload}])
    tree = ast.parse(source)
    plays = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "play"
             and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "xy"]
    assert len(plays) == 1 and isinstance(plays[0].args[0], ast.Constant) and plays[0].args[0].value == NASTY
    assert not any(isinstance(n, ast.Name) and n.id == "__import__" for n in ast.walk(tree))
    waits = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "int"]
    assert [w.args[0].value for w in waits] == [payload]
    assert "an unchecked operation" in ast.get_docstring(tree.body[-1])
    assert T.parse(source).spec.before[0]["operation"] == NASTY


#: Two xy steps on different qubits have no align between them: the plays start together (the class
#: docstring says so), and an align step chosen between them is what orders them in time.
TOGETHER = ([XY, {"kind": "xy", "qubit": "target", "operation": "y90"}],
            [XY, ALIGN, {"kind": "xy", "qubit": "target", "operation": "y90"}],
            ["play control_xy x180", "play target_xy y90", "align", "play coupler coupler_op", "align", "align",
             "play control_xy x180", "align", "play target_xy y90"])


@lab
@pytest.mark.parametrize("before,after,expected", [
    ([WAIT], [], ["wait 4", "align", "play coupler coupler_op", "align", "align"]),
    ([XY], [], ["play control_xy x180", "align", "play coupler coupler_op", "align", "align"]),
    ([], [ALIGN], ["align", "play coupler coupler_op", "align", "align", "align"]),
    ([XY, {"kind": "wait", "length": 20}], [{"kind": "xy", "qubit": "target", "operation": "y90"}, WAIT],
     ["play control_xy x180", "wait 5", "align", "play coupler coupler_op", "align", "align",
      "play target_xy y90", "wait 4"]),
    TOGETHER,
    PLAY_WAIT_PLAY,
])
def test_lab_statement_order(tmp_path, before, after, expected):
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, before, after))
    result = run_check(tmp_path, source, xy_operations={"control": ["x180"], "target": ["y90"]})
    assert result["status"] == "ok" and result["ok"], json.dumps(result)
    assert result["statements"] == expected


@lab
def test_lab_missing_xy_operation(tmp_path):
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, [WAIT], [ALIGN, XY]))
    result = run_check(tmp_path, source, xy_operations={"target": ["x180"]})
    assert not result["ok"]
    assert result["error"] == "ValueError: " + T.XY_MISSING_PROBLEM.format(
        number=2, where="after", role="control", operation="x180"), result
    assert result["statements"] == []


@lab
@pytest.mark.parametrize("length", [18, 12, 52.5, True])
def test_lab_wait_guard_without_the_pure_check(tmp_path, monkeypatch, length):
    source = _forced(monkeypatch, [WAIT], [{"kind": "wait", "length": length}])
    result = run_check(tmp_path, source)
    assert not result["ok"]
    assert result["error"] == "ValueError: " + T.WAIT_PROBLEM.format(number=1, where="after", value=length), result
    assert result["statements"] == []


# Exact types, inert forced input, the wait upper limit and a run of the generated apply() without the lab env.

ACCEPTED = [[WAIT], [{"kind": "wait", "length": 20}], [{"kind": "wait", "length": T.MAX_WAIT_NS}], [XY],
            [{"kind": "xy", "qubit": "target", "operation": "x180"}],
            [{"kind": "xy", "qubit": "control", "operation": "x90.DRAG"}],
            [{"kind": "xy", "qubit": "target", "operation": "a-b_c"}], [ALIGN]]


@pytest.mark.parametrize("steps", ACCEPTED)
@pytest.mark.parametrize("where", ["before", "after"])
def test_accepted_steps_render_byte_stable(steps, where):
    assert T.sequence_steps_problem(steps, where) is None
    spec = T.SlotSpec("ExampleGate", {"coupler"}, False, **{where: steps})
    source = T.render(spec)
    parsed = T.parse(source)
    assert parsed.ok, parsed.why
    assert parsed.spec == spec and T.render(parsed.spec) == source
    assert source.isascii()


@pytest.mark.parametrize("length", [IntSub(16), EvilInt(16), FloatSub(16.0), 16.0, True])
def test_only_plain_values_reach_the_source(monkeypatch, length):
    """The check refuses a subclass; forced past it, the source still holds no text of a foreign type."""
    step = {"kind": "wait", "length": length}
    assert T.sequence_steps_problem([step])
    with pytest.raises(ValueError, match="Sequence step 1 before the gate waits"):
        T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, [step]))
    source = _forced(monkeypatch, [step], [{"kind": "xy", "qubit": "control", "operation": StrSub("x180")}])
    tree = ast.parse(source)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"__import__", "BOOM", "IntSub", "EvilInt", "FloatSub", "StrSub", "np"}
    assert len(source.splitlines()) == len(_forced(monkeypatch, [WAIT], [XY]).splitlines())


#: Text that would close a docstring, start a comment or add a line if it were pasted into the source.
BREAKERS = ['""" ; BOOM = 1 ; """', "a\n        BOOM = 1\n", "x\\", "# BOOM", "'" * 3, "x\r    BOOM = 1", "\u2028BOOM = 1",
            "x\u00e9", "\uac00"]
FORCED_LENGTHS = BREAKERS + [float("nan"), float("inf"), 10 ** 400, 10 ** 5000, T.MAX_WAIT_NS + T.LENGTH_STEP_NS,
                             18, 52.5, 16.0, True, None, EvilInt(16), FloatSub(16.0), StrSub("16")]


def _shape(source):
    """Return what a forced value must not change: line count, class body and apply() statement kinds."""
    tree = ast.parse(source)
    body = tree.body[-1].body
    apply = next(n for n in body if isinstance(n, ast.FunctionDef) and n.name == "apply")
    return (len(source.splitlines()), [type(n).__name__ for n in tree.body], [type(n).__name__ for n in body],
            [type(n).__name__ for n in apply.body], len(ast.get_docstring(tree.body[-1]).splitlines()))


@pytest.mark.parametrize("length", FORCED_LENGTHS, ids=lambda v: T._shown(v)[:24])
def test_forced_wait_length_stays_inert(monkeypatch, length):
    assert T.sequence_steps_problem([{"kind": "wait", "length": length}])
    source = _forced(monkeypatch, [{"kind": "wait", "length": length}], [XY])
    assert source.isascii()
    assert _shape(source) == _shape(_forced(monkeypatch, [WAIT], [XY]))
    tree = ast.parse(source)
    assert "BOOM" not in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    doc = ast.get_docstring(tree.body[-1]).splitlines()
    shown = f"wait {length} ns" if type(length) is int and length < 2 ** 63 else "wait an unchecked length"
    assert f"Before the gate pulses: {shown}." in doc
    assert sum(line.startswith("    #") for line in source.splitlines()) == 6


@pytest.mark.parametrize("operation", BREAKERS + [NASTY, StrSub('"""'), 5, None, float("nan")],
                         ids=lambda v: T._shown(v)[:24])
def test_forced_operation_stays_inert(monkeypatch, operation):
    assert T.sequence_steps_problem([{"kind": "xy", "qubit": "target", "operation": operation}])
    source = _forced(monkeypatch, [WAIT], [{"kind": "xy", "qubit": "target", "operation": operation}])
    assert source.isascii()
    assert _shape(source) == _shape(_forced(monkeypatch, [WAIT], [XY]))
    tree = ast.parse(source)
    assert "BOOM" not in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    shown = T._shown(operation) if type(operation) is not str and T.OPERATION_TOKEN.fullmatch(
        T._shown(operation)) else "an unchecked operation"
    assert f"After the gate pulses: play {shown} on the target xy channel." in ast.get_docstring(
        tree.body[-1]).splitlines()
    plays = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "play"
             and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "xy"]
    assert len(plays) == 1 and isinstance(plays[0].args[0], ast.Constant) and type(plays[0].args[0].value) is str


@pytest.mark.parametrize("steps,sentence", [
    ([{"kind": "xy", "qubit": "control.xy.play('a')\n        BOOM = 1\n        pair.qubit_control", "operation": "x"}],
     "Sequence step 1 before the gate names the qubit"),
    ([ALIGN, {"kind": "xy", "qubit": StrSub("control"), "operation": "x"}],
     "Sequence step 2 before the gate names the qubit"),
    ([{"kind": "xy", "operation": "x"}], "Sequence step 1 before the gate names the qubit None"),
    ([None], "Sequence step 1 before the gate must be a mapping"),
    ([ALIGN, {"kind": "reset"}], "Sequence step 2 before the gate has the unknown kind 'reset'"),
    ([{}], "Sequence step 1 before the gate has the unknown kind None"),
    ("align", "The sequence steps before the gate must be a list"),
])
def test_forced_steps_that_cannot_render_are_refused_with_a_sentence(monkeypatch, steps, sentence):
    with pytest.raises(ValueError) as caught:
        _forced(monkeypatch, steps)
    assert str(caught.value).startswith(sentence), str(caught.value)
    assert "\n" not in str(caught.value)


def test_roles_come_from_the_fixed_table():
    assert set(T.XY_CHANNEL) == set(T.XY_ROLES) == {"control", "target"}
    assert T.XY_CHANNEL == {"control": "pair.qubit_control.xy", "target": "pair.qubit_target.xy"}
    for role in T.XY_ROLES:
        source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False,
                                     [{"kind": "xy", "qubit": role, "operation": "x180"}]))
        assert f"        if 'x180' not in pair.qubit_{role}.xy.operations:\n" in source
        assert f"        pair.qubit_{role}.xy.play('x180')\n" in source


def _standin_apply(monkeypatch, source, xy_operations=None):
    """Run the generated apply() on small stand-in objects, without a QM library; return (statements, error)."""
    import dataclasses
    import sys
    import types
    statements = []
    qua = types.ModuleType("qm.qua")
    qua.align = lambda *channels: statements.append("align")
    qua.wait = lambda duration, *channels: statements.append(f"wait {duration}")
    qua.amp = lambda scale: scale
    qua.play = lambda pulse, element, **kwargs: statements.append(f"play {element} spectator")
    core, macro, pulses = (types.ModuleType(n) for n in ("quam.core", "quam.components.macro",
                                                         "quam.components.pulses"))
    core.quam_dataclass = dataclasses.dataclass
    macro.QubitPairMacro = type("QubitPairMacro", (), {})
    pulses.Pulse = type("Pulse", (), {})
    for module in (types.ModuleType("qm"), qua, types.ModuleType("quam"), core,
                   types.ModuleType("quam.components"), macro, pulses):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    class Channel:
        def __init__(self, name, operations):
            self.name, self.operations = name, {op: types.SimpleNamespace(id=op) for op in operations}

        def play(self, label, **kwargs):
            statements.append(f"play {self.name} {label}")

        def frame_rotation_2pi(self, phase):
            statements.append(f"frame_rotation {self.name}")

    def qubit(name):
        z = Channel(name + "_z", [name + "_op"])
        xy = Channel(name + "_xy", (xy_operations or {}).get(name, []))
        return types.SimpleNamespace(z=z, xy=xy, channels={"z": z, "xy": xy})

    pair = types.SimpleNamespace(qubit_control=qubit("control"), qubit_target=qubit("target"),
                                 coupler=Channel("coupler", ["coupler_op"]))
    namespace = {"__name__": "standin_slot_gate"}
    exec(compile(source, "standin_slot_gate.py", "exec"), namespace)

    class Recorder(namespace["ExampleGate"]):
        @property
        def qubit_pair(self):
            return pair

    try:
        Recorder(coupler_flux_pulse="coupler_op").apply()
    except ValueError as exc:
        return statements, str(exc)
    return statements, None


@pytest.mark.parametrize("before,after,expected", [
    ([WAIT], [], ["wait 4", "align", "play coupler coupler_op", "align", "align"]),
    ([XY], [], ["play control_xy x180", "align", "play coupler coupler_op", "align", "align"]),
    ([], [ALIGN], ["align", "play coupler coupler_op", "align", "align", "align"]),
    ([{"kind": "wait", "length": T.MAX_WAIT_NS}], [],
     [f"wait {T.MAX_WAIT_NS // T.LENGTH_STEP_NS}", "align", "play coupler coupler_op", "align", "align"]),
    TOGETHER,
    PLAY_WAIT_PLAY,
])
def test_standin_statement_order(monkeypatch, before, after, expected):
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, before, after))
    statements, error = _standin_apply(monkeypatch, source, {"control": ["x180"], "target": ["y90"]})
    assert error is None and statements == expected


@pytest.mark.parametrize("before,after,have,number,where,role,operation", [
    ([WAIT], [ALIGN, XY], {"target": ["x180"]}, 2, "after", "control", "x180"),
    ([XY], [], {}, 1, "before", "control", "x180"),
    ([XY, {"kind": "xy", "qubit": "target", "operation": "y90"}], [], {"control": ["x180", "y90"]},
     2, "before", "target", "y90"),
    ([XY], [WAIT, XY, {"kind": "xy", "qubit": "target", "operation": "y90"}], {"control": ["x180"]},
     3, "after", "target", "y90"),
])
def test_standin_missing_xy_operation(monkeypatch, before, after, have, number, where, role, operation):
    """The generated apply() refuses a missing xy operation before it emits any statement."""
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, before, after))
    statements, error = _standin_apply(monkeypatch, source, have)
    assert error == T.XY_MISSING_PROBLEM.format(number=number, where=where, role=role, operation=operation)
    assert statements == []
    statements, error = _standin_apply(monkeypatch, source, {"control": ["x180", "y90"], "target": ["x180", "y90"]})
    assert error is None and len(statements) == 4 + len(before + after)


def _forced_text(length):
    """Return the text the rule sentence shows for a forced wait length."""
    if type(length) is int and length.bit_length() > 64:
        return "a whole number too large to show"
    return str(length)


@pytest.mark.parametrize("length", [float("nan"), float("inf"), 10 ** 400, 10 ** 5000,
                                    T.MAX_WAIT_NS + T.LENGTH_STEP_NS, 2 ** 63, 18, 12, 52.5, 16.0, True, None, "16"],
                         ids=lambda v: T._shown(v)[:24])
def test_standin_wait_guard_without_the_pure_check(monkeypatch, length):
    source = _forced(monkeypatch, [WAIT], [{"kind": "wait", "length": length}])
    statements, error = _standin_apply(monkeypatch, source)
    assert error == T.WAIT_PROBLEM.format(number=1, where="after", value=_forced_text(length))
    assert statements == []


@lab
@pytest.mark.parametrize("length", [float("nan"), float("inf"), 10 ** 400, 10 ** 5000,
                                    T.MAX_WAIT_NS + T.LENGTH_STEP_NS], ids=lambda v: T._shown(v)[:24])
def test_lab_wait_guard_for_values_no_literal_can_hold(tmp_path, monkeypatch, length):
    source = _forced(monkeypatch, [WAIT], [{"kind": "wait", "length": length}])
    result = run_check(tmp_path, source)
    assert not result["ok"]
    assert result["error"] == "ValueError: " + T.WAIT_PROBLEM.format(
        number=1, where="after", value=_forced_text(length)), result
    assert result["statements"] == []


@lab
def test_lab_wait_at_the_upper_limit_builds(tmp_path):
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, [{"kind": "wait", "length": T.MAX_WAIT_NS}]))
    result = run_check(tmp_path, source)
    assert result["status"] == "ok" and result["ok"], json.dumps(result)
    assert result["statements"][0] == f"wait {T.MAX_WAIT_NS // T.LENGTH_STEP_NS}"


# The value text of a message, the exact step list type, and what the checker counts as a slot play.

class ListSub(list):
    pass


class TupleSub(tuple):
    pass


E_ACUTE, HANGUL = chr(233), chr(0xAC00)


class NoText:
    def __repr__(self):
        raise RuntimeError("no text")

    __str__ = __repr__


def test_shown_value_is_short_ascii():
    long_name = "bad name " * 20
    assert T._shown(long_name) == ascii(long_name)[:57] + "..." and len(T._shown(long_name)) == 60
    assert T._shown("a" * 58) == "'" + "a" * 58 + "'" and len(T._shown("a" * 58)) == 60
    assert len(T._shown("a" * 59)) == 60 and T._shown("a" * 59).endswith("a...")
    assert len(T._shown("x" * 1_000_000)) == 60
    assert T._shown(10 ** 5000) == "a whole number too large to show"
    assert T._shown(2 ** 64 - 1) == str(2 ** 64 - 1)
    assert T._shown(NoText()) == "a value that cannot be shown"
    assert T._shown("x" + E_ACUTE) == "'x\\xe9'" and T._shown(HANGUL) == "'\\uac00'"
    for value in ("x" + E_ACUTE, HANGUL * 100, [E_ACUTE], {E_ACUTE: HANGUL}, StrSub(E_ACUTE), chr(0x2028), NASTY):
        assert T._shown(value).isascii() and T._shown(value).isprintable() and len(T._shown(value)) <= 60


@pytest.mark.parametrize("where", ["before", "after"])
def test_messages_show_values_short_and_ascii(where):
    problem = T.sequence_steps_problem([{"kind": "xy", "qubit": "control", "operation": "bad name " * 20}], where)
    shown = ascii("bad name " * 20)[:57] + "..."
    assert problem == T.XY_OP_PROBLEM.format(number=1, where=where, value=shown)
    assert len(problem) == len(T.XY_OP_PROBLEM.format(number=1, where=where, value="")) + 60
    for steps in ([{"kind": "xy", "qubit": "control", "operation": "x" + E_ACUTE}],
                  [{"kind": "xy", "qubit": HANGUL, "operation": "x180"}],
                  [{"kind": "wait", "length": E_ACUTE * 500}], [{"kind": E_ACUTE}], [HANGUL * 500],
                  [{"kind": "align", E_ACUTE: 1}], E_ACUTE * 500):
        problem = T.sequence_steps_problem(steps, where)
        assert problem and problem.isascii() and len(problem) < 200, problem


@pytest.mark.parametrize("where", ["before", "after"])
def test_step_list_must_be_an_exact_list_or_tuple(where):
    sentence = f"The sequence steps {where} the gate must be a list; received "
    for steps in (ListSub([ALIGN]), TupleSub([ALIGN]), ListSub(), TupleSub(), (step for step in [ALIGN]),
                  iter([ALIGN]), {"kind": "align"}.values()):
        problem = T.sequence_steps_problem(steps, where)
        assert problem == T.STEPS_PROBLEM.format(where=where, value=T._shown(steps)) and problem.startswith(sentence)
    assert T.sequence_steps_problem(ListSub([ALIGN]), where) == sentence + "[{'kind': 'align'}]."
    assert T.sequence_steps_problem(TupleSub([ALIGN]), where) == sentence + "({'kind': 'align'},)."
    for steps in (TupleSub([ALIGN]), (step for step in [ALIGN])):
        spec = T.SlotSpec("ExampleGate", {"coupler"}, False, **{where: steps})
        assert [p for p in T.validate(spec) if p.startswith(sentence)]
        with pytest.raises(ValueError, match="must be a list; received"):
            T.render(spec)
    assert T.sequence_steps_problem([ALIGN], where) is None and T.sequence_steps_problem((ALIGN,), where) is None


def _standin_check(monkeypatch, tmp_path, source, **request):
    """Run the checker script's check() in this process on stand-in QM modules; return its result."""
    import contextlib
    import dataclasses
    import importlib.util
    import sys
    import types
    from quam_state_manager.core.config_generator import _script_path
    qua = types.ModuleType("qm.qua")
    qua.align = lambda *channels: None
    qua.wait = lambda duration, *channels: None
    # The generated class multiplies an operation name by the amp value, as QUA does.
    qua.amp = lambda scale: type("Amp", (), {"__rmul__": lambda self, name: (name, scale)})()
    qua.play = lambda pulse, element, **kwargs: None
    qua.program = contextlib.nullcontext
    core, macro, pulses, channels = (types.ModuleType(n) for n in (
        "quam.core", "quam.components.macro", "quam.components.pulses", "quam.components.channels"))
    core.quam_dataclass = dataclasses.dataclass
    macro.QubitPairMacro = type("QubitPairMacro", (), {})
    pulses.Pulse = type("Pulse", (), {})

    class SquarePulse(pulses.Pulse):
        def __init__(self, length, amplitude):
            self.length, self.amplitude, self.id = length, amplitude, None

    class Channel:
        def __init__(self, id, operations):
            self.name, self.operations = id, operations
            for name, pulse in operations.items():
                pulse.id = name

        def play(self, label, **kwargs):
            return None

        def frame_rotation_2pi(self, phase):
            return None

    pulses.SquarePulse, channels.Channel = SquarePulse, Channel
    for module in (types.ModuleType("qm"), qua, types.ModuleType("quam"), core,
                   types.ModuleType("quam.components"), macro, pulses, channels):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    file = tmp_path / "gate.py"
    file.write_text(source, encoding="utf-8")
    script = importlib.util.spec_from_file_location("standin_slot_gate_check", _script_path("run_slot_gate_check.py"))
    checker = importlib.util.module_from_spec(script)
    script.loader.exec_module(checker)
    loaded = set(sys.modules)
    try:
        return checker.check(dict(file=str(file.resolve()), class_name="ExampleGate", **request))
    finally:
        for name in set(sys.modules) - loaded:
            del sys.modules[name]


#: Steps with an xy play on each qubit, and the statements of a class with the coupler slot only.
XY_BOTH = ([XY], [{"kind": "xy", "qubit": "target", "operation": "y90"}],
           ["play control_xy x180", "align", "play coupler coupler_op", "align", "align", "play target_xy y90"])


def _xy_play_is_a_statement_only(result):
    assert result["status"] == "ok" and result["ok"], json.dumps(result)
    assert result["statements"] == XY_BOTH[2]
    assert result["slots_declared"] == result["slots_played"] == ["coupler"]
    assert [play["slot"] for play in result["slot_plays"]] == ["coupler"]
    assert [play["label"] for play in result["slot_plays"]] == ["coupler_op"]


def test_standin_xy_play_is_a_statement_and_not_a_slot_play(monkeypatch, tmp_path):
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, *XY_BOTH[:2]))
    _xy_play_is_a_statement_only(_standin_check(monkeypatch, tmp_path, source,
                                                xy_operations={"control": ["x180"], "target": ["y90"]}))


@lab
def test_lab_xy_play_is_a_statement_and_not_a_slot_play(tmp_path):
    source = T.render(T.SlotSpec("ExampleGate", {"coupler"}, False, *XY_BOTH[:2]))
    _xy_play_is_a_statement_only(run_check(tmp_path, source, xy_operations={"control": ["x180"], "target": ["y90"]}))


#: Every statement kind the checker reports, in one class: control and coupler slots, one spectator
#: row, steps before and after the gate, and a phase correction on each qubit.
EVERY_KIND = ([WAIT, XY], [{"kind": "xy", "qubit": "target", "operation": "y90"}, WAIT],
              ["wait 4", "play control_xy x180", "align", "play control_z control_op", "play coupler coupler_op",
               "play spectator_a_z spectator", "align", "frame_rotation control_xy", "frame_rotation target_xy",
               "align", "play target_xy y90", "wait 4"])
EVERY_KIND_REQUEST = dict(spectators={"spectator_a": [0.125, 52, 0.25]},
                          xy_operations={"control": ["x180"], "target": ["y90"]},
                          apply_kwargs={"phase_shift_control": 0.125, "phase_shift_target": -0.25})


def _every_statement_kind_in_order(result):
    assert result["status"] == "ok" and result["ok"], json.dumps(result)
    assert result["statements"] == EVERY_KIND[2]
    assert result["slots_declared"] == result["slots_played"] == ["control", "coupler"]
    assert result["rotations"] == [["control_xy", 0.125], ["target_xy", -0.25]]
    assert result["spectator_plays"] == [{"name": "spectator_a", "scale": 0.5, "duration": 13}]


def test_standin_every_statement_kind_in_order(monkeypatch, tmp_path):
    source = T.render(T.SlotSpec("ExampleGate", {"control", "coupler"}, True, *EVERY_KIND[:2]))
    _every_statement_kind_in_order(_standin_check(monkeypatch, tmp_path, source, **EVERY_KIND_REQUEST))


@lab
def test_lab_every_statement_kind_in_order(tmp_path):
    source = T.render(T.SlotSpec("ExampleGate", {"control", "coupler"}, True, *EVERY_KIND[:2]))
    _every_statement_kind_in_order(run_check(tmp_path, source, **EVERY_KIND_REQUEST))
