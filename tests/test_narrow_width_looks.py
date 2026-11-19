"""Narrow-width looks: gate row buttons, the class picker, wrapped segmented
controls, the top bar pill and the CZ plan chip layout card.

jsdom has no layout, so these pin the kit rules that make each look possible;
the widths themselves were measured in real Chrome at 1440 / 700 / 400 / 360 px
(every gate row button inside its list, the picked class name and its note both
on the closed picker, no page wider than the window).
"""
from pathlib import Path

from tests.test_ui_contract import _blocks, _strip_comments

WEB = Path(__file__).resolve().parents[1] / "quam_state_manager/web"


def _rules(name="ui-kit.css"):
    css = (WEB / "static" / name).read_text(encoding="utf8")
    return list(_blocks(_strip_comments(css)))


def _decls(body):
    return {k.strip(): v.strip() for k, _, v in (d.partition(":") for d in body.split(";")) if k.strip()}


def _rule(selector, stack=None):
    hits = [(_decls(b), s) for sel, b, s in _rules() if sel == selector and (stack is None or s == stack)]
    assert len(hits) == 1, f"one rule for {selector!r}, found {len(hits)}"
    return hits[0][0]


def test_gate_row_actions_wrap_instead_of_clipping():
    assert _rule(".gate-status-row").get("flex-wrap") == "wrap"
    acts = _rule(".gate-status-actions")
    assert acts.get("flex-wrap") == "wrap", "the row's buttons wrap under each other"
    assert acts.get("max-width") == "100%", "the button group never runs past its row"
    assert acts.get("flex", "").startswith("0 1"), "the button group may shrink to the row"


def test_closed_picker_keeps_the_name_beside_its_note():
    trig = _rule('.ui-select.ui-select.ui-menu-trigger:has(> .ui-menu-note)')
    assert trig.get("flex-wrap") == "wrap" and trig.get("height") == "auto"
    assert trig.get("white-space") == "normal"
    note = _rule(".ui-menu-trigger > .ui-menu-note")
    assert note.get("flex") == "1 1 100%", "the note takes its own line under the name"
    js = (WEB / "static/cz-gate.js").read_text(encoding="utf8")
    assert "el('span', 'ui-text-quiet ui-menu-note', OLDER_WORDS)" in js
    macro = (WEB / "templates/_ui.html").read_text(encoding="utf8")
    assert 'class="ui-text-quiet ui-menu-note"' in macro, "the kit macro renders the same note"


def test_open_list_is_not_cut_by_its_zone():
    assert _rule('.ui-zone:has(.ui-menu-trigger[aria-expanded="true"])').get("overflow") == "visible"
    assert _rule(".ui-menu--block > .ui-menu-list").get("max-width") == "100%"
    lst = [_decls(b) for sel, b, _s in _rules() if sel == ".ui-menu-list"][0]
    assert lst.get("scrollbar-width") == "thin", "a list taller than its cap shows a scrollbar"


def test_wrapped_segmented_lines_keep_a_kit_gap():
    seg = _rule(".ui-segmented.ui-segmented")
    assert seg.get("row-gap") == "var(--ui-gap-1)"
    assert "gap" not in seg and "column-gap" not in seg, "one-line controls keep their width"


def test_auto_sync_pill_wraps_only_at_the_fit_floor():
    pill = _rule(".topbar.tb-fit-11 .auto-apply-pill")
    assert pill.get("white-space") == "normal" and pill.get("flex-wrap") == "wrap"
    assert pill.get("max-width") == "100%"
    assert _rule(".topbar.tb-fit-11 .auto-sync-wrap").get("max-width") == "100%"
    for sel, body, _s in _rules() + _rules("style.css"):
        if ".auto-apply-pill" in sel and "tb-fit-11" not in sel:
            assert _decls(body).get("white-space") != "normal", sel


