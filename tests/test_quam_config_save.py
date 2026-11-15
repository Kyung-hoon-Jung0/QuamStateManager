"""Class publication order, copy validation, authorship and creation readiness."""
import base64
import copy
import json
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator, safe_io
from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_template as T
from quam_state_manager.core import pulse_class_watch as W
from quam_state_manager.core import quam_config_save as Q
from quam_state_manager.core.loader import QuamStore

CLASS = "FlatPulse"
QCLASS = "quam_config.FlatPulse.FlatPulse"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    instance = tmp_path / "instance"
    folder = tmp_path / "source" / "quam_config"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("")
    monkeypatch.setattr(config_generator, "known_interpreter", lambda *a: True)
    monkeypatch.setattr(config_generator, "get_lab_code_dir", lambda *a: str(folder.parent))
    source = T.render(T.ClassSpec(CLASS, [T.FieldSpec("amplitude", "float", 0.1),
                                        T.FieldSpec("length", "int", 40)], "amplitude * np.ones(n)"))
    D.save_draft(instance, "scope", CLASS, source)
    calls = []

    def runner(python, req, **kwargs):
        calls.append((copy.deepcopy(req), kwargs))
        if req["action"] == "discover":
            return {"ok": True, "folder": str(folder.resolve()), "site_packages": []}
        if req["action"] == "load":
            return {"ok": True, "chip_loaded": True}
        data = base64.b64decode(req["source"])
        if req.get("chip"):
            assert Path(req["chip"]).is_dir()
            assert Path(req["chip"]) != folder
        assert (Path(req["copy_root"]) / "quam_config" / "__init__.py").exists()
        return {"ok": True, "sha256": D.sha256_bytes(data), "chip_loaded": bool(req.get("chip")),
                "proportional": {"proportional": True}}

    def save(**kwargs):
        return Q.save_class("python", instance, "scope", CLASS, runner=runner,
                            history_folder=tmp_path / "history", **kwargs)
    return instance, folder, source.encode(), calls, runner, save


def test_confirmation_precedes_draft_read_and_has_absolute_env_folder(setup, monkeypatch):
    instance, folder, source, calls, runner, save = setup
    monkeypatch.setattr(D, "load_draft", lambda *a: pytest.fail("draft read before confirmation"))
    result = save()
    assert result["confirm"]["folder"] == str(folder.resolve())
    assert calls == [({"action": "discover"}, {"natural": True})]


