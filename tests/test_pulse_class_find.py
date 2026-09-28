"""w8 pulsehint (user, 2026-09-28): the create form's "Don't see your pulse
class?" line under the class list.

The only way to reach a lab pulse class the chip does not use yet is to NAME
its module in the env strip's folded "another module?" box (docs/218), and
nothing in the create flow pointed there. The line under the class list does,
in one of three states rendered from the strip's own context:

  * no usable env  -> "Pick a Python environment first" (the env picker link);
  * env ok         -> "Name the module it lives in (e.g. my_lab.cz_pulses)",
                      a link that opens the strip's box (pulses.js);
  * a named module that failed to import -> "✗ <module>: <error>".

The strip-only responses (poll, Add/remove module, Probe now) carry the line
again out-of-band so it never disagrees with the strip; the full form must
NOT carry the OOB attribute (htmx 2 processes nested OOB and would pull the
line out of the form). The client half -- the link opens + focuses, the OOB
swap in the REAL htmx, a module name typed in the strip does not count as a
form edit -- is tests/pulses_classfind_fragcheck.cjs, driven here against the
HTML these very routes render.

Also pinned: the Add-module press no longer costs TWO probes. The press kicks
one; the strip it answers with saw the new set "not read yet" and queued a
second, identical probe behind it, so the new class showed only after both.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import state_env_schema
from quam_state_manager.web.app import create_app
from tests.test_pulses_routes import _make_state, _make_wiring

_ROOT = Path(__file__).resolve().parents[1]
FIND_RE = re.compile(r'<p id="pulse-create-classfind"[^>]*>.*?</p>', re.S)


@pytest.fixture
def chip(tmp_path):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    inst = tmp_path / "_inst"
    app = create_app(testing=True, instance_path=str(inst))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    return app, c, tmp_path


@pytest.fixture
def env_ok(chip, monkeypatch):
    """An existing interpreter selected AFTER the load (no background probe),
    a warm manifest whose module statuses the test sets, and no real kick."""
    from quam_state_manager.web import routes
    app, c, tmp_path = chip
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")
    (Path(app.instance_path) / "config_generator.json").write_text(
        json.dumps({"selected_env_python": str(py)}), encoding="utf-8")
    manifest = {"pulse_modules": {}, "sources": {}}
    monkeypatch.setattr(routes, "_live_env_manifest", lambda store: manifest)
    kicked = []
    monkeypatch.setattr(routes, "_kick_env_reprobe", lambda *a: kicked.append(a))
    routes._lab_reprobe_tried.clear()
    yield app, c, manifest, kicked
    routes._lab_reprobe_tried.clear()


def _find(html: str) -> str:
    m = FIND_RE.search(html)
    assert m, "no #pulse-create-classfind in:\n" + html[:600]
    return m.group(0)


def _text(frag: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", frag)).strip()


class TestTheLineUnderTheClassList:
    def test_it_sits_right_under_the_class_list_and_is_never_oob_there(self, env_ok):
        _, c, _, _ = env_ok
        html = c.get("/pulse/new").data.decode()
        # directly after the class <select> -- the place the user looks when
        # the class is not in it
        assert re.search(r'</select>\s*<p id="pulse-create-classfind"', html), \
            html[html.find('id="pulse-create-type"'):][:1500]
        frag = _find(html)
        assert "hx-swap-oob" not in frag     # nested OOB would detach it
        assert html.count('id="pulse-create-classfind"') == 1

    def test_no_env_points_at_the_env_picker(self, chip):
        _, c, _ = chip
        frag = _find(c.get("/pulse/new").data.decode())
        assert 'data-classfind-state="noenv"' in frag
        assert "Don&#39;t see your pulse class?" in frag or "Don't see your pulse class?" in frag
        assert "Pick a Python environment first" in frag
        assert 'href="/generate"' in frag and 'hx-get="/generate"' in frag
        assert "data-pulse-module-open" not in frag   # nothing to open without an env

    def test_a_vanished_env_says_so(self, chip):
        from quam_state_manager.core import config_generator as cg
        app, c, _ = chip
        cg.set_selected_env(app.instance_path, r"C:\nope\python.exe")
        frag = _find(c.get("/pulse/new").data.decode())
        assert 'data-classfind-state="noenv"' in frag
        assert "Pick a Python environment first" in frag
        assert "no longer exists" in frag

    def test_env_ok_names_the_module_box(self, env_ok):
        _, c, _, _ = env_ok
        frag = _find(c.get("/pulse/new").data.decode())
        assert 'data-classfind-state="ok"' in frag
        assert "data-pulse-module-open" in frag
        t = _text(frag)
        assert "Don't see your pulse class? Name the module it lives in (e.g. my_lab.cz_pulses)" \
            in t.replace("&#39;", "'"), t
        assert "✗" not in t and "&#10007;" not in frag

    def test_a_failed_module_is_named_with_its_error(self, env_ok):
        app, c, manifest, _ = env_ok
        state_env_schema.save_pulse_modules(app.instance_path, ["my_lab.nope", "smlab.fine"])
        manifest["pulse_modules"] = {
            "my_lab.nope": "error: ModuleNotFoundError: No module named 'my_lab'",
            "smlab.fine": "ok"}
        frag = _find(c.get("/pulse/new").data.decode())
        assert 'data-classfind-state="failed"' in frag
        t = _text(frag).replace("&#39;", "'").replace("&#10007;", "✗")
        # "✗ <module>: <error>" -- the strip's own "error: " prefix dropped
        assert "✗ my_lab.nope: ModuleNotFoundError: No module named 'my_lab'" in t, t
        assert "error: ModuleNotFoundError" not in t
        assert "smlab.fine" not in t                 # an imported module is not news here
        assert "data-pulse-module-open" in frag     # the way to fix it stays one click

    def test_a_module_not_read_yet_says_the_list_will_refresh(self, env_ok):
        app, c, manifest, kicked = env_ok
        state_env_schema.save_pulse_modules(app.instance_path, ["smlab.new"])
        frag = _find(c.get("/pulse/new").data.decode())
        t = _text(frag)
        assert "reading smlab.new" in t and "refreshes when done" in t, t
        assert kicked                                 # the read was asked for


class TestTheStripCarriesTheLineOutOfBand:
    def test_the_strip_response_carries_it_oob(self, env_ok):
        app, c, manifest, _ = env_ok
        state_env_schema.save_pulse_modules(app.instance_path, ["my_lab.nope"])
        manifest["pulse_modules"] = {"my_lab.nope": "error: ImportError: boom"}
        html = c.get("/pulse/new/env-strip").data.decode()
        assert html.lstrip().startswith('<div id="pulse-env-strip"')
        frag = _find(html)
        assert 'hx-swap-oob="true"' in frag
        assert "my_lab.nope: ImportError: boom" in _text(frag)

    def test_add_module_answers_with_the_line_too(self, env_ok):
        _, c, _, kicked = env_ok
        r = c.post("/pulse/class-modules", data={"action": "add", "module": "smlab.cz"})
        html = r.data.decode()
        frag = _find(html)
        assert 'hx-swap-oob="true"' in frag
        assert "reading smlab.cz" in _text(frag)
        assert kicked


def _selfcheck_fixture(env_ok_ctx, chip_noenv_client) -> dict:
    """The REAL renders the client pins run against. ``form_more`` is the same
    form after a probe that found one more class (a roster with a lab class
    added) -- what the after-probe rebuild fetches."""
    import copy
    from quam_state_manager.core import pulse_catalog as pc
    app, c, manifest, _ = env_ok_ctx
    roster = json.loads((_ROOT / "tests" / "golden" / "state_schema_modern.json")
                        .read_text(encoding="utf-8"))["pulse_roster"]
    more = copy.deepcopy(roster)
    rec = copy.deepcopy(roster["CosineBipolarPulse"])
    rec["canonical"] = "otherlab.custom.pulses.LabWigglePulse"
    rec["homes"] = ["otherlab.custom.pulses"]
    more["LabWigglePulse"] = rec
    try:
        pc.apply_env_overlay(roster)
        form_ok = c.get("/pulse/new").data.decode()
        pc.apply_env_overlay(more)
        form_more = c.get("/pulse/new").data.decode()
    finally:
        pc.apply_env_overlay(None)
    assert ">LabWigglePulse</option>" in form_more and ">LabWigglePulse</option>" not in form_ok
    state_env_schema.save_pulse_modules(app.instance_path, ["my_lab.nope"])
    manifest["pulse_modules"] = {
        "my_lab.nope": "error: ModuleNotFoundError: No module named 'my_lab'"}
    strip_failed = c.get("/pulse/new/env-strip").data.decode()
    return {"form_ok": form_ok, "form_more": form_more, "strip_failed": strip_failed,
            "form_noenv": chip_noenv_client.get("/pulse/new").data.decode()}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_pulses_classfind_client_fragcheck(env_ok, tmp_path):
    # a second, env-less app for the no-env form
    folder = tmp_path / "chip2"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    app2 = create_app(testing=True, instance_path=str(tmp_path / "_inst2"))
    c2 = app2.test_client()
    c2.post("/load", data={"folder": str(folder)})
    fx = tmp_path / "classfind_fixture.json"
    fx.write_text(json.dumps(_selfcheck_fixture(env_ok, c2)), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "pulses_classfind_fragcheck.cjs"), str(fx)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(_ROOT), timeout=180)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "ALL OK" in proc.stdout and proc.stdout.count("ok - ") >= 12, proc.stdout


def test_add_module_costs_one_probe_not_two(chip, monkeypatch):
    """The Add-module press kicks a probe; the strip it answers with runs the
    schema check, which saw the new set "not read yet" and queued a SECOND,
    identical probe behind the first -- the new classes showed only after
    both. A probe running with exactly the set it wants read answers it."""
    from quam_state_manager.web import routes
    app, c, tmp_path = chip
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")
    (Path(app.instance_path) / "config_generator.json").write_text(
        json.dumps({"selected_env_python": str(py)}), encoding="utf-8")
    store = app.config["contexts"][app.config["active_context"]]["store"]
    manifest = {"pulse_modules": {}, "sources": {}}
    monkeypatch.setattr(routes, "_live_env_manifest", lambda s: manifest)
    started, release = threading.Event(), threading.Event()
    reads = []

    def fake_probe(python_path, classes, instance_path=None, **kw):
        mods = state_env_schema.load_pulse_modules(instance_path)
        reads.append(mods)
        started.set()
        release.wait(10)
        return {"ok": True, "pulse_modules": {m: "ok" for m in mods}}
    monkeypatch.setattr(state_env_schema, "probe_state_schema", fake_probe)

    def attach(s, i, f, p, res):
        manifest["pulse_modules"] = dict(res["pulse_modules"])
    monkeypatch.setattr(routes, "_attach_probe_result", attach)
    routes._lab_reprobe_tried.clear()
    try:
        r = c.post("/pulse/class-modules", data={"action": "add", "module": "smlab.cz"})
        assert r.status_code == 200
        assert started.wait(10)
        c.get("/pulse/new/env-strip?after_probe=1")      # a poll tick while it runs
        release.set()
        deadline = time.time() + 10
        while time.time() < deadline and (routes._schema_warm_inflight
                                          or routes._schema_rerun_pending):
            time.sleep(0.05)
        time.sleep(0.2)
        assert reads == [["smlab.cz"]], reads            # ONE probe read the set
        assert not routes._schema_rerun_pending
        assert manifest["pulse_modules"] == {"smlab.cz": "ok"}
        html = c.get("/pulse/new/env-strip?after_probe=1").data.decode()
        assert "PulsesPage.reloadCreateForm" in html      # the form rebuilds itself
    finally:
        release.set()
        routes._lab_reprobe_tried.clear()
