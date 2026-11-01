"""D-103 / M-1: a head-fold button only renders on a head that has something to fold."""
import pathlib, re

TPL = pathlib.Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "templates"
DATA_HEADS = ["_wiring", "_qubits", "_pairs", "_resonators", "_flux", "_couplers",
              "_qdac", "_instrument_wiring", "_cz_plan"]


def test_data_heads_carry_no_fold():
    for name in DATA_HEADS:
        assert "ui_head_fold" not in (TPL / f"{name}.html").read_text(encoding="utf-8"), name


def test_macro_folds_only_with_a_gist():
    src = (TPL / "_ui.html").read_text(encoding="utf-8")
    m = re.search(r"\{%- if ([^%]*)%\}\{\{ ui_head_fold\(\) \}\}", src)
    assert m and "gist" in m.group(1) and "not data" in m.group(1)


def test_map_fit_shrinks_to_the_floor():
    js = (TPL.parent / "static" / "component-map.js").read_text(encoding="utf-8")
    assert "if (s < floor) s = Math.min(1, floor);" in js


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _cr(a, b):
    a, b = sorted([_lum(a), _lum(b)])
    return (b + 0.05) / (a + 0.05)


def test_dark_wiring_stones_clear_the_card():
    css = (TPL.parent / "static" / "style.css").read_text(encoding="utf-8")
    for tok in ("--role-z-color", "--role-twpa-pump-color"):
        dark = re.search(tok + r":\s*(#[0-9a-fA-F]{6})", css).group(1)
        assert _cr(dark, "#2a2a3a") >= 3.0, (tok, dark)
        assert _cr(dark, "#ffffff") >= 3.0, (tok, dark)
    js = (TPL.parent / "static" / "app.js").read_text(encoding="utf-8")
    assert "circle.style.fill = 'var(' + themed" in js


def test_light_theme_keeps_the_darker_wiring_fills():
    css = (TPL.parent / "static" / "style.css").read_text(encoding="utf-8")
    block = css[css.index('[data-theme="light"] {'):]
    block = block[:block.index("}")]
    assert "--role-z-color: #2471a3" in block and "--role-twpa-pump-color: #c8392b" in block


def test_a_theme_switch_redraws_the_racks():
    js = (TPL.parent / "static" / "app.js").read_text(encoding="utf-8")
    i = js.index("var _iwDrawn = [];")
    assert "sm:themechange" in js[i:i + 400] and "renderInstrumentWiring.apply" in js[i:i + 600]
    assert "if (_iwDrawn.indexOf(container) < 0) _iwDrawn.push(container);" in js
