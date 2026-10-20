"""Contrast pins resolve the actual outlined diagnostic consumer rules."""
import re
from tests.test_qm_theme import tokens, ratio, rules, STATIC
import subprocess
from pathlib import Path
import pytest


def consumer(selector, prop):
    matched = [body for sel, body in rules(STATIC / "style.css") if sel.strip() == selector]
    assert matched, selector
    return re.search(r"(?:^|;)\s*" + prop + r":\s*([^;]+)", matched[-1]).group(1).strip()


@pytest.mark.parametrize("selector", [".diag-ok", ".diag-ok-badge", ".diag-pill.diag-error", ".diag-pill.diag-warning",
    ".diag-mini.diag-error", ".diag-mini.diag-warning", ".diag-header-badge.diag-error", ".diag-header-badge.diag-warning", ".diag-header-badge.diag-ok-badge"])
def test_outlined_diagnostic_text_on_card(selector):
    palette = tokens("qm")
    value = consumer(selector, "color")
    token = re.fullmatch(r"var\((--[\w-]+)\)", value)
    assert token, value
    assert ratio(palette[token[1]], palette["--pico-card-background-color"]) >= 4.5, (selector, value)


def test_qm_headings_use_the_heading_property():
    assert consumer('[data-theme="qm"] :is(h1,h2,h3,h4,h5,h6,.app-title)', "color") == "var(--pico-h1-color)"


def test_wiring_border_consumes_palette():
    r = subprocess.run(["node", str(Path(__file__).with_name("v152b_consumers.cjs"))], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
