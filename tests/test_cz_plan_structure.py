"""Server-owned table presentation and local operation controls on synthetic state."""
import json
from html import unescape
from html.parser import HTMLParser
import os
import re
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import cz_gate_plan as C
from quam_state_manager.web.routes import _cz_plan_table
from tests.test_cz_gate_plan import model_qubit
from tests.test_cz_plan_page import _chip, _client

ROOT = Path(__file__).resolve().parents[1]


def _row(values):
    state, _ = _chip()
    row = C.operations(state)[0][1]
    for line, value in zip(row["lines"], values):
        line["moves"] = value != "idle"
        line["point"]["validity"] = value
    return _cz_plan_table([row])[0]


@pytest.mark.parametrize("values,text,kind", [
    (["within the fitted model's measured range", "idle"], "in range (model)", "ok"),
    (["within the measured quadratic range", "idle"], "in range (quad)", "ok"),
    (["within the quadratic range", "idle"], "in range (quad, generic limit)", "ok"),
    (["within the fitted model's measured range", "within the measured quadratic range"], "in range (model / quad)", "ok"),
    (["within the fitted model's measured range", "outside the quadratic range"], "outside the quadratic range", "warn"),
    (["quadratic range unknown", "idle"], "quadratic range unknown", "unknown"),
    ([None, "idle"], "validity unknown", "unknown"),
])
def test_validity_decided_once(values, text, kind):
    row = _row(values)
    assert row.get("validity_class") == kind
    assert row["validity"] == text
    for name, value in zip(row["names"], values):
        if value and value != "idle":
            assert name + ": " + value in row["validity_title"]


def test_validity_markup_and_idle(tmp_path):
    html = _client(tmp_path).get("/cz-plan").get_data(as_text=True)
    assert re.search(r'class="muted"[^>]*>in range \(quad, generic limit\)</span>', html)
    assert _row(["idle", "idle"])["validity"] is None
    assert 'class="czp-prose czp-validity"' in html


def test_sort_cue_and_physics_titles(tmp_path):
    html = _client(tmp_path).get("/cz-plan").get_data(as_text=True)
    class Headers(HTMLParser):
        def __init__(self):
            super().__init__()
            self.heads = []
            self.buttons = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'th' and 'data-sort-key' in attrs:
                self.heads.append(attrs)
            if tag == 'button' and attrs.get('class') == 'czp-sort':
                self.buttons.append(attrs)
    parsed = Headers()
    parsed.feed(html)
    sortable = parsed.heads
    assert len(sortable) == 7
    for attrs in sortable:
        assert attrs.get('title', '').endswith('Click to sort.')
    assert all('title' not in attrs for attrs in parsed.buttons)


