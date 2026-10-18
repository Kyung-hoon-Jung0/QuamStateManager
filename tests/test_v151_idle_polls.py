"""v1.5.1 U P2-28: idle polling is cut where nothing can be shown.

/scheduler/status polled every 2.5 s although the Experiment Runner UI is
hidden without SM_EXPERIMENTAL; the QUAlibrate banner and the Datasets
running-data hint polled every 3 s from hidden tabs. Pinned on the REAL
rendered page (with and without SM_EXPERIMENTAL) and the REAL /datasets
fragment under jsdom (tests/v151_idle_polls_selfcheck.cjs): one look then
silence without the runner UI (a queue left running is still followed), the
same polling with it, and no htmx poll from a hidden tab, one on return.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
HX = {"HX-Request": "true"}


def test_idle_and_hidden_tabs_stop_polling(tmp_path, monkeypatch):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    monkeypatch.delenv("SM_EXPERIMENTAL", raising=False)
    plain = c.get("/help").get_data(as_text=True)
    datasets = c.get("/datasets", headers=HX).get_data(as_text=True)
    monkeypatch.setenv("SM_EXPERIMENTAL", "1")
    runner = c.get("/help").get_data(as_text=True)
    assert 'data-sm-runner="1"' in runner and 'data-sm-runner="1"' not in plain
    assert 'id="qualibrate-running-data-slot"' in datasets
    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps({"page_plain": plain, "page_runner": runner,
                              "datasets_html": datasets}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_idle_polls_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
