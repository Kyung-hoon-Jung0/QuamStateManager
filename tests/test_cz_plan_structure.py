"""Server-owned table presentation and local operation controls on synthetic state."""
import json
from html import unescape
from html.parser import HTMLParser
import os
import re
import subprocess
from pathlib import Path

import numpy as np
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
    assert lede[1:].lstrip().startswith('Where each CZ operation moves its qubits')
    css = (ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
    assert 'font-size' not in re.search(r'\.czp-lede\s*\{([^}]+)', css)[1]


CLAUSE = ("; this page uses the lab flux model, not the quad term: beyond the model's measured flux range"
          " the operation is refused (see Validity)")


@pytest.mark.parametrize("variant,rejected", [("ratio", False), ("ratio", True), ("other", False)])
def test_curvature_explains_frequency_source(variant, rejected):
    qubit = model_qubit(f01_shift=7e6 if rejected else 0)
    curv = C.Conversion(qubit).curvature
    qubit['freq_vs_flux_01_quad_term'] = (3.57 * abs(curv['here_hz_per_v2']) if variant == 'ratio'
                                          else abs(curv['other_hz_per_v2']))
    conv = C.Conversion(qubit)
    text = conv.curvature["note"]
    assert text.startswith('freq_vs_flux_01_quad_term ')
    assert ("fitted model's local curvature" if variant == 'ratio' else 'at the OTHER sweet spot') in text
    if rejected:
        assert text.endswith("fitted model's local curvature")
    else:
        assert text.endswith(CLAUSE)
    assert text in conv.notes


def test_the_clause_is_true_on_every_branch_of_point():
    """With an anchor and no rejection the quad term never sets a frequency: inside the
    model window the model answers, beyond it the operation is refused."""
    qubit = model_qubit()
    qubit['freq_vs_flux_01_quad_term'] = 3.57 * abs(C.Conversion(qubit).curvature['here_hz_per_v2'])
    conv = C.Conversion(qubit)
    assert conv.anchor is not None and conv.rejection is None and CLAUSE in conv.curvature['note']
    m = conv.model
    span = max(abs(m['amp_min_v']), abs(m['amp_max_v']))
    seen = set()
    for a in np.linspace(-3 * span, 3 * span, 241):
        p = conv.point(float(a))
        if a == 0:
            continue
        if p['frequency_hz'] is None:
            assert p['validity'] == "beyond the fitted arc's measured range"
            seen.add('refused')
        else:
            assert p['kind'] == conv.model_kind and p['frequency_hz'] == p['model_hz']
            seen.add('model')
    assert seen == {'model', 'refused'}


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
        assert line['amp_text'] == '+120 mV'
        assert 'first lobe +120 mV' in line['amp_title']
        assert ('third lobe -60 mV' if mode == 'third' else 'mirror lobe -130 mV') in line['amp_title']
    elif mode == 'port':
        # no LF port: the stored number, never a volt unit
        assert line['amp_text'] == '-0.123 stored'
        assert 'V' not in line['amp_text']
    else:
        assert line['amp_text'] == '-123 mV'
        assert float(re.search(r'data-s-amp="([^"]*)"', row)[1]) == pytest.approx(.1234)
    assert html.index('>At the operation (GHz)</button>') < html.index('>Z amp</button>') < html.index('>&Delta; (MHz)</button>')


def test_signed_volts_never_prints_plus_minus_zero():
    assert C.signed_volts(0.0) == '0 V' and C.signed_volts(-0.0) == '0 V'
    assert C.signed_volts(-0.001) == '-1 mV' and C.signed_volts(0.25) == '+250 mV'
    assert C._signed_stored(-0.0) == '0 stored'


def _lobe_state(pulse):
    state, wiring = _chip()
    state['qubits']['q1']['z']['operations']['cz_flux'] = pulse
    state['ports'] = {'analog_outputs': {'c1': {'1': {}}}}
    state['qubits']['q1']['z']['opx_output'] = '#/ports/analog_outputs/c1/1'
    return state


@pytest.mark.parametrize('amplitude', [0.08, -0.08])
def test_catalog_snz_volts_match_the_synthesized_waveform(amplitude):
    """A real catalog class: the table's lobe volts equal the synthesized plateau samples,
    with the sign of the first lobe kept for a negative amplitude."""
    from quam_state_manager.core.waveform_synth import synthesize_raw
    pulse = {'__class__': 'quam.components.pulses.SNZPulse', 'amplitude': amplitude,
             'flat_length': 20, 't_phi_eff': 0.0, 'padding': 0, 'length': 24}
    row = next(r for r in C.operations(_lobe_state(pulse))[0] if r['operation'] == 'cz_unipolar')
    line = row['lines'][0]
    samples = np.asarray(synthesize_raw('SNZPulse', dict(pulse)))
    first = next(s for s in samples if s != 0)
    last = next(s for s in samples[::-1] if s != 0)
    assert [lobe['name'] for lobe in line['lobes']] == ['first lobe', 'mirror lobe']
    assert line['amp_text'] == C.signed_volts(first)
    assert 'mirror lobe ' + C.signed_volts(last) in line['amp_title']
    assert line['amp_text'] == ('+80 mV' if amplitude > 0 else '-80 mV')
    assert line['amp_sort'] == pytest.approx(abs(amplitude))


def test_offset_mirror_volts_match_the_lab_transcription():
    """An asymmetric mirror: the volts are the transcribed plateau samples, not the
    stored amplitude scaled by a unit-amplitude peak."""
    from quam_state_manager.core.waveform_synth import lab_waveform
    pulse = {'__class__': 'x.SNZTwoFluxPulse', 'amplitude': -0.1, 'neg_offset_v': 0.02,
             'flat_length': 20, 't_phi': 2, 'length': 28}
    row = next(r for r in C.operations(_lobe_state(pulse))[0] if r['operation'] == 'cz_unipolar')
    line = row['lines'][0]
    samples = np.asarray(lab_waveform(dict(pulse)))
    assert line['amp_text'] == C.signed_volts(samples.min()) == '-100 mV'
    assert 'mirror lobe ' + C.signed_volts(samples.max()) in line['amp_title']
    assert 'mirror lobe +120 mV' in line['amp_title']


def test_a_mirror_lobe_past_the_range_is_not_in_range(tmp_path):
    """The first lobe is inside the generic quadratic limit, the mirror lobe (amplitude +
    offset) is past it: the row warns and names the lobe."""
    state = _lobe_state({'__class__': 'x.SNZTwoFluxPulse', 'amplitude': 0.12, 'neg_offset_v': 0.05,
                         'flat_length': 20, 't_phi': 2, 'length': 28})
    row = next(r for r in C.operations(state)[0] if r['operation'] == 'cz_unipolar')
    line = row['lines'][0]
    assert line['point']['validity'] == 'within the quadratic range'
    assert line['lobes'][1]['point']['validity'] == 'outside the quadratic range'
    table = _cz_plan_table([row])[0]
    assert table['validity_class'] == 'warn'
    assert table['validity'] == 'outside the quadratic range (mirror lobe)'
    assert table['validity_title'] == 'q1 mirror lobe: outside the quadratic range'


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
