"""Saving a slot gate class through the class publication door.

A fake runner stands in for the lab worker; one test runs the real worker's
slot check in the lab environment when SM_LAB_ENV_PYTHON names one.
"""
import base64
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator
from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_template as T
from quam_state_manager.core import quam_config_save as Q
from quam_state_manager.core import slot_gate_template as S

NAME = "SlotCz"
LAB_PY = Path(os.environ.get("SM_LAB_ENV_PYTHON") or "__no_lab_env__")
lab = pytest.mark.skipif(not LAB_PY.is_file(), reason="SM_LAB_ENV_PYTHON not set or absent")


XY_BEFORE = [{"kind": "xy", "qubit": "control", "operation": "x180"},
             {"kind": "xy", "qubit": "target", "operation": "y90"}]
XY_AFTER = [{"kind": "align"}, {"kind": "xy", "qubit": "control", "operation": "x180"},
            {"kind": "wait", "length": 20}, {"kind": "xy", "qubit": "control", "operation": "x90"}]


def _slot(slots=("control", "coupler"), spectators=False, before=(), after=()):
    return S.render(S.SlotSpec(NAME, frozenset(slots), spectators, before, after))


def _without_coupler_play(source):
    """The class still declares the coupler slot but no longer plays it."""
    lines = source.split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith("        pair.coupler.play("))
    assert lines[start + 2].endswith("duration)")
    return "\n".join(lines[:start] + lines[start + 3:])


@pytest.fixture
def setup(tmp_path, monkeypatch):
    instance = tmp_path / "instance"
    folder = tmp_path / "source" / "quam_config"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("")
    monkeypatch.setattr(config_generator, "known_interpreter", lambda *a: True)
    monkeypatch.setattr(config_generator, "get_lab_code_dir", lambda *a: str(folder.parent))
    calls = []
    answer = {}

    def runner(python, req, **kwargs):
        calls.append(copy.deepcopy(req))
        if req["action"] == "discover":
            return {"ok": True, "folder": str(folder.resolve()), "site_packages": []}
        data = base64.b64decode(req["source"])
        out = {"ok": True, "sha256": D.sha256_bytes(data), "proportional": {"proportional": False}}
        out.update(answer)
        return out

    def save(source, name=NAME):
        D.save_draft(instance, "scope", name, source)
        return Q.save_class("python", instance, "scope", name, runner=runner,
                            confirmed_folder=str(folder.resolve()), history_folder=tmp_path / "history")
    return folder, calls, answer, save


def test_slot_draft_goes_to_the_slot_check_and_has_no_proportional_rule(setup):
    folder, calls, answer, save = setup
    source = _slot()
    result = save(source)
    assert result["ok"], result
    assert [c["action"] for c in calls] == ["discover", "check_slot"]
    assert calls[1]["class_name"] == NAME and calls[1]["filename"] == NAME + ".py"
    assert sorted(calls[1]) == ["action", "chip", "class_name", "copy_root", "filename", "root_class", "source"]
    assert base64.b64decode(calls[1]["source"]).decode("utf-8") == source
    assert (folder / (NAME + ".py")).read_text(encoding="utf-8") == source
    assert result["proportional"] is None and result["proportional_warning"] is None


@pytest.mark.parametrize("spectators", [False, True])
@pytest.mark.parametrize("preset", sorted(S.PRESETS))
def test_every_preset_saves_without_a_chip_argument(setup, preset, spectators):
    folder, calls, answer, save = setup
    slots = S.PRESETS[preset]
    source = _slot(slots, spectators)
    assert save(source)["ok"]
    assert calls[-1]["action"] == "check_slot" and calls[-1].get("chip") is None
    saved = (folder / (NAME + ".py")).read_text(encoding="utf-8")
    assert saved == source and not re.search(r"chip", saved, re.I)
    receipt = json.loads((D.draft_path(folder.parents[1] / "instance", "scope", NAME).parent
                          / Q.W.PUBLISHED_FILE).read_text(encoding="utf-8"))
    assert receipt["proportional"] is None and receipt["qclass"] == f"quam_config.{NAME}.{NAME}"


