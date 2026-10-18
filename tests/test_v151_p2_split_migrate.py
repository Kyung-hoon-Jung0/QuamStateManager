"""v1.5.1 T2-F3: a split position dragged in an older SM is carried over.

SM 1.3.0 kept a dragged split only in ``quam_split_sizes``; this version reads
the user's last action from ``quam_split_user``, so the upgraded dataset
inspector opened at its default instead of the dragged height. Once per
browser, with no choice recorded yet, the old drag becomes the last action --
never one of SM's own fixed positions, never twice. Pinned on the split
script of a REAL rendered page in ``tests/v151_split_migrate_selfcheck.cjs``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "quam_state_manager" / "web" / "static" / "app.js"


def _ui_split() -> dict:
    src = APP_JS.read_text(encoding="utf-8")
    block = src[src.index("    split: {"):src.index("    },", src.index("    split: {"))]
    out = {}
    for key in ("defaultSizes", "expandedSizes", "collapsedSizes", "datasetExpandedSizes", "minSizes"):
        m = re.search(key + r":\s*(\[[^\]]+\])", block)
        out[key] = json.loads(m.group(1))
    out["gutterSize"] = int(re.search(r"gutterSize:\s*(\d+)", block).group(1))
    return out


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_an_old_drag_becomes_the_last_action_once(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    page = app.test_client().get("/").get_data(as_text=True)
    assert "__smBase.replace('split')" in page
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page, "split": _ui_split()}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_split_migrate_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
