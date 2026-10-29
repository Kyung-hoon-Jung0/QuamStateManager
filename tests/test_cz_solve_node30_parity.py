"""S2-N30: executable official-code parity, never writes the reference folder."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from quam_state_manager.core import cz_gate_plan as C
from quam_state_manager.core.loader import QuamStore

PYTHON = Path(os.environ.get('CZ_OFFICIAL_PYTHON', 'C:/ProgramData/miniconda3/envs/qsm_official/python.exe'))
RIGS = Path(os.environ.get('CZ_RIG_ROOT', 'D:/work_laptop/study/2026-10-29_qsm-v160-release/rigs'))


@pytest.mark.skipif(not PYTHON.is_file() or not any((p / 'state.json').is_file() for p in RIGS.glob('sm_*')),
                    reason='official environment or reference folder absent')
def test_every_official_pair_and_macro(tmp_path):
    destination = tmp_path / 'copy'
    result = subprocess.run([str(PYTHON), str(Path(__file__).with_name('cz_node30_oracle.py')),
                             str(RIGS), str(destination)], capture_output=True, text=True,
                            encoding='utf-8', timeout=180, env={**os.environ, 'PYTHONUTF8': '1'})
    assert result.returncode == 0, result.stdout + result.stderr
    oracle = json.loads(result.stdout.splitlines()[-1])
    assert oracle['branches'] == ['high', 'low']
    assert len(oracle['answers']) > 1
    store = QuamStore(destination)
    rows, convs = C.operations(store.merged)
    for answer in oracle['answers']:
        proposal = C.solve(store.merged, answer['pair'], answer['op'], tier='quad', rows=rows, conversions=convs)
        assert proposal['ok'], (answer, proposal)
        assert proposal['mover'] == answer['mover']
        assert proposal['delta_hz'] == pytest.approx(answer['delta_hz'], rel=1e-9, abs=1e-6)
        assert proposal['amplitude_v'] == pytest.approx(answer['amplitude_v'], rel=1e-9)
