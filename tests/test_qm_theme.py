"""QM has a complete dark palette, persistent controls and readable plots."""
import re
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "quam_state_manager/web/static"


def rules(path):
    text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", text)


def tokens(theme):
    out = {}
    for name in ("pico.min.css", "style.css"):
        for selector, body in rules(STATIC / name):
            if f'[data-theme="{theme}"]' in selector or f"[data-theme={theme}]" in selector:
                if selector.strip().endswith("]"):
                    out.update(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))
    return out


def ratio(fg, bg):
    def luminance(value):
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), value
        c = [int(value[i:i+2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in c]
        return sum(a * b for a, b in zip(c, (.2126, .7152, .0722)))
    a, b = sorted((luminance(fg), luminance(bg)))
    return (b + .05) / (a + .05)


def test_qm_defines_every_dark_property():
    dark, qm = tokens("dark"), tokens("qm")
    assert qm, "QM palette missing"
    assert not dark.keys() - qm.keys(), sorted(dark.keys() - qm.keys())
    assert qm["--pico-background-color"] == "#0e0e0e"
    assert qm["--pico-card-background-color"] == "#1d1d1d"
    # Match Pico's :root:not([data-theme=dark]) specificity, so QM wins.
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ':root[data-theme="qm"], [data-theme="qm"] {' in css
    # Pico's contrast-button icons need the same dark-surface override.
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert '[data-theme="qm"] details summary[role=button].contrast:not(.outline)::after { filter: brightness(0); }' in css
    assert '[data-theme="qm"] [aria-busy=true]:not(input,select,textarea).contrast:is(button,' in css


@pytest.mark.parametrize("background", ["--pico-background-color", "--pico-card-background-color",
                                         "--pico-form-element-background-color"])
def test_qm_main_text_contrast(background):
    qm = tokens("qm")
    assert qm, "QM palette missing"
    for fg in ("--pico-color", "--pico-muted-color", "--color-text-muted", "--pico-primary", "--color-accent"):
        assert ratio(qm[fg], qm[background]) >= 4.5, (fg, background)
    assert ratio(qm["--pico-primary-inverse"], qm["--pico-primary-background"]) >= 4.5


def test_qm_semantic_colours():
    qm = tokens("qm")
    assert qm, "QM palette missing"
    for family in (("--color-success-text", "--color-warning-text", "--color-error-text"),
                   ("--diff-added-text", "--diff-removed-text")):
        assert len({qm[k] for k in family}) == len(family)
    for role in ("success", "warning", "error", "info", "neutral"):
        assert ratio(qm[f"--color-{role}-text"], qm[f"--color-{role}-bg"]) >= 4.5
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert '[data-theme="qm"] .sync-st-live, [data-theme="qm"] .sync-st-both {\n    --sc-accent: var(--pico-primary)' in css
    roles = [v for k, v in qm.items() if k.startswith("--role-")]
    assert len(roles) >= 9 and len(set(roles)) == len(roles)


def test_qm_controls_plots_and_maps():
    env = dict(os.environ, NODE_PATH="D:/work/statemanager/node_modules")
    result = subprocess.run(["node", str(ROOT / "tests/qm_theme_selfcheck.cjs"), str(ROOT)],
                            capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_qm_fonts_are_bundled_offline():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    for font in ("work-sans.ttf", "poppins-medium.ttf"):
        assert f'url("fonts/{font}")' in css
        assert (STATIC / "fonts" / font).stat().st_size > 100000
    assert "SIL OPEN FONT LICENSE Version 1.1" in (STATIC / "fonts/OFL.txt").read_text(encoding="utf-8")
    assert not re.search(r"@import|url\([\"']?https?://", css)


def test_qm_leaves_layout_tokens_alone():
    # The QM block outranks html[data-font-size=...] and html.sm-in-frame, so a
    # sizing token copied into it would silently break the font-size presets.
    qm = tokens("qm")
    for name in ("--font-size-base", "--sidebar-width", "--topbar-height", "--bulk-fs"):
        assert name not in qm, name
