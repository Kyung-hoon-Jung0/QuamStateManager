"""Pre-customer hardening: malformed numeric params must never 500 a route, and
no-chip menus must render a clear empty state (not a tiny 'dead-click' toast).

Covers the red-team crash sweep (unguarded int()/float() of query/form params)
and the dead-click UX fix.
"""
from __future__ import annotations

import pytest

from quam_state_manager.web.app import create_app
from quam_state_manager.web import routes as R


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


# ── _int_arg / _float_arg helpers ──────────────────────────────────────────
def test_int_arg_falls_back_and_floors():
    app = create_app()
    with app.test_request_context("/x?page=abc"):
        assert R._int_arg("page", 1, minimum=1) == 1          # non-numeric → default
    with app.test_request_context("/x?page=-5"):
        assert R._int_arg("page", 1, minimum=1) == 1          # floored
    with app.test_request_context("/x?page=7"):
        assert R._int_arg("page", 1, minimum=1) == 7          # valid
    with app.test_request_context("/x"):
        assert R._int_arg("page", 3) == 3                     # missing → default


def test_float_arg_falls_back():
    app = create_app()
    with app.test_request_context("/x?tol=abc"):
        assert R._float_arg("tol", 1e-12) == 1e-12
    with app.test_request_context("/x?tol=0.25"):
        assert R._float_arg("tol", 1e-12) == 0.25
    from werkzeug.datastructures import MultiDict
    with app.test_request_context():
        # form source
        assert R._float_arg("tol", 9.0, source=MultiDict({"tol": "bad"})) == 9.0


# ── Routes that parse numeric params BEFORE any chip guard ─────────────────
# (these previously 500'd on malformed input; now they must degrade cleanly)
MALFORMED_GET = [
    "/search?q=q1&limit=abc",
    "/api/search?q=q1&limit=abc",
    "/compare/diff?ref=abc",
    "/compare/state?idx=abc&paths=a&paths=b",
    "/compare/state?idx=-5&paths=a&paths=b",   # negative index (was IndexError)
    "/compare/state?ref=-99&paths=a&paths=b",
    "/chip-compare/topology?ref=abc",
    "/chip-compare/diff?ref=abc",
]


@pytest.mark.parametrize("url", MALFORMED_GET)
def test_malformed_get_never_500(client, url):
    resp = client.get(url)
    assert resp.status_code != 500, f"{url} → {resp.status_code}"


MALFORMED_POST = [
    ("/compare", {"ref": "abc", "paths": ["a", "b"]}),
    ("/chip-compare", {"ref": "abc"}),
    ("/diff", {"path_a": "/nope/a", "path_b": "/nope/b", "tolerance": "abc"}),
]


@pytest.mark.parametrize("url,form", MALFORMED_POST)
def test_malformed_post_never_500(client, url, form):
    resp = client.post(url, data=form)
    assert resp.status_code != 500, f"{url} → {resp.status_code}"


# ── Chip-gated paging routes: malformed page never 500 even with the guard ─
PAGING_MENUS = ["/qubits", "/pairs", "/table", "/pulses", "/state-history", "/api/history"]


@pytest.mark.parametrize("path", PAGING_MENUS)
def test_paging_menu_malformed_page_never_500(client, path):
    # No chip loaded → the empty-state guard returns before the parse; either way
    # the contract is "never 500".
    assert client.get(f"{path}?page=abc&per_page=xyz").status_code != 500


# ── Dead-click UX: no-chip menus render a real empty state ─────────────────
EMPTY_STATE_MENUS = ["/qubits", "/pairs", "/pulses", "/topology", "/explorer",
                     "/bulk", "/instrument", "/diagnostics", "/state-history",
                     "/param-history", "/table"]


@pytest.mark.parametrize("path", EMPTY_STATE_MENUS)
def test_no_chip_menu_shows_empty_state(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    body = resp.data.decode()
    # the clear, persistent empty state — not the old fading "No state loaded" toast
    assert "pane-empty" in body and "No chip loaded" in body
    assert "toast-warning" not in body