def test_a_proportional_pulse_draft_is_saved_through_the_pulse_check(setup):
    folder, calls, answer, save = setup
    answer.update(proportional={"proportional": True})
    source = T.render(T.ClassSpec("FlatPulse", [T.FieldSpec("amplitude", "float", 0.1),
                                                T.FieldSpec("length", "int", 40)], "amplitude * np.ones(n)"))
    assert save(source, name="FlatPulse")["ok"]
    assert [c["action"] for c in calls] == ["discover", "check"]


def test_a_failed_slot_check_writes_nothing(setup):
    folder, calls, answer, save = setup
    answer.update(ok=False, error="The slot gate class must play each declared slot once.")
    with pytest.raises(D.DraftError, match="each declared slot"):
        save(_slot())
    assert not (folder / (NAME + ".py")).exists()


def _broken_marker():
    return _slot().replace(S.REGION_END, "    # broken end")


def _variable_xy():
    """The target xy step plays a name the class reads from a variable."""
    source = _slot(before=XY_BEFORE, after=XY_AFTER)
    hidden = source.replace("pair.qubit_target.xy.play('y90')", "pair.qubit_target.xy.play(self.extras['op'])")
    assert hidden != source
    return hidden


def _cut_after_region_start():
    lines = _slot().split("\n")
    return "\n".join(lines[:lines.index(S.REGION_BEGIN) + 1] + [S.REGION_END, ""])


UNREADABLE = {
    "broken region marker": _broken_marker,
    "xy step with a variable name": _variable_xy,
    "hand-edited text": lambda: _slot().replace("Slot duration is in clock cycles", "Slot duration in clock cycles"),
    "another template version": lambda: _slot().replace(f", version {S.TEMPLATE_VERSION}.", ", version 9.", 1),
}

# Drafts the parser answers with the text of an exception it caught: the door words them itself.
DAMAGED_LINE = {
    "blank after the class line": lambda: _slot().replace("(QubitPairMacro):\n", "(QubitPairMacro): \n", 1),
    "damaged slots line": lambda: _slot().replace("    # slots = ", "    # slots = [", 1),
    "cut off after the region start": _cut_after_region_start,
}
OTHER_LINE_ENDS = {
    "windows line ends": lambda: _slot(before=XY_BEFORE).replace("\n", "\r\n"),
    "blanks at the end of the first line": lambda: _slot(before=XY_BEFORE).replace("\n", "  \n", 1),
    "a tab at the end of the first line": lambda: _slot().replace("\n", "\t\n", 1),
    "lone carriage returns": lambda: _slot().replace("\n", "\r"),
    "a lone carriage return after the first line": lambda: _slot().replace("\n", "\r", 1),
    "windows line end on a later line only": lambda: "\r\n".join(_slot().rsplit("\n", 1)),
    "a byte order mark": lambda: "\ufeff" + _slot(),
}
REFUSED = {**UNREADABLE, **DAMAGED_LINE, **OTHER_LINE_ENDS}


def _expected_why(kind, draft):
    if kind in DAMAGED_LINE:
        return Q.SLOT_NOT_AS_WRITTEN
    if kind in OTHER_LINE_ENDS:
        return Q.SLOT_LINE_ENDS
    return S.parse(draft).why


def _tree(root):
    """Every file under *root* with its bytes."""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


