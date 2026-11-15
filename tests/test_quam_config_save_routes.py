"""The Save to quam_config door through Flask: a gate that uses an SM class
waits for the class file, every class-file write leaves one history line,
and Save to file cannot bypass the checked door."""
import base64
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator
from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_watch as W
from quam_state_manager.core import quam_config_save as Q
from tests.test_cz_gate_routes import PLAN, App, _body

CLASS = "FlatPulse"
QCLASS = f"quam_config.{CLASS}.{CLASS}"


@pytest.fixture
def env(monkeypatch, tmp_path):
    tmp = str(tmp_path)
    folder = Path(tmp) / "source" / "quam_config"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("")
    monkeypatch.setattr(config_generator, "known_interpreter", lambda *a, **k: True)
    monkeypatch.setattr(config_generator, "get_lab_code_dir", lambda *a: str(folder.parent))
    checks = []

    def runner(python, req, **kwargs):
        if req["action"] == "discover":
            return {"ok": True, "folder": str(folder.resolve()), "site_packages": []}
        if req["action"] == "load":
            return {"ok": True, "chip_loaded": True}
        checks.append(req)
        data = base64.b64decode(req["source"])
        return {"ok": True, "sha256": D.sha256_bytes(data), "chip_loaded": bool(req.get("chip")),
                "proportional": {"proportional": True}}
    monkeypatch.setattr(Q, "_run", runner)
    from quam_state_manager.web import port_play_page
    monkeypatch.setattr(port_play_page, "_chosen_env", lambda _: ("py", ""))
    W._WATCHES.clear()
    a = App(tmp)
    r = a.c.post("/pulse-class/new", json={"cls": CLASS})
    assert r.status_code == 200, r.get_json()
    a.folder = folder.resolve()
    a.checks = checks
    yield a
    from quam_state_manager.core.hub_index import close_readers
    close_readers()


def _history(a):
    from quam_state_manager.core import gate_check_log
    folder = gate_check_log.chip_dir(a.app.config["history_manager"], a.ctx["path"])
    p = Path(folder) / Q.HISTORY_FILE
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def _save(a, headers=None):
    first = a.c.post("/pulse-class/save-config", json={"cls": CLASS, "python": "py"},
                     headers=headers or {}).get_json()
    assert first["confirm"]["folder"] == str(a.folder)
    return a.c.post("/pulse-class/save-config", json={"cls": CLASS, "python": "py",
                                                      "confirmed_folder": first["confirm"]["folder"]},
                    headers=headers or {})


def _sm_class_body():
    b = _body()
    b["pulses"]["flux_pulse_qubit"]["class"] = QCLASS
    return b


def test_a_gate_with_an_unsaved_sm_class_is_refused_until_the_class_is_saved(env):
    before = env.snap()
    j = env.c.post(PLAN, json=_sm_class_body()).get_json()
    refusals = " ".join(j["pairs"][0].get("refusals") or [])
    assert f"Save {CLASS} to quam_config" in refusals
    r = _save(env)
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["restart"] == Q.RESTART_NEW and "QUAlibrate" in out["restart"]
    assert f"quam_config/{CLASS}.py" in out["dependency"]
    j = env.c.post(PLAN, json=_sm_class_body()).get_json()
    assert f"Save {CLASS}" not in " ".join(j["pairs"][0].get("refusals") or [])
    assert env.snap() == before                     # the save writes no state and no extras


def test_save_to_quam_config_records_who_path_and_hash(env):
    r = _save(env, headers={"X-SM-Agent": "by_claude"})
    assert r.status_code == 200, r.get_json()
    rows = _history(env)
    target = env.folder / f"{CLASS}.py"
    assert len(rows) == 1 and rows[0]["actor"] == "agent"
    assert rows[0]["path"] == str(target) and rows[0]["sha256"] == D.sha256_bytes(target.read_bytes())
    assert D.sha256_bytes(base64.b64decode(env.checks[-1]["source"])) == rows[0]["sha256"]


