"""History and dataset controls use the shared kit, including offline reports."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "quam_state_manager" / "web"


def read(name):
    return (WEB / name).read_text(encoding="utf-8")


def rule(selector):
    css = read("static/ui-kit.css").split("/* Kit sweep wave 2: history */")[1]
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
    assert match, selector
    return match.group(1)


def test_history_requests_wait_for_the_dialog_confirmation(tmp_path):
    template = read("templates/_ledger_state_history.html")
    buttons = re.findall(r'<button[^>]*data-history-confirm=[\s\S]*?</button>', template)
    assert len(buttons) == 2
    assert "hx-confirm=" not in "".join(buttons)
    assert "{% if chip_origin == 'live' and not r.why_write and not r.pending %}" in template
    assert 'hx-target="#status-bar" hx-swap="innerHTML"' in buttons[0]
    assert 'hx-target="#state-history-detail" hx-swap="innerHTML"' in buttons[1]
    env = Environment(autoescape=True)
    env.filters["format_ts_zone"] = lambda value: "2026-01-01 00:00:00"
    rendered = env.from_string("".join(buttons)).render(
        r={"ts": "snapshot", "when": "date"}, hist_chip_key="chip", auto_push_note="")
    fixture = tmp_path / "buttons.json"
    fixture.write_text(json.dumps(rendered), encoding="utf-8")
    node = shutil.which("node")
    assert node, "node is required for the confirmation selfcheck"
    result = subprocess.run([node, str(ROOT / "tests/history_dialog_selfcheck.cjs"), str(fixture)],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DONE" in result.stdout


def test_report_controls_use_compact_parts_in_the_offline_stylesheet():
    tpl = read("templates/chip_report.html")
    assert 'class="rep-theme ui-segmented"' in tpl
    assert tpl.count('class="ui-segmented-opt" data-rep-theme=') == 3
    assert 'class="ui-check {{' in tpl
    assert 'class="chip-report ui-report-controls"' in tpl
    assert ".rep-opts select {" not in tpl
    assert ".rep-toolbar button {" not in tpl
    # Both stylesheet serialization paths include the kit. buildStandalone keeps local styles too.
    urls = tpl[tpl.index("var urls ="):tpl.index("var urls =") + 240]
    assert "ui-kit.css" in urls
    assert "walk(sheet.cssRules)" in tpl and "cloneNode(true)" in tpl
    assert "var(--ui-check-size)" in rule(".ui-report-controls .rep-opts .ui-check > input")


def test_history_label_editor_keeps_actions_compact():
    assert 'sh-entry-actions ui-history-actions' in read("templates/_ledger_state_history.html")
    assert "align-items: center" in rule(".ui-history-actions")
    assert "width: auto" in rule(".ui-history-actions .version-label-form > button")
    assert "height: var(--ui-ctl-h)" in rule(".ui-history-actions .version-label-form input.ui-input")


def test_dataset_help_has_room_for_syntax_and_stacks_on_phone():
    assert 'ds-search-help-panel ui-dataset-help' in read("templates/_datasets.html")
    assert "width: min(38rem, calc(100vw - 3rem))" in rule(".ui-dataset-help")
    assert "display: table-cell" in rule(".ui-dataset-help .ds-search-help-grid td")
    css = read("static/ui-kit.css").split("/* Kit sweep wave 2: history */")[1]
    phone = css[css.index("@media"):]
    assert ".ui-dataset-help .ds-search-help-grid td { display: block; width: auto" in phone


def test_dataset_search_keeps_a_usable_width():
    tpl = read("templates/_datasets.html")
    assert 'ds-search-wrap ui-dataset-search' in tpl and 'aria-label="Search datasets"' in tpl
    assert "flex: 1 1 16rem" in rule(".ui-dataset-search")
    assert "width: 100%" in rule(".ui-dataset-search > input.ui-search")
    assert "flex-wrap: wrap" in rule(".ui-head .ds-head-controls")
    assert "border-color: var(--ui-border-strong)" in rule(".ui-dataset-search > #dataset-search")


def test_dataset_success_matches_the_list_status_tone():
    tpl = read("templates/_dataset_detail.html")
    badge = re.search(r'<span class="ui-badge ui-badge--[^<]+</span>', tpl).group()
    env = Environment(autoescape=True)
    for status, tone in (("successful", "ok"), ("finished", "neutral"), ("failed", "danger")):
        assert f'ui-badge--{tone}' in env.from_string(badge).render(run={"status": status})


def test_experiment_filters_use_regular_type_and_keep_names_together():
    tpl = read("templates/_datasets.html")
    assert tpl.count('class="ui-experiment-chip exp-chip" data-exp="{{ exp }}" title="{{ exp }}"') == 2
    assert "font-family: inherit" in rule(".ui-experiment-chip.exp-chip")
    assert "font-weight: var(--ui-fw-normal)" in rule(".ui-experiment-chip.exp-chip")
    assert "white-space: nowrap" in rule(".ui-experiment-chip > span")


def test_history_date_row_wraps_before_the_go_button_overflows():
    assert "flex-wrap: wrap" in rule(".ui-history-toolbar")
    assert "white-space: nowrap" in rule(".ui-history-toolbar > button")
    assert "max-width: 100%" in rule(".ui-history-date")
    assert "flex-basis: 100%" in rule(".ui-history-date") or (
        ".ui-history-date { margin-left: 0; flex-basis: 100%; }" in read("static/ui-kit.css"))


def test_history_date_uses_kit_sizing_and_native_validation():
    tpl = read("templates/_ledger_state_history.html")
    assert '<input class="ui-input" type="date" name="date"' in tpl
    assert 'required aria-label="Go to date"' in tpl
    assert "width: 10rem; max-width: 100%" in rule(".ui-history-date input.ui-input")


def test_live_restore_uses_one_danger_palette():
    assert 'outline btn-sm danger sh-restore-live' in read("templates/_ledger_state_history.html")
    assert ".sh-restore-live {" not in read("static/style.css")


def test_baseline_disclosure_has_one_kit_marker():
    tpl = read("templates/_param_history.html")
    assert 'ph-drift-card ui-disclosure' in tpl
    assert 'ph-drift-title ui-disclosure-summary' in tpl
    legacy = re.search(r"\.ph-drift-card > summary\.ph-drift-title \{([^}]+)\}", read("static/style.css"))
    assert "list-style: revert" not in legacy.group(1)
    css = read("static/ui-kit.css")
    assert ".ui-disclosure-summary::after { display: none; }" in css
    assert ".ui-disclosure-summary::-webkit-details-marker { display: none; }" in css


def test_changes_search_uses_a_theme_colored_outline_icon():
    tpl = read("templates/_param_history_changes.html")
    assert 'class="ui-history-search-icon" aria-hidden="true"' in tpl
    assert "&#128269;" not in tpl
    assert "solid var(--ui-text-2)" in rule(".ui-history-search-icon")


def test_dataset_tabs_use_the_shared_small_type():
    assert 'dataset-tabs ui-dataset-tabs' in read("templates/_dataset_detail.html")
    assert "font-size: var(--ui-fs-sm)" in rule(".ui-dataset-tabs.dataset-tabs a")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_history_styles_use_available_theme_tokens_and_fit_phone(theme):
    css = read("static/ui-kit.css")
    block = css.split("/* Kit sweep wave 2: history */")[1]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", block)
    defined = set(re.findall(r"(--[\w-]+)\s*:", css))
    assert set(re.findall(r"var\((--[\w-]+)\)", block)) <= defined
    from tests.test_ui_contract import theme_env, _resolve
    env = theme_env(theme)
    for token in ("--ui-text", "--ui-text-2", "--ui-surface-1", "--ui-danger-text", "--ui-ok-text"):
        assert len(_resolve(f"var({token})", env)) == 4
    from tests.test_ui_contract import _blocks, _strip_comments
    for selector, body, stack in _blocks(_strip_comments(block)):
        assert all(part.strip().startswith(".ui-") for part in selector.split(","))
    assert "@media (max-width: 600px)" in block
    assert "max-width: 100%" in block and "calc(100vw - 3rem)" in block
