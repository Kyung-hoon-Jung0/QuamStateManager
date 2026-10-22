"""The floating CZ plan header follows the pane, horizontal scroll, and sorting."""
import os
import subprocess
from pathlib import Path

from tests.test_cz_plan_page import _client


def test_cz_plan_floating_header(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    page = tmp_path / "page.html"
    page.write_text(_client(tmp_path).get("/cz-plan", headers={"HX-Request": "true"}).get_data(as_text=True), encoding="utf-8")
    result = subprocess.run(["node", str(root / "tests/cz_plan_float_head_selfcheck.cjs"), str(page)],
                            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "floating header assertions passed" in result.stdout
