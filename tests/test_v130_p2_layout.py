"""Layout contracts verified in Chrome at 1280 and 1600 pixels."""
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]

PINS = [
    ('L1', 'quam_state_manager/core/search_query.py', '    parts = [e for e in extras if e]\n    return "Search" + (": " + ", ".join(parts) if parts else "")'),
    ('L1-journal', 'quam_state_manager/web/templates/_journal.html', 'placeholder="{{ search_hint() }}"'),
    ('L2', 'quam_state_manager/web/static/topo-graph.js', '             \'<circle class="cm-occluder" cx="\' + pt.x + \'" cy="\' + pt.y + \'" r="\' + R + \'"/>\' +\n             \'<circle class="cm-stone" cx="\' + pt.x'),
    ('L2-fill', 'quam_state_manager/web/static/style.css', '.cm-occluder { fill: var(--pico-card-background-color, #f7f7f7); pointer-events: none; }\n.cm-stone { fill: var(--pico-card-background-color, #f7f7f7);'),
    ('L3', 'quam_state_manager/web/static/app.js', '        // Keep short feedline labels readable while containing longer names.\n        fontSize = Math.min(10, Math.floor((2 * r - 2) / (0.62 * Math.max(1, display.length))));'),
    ('L4-markup', 'quam_state_manager/web/templates/_datasets.html', '><span>{{ exp | soft_breaks }}</span></span>'),
    ('L4', 'quam_state_manager/web/static/style.css', '    white-space: normal; overflow-wrap: normal;\n    font-family: var(--font-mono);'),
    ('L5-markup', 'quam_state_manager/web/templates/_state_versions.html', '<td class="sv-q-path"><code>{% for part in e.dot_path.split(\'.\') %}{{ part }}{% if not loop.last %}.<wbr>{% endif %}{% endfor %}</code></td>'),
    ('L5', 'quam_state_manager/web/static/style.css', '.sv-q-path code { font-size: .95em; word-break: normal; overflow-wrap: normal; }'),
    ('L19', 'quam_state_manager/web/static/style.css', '.state-versions-list { list-style: none; margin: 0; padding: 0; display: grid;\n    grid-template-columns: max-content max-content minmax(0, 1fr); }'),
    ('L19-row', 'quam_state_manager/web/static/style.css', '.state-version-row { display: grid; align-items: center; gap: 0.5rem;\n    grid-template-columns: subgrid; grid-column: 1 / -1;\n    padding: 0.28rem 0.25rem; border-radius: 0.25rem; }'),
    ('L19-marker', 'quam_state_manager/web/static/style.css', '.state-version-row::before { content: none; }'),
    ('L6-css', 'quam_state_manager/web/static/style.css', '    --split-gutter-size:     24px;     /* Keep the drag handle height in sync with UI_CONFIG.split.gutterSize. */'),
    ('L6', 'quam_state_manager/web/static/app.js', '        gutterSize:     24          /* Keep the drag handle height in sync with --split-gutter-size in style.css. */'),
    ('L27', 'quam_state_manager/web/templates/base.html', "function expandedSizes() {\n            var fallback = document.querySelector('#inspector-pane #ds-detail-root')\n                ? [25, 75] : UI_CONFIG.split.expandedSizes;\n            return loadPreset(PRESET_EXPANDED_KEY, fallback);\n        }"),
    ('L25', 'quam_state_manager/web/static/dataset-virtual.js', "        if (/(finish|done|complet)/.test(s)) cls = 'ds-status-done';\n        else if (/(success|pass)/.test(s)) cls = 'ds-status-ok';"),
    ('L25-css', 'quam_state_manager/web/static/style.css', '.ds-status-done { color: var(--pico-muted-color); }\n.ds-status-ok { color: var(--color-success-text); }'),
    ('L25-legacy', 'quam_state_manager/web/static/style.css', '.status-finished { background: none; color: var(--pico-muted-color); }'),
    ('L25-detail', 'quam_state_manager/web/templates/_dataset_detail.html', '<span class="entry-status status-{{ run.status }}">{{ run.status }}</span>'),
    ('L27-context', 'quam_state_manager/web/static/dataset-virtual.js', 'el.scrollTop += rowTop - headH - ROW_HEIGHT;'),
    ('L27-sync', 'quam_state_manager/web/static/dataset-virtual.js', '_kbIdx = k; _kbReveal(context); return;'),
    ('L27-open', 'quam_state_manager/web/static/dataset-virtual.js', '            if (root) _kbSyncTo(_kbOpenUid, true);'),
    ('L8', 'quam_state_manager/web/static/style.css', '.bulk-table-wrap { overflow-x: auto; max-height: none; width: 100%; max-width: 100%;'),
    ('L8-scroll', 'quam_state_manager/web/static/bulk-edit.js', "return t.closest('.bulk-table-wrap') || document.getElementById('table-pane') || t.parentElement;"),
    ('L8-pairs', 'quam_state_manager/web/static/pair-edit.js', "            // Each grid owns horizontal scrolling; the pane owns vertical scrolling.\n            scroller: function (t) {\n                return t.closest('.bulk-table-wrap')\n                    || document.getElementById('table-pane') || t.parentElement;"),
    ('L8-note', 'quam_state_manager/web/static/grid-virt.js', "                    if (sc && sc.contains(el) && sc.scrollLeft)\n                        el.style.transform = 'translateX(' + sc.scrollLeft + 'px)';"),
    ('L8-bars', 'quam_state_manager/web/static/bulk-edit.js', "        var s = document.getElementById('table-pane'); if (!s) return;"),
    ('L8-left', 'quam_state_manager/web/static/style.css', '.bulk-corner, .bulk-rowhead { position: sticky; left: 0;'),
    ('L8-right', 'quam_state_manager/web/static/style.css', '.bulk-apply-col { text-align: center; position: sticky; right: 0;'),
    ('L8-head-css', 'quam_state_manager/web/static/style.css', '.bulk-table thead th { position: sticky; top: var(--bulk-sticky-top, 0px);'),
    ('L8-head2-css', 'quam_state_manager/web/static/style.css', '.bulk-table .bulk-head-row th { top: calc(var(--bulk-grouphead-h, 1.7rem) + var(--bulk-sticky-top, 0px)); }'),
    ('L8-heads', 'quam_state_manager/web/static/bulk-edit.js', '        _pinGridHeadsToPane();'),
    ('L9-qubits', 'quam_state_manager/web/templates/_qubits.html', '<div class="table-scroll">\n<table id="qubits-table"'),
    ('L9-pulses', 'quam_state_manager/web/templates/_pulse_rows.html', '<div class="table-scroll">\n<table id="pulses-table"'),
    ('L20', 'quam_state_manager/web/static/style.css', '.diag-loc-cell { min-width: 10rem; }\n.diag-results .diag-table th:nth-child(3), .diag-results .diag-table td:nth-child(3) { min-width: 16rem; }'),
    ('L22', 'quam_state_manager/web/templates/_couplers.html', '{% if total %}\n{% set cmap_highlight = \'couplers\' %}\n{% set cmap_label = \'— coupler edges highlighted\' %}\n{% include \'_component_map.html\' %}\n{% else %}\n<p class="muted couplers-empty">This chip declares no couplers.</p>\n{% endif %}'),
    ('L23', 'quam_state_manager/web/templates/_instrument_wiring.html', '<div class="table-header-row iw-header">'),
    ('L23-css', 'quam_state_manager/web/static/style.css', '.iw-header { padding-right: 1.8rem; }\n.pageheader-toggle:hover, .pageheader-toggle:focus {'),
    ('L20-actions', 'quam_state_manager/web/static/style.css', '.diag-results .diag-action { white-space: normal; width: 12rem; }'),
    ('L10-diff', 'quam_state_manager/web/static/style.css', '.diff-side select { margin: 0; font-size: 0.88em; padding: 0.25rem 1.8rem 0.25rem 0.5rem; height: auto; }'),
    ('L10', 'quam_state_manager/web/static/style.css', '.trends-filters select { font-size: 0.88em; padding: 0.2rem 1.8rem 0.2rem 0.4rem; margin: 0; max-width: 280px; }'),
    ('L11', 'quam_state_manager/web/templates/_state_history.html', 'review, compare, roll&nbsp;back</small>'),
    ('L13', 'quam_state_manager/web/templates/_param_history_changes.html', "  </div>\n\n  {% with notes=hub_notes %}{% include '_hub_notes.html' %}{% endwith %}\n"),
]

