"""v1.5.1 T34-F1 / T34-F2: the time-zone picker.

* F1 -- in light mode the zone names and offsets were white on the near-white
  list: each row is a <button>, Pico sets a button's ``--pico-color`` to the
  primary-inverse (white), and the row's ``color: var(--pico-color)`` read
  that. The row now inherits the page's text colour (jsdom cannot evaluate
  Pico's button rule -- its selector list holds ``::file-selector-button`` --
  so the pin reads the two shipped rules that decide it).
* F2 -- searching exactly "UTC" omitted UTC (offset-zero zones, alphabetical,
  capped at 12). An exact name comes first now. Pinned on the REAL landing
  fragment in ``tests/v151_zone_picker_selfcheck.cjs``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_project_time_without_projects import empty_lab, fragment  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "quam_state_manager" / "web" / "static"


def _rule(css: str, selector: str) -> str:
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert m, selector
    return re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)


def test_a_zone_row_reads_the_page_text_colour_not_the_buttons():
    pico = (STATIC / "pico.min.css").read_text(encoding="utf-8")
    # the trap: a button's --pico-color is the primary-inverse
    assert re.search(r"button\{--pico-background-color:var\(--pico-primary-background\);"
                     r"--pico-border-color:var\(--pico-primary-border\);--pico-color:var\(--pico-primary-inverse\)", pico)
    row = _rule((STATIC / "style.css").read_text(encoding="utf-8"), ".landing-tz-row")
    color = re.search(r"(?<![-\w])color:\s*([^;]+);", row).group(1).strip()
    assert color == "inherit", f"a zone row's text is {color!r}: on a <button> that is Pico's white"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_exactly_utc_lists_utc_first(empty_lab, tmp_path):  # noqa: F811
    _app, client, _cfg, _scope = empty_lab
    html = fragment(client)
    assert 'id="landing-tz"' in html and "data-tz-input" in html
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"fragment": html}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_zone_picker_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
