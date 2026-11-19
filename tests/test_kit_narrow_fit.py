"""Narrow-width fit of a few kit pages: the markup hooks and the kit rules that keep them readable at 400 px.

Each test pins one element and the one kit rule it relies on, so reverting either side fails.
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
KIT = re.sub(r"(?s)/\*.*?\*/", "", (WEB / "static" / "ui-kit.css").read_text(encoding="utf-8"))


def _tpl(name):
    return (WEB / "templates" / name).read_text(encoding="utf-8")


def _has_rule(selector, decl):
    """True when ui-kit.css has a rule whose selector list contains `selector` and whose body has `decl`."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", KIT):
        sels = [s.strip() for s in m.group(1).split(",")]
        if selector in sels and decl in m.group(2).replace("\n", " "):
            return True
    return False


def test_regenerate_source_row_wraps_so_the_load_button_stays_whole():
    src = _tpl("_regenerate.html")
    assert re.search(r'<div class="[^"]*\bui-regen\b[^"]*" id="regen-surface"', src)
    bar = src.index('class="regen-bar"')
    assert src.index('id="regen-load-btn"') > bar
    assert _has_rule(".ui-regen .regen-bar", "flex-wrap: wrap")


def test_zline_table_scrolls_inside_its_own_box():
    src = _tpl("_zline.html").replace("\r\n", "\n")
    m = re.search(r'<div class="([^"]*)">\s*<table id="zline-table"', src)
    assert m, "the Z-line table must sit directly inside a wrapper"
    assert "ui-xscroll" in m.group(1).split()
    tail = src[m.end():]
    assert re.match(r"(?s).*?</table>\s*</div>", tail), "the wrapper closes right after the table"
    assert _has_rule(".ui-xscroll", "overflow-x: auto")
    assert _has_rule(".ui-xscroll", "max-width: 100%")


def test_kit_sample_pair_stacks_when_a_column_would_not_fit():
    assert _has_rule(".ui-kit-pair", "minmax(min(420px, 100%), 1fr)")


def test_kit_page_samples_sit_in_a_titled_section():
    src = _tpl("ui_kit.html")
    sec = src.index("Page samples")
    for cls in ("ui-iw-conflict", "ui-running-overlay", "ui-live-tray", "ui-live-pin"):
        assert src.index(cls) > sec, cls
    assert _has_rule(".ui-kit-samples", "flex-direction: column")


def test_opx_monitor_connection_summary_wraps():
    assert 'class="ui-opxmon-settings"' in _tpl("_opx_monitor_body.html")
    assert _has_rule(".ui-opxmon-settings .ui-zone-sum", "white-space: normal")


def test_port_boxes_with_an_explaining_placeholder_take_the_field_width():
    for sel in (".ui-opxmon-settings .ui-field > .ui-input--num.ui-input--num",
                ".ui-generate .gen-field > .ui-input--num.ui-input--num"):
        assert _has_rule(sel, "width: 100%"), sel


def test_generate_stacked_labels_sit_at_the_left_of_their_control():
    for sel in (".ui-generate .gen-line-toggle.ui-field", ".ui-generate .gen-naming-controls .ui-field",
                ".ui-generate .gen-preset-bar .ui-field"):
        assert _has_rule(sel, "align-items: flex-start"), sel


def test_palette_picked_row_is_a_soft_accent_band():
    assert re.search(r'id="cmd-palette" class="[^"]*\bui-palette\b', _tpl("base.html"))
    assert _has_rule(".ui-palette .cmd-palette-item.active", "background: var(--ui-accent-bg)")


def test_skip_link_opens_on_its_own_surface():
    assert re.search(r'class="skip-link ui-skip-link" id="skip-link"', _tpl("base.html"))
    assert _has_rule(".ui-skip-link:focus", "position: fixed")
    assert _has_rule(".ui-skip-link:focus", "background: var(--ui-surface-0)")


def test_runner_step_titles_stay_on_one_line():
    assert re.search(r'id="sched-root" class="[^"]*\bui-scheduler\b', _tpl("_scheduler.html"))
    assert _has_rule(".ui-scheduler .ui-disclosure-summary > strong", "white-space: nowrap")
    assert _has_rule(".ui-scheduler .ui-disclosure-summary", "flex-wrap: wrap")


def test_kit_tray_and_legend_samples_keep_a_gap_between_their_parts():
    assert _has_rule(".ui-kit-samples .ui-live-tray", "gap: var(--ui-gap-2)")
    assert _has_rule(".ui-kit-samples .ui-iw-conflict", "gap: var(--ui-gap-2)")