@pytest.mark.parametrize("kind", sorted(REFUSED))
def test_a_slot_draft_the_template_cannot_read_back_is_refused_before_any_check(setup, tmp_path, kind):
    """The fake runner would pass every check: the refusal is the door's own,
    and the door and the template parser give one answer for every draft."""
    folder, calls, answer, save = setup
    draft = REFUSED[kind]()
    why = _expected_why(kind, draft)
    assert why and not S.parse(draft).ok and S.HEADER_MARK in draft[:len(S.HEADER_MARK) + 1]
    with pytest.raises(D.DraftError) as refused:
        save(draft)
    assert str(refused.value) == Q.SLOT_UNREADABLE.format(why=why)
    said = str(refused.value)
    assert said.count(": ") == 1 and ". Make the slot gate class again" in said
    for raw in ("NoneType", "<unknown>", "index out of range", "was never closed", "Error", "object"):
        assert raw not in said, raw
    if kind in DAMAGED_LINE:
        # the parser's own answer here is the text of an exception, not a sentence
        assert not S.parse(draft).why.endswith(".")
    if kind in OTHER_LINE_ENDS:
        assert "line ends" in said and "blanks at the end of its first line" in said
    assert "was not saved" in str(refused.value) and "header" not in str(refused.value)
    assert [c["action"] for c in calls] == ["discover"]
    assert _tree(folder) == {"__init__.py": b""} and not (tmp_path / "history").exists()
    # With a saved class in place: the file, its backups, receipt and history stay as they were.
    assert save(_slot())["ok"]
    del calls[:]
    before = _tree(tmp_path)
    with pytest.raises(D.DraftError, match="was not saved"):
        save(draft)
    after = _tree(tmp_path)
    draft_file = str(D.draft_path(tmp_path / "instance", "scope", NAME).relative_to(tmp_path))
    assert after.pop(draft_file) == draft.encode() and before.pop(draft_file) == _slot().encode()
    assert after == before and [c["action"] for c in calls] == ["discover"]


@pytest.mark.parametrize("why, shown", [
    ("Template markers are out of order.", "Template markers are out of order."),
    ("Boom without a full stop", None),
    ("lower case start.", None),
    ("'NoneType' object is not subscriptable", None),
    ("", None),
    (None, None),
])
def test_only_a_sentence_of_the_parser_is_passed_on(monkeypatch, why, shown):
    monkeypatch.setattr(S, "parse", lambda text: S.Parsed(False, why, None))
    assert Q._slot_unreadable(_slot()) == (shown or Q.SLOT_NOT_AS_WRITTEN)


def test_only_a_draft_the_parser_reads_is_saved_as_a_slot_class(setup):
    folder, calls, answer, save = setup
    source = _slot(before=XY_BEFORE)
    assert S.parse(source).ok and Q._slot_unreadable(source) is None
    assert save(source)["ok"]
    saved = (folder / (NAME + ".py")).read_bytes()
    assert saved == source.encode() and S.parse(saved.decode()).ok


def test_a_pulse_draft_with_windows_line_ends_is_saved_through_the_pulse_check(setup):
    folder, calls, answer, save = setup
    source = T.render(T.ClassSpec("FlatPulse", [T.FieldSpec("amplitude", "float", 0.1),
                                                T.FieldSpec("length", "int", 40)],
                                  "amplitude * np.ones(n)")).replace("\n", "\r\n")
    result = save(source, name="FlatPulse")
    assert result["ok"] and result["proportional_warning"] == Q.PROPORTIONAL_WARNING
    assert [c["action"] for c in calls] == ["discover", "check"]
    assert (folder / "FlatPulse.py").read_bytes() == source.encode()


def test_a_saved_slot_class_is_not_listed_as_a_pulse_class_module(setup):
    """The probe's module list is the list of pulse class modules; a pulse class joins it, a gate class does not."""
    from quam_state_manager.core import state_env_schema
    folder, calls, answer, save = setup
    instance = folder.parents[1] / "instance"
    result = save(_slot())
    assert result["ok"] and result["registered"] is None
    assert state_env_schema.load_pulse_modules(instance) == []
    answer.update(proportional={"proportional": True})
    pulse = save(T.render(T.ClassSpec("FlatPulse", [T.FieldSpec("amplitude", "float", 0.1),
                                                    T.FieldSpec("length", "int", 40)], "amplitude * np.ones(n)")),
                 name="FlatPulse")
    assert pulse["registered"] == "quam_config.FlatPulse"
    assert state_env_schema.load_pulse_modules(instance) == ["quam_config.FlatPulse"]