def test_sort_cue_css():
    css = (ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
    cue = re.search(r'\.czp-table th button\.czp-sort::after\s*\{([^}]+)', css)[1]
    assert 'opacity: 0' in cue and 'width:' in cue and 'content:' in cue
    assert 'th:hover button.czp-sort::after' in css and 'button.czp-sort:focus-visible::after' in css


def test_controls_placement(tmp_path):
    html = _client(tmp_path).get("/cz-plan").get_data(as_text=True)
    assert 'class="czp-controls czp-table-controls"' in html
    controls = html.split('class="czp-controls czp-table-controls"', 1)[1].split('<div class="czp-table-wrap">', 1)[0]
    assert 'id="czp-table-op"' in controls and 'Listed operation' in controls
    assert '<p class="muted czp-filter-note"' in controls
    assert '<div class="muted czp-history-note"' in controls
    assert html.index('</section>') < html.index('id="czp-table-op"') < html.index('id="czp-table"')


def test_lede_order_and_style(tmp_path):
    html = _client(tmp_path).get("/cz-plan").get_data(as_text=True)
    lede = html.split('class="muted czp-lede"', 1)[1].split('<details', 1)[0]
    assert lede.index('Where each CZ') < lede.index('Operating values')
    assert '<small class="muted czp-lede-caveat">Operating values describe the first lobe.' in lede
    assert '<p>' not in lede
    css = (ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
    assert 'font-size' not in re.search(r'\.czp-lede\s*\{([^}]+)', css)[1]


@pytest.mark.parametrize("rejected", [False, True])
def test_curvature_explains_frequency_source(rejected):
    qubit = model_qubit(f01_shift=7e6 if rejected else 0)
    qubit['freq_vs_flux_01_quad_term'] = 3.57 * abs(C.Conversion(qubit).curvature['here_hz_per_v2'])
    conv = C.Conversion(qubit)
    text = conv.curvature["note"]
    assert "fitted model's local curvature" in text
    clause = ('frequencies come from the lab flux model wherever its measured flux range covers the amplitude; '
              'the quad term is used only beyond it (see Estimate)')
    assert (clause in text) == (not rejected)
    assert not text.startswith('q')
    assert text in conv.notes


def _amplitude_state(mode):
    state, wiring = _chip()
    pulse = state['qubits']['q1']['z']['operations']['cz_flux']
    pulse.update(__class__='quam.components.pulses._FlatTopGaussianPulse', amplitude=-.1234,
                 flat_length=20, smoothing_length=8, sigma=2)
    pulse.pop('length', None)
    state['ports'] = {'analog_outputs': {'c1': {'1': {}}}}
    state['qubits']['q1']['z']['opx_output'] = '#/ports/analog_outputs/c1/1'
    macro = state['qubit_pairs']['q1-2']['macros']['cz_unipolar']
    if mode == 'copy':
        macro['flux_pulse_qubit'] = dict(pulse, id='cz_flux', amplitude=.29)
    elif mode in ('snz', 'third'):
        pulse.update(__class__='x.SNZTwoFluxPulse', amplitude=.12, neg_offset_v=.01,
                     unipolar=mode == 'third', flat_length=20, t_phi=2, lobe3_max_v=.06)
    elif mode == 'port':
        del state['qubits']['q1']['z']['opx_output']
    elif mode == 'refused':
        macro['flux_pulse_qubit'] = 'missing'
    return state, wiring


@pytest.mark.parametrize('mode', ['flat', 'snz', 'third', 'copy', 'port', 'refused'])
def test_played_amplitudes_in_table_and_payload(tmp_path, mode):
    state, wiring = _amplitude_state(mode)
    folder = tmp_path / 'state'
    folder.mkdir()
    (folder / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    (folder / 'wiring.json').write_text(json.dumps(wiring), encoding='utf-8')
    client = _client(tmp_path, folder)
    data = client.get('/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_unipolar').get_json()
    line, idle = data['row']['lines']
    html = client.get('/cz-plan').get_data(as_text=True)
    row = re.search(r'<tr[^>]*data-op="qubit_pairs.q1-2.macros.cz_unipolar".*?</tr>', html, re.S)[0]
    cell = re.search(r'<td class="czp-num czp-amp">(.*?)</td>', row, re.S)
    assert cell is not None
    cell = unescape(cell[1])
    assert idle['amp_text'] == chr(8212)
    assert line['amp_text'] in cell
    assert line['amp_title'] in cell
    assert line['pulse_path'] in line['amp_title']
    if mode == 'refused':
        assert line['amp_text'] == chr(8212) and 'not playable' in line['amp_title']
        assert re.search(r'data-s-amp="([^"]*)"', row)[1] == ''
    elif mode in ('snz', 'third'):
        assert line['amp_text'] == '+120 mV stored'
        assert 'first lobe +120 mV stored' in line['amp_title']
        assert ('third lobe -60 mV stored' if mode == 'third' else 'mirror lobe -130 mV stored') in line['amp_title']
    else:
        assert line['amp_text'] == '-123 mV' + (' stored' if mode == 'port' else '')
        assert float(re.search(r'data-s-amp="([^"]*)"', row)[1]) == pytest.approx(.1234)
    assert html.index('>At the operation (GHz)</button>') < html.index('>Z amp (V)</button>') < html.index('>&Delta; (MHz)</button>')


@pytest.mark.parametrize('mode', ['picker', 'amp', 'card'])
def test_local_picker_and_amplitude_sort(tmp_path, mode):
    page = tmp_path / 'page.html'
    client = _client(tmp_path)
    page.write_text(client.get('/cz-plan').get_data(as_text=True), encoding='utf-8')
    payload = tmp_path / 'payload.json'
    payload.write_text(json.dumps(client.get('/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_unipolar').get_json()), encoding='utf-8')
    result = subprocess.run(['node', str(ROOT / 'tests/cz_plan_structure_selfcheck.cjs'), str(page), mode, str(payload)],
                            cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'structure assertions passed' in result.stdout
