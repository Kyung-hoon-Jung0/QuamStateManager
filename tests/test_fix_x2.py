"""Fix X2: the landing's "Open QUAlibrate's current project" (and its orphan /load variant) say beforehand that
the open arms Auto-Sync and offer the quiet open; the sidebar's Projects row says it after the open, every time
it armed; the arm wording has one source."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from tests.test_fix_x import _armed, _files, _restart_landing
from tests.test_project_scope import scoped  # noqa: F401 -- tmp QUAlibrate config tree + chips

pytestmark = pytest.mark.usefixtures("any_project_env_chosen")

ROOT = Path(__file__).resolve().parents[1]
_NOTICE = "data-resume-arms-auto-sync"
_QUIET = 'name="auto_sync" value="0"'
_LINE = "Auto-Sync turned on for this chip: your edits go to the chip file. Turn it off in the top bar."


@pytest.fixture
def arming(monkeypatch):
    monkeypatch.setenv("SM_AUTO_SYNC_ON_OPEN_DEFAULT", "1")


def _current_form(body):
    m = re.search(r'<form[^>]*class="landing-current-project">.*?</form>', body, re.S)
    assert m, "the landing offers QUAlibrate's current project"
    return m.group()


def _orphan(sc):
    """The root config names a project with no projects/<name> folder; its state_path is chip_a."""
    root = sc["cfg"] / "config.toml"
    text = root.read_text(encoding="utf-8").replace('project = "alpha"', 'project = "omega"')
    root.write_text(text, encoding="utf-8")


def _no_arm(sc):
    sc["client"].post("/settings/auto-sync-on-open", json={"enabled": False})


# ---------------------------------------------------------------- landing: current project (/qualibrate/open)

def test_current_project_that_arms_says_so_and_the_quiet_open_is_honoured(scoped, arming):  # noqa: F811
    body, _resume = _restart_landing(scoped)
    form = _current_form(body)
    assert 'action="/qualibrate/open"' in form and _NOTICE in form
    assert re.search(r'<button type="submit" ' + re.escape(_QUIET) + r'[^>]*>Open without Auto-Sync<', form)
    before = _files(scoped["chip_a"])
    r = scoped["client"].post("/qualibrate/open", data={"project": "alpha", "from": "landing", "auto_sync": "0"})
    assert r.status_code in (200, 302)
    assert not _armed(scoped["app"])
    assert before == _files(scoped["chip_a"])


def test_current_project_that_does_not_arm_offers_neither(scoped, arming):  # noqa: F811
    _no_arm(scoped)
    body, _resume = _restart_landing(scoped)
    form = _current_form(body)
    assert _NOTICE not in form and _QUIET not in form and "without Auto-Sync" not in form


def test_current_project_state_path_is_the_open_routes_own(scoped):  # noqa: F811
    with scoped["app"].test_request_context("/"):
        assert routes_mod._qualibrate_open_state_path("alpha") == routes_mod._qualibrate_project_entry(
            "alpha")["state_path"]["native"]
        assert routes_mod._qualibrate_open_state_path("delta") is None   # missing folder: that open refuses
        assert routes_mod._qualibrate_open_state_path("nope") is None
    src = (ROOT / "quam_state_manager" / "web" / "routes.py").read_text(encoding="utf-8")
    i = src.index("def qualibrate_open_project(")
    body = src[i:src.index("\n@bp.route", i)]
    assert "_qualibrate_project_entry(name)" in body and "_qualibrate_listing()" not in body


# ---------------------------------------------------------------- landing: orphan variant (/load)

def test_orphan_current_chip_that_arms_says_so_and_the_quiet_open_is_honoured(scoped, arming):  # noqa: F811
    _orphan(scoped)
    body, _resume = _restart_landing(scoped)
    form = _current_form(body)
    assert 'action="/load"' in form and _NOTICE in form
    assert re.search(r'<button type="submit" ' + re.escape(_QUIET) + r'[^>]*>Open without Auto-Sync<', form)
    r = scoped["client"].post("/load", data={"folder": str(scoped["chip_a"]), "auto_sync": "0"})
    assert r.status_code in (200, 302)
    assert not _armed(scoped["app"])


def test_orphan_current_chip_that_does_not_arm_offers_neither(scoped, arming):  # noqa: F811
    _orphan(scoped)
    _no_arm(scoped)
    body, _resume = _restart_landing(scoped)
    form = _current_form(body)
    assert 'action="/load"' in form
    assert _NOTICE not in form and _QUIET not in form


# ---------------------------------------------------------------- sidebar Projects row

def _sidebar_open(sc):
    r = sc["client"].post("/qualibrate/open", data={"project": "alpha"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.headers.get("HX-Redirect")
    return sc["client"].get(r.headers["HX-Redirect"]).get_data(as_text=True)


def test_sidebar_open_that_arms_shows_the_line_every_time(scoped, arming):  # noqa: F811
    page = _sidebar_open(scoped)
    assert _armed(scoped["app"])
    assert "data-auto-sync-open" in page and _LINE in page
    scoped["client"].post("/auto-apply/disarm")
    scoped["client"].post("/auto-sync/set", data={"pull": "1", "push": "1"})
    _restart_landing(scoped)
    page = _sidebar_open(scoped)
    assert _armed(scoped["app"]) and _LINE in page, "the line is said on every arming open, not only the first"


def test_sidebar_open_that_does_not_arm_shows_no_line(scoped, arming):  # noqa: F811
    _no_arm(scoped)
    page = _sidebar_open(scoped)
    assert not _armed(scoped["app"])
    assert "data-auto-sync-open" not in page and "Auto-Sync turned on" not in page


# ---------------------------------------------------------------- one wording source

def test_the_arm_wording_has_one_source():
    hits = []
    for sub in ("quam_state_manager",):
        for p in (ROOT / sub).rglob("*"):
            if p.suffix in (".py", ".html", ".js") and p.is_file():
                n = p.read_text(encoding="utf-8", errors="replace").count("your edits go to the chip file")
                hits += [p.name] * n
    assert hits == ["routes.py"], hits
    tpl = (ROOT / "quam_state_manager" / "web" / "templates" / "_open_auto_sync_choice.html").read_text(
        encoding="utf-8")
    assert "{{ auto_sync_open_words }}" in tpl and "Turn it off" not in tpl


# ---------------------------------------------------------------- Fix X3: an armed chip gets no offer

def test_an_armed_chip_is_offered_no_quiet_open_anywhere(scoped, arming):  # noqa: F811
    from tests.test_fix_x import _card_form
    _restart_landing(scoped)
    assert scoped["client"].post("/load", data={"folder": str(scoped["chip_a"])}).status_code in (200, 302)
    assert _armed(scoped["app"]), "the default open armed chip_a"
    body = scoped["client"].get("/?landing=1").get_data(as_text=True)
    resume = re.search(r'<form[^>]*class="landing-resume-form">.*?</form>', body, re.S)
    assert resume, "the landing offers Resume"
    card = _card_form(scoped["client"].get("/landing/projects").get_data(as_text=True), "alpha")
    for where, html in (("resume", resume.group()), ("current project", _current_form(body)), ("card", card)):
        assert _NOTICE not in html and _QUIET not in html, where + ": an armed chip's open arms nothing"
        assert "without Auto-Sync" not in html.replace("Without", "without"), where


def test_the_armed_check_is_the_pills_own(scoped, arming):  # noqa: F811
    _restart_landing(scoped)
    scoped["client"].post("/load", data={"folder": str(scoped["chip_a"])})
    with scoped["app"].test_request_context("/"):
        ctx = routes_mod._find_quam_ctx_by_path(str(scoped["chip_a"]))
        assert routes_mod._auto_sync_state(ctx), "armed, as the top bar pill reads it"
        assert routes_mod._open_arms_auto_sync(str(scoped["chip_a"])) is False
        sess = ctx.pop("auto_apply")
        try:
            # the same chip, not armed and with no OFF choice remembered: the open would arm it again
            assert routes_mod._open_arms_auto_sync(str(scoped["chip_a"])) is True
        finally:
            ctx["auto_apply"] = sess
