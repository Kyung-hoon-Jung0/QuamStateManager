"""docs/294: `/` answers an htmx request with the pane's partial, like every other page.

PaneState re-fetches the current URL into ``#table-pane`` after a Back it could
not restore (``htmx.ajax('GET', location.pathname, {target: '#table-pane'})``).
For every route that is a partial; ``/`` always returned the whole page, so a
Back onto it after (for example) a Versions Compare nested a second app shell
-- sidebar, topbar and all -- inside the main pane. A history-restore request
(``HX-History-Restore-Request``) and a full load still get the whole page.
"""

from __future__ import annotations

import json

import pytest

from quam_state_manager.web.app import create_app

HX = {"HX-Request": "true"}
RESTORE = {"HX-Request": "true", "HX-History-Restore-Request": "true"}


def _shell(html: str) -> bool:
    return 'id="sidebar"' in html or "<html" in html.lower()


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


@pytest.fixture
def chip_client(client, tmp_path):
    from tests.test_web import _make_state, _make_wiring
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    client.post("/load", data={"folder": str(d)})
    return client


def test_with_a_chip_open_an_htmx_request_gets_the_chip_status_partial(chip_client):
    html = chip_client.get("/", headers=HX).get_data(as_text=True)
    assert not _shell(html), "a second app shell inside the pane"
    assert 'id="topo-overview-tiles"' in html and 'id="agent-home"' not in html


def test_the_landing_answers_an_htmx_request_with_its_partial(client):
    html = client.get("/", headers=HX).get_data(as_text=True)
    assert not _shell(html)
    assert html.strip(), "the pane is not left empty"


def test_a_full_load_and_a_history_restore_still_get_the_whole_page(chip_client):
    for headers in ({}, RESTORE):
        r = chip_client.get("/", headers=headers)
        assert r.status_code == 302 and r.headers["Location"].endswith("/topology?view=overview"), headers
        html = chip_client.get("/", headers=headers, follow_redirects=True).get_data(as_text=True)
        assert _shell(html) and 'id="topo-overview-tiles"' in html, headers
