"""docs/206 — the Sync-Qualibrate shell may ask another localhost port whether
it is alive.

Customer (2026-09-25): Qualibrate on http://127.0.0.1:8001 opens fine in a
plain browser tab, but SM's ⌗ Sync-Qualibrate shows "Nothing answered at
http://127.0.0.1:8001". Measured in real Chrome with a stub server on 8001:
the shell's liveness probe is a fetch() to that port, SM's own
Content-Security-Policy says ``connect-src 'self'``, the browser refuses the
fetch ("Refused to connect because it violates the document's Content
Security Policy"), the probe reads that as "nothing there", and the failure
panel covers an iframe that had loaded.

The relaxation is scoped to the one page that needs it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    return app.test_client()


def _connect_src(csp: str) -> str:
    m = re.search(r"connect-src ([^;]+);", csp)
    assert m, csp
    return m.group(1).strip()


class TestTheShellMayProbeLocalhost:
    def test_workbench_may_connect_to_local_ports(self, client):
        csp = client.get("/workbench").headers["Content-Security-Policy"]
        src = _connect_src(csp)
        assert "http://127.0.0.1:*" in src and "http://localhost:*" in src, src
        assert "'self'" in src

    def test_every_other_page_keeps_connect_src_self(self, client):
        for path in ("/", "/help", "/generate"):
            r = client.get(path)
            assert _connect_src(r.headers["Content-Security-Policy"]) == "'self'", path

    def test_the_probe_is_still_a_fetch_to_the_address(self):
        """The relaxation exists for this line; if the probe changes shape,
        re-measure instead of keeping a dead relaxation."""
        html = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
                / "templates" / "workbench.html").read_text(encoding="utf-8")
        assert 'fetch(url, { mode: "no-cors"' in html
        assert "Nothing answered at" in html
