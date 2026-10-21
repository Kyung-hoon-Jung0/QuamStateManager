"""CZ gate plan table follows the map's Operation picker and sorts by a clicked header."""
import os
import subprocess
from pathlib import Path

import pytest

from tests.test_cz_plan_page import _client


def test_the_table_follows_the_map_picker_and_sorts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    probe = subprocess.run(["node", "-e", "require('jsdom')"], cwd=root, env=os.environ.copy(),
                           capture_output=True, text=True)
    if probe.returncode:
        pytest.skip("jsdom not installed")
    html = _client(tmp_path).get("/cz-plan", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'data-s-freq="' in html and 'data-sort-key="delta"' in html and 'id="czp-filter-note"' in html
    page = tmp_path / "page.html"
    page.write_text(html, encoding="utf-8")
    result = subprocess.run(["node", str(root / "tests/cz_plan_sort_selfcheck.cjs"), str(page)], cwd=root,
                            env=os.environ.copy(), capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "14 assertions passed" in result.stdout
