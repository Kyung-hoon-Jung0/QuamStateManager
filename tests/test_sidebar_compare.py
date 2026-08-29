"""docs/141 4y -- the sidebar's compare ticks: capped at five, kept across a
tree re-render and a reload, Compare disabled below two, HX-Trigger toast
bridge (tests/sidebar_compare_selfcheck.cjs under jsdom). The route side
(/compare -> HX-Location into #table-pane, 2..5 -> /diff a..e, figures first)
is pinned in test_web.py::TestCompareRedirect and test_compare_hub_routes.py."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_sidebar_compare_selfcheck():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "sidebar_compare_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ok - the SIXTH tick is refused" in res.stdout


def test_one_number_everywhere():
    """The sidebar's cap, the route's cap and the slot list agree."""
    js = (_ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
    py = (_ROOT / "quam_state_manager/web/routes.py").read_text(encoding="utf-8")
    assert "var MAX_DIFF = 5;" in js
    assert '_DIFF_MAX_SOURCES = 5' in py and '_DIFF_SLOTS = "abcde"' in py
    assert '_DIFF_TABS = ("figures", "state", "wiring", "node", "data")' in py


def test_the_workbench_carries_every_slot():
    tpl = (_ROOT / "quam_state_manager/web/templates/_diff_workbench.html").read_text(encoding="utf-8")
    assert 'name="d" value="{{ d_ref }}"' in tpl and 'name="e" value="{{ e_ref }}"' in tpl
    assert "compare-hub" not in tpl.replace("{# docs/141 4y: the Compare hub is retired as a destination -- no link. #}", "")
    # every tab / view / paging link carries d and e beside c
    assert tpl.count("&amp;c={{ c_ref | urlencode }}") == tpl.count("&amp;d={{ d_ref | urlencode }}&amp;e={{ e_ref | urlencode }}")