def test_a_slot_header_without_version_is_refused_before_any_check(setup):
    folder, calls, answer, save = setup
    bad = _slot().replace(f", version {S.TEMPLATE_VERSION}.", "", 1)
    with pytest.raises(D.DraftError, match="SM header and template version"):
        save(bad)
    assert [c["action"] for c in calls] == ["discover"]


def test_a_pulse_draft_still_goes_to_the_pulse_check(setup):
    folder, calls, answer, save = setup
    source = T.render(T.ClassSpec("FlatPulse", [T.FieldSpec("amplitude", "float", 0.1),
                                                T.FieldSpec("length", "int", 40)], "amplitude * np.ones(n)"))
    with pytest.raises(D.DraftError, match="proportional"):
        save(source, name="FlatPulse")
    assert calls[-1]["action"] == "check"


def test_an_sm_slot_file_may_be_overwritten_a_lab_file_without_either_header_may_not(setup):
    folder, calls, answer, save = setup
    first = save(_slot())
    assert first["ok"]
    second = save(_slot(("control", "target", "coupler")))
    assert second["ok"] and second["backup"]
    other = folder / "Other.py"
    other.write_text("# lab module\n", encoding="utf-8")
    D.save_draft(Path(folder).parents[1] / "instance", "scope", "Other",
                 S.render(S.SlotSpec("Other", frozenset({"control"}))))
    with pytest.raises(D.DraftError, match="lab class file"):
        save(S.render(S.SlotSpec("Other", frozenset({"control"}))), name="Other")
    assert other.read_text(encoding="utf-8") == "# lab module\n"


def test_an_unpublished_sm_slot_file_is_not_taken_for_a_lab_class(setup):
    folder, calls, answer, save = setup
    instance = folder.parents[1] / "instance"
    D.save_draft(instance, "scope", NAME, _slot())
    qclass = f"quam_config.{NAME}.{NAME}"
    (folder / (NAME + ".py")).write_text("class SlotCz: pass\n", encoding="utf-8")
    assert Q.class_refusal(instance, "scope", classes=[qclass], lab_folder=folder) is None
    (folder / (NAME + ".py")).write_text(_slot(), encoding="utf-8")
    assert Q.class_refusal(instance, "scope", classes=[qclass], lab_folder=folder) == (
        f"Save {NAME} to quam_config before creating a gate that uses it.")


def _worker(tmp_path, source, **more):
    tmp_path = Path(tempfile.mkdtemp(dir=tmp_path))
    root = tmp_path / "copy"
    (root / "quam_config").mkdir(parents=True)
    (root / "quam_config" / "__init__.py").write_text("", encoding="utf-8")
    req = {"action": "check_slot", "source": base64.b64encode(source.encode()).decode("ascii"),
           "class_name": NAME, "filename": NAME + ".py", "copy_root": str(root), "chip": None, **more}
    inp, out = tmp_path / "in.json", tmp_path / "out.json"
    inp.write_text(json.dumps(req), encoding="utf-8")
    subprocess.run([str(LAB_PY), "-B", str(config_generator._script_path("run_class_save.py")),
                    "--in", str(inp), "--out", str(out)], cwd=tmp_path, timeout=300, check=True,
                   env=dict(os.environ, PYTHONPATH=str(root)))
    return json.loads(out.read_text(encoding="utf-8"))


@lab
@pytest.mark.parametrize("preset", sorted(S.PRESETS))
def test_real_worker_plays_every_declared_slot(tmp_path, preset):
    slots = S.PRESETS[preset]
    source = _slot(slots, True)
    result = _worker(tmp_path, source)
    assert result["ok"], result
    assert sorted(result["slots_played"]) == sorted(result["slots_declared"]) == sorted(slots)
    assert result["sha256"] == D.sha256_bytes(source.encode()) and result["proportional"] is None


@lab
def test_real_worker_refuses_a_class_that_does_not_import(tmp_path):
    result = _worker(tmp_path, _slot().replace("from quam.core import", "from quam.nowhere import"))
    assert not result["ok"] and result["error"]


