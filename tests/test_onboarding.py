"""docs/115 (#14) — the first hour.

Pins: /help exists at a permanent address and serves the SAME mental-model
fragment the landing shows (one source, no drift) plus the shortcut
reference; every page's sidebar links to it; the landing offers a real
"open a folder" CTA instead of a muted sentence; and the tray carries the
working-copy teaching line (dismissible once).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}
_STATE = {"qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9}},
          "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


class TestHelpPage:
    def test_help_serves_the_mental_model_and_shortcuts(self, client):
        r = client.get("/help")
        assert r.status_code == 200
        html = r.data.decode("utf-8")
        # the shared getting-started fragment (glossary) — one source
        assert "Working state" in html
        assert "Live chip" in html
        assert "Snapshot" in html
        # and the shortcut reference
        assert "help-shortcuts" in html
        assert "Ctrl</kbd>+<kbd>D" in html or "Ctrl+D" in html
        assert "Apply to live" in html

    def test_help_is_reachable_from_every_page(self, client, tmp_path):
        live = tmp_path / "chip"
        live.mkdir()
        (live / "state.json").write_text(json.dumps(_STATE), encoding="utf-8")
        (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
        assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        for page in ("/qubits", "/bulk", "/datasets"):
            html = client.get(page).data.decode("utf-8")
            assert 'href="/help"' in html, page

    def test_help_partial_for_htmx(self, client):
        r = client.get("/help", headers={"HX-Request": "true"})
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "<html" not in body.lower()      # a fragment, not a full page
        assert "help-page" in body


class TestLandingCta:
    def test_landing_offers_an_open_folder_button(self, client):
        html = client.get("/").data.decode("utf-8")
        assert "landing-cta" in html
        assert "Open a state folder" in html
        assert 'href="/help"' in html


class TestTrayTeaching:
    def test_tray_carries_the_working_copy_teaching(self, client, tmp_path):
        live = tmp_path / "chip2"
        live.mkdir()
        (live / "state.json").write_text(json.dumps(_STATE), encoding="utf-8")
        (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
        assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        html = client.get("/state/tray").data.decode("utf-8")
        assert "tray-teach" in html
        assert "working state" in html
        assert "Apply to live" in html
        assert "Revert last apply" in html      # says it is reversible
        assert "dismissTrayTeach" in html       # dismissible once


def test_client_dismiss_is_localstorage_gated():
    app_js = (Path(__file__).resolve().parent.parent / "quam_state_manager"
              / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "quam_tray_teach_done" in app_js
    assert "window.dismissTrayTeach" in app_js
