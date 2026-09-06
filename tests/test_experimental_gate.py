"""docs/172: the execution trio is hidden unless SM_EXPERIMENTAL=1.

The customer's finding: nobody used Experiment Runner / Fit Replay / Auto
Calibrate -- they calibrate from a terminal agent. The code stays (routes
registered, tests green), every NAVIGATION surface goes: the sidebar, the
running-run badge, the Ctrl-K palette, the landing/help getting-started bullets
and the dataset page's "Autofit diagnose" button. A release red-team found the
palette and the help bullets, which a sidebar-only gate would have missed.

These pins read the RENDERED page, not the template source -- the r15 sidebar
pins read source and stay green under a Jinja gate, so they prove nothing here.
"""

from __future__ import annotations

import json
import re

import pytest

from quam_state_manager.web.app import create_app

TRIO = ('href="/scheduler"', 'href="/fit-audit"', 'href="/autofit"')
LABELS = ("Experiment Runner", "Fit Replay", "Auto Calibrate")


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


def _palette(html: str) -> list[str]:
    m = re.search(r'<script id="cmd-palette-data"[^>]*>(.*?)</script>', html, re.S)
    assert m, "palette data block missing"
    return [e["label"] for e in json.loads(m.group(1))["pages"]]


class TestHiddenByDefault:
    def test_sidebar_and_badge_carry_no_trio_link(self, client, monkeypatch):
        monkeypatch.delenv("SM_EXPERIMENTAL", raising=False)
        html = client.get("/").get_data(as_text=True)
        for needle in TRIO:
            assert needle not in html, needle
        assert 'id="scheduler-badge"' not in html
        assert 'id="autofit-nav-badge"' not in html

    def test_palette_offers_no_trio_page(self, client, monkeypatch):
        monkeypatch.delenv("SM_EXPERIMENTAL", raising=False)
        labels = _palette(client.get("/").get_data(as_text=True))
        assert not set(LABELS) & set(labels), labels
        assert "Datasets" in labels, "the rest of the palette is intact"

    def test_getting_started_does_not_teach_a_hidden_menu(self, client, monkeypatch):
        monkeypatch.delenv("SM_EXPERIMENTAL", raising=False)
        for url in ("/", "/help"):
            html = client.get(url).get_data(as_text=True)
            assert "<b>Experiment Runner</b>" not in html, url
            assert "<b>Fit Replay</b>" not in html, url

    def test_the_routes_still_answer(self, client, monkeypatch):
        """Hidden, not removed: a typed URL still renders (the decision), and
        the heartbeat every page polls is untouched."""
        monkeypatch.delenv("SM_EXPERIMENTAL", raising=False)
        assert client.get("/scheduler/status").status_code == 200
        assert client.get("/autofit").status_code == 200


class TestOptIn:
    def test_the_flag_brings_everything_back(self, client, monkeypatch):
        monkeypatch.setenv("SM_EXPERIMENTAL", "1")
        html = client.get("/").get_data(as_text=True)
        for needle in TRIO:
            assert needle in html, needle
        assert 'id="scheduler-badge"' in html
        labels = _palette(html)
        assert set(LABELS) <= set(labels), labels
        assert "<b>Experiment Runner</b>" in html

    def test_the_flag_is_read_per_request(self, client, monkeypatch):
        monkeypatch.setenv("SM_EXPERIMENTAL", "1")
        assert 'href="/autofit"' in client.get("/").get_data(as_text=True)
        monkeypatch.delenv("SM_EXPERIMENTAL")
        assert 'href="/autofit"' not in client.get("/").get_data(as_text=True)
