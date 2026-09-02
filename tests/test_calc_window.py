"""docs/156 — the Calculator as its OWN browser window (browser mode).

User feedback: the calculator floats, but only INSIDE the SM window. The
popover's ↗ now opens the same calculator via ``window.open`` as a separate
browser window (movable across monitors, outliving navigation). These pin:

- the ``/calc-window`` route: a standalone document (no shell, no htmx/app.js,
  never touches a chip)
- the ONE-partial contract (``_calc_body.html``) — both surfaces render the
  same fields, and every id calc.js reads exists there
- the popover's ↗ door + its desktop gate
- the node selfcheck (``tests/calc_window_selfcheck.cjs``) that drives calc.js
  in both worlds under jsdom
"""

from __future__ import annotations

import inspect
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_BASE = (_TPL / "base.html").read_text(encoding="utf-8")
_WIN = (_TPL / "calc_window.html").read_text(encoding="utf-8")
_BODY = (_TPL / "_calc_body.html").read_text(encoding="utf-8")
_CALC = (_STATIC / "calc.js").read_text(encoding="utf-8")
_CSS = (_STATIC / "style.css").read_text(encoding="utf-8")


def _client(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()


class TestRoute:
    def test_renders_a_standalone_document(self, tmp_path):
        r = _client(tmp_path).get("/calc-window")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert 'id="calc-popover"' in body and "calc-standalone" in body
        assert "calc.js" in body and "style.css" in body and "pico.min.css" in body
        # no shell: the window is the frame
        for absent in ('id="sidebar"', "<aside", 'class="topbar"', "htmx.min.js",
                       "app.js", "float-panel.js", "bundle-manifest", "calc-header",
                       "calc-popout", "calc-hidden"):
            assert absent not in body, absent

    def test_every_field_renders(self, tmp_path):
        body = _client(tmp_path).get("/calc-window").get_data(as_text=True)
        for fid in ("calc-s1-dp", "calc-s1-amp", "calc-s1-from", "calc-s1-to", "calc-s1-k", "calc-s1-anew",
                    "calc-s2-fsp", "calc-s2-amp", "calc-s2-target", "calc-s2-dbm", "calc-s2-anew",
                    "calc-s3-dbm", "calc-s3-r", "calc-s3-pmw", "calc-s3-vrms", "calc-s3-vpk", "calc-s3-vpp",
                    "calc-s4-rf", "calc-s4-lo", "calc-s4-if", "calc-s4-note", "calc-expr", "calc-expr-res"):
            assert f'id="{fid}"' in body, fid

    def test_never_touches_a_chip(self):
        """The calculator is chip-independent; rendering it must not run the
        shared page context (which self-heals live drift, resolves the active
        chip, …) — a calculator window opened beside a chip must be inert."""
        from quam_state_manager.web import routes
        src = inspect.getsource(routes.calc_window).replace(routes.calc_window.__doc__ or "", "")
        assert "_ctx(" not in src and "_store(" not in src
        assert 'render_template("calc_window.html")' in src

    def test_works_with_and_without_a_chip(self, tmp_path):
        c = _client(tmp_path)
        assert c.get("/calc-window").status_code == 200
        live = tmp_path / "chip"
        live.mkdir()
        (live / "state.json").write_text('{"qubits": {"q1": {"id": "q1", "f_01": 5e9}}, "active_qubit_names": ["q1"]}',
                                         encoding="utf-8")
        (live / "wiring.json").write_text('{"network": {"host": "1.1.1.1", "cluster_name": "C"}}', encoding="utf-8")
        assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        r = c.get("/calc-window?theme=light")
        assert r.status_code == 200
        assert "Content-Security-Policy" in r.headers


class TestOneSource:
    """Both surfaces render the same partial — the fields live in exactly one
    file, so the popover and the window can never drift apart."""

    def test_both_surfaces_include_the_partial(self):
        assert "{% include '_calc_body.html' %}" in _BASE
        assert "{% include '_calc_body.html' %}" in _WIN
        for tpl in (_BASE, _WIN):
            assert 'id="calc-s1-dp"' not in tpl and 'id="calc-expr"' not in tpl

    def test_every_id_calc_js_reads_exists_in_the_partial(self):
        ids = set(re.findall(r"""['"](calc-(?:s\d-[a-z]+|expr(?:-res)?))['"]""", _CALC))
        assert {"calc-s1-dp", "calc-s4-note", "calc-expr-res"} <= ids  # sanity: the scan found them
        missing = sorted(i for i in ids if f'id="{i}"' not in _BODY)
        assert not missing, missing

    def test_the_theme_boot_is_shared(self):
        """A window opened from a page must look like that page: one boot
        script (theme, font size, UI scale) for every full document."""
        boot = (_TPL / "_theme_boot.html").read_text(encoding="utf-8")
        assert "localStorage.getItem('quam_theme')" in boot
        assert "get('theme')" in boot and "quam_ui_scale" in boot and "data-font-size" in boot
        assert "{% include '_theme_boot.html' %}" in _BASE
        assert "{% include '_theme_boot.html' %}" in _WIN
        assert "localStorage.getItem('quam_theme')" not in _BASE  # moved, not copied

    def test_the_window_document_is_light(self):
        """No htmx, no app.js, no bundles — the calculator needs calc.js and
        the stylesheet. Anything more is a slower window for nothing."""
        scripts = re.findall(r"<script src=\"[^\"]*?([\w\-\.]+\.js)", _WIN)
        assert scripts == ["calc.js"], scripts
        assert 'class="calc-window"' in _WIN and "calc-standalone" in _WIN


class TestPopoutDoor:
    def test_the_popover_header_carries_the_door(self):
        head = _BASE[_BASE.index('id="calc-header"'):_BASE.index("{% include '_calc_body.html' %}")]
        assert 'class="calc-close calc-popout"' in head
        assert 'onclick="openCalcWindow(this)"' in head
        assert "data-calc-window-url=\"{{ url_for('main.calc_window') }}\"" in head
        # the ↗ sits BEFORE the × (close stays the last thing in the corner)
        assert head.index("calc-popout") < head.index('onclick="toggleCalc()"')
        assert "calc-header-tools" in head  # inside the drag-excluded tools span

    def test_the_door_renders_the_route(self, tmp_path):
        page = _client(tmp_path).get("/").get_data(as_text=True)
        assert 'data-calc-window-url="/calc-window"' in page
        assert page.count("calc-popout") == 1

    def test_calc_js_contract(self):
        assert "window.openCalcWindow = function" in _CALC
        assert "var WIN_NAME = 'quam-calc';" in _CALC
        assert "var WIN_KEY = 'quam_calc_win';" in _CALC
        # the desktop gate — window.open under pywebview navigates the app away
        assert "if (window.pywebview) return null;" in _CALC
        assert "window.addEventListener('pywebviewready', hidePopout)" in _CALC
        # a live window is focused instead of opening a second calculator
        assert "if (willOpen && calcWinAlive())" in _CALC
        # the standalone document: Escape closes the window, no drag/outside-click
        assert "if (standalone()) { wireStandalone(); return; }" in _CALC
        assert "if (standalone()) { try { window.close(); }" in _CALC

    def test_stylesheet_frames_the_window(self):
        rule = _CSS[_CSS.index(".calc-popover.calc-standalone {"):][:260]
        assert "position: static" in rule and "resize: none" in rule and "box-shadow: none" in rule
        assert "html.calc-window body" in _CSS
        assert ".calc-popout" in _CSS and ".calc-copy.calc-copied" in _CSS


_SELFCHECK = _ROOT / "tests" / "calc_window_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_calc_window_selfcheck():
    """Both worlds against the REAL calc.js under jsdom."""
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30, cwd=str(_ROOT))
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(_SELFCHECK)], capture_output=True, text=True, encoding="utf-8",
                       timeout=180, cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 30, r.stdout