@lab
def test_real_worker_refuses_a_declared_slot_that_is_not_played(tmp_path):
    result = _worker(tmp_path, _without_coupler_play(_slot()))
    assert not result["ok"] and result["error"].startswith("The slot gate class must play each declared slot once")
    assert result["slots_declared"] == ["control", "coupler"] and result["slots_played"] == ["control"]


@lab
def test_real_worker_refuses_a_slot_played_twice(tmp_path):
    source = _slot()
    line = "        align(*channels)\n        pair.qubit_control.z.play("
    assert source.count(line) == 1
    twice = source.replace(line, "        pair.qubit_control.z.play(self.control_flux_pulse_label)\n" + line)
    result = _worker(tmp_path, twice)
    assert not result["ok"] and result["slots_played"].count("control") == 2


@lab
def test_real_worker_gives_the_xy_operations_to_the_class(tmp_path):
    source = _slot(before=XY_BEFORE, after=XY_AFTER)
    result = _worker(tmp_path, source)
    assert result["ok"], result
    assert result["slots_played"] == result["slots_declared"] == ["control", "coupler"]


def _without_target_guard(source):
    lines = source.split("\n")
    start = next(i for i, line in enumerate(lines) if line == "        if 'y90' not in pair.qubit_target.xy.operations:")
    assert lines[start + 1].startswith("            raise ValueError(")
    return "\n".join(lines[:start] + lines[start + 2:])


TARGET_PLAY = "        pair.qubit_target.xy.play('y90')"
XY_FORMS = {
    "variable": (lambda s: s.replace(TARGET_PLAY, "        pair.qubit_target.xy.play(self.extras['op'])"), True),
    "alias": (lambda s: s.replace(TARGET_PLAY, "        txy = pair.qubit_target.xy\n        txy.play('y90')"), True),
    "keyword": (lambda s: s.replace(TARGET_PLAY, "        pair.qubit_target.xy.play(pulse_name='y90')"), True),
    "guard names another operation": (lambda s: s.replace(TARGET_PLAY, "        pair.qubit_target.xy.play('z90')"), True),
    "alias without the guard": (lambda s: _without_target_guard(s).replace(
        TARGET_PLAY, "        txy = pair.qubit_target.xy\n        txy.play('y90')"), False),
}


def _assert_unread_sentence(error, step):
    """The refusal says what the check could not read and blames no qubit of the chip."""
    assert error.startswith("The save check could not tie the xy operation 'y90' that the class asks of the target qubit")
    assert "so the class file was not saved" in error and "quoted name" in error
    assert error.endswith("The check ran on stand-in qubits and did not look at the chip.")
    assert ("sequence step 2 before the gate" in error) is step and re.search(r"line \d+\)", error)
    assert "lacks" not in error and "target_xy" not in error and "not found" not in error


@lab
@pytest.mark.parametrize("form", sorted(XY_FORMS))
def test_real_worker_says_which_xy_operation_it_could_not_read(tmp_path, form):
    change, step = XY_FORMS[form]
    source = _slot(before=XY_BEFORE, after=XY_AFTER)
    changed = change(source)
    assert changed != source
    result = _worker(tmp_path, changed)
    assert not result["ok"]
    _assert_unread_sentence(result["error"], step)


@lab
def test_real_worker_pulse_check_refuses_a_slot_class(tmp_path):
    """The pulse action cannot pass a gate class: it is not a pulse."""
    assert not _worker(tmp_path, _slot(), action="check")["ok"]


