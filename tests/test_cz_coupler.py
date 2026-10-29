"""S3 model, pulsed frequencies, dressed distances, browser and official storage pins."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess

import numpy as np
import pytest

from quam_state_manager.core import cz_flux_math as M, cz_gate_plan as C
from tests.test_cz_plan_page import _chip, _client
from tests.test_gate_pulse_ids import loaded

ROOT = Path(__file__).resolve().parents[1]
MODEL = dict(fc_max_hz=7.2e9, ec_c_hz=250e6, d_c=.1, phi0_v=.9,
             phi_idle=.03, g_c_hz=60e6, g_t_hz=70e6, g_ct_hz=1e6,
             source="manual", created_at="2026-10-29")


def test_unknown_and_validation():
    doc, _ = _chip()
    doc['qubit_pairs']['q1-2']['coupler'] = {'operations': {}}
    doc['qubit_pairs']['q1-2']['macros']['cz_unipolar']['coupler_flux_pulse'] = {'amplitude': .1}
    rows, _ = C.operations(doc)
    assert C.COUPLER_UNKNOWN in rows[0]['notes']
    for row in rows:
        for p in C.operating_points(row):
            assert p['coupler_shift_hz'] is None
            assert p['shifted_frequency_hz'] is None
    assert C.coupler_model(MODEL) == MODEL
    for key in MODEL:
        bad = deepcopy(MODEL)
        bad.pop(key)
        assert C.coupler_model(bad) is None
    for key, value in [('d_c', 1), ('phi0_v', 0), ('g_c_hz', -1), ('fc_max_hz', float('inf'))]:
        assert C.coupler_model(dict(MODEL, **{key: value})) is None


def test_lamb_cr_switch():
    for ratio in (.02, .05, .1):
        fq, g = 5e9, 60e6
        fc = fq + g / ratio
        states = M.dressed_energies([fq, fc], [-200e6, -100e6], [(0, 1, g)], [(0, 0), (1, 0)])
        shift = states[1][0] - states[0][0] - fq
        result = M.coupler_shift(fq, fc, -200e6, 100e6, g)
        assert result['coupler_method'] == 'dispersive estimate (f_01 shift only)'
        assert result['coupler_shift_hz'] == pytest.approx(shift, rel=.0016)
        assert result['coupler_shift_hz'] == M.lamb_shift(fq, fc, g, -200e6, -100e6)
    assert M.coupler_shift(5e9, 5.59e9, -200e6, 100e6, 60e6)['coupler_method'] == 'diagonalised'


def test_pulsed_frequencies():
    doc, _ = _chip()
    pair = doc['qubit_pairs']['q1-2']
    pair['extras'] = {'sm_coupler_model': MODEL}
    pair['coupler'] = {'operations': {'cp': {'id': 'cp', 'amplitude': .12}}}
    pair['macros']['cz_unipolar']['coupler_flux_pulse'] = '#/qubit_pairs/q1-2/coupler/operations/cp'
    rows, _ = C.operations(doc)
    row = rows[0]
    wc = M.coupler_frequency(MODEL, .12)
    assert wc != M.coupler_frequency(MODEL, 0)
    for ln, key, alpha in zip(row['lines'], ['g_c_hz', 'g_t_hz'], [-200e6, -210e6]):
        fq = ln['point']['frequency_hz']
        assert ln['coupler_frequency_hz'] == wc
        assert ln['coupler_shift_hz'] == M.lamb_shift(fq, wc, MODEL[key], alpha, MODEL["ec_c_hz"])
        assert ln['shifted_frequency_hz'] == fq + ln['coupler_shift_hz']
    assert row['lines'][0]['coupler_shift_hz'] != M.lamb_shift(5e9, wc, MODEL['g_c_hz'])


def test_dressed_distance():
    args = (5e9, 4.72e9, 5.5e9, -200e6, -210e6, 100e6, 60e6, 40e6, 1e6)
    rs = M.dressed_resonances(*args)
    states = M.dressed_energies([args[0], args[2], args[1]], [args[3], args[5], args[4]],
        [(0,1,args[6]), (1,2,args[7]), (0,2,args[8])], [(1,0,1), (0,0,2), (2,0,0)])
    assert all(r['distance_hz'] is not None for r in rs)
    assert rs[1]['dressed_energy_difference_hz'] == states[1][0] - states[0][0]
    assert rs[0]['distance_hz'] == states[2][0] - states[0][0]
    assert rs[1]['distance_hz'] == states[0][0] - states[1][0]
    # An f_01 correction cannot stand in for the dressed leakage energy.
    shift = M.coupler_shift(args[0], args[2], args[3], args[5], args[6])['coupler_shift_hz']
    assert abs(rs[1]['distance_hz'] - (args[0]-args[1]+abs(args[4])+2*shift)) > 1e6


def test_hybridised_refusal():
    result = M.coupler_shift(5e9, 5e9, -200e6, 100e6, 60e6)
    assert result['coupler_shift_hz'] is None
    assert result['coupler_method'] == 'qubit and coupler hybridised: no frequency assigned'
    assert all(r['distance_hz'] is None for r in M.dressed_resonances(5e9, 4.7e9, 5e9, 200e6, 210e6, 100e6, 60e6, 40e6, 1e6))


def test_independent_two_hundred():
    from tests.test_cz_flux_math_crosscheck import load_check
    spec = load_check('checks/spec_checks.py')
    bound_check = load_check('checks/s3l_bound.py')
    rng = np.random.default_rng(304)
    for _ in range(200):
        model = dict(MODEL, fc_max_hz=rng.uniform(6e9, 8e9), phi_idle=rng.uniform(-.1,.1))
        amp = rng.uniform(-.1,.1)
        wc = spec.F(model['phi_idle']+amp/model['phi0_v'], model['fc_max_hz'], model['ec_c_hz'], model['d_c'])
        assert M.coupler_frequency(model, amp) == pytest.approx(wc, rel=1e-6)
        fq = rng.uniform(4e9, 5e9)
        g = rng.uniform(10e6, 50e6)
        f, overlap = spec.two_mode(fq, wc, -200e6, -250e6, g)
        states = M.dressed_energies([fq, wc], [200e6, 250e6], [(0,1,g)], [(0,0),(1,0)])
        assert states[1][0]-states[0][0] == pytest.approx(f, rel=1e-6)
        assert states[1][1] == pytest.approx(overlap, rel=1e-6)
        assert M.lamb_shift(fq,wc,g,-200e6,-250e6) == pytest.approx(bound_check.estimates(fq,wc,-200e6,-250e6,g)[-1], rel=1e-6)


def browser_form(create_extras=False):
    source = (ROOT/'quam_state_manager/web/static/cz-plan.js').read_text(encoding='utf-8')
    source = source[source.index('    function stageCoupler'):source.index('    window.CzStageCoupler')]
    script = """