def test_chip_layout_card_takes_the_row_at_phone_width():
    view = _rule(".czp-map-view")
    assert view.get("flex-wrap") == "wrap", "the map drops under the card when the row holds only one"
    card = _rule(".czp-map-view > .cmap")
    assert card.get("flex") == "1 0 min(var(--ui-cz-minimap-width), var(--ui-cz-minimap-max))",         "the card keeps its width beside the map and grows to the full row alone"
    assert "max-width" not in card and "width" not in card, "nothing caps the card on its own row"
    fmap = _rule(".czp-map-view > .czp-map")
    assert fmap.get("flex") == "9999 1 var(--ui-czp-map-min-w)", "the map takes the free space beside the card"


def test_auto_sync_pill_and_picker_rules_are_reviewed_kit_rules():
    import tests.test_ui_contract as c
    for sel in ('.ui-select.ui-select.ui-menu-trigger:has(> .ui-menu-note)',
                '.ui-zone:has(.ui-menu-trigger[aria-expanded="true"])',
                '.topbar.tb-fit-11 .auto-sync-wrap', '.topbar.tb-fit-11 .auto-apply-pill'):
        assert sel in c._STAGE6_SELECTORS, sel


# Every declaration the narrow-width fixes added or changed, with its exact value:
# deleting one, or changing its value, fails here (one row per declaration).
_DECLARED = {
    (":root", None): {"--ui-czp-map-min-w": "24rem", "--ui-pce-cell-min": "6rem",
                      "--ui-pce-field-cols": "repeat(auto-fit, minmax(var(--ui-pce-cell-min), 1fr))"},
    (".ui-field-head", None): {"display": "flex", "flex-wrap": "wrap", "align-items": "center"},
    (".ui-menu-trigger > .ui-menu-note", None): {
        "flex": "1 1 100%", "min-width": "0", "font-size": "var(--ui-fs-xs)", "overflow-wrap": "anywhere"},
    (".ui-menu--block > .ui-menu-list", None): {"max-width": "100%"},
    (".ui-segmented.ui-segmented", None): {"row-gap": "var(--ui-gap-1)"},
    # the capped height scrolls the map vertically only; the row still wraps
    (".czp-map-view", None): {"max-height": "var(--ui-czp-map-max-h)", "overflow-y": "auto",
                              "display": "flex", "flex-wrap": "wrap", "align-items": "flex-start",
                              "gap": "var(--ui-gap-3)"},
    (".czp-map-view > .czp-map", None): {"flex": "9999 1 var(--ui-czp-map-min-w)", "min-width": "0"},
    (".gate-status-row", None): {"flex-wrap": "wrap"},
    (".gate-status-actions", None): {
        "display": "flex", "flex-wrap": "wrap", "flex": "0 1 auto", "max-width": "100%",
        "align-items": "center", "gap": "var(--ui-gap-1)", "margin-left": "auto"},
    (".gate-status-row > .ui-chip", None): {"flex-shrink": "1", "min-width": "0", "max-width": "100%"},
    (".gate-delete-list code", None): {"overflow-wrap": "anywhere"},
    (".topbar.tb-fit-11 .auto-sync-wrap", None): {"min-width": "0", "max-width": "100%"},
    (".topbar.tb-fit-11 .auto-apply-pill", None): {
        "flex-wrap": "wrap", "row-gap": "0", "max-width": "100%", "white-space": "normal",
        "text-align": "left", "border-radius": "var(--ui-r-sm)"},
    ('.ui-select.ui-select.ui-menu-trigger:has(> .ui-menu-note)', None): {
        "flex-wrap": "wrap", "height": "auto", "padding-top": "var(--ui-gap-1)",
        "padding-bottom": "var(--ui-gap-1)", "white-space": "normal"},
    ('.ui-zone:has(.ui-menu-trigger[aria-expanded="true"])', None): {"overflow": "visible"},
}