def test_save_to_file_records_a_history_line_and_cannot_bypass_the_checked_door(env, tmp_path,
                                                                                monkeypatch):
    monkeypatch.setattr(D, "import_check", lambda *a, **k: None)
    outside = tmp_path / "copy.py"
    r = env.c.post("/pulse-class/save-file", json={"cls": CLASS, "target": str(outside)}).get_json()
    assert r["ok"] and r["record_error"] is None
    rows = _history(env)
    assert rows[-1]["actor"] == "person" and rows[-1]["path"] == str(outside)
    assert rows[-1]["sha256"] == D.sha256_bytes(outside.read_bytes())
    package = env.folder / "Other.py"
    r = env.c.post("/pulse-class/save-file", json={"cls": CLASS, "target": str(package)})
    assert r.status_code == 400 and "Save to quam_config" in r.get_json()["error"]
    assert not package.exists()
    assert _save(env).status_code == 200
    published = env.folder / f"{CLASS}.py"
    before = published.read_bytes()
    r = env.c.post("/pulse-class/save-file", json={"cls": CLASS, "target": str(published)})
    assert r.status_code == 400 and "Save to quam_config" in r.get_json()["error"]
    assert published.read_bytes() == before


def test_a_lab_class_named_like_a_draft_is_not_refused_by_plan(env):
    (env.folder / f"{CLASS}.py").write_text("class FlatPulse: pass\n", encoding="utf-8")
    j = env.c.post(PLAN, json=_sm_class_body()).get_json()
    assert f"Save {CLASS}" not in " ".join(j["pairs"][0].get("refusals") or [])
    r = _save(env)
    assert r.status_code == 400 and "Pick another class name" in r.get_json()["error"]


@pytest.mark.parametrize("url,headers", [
    ("/pulse-class/save-config", {}),
    ("/api/agent/class-file/save", {"X-SM-Agent": "codex"}),
])
def test_save_config_refuses_during_a_queue_then_saves_the_checked_bytes(env, monkeypatch,
                                                                       url, headers):
    from quam_state_manager.web import port_play_page, routes

    monkeypatch.setattr(port_play_page, "_chosen_env", lambda _: ("py", ""))
    body = {"cls": CLASS, "python": "py"}
    first = env.c.post(url, json=body, headers=headers)
    assert first.status_code == 409, first.get_json()
    body["confirmed_folder"] = first.get_json()["confirm"]["folder"]
    before = {p.name: p.read_bytes() for p in env.folder.iterdir()}
    monkeypatch.setattr(routes.scheduler, "is_active", lambda _: True)
    refused = env.c.post(url, json=body, headers=headers)
    assert refused.status_code == 409
    assert refused.get_json()["error"] == "scheduler_running"
    assert {p.name: p.read_bytes() for p in env.folder.iterdir()} == before
    assert not env.checks
    assert not _history(env)

    monkeypatch.setattr(routes.scheduler, "is_active", lambda _: False)
    saved = env.c.post(url, json=body, headers=headers)
    assert saved.status_code == 200, saved.get_json()
    assert saved.get_json()["ok"] and saved.get_json()["restart"] == Q.RESTART_NEW
    target = env.folder / f"{CLASS}.py"
    assert target.read_bytes() == base64.b64decode(env.checks[-1]["source"])


def test_queue_refusal_sentence(env, monkeypatch):
    from quam_state_manager.web import routes
    monkeypatch.setattr(routes.scheduler, "is_active", lambda _: True)
    response = env.c.post("/pulse-class/save-config", json={"cls": CLASS})
    assert response.status_code == 409
    assert response.get_json()["message"] == (
        "Editing and class saves are refused because the Experiment Runner is running; "
        "try again after it finishes or is cancelled.")


def _post_file(env, target):
    return env.c.post("/pulse-class/save-file", json={"cls": CLASS, "target": str(target)})


def _package_sentence(parent):
    return W.PACKAGE_SENTENCE.format(parent=parent)


def _tree(root):
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


def _junction(link, target):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


NAME_RULE_CASES = ["direct", "deeper", "upper_case", "archive", "lab_folder_not_env_import",
                   "dotdot_typed", "dotdot_out_of_package", "junction_into_package",
                   "junction_named_quam_config", "sibling", "prefix", "outside"]


