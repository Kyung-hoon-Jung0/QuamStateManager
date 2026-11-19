"""Compact shared controls and full-page search keep the kit contract."""
from pathlib import Path
import re

import pytest

from tests.test_web import app, client, loaded_client, synth_folder

WEB = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"


def source(name):
    return (WEB / name).read_text(encoding="utf-8")


def rule(selector):
    css = source("static/ui-kit.css").split("/* Kit sweep wave 2: chrome */")[1].split("/* Kit sweep wave 2:")[0]
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
    assert match, selector
    return match[1]


def test_sidebar_filter_uses_compact_block_input():
    base = source("templates/base.html")
    tag = re.search(r'<textarea[^>]*id="sidebar-filter-input"[^>]*>', base)[0]
    assert 'class="search-help-input ui-textarea ui-textarea--grow"' in tag
    assert 'resize: none' in rule('.ui-textarea--grow.ui-textarea--grow')
    cap = rule('.sidebar-filter .ui-textarea--grow')
    assert 'max-height: calc(var(--ui-lh-read) * 3em + 2 * var(--ui-gap-1) + 2 * var(--ui-bw))' in cap


def test_explorer_warning_uses_icon_button_shape():
    assert "ic.className = 'tree-warn-icon ui-btn-icon ui-btn-icon--warn';" in source("static/app.js")
    assert 'color: var(--ui-warn-text)' in rule('.ui-btn-icon.ui-btn-icon--warn')


def test_pulse_selection_uses_kit_checkboxes():
    assert 'class="pulse-sel-chk ui-check-box"' in source("templates/_pulse_row.html")


def test_gate_summary_is_a_compact_status_line():
    template = source("templates/_gate_status_list.html")
    assert 'class="ui-panel-sum ui-gate-summary"' in template
    assert 'strong=True' in template
    assert 'font-size: var(--ui-fs-sm)' in rule('.ui-gate-summary .ui-verdict')
    assert 'font-size: var(--ui-fs-sm)' in rule('.ui-gate-summary .ui-verdict-key')
    assert 'width: var(--ui-check-size)' in rule('.ui-gate-summary .ui-verdict > .ui-dot')


def test_gate_plan_link_matches_header_actions():
    assert 'inspector-czplan-link ui-plan-link' in source("templates/_inspector_header.html")
    assert 'font-size: var(--ui-fs-sm)' in rule('.ui-plan-link.ui-plan-link')


def test_bulk_sync_and_cells_use_compact_kit_sizes():
    template = source("templates/_bulkedit.html")
    assert 'class="bulk-freqsync-ctl ui-check"' in template
    assert 'bulk-panel ui-bulk-compact' in template
    assert 'font-size: var(--ui-bulk-cell-fs)' in rule('.ui-bulk-compact .bulk-rowhead')
    assert '.ui-bulk-compact .bulk-cell,' in source("static/ui-kit.css")
    root = source("static/ui-kit.css").split(":root {", 1)[1].split("}", 1)[0]
    assert '--ui-bulk-cell-fs: calc(var(--ui-fs-sm) * var(--bulk-fs, 1));' in root


def test_pulses_head_keeps_the_corner_free_for_the_header_toggle():
    assert 'id="content-area" class="ui-chrome-content"' in source("templates/base.html")
    head = re.search(r'<div class="table-header-row[^"]*ui-pulses-head">', source("templates/_pulses.html"))[0]
    assert 'ui-head--page' in head


def test_overview_panels_button_uses_kit_button_size():
    assert 'class="btn-sm outline ov-settings-btn ui-title-btn"' in source("templates/_wiring.html")
    assert 'font-size: var(--ui-fs-sm)' in rule('.ui-title-btn.btn-sm')


def test_pulse_pager_links_have_compact_borders_and_wrap():
    assert 'id="pulses-rows-wrap" class="ui-pulse-list"' in source("templates/_pulses.html")
    assert 'border: var(--ui-bw) solid var(--ui-border)' in rule('.ui-pulse-list .pagination a')
    assert 'flex-wrap: wrap' in rule('.ui-pulse-list .pagination > ul')


def test_fidelity_meta_checkbox_and_heading_use_kit_sizes():
    js = source("static/chip-status.js")
    assert 'class="topo-meta-toggle ui-check"' in js
    assert 'class="topo-meta-cb ui-check-box"' in js
    assert 'class="topo-metric-panel-title ui-fidelity-title"' in js
    assert 'font-size: var(--ui-fs-md)' in rule('.ui-fidelity-title.ui-fidelity-title')


def test_folder_browser_rows_and_bands_share_kit_spacing():
    base = source("templates/base.html")
    assert 'folder-browser-dialog ui-folder-dialog' in base
    assert '<summary class="ui-disclosure-summary">Recent Folders</summary>' in base
    assert 'var(--ui-border)' in rule('.ui-folder-dialog .browser-folder')
    assert 'var(--ui-surface-2)' in rule('.ui-folder-dialog .browser-breadcrumbs')
    assert 'min-width: 0' in rule('.ui-folder-dialog .browser-toolbar .ui-input')


