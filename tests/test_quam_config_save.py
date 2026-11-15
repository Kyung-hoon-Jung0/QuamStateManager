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
    assert "restart QUAlibrate" in result["restart"]
    assert "without isolation" in result["notice"]


def test_existing_name_is_refused_unchanged(setup):
    instance, folder, source, calls, runner, save = setup
    target = folder / "FlatPulse.py"
    target.write_bytes(b"# existing module\n")
    before = D.sha256_bytes(target.read_bytes())
    with pytest.raises(D.DraftError, match="already exists"):
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
