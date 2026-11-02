"""The Interactive tab's plot worker is started and its lab imports done when a
chip opens, so the first Interactive open does not wait for them."""
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app
from tests.test_lab_prewarm import _chip, _plain_state

SCRIPT = Path(__file__).resolve().parents[1] / "quam_state_manager" / "generator" / "run_interactive_replot.py"


def _settle():
    for t in list(threading.enumerate()):
        if t.name == "plot-prewarm-decide":
            t.join(timeout=40)


@pytest.fixture
def started(tmp_path, monkeypatch):
    from quam_state_manager.core import config_generator
    from quam_state_manager.web import routes
    calls = []
    env = {"python": sys.executable}
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: env["python"])
    monkeypatch.setattr(config_generator, "probe_capabilities", lambda *a, **k: {})
    monkeypatch.setattr(routes, "_warm_state_schema_async", lambda *a, **k: None)

    def make(**cfg):
        app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
        app.config["SM_PLOT_PREWARM_IN_TESTS"] = True
        app.config["SM_PLOT_PREWARM_STARTER"] = lambda py, why: calls.append((py, why))
        app.config.update(cfg)
        c = app.test_client()
        c._folder = _chip(tmp_path, _plain_state())
        return c
    yield make, calls, env


class TestWhenItStarts:
    def test_a_chip_open_with_an_env_starts_it(self, started):
        make, calls, _ = started
        c = make()
        assert c.post("/load", data={"folder": str(c._folder)}).status_code in (200, 302)
        _settle()
        assert calls == [(sys.executable, "chip-open")]

    def test_no_env_starts_nothing(self, started):
        make, calls, env = started
        env["python"] = None
        c = make()
        c.post("/load", data={"folder": str(c._folder)})
        _settle()
        assert calls == []

    def test_the_suite_never_spawns_unless_a_pin_asks(self, started):
        make, calls, _ = started
        c = make(SM_PLOT_PREWARM_IN_TESTS=False)
        c.post("/load", data={"folder": str(c._folder)})
        _settle()
        assert calls == []


class TestTheWarmRequest:
    def test_it_imports_and_answers_in_the_protocol_the_worker_reads(self):
        pytest.importorskip("matplotlib")
        pytest.importorskip("xarray")
        p = subprocess.run([sys.executable, "-B", str(SCRIPT), "--serve"],
                           input=json.dumps({"warm": True}) + "\n", capture_output=True,
                           text=True, timeout=120)
        line = [x for x in p.stdout.splitlines() if x.startswith("@@SM-IPLOT@@")]
        assert line, p.stdout + p.stderr
        ans = json.loads(line[0][len("@@SM-IPLOT@@"):])
        assert ans["status"] == "ok" and ans["warm"] is True
        assert isinstance(ans["sources"], dict)
        # no replay ran: nothing node-specific, no figures
        assert "figures" not in ans

    def test_a_replay_after_the_warm_reuses_the_same_worker(self, monkeypatch):
        from quam_state_manager.core.interactive_plots import replot
        made = []

        class W:
            context, python_path = "", sys.executable

            def __init__(self, *a, **k):
                made.append(self)

            def fresh(self):
                return True

            def ask(self, spec, timeout):
                return {"status": "ok", "warm": True, "sources": {}}

            def kill(self):
                pass
        from quam_state_manager.core import lab_worker
        monkeypatch.setattr(lab_worker, "Worker", W)
        monkeypatch.setattr(lab_worker, "retire_except", lambda *a, **k: None)
        monkeypatch.setattr(replot, "_PLOT_WORKER", None)
        assert replot.prewarm(sys.executable) == "warm"
        assert replot._PLOT_WORKER is made[0]
        assert replot.prewarm(sys.executable) == "already-warm"
        assert len(made) == 1
