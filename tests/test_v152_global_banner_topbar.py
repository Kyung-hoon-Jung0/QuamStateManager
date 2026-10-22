"""Global banner sizing and accessible compact reload pins."""
import re
from pathlib import Path

import pytest
from html.parser import HTMLParser
from tests.test_chip_identity import _df_env, _state

class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = (tag, dict(attrs), tuple(self.stack))
        self.nodes.append(node)
        if tag not in {"input", "meta", "link", "br", "hr", "img"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def one(self, tag=None, **attrs):
        return next(node for node in self.nodes if (tag is None or node[0] == tag)
                    and all(node[1].get(key) == value for key, value in attrs.items()))


ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "quam_state_manager/web/static/style.css"


@pytest.mark.parametrize("selector, expected, forbidden", [
    (".cnb-note", r"font-size:", r"display:\s*block"),
    (".cnb-text code", r"padding:\s*0\s+0?\.3em", None),
    (".cnb-text code", r"text-overflow:\s*ellipsis", None),
    (".cnb-form input[type=text]", r"height:\s*1\.7em\s*;", None),
    (".cnb-form .cnb-use, .cnb-form .cnb-clear", r"max-width:\s*14rem", None),
])
def test_banner_css(selector, expected, forbidden):
    css = CSS.read_text(encoding="utf-8")
    rule = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css).group(1)
    assert re.search(expected, rule), rule
    assert not forbidden or not re.search(forbidden, rule), rule


def test_no_short_reload_css():
    assert ".rl-short" not in CSS.read_text(encoding="utf-8")


def test_dangling_banner_title_and_open_form(tmp_path):
    missing = str(tmp_path / "unreachable" / "folder")
    _, client, _, _ = _df_env(tmp_path, _state(chip_name="1", data_folder=missing))
    page = Elements(client.get("/qubits").get_data(as_text=True))
    banner = page.one(id="chip-name-banner")
    assert "cnb-dangling" in banner[1]["class"]
    code = next(node for node in page.nodes if node[0] == "code" and banner in node[2])
    assert code[1].get("title") == missing
    form = next(node for node in page.nodes if node[0] == "form" and banner in node[2])
    assert all(parent[0] != "details" for parent in form[2])
    assert form in page.one(id="cnb-df-input")[2]


def test_confirm_form_remains_open(tmp_path):
    _, client, _, _ = _df_env(tmp_path, _state())
    missing = str(tmp_path / "unreachable")
    response = client.post("/chip-data-folder/set", data={"value": missing})
    assert response.status_code == 409
    page = Elements(response.get_data(as_text=True))
    form = page.one("form", **{"class": "cnb-form"})
    assert any("cnb-dangling" in parent[1].get("class", "") for parent in form[2])
    assert all(parent[0] != "details" for parent in form[2])
    assert form in page.one(name="force_cross")[2]
    assert client.post("/chip-data-folder/set", data={"value": missing, "force_cross": "1"}).status_code == 200


def test_topbar_reload_accessible_whole_label(tmp_path):
    _, client, _, _ = _df_env(tmp_path, _state())
    html = client.get("/qubits").get_data(as_text=True)
    page = Elements(html)
    button = page.one(id="topbar-reload")
    assert button[1].get("aria-label") == "Reload"
    assert button in page.one("span", **{"class": "rl-long"})[2]
    assert not any(node[1].get("class") == "rl-short" for node in page.nodes)
    assert '> Re<' not in html