@pytest.mark.parametrize("case", NAME_RULE_CASES)
def test_save_file_refuses_any_quam_config_ancestor(env, tmp_path, monkeypatch, case):
    """The one rule: a target with any ancestor folder named quam_config
    (letter case ignored), as typed or as resolved, is refused and nothing is
    written; every other folder takes the file."""
    if case.startswith("junction") and os.name != "nt":
        pytest.skip("junctions are a Windows feature")
    monkeypatch.setattr(D, "import_check", lambda *a, **k: None)
    root = tmp_path / "t"
    root.mkdir()
    deep = env.folder / "components" / "more"
    deep.mkdir(parents=True)
    upper = root / "QUAM_CONFIG"
    upper.mkdir()
    archive = root / "archive" / "quam_config" / "old"
    archive.mkdir(parents=True)
    # SM's lab code folder holds one quam_config while the env imports another.
    lab = root / "smlab" / "quam_config"
    lab.mkdir(parents=True)
    imported = root / "envsrc" / "quam_config"
    imported.mkdir(parents=True)
    monkeypatch.setattr(config_generator, "get_lab_code_dir", lambda *a: str(lab.parent))
    monkeypatch.setattr(Q, "_run", lambda *a, **k: {"ok": True, "folder": str(imported), "site_packages": []})
    sibling = root / "quam_config_backup"
    sibling.mkdir()
    prefix = root / "my_quam_config"
    prefix.mkdir()
    plain = root / "plain"
    plain.mkdir()
    cases = {
        "direct": (env.folder / "Other.py", env.folder),
        "deeper": (deep / "Other.py", env.folder),
        "upper_case": (upper / "Other.py", upper),
        "archive": (archive / "Other.py", archive.parent),
        "lab_folder_not_env_import": (lab / "Other.py", lab),
        "dotdot_typed": (upper / ".." / "Other.py", upper),
        "dotdot_out_of_package": (env.folder / ".." / "Other.py", env.folder),
        "sibling": (sibling / "Other.py", None),
        "prefix": (prefix / "Other.py", None),
        "outside": (plain / "Other.py", None),
    }
    if case == "junction_into_package":
        _junction(root / "innocent", env.folder)
        cases[case] = (root / "innocent" / "Other.py", env.folder)
    if case == "junction_named_quam_config":
        link = root / "named" / "quam_config"
        link.parent.mkdir()
        _junction(link, plain)
        cases[case] = (link / "Other.py", link)
    target, refused_by = cases[case]
    before = _tree(tmp_path)
    response = _post_file(env, target)
    if refused_by is None:
        assert response.status_code == 200 and response.get_json()["ok"], response.get_json()
        assert target.is_file()
        return
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert error in {_package_sentence(refused_by), _package_sentence(refused_by.resolve())}, error
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("case", [
    "empty", "relative", "nul", "nul_in_folder", "long", "directory", "suffix", "colon", "device",
    "missing_parent", "missing_parent_in_package", "resolve_value", "resolve_os",
])
def test_save_file_target_failures(env, tmp_path, monkeypatch, case):
    """Each unusable target gets its own sentence and nothing is written."""
    directory = tmp_path / "directory.py"
    directory.mkdir()
    invalid = W.INVALID_TARGET_SENTENCE
    targets = {
        "empty": ("", "Type the full path of the class file."),
        "relative": ("relative.py", "The class file must be a full path, starting with its drive "
                     "or root folder: relative.py"),
        "nul": (str(tmp_path / "a\x00b.py"), invalid),
        # Without the NUL guard this one reads as a missing folder.
        "nul_in_folder": (str(tmp_path / "a\x00" / "b.py"), invalid),
        "long": (str(tmp_path / ("a" * 40000 + ".py")), invalid),
        "directory": (str(directory), f"{directory} is a folder, not a file; choose a file name inside it."),
        "suffix": (str(tmp_path / "other.txt"), "The class file must end in .py"),
        "colon": (str(tmp_path / "a:b.py"), "The class file path cannot contain a colon after the drive."),
        "device": (str(tmp_path / "NUL.py"), "NUL.py is a reserved Windows device name; choose another file name."),
        "missing_parent": (str(tmp_path / "missing" / "other.py"),
                           f"The folder {tmp_path / 'missing'} does not exist"),
        "missing_parent_in_package": (str(env.folder / "missing" / "other.py"),
                                      f"The folder {env.folder / 'missing'} does not exist"),
        "resolve_value": (str(tmp_path / "other.py"), invalid),
        "resolve_os": (str(tmp_path / "other.py"), invalid),
    }
    target, expected = targets[case]
    if case == "colon" and not Path(target).is_absolute():
        # Some Python versions read "a:" inside the path as a drive and see a relative path.
        expected = ("The class file must be a full path, starting with its drive "
                    f"or root folder: {target}")
    if case in {"resolve_value", "resolve_os"}:
        resolve = Path.resolve

        def broken_resolve(path, *args, **kwargs):
            if str(path) == target:
                raise (ValueError if case == "resolve_value" else OSError)("unusable path")
            return resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", broken_resolve)
    monkeypatch.setattr(D, "import_check", lambda *a, **k: None)
    before = _tree(tmp_path)
    response = env.c.post("/pulse-class/save-file", json={"cls": CLASS, "target": target})
    assert 400 <= response.status_code < 500
    assert response.get_json()["error"] == expected
    assert _tree(tmp_path) == before