def test_wizard_tabs_scroll_within_narrow_content():
    css = source("static/ui-kit.css").split("/* Kit sweep wave 2: chrome */")[1].split("/* Kit sweep wave 2:")[0]
    narrow = re.search(r"@media \(max-width: 600px\) \{((?:[^{}]*\{[^}]*\})*)\s*\}", css)
    assert narrow and '.ui-chrome-content .gen-steps {' in narrow[1]
    assert '.ui-chrome-content .gen-steps > li {' in narrow[1]
    assert css.count('.gen-steps') == 2, "the tab row keeps its wrapping outside the narrow query"
    assert 'padding-bottom: var(--ui-bw)' in rule('.ui-chrome-content .gen-steps')
    assert 'overflow-x: auto' in rule('.ui-chrome-content .gen-steps')
    assert 'max-width: 100%' in rule('.ui-chrome-content .gen-steps')
    assert 'flex-wrap: nowrap' in rule('.ui-chrome-content .gen-steps')
    assert 'flex: 0 0 auto' in rule('.ui-chrome-content .gen-steps > li')


@pytest.mark.parametrize("query", ["", "qA1", "missing"])
def test_direct_search_has_app_shell_but_htmx_keeps_fragment(loaded_client, query):
    direct = loaded_client.get('/search', query_string={'q': query}).get_data(as_text=True)
    fragment = loaded_client.get('/search', query_string={'q': query},
                                 headers={'HX-Request': 'true'}).get_data(as_text=True)
    assert '<!DOCTYPE html>' in direct and 'id="table-pane"' in direct
    assert '<!DOCTYPE html>' not in fragment and 'id="table-pane"' not in fragment
    if query:
        assert 'search-panel' in direct and 'search-panel' in fragment
        table = direct.split('id="table-pane"', 1)[1].split('id="inspector-pane"', 1)[0]
        inspector = direct.split('id="inspector-pane"', 1)[1].split('id="status-bar"', 1)[0]
        assert 'search-panel' not in table and 'search-panel' in inspector


def test_history_restore_of_search_gets_the_full_page(loaded_client):
    restore = loaded_client.get('/search', query_string={'q': 'qA1'},
                                headers={'HX-Request': 'true', 'HX-History-Restore-Request': 'true'})
    assert '<!DOCTYPE html>' in restore.get_data(as_text=True)


def test_chrome_additions_are_theme_independent_and_narrow_safe():
    css = source("static/ui-kit.css").split("/* Kit sweep wave 2: chrome */")[1].split("/* Kit sweep wave 2:")[0]
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b|rgba?\(|data-theme', css)
    assert 'max-width: 100%' in rule('.ui-pulse-list .pagination')
    assert 'min-width: 0' in rule('.ui-gate-summary')
    assert 'flex-wrap: wrap' in rule('.ui-folder-dialog .browser-toolbar')
    assert 'overflow-wrap: anywhere' in rule('.ui-fidelity-title.ui-fidelity-title')

def test_settings_target_fields_keep_room_for_labels_and_inputs():
    assert 'ui-settings-targets' in source("templates/base.html")
    assert 'grid-template-columns: minmax(0, 1fr)' in rule('.ui-settings-targets .ui-field-grid')
    assert 'max-width: 100%' in rule('.ui-settings-targets .ui-input')


def test_chrome_css_only_changes_the_reviewed_scopes():
    css = source("static/ui-kit.css").split("/* Kit sweep wave 2: chrome */")[1].split("/* Kit sweep wave 2:")[0]
    selectors = set(re.findall(r"([^{}]+)\{", css))
    assert {s.strip() for s in selectors} == {
        '.ui-chrome-content',
        '@media (max-width: 600px)',
        '.ui-chrome-content .gen-steps',
        '.ui-chrome-content .gen-steps > li',
        '.ui-pulse-list .pagination',
        '.ui-pulse-list .pagination > ul',
        '.ui-pulse-list .pagination a',
        '.ui-pulse-list .page-size-picker > .ui-field',
        '.ui-gate-summary',
        '.ui-gate-summary .ui-verdict',
        '.ui-gate-summary .ui-verdict > .ui-dot',
        '.ui-gate-summary .ui-verdict-key',
        '.ui-plan-link.ui-plan-link',
        '.ui-bulk-compact .bulk-cell,\n.ui-bulk-compact .bulk-rowhead',
        '.ui-bulk-compact .ui-check',
        '.ui-check input.ui-check-box',
        '.ui-fidelity-title.ui-fidelity-title',
        '.ui-folder-dialog .browser-toolbar',
        '.ui-folder-dialog .browser-toolbar .ui-input',
        '.ui-folder-dialog .browser-breadcrumbs',
        '.ui-folder-dialog .browser-recent',
        '.ui-folder-dialog .browser-folder',
        '.ui-folder-dialog .ui-disclosure-summary',
        '.ui-settings-targets .ui-field-grid',
        '.ui-settings-targets .ui-input',
        '.ui-textarea--grow.ui-textarea--grow',
        '.sidebar-filter .ui-textarea--grow',
        '.ui-title-btn.btn-sm',
        '.ui-btn-icon.ui-btn-icon--warn',
    }
