"""v1.3.0 (dsperf): the first /scheduler/status of a cold open does not import
the autofit engine (and matplotlib behind it) when no plan engine can exist and
no run was persisted -- measured ~0.5 s of the interpreter beside a chip's
history catch-up. The payload is the same: a persisted run still reaches the
badge, and a live engine module is still asked."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import quam_state_manager.core.autofit as autofit_pkg
from quam_state_manager.core import scheduler
from quam_state_manager.web.app import create_app

ENGINE = "quam_state_manager.core.autofit.engine"


@pytest.fixture
def cold(tmp_path, monkeypatch):
    """An app whose process has not imported the engine (restored afterwards)."""
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    monkeypatch.delitem(sys.modules, ENGINE, raising=False)
    monkeypatch.delattr(autofit_pkg, "engine", raising=False)
    return app


def test_a_cold_poll_does_not_import_the_autofit_engine(cold):
    body = cold.test_client().get("/scheduler/status").get_json()
    assert ENGINE not in sys.modules, "the badge poll imported the autofit engine"
    assert "autofit" not in body


def test_a_persisted_run_still_reaches_the_badge(cold):
    inst = Path(scheduler.scope_dir(cold.instance_path, None))
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "autofit_run.json").write_text(json.dumps(
        {"status": "done", "is_sim": True, "plan": {"name": "1q"},
         "review_queue": [{"x": 1}]}), encoding="utf-8")
    body = cold.test_client().get("/scheduler/status").get_json()
    assert body.get("autofit") == {"status": "done", "running": False, "sim": True,
                                   "plan": "1q", "current": None, "review_count": 1}


def test_the_state_file_name_is_the_engines(cold):
    from quam_state_manager.core.autofit import engine
    assert engine._STATE_FILE == "autofit_run.json"