@lab
def test_the_door_saves_a_slot_class_with_xy_steps_through_the_real_worker(tmp_path, monkeypatch):
    folder = tmp_path / "source" / "quam_config"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(folder.parent))
    monkeypatch.setattr(config_generator, "known_interpreter", lambda *a: True)
    monkeypatch.setattr(config_generator, "get_lab_code_dir", lambda *a: str(folder.parent))
    instance = tmp_path / "instance"
    source = _slot(("control", "target", "coupler"), True, XY_BEFORE, XY_AFTER)
    D.save_draft(instance, "scope", NAME, source)
    result = Q.save_class(str(LAB_PY), instance, "scope", NAME, confirmed_folder=str(folder.resolve()),
                          history_folder=tmp_path / "history")
    assert result["ok"] and result["proportional"] is None and result["proportional_warning"] is None
    assert (folder / (NAME + ".py")).read_text(encoding="utf-8") == source
    # A class edited so that a declared slot is not played is no longer the template's text.
    D.save_draft(instance, "scope", NAME, _without_coupler_play(source))
    with pytest.raises(D.DraftError, match="Template text does not re-render identically"):
        Q.save_class(str(LAB_PY), instance, "scope", NAME, confirmed_folder=str(folder.resolve()),
                     history_folder=tmp_path / "history")
    assert (folder / (NAME + ".py")).read_text(encoding="utf-8") == source
    # The real worker would pass a class with a broken region marker: the door refuses it first.
    broken = source.replace(S.REGION_END, "    # broken end")
    D.save_draft(instance, "scope", NAME, broken)
    before = _tree(tmp_path / "source"), _tree(tmp_path / "history")
    with pytest.raises(D.DraftError) as refused:
        Q.save_class(str(LAB_PY), instance, "scope", NAME, confirmed_folder=str(folder.resolve()),
                     history_folder=tmp_path / "history")
    assert str(refused.value) == Q.SLOT_UNREADABLE.format(why="Missing or duplicated template markers.")
    assert (_tree(tmp_path / "source"), _tree(tmp_path / "history")) == before
    assert not (instance / "class_file_backups").exists()
    # The same class with Windows line ends would pass the real check: the door refuses it first.
    windows = source.replace("\n", "\r\n")
    D.save_draft(instance, "scope", NAME, windows)
    with pytest.raises(D.DraftError) as refused:
        Q.save_class(str(LAB_PY), instance, "scope", NAME, confirmed_folder=str(folder.resolve()),
                     history_folder=tmp_path / "history")
    assert str(refused.value) == Q.SLOT_UNREADABLE.format(why=Q.SLOT_LINE_ENDS)
    assert (_tree(tmp_path / "source"), _tree(tmp_path / "history")) == before


