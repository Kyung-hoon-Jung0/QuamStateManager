"""w9 final QA (Pulses page), three user-visible defects:

* P2 -- another window's edit: the open inspector must follow it the way its row
  does (htmx's per-element `last` queue dropped the inspector re-fetch when the
  poll's tray GETs shared document.body with it);
* P3 -- the Json Tree's /pulses/goto link (and any ?pulse= address) to a pulse
  that is not on page 1 of its owner/tab lands on page 1 without the row; the
  virtual All view never scrolled to it;
* P3 -- a lab-refused Delete purged the waveform plot (htmx 2.0.4 raises
  beforeSwap on the ORIGINAL target of a retargeted response), and the section
  header kept "preview unavailable" after the class's own code drew the curve.

The client halves run in tests/pulses_fq_w9_selfcheck.cjs (real htmx + app.js
+ pulses.js under jsdom) and tests/pulses_vt_selfcheck.cjs section P.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app
from tests.test_pulses_routes import QC, _make_state, _make_wiring

_ROOT = Path(__file__).resolve().parents[1]
XY = "qubits.qA1.xy.operations"
N_EXTRA = 120            # q30 xy on big30x had 105; three pages at 50


def _state() -> dict:
    st = _make_state()
    ops = st["qubits"]["qA1"]["xy"]["operations"]
    for i in range(N_EXTRA):
        ops[f"op_{i:03d}"] = {"__class__": QC + "SquarePulse", "length": 16 + i, "amplitude": 0.1}
    return st


@pytest.fixture
def client(tmp_path):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    return c


def _page(html: str) -> int:
    m = re.search(r'data-current-page="(\d+)"', html)
    assert m, "no pagination rendered"
    return int(m.group(1))


def _row_paths(html: str) -> list:
    return re.findall(r'<tr class="clickable-row" data-pulse-path="([^"]+)"', html)


class TestTheAddressLandsOnThePageThatHoldsThePulse:
    def test_owner_tab_link_opens_the_page_with_the_row(self, client):
        rows = _row_paths(client.get("/pulses?owner=qA1&channel=xy&per_page=0").get_data(as_text=True))
        assert len(rows) > 100
        far = rows[-2]                                  # the 105th-of-105 case
        want_page = (len(rows) - 2) // 50 + 1
        assert want_page >= 3
        url = client.get(f"/pulses/goto?path={far}&json=1").get_json()["url"]
        assert "page=" not in url and f"pulse={far}" in url
        html = client.get(url).get_data(as_text=True)
        assert _page(html) == want_page
        assert far in _row_paths(html), "the open pulse's row is on the page the link lands on"
        assert f'data-open-pulse="{far}"' in html

    def test_an_explicit_page_is_the_readers_choice(self, client):
        rows = _row_paths(client.get("/pulses?owner=qA1&channel=xy&per_page=0").get_data(as_text=True))
        far = rows[-2]
        html = client.get(f"/pulses?owner=qA1&channel=xy&page=1&pulse={far}").get_data(as_text=True)
        assert _page(html) == 1 and far not in _row_paths(html)

    def test_a_pulse_on_page_one_and_one_outside_the_filter_change_nothing(self, client):
        rows = _row_paths(client.get("/pulses?owner=qA1&channel=xy&per_page=0").get_data(as_text=True))
        html = client.get(f"/pulses?owner=qA1&channel=xy&pulse={rows[3]}").get_data(as_text=True)
        assert _page(html) == 1 and rows[3] in _row_paths(html)
        # not in this tab's rows: the default page, the inspector still opens it
        html = client.get(f"/pulses?channel=resonator&pulse={rows[-2]}").get_data(as_text=True)
        assert _page(html) == 1 and f'data-open-pulse="{rows[-2]}"' in html

    def test_the_rows_refresh_is_untouched(self, client):
        rows = _row_paths(client.get("/pulses?owner=qA1&channel=xy&per_page=0").get_data(as_text=True))
        html = client.get(f"/pulses?rows=1&owner=qA1&channel=xy&pulse={rows[-2]}").get_data(as_text=True)
        assert _page(html) == 1


def test_the_loader_names_the_pulse_for_the_reveal(client):
    html = client.get(f"/pulses?pulse={XY}.x180_DragCosine").get_data(as_text=True)
    tag = re.search(r'<div id="pulse-open-loader"[^>]*>', html).group(0)
    assert f'data-open-pulse="{XY}.x180_DragCosine"' in tag


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_pulses_fq_w9_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "pulses_fq_w9_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.count("ok - ") >= 25, proc.stdout