def test_every_narrow_width_declaration_keeps_its_value():
    for (sel, stack), want in _DECLARED.items():
        if sel == ":root":
            got = {}
            for s, b, _st in _rules():
                if s == ":root":
                    got.update(_decls(b))
        else:
            got = _rule(sel, stack)
        for prop, val in want.items():
            assert got.get(prop) == val, f"{sel} {{ {prop}: {val} }} -> {got.get(prop)!r}"
    lst = [_decls(b) for sel, b, _s in _rules() if sel == ".ui-menu-list"][0]
    assert lst.get("scrollbar-color") == "var(--ui-scroll-thumb) transparent"


# Every rule in the table is owned outright by these fixes, except the ones below, which carry older
# declarations too. An owned rule may hold nothing beside what the table lists: an extra white-space or
# overflow declaration would undo the wrap while every listed value still matches.
_SHARED = {":root", ".ui-segmented.ui-segmented", ".gate-status-row", ".ui-field-head"}


def test_owned_rules_carry_nothing_else():
    owned = [key for key in _DECLARED if key[0] not in _SHARED]
    assert len(owned) == len(_DECLARED) - len(_SHARED)
    for sel, stack in owned:
        assert _rule(sel, stack) == _DECLARED[(sel, stack)], sel


def test_gate_row_rules_never_force_a_value():
    forced = [sel for sel, body, _s in _rules() if ".gate-status" in sel and "!important" in body]
    assert forced == []


def test_long_name_shrink_rule_comes_after_the_rule_it_overrides():
    sels = [sel for sel, _b, _s in _rules()]
    base = [i for i, sel in enumerate(sels)
            if ".gate-status-row > .ui-chip" in [part.strip() for part in sel.split(",")] and "," in sel]
    assert len(base) == 1, base
    assert sels.index(".gate-status-row > .ui-chip") > base[0]


def _env():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(WEB / "templates")), autoescape=True)
    env.globals["ui_valid_id"] = lambda v: ""
    return env


def test_kit_picker_note_is_part_of_its_accessible_name():
    tpl = _env().from_string(
        "{% import '_ui.html' as ui %}{{ ui.ui_menu('pk', items, 'Pair', value='a', block=True, note=n) }}")
    items = [{"value": "a", "title": "A"}]
    html = tpl.render(items=items, n="Made by an older version.")
    assert 'aria-labelledby="pk-label pk-value pk-note"' in html
    assert '<span id="pk-note" class="ui-text-quiet ui-menu-note">Made by an older version.</span>' in html
    plain = tpl.render(items=items, n=None)
    assert 'aria-labelledby="pk-label pk-value"' in plain and "ui-menu-note" not in plain


def test_delete_offer_pointer_lines_break_at_their_dots():
    html = _env().get_template("_gate_delete_offer.html").render(
        gate="g<x>", pair="p", in_list=False,
        parts={"ok": True, "macro": "a.b<c>.d", "pulses": ["e.f"], "operations": ["g.h.i"],
               "active": True, "alias": "j.k", "kept": []})
    for want in ("<code>a.<wbr>b&lt;c&gt;.<wbr>d</code>", "<code>e.<wbr>f</code>",
                 "<code>g.<wbr>h.<wbr>i</code>", "<code>j.<wbr>k</code>"):
        assert want in html, want


def test_kit_page_shows_a_picker_with_a_note():
    kit = (WEB / "templates/ui_kit.html").read_text(encoding="utf8")
    line = [ln for ln in kit.splitlines() if "ui.ui_menu('uk-menu-note'" in ln]
    assert len(line) == 1 and "block=True" in line[0] and "note='" in line[0]


def _style_rule(selector):
    hits = [_decls(b) for sel, b, _s in _rules("style.css") if sel == selector]
    assert len(hits) == 1, f"one style.css rule for {selector!r}, found {len(hits)}"
    return hits[0]


def test_panels_popover_rows_wrap_at_phone_width():
    for sel in ("#ov-settings-pop .ov-set-global", "#ov-settings-pop .ov-set-row"):
        assert _style_rule(sel).get("flex-wrap") == "wrap", sel