def test_one_new_file_header_bytes_hash_and_history(setup):
    instance, folder, source, calls, runner, save = setup
    result = save(confirmed_folder=str(folder.resolve()), actor="agent")
    assert result["ok"]
    target = folder / "FlatPulse.py"
    assert target.read_bytes() == source
    assert "State Manager" in source.decode().splitlines()[0] and "version 1" in source.decode().splitlines()[0]
    assert sorted(p.name for p in folder.iterdir()) == ["FlatPulse.py", "__init__.py"]
    assert result["sha256"] == D.sha256_bytes(source)
    rows = [json.loads(line) for line in (instance.parent / "history" / "class_files.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["actor"] == "agent"
    assert rows[0]["path"] == str(target) and rows[0]["sha256"] == D.sha256_bytes(source)
    assert result["restart"] == Q.RESTART_NEW and "no restart" in result["restart"]
    assert "without isolation" in result["notice"]


def test_existing_name_is_refused_unchanged(setup):
    instance, folder, source, calls, runner, save = setup
    target = folder / "FlatPulse.py"
    target.write_bytes(b"# existing module\n")
    before = D.sha256_bytes(target.read_bytes())
    with pytest.raises(D.DraftError, match="lab class file the environment already imports. "
                                           "Pick another class name"):
        save(confirmed_folder=str(folder.resolve()))
    assert D.sha256_bytes(target.read_bytes()) == before
    assert not any(c[0]["action"] == "check" for c in calls)


@pytest.mark.parametrize("error", ["TypeError: required default missing", "ValueError: waveform failed"])
def test_failed_check_cannot_write_even_if_import_succeeded(setup, monkeypatch, error):
    instance, folder, source, calls, runner, save = setup
    def failed(py, req, **kwargs):
        if req["action"] == "discover":
            return runner(py, req, **kwargs)
        assert not (folder / "FlatPulse.py").exists()
        return {"ok": False, "error": error}
    monkeypatch.setattr(Q, "_run", failed)
    with pytest.raises(D.DraftError, match=error):
        Q.save_class("python", instance, "scope", CLASS, confirmed_folder=str(folder.resolve()),
                     history_folder=instance / "history")
    assert not (folder / "FlatPulse.py").exists()


def test_draft_read_once_and_racing_edit_cannot_change_checked_or_written_hash(setup, monkeypatch):
    instance, folder, source, calls, runner, save = setup
    original = D.load_draft
    reads = []
    def read(*args):
        reads.append(args)
        return original(*args)
    def racing(py, req, **kwargs):
        result = runner(py, req, **kwargs)
        if req["action"] == "check":
            D.save_draft(instance, "scope", CLASS, source + b"# edited during check\n")
        return result
    monkeypatch.setattr(D, "load_draft", read)
    result = Q.save_class("python", instance, "scope", CLASS, runner=racing,
                          confirmed_folder=str(folder.resolve()), history_folder=instance / "history")
    checked = base64.b64decode(calls[-1][0]["source"])
    assert len(reads) == 1
    assert (folder / "FlatPulse.py").read_bytes() == checked == source
    assert result["sha256"] == D.sha256_bytes(checked)


def _store():
    return QuamStore.from_dicts({"qubit_pairs": {"pair": {"macros": {
        "cz_flat": {"__class__": "Gate", "pulse": {"__class__": QCLASS, "length": 40}},
        "cz": "#./cz_flat"}}}}, {})


def test_in_use_copy_load_refusal_and_backup_and_exact_gate_history(setup, monkeypatch):
    instance, folder, source, calls, runner, save = setup
    save(confirmed_folder=str(folder.resolve()))
    store = _store()
    before = copy.deepcopy(store.merged), list(store.change_log)
    preview = save(store=store, live=store.merged)["confirm"]
    assert preview["gates"] == [{"pair": "pair", "gate": "cz_flat", "live_active": True}]
    changed = source.replace(b"0.1", b"0.2")
    D.save_draft(instance, "scope", CLASS, changed)
    def failed(py, req, **kwargs):
        if req["action"] == "check" and req.get("chip"):
            return {"ok": False, "error": "TypeError: removed a field"}
        return runner(py, req, **kwargs)
    monkeypatch.setattr(Q, "_run", failed)
    with pytest.raises(D.DraftError, match="removed a field"):
        Q.save_class("python", instance, "scope", CLASS, store=store,
                     confirmed_folder=str(folder.resolve()), history_folder=instance / "history")
    assert (folder / "FlatPulse.py").read_bytes() == source
    result = save(confirmed_folder=str(folder.resolve()), store=store, live=store.merged)
    assert Path(result["backup"]).is_relative_to(instance)
    assert Path(result["backup"]).read_bytes() == source
    assert (folder / "FlatPulse.py").read_bytes() == changed
    assert (store.merged, store.change_log) == before
    rows = (instance.parent / "history" / "class_files.jsonl").read_text().splitlines()
    assert len(rows) == 2 and json.loads(rows[-1])["gates"] == preview["gates"]
    assert "Restart QUAlibrate" in result["restart"]
    chip_paths = [c[0]["chip"] for c in calls if c[0].get("chip")]
    assert chip_paths and all(not Path(p).exists() for p in chip_paths)


def test_live_active_requires_the_live_gate_to_use_the_class(setup):
    store = _store()
    live = copy.deepcopy(store.merged)
    live["qubit_pairs"]["pair"]["macros"]["cz_flat"]["pulse"]["__class__"] = "Other"
    assert not Q.affected_gates(store.merged, QCLASS, live)[0]["live_active"]


def test_history_failure_keeps_published_file_and_reports_it(setup, monkeypatch):
    instance, folder, source, calls, runner, save = setup
    def failed(*a, **kw):
        raise OSError("history unavailable")
    monkeypatch.setattr(Q, "record_write", failed)
    result = save(confirmed_folder=str(folder.resolve()))
    assert result["ok"] and (folder / "FlatPulse.py").read_bytes() == source
    assert "history unavailable" in result["record_error"]


def test_failed_publication_leaves_no_half_file_or_temp(setup, monkeypatch):
    instance, folder, source, calls, runner, save = setup
    def fail(*args):
        raise OSError("publish failed")
    monkeypatch.setattr(safe_io.os, "link", fail)
    with pytest.raises(OSError, match="publish failed"):
        save(confirmed_folder=str(folder.resolve()))
    assert sorted(p.name for p in folder.iterdir()) == ["__init__.py"]
    assert not (instance.parent / "history" / "class_files.jsonl").exists()


def test_atomic_create_refuses_a_racing_destination(tmp_path, monkeypatch):
    path = tmp_path / "class.py"
    original = safe_io.os.link
    def race(source, target):
        path.write_bytes(b"other writer")
        original(source, target)
    monkeypatch.setattr(safe_io.os, "link", race)
    with pytest.raises(FileExistsError):
        safe_io.atomic_write_bytes(path, b"new bytes", create_only=True)
    assert path.read_bytes() == b"other writer"
    assert list(tmp_path.iterdir()) == [path]


def test_destination_mismatch_and_override_and_site_packages(setup, monkeypatch):
    instance, folder, source, calls, runner, save = setup
    monkeypatch.setattr(config_generator, "get_lab_code_dir", lambda *a: str(folder.parent / "other"))
    with pytest.raises(D.DraftError) as error:
        save()
    assert str(folder) in str(error.value) and str(folder.parent / "other") in str(error.value)
    monkeypatch.setattr(config_generator, "get_lab_code_dir", lambda *a: str(folder.parent))
    assert save()["confirm"]["folder"] == str(folder)
    other = folder.parent / "alternative"
    other.mkdir()
    result = save(folder=str(other), confirmed_folder=str(other))
    assert result["ok"] and "does not import" in result["warning"]
    creates = [("gate", {"__class__": QCLASS})]
    assert "does not import" in Q.class_refusal(instance, "scope", creates)
    def installed(py, req, **kwargs):
        result = runner(py, req, **kwargs)
        result["site_packages"] = [str(folder.parent)]
        return result
    with pytest.raises(D.DraftError, match="site-packages"):
        Q.destination("python", runner=installed)
    monkeypatch.setattr(Q.os, "access", lambda *a: False)
    with pytest.raises(D.DraftError, match="not writable"):
        Q.destination("python", runner=runner)


def test_gate_class_requires_publication_and_override_remains_blocked(setup):
    instance, folder, source, calls, runner, save = setup
    creates = [("gate", {"__class__": "Gate", "pulse": {"__class__": QCLASS}})]
    assert "Save FlatPulse" in Q.class_refusal(instance, "scope", creates)
    save(confirmed_folder=str(folder.resolve()))
    assert Q.class_refusal(instance, "scope", creates) is None
    assert Q.class_refusal(instance, "scope", [("gate", {"__class__": "Unrelated"})]) is None


def test_wrong_checked_hash_is_refused(setup):
    instance, folder, source, calls, runner, save = setup
    def wrong(py, req, **kwargs):
        result = runner(py, req, **kwargs)
        if req["action"] == "check":
            result["sha256"] = "wrong"
        return result
    with pytest.raises(D.DraftError, match="different source bytes"):
        Q.save_class("python", instance, "scope", CLASS, runner=wrong,
                     confirmed_folder=str(folder), history_folder=instance / "history")
    assert not (folder / "FlatPulse.py").exists()


def test_draft_without_header_and_unknown_environment_are_refused(setup, monkeypatch):
    instance, folder, source, calls, runner, save = setup
    D.save_draft(instance, "scope", CLASS, "class FlatPulse: pass")
    with pytest.raises(D.DraftError, match="header"):
        save(confirmed_folder=str(folder))
    monkeypatch.setattr(config_generator, "known_interpreter", lambda *a: False)
    with pytest.raises(D.DraftError, match="environment"):
        save()


def _not_proportional(runner):
    def run(py, req, **kwargs):
        result = runner(py, req, **kwargs)
        if req["action"] == "check":
            result["proportional"] = {"proportional": False}
        return result
    return run


def test_a_template_class_must_scale_with_amplitude(setup):
    instance, folder, source, calls, runner, save = setup
    with pytest.raises(D.DraftError, match="proportional"):
        Q.save_class("python", instance, "scope", CLASS, runner=_not_proportional(runner),
                     confirmed_folder=str(folder), history_folder=instance / "history")
    assert not (folder / "FlatPulse.py").exists()


def test_a_hand_edited_class_that_does_not_scale_is_saved_with_a_warning(setup):
    instance, folder, source, calls, runner, save = setup
    edited = source.decode().replace(T.REGION_END + "\n", "")
    assert not T.parse(edited).editable
    D.save_draft(instance, "scope", CLASS, edited)
    result = Q.save_class("python", instance, "scope", CLASS, runner=_not_proportional(runner),
                          confirmed_folder=str(folder), history_folder=instance / "history")
    assert result["ok"] and result["proportional_warning"] == Q.PROPORTIONAL_WARNING
    assert (folder / "FlatPulse.py").read_bytes() == edited.encode()


def test_save_to_file_elsewhere_keeps_the_publication(setup, tmp_path, monkeypatch):
    instance, folder, source, calls, runner, save = setup
    monkeypatch.setattr(D, "import_check", lambda *a, **k: None)
    save(confirmed_folder=str(folder))
    published = folder / "FlatPulse.py"
    copy_path = tmp_path / "elsewhere" / "FlatPulse.py"
    copy_path.parent.mkdir()
    assert W.save_to_target(instance, "scope", CLASS, str(copy_path))["ok"]
    creates = [("gate", {"__class__": "Gate", "pulse": {"__class__": QCLASS}})]
    assert Q.class_refusal(instance, "scope", creates) is None
    with pytest.raises(D.DraftError, match="Save to quam_config"):
        W.save_to_target(instance, "scope", CLASS, str(published))
    assert published.read_bytes() == source
    changed = source.replace(b"0.1", b"0.3")
    D.save_draft(instance, "scope", CLASS, changed)
    result = save(confirmed_folder=str(folder))
    assert result["ok"] and published.read_bytes() == changed
    assert Path(result["backup"]).read_bytes() == source
    assert Q.class_refusal(instance, "scope", creates) is None


def test_save_to_file_refuses_the_published_file_in_any_folder(setup, monkeypatch):
    instance, folder, source, calls, runner, save = setup
    monkeypatch.setattr(D, "import_check", lambda *a, **k: None)
    other = folder.parent / "plain"
    other.mkdir()
    assert save(folder=str(other), confirmed_folder=str(other))["ok"]
    with pytest.raises(D.DraftError, match="Save to quam_config"):
        W.save_to_target(instance, "scope", CLASS, str(other / "FlatPulse.py"))


def test_save_to_file_refuses_a_quam_config_folder_without_init(setup, tmp_path, monkeypatch):
    instance, folder, source, calls, runner, save = setup
    monkeypatch.setattr(D, "import_check", lambda *a, **k: None)
    namespace = tmp_path / "ns" / "quam_config"
    namespace.mkdir(parents=True)
    with pytest.raises(D.DraftError, match="quam_config folder"):
        W.save_to_target(instance, "scope", CLASS, str(namespace / "FlatPulse.py"))
    assert list(namespace.iterdir()) == []


def _chip_runner(runner, *, old=None, draft=None):
    """*old* / *draft*: the load error of the chip copy with that file, or None."""
    def run(py, req, **kwargs):
        if req["action"] == "load":
            return {"ok": old is None, "chip_loaded": old is None, "error": old}
        result = runner(py, req, **kwargs)
        if req["action"] == "check" and req.get("chip") and draft:
            result.update(ok=False, chip_loaded=False, chip_error=draft,
                          error="The chip copy does not load with the draft class: " + draft)
        return result
    return run


def _history_dir(instance):
    return Path(instance) / "history"


def _in_use(setup):
    instance, folder, source, calls, runner, save = setup
    save(confirmed_folder=str(folder))
    changed = source.replace(b"0.1", b"0.4")
    D.save_draft(instance, "scope", CLASS, changed)
    return _store(), changed


def _save_used(setup, store, run):
    instance, folder = setup[0], setup[1]
    return Q.save_class("python", instance, "scope", CLASS, runner=run, store=store,
                        confirmed_folder=str(folder), history_folder=_history_dir(instance))


def test_a_chip_that_loads_with_the_old_file_refuses_a_breaking_draft(setup):
    instance, folder, source, calls, runner, save = setup
    store, changed = _in_use(setup)
    run = _chip_runner(runner, draft="AttributeError: Unexpected attribute width")
    with pytest.raises(D.DraftError, match="Unexpected attribute"):
        _save_used(setup, store, run)
    assert (folder / "FlatPulse.py").read_bytes() == source


def test_a_chip_broken_before_the_change_saves_with_the_old_load_error(setup):
    instance, folder, source, calls, runner, save = setup
    store, changed = _in_use(setup)
    order = []
    missing = "ImportError: no module quam_config.Missing"
    inner = _chip_runner(runner, old=missing, draft=missing)
    def run(py, req, **kwargs):
        order.append(req["action"])
        if req["action"] == "load":
            assert "source" not in req and req["chip"]
            assert (Path(req["copy_root"]) / "quam_config" / "FlatPulse.py").read_bytes() == source
        return inner(py, req, **kwargs)
    result = _save_used(setup, store, run)
    assert order == ["discover", "load", "check"]
    assert result["ok"] and (folder / "FlatPulse.py").read_bytes() == changed
    assert "could not judge" in result["chip_warning"]
    assert "quam_config.Missing" in result["chip_warning"]
    row = json.loads((_history_dir(instance) / Q.HISTORY_FILE).read_text().splitlines()[-1])
    assert row["warning"] == result["chip_warning"]


def test_a_repair_of_a_broken_chip_is_saved(setup):
    instance, folder, source, calls, runner, save = setup
    store, changed = _in_use(setup)
    run = _chip_runner(runner, old="AttributeError: Unexpected attribute width")
    result = _save_used(setup, store, run)
    assert result["ok"] and (folder / "FlatPulse.py").read_bytes() == changed
    assert "loads with this draft" in result["chip_warning"]


def test_a_draft_that_fails_to_import_is_refused_even_on_a_broken_chip(setup):
    instance, folder, source, calls, runner, save = setup
    store, changed = _in_use(setup)
    broken = _chip_runner(runner, old="ImportError: other class")
    def run(py, req, **kwargs):
        result = broken(py, req, **kwargs)
        if req["action"] == "check":
            result.update(ok=False, error="SyntaxError: bad draft")
        return result
    with pytest.raises(D.DraftError, match="SyntaxError"):
        _save_used(setup, store, run)
    assert (folder / "FlatPulse.py").read_bytes() == source


def test_a_used_class_needs_the_chip_loaded_with_the_draft(setup):
    instance, folder, source, calls, runner, save = setup
    store, changed = _in_use(setup)
    def run(py, req, **kwargs):
        result = runner(py, req, **kwargs)
        if req["action"] == "check":
            result.pop("chip_loaded", None)
        return result
    with pytest.raises(D.DraftError, match="not loaded with the draft"):
        _save_used(setup, store, run)
    assert (folder / "FlatPulse.py").read_bytes() == source


def test_a_class_file_changed_during_the_check_is_not_overwritten(setup):
    instance, folder, source, calls, runner, save = setup
    store, changed = _in_use(setup)
    def run(py, req, **kwargs):
        if req["action"] == "check":
            (folder / "FlatPulse.py").write_bytes(source + b"# lab edit\n")
        return runner(py, req, **kwargs)
    with pytest.raises(D.DraftError, match="changed during the check"):
        _save_used(setup, store, run)
    assert (folder / "FlatPulse.py").read_bytes() == source + b"# lab edit\n"


def test_live_active_needs_the_active_pointer(setup):
    store = _store()
    live = copy.deepcopy(store.merged)
    live["qubit_pairs"]["pair"]["macros"]["cz_other"] = {"__class__": "Gate"}
    live["qubit_pairs"]["pair"]["macros"]["cz"] = "#./cz_other"
    gates = Q.affected_gates(store.merged, QCLASS, live)
    assert gates == [{"pair": "pair", "gate": "cz_flat", "live_active": False}]


def test_a_draft_named_like_a_lab_class_does_not_block_its_gates(setup):
    instance, folder, source, calls, runner, save = setup
    creates = [("gate", {"__class__": "Gate", "pulse": {"__class__": QCLASS}})]
    lab = folder / "FlatPulse.py"
    lab.write_bytes(b"class FlatPulse: pass\n")
    assert Q.class_refusal(instance, "scope", creates, lab_folder=folder) is None
    assert Q.class_refusal(instance, "scope", classes=[QCLASS], lab_folder=folder) is None
    assert "Save FlatPulse" in Q.class_refusal(instance, "scope", creates)
    lab.write_bytes(source)
    assert "Save FlatPulse" in Q.class_refusal(instance, "scope", creates, lab_folder=folder)
    assert Q.lab_package("python") == folder


def test_the_saved_module_is_registered_only_when_the_environment_imports_it(setup):
    from quam_state_manager.core import state_env_schema
    instance, folder, source, calls, runner, save = setup
    other = folder.parent / "plain"
    other.mkdir()
    result = save(folder=str(other), confirmed_folder=str(other))
    assert result["registered"] is None
    assert state_env_schema.load_pulse_modules(instance) == []
    result = save(confirmed_folder=str(folder))
    assert result["registered"] == "quam_config.FlatPulse"
    assert state_env_schema.load_pulse_modules(instance) == ["quam_config.FlatPulse"]


def test_typing_the_environment_folder_still_compares_with_sm_lab_folder(setup, monkeypatch):
    instance, folder, source, calls, runner, save = setup
    monkeypatch.setattr(config_generator, "get_lab_code_dir", lambda *a: str(folder.parent / "other"))
    with pytest.raises(D.DraftError, match="but SM checks"):
        save(folder=str(folder), confirmed_folder=str(folder))
    assert not (folder / "FlatPulse.py").exists()


def test_an_sm_file_this_draft_did_not_publish_is_refused_unchanged(setup):
    instance, folder, source, calls, runner, save = setup
    target = folder / "FlatPulse.py"
    other = source + b"# from another computer\n"
    target.write_bytes(other)
    with pytest.raises(D.DraftError, match="already exists"):
        save(confirmed_folder=str(folder))
    assert target.read_bytes() == other