@pytest.mark.parametrize("item,path,required", PINS, ids=[p[0] for p in PINS])
def test_layout_contract(item, path, required):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert required in text, item


@pytest.mark.parametrize('script', ['v130_p2_map_selfcheck.cjs', 'wiring_portlabel_selfcheck.cjs', 'chip_status_hero_selfcheck.cjs'])
def test_browser_logic(script):
    proc = subprocess.run(['node', str(ROOT / 'tests' / script)], capture_output=True,
                          text=True, encoding='utf-8', timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


LOGIC_MODES = ['preset', 'status', 'context', 'grid', 'gutter']


@pytest.mark.parametrize('mode', LOGIC_MODES)
def test_layout_logic(mode):
    proc = subprocess.run(['node', str(ROOT / 'tests/v130_p2_layout_logic_selfcheck.cjs'), mode],
                          capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_compact_search():
    from quam_state_manager.core.search_query import search_hint, search_title
    assert search_hint() == 'Search'
    assert 'AND' in search_title() and 'OR' in search_title()


def test_version_path_breaks(tmp_path):
    import re
    from flask import render_template_string
    from quam_state_manager.web.app import create_app
    template = (ROOT / 'quam_state_manager/web/templates/_state_versions.html').read_text(encoding='utf-8')
    cell = re.search(r'<td class="sv-q-path">.*?</td>', template, re.S).group()
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    with app.test_request_context():
        body = render_template_string(cell, e={'dot_path': 'qubits.q1.<img>'})
    assert body.count('<wbr>') == 2
    assert '&lt;img&gt;' in body and '<img>' not in body


def test_history_title_before_notice():
    template = (ROOT / 'quam_state_manager/web/templates/_param_history_changes.html').read_text(encoding='utf-8')
    assert template.index('<div class="param-history-header">') < template.index("'_hub_notes.html'")


def test_empty_couplers(tmp_path):
    import json
    from quam_state_manager.web.app import create_app
    state_dir = tmp_path / 'state'
    state_dir.mkdir()
    (state_dir / 'state.json').write_text(json.dumps({
        'qubits': {'q1': {'id': 'q1', 'grid_location': '0,0'}},
        'qubit_pairs': {},
    }), encoding='utf-8')
    (state_dir / 'wiring.json').write_text('{}', encoding='utf-8')
    client = create_app(testing=True, instance_path=str(tmp_path / 'instance')).test_client()
    client.post('/load', data={'folder': str(state_dir)})
    body = client.get('/couplers').get_data(as_text=True)
    assert 'This chip declares no couplers.' in body
    assert 'id="component-map"' not in body
