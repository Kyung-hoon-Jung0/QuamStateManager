"""B3 (U22 / T1-F5): the running SM version is visible where a user looks.

A newcomer looked in the top bar, Settings and Help and found no version;
only the printable chip report carried it. The version is now the first line
of Settings, part of the Help header, and the tooltip of the app title.
These pins read the RENDERED pages.
"""

from __future__ import annotations

import re

import pytest

from quam_state_manager import __version__
from quam_state_manager.web.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


def test_settings_first_line_is_the_version(client):
    html = client.get("/").get_data(as_text=True)
    start = html.index('id="settings-dropdown"')
    head_end = html.index("</div>", html.index('id="settings-header"', start))
    first_group = html.index('class="settings-group"', start)
    m = re.search(r'id="settings-version"[^>]*>([^<]*)<', html[head_end:first_group])
    assert m, "no version line between the Settings header and its first group"
    assert __version__ in m.group(1)


def test_help_header_names_the_version(client):
    for url, hx in (("/help", False), ("/help", True)):
        headers = {"HX-Request": "true"} if hx else {}
        html = client.get(url, headers=headers).get_data(as_text=True)
        m = re.search(r'id="help-version"[^>]*>([^<]*)<', html)
        assert m and __version__ in m.group(1), (url, hx)
        assert html.index('id="help-version"') < html.index("Keyboard shortcuts")


def test_app_title_tooltip_names_the_version(client):
    html = client.get("/").get_data(as_text=True)
    m = re.search(r'<a href="/\?landing=1" class="app-title-link" title="([^"]*)"', html)
    assert m, "the title link lost its landing href"
    assert __version__ in m.group(1) and "Projects landing" in m.group(1)
