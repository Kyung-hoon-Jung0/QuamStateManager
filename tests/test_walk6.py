"""Synthetic regressions for the walk-6 review."""
import json
import re

import pytest

from quam_state_manager.core import cz_gate_plan as C
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.web.app import create_app


def fixture_state():
    pulse = {"__class__": "synthetic.SNZTwoFluxPulse", "amplitude": .12,
             "flat_length": 48, "length": "#./inferred_length", "neg_offset_v": .001}
    macro = {"__class__": "synthetic.CZGateTwoFlux", "flux_pulse_qubit": pulse,
             "flux_pulse_target": dict(pulse, amplitude=0., neg_offset_v=0.),
             "phase_shift_control": .125, "phase_shift_target": .25,
             "fidelity": {"StandardRB": .91, "StandardRB_load_id": 11,
                          "InterleavedRB": .97, "InterleavedRB_load_id": 12,
                          "Bell_State": {"Fidelity": .93}}}
    state = {"qubits": {}, "qubit_pairs": {"p": {
        "qubit_control": "#/qubits/q0", "qubit_target": "#/qubits/q1",
        "detuning": 400e6, "macros": {"cz_SNZ": macro}}}}
    for i in range(2):
        state["qubits"][f"q{i}"] = {"f_01": 5e9-i*400e6, "anharmonicity": -200e6,
            "freq_vs_flux_01_quad_term": -8e9, "z": {"flux_point": "joint", "operations": {}}}
    # Like a generated operation: references to optional fields absent on the macro.
    mirror = {"__class__": pulse["__class__"], "length": "#./inferred_length"}
    for k in ("amplitude", "flat_length", "neg_offset_v", "tail_length", "padding", "t_phi", "b_over_a"):
        mirror[k] = "#/qubit_pairs/p/macros/cz_SNZ/flux_pulse_qubit/" + k
    state["qubits"]["q0"]["z"]["operations"]["snz"] = mirror
    pulse["id"] = "snz"
    return state


