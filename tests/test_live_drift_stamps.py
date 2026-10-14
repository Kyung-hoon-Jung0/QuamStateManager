"""A same-stamp external save is eventually checked against the baseline."""
import json
import os

from tests.test_live_drift import live_folder, _app_client, _chip_state, _drift_count, _write_live
from quam_state_manager.web import routes


def test_same_stamp_save_is_rechecked_without_sleeps(tmp_path, live_folder, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(routes.time, 'monotonic', lambda: clock[0])
    client = _app_client(tmp_path)
    client.post('/load', data={'folder': str(live_folder)})
    assert _drift_count(client) == 0
    _write_live(live_folder, _chip_state(f_01=6.30e9))
    assert _drift_count(client) == 1
    path = live_folder / 'state.json'
    stat = path.stat()
    path.write_text(json.dumps(_chip_state(f_01=6.30e9, t1=8500.0), indent=2), encoding='utf-8')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size
    assert _drift_count(client) == 1
    clock[0] += 31
    assert _drift_count(client) == 2
    assert _drift_count(client) == 2