const posts=[];
global.window={__chipToken:'token',_keepMineSeen:()=>({n:7,sig:'seen'})};
global.refresh=()=>{};
global.fetch=(url,options)=>{posts.push({url,body:JSON.parse(options.body)});return Promise.resolve({json:()=>({ok:true})});};
""" + source + """
const model=JSON.parse(process.argv[1]);
const form={dataset:{pair:'q1-q2',createExtras:process.argv[2]},elements:{namedItem:k=>({value:model[k]})},querySelector:()=>({})};
stageCoupler(form).then(()=>console.log(JSON.stringify(posts)));
"""
    result = subprocess.run(['node','-e',script,json.dumps(MODEL),'1' if create_extras else '0'],capture_output=True,text=True,check=True)
    return json.loads(result.stdout)


def test_form_one_batch_group_and_identity(loaded):
    client, store, token, _ = loaded
    posts = browser_form(True)
    assert len(posts) == 1
    assert posts[0]['url'] == '/field/edit-batch'
    payload = posts[0]['body']
    assert payload['expect_chip'] == 'token' and payload['seen_changes'] == 7
    assert payload['group'] == 'new' and len(payload['updates']) == 1
    before = deepcopy(store.state)
    payload['expect_chip'] = 'wrong'
    assert client.post('/field/edit-batch',json=payload).status_code == 409
    assert store.state == before
    payload['expect_chip'] = token
    response = client.post('/field/edit-batch',json=payload)
    assert response.json['ok'], response.json
    stored = store.merged['qubit_pairs']['q1-q2']['extras']['sm_coupler_model']
    assert C.coupler_model(stored)
    assert len({e.group_id for e in store.change_log}) == 1
    client.post('/undo')
    assert store.state == before
    # The existing-extras variant still writes the complete model in one update.
    assert browser_form()[0]['body']['updates'][0]['dot_path'].endswith('.extras.sm_coupler_model')


def test_table_map_blob_toggle(tmp_path):
    doc, wiring = _chip()
    pair = doc['qubit_pairs']['q1-2']
    pair['extras'] = {'sm_coupler_model': MODEL}
    pair['coupler'] = {'operations': {}}
    folder = tmp_path/'state'
    folder.mkdir()
    (folder/'state.json').write_text(json.dumps(doc),encoding='utf-8')
    (folder/'wiring.json').write_text(json.dumps(wiring),encoding='utf-8')
    client = _client(tmp_path,folder)
    html = client.get('/cz-plan',headers={'HX-Request':'true'}).text
    points = json.loads(re.search(r'id="cz-operating-points">(.*?)</script>',html,re.S)[1])
    ops = sorted({p['op'] for p in points})
    fixture = tmp_path/'fixture.json'
    fixture.write_text(json.dumps(dict(html=html, map=client.get('/cz-plan/map').json,
        data={op:client.get('/cz-plan/data',query_string={'op':op}).json for op in ops},
        solves={op:client.get('/cz-plan/solve',query_string={'pair':next(p['pair'] for p in points if p['op']==op),'op':op}).json for op in ops})),encoding='utf-8')
    result = subprocess.run(['node',str(Path(__file__).with_name('cz_coupler_selfcheck.cjs')),str(fixture)],capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr


def test_a1_exact_line_no_rows(tmp_path):
    doc, wiring = _chip()
    for q in doc['qubits'].values(): q.pop('z',None)
    for p in doc['qubit_pairs'].values(): p['macros'] = {}
    folder = tmp_path/'fixed'
    folder.mkdir()
    (folder/'state.json').write_text(json.dumps(doc),encoding='utf-8')
    (folder/'wiring.json').write_text(json.dumps(wiring),encoding='utf-8')
    html = _client(tmp_path,folder).get('/cz-plan',headers={'HX-Request':'true'}).text
    line = 'No flux CZ applies: qubits and couplers are fixed-frequency. Two-qubit gates on this chip are cross-resonance (and, where the environment has it, Stark-induced CZ).'
    assert html.count(line) == 1
    assert 'czp-row' not in html and '<table' not in html
    assert json.loads(re.search(r'id="cz-operating-points">(.*?)</script>',html,re.S)[1]) == []


PYTHON = Path(os.environ.get('CZ_OFFICIAL_PYTHON','C:/ProgramData/miniconda3/envs/qsm_official/python.exe'))
RIG = Path(os.environ.get('CZ_COUPLER_RIG', str(Path(os.environ.get('CZ_RIG_ROOT','D:/work_laptop/study/2026-10-29_qsm-v160-release/rigs'))/'sm_chip_24q')))


@pytest.mark.skipif(not PYTHON.is_file() or not (RIG/'state.json').is_file(),reason='official environment or reference folder absent')
def test_official_storage_roundtrip(tmp_path):
    script = """
