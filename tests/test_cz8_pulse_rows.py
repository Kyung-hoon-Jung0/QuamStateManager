"""Pulses table: a square pulse's thumbnail closes to the baseline."""
import re

from quam_state_manager.core.waveform_synth import sparkline_svg


def _points(svg):
    return [tuple(map(float, p.split(','))) for p in re.search(r'points="([^"]+)"', svg).group(1).split()]


def test_a_square_pulse_thumbnail_shows_its_edges():
    pts = _points(sparkline_svg({'ok': True, 'i': [0.1] * 50}))
    baseline = 24 - 1.5
    assert pts[0][1] == baseline and pts[-1][1] == baseline   # rise and fall drawn
    assert any(y == 1.5 for _, y in pts)                       # the plateau at the top


def test_a_pulse_that_already_starts_at_zero_is_unchanged():
    vals = [0.0, 0.05, 0.1, 0.05, 0.0]
    pts = _points(sparkline_svg({'ok': True, 'i': vals}))
    assert len(pts) == len(vals)


def test_an_alias_row_shows_its_target_length():
    """A pair-macro slot pointing at a qubit operation whose length is a runtime property
    (#./inferred_total_length) showed '-' while the target row showed the length."""
    from quam_state_manager.core.pulse_index import _row_for_pulse
    pulse = {'__class__': 'quam.components.pulses._FlatTopGaussianPulse', 'length': '#./inferred_total_length',
             'amplitude': 0.1, 'flat_length': 100, 'smoothing_length': 20, 'post_zero_padding_length': 20,
             'id': None, 'digital_marker': None, 'axis_angle': None}
    alias = '#/qubits/q1/z/operations/cz_flat'
    merged = {'qubits': {'q1': {'z': {'operations': {'cz_flat': pulse}}}},
              'qubit_pairs': {'q1-2': {'macros': {'cz': {'flux_pulse_qubit': alias}}}}}
    target = _row_for_pulse(merged, 'qubits.q1.z.operations.cz_flat', pulse, owner_kind='qubit',
                            owner='q1', channel='z', op_name='cz_flat', gate=None)
    row = _row_for_pulse(merged, 'qubit_pairs.q1-2.macros.cz.flux_pulse_qubit', alias, owner_kind='pair',
                         owner='q1-2', channel='flux', op_name='cz.flux_pulse_qubit', gate='cz')
    assert target['length'] is not None
    assert row['is_alias'] and row['length'] == target['length']
