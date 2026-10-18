"""v1.5.1 (perf) -- the waveform-synthesis modules load off the request path.

The Diagnostics badge, banner and findings a page fetches right after a chip
opens all wait on the chip's first lint, which imports scipy's window and
filter modules (``waveform_synth.preload_scipy``). Measured on a restart of a
copy of a customer chip: 1.4-2.3 s each, 2.1 s of it that import. The server
start now imports them first thing on its warm-up thread (then the templates),
one module at a time and only while no page is being served; a chip open
starts the same import if nothing has; a lint that comes first imports what is
left itself, as before.

Pinned: a chip open starts it on its own thread; each module waits while a
page is being served; it runs once per process; the server start runs it
before the template warm-up.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import activity, waveform_synth
from quam_state_manager.web import app as app_mod
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


@pytest.fixture
def fresh(monkeypatch):
    """A process that has not imported them yet; steps and the final import recorded."""
    monkeypatch.setattr(app_mod, "_purge_test_leftovers", lambda _p: None)
    monkeypatch.setattr(waveform_synth, "_PRELOAD", {"thread": None})
    monkeypatch.setattr(waveform_synth, "_scipy_loaded", lambda: False)
    rec = {"steps": [], "threads": set(), "done": threading.Event(), "busy": False}

    def step(name):
        rec["steps"].append(name)
        rec["threads"].add(threading.current_thread().name)

    def final():
        # only the preload counts: a lint elsewhere in the process (another
        # test's leftover warm-up) imports through the same function
        if threading.current_thread().name in rec["threads"]:
            rec["done"].set()
    monkeypatch.setattr(waveform_synth, "_import_step", step)
    monkeypatch.setattr(waveform_synth, "_import_scipy", final)
    monkeypatch.setattr(activity, "busy", lambda *a, **k: rec["busy"])
    return rec


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "T1": 1e-5}}, "qubit_pairs": {},
         "active_qubit_names": ["q1"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    return folder


def test_opening_a_chip_starts_the_import_on_its_own_thread(tmp_path, fresh):
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    app.config["SM_SYNTH_PRELOAD_IN_TESTS"] = True
    assert app.test_client().post("/load", data={"folder": str(_chip(tmp_path / "chip"))}).status_code in (200, 302)
    assert fresh["done"].wait(10), "opening a chip did not start the synthesis-module import"
    assert fresh["threads"] == {"sm-scipy-preload"}
    assert fresh["steps"] == list(waveform_synth._PRELOAD_STEPS)


def test_the_suite_imports_nothing_in_the_background_unless_asked(tmp_path, fresh):
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    app.test_client().post("/load", data={"folder": str(_chip(tmp_path / "chip"))})
    assert not fresh["done"].wait(0.5) and fresh["steps"] == []


def test_each_module_waits_while_a_page_is_served(fresh, monkeypatch):
    real_step = waveform_synth._import_step

    def step_then_a_page_arrives(name):
        real_step(name)
        fresh["busy"] = True                # a request starts right after this module
    monkeypatch.setattr(waveform_synth, "_import_step", step_then_a_page_arrives)
    assert waveform_synth.preload_scipy_soon() is True
    time.sleep(0.5)
    assert fresh["steps"] == [waveform_synth._PRELOAD_STEPS[0]], fresh["steps"]
    assert not fresh["done"].is_set()
    monkeypatch.setattr(waveform_synth, "_import_step", real_step)
    fresh["busy"] = False
    assert fresh["done"].wait(5)
    assert fresh["steps"] == list(waveform_synth._PRELOAD_STEPS)


def test_it_runs_once_per_process(fresh):
    assert waveform_synth.preload_scipy_soon() is True
    assert fresh["done"].wait(5)
    assert waveform_synth.preload_scipy_soon() is False
    assert waveform_synth.preload_scipy_quietly() is False
    assert fresh["steps"] == list(waveform_synth._PRELOAD_STEPS)


def test_the_server_start_imports_them_before_the_templates(tmp_path, monkeypatch, fresh):
    from quam_state_manager.core import config_generator
    order: list[str] = []
    ran = threading.Event()
    monkeypatch.delenv("SM_DISABLE_ENV_WARMUP", raising=False)
    monkeypatch.setenv("SM_GRANT_WATCH_S", "0")
    monkeypatch.setattr(config_generator, "discover_envs", lambda *a, **k: [])
    import jinja2
    monkeypatch.setattr(waveform_synth, "preload_scipy_quietly",
                        lambda **k: order.append("synthesis modules") or True)
    real_get = jinja2.Environment.get_template

    def compiled(self, name, *a, **k):
        if "templates" not in order:
            order.append("templates")
        return real_get(self, name, *a, **k)
    monkeypatch.setattr(jinja2.Environment, "get_template", compiled)
    real_warm = app_mod.warm_templates
    monkeypatch.setattr(app_mod, "warm_templates", lambda app, **kw: (real_warm(app, **kw), ran.set()))
    create_app(testing=False, instance_path=str(tmp_path / "inst"))
    assert ran.wait(60)
    assert order[:2] == ["synthesis modules", "templates"], order
