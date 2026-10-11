"""v1.3.0 A5: htmx keeps no page snapshots now (base.html historyCacheSize 0), so
every Back/Forward is a server history restore -- the request carries both
HX-Request and HX-History-Restore-Request, and the route must answer the FULL
page (htmx swaps it into <body>). /agent/setup read HX-Request alone and
answered its bare partial: a Back onto it lost the sidebar and top bar."""
import pytest

from quam_state_manager.web.app import create_app

RESTORE = {"HX-Request": "true", "HX-History-Restore-Request": "true"}


@pytest.fixture
def client(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()


@pytest.mark.parametrize("page", ["/agent/setup", "/help", "/journal", "/qubits", "/datasets"])
def test_a_history_restore_gets_the_whole_page(client, page):
    html = client.get(page, headers=RESTORE).get_data(as_text=True)
    assert 'id="sidebar"' in html and 'id="table-pane"' in html, (page, html[:200])


def test_a_menu_swap_still_gets_the_partial(client):
    html = client.get("/agent/setup", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'id="sidebar"' not in html
