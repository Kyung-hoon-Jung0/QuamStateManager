"""w9/labwarm -- each QUAlibrate project remembers the Python env it runs with.

User decisions (2026-09-28):

1. the project-first landing carries an env picker per project (the same
   discovery as Generate Config), and opening a project never waits for it;
2. a project synced with an env opens with that env ALREADY selected, and
   nothing is re-discovered or re-probed unless the user asks to change it;
3. a project never synced is offered the env used most recently by any
   project, marked "suggested -- confirm", and remembered once confirmed or
   changed;
4. the project's env IS the one selected env SM uses for everything
   (``config_generator``'s setting), and the sidebar says which env is active;
5. so the lab-worker pre-warm starts the moment such a project opens.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator, project_env
from quam_state_manager.core import qualibrate_config as qc
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _chip(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {name: {"id": name, "f_01": 6.25e9}},
         "qubit_pairs": {}, "active_qubit_names": [name]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.1.1.1"}}), encoding="utf-8")
    return folder


def _env(tmp_path: Path, name: str) -> str:
    py = tmp_path / "envs" / name / "python.exe"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_bytes(b"")
    return str(py)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    cfg = tmp_path / ".qualibrate"
    a = _chip(tmp_path / "chips" / "a", "qA1")
    b = _chip(tmp_path / "chips" / "b", "qB1")
    _write(cfg / "config.toml", f'''
[qualibrate]
project = "alpha"
version = 5

[quam]
state_path = "{a.as_posix()}"
version = 3
''')
    _write(cfg / "projects" / "alpha" / "config.toml", f'[quam]\nstate_path = "{a.as_posix()}"\n')
    _write(cfg / "projects" / "beta" / "config.toml", f'[quam]\nstate_path = "{b.as_posix()}"\n')
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    qc._state_index_cache.clear()
    # what a SELECTION runs is counted, never spawned: these are the
    # "re-discover / re-probe" a remembered env must not cause
    seen = {"apply": [], "discover": 0, "probe": 0, "caps": 0}
    real_apply = routes._apply_selected_env

    def apply(py, **kw):
        seen["apply"].append(py)
        return real_apply(py, **kw)
    monkeypatch.setattr(routes, "_apply_selected_env", apply)
    monkeypatch.setattr(config_generator, "probe_capabilities",
                        lambda *a, **k: seen.__setitem__("caps", seen["caps"] + 1) or {})
    monkeypatch.setattr(config_generator, "discover_envs",
                        lambda *a, **k: seen.__setitem__("discover", seen["discover"] + 1) or [])
    monkeypatch.setattr(config_generator, "probe_envs",
                        lambda *a, **k: seen.__setitem__("probe", seen["probe"] + 1) or {})
    monkeypatch.setattr(routes, "_warm_state_schema_async", lambda *a, **k: None)
    inst = tmp_path / "_inst"
    app = create_app(testing=True, instance_path=str(inst))
    return {"app": app, "c": app.test_client(), "inst": inst, "seen": seen,
            "A": _env(tmp_path, "ENV_A"), "B": _env(tmp_path, "ENV_B"), "tmp": tmp_path}


def _selected(lab):
    return config_generator.get_selected_env(str(lab["inst"]))


def _card_env(html: str, project: str) -> str:
    i = html.index(f'data-project="{project}"')
    return html[i:html.index("</div>", i)]


# ------------------------------------------------------------ the memory
class TestTheMemory:
    def test_labels_come_from_the_path_alone(self):
        assert project_env.label(r"D:\miniconda3\envs\KRISS_CZ\python.exe") == "KRISS_CZ"
        assert project_env.label(r"C:\labs\qpu\.venv\Scripts\python.exe") == "qpu (.venv)"
        assert project_env.label("/opt/labs/qpu/.venv/bin/python") == "qpu (.venv)"
        assert project_env.label(None) == ""

    def test_a_project_never_synced_is_offered_the_most_recently_used(self, tmp_path):
        inst = tmp_path / "i"
        inst.mkdir()
        assert project_env.suggestion(inst, fallback="G") == "G"      # nothing yet
        assert project_env.view(inst, "p", None)["state"] == "none"
        project_env.remember(inst, "p1", "A", "confirmed")
        project_env.remember(inst, "p2", "B", "changed")
        assert project_env.suggestion(inst, fallback="G") == "B"
        project_env.mark_used(inst, "p1", "A")                         # p1 opened
        assert project_env.suggestion(inst) == "A"
        v = project_env.view(inst, "p3", "G")
        assert v["state"] == "suggested" and v["python"] == "A"
        v = project_env.view(inst, "p2", "G")
        assert v["state"] == "remembered" and v["python"] == "B"

    def test_a_corrupt_file_is_no_memory_never_an_error(self, tmp_path):
        inst = tmp_path / "i"
        inst.mkdir()
        (inst / project_env.FILENAME).write_text("{not json", encoding="utf-8")
        assert project_env.load(inst) == {"projects": {}, "last_used": None}
        project_env.remember(inst, "p", "A")
        assert project_env.remembered(inst, "p") == "A"


# ------------------------------------------------------------ the landing
class TestTheLanding:
    def test_a_first_ever_project_is_suggested_the_env_already_selected(self, lab):
        config_generator.set_selected_env(str(lab["inst"]), lab["A"])
        html = lab["c"].get("/landing/projects").get_data(as_text=True)
        row = _card_env(html, "alpha")
        assert "ENV_A" in row and "suggested &mdash; confirm" in row
        assert 'hx-post="/qualibrate/project-env"' in row and ">Confirm<" in row
        assert 'id="landing-env-picker"' in html
        # rendering the cards discovers and probes NOTHING
        assert lab["seen"]["discover"] == 0 and lab["seen"]["probe"] == 0

    def test_no_env_ever_used_offers_a_choice(self, lab):
        html = lab["c"].get("/landing/projects").get_data(as_text=True)
        row = _card_env(html, "beta")
        assert "<em>none</em>" in row and "Choose…" in row and ">Confirm<" not in row

    def test_confirm_remembers_and_answers_the_row_and_the_badge(self, lab):
        config_generator.set_selected_env(str(lab["inst"]), lab["A"])
        r = lab["c"].post("/qualibrate/project-env",
                          data={"project": "alpha", "python": lab["A"], "how": "confirmed"})
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        own = body.split("hx-swap-oob")[0]           # the requested row itself
        assert "&#10003;" in own and "suggested" not in own
        assert 'id="sidebar-folder-badges-slot" hx-swap-oob="true"' in body
        assert project_env.remembered(lab["inst"], "alpha") == lab["A"]
        row = _card_env(lab["c"].get("/landing/projects").get_data(as_text=True), "alpha")
        assert "&#10003;" in row and "suggested" not in row

    def test_a_sync_repaints_every_card_whose_suggestion_it_moved(self, lab):
        config_generator.set_selected_env(str(lab["inst"]), lab["A"])
        html = lab["c"].get("/landing/projects").get_data(as_text=True)
        assert "ENV_A" in _card_env(html, "beta")              # suggested A
        r = lab["c"].post("/qualibrate/project-env",
                          data={"project": "alpha", "python": lab["B"], "how": "changed"})
        body = r.get_data(as_text=True)
        i = body.index('data-project="beta"')
        beta = body[body.rindex("<div", 0, i):body.index("</div>", i)]
        assert 'hx-swap-oob="true"' in beta and "ENV_B" in beta and "suggested" in beta
        # the id it swaps is the one the landing rendered
        dom = beta.split('id="')[1].split('"')[0]
        assert f'id="{dom}"' in html
        # and it equals a cold render
        cold = _card_env(lab["c"].get("/landing/projects").get_data(as_text=True), "beta")
        assert "ENV_B" in cold and "suggested" in cold

    def test_an_unknown_project_or_path_is_refused(self, lab):
        r = lab["c"].post("/qualibrate/project-env",
                          data={"project": "nope", "python": lab["A"]})
        assert r.status_code == 404
        r = lab["c"].post("/qualibrate/project-env",
                          data={"project": "alpha", "python": str(lab["tmp"] / "none.exe")})
        assert r.status_code == 400
        assert project_env.load(lab["inst"])["projects"] == {}


# ------------------------------------------------------------ opening
class TestOpening:
    def test_a_synced_project_opens_with_its_env_selected(self, lab):
        config_generator.set_selected_env(str(lab["inst"]), lab["B"])
        project_env.remember(lab["inst"], "alpha", lab["A"])
        r = lab["c"].post("/qualibrate/open", data={"project": "alpha"})
        assert r.status_code in (200, 302)
        assert os.path.normcase(_selected(lab)) == os.path.normcase(lab["A"])
        assert lab["seen"]["apply"] == [lab["A"]]

    def test_the_second_open_reprobes_nothing(self, lab):
        project_env.remember(lab["inst"], "alpha", lab["A"])
        lab["c"].post("/qualibrate/open", data={"project": "alpha"})
        caps = lab["seen"]["caps"]
        lab["c"].post("/qualibrate/open", data={"project": "beta"})    # suggested = A
        lab["c"].post("/qualibrate/open", data={"project": "alpha"})
        assert lab["seen"]["apply"] == [lab["A"]]          # selected ONCE
        assert lab["seen"]["caps"] == caps
        assert lab["seen"]["discover"] == 0 and lab["seen"]["probe"] == 0

    def test_a_never_synced_project_opens_with_the_suggestion_unconfirmed(self, lab):
        project_env.remember(lab["inst"], "alpha", lab["A"])
        config_generator.set_selected_env(str(lab["inst"]), lab["B"])
        lab["c"].post("/qualibrate/open", data={"project": "beta"})
        assert os.path.normcase(_selected(lab)) == os.path.normcase(lab["A"])
        assert project_env.remembered(lab["inst"], "beta") is None       # not confirmed
        row = _card_env(lab["c"].get("/landing/projects").get_data(as_text=True), "beta")
        assert "suggested" in row

    def test_a_vanished_env_is_not_selected_and_the_card_says_so(self, lab):
        gone = str(lab["tmp"] / "envs" / "GONE" / "python.exe")
        project_env.remember(lab["inst"], "alpha", gone)
        config_generator.set_selected_env(str(lab["inst"]), lab["B"])
        lab["c"].post("/qualibrate/open", data={"project": "alpha"})
        assert os.path.normcase(_selected(lab)) == os.path.normcase(lab["B"])
        row = _card_env(lab["c"].get("/landing/projects").get_data(as_text=True), "alpha")
        assert "not found" in row

    def test_changing_the_open_projects_env_selects_it_now(self, lab):
        project_env.remember(lab["inst"], "alpha", lab["A"])
        lab["c"].post("/qualibrate/open", data={"project": "alpha"})
        lab["c"].post("/qualibrate/project-env",
                      data={"project": "alpha", "python": lab["B"], "how": "changed"})
        assert os.path.normcase(_selected(lab)) == os.path.normcase(lab["B"])
        assert lab["seen"]["apply"] == [lab["A"], lab["B"]]

    def test_changing_another_projects_env_only_remembers_it(self, lab):
        project_env.remember(lab["inst"], "alpha", lab["A"])
        lab["c"].post("/qualibrate/open", data={"project": "alpha"})
        lab["c"].post("/qualibrate/project-env",
                      data={"project": "beta", "python": lab["B"], "how": "changed"})
        assert os.path.normcase(_selected(lab)) == os.path.normcase(lab["A"])
        assert project_env.remembered(lab["inst"], "beta") == lab["B"]

    def test_re_opening_a_cached_chip_binds_it_to_the_new_env(self, lab, monkeypatch):
        bound = []
        real = routes._bind_to_selected_env
        monkeypatch.setattr(routes, "_bind_to_selected_env",
                            lambda ctx, inst: bound.append(ctx.get("path")) or real(ctx, inst))
        project_env.remember(lab["inst"], "alpha", lab["A"])
        lab["c"].post("/qualibrate/open", data={"project": "alpha"})
        lab["c"].post("/qualibrate/open", data={"project": "beta"})
        lab["c"].post("/qualibrate/project-env",          # alpha is not open
                      data={"project": "alpha", "python": lab["B"], "how": "changed"})
        bound.clear()
        lab["c"].post("/qualibrate/open", data={"project": "alpha"})   # cached: fast path
        assert os.path.normcase(_selected(lab)) == os.path.normcase(lab["B"])
        assert len(bound) == 1 and bound[0].endswith("a")

    def test_a_state_load_of_a_synced_projects_folder_selects_its_env(self, lab):
        project_env.remember(lab["inst"], "alpha", lab["A"])
        config_generator.set_selected_env(str(lab["inst"]), lab["B"])
        chip_a = lab["tmp"] / "chips" / "a"
        assert lab["c"].post("/load", data={"folder": str(chip_a)}).status_code in (200, 302)
        assert os.path.normcase(_selected(lab)) == os.path.normcase(lab["A"])
        lab["c"].post("/load", data={"folder": str(chip_a)})           # again: nothing
        assert lab["seen"]["apply"] == [lab["A"]]

    def test_re_activating_the_active_chip_keeps_an_env_picked_meanwhile(self, lab):
        project_env.remember(lab["inst"], "alpha", lab["A"])
        chip_a = lab["tmp"] / "chips" / "a"
        lab["c"].post("/load", data={"folder": str(chip_a)})
        assert os.path.normcase(_selected(lab)) == os.path.normcase(lab["A"])
        # Generate Config picks another env while alpha's chip stays open
        assert lab["c"].post("/generate/select-env", json={"python": lab["B"]}).status_code == 200
        lab["c"].get("/api/topology?refresh=1")           # re-activates the active chip
        lab["c"].post("/load", data={"folder": str(chip_a)})
        assert os.path.normcase(_selected(lab)) == os.path.normcase(lab["B"])
        with lab["app"].test_request_context("/"):
            assert routes._active_env_badge("alpha")["state"] == "differs"

    def test_a_state_load_of_a_never_synced_folder_adopts_no_suggestion(self, lab):
        project_env.remember(lab["inst"], "alpha", lab["A"])
        config_generator.set_selected_env(str(lab["inst"]), lab["B"])
        lab["c"].post("/load", data={"folder": str(lab["tmp"] / "chips" / "b")})
        assert os.path.normcase(_selected(lab)) == os.path.normcase(lab["B"])
        assert lab["seen"]["apply"] == []

    def test_the_sidebar_names_the_active_env(self, lab):
        project_env.remember(lab["inst"], "alpha", lab["A"])
        lab["c"].post("/qualibrate/open", data={"project": "alpha"})
        with lab["app"].test_request_context("/"):
            b = routes._qualibrate_tray_badge()
        assert b["env"]["state"] == "remembered" and b["env"]["label"] == "ENV_A"
        lab["c"].post("/qualibrate/open", data={"project": "beta"})
        with lab["app"].test_request_context("/"):
            b = routes._qualibrate_tray_badge()
        assert b["env"]["state"] == "suggested"
        html = lab["c"].get("/qubits").get_data(as_text=True)
        assert 'class="project-env-badge project-env-badge-warn"' in html
        assert "(suggested — confirm)" in html


# ------------------------------------------------------------ the picker JS
def test_landing_env_selfcheck():
    import shutil
    import subprocess
    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    r = subprocess.run([node, str(root / "tests" / "landing_env_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=120, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 13, r.stdout


def test_every_page_loads_the_picker():
    root = Path(__file__).resolve().parent.parent
    base = (root / "quam_state_manager" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "asset_url('landing-env.js')" in base