def test_pulses_headers_and_badges_break_only_between_words():
    want = {"word-break": "keep-all", "overflow-wrap": "normal", "white-space": "normal"}
    assert _style_rule("#pulses-table th, #pulses-table .pointer-badge") == want
    # the badge's own break-all must come before this override, or "alias" breaks mid-word again
    order = [sel for sel, _b, _s in _rules("style.css")]
    badge = max(i for i, sel in enumerate(order) if sel == ".pointer-badge")
    assert badge < order.index("#pulses-table th, #pulses-table .pointer-badge")
    for sel, body, _s in _rules("style.css"):
        if sel.startswith("#pulses-table th"):
            assert "text-overflow" not in _decls(body), sel


def test_pulses_amp_and_used_by_leave_only_on_a_phone():
    import re
    head = (WEB / "templates" / "_pulse_rows.html").read_text(encoding="utf8")
    row = (WEB / "templates" / "_pulse_row.html").read_text(encoding="utf8")
    for col in ("7", "8"):
        th = re.search(r'<th[^>]*data-col="' + col + r'"[^>]*>', head).group(0)
        assert "ui-col-phone" in th and "ui-col-secondary" not in th, th
    tds = re.findall(r"<td[^>]*>", row)
    assert all("ui-col-phone" in tds[i] and "ui-col-secondary" not in tds[i] for i in (7, 8)), tds
    rules = [(sel, body, stack) for sel, body, stack in _rules() if ".ui-col-phone" in sel]
    assert len(rules) == 1, rules
    sel, body, stack = rules[0]
    assert sel == ".ui-col-phone" and stack == ["@media (max-width: 600px)"], (sel, stack)
    assert _decls(body) == {"display": "none"}


def test_chart_is_visible_before_its_lane_width_is_measured():
    js = (WEB / "static/cz-gate.js").read_text(encoding="utf8")
    render = js[js.index("function renderChart("):js.index("function lineOfChannel(")]
    assert render.index("$('czg-sec-chart').hidden = false") < render.index("window.GateTimeline.render(host")
    chart = (WEB / "static/gate-timeline.js").read_text(encoding="utf8")
    assert "hostPx - margin.l - margin.r < STACK_PLOT_PX" in chart
    assert "var STACK_PLOT_PX = 200" in chart


def test_unit_fields_keep_a_visible_kit_focus_ring():
    """One box: the unit wrapper draws the focus ring; the input inside draws no second ring."""
    ring = _rule(".ui-input-unit:focus-within")
    assert ring["border-color"] == "var(--ui-accent)"
    assert ring["box-shadow"] == "var(--ui-ctl-ring)"
    inner = _rule(".ui-input-unit > .ui-input.ui-input:focus")
    assert inner["outline"] == "0" and inner["box-shadow"] == "none"


def _rule_with(part):
    hits = [_decls(b) for sel, b, s in _rules() if part in [p.strip() for p in sel.split(",")]]
    assert len(hits) == 1, f"one rule lists {part!r}, found {len(hits)}"
    return hits[0]


def test_an_inline_field_control_keeps_its_own_width():
    """A control in an inline field is as wide as itself (never stretched across the row);
    --block still fills the column."""
    for part in (".ui-input-unit", ".ui-input", ".ui-select", ".ui-menu"):
        assert _rule_with(".ui-field--inline > " + part).get("justify-self") == "start"
        assert _rule_with(".ui-field--inline > " + part + "--block").get("justify-self") == "stretch"


def test_a_field_in_a_table_cell_is_never_squeezed_below_its_control():
    """In a table cell the control column sizes to its control and a select or number is not
    compressed, so a band label or a six-decimal value stays whole."""
    grid = _rule(".ui-table-cell .ui-field.ui-field--inline")
    assert grid["grid-template-columns"] == "var(--ui-field-label-w) auto"
    for part in (".ui-table-cell .ui-field--inline > .ui-select", ".ui-table-cell .ui-field--inline > .ui-input",
                 ".ui-table-cell .ui-field--inline > .ui-input-unit", ".ui-table-cell .ui-input-unit > .ui-input"):
        decls = _rule_with(part)
        assert decls.get("max-width") == "none" and decls.get("flex") == "none", part