def _worker_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sm_class_save_worker_under_test",
                                                  config_generator._script_path("run_class_save.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_package(monkeypatch):
    """The worker imports quam_config from its copy: no module of an earlier
    test may answer, and none of this test may stay behind."""
    def forget():
        for name in [m for m in sys.modules if m == "quam_config" or m.startswith("quam_config.")]:
            del sys.modules[name]
    forget()
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield
    forget()


def test_worker_reads_the_xy_operations_the_class_plays():
    found = _worker_module()._xy_operations(_slot(before=XY_BEFORE, after=XY_AFTER))
    assert found == {"control": ["x180", "x90"], "target": ["y90"]}
    assert _worker_module()._xy_operations(_slot(("control", "target", "coupler"), True)) == {}


@pytest.mark.parametrize("played, ok", [(["control", "coupler"], True), (["coupler", "control"], True),
                                        (["control"], False), (["control", "coupler", "coupler"], False),
                                        ([], False)])
def test_worker_slot_check_needs_each_declared_slot_played_once(tmp_path, monkeypatch, clean_package, played, ok):
    seen = []

    def check(request):
        seen.append(request)
        return {"ok": True, "imports": {"ok": True}, "slots_declared": ["control", "coupler"],
                "slots_played": played, "error": None}
    monkeypatch.setitem(sys.modules, "run_slot_gate_check", types.SimpleNamespace(check=check))
    (tmp_path / "quam_config").mkdir()
    (tmp_path / "quam_config" / "__init__.py").write_text("", encoding="utf-8")
    source = "class Plain:\n    def apply(self, pair):\n        pair.qubit_target.xy.play('y90')\n"
    result = _worker_module().run({"action": "check_slot", "source": base64.b64encode(source.encode()).decode(),
                                   "class_name": "Plain", "filename": "Plain.py", "copy_root": str(tmp_path),
                                   "chip": None})
    assert result["ok"] is ok and result["sha256"] == D.sha256_bytes(source.encode())
    assert result["proportional"] is None and (result["error"] is None) is ok
    assert seen[0]["spectators"] == {} and seen[0]["xy_operations"] == {"target": ["y90"]}
    assert seen[0]["class_name"] == "Plain" and Path(seen[0]["file"]).read_bytes() == source.encode()


def test_worker_slot_check_reports_a_class_that_fails_the_check(tmp_path, monkeypatch, clean_package):
    monkeypatch.setitem(sys.modules, "run_slot_gate_check", types.SimpleNamespace(check=lambda request: {
        "ok": False, "imports": {"ok": True}, "slots_declared": ["coupler"], "slots_played": ["coupler"],
        "error": "ValueError: refused", "line": 7}))
    (tmp_path / "quam_config").mkdir()
    (tmp_path / "quam_config" / "__init__.py").write_text("", encoding="utf-8")
    source = "class Plain:\n    pass\n"
    result = _worker_module().run({"action": "check_slot", "source": base64.b64encode(source.encode()).decode(),
                                   "class_name": "Plain", "filename": "Plain.py", "copy_root": str(tmp_path),
                                   "chip": None})
    assert not result["ok"]
    assert result["error"] == "The slot gate class check failed: ValueError: refused (line 7)"


def _run_with_check(tmp_path, monkeypatch, answer):
    monkeypatch.setitem(sys.modules, "run_slot_gate_check", types.SimpleNamespace(check=lambda request: answer))
    (tmp_path / "quam_config").mkdir()
    (tmp_path / "quam_config" / "__init__.py").write_text("", encoding="utf-8")
    source = "class Plain:\n    pass\n"
    return _worker_module().run({"action": "check_slot", "source": base64.b64encode(source.encode()).decode(),
                                 "class_name": "Plain", "filename": "Plain.py", "copy_root": str(tmp_path),
                                 "chip": None})


def test_worker_refuses_a_class_that_declares_no_slot(tmp_path, monkeypatch, clean_package):
    result = _run_with_check(tmp_path, monkeypatch, {"ok": True, "imports": {"ok": True}, "slots_declared": [],
                                                     "slots_played": [], "error": None})
    assert not result["ok"]
    assert result["error"] == "The slot gate class must play each declared slot once: declared [], played []."


@pytest.mark.parametrize("error, step", [
    ("ValueError: " + S.XY_MISSING_PROBLEM.format(number=2, where="before", role="target", operation="y90"), True),
    ("KeyError: \"Operation 'y90' not found in channel 'target_xy'\"", False)])
def test_worker_never_says_a_qubit_lacks_an_xy_operation(tmp_path, monkeypatch, clean_package, error, step):
    """Only stand-in qubits were checked: the class's own sentence about a missing xy operation is not passed on."""
    result = _run_with_check(tmp_path, monkeypatch, {"ok": False, "imports": {"ok": True}, "slots_declared": ["coupler"],
                                                     "slots_played": [], "error": error, "line": 67})
    assert not result["ok"] and "line 67)" in result["error"]
    _assert_unread_sentence(result["error"], step)


@pytest.mark.parametrize("loads", [True, False])
def test_worker_slot_check_loads_the_chip_copy_with_the_draft(tmp_path, monkeypatch, clean_package, loads):
    monkeypatch.setitem(sys.modules, "run_slot_gate_check", types.SimpleNamespace(check=lambda request: {
        "ok": True, "imports": {"ok": True}, "slots_declared": ["coupler"], "slots_played": ["coupler"]}))
    (tmp_path / "quam_config").mkdir()
    (tmp_path / "quam_config" / "__init__.py").write_text(
        "class Quam:\n    @classmethod\n    def load(cls, path):\n"
        "        if not %r:\n            raise ValueError('chip refused ' + path)\n" % loads, encoding="utf-8")
    source = "class Plain:\n    pass\n"
    result = _worker_module().run({"action": "check_slot", "source": base64.b64encode(source.encode()).decode(),
                                   "class_name": "Plain", "filename": "Plain.py", "copy_root": str(tmp_path),
                                   "chip": "chip_copy", "root_class": "quam_config.Quam"})
    assert result["ok"] is loads and result.get("chip_loaded", False) is loads
    assert loads or result["chip_error"] == "ValueError: chip refused chip_copy"


# ---------------------------------------------------------------- slot pointers on a real chip

#: Gate fields as the gate plan stores them: references to operations of each slot's own channel.
POINTER_FIELDS = {"control_flux_pulse": "#/qubits/q1/z/operations/cz_unipolar_flux_pulse_q1_q2",
                  "target_flux_pulse": "#/qubits/q2/z/operations/const",
                  "coupler_flux_pulse": "#/qubit_pairs/q1-2/coupler/operations/cz_unipolar_coupler_pulse_q1_q2"}

_POINTER_RUN = r'''
import importlib, json, sys
folder = sys.argv[1]
root = json.load(open(folder + "/state.json", encoding="utf-8"))["__class__"]
module, name = root.rsplit(".", 1)
machine = getattr(importlib.import_module(module), name).load(folder)
from qm.qua import program
out = {}
for gate, macro in machine.qubit_pairs["q1-2"].macros.items():
    if not gate.startswith("slot_"):
        continue
    try:
        labels = {s: getattr(macro, s + "_flux_pulse_label") for s in ("control", "target", "coupler")
                  if hasattr(macro, s + "_flux_pulse")}
        with program() as prog:
            macro.apply()
        out[gate] = {"ok": True, "labels": labels}
    except Exception as exc:
        out[gate] = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
print("RESULT " + json.dumps(out))
'''


@lab
def test_lab_gate_with_slot_pointers_plays_on_a_built_chip():
    """A control-slot class and a control + target + coupler class, stored with references as the plan
    writes them, load with the lab code and apply inside a QUA program; a control reference to another
    channel's operation is refused with the slot sentence."""
    from tests.test_coupler_only_live import _build, _spec
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        spec = _spec(quam_class=None)
        spec.pop("populate")
        spec["pair_gate"] = "cz_fixed"
        spec["lines"] = spec["lines"] + [{"element": q, "line": "flux", "channel": {"kind": "lf_fem"}}
                                         for q in spec["qubits"]]
        built = tmp / "built"
        _build(spec, built)
        package = tmp / "slot_pointer_classes"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        classes = {"ControlGate": {"control"}, "FullGate": set(S.SLOTS)}
        for name, slots in classes.items():
            (package / f"{name}.py").write_text(S.render(S.SlotSpec(name, slots)), encoding="utf-8")
        state = json.loads((built / "state.json").read_text(encoding="utf-8"))
        macros = state["qubit_pairs"]["q1-2"]["macros"]
        for name, slots in classes.items():
            macros[f"slot_{name}"] = {"__class__": f"slot_pointer_classes.{name}.{name}",
                                      **{f"{s}_flux_pulse": POINTER_FIELDS[f"{s}_flux_pulse"] for s in slots}}
        macros["slot_other_channel"] = {"__class__": "slot_pointer_classes.ControlGate.ControlGate",
                                        "control_flux_pulse": POINTER_FIELDS["target_flux_pulse"]}
        (built / "state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(tmp), PYTHONUTF8="1")
        run = subprocess.run([str(LAB_PY), "-c", _POINTER_RUN, str(built)], capture_output=True, text=True,
                             encoding="utf-8", timeout=900, env=env)
        assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-4000:]
        result = json.loads(next(ln for ln in run.stdout.splitlines() if ln.startswith("RESULT "))[7:])
        assert result["slot_ControlGate"] == {"ok": True, "labels": {"control": "cz_unipolar_flux_pulse_q1_q2"}}
        assert result["slot_FullGate"] == {"ok": True, "labels": {
            "control": "cz_unipolar_flux_pulse_q1_q2", "target": "const",
            "coupler": "cz_unipolar_coupler_pulse_q1_q2"}}, result
        other = result["slot_other_channel"]
        assert not other["ok"] and other["error"].startswith(
            "ValueError: " + S.SLOT_PROBLEM.split("{")[0] + "control slot accepts " + S.SLOT_KINDS["control"]), other
