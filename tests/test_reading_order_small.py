"""Navigation, stored map names and narrow evidence columns, without a browser."""
import json
import re
import subprocess
from pathlib import Path

from quam_state_manager.web.app import create_app
from tests.test_ui_contract import _blocks, _strip_comments

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "quam_state_manager/web"


def run_check(mode, html=""):
    result = subprocess.run(
        ["node", str(ROOT / "tests/reading_order_small_selfcheck.cjs"), mode],
        input=html, capture_output=True, text=True, encoding="utf8", timeout=60,
        cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_channel_segments_keep_requests_and_keyboard(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    with app.test_request_context():
        from flask import render_template
        html = render_template("_pulses.html", total=0, per_page=50,
                               active_channel="xy", active_query="probe", rows=[],
                               current_page=1, total_pages=1, has_pair_flux=True,
                               has_pair_drive=True, has_found=True)
    run_check("tabs", html)


def test_channel_segments_without_optional_tabs_and_without_query(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    with app.test_request_context():
        from flask import render_template
        html = render_template("_pulses.html", total=0, per_page=50,
                               active_channel="", active_query="", rows=[],
                               current_page=1, total_pages=1, has_pair_flux=False,
                               has_pair_drive=False, has_found=False)
    run_check("tabs_min", html)


def test_stored_map_names_stay_visible_in_narrow_columns():
    run_check("map")
    template = (WEB / "templates/_cz_plan.html").read_text(encoding="utf8")
    assert re.search(r'<div class="czp-map-view ui-xscroll" tabindex="0" role="region" aria-label="[^"]+">', template)


def test_secondary_columns_share_one_rule_and_row_template(tmp_path):
    css = (WEB / "static/ui-kit.css").read_text(encoding="utf8")
    rules = [(sel, body, stack) for sel, body, stack in _blocks(_strip_comments(css))
             if ".ui-col-secondary" in sel]
    assert len(rules) == 1
    sel, body, stack = rules[0]
    assert sel == ".ui-col-secondary" and stack == ["@media (max-width: 900px)"]
    assert body.strip() == "display: none;"
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    row = dict(path="qubits.q1.xy.operations.probe", owner="q1", channel="xy",
               op_name="probe_operation_name", known=True, qclass="Pulse", class_short="Pulse",
               length=None, amplitude=None, used_by=[])
    with app.test_request_context():
        from flask import render_template
        paged = render_template("_pulse_rows.html", rows=[row], total=1, per_page=50,
                                current_page=1, total_pages=1)
        virtual = render_template("_pulse_row.html", r=row, lazy_spark=True)
    run_check("columns", json.dumps([paged, virtual]))
    detail = (WEB / "templates/_pulse_detail.html").read_text(encoding="utf8")
    assert "{{ qclass }}" in detail and 'id="pulse-detail-plot"' in detail


def test_priority_columns_refit_saved_widths():
    run_check("widths")


def test_priority_columns_refit_without_saved_widths():
    run_check("widths-unsaved")


def _kit_rules(prefix):
    css = (WEB / "static/ui-kit.css").read_text(encoding="utf8")
    return [(sel, dict(_decls_of(body))) for sel, body, _stack in _blocks(_strip_comments(css))
            if any(p.strip().startswith(prefix) for p in sel.split(","))]


def _decls_of(body):
    return [(k.strip(), v.strip()) for k, _, v in (d.partition(":") for d in body.split(";")) if k.strip()]


def test_link_option_is_never_underlined_in_any_state():
    states = {"": False, ":hover": False, ":focus": False, ":visited": False}
    for sel, decls in _kit_rules(".ui-segmented-opt"):
        if decls.get("text-decoration") == "none":
            for part in sel.split(","):
                part = part.strip()
                if part.startswith(".ui-segmented-opt"):
                    states[part[len(".ui-segmented-opt"):]] = True
    assert all(states.values()), states
    visited = [d for sel, d in _kit_rules(".ui-segmented-opt")
               if ".ui-segmented-opt:visited" in [p.strip() for p in sel.split(",")] and "color" in d]
    assert visited and visited[0]["color"] == "var(--ui-text-2)"


def test_selected_link_option_keeps_the_accent_when_visited():
    on = [d for sel, d in _kit_rules(".ui-segmented-on") if sel.strip() == ".ui-segmented-on"]
    assert on and on[0]["color"] == "var(--ui-accent)"
    visited = [d for sel, d in _kit_rules(".ui-segmented")
               if sel.strip() == ".ui-segmented.ui-segmented > .ui-segmented-on:visited"]
    assert len(visited) == 1, "the selected, visited link option needs its own rule"
    assert visited[0] == {"color": on[0]["color"]}


def test_many_options_wrap_inside_the_group_and_never_clip():
    rules = [d for sel, d in _kit_rules(".ui-segmented.ui-segmented") if sel.strip() == ".ui-segmented.ui-segmented"]
    merged = {}
    for d in rules:
        merged.update(d)
    assert merged.get("flex-wrap") == "wrap"
    assert merged.get("max-width") == "100%"
    assert merged.get("min-height") == "var(--ui-ctl-h)" and "height" not in merged
    kit = (WEB / "templates/ui_kit.html").read_text(encoding="utf8")
    assert kit.count("'href': '#'") >= 7


def test_selected_attribute_follows_the_element_in_macro_and_script(tmp_path):
    macro = (WEB / "templates/_ui.html").read_text(encoding="utf8")
    start = macro.index("macro ui_segmented")
    body = macro[start:macro.index("endmacro", start)]
    assert "aria-current=\"true\"" in body
    assert body.index("o.href") < body.index("aria-pressed")
    script = (WEB / "static/ui.js").read_text(encoding="utf8")
    seg = script[script.index("function segmented"):]
    seg = seg[:seg.index("return g;")]
    assert 'aria-current' in seg and seg.index("o.href") < seg.index("aria-pressed")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    with app.test_request_context():
        from flask import render_template_string
        html = render_template_string(
            "{% from '_ui.html' import ui_segmented %}{{ ui_segmented(o, 'x') }}",
            o=[{"label": "A", "href": "/a", "selected": True}, {"label": "B", "href": "/b"},
               {"label": "C", "selected": True}, {"label": "D"}])
    assert html.count("aria-current") == 1
    with app.test_request_context():
        from flask import render_template_string
        probe = render_template_string(
            "{% from '_ui.html' import ui_segmented %}{{ ui_segmented(o, 'x') }}",
            o=[{"label": "L1", "href": "/l1", "selected": True, "attrs": {"data-probe": "1", "hx-get": "/x"}},
               {"label": "L2", "href": "/l2"}, {"label": "B1", "selected": True, "attrs": {"data-probe": "3"}},
               {"label": "B2"}])
    l1, l2, b1, b2 = re.findall(r"<(?:a|button)\b[^>]*>", probe)
    assert 'data-probe="1"' in l1 and 'hx-get="/x"' in l1 and 'href="/l1"' in l1
    assert l1.endswith('class="ui-segmented-opt ui-segmented-on active">')
    assert l2.endswith('class="ui-segmented-opt">') and "aria-" not in l2 and "type=" not in l2
    assert 'data-probe="3"' in b1 and 'type="button"' in b1 and 'aria-pressed="true"' in b1
    assert b1.endswith('class="ui-segmented-opt ui-segmented-on">'), "a selected button never gets the link class"
    assert b2 == '<button type="button" aria-pressed="false" class="ui-segmented-opt">'
    assert html.count('aria-pressed="true"') == 1 and html.count('aria-pressed="false"') == 1
    assert "<a" in html and 'aria-current="true"' in html.split("</a>")[0]


def test_segmented_builder_sets_the_right_selected_attribute():
    run_check("segmented")


def _render_pulses(tmp_path, channel):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    with app.test_request_context():
        from flask import render_template
        return render_template("_pulses.html", total=0, per_page=50,
                               active_channel=channel, active_query="", rows=[],
                               current_page=1, total_pages=1, has_pair_flux=True,
                               has_pair_drive=True, has_found=True)


def test_channel_tabs_keep_the_selected_look_when_page_code_moves_the_tab(tmp_path):
    payload = json.dumps({"all": _render_pulses(tmp_path, ""), "xy": _render_pulses(tmp_path, "xy")})
    result = subprocess.run(
        ["node", str(ROOT / "tests/pulse_tabs_state_selfcheck.cjs")],
        input=payload, capture_output=True, text=True, encoding="utf8", timeout=120, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("ok - ") == 21, result.stdout


def test_channel_nav_is_a_named_landmark(tmp_path):
    html = _render_pulses(tmp_path, "xy")
    m = re.search(r'<nav id="pulse-channel-tabs"([^>]*)>', html)
    assert m and 'aria-label="Pulse channels"' in m.group(1), m and m.group(0)


def test_channel_tabs_keep_the_space_the_older_tabs_had_below_them(tmp_path):
    html = _render_pulses(tmp_path, "")
    m = re.search(r'<nav id="pulse-channel-tabs"([^>]*)>', html)
    assert m and "ui-segmented-bar" in m.group(1).split('class="')[1].split('"')[0].split()
    rules = [d for sel, d in _kit_rules(".ui-segmented-bar") if sel.strip() == ".ui-segmented-bar"]
    assert len(rules) == 1 and rules[0] == {"margin": "0 0 var(--ui-gap-3)"}, rules
    css = (WEB / "static/ui-kit.css").read_text(encoding="utf8")
    assert re.search(r"--ui-gap-3:\s*\.75rem;", css), "the token must equal the 0.75rem the old tabs left below them"
    # the shared segmented rule stays flush: other pages that use the control keep their spacing
    shared = [d for sel, d in _kit_rules(".ui-segmented.ui-segmented") if sel.strip().startswith(".ui-segmented.ui-segmented,")]
    assert shared and shared[0].get("margin-bottom") == "0"
    kit = (WEB / "templates/ui_kit.html").read_text(encoding="utf8")
    assert 'class="ui-segmented-bar"' in kit