import json, shutil, sys
from pathlib import Path
from quam_builder.architecture.superconducting.qpu.flux_tunable_quam import FluxTunableQuam as Quam
src, dst, encoded = sys.argv[1:]
shutil.copytree(src,dst)
base=Quam.load(dst).generate_config()
p=Path(dst)/'state.json'
doc=json.loads(p.read_text(encoding='utf-8'))
key=next(k for k,v in doc['qubit_pairs'].items() if isinstance(v,dict))
model=json.loads(encoded)
doc['qubit_pairs'][key].setdefault('extras',{})['sm_coupler_model']=model
p.write_text(json.dumps(doc),encoding='utf-8')
m=Quam.load(dst)
assert m.qubit_pairs[key].extras['sm_coupler_model']==model
assert m.generate_config()==base
m.save(dst)
m=Quam.load(dst)
assert m.qubit_pairs[key].extras['sm_coupler_model']==model
assert m.generate_config()==base
print('roundtrip passed')
"""
    result = subprocess.run([str(PYTHON),'-c',script,str(RIG),str(tmp_path/'copy'),json.dumps(MODEL)],capture_output=True,text=True,encoding='utf-8',timeout=180,env={**os.environ,'PYTHONUTF8':'1'})
    assert result.returncode == 0, result.stdout+result.stderr