def test_save_file_door_asks_no_environment(env, tmp_path, monkeypatch):
    """Save to file judges the target by its folder names alone: during a
    press every way to start a process or ask the env raises, and both a
    refusal and a save still answer normally."""
    from quam_state_manager.core import lab_worker, project_env
    from quam_state_manager.web import port_play_page

    def forbidden(*a, **k):
        raise AssertionError("the Save to file door started a process or asked the env")

    monkeypatch.setattr(D, "import_check", lambda *a, **k: None)
    for owner, name in [(subprocess, "Popen"), (subprocess, "run"), (subprocess, "call"),
                        (subprocess, "check_output"), (os, "system"), (lab_worker, "run"),
                        (Q, "_run"), (Q, "lab_package"), (Q, "destination"),
                        (project_env, "run_env_source"), (port_play_page, "_chosen_env")]:
        monkeypatch.setattr(owner, name, forbidden)
    env.app.config["PROPAGATE_EXCEPTIONS"] = True
    refused = _post_file(env, env.folder / "x.py")
    assert refused.status_code == 400
    assert refused.get_json()["error"] == _package_sentence(env.folder)
    outside = tmp_path / "outside.py"
    saved = _post_file(env, outside)
    assert saved.status_code == 200 and saved.get_json()["ok"] and outside.is_file()


# Every sentence the Save to file door sends, and the condition(s) that send it.
SAVE_FILE_SENTENCES = {
    "PACKAGE_SENTENCE": {"name_rule"},
    "PUBLISHED_SENTENCE": {"published", "published_in_package"},
    "NO_DRAFT_SENTENCE": {"no_draft"},
    "INVALID_TARGET_SENTENCE": {"invalid_target"},
    "REPLACE_QUESTION": {"file_there"},
}


def test_save_file_door_raises_only_table_sentences():
    import ast
    import inspect
    import textwrap

    source = textwrap.dedent(inspect.getsource(W.save_to_target))
    used = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "DraftError":
            if ast.unparse(node.args[0]) == "why":
                continue  # the lab import check's own sentence, passed on as it is
            names = {n.id for n in ast.walk(node.args[0]) if isinstance(n, ast.Name)}
            assert names & set(SAVE_FILE_SENTENCES), ast.unparse(node)
            assert not any(isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value
                           for n in ast.walk(node.args[0])), ast.unparse(node)
            used |= names & set(SAVE_FILE_SENTENCES)
    assert "REPLACE_QUESTION" in source
    assert used | {"REPLACE_QUESTION"} == set(SAVE_FILE_SENTENCES)


def test_save_file_package_sentence_is_the_one_customers_already_see():
    assert W.PACKAGE_SENTENCE.format(parent="P") == (
        "P is a quam_config folder; use Save to quam_config, "
        "which checks the class before it writes the file.")


@pytest.mark.parametrize("condition", sorted(set().union(*SAVE_FILE_SENTENCES.values())))
def test_save_file_sentences_are_sent_only_by_their_branch(env, tmp_path, monkeypatch, condition):
    import re

    monkeypatch.setattr(D, "import_check", lambda *a, **k: None)
    target = tmp_path / "archive" / "quam_config"
    target.mkdir(parents=True)
    target = target / "x.py"
    if condition in {"published", "published_in_package"}:
        # Save to quam_config's own file is named as such, also inside the package.
        target = env.folder / "x.py" if condition == "published_in_package" else tmp_path / "published.py"
        monkeypatch.setattr(W, "published_record", lambda *a: {"path": str(target)})
    elif condition == "no_draft":
        target = tmp_path / "nodraft.py"
        monkeypatch.setattr(D, "load_draft", lambda *a: None)
    elif condition == "invalid_target":
        target = str(tmp_path / "a\x00b.py")
    elif condition == "file_there":
        target = tmp_path / "existing.py"
        target.write_text("other")
    body = _post_file(env, target).get_json()
    text = body.get("error") or (body.get("confirm") or {}).get("question") or ""

    def pattern(name):
        parts = re.split(r"\{[a-z]+\}", getattr(W, name))
        return "(?s)" + ".+".join(re.escape(p) for p in parts)

    sent = {name for name in SAVE_FILE_SENTENCES if re.search(pattern(name), text)}
    assert sent == {name for name, conds in SAVE_FILE_SENTENCES.items() if condition in conds}, text
