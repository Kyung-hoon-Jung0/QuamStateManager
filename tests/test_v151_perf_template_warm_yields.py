"""v1.5.1 (perf) -- the template warm-up yields to the page being served.

The server compiles every template on a daemon thread at start (w7 liveedit,
RAM P5), exactly when the browser asks for the landing. Measured on a restart
of a copy of a customer chip: the landing's first request took 1.06 s with
the warm-up compiling beside it the whole time, 0.43 s once it waits while a
request is in flight (``activity.busy``, the one yield-to-foreground rule).

Pinned: nothing is compiled while a page is being served; it continues once
the server is quiet; and a server that never goes quiet still gets its
templates warmed after ``_TPL_WARM_YIELD_MAX_S``.
"""
from __future__ import annotations

import threading
import time

from quam_state_manager.core import activity
from quam_state_manager.web import app as app_mod
from quam_state_manager.web.app import create_app, warm_templates


def _app(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "_purge_test_leftovers", lambda _p: None)
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    compiled: list[str] = []
    real = app.jinja_env.get_template

    def spy(name, *a, **k):
        compiled.append(name)
        return real(name, *a, **k)
    monkeypatch.setattr(app.jinja_env, "get_template", spy)
    return app, compiled


def test_nothing_compiles_while_a_page_is_served(tmp_path, monkeypatch):
    app, compiled = _app(tmp_path, monkeypatch)
    state = {"busy": True}
    monkeypatch.setattr(activity, "busy", lambda *a, **k: state["busy"])
    t = threading.Thread(target=warm_templates, args=(app,), daemon=True)
    t.start()
    time.sleep(0.4)
    assert compiled == [], f"compiled while a request was in flight: {compiled[:3]}"
    state["busy"] = False
    t.join(60)
    assert not t.is_alive() and len(compiled) > 10


def test_a_server_that_never_goes_quiet_still_gets_them(tmp_path, monkeypatch):
    app, compiled = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(activity, "busy", lambda *a, **k: True)
    monkeypatch.setattr(app_mod, "_TPL_WARM_YIELD_MAX_S", 0.2)
    t0 = time.monotonic()
    n = warm_templates(app)
    assert n > 10 and len(compiled) >= n
    assert time.monotonic() - t0 < 30
