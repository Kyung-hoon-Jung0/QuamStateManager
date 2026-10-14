"""Noncanonical data and saved figures keep a usable Interactive tab."""
import json

import pytest

from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web.app import create_app
from quam_state_manager.web import routes


@pytest.mark.parametrize('saved_file,snapshot', [('ds_raw_qA1.h5', True), ('arrays.npz', True),
                                                ('arrays.npz', False)])
def test_saved_input_gate_survives_store_restart(tmp_path, monkeypatch, saved_file, snapshot):
    root, cache = tmp_path / 'data', tmp_path / 'cache'
    folder = root / '2026-08-01' / '#1_sample_010000'
    folder.mkdir(parents=True)
    (folder / 'node.json').write_text(json.dumps({'metadata': {'name': 'sample'}, 'id': 1}))
    (folder / 'data.json').write_text(json.dumps({'figures': {'response': './figures.response.png'}}))
    (folder / saved_file).write_bytes(b'data')
    (folder / 'figures.response.png').write_bytes(b'saved image')
    if snapshot:
        (folder / 'quam_state').mkdir()
    store = DatasetStore(root, cache_dir=cache)
    assert store.flush_store_cache()
    store = DatasetStore(root, cache_dir=cache)
    assert store.cache_hit_runs == 1
    run = store.get_run(1)
    assert run.get('has_interactive') is True
    assert not run['has_ds_raw'] and not run['has_ds_fit']
    monkeypatch.setattr(routes, '_resolve_run', lambda uid: (store, 1, ''))
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    client = app.test_client()
    detail = client.get('/dataset/sample').get_data(as_text=True)
    assert 'data-ds-tab="interactive"' in detail
    assert 'id="ds-interactive-container"' in detail
    menu = client.get('/dataset/sample/interactive').get_data(as_text=True)
    assert 'response' in menu
    assert 'saved' in menu.lower()


def test_unavailable_replay_keeps_its_capability_reason(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from quam_state_manager.core.interactive_plots import replot
    folder = tmp_path / 'run'
    folder.mkdir()
    (folder / 'data.json').write_text(json.dumps({'figures': {'response':'./figures.response.png'}}))
    (folder / 'figures.response.png').write_bytes(b'saved image')
    run = SimpleNamespace(folder_path=folder, experiment_name='sample', qubits=[], qubit_pairs=[])
    store = SimpleNamespace(runs={1:run})
    monkeypatch.setattr(routes, '_resolve_run', lambda uid:(store,1,''))
    reason = 'Run has no state snapshot to rebuild the device.'
    monkeypatch.setattr(replot, 'replot_capability', lambda run, ip:
                        dict(available=False, reason=reason, util='sample', env='offline'))
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    body = app.test_client().get('/dataset/sample/interactive').get_data(as_text=True)
    assert reason in body
    assert 'response' in body and '(static)' in body
