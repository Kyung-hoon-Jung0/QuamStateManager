"""W8: stored CZ measurements and reason freshness are read-only plan facts."""
import copy
import json

import pytest

from quam_state_manager.core import cz_gate_plan as C
from tests.test_cz_plan_page import _chip, _client


def measured_plan():
    return dict(two_j_hz=14.01e6, t_lim_ns=71.38, t_p_ns=74,
                two_j_hz_run_id=3, reasons_json='["two_j_needs_remeasure", "other"]',
                reasons_json_run_id=1)


def test_state_measurements_reach_plan_route(tmp_path, monkeypatch):
    state, wiring = _chip()
    state['qubit_pairs']['q1-2']['macros']['cz_unipolar']['duration_qubit'] = 10
    state['qubit_pairs']['q1-2']['extras'] = {'cz_plan': measured_plan()}
    folder = tmp_path / 'chip'
    folder.mkdir()
    (folder / 'state.json').write_text(json.dumps(state))
    (folder / 'wiring.json').write_text(json.dumps(wiring))
    data = _client(tmp_path, folder).get('/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_unipolar').get_json()
    plan = data['row']['plan']
    assert [plan[k] for k in ('two_j_hz', 't_lim_ns', 't_p_ns')] == [14.01e6, 71.38, 74]
    assert plan['sources']['two_j_hz']['run_id'] == 3
    assert plan['duration_minus_t_p_ns'] == -34
    from quam_state_manager.web import routes
    prefix = 'qubit_pairs.q1-2.extras.cz_plan.'
    def history(ctx, paths, **kwargs):
        assert all(prefix + field in paths for field in C.PLAN_FIELDS)
        return {'mode': 'ledger', 'rows': {
            prefix + field: {'effective': [{'value': plan['stored_values'][field],
                'run_id': run, 'provenance': 'run_proven'}]}
            for field, run in [('two_j_hz', 8), ('reasons_json', 7)]}}
    monkeypatch.setattr(routes, '_value_history', history)
    data = _client(tmp_path / 'second', folder).get('/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_unipolar').get_json()
    assert data['row']['plan']['sources']['two_j_hz']['run_id'] == 8
    assert data['row']['plan']['sources']['reasons_json']['run_id'] == 7
    assert data['row']['plan']['reasons'] == ['other']


def test_newer_measurement_resolves_reason_without_changing_state():
    plan = measured_plan()
    original = copy.deepcopy(plan)
    view = C._plan(plan, [])
    assert view['reasons'] == ['other']
    assert view['reason_details'][0]['status'] == 'resolved'
    assert plan == original


@pytest.mark.parametrize('measurement_run', [None, 'unranked'])
def test_incomparable_reason_names_source_run(measurement_run):
    plan = measured_plan()
    plan['two_j_hz_run_id'] = measurement_run
    view = C._plan(plan, [])
    assert view['reasons'][0] == 'two_j_needs_remeasure (from run #1 (may be stale))'


@pytest.mark.parametrize('measurement_run', [1, 0])
def test_same_or_older_measurement_keeps_reason(measurement_run):
    plan = measured_plan()
    plan['two_j_hz_run_id'] = measurement_run
    assert C._plan(plan, [])['reasons'][0] == 'two_j_needs_remeasure'


def test_history_sources_are_field_specific_and_match_current_values():
    plan = measured_plan()
    del plan['two_j_hz_run_id'], plan['reasons_json_run_id']
    view = C._plan(plan, [])
    prefix = 'qubit_pairs.p.extras.cz_plan.'
    def series(value, run):
        return {'points': [{'value': value, 'run_id': run, 'provenance': 'run_proven'}]}
    history = {'rows': {prefix + 'two_j_hz': series(plan['two_j_hz'], 3),
                        prefix + 'reasons_json': series(plan['reasons_json'], 1)}}
    C.plan_history(view, prefix, history)
    assert view['reasons'] == ['other']
    assert view['sources']['two_j_hz']['run_id'] == 3
    history['rows'][prefix + 'two_j_hz'] = series(1, 3)
    view = C._plan(plan, [])
    C.plan_history(view, prefix, history)
    assert 'may be stale' in view['reasons'][0]


def test_timestamp_order_and_missing_value():
    plan = measured_plan()
    del plan['two_j_hz_run_id'], plan['reasons_json_run_id']
    plan.update(two_j_hz_timestamp='2026-11-02T00:00:00Z', reasons_json_timestamp='2026-11-01T00:00:00Z')
    assert C._plan(plan, [])['reasons'] == ['other']
    plan['two_j_hz'] = None
    assert 'two_j_needs_remeasure' in C._plan(plan, [])['reasons']
