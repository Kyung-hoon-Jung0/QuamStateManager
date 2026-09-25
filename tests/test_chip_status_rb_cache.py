"""Chip Status RB renders must not sweep the archive or reread unchanged fits."""
import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from quam_state_manager.core import rb_gate_fidelity
from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web import routes


@pytest.fixture
def rb_render(tmp_path, monkeypatch):
    with routes._RB_CACHE_LOCK:
        monkeypatch.setattr(routes, "_RB_RUN_FOLDERS", {})
        monkeypatch.setattr(routes, "_RB_DERIVED_VALUES", {})
    run = tmp_path / "2026-09-25" / "#7_standard_rb_120000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "id": 7, "metadata": {"name": "standard_rb", "status": "successful"},
        "created_at": "2026-09-25T12:00:00", "data": {}}), encoding="utf-8")
    data = run / "data.json"

    def write(value):
        data.write_text(json.dumps({"fit_results": {"q1-2": {
            "average_gate_fidelity": value, "average_gates_per_clifford": 5.371}}}),
            encoding="utf-8")

    write(0.99)
    store = DatasetStore(tmp_path)
    real_rescan = DatasetStore.rescan_if_stale
    rescans = Mock(side_effect=lambda self, *a, **kw: real_rescan(self, *a, **kw))
    monkeypatch.setattr(DatasetStore, "rescan_if_stale", rescans)

    def active(*, fast=False, rescan=True):
        assert fast
        if rescan:
            DatasetStore.rescan_if_stale(store)
        return [{"store": store}]

    active_spy = Mock(side_effect=active)
    monkeypatch.setattr(routes, "_active_dataset_stores", active_spy)
    reader = Mock(wraps=rb_gate_fidelity.from_run_folder)
    monkeypatch.setattr(rb_gate_fidelity, "from_run_folder", reader)
    plain = {"edges": [{"pair_id": "q1-2", "gate_fidelities": [
        {"level": "clifford", "load_id": 7, "value": 0.97}]}]}
    engine = SimpleNamespace(get_topology=lambda: plain)

    def render():
        return routes._topology_with_derived_rb(engine)["edges"][0]["gate_fidelities"][0]

    return SimpleNamespace(render=render, plain=plain, write=write, data=data,
                           rescans=rescans, reader=reader, active=active_spy)


def test_second_render_neither_rescans_nor_reads_data(rb_render):
    r = rb_render
    original = copy.deepcopy(r.plain)
    first = r.render()
    assert first["derived_gate_fidelity"] == 0.99
    assert r.reader.call_count == 1
    r.rescans.reset_mock()
    r.reader.reset_mock()
    r.active.reset_mock()
    assert r.render() == first
    r.rescans.assert_not_called()
    r.reader.assert_not_called()
    r.active.assert_not_called()
    assert r.plain == original


def test_changed_data_invalidates_derived_values(rb_render):
    r = rb_render
    assert r.render()["derived_gate_fidelity"] == 0.99
    # Establish that this is a cached render before testing invalidation.
    assert r.render()["derived_gate_fidelity"] == 0.99
    assert r.reader.call_count == 1
    stamp = r.data.stat()
    r.write(0.987654)
    os.utime(r.data, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
    assert r.render()["derived_gate_fidelity"] == 0.987654
    assert r.reader.call_count == 2
    assert "derived_gate_fidelity" not in r.plain["edges"][0]["gate_fidelities"][0]


def test_unknown_ids_share_one_sweep_and_remember_misses(rb_render, monkeypatch):
    r = rb_render
    rows = r.plain["edges"][0]["gate_fidelities"]
    rows[0]["load_id"] = 999
    rows.append({"level": "clifford", "load_id": 1000, "value": 0.96})
    now = [100.0]
    monkeypatch.setattr(routes.time, "monotonic", lambda: now[0])
    assert "derived_gate_fidelity" not in r.render()
    assert r.rescans.call_count == 1
    now[0] += 59
    assert "derived_gate_fidelity" not in r.render()
    assert r.rescans.call_count == 1
    now[0] += 2
    assert "derived_gate_fidelity" not in r.render()
    assert r.rescans.call_count == 2
    r.reader.assert_not_called()
