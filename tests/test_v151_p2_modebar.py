"""v1.5.1 U P2-4: Plotly's toolbar never covers the data or a label.

Chip Status > Trends drew its charts with a top margin of 8 px, so the toolbar
(top right, ~28 px) sat on the newest points and caught their hover; the CZ
plan's chip map (column names above the plot) used 30 px and the toolbar
covered the last column's name. Pinned in
``tests/v151_modebar_clear_selfcheck.cjs`` on the REAL Trends fragment and
the REAL CZ plan page + map/loss answers.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_cz_plan_page import _client
from tests.test_hub_drawer import _inline, sm  # noqa: F401 -- fixtures

ROOT = Path(__file__).resolve().parent.parent


def _selected(page: str) -> str:
    import re
    return re.search(r'id="czp-root" class="czp-root" data-selected="([^"]+)"', page).group(1)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_toolbar_clears_trends_and_the_chip_map(sm, tmp_path):  # noqa: F811
    trends = sm["client"].get("/topology/trends?metrics=T1",
                              headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'id="topo-trends-data"' in trends
    c = _client(tmp_path / "cz")
    page = c.get("/cz-plan", headers={"HX-Request": "true"}).get_data(as_text=True)
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"trends": trends, "czplan": page,
                              "map": c.get("/cz-plan/map").get_json(),
                              "loss": c.get("/cz-plan/loss").get_json(),
                              "data": c.get(f"/cz-plan/data?op={_selected(page)}").get_json()}),
                  encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_modebar_clear_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