@pytest.fixture
def chip(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(fixture_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    return client, QuamStore(tmp_path)


def test_w6_1_snz_duration_and_frequency_samples(chip):
    client, store = chip
    row = C.operations(store.merged)[0][0]
    assert row["duration_ns"] == 52
    ans = client.get("/cz-plan/data", query_string={"op": row["path"]}).get_json()
    expected = [0.] + [.12]*24 + [0., 0.] + [-.121]*24 + [0.]
    assert ans["trajectory"][0]["amplitude_v"] == pytest.approx(expected)
    conv = C.Conversion(C.qubit_at(store.merged, "qubits.q0"))
    assert ans["trajectory"][0]["frequency_hz"] == pytest.approx([conv.point(a)["frequency_hz"] for a in expected])
    assert len(ans["time_panel"]["time_ns"]) == 52


def test_w6_2_report_metric_runs(chip):
    html = chip[0].get("/chip-status/report").get_data(as_text=True)
    for metric, run in (("StandardRB", "11"), ("InterleavedRB", "12"), ("Bell_State", "unrecorded")):
        rows = re.findall(r"<tr>(.*?)</tr>", html, re.S)
        row = next(r for r in rows if re.search(r"<td>\s*" + metric + r"\s*</td>", r))
        assert re.search(r"<td>\s*" + run + r"\s*</td>\s*$", row)


def test_w6_2_missing_irb_id_uses_its_own_fit(tmp_path):
    from quam_state_manager.core.dataset import DatasetStore
    folder = tmp_path
    state = fixture_state()
    fidelity = state['qubit_pairs']['p']['macros']['cz_SNZ']['fidelity']
    del fidelity['InterleavedRB_load_id']
    fidelity['InterleavedRB_alpha'] = .96
    (folder / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    (folder / 'wiring.json').write_text('{}', encoding='utf-8')
    run = folder / 'data' / '2026-01-01' / '#12_two_qubit_interleaved_rb_120000'
    run.mkdir(parents=True)
    (run / 'node.json').write_text(json.dumps({'data': {'parameters': {'model': {'operation': 'cz_SNZ', 'qubit_pairs': ['p']}}}}))
    (run / 'data.json').write_text(json.dumps({'fit_results': {'p': {'fidelity': .97, 'alpha': .96}}}))
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    client = app.test_client()
    app.config['dataset_store'] = DatasetStore(folder / 'data')
    client.post('/load', data={'folder': str(folder)})
    html = client.get('/chip-status/report').get_data(as_text=True)
    for metric in ('InterleavedRB', 'InterleavedRB_alpha'):
        row = next(r for r in re.findall(r'<tr>(.*?)</tr>', html, re.S) if f'<td>{metric}</td>' in r)
        assert '12' in row and 'inferred source, not proven' in row


def test_w6_2_ambiguous_or_wrong_gate_never_gets_a_run():
    from types import SimpleNamespace
    from quam_state_manager.core.rb_gate_fidelity import cite_missing_runs
    def run(rid, operation='cz_SNZ'):
        return SimpleNamespace(run_id=rid, experiment_name='two_qubit_interleaved_rb',
            parameters={'operation': operation}, fit_results={'p': {'fidelity': .97, 'alpha': .96}})
    row = {'gate': 'cz_SNZ', 'metric': 'InterleavedRB', 'value': .97, 'load_id': None}
    edge = {'pair_id': 'p', 'gate_fidelities': [row,
        {'gate': 'cz_SNZ', 'metric': 'InterleavedRB_alpha', 'value': .96}]}
    cite_missing_runs([edge], [run(12, 'other_gate')])
    assert row['load_id'] is None
    cite_missing_runs([edge], [run(12), run(13)])
    assert row['load_id'] is None


def test_w6_4_zero_first_lobe_with_nonzero_mirror_still_moves():
    state = fixture_state()
    state['qubit_pairs']['p']['macros']['cz_SNZ']['flux_pulse_target']['neg_offset_v'] = .001
    assert C.operations(state)[0][0]['lines'][1]['moves'] is True


def test_w6_3_phase_unit(chip):
    html = chip[0].get("/chip-status/report").get_data(as_text=True)
    assert "Phase shift ctrl (turns)" in html
    assert "Phase shift tgt (turns)" in html


def test_w6_4_zero_line_stays_idle(chip):
    client, store = chip
    row = C.operations(store.merged)[0][0]
    assert [ln["moves"] for ln in row["lines"]] == [True, False]
    ans = client.get("/cz-plan/data", query_string={"op": row["path"]}).get_json()
    assert ans["gate_loss"][1]["moves"] is False


def test_w6_5_pair_detuning_unit(chip):
    from quam_state_manager.core.units import stored_unit_label
    assert stored_unit_label("pair_detuning") == "V"
    html = chip[0].get("/chip-status/report").get_data(as_text=True)
    assert "Detuning (V)" in html
    assert "400000000.0 (raw, unit doubtful)" in html
    assert "400000000.0 V" not in html
    assert "400000000.0 Hz" not in html


def test_w6_6_xeb_fit_extraction(tmp_path):
    from quam_state_manager.core.dataset import DatasetStore
    folder = tmp_path / "2026-01-01" / "#1_two_qubit_xeb_120000"
    folder.mkdir(parents=True)
    (folder / "node.json").write_text(json.dumps({"data": {"parameters": {"model": {"qubit_pairs": ["q0-q1"]}}}}))
    (folder / "data.json").write_text(json.dumps({"estimated_2q_params": {
        "phi_cphase": 2.5, "phi_rz1": .1, "phi_rz2": .2, "starts": [{"loss": 1.}]},
        "purity_err_per_cycle": .98, "purity_fit_params": "./arrays.npz#purity_fit_params"}))
    fit = DatasetStore(tmp_path).get_run(1)["fit_results"]["q0-q1"]
    assert fit["phi_cphase"] == 2.5
    assert fit["phi_rz1"] == .1 and fit["phi_rz2"] == .2
    assert fit["purity_err_per_cycle"] == .98
    assert "starts" not in fit


def test_w6_8_difference_is_stored_minus_required(chip, monkeypatch):
    monkeypatch.setattr(C, "_solve", lambda *a: {"ok": True, "amplitude_v": .1198, "line": "control"})
    result = C.solve(chip[1].merged, "p", "cz_SNZ")
    assert result["difference_v"] == pytest.approx(.0002)


def test_w6_review_fit_is_not_proof(tmp_path):
    from types import SimpleNamespace
    from quam_state_manager.core.rb_gate_fidelity import cite_missing_runs
    row = {'gate': 'cz_SNZ', 'metric': 'InterleavedRB', 'value': .97, 'load_id': None}
    run = SimpleNamespace(run_id=12, experiment_name='two_qubit_interleaved_rb',
        parameters={'operation': 'cz_SNZ'}, fit_results={'p': {'fidelity': .97}}, folder_path=tmp_path)
    cite_missing_runs([{'pair_id': 'p', 'gate_fidelities': [row]}], [run])
    assert row['load_id'] is None
    assert row['inferred_load_id'] == 12


@pytest.mark.parametrize('name,operation', [('renamed_calibration', 'other'), ('two_qubit_interleaved_rb', 'cz_SNZ')])
def test_w6_review_patch_proof_ignores_name_heuristic(tmp_path, name, operation):
    from types import SimpleNamespace
    from quam_state_manager.core.rb_gate_fidelity import cite_missing_runs
    (tmp_path / 'node.json').write_text(json.dumps({'patches': [{'op': 'replace',
        'path': '/quam/qubit_pairs/p/macros/cz_SNZ/fidelity/InterleavedRB', 'value': .97}]}))
    row = {'gate': 'cz_SNZ', 'metric': 'InterleavedRB', 'value': .97, 'load_id': None}
    run = SimpleNamespace(run_id=12, experiment_name=name, parameters={'operation': operation},
        fit_results={}, folder_path=tmp_path)
    cite_missing_runs([{'pair_id': 'p', 'gate_fidelities': [row]}], [run])
    assert row['load_id'] == 12
    assert 'inferred_load_id' not in row


@pytest.mark.parametrize('source_kind', ['fixture', 'installed'])
def test_w6_review_phase_unit_has_executable_source_evidence(source_kind):
    import ast
    import sysconfig
    from pathlib import Path
    from quam_state_manager.core.units import stored_unit_label
    source = Path(sysconfig.get_paths()['purelib']) / 'quam_builder/architecture/superconducting/custom_gates/flux_tunable_transmon_pair/two_qubit_gates.py'
    if source_kind == 'fixture':
        source = Path(__file__).parent / 'fixtures/w6_cz_gate_phase.py'
    elif not source.is_file():
        pytest.skip('Installed gate unavailable; portable source excerpt checked separately')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    gate = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CZGate')
    for side in ('control', 'target'):
        assert stored_unit_label('phase_shift_' + side) == 'turns'
        calls = [n for n in ast.walk(gate) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == 'frame_rotation_2pi'
                 and ast.unparse(n.func.value).endswith('qubit_' + side + '.xy')]
        assert any([ast.unparse(a) for a in n.args] == ['self.phase_shift_' + side] for n in calls)


def test_w6_review_inferred_ui_surfaces():
    import subprocess
    result = subprocess.run(['node', 'tests/w6_provenance_selfcheck.cjs'], capture_output=True,
                            text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_w6_review_inferred_deep_link(chip, tmp_path):
    from quam_state_manager.core.dataset import DatasetStore
    folder = tmp_path / 'data' / '2026-01-01' / '#12_two_qubit_interleaved_rb_120000'
    folder.mkdir(parents=True)
    (folder / 'node.json').write_text('{}')
    (folder / 'data.json').write_text('{}')
    client = chip[0]
    client.application.config['dataset_store'] = DatasetStore(tmp_path / 'data')
    response = client.get('/dataset/by-run/12?via=inferred', follow_redirects=True)
    assert response.status_code == 200
    assert 'inferred source, not proven' in response.get_data(as_text=True)


@pytest.mark.parametrize('patch', [
    {'op':'replace', 'path':'/qubit_pairs/other/macros/cz_SNZ/fidelity/InterleavedRB', 'value':.97},
    {'op':'replace', 'path':'/qubit_pairs/p/macros/other/fidelity/InterleavedRB', 'value':.97},
    {'op':'replace', 'path':'/qubit_pairs/p/macros/cz_SNZ/fidelity/StandardRB', 'value':.97},
    {'op':'replace', 'path':'/qubit_pairs/p/macros/cz_SNZ/fidelity/InterleavedRB', 'value':.98},
    {'op':'test', 'path':'/qubit_pairs/p/macros/cz_SNZ/fidelity/InterleavedRB', 'value':.97},
])
def test_w6_review_only_the_written_metric_and_value_are_proof(tmp_path, patch):
    from types import SimpleNamespace
    from quam_state_manager.core.rb_gate_fidelity import cite_missing_runs
    (tmp_path / 'node.json').write_text(json.dumps({'patches':[patch]}))
    row = {'gate':'cz_SNZ', 'metric':'InterleavedRB', 'value':.97, 'load_id':None}
    run = SimpleNamespace(run_id=12, experiment_name='two_qubit_interleaved_rb',
        parameters={'operation':'cz_SNZ'}, fit_results={'p':{'fidelity':.97}}, folder_path=tmp_path)
    cite_missing_runs([{'pair_id':'p', 'gate_fidelities':[row]}], [run])
    assert row['load_id'] is None
    assert row['inferred_load_id'] == 12


def test_w6_review_patch_path_keeps_literal_gate_key(tmp_path):
    from types import SimpleNamespace
    from quam_state_manager.core.rb_gate_fidelity import cite_missing_runs
    (tmp_path / 'node.json').write_text(json.dumps({'patches':[{'op':'replace',
        'path':'/qubit_pairs/p/macros/cz/SNZ/fidelity/InterleavedRB', 'value':.97}]}))
    row = {'gate':'cz.SNZ', 'metric':'InterleavedRB', 'value':.97, 'load_id':None}
    run = SimpleNamespace(run_id=12, experiment_name='renamed', parameters={}, fit_results={}, folder_path=tmp_path)
    cite_missing_runs([{'pair_id':'p', 'gate_fidelities':[row]}], [run])
    assert row['load_id'] is None


@pytest.mark.parametrize('field', ['run', 'load_id', 'run_id', 'dataset_id'])
def test_w6_recorded_nested_run_is_proof(field):
    from quam_state_manager.core.query import _extract_pair_gate_fidelities
    rows = _extract_pair_gate_fidelities({'cz': {'fidelity': {
        'InterleavedRB': {'Fidelity': .97, field: 12}}}})
    assert rows[0]['load_id'] == 12


def test_w6_alpha_owns_its_citation():
    from quam_state_manager.core.query import _extract_pair_gate_fidelities
    def rows(fid):
        return {r['metric']: r for r in _extract_pair_gate_fidelities({'cz': {'fidelity': fid}})}
    fid = {'StandardRB': .97, 'StandardRB_alpha': .96, 'StandardRB_load_id': 11}
    alpha = rows(fid)['StandardRB_alpha']
    assert alpha['load_id'] is None
    assert alpha['inferred_load_id'] == 11
    from quam_state_manager.core.rb_gate_fidelity import cite_missing_runs
    cite_missing_runs([{'pair_id': 'p', 'gate_fidelities': [alpha]}], [])
    assert alpha['inferred_load_id'] == 11
    fid['StandardRB_alpha_load_id'] = 12
    assert rows(fid)['StandardRB_alpha']['load_id'] == 12


def test_w6_alpha_patch_overrides_sibling_candidate(tmp_path):
    from types import SimpleNamespace
    from quam_state_manager.core.query import _extract_pair_gate_fidelities
    from quam_state_manager.core.rb_gate_fidelity import cite_missing_runs
    rows = _extract_pair_gate_fidelities({'cz': {'fidelity': {
        'StandardRB': .97, 'StandardRB_alpha': .96, 'StandardRB_load_id': 11}}})
    (tmp_path / 'node.json').write_text(json.dumps({'patches': [{'op': 'replace',
        'path': '/qubit_pairs/p/macros/cz/fidelity/StandardRB_alpha', 'value': .96}]}))
    run = SimpleNamespace(run_id=12, experiment_name='renamed', parameters={}, fit_results={}, folder_path=tmp_path)
    cite_missing_runs([{'pair_id': 'p', 'gate_fidelities': rows}], [run])
    alpha = next(r for r in rows if r['metric'] == 'StandardRB_alpha')
    assert alpha['load_id'] == 12
    assert 'inferred_load_id' not in alpha


def test_w6_recorded_run_renders_as_proven(tmp_path):
    state = fixture_state()
    state['qubit_pairs']['p']['macros']['cz_SNZ']['fidelity']['InterleavedRB'] = {'Fidelity': .97, 'run': 12}
    del state['qubit_pairs']['p']['macros']['cz_SNZ']['fidelity']['InterleavedRB_load_id']
    (tmp_path / 'state.json').write_text(json.dumps(state))
    (tmp_path / 'wiring.json').write_text('{}')
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    client = app.test_client()
    client.post('/load', data={'folder': str(tmp_path)})
    topology = client.get('/api/topology').get_json()
    metric = next(r for r in topology['edges'][0]['gate_fidelities'] if r['metric'] == 'InterleavedRB')
    assert metric['load_id'] == 12
    assert 'inferred_load_id' not in metric
    html = client.get('/chip-status/report').get_data(as_text=True)
    row = next(r for r in re.findall(r'<tr>(.*?)</tr>', html, re.S) if '<td>InterleavedRB</td>' in r)
    assert re.search(r'<td>\s*12\s*</td>\s*$', row)
    assert 'inferred' not in row
