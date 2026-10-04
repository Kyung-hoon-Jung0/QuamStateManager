"""docs/263: the same zone picker and save contract before projects exist."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from flask import render_template
import pytest

from quam_state_manager.core import project_time as pt
from quam_state_manager.web.app import create_app


@pytest.fixture(params=["root-only", "no-config"])
def empty_lab(request, tmp_path, monkeypatch):
    cfg = tmp_path / "qualibrate"
    scope = "current-chip" if request.param == "root-only" else "default"
    if request.param == "root-only":
        cfg.mkdir()
        (cfg / "config.toml").write_text(
            '[qualibrate]\nproject = "current-chip"\nversion = 5\n', encoding="utf-8")
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg / "config.toml"))
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    return app, app.test_client(), cfg, scope


def fragment(client):
    response = client.get("/landing/projects")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def views(html):
    return json.loads(re.search(r'data-tz-views>(.*?)</script>', html, re.S)[1])


def pick(client, scope):
    return client.post("/project-time/zone", data={
        "project": scope, "zone": "America/Los_Angeles", "os_answer": "view",
        "os_offset": "+09:00"})


def test_picker_saves_in_sm_and_reload_shows_the_named_scope(empty_lab):
    app, client, cfg, scope = empty_lab
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in cfg.rglob("*") if p.is_file()}
    html = fragment(client)
    assert html.count('id="landing-tz"') == 1
    assert f'data-tz-default="{scope}"' in html
    assert views(html) == {scope: views(html)[scope]}
    assert views(html)[scope]["state"] == "none"
    assert pick(client, scope).status_code == 200
    stored = json.loads((Path(app.instance_path) / "project_time.json").read_text())
    assert stored["projects"][scope]["zone"] == "America/Los_Angeles"
    assert stored["projects"][scope]["os_check"]["answer"] == "view"
    assert stored["last_zone"]["project"] == scope
    assert views(fragment(client))[scope]["state"] == "picked"
    assert views(fragment(client))[scope]["zone"] == "America/Los_Angeles"
    assert client.post("/project-time/zone", data={"project": "unknown", "zone": "UTC"}).status_code == 404
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in cfg.rglob("*") if p.is_file()} == before
    assert not (cfg / "projects").exists()


def test_watch_answer_uses_the_same_scope(empty_lab):
    app, client, cfg, scope = empty_lab
    assert pick(client, scope).status_code == 200
    response = client.post("/project-time/watch", data={
        "project": scope, "answer": "matches", "shown": "12:34", "ntp_synced": "true"})
    assert response.status_code == 200
    stored = pt.load(app.instance_path)["projects"][scope]
    assert stored["watch"]["answer"] == "matches"
    assert stored["watch"]["shown"] == "12:34"


def test_empty_landing_never_probes_the_clock_on_render(empty_lab, monkeypatch):
    app, client, cfg, scope = empty_lab

    def probe():
        raise AssertionError("Clock probes must wait for the browser's request")

    monkeypatch.setattr(pt, "clock_status", probe)
    assert client.get("/?landing=1").status_code == 200
    assert 'id="landing-tz"' in fragment(client)


def test_a_later_project_inherits_the_pick(empty_lab):
    app, client, cfg, scope = empty_lab
    assert pick(client, scope).status_code == 200
    # Simulate QUAlibrate creating its first overlay; SM never writes it.
    project = cfg / "projects" / "later"
    project.mkdir(parents=True)
    if not (cfg / "config.toml").exists():
        (cfg / "config.toml").write_text('[qualibrate]\nversion = 5\n', encoding="utf-8")
    (project / "config.toml").write_text('[quam]\nstate_path = ""\n', encoding="utf-8")
    offered = views(fragment(client))["later"]
    assert (offered["zone"], offered["state"], offered["from_project"]) == (
        "America/Los_Angeles", "suggested", scope)
    pt.ensure_default(app.instance_path, "later")
    assert pt.view(app.instance_path, "later")["state"] == "default"
    assert pt.view(app.instance_path, "later")["zone"] == "America/Los_Angeles"


@pytest.mark.parametrize("configuration", ["projects", "root-only", "no-config"])
def test_settings_link_reaches_a_picker(configuration, tmp_path, monkeypatch):
    cfg = tmp_path / "qualibrate"
    if configuration != "no-config":
        cfg.mkdir()
        (cfg / "config.toml").write_text('[qualibrate]\nproject = "alpha"\nversion = 5\n', encoding="utf-8")
    if configuration == "projects":
        p = cfg / "projects" / "alpha"
        p.mkdir(parents=True)
        (p / "config.toml").write_text('[quam]\nstate_path = ""\n', encoding="utf-8")
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg / "config.toml"))
    client = create_app(testing=True, instance_path=str(tmp_path / "instance")).test_client()
    html = client.get("/").get_data(as_text=True)
    target = re.search(r'class="settings-opt settings-tz-change" href="([^"]+)"', html)[1]
    landing = client.get(target).get_data(as_text=True)
    if configuration == "no-config":
        # no config: the Welcome carries the picker itself and never asks for the project cards
        # (tests/test_project_scope.py::test_landing_welcome_without_config)
        assert 'id="landing-tz"' in landing and 'id="landing-cards"' not in landing
        assert 'data-tz-default="default"' in landing
    else:
        assert 'id="landing-cards"' in landing and 'hx-get="/landing/projects"' in landing
        assert 'id="landing-tz"' in fragment(client)


@pytest.mark.parametrize("last, digest", [
    (None, "25666df66ab52f99d7159a76f4eff5292fcdf25c1f141f8ed873ad589016c65b"),
    ("alpha", "82d34546b8e090ed21957629a9983f893f25d2e4616f59335bd5bf58869e5f55"),
    ("beta", "9e6c553e31fe92568d7b8b412ddb67b00a4314125e2d6f735c47a3cd55334c88"),
    ("unknown", "25666df66ab52f99d7159a76f4eff5292fcdf25c1f141f8ed873ad589016c65b"),
])
def test_with_projects_fragment_is_byte_identical(last, digest, tmp_path):
    # SHA256 of the pre-fix rendered fragment, captured before any edits.
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    def project(name, active):
        return dict(name=name, active=active, state_path=dict(raw="scratch/state", exists=True),
                    storage=dict(raw="scratch/data", exists=True), loaded_in_sm=False)
    listing = dict(config_exists=True, projects=[project("alpha", True), project("beta", False)], doctor=[])
    with app.test_request_context("/"):
        html = render_template("_landing_projects.html", listing=listing, last_project=last,
                               env_views={}, tz_views={})
    assert hashlib.sha256(html.encode()).hexdigest() == digest


def test_browser_component_without_environment_picker(empty_lab):
    app, client, cfg, scope = empty_lab
    fresh = fragment(client)
    assert pick(client, scope).status_code == 200
    saved = fragment(client)
    node = shutil.which("node")
    assert node, "Node is required for this component pin"
    result = subprocess.run([node, str(Path(__file__).with_name("project_time_without_projects_selfcheck.cjs"))],
                            input=json.dumps(dict(fresh=fresh, saved=saved, scope=scope)),
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_time_sync_line_says_the_os_message_briefly():
    # the real message on a PC whose Windows Time service is stopped; the full text stays in ntp.detail
    raw = ("Windows Time query failed (-2147023834): The following error occurred: "
           "The service has not been started. (0x80070426)")
    assert pt.ntp_text({"ntp": {"synced": None, "detail": raw}}) == \
        "Time sync: unknown (the Windows Time service is not running)"
    long_text = pt.ntp_text({"ntp": {"synced": None, "detail": "y" * 200}})
    assert long_text.endswith("...)") and len(long_text) < 100
    assert pt.ntp_text({"ntp": {"synced": None, "detail": ""}}) == "Time sync: unknown"
