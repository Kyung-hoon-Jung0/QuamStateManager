"""Seam proof uses a run's own patches, with a bounded node-read cache."""

import json

from quam_state_manager.core import hub_build, hub_lanes
from tests.test_hub_folder_view import _inline, lab  # noqa: F401
from tests.test_hub_drawer import chip_state


def test_parallel_folder_masking_keeps_the_runs_own_patch_proof(lab, _inline):
    lab.folder("labA", chip_state())
    lab.write("labA", "qubits.qA1.f_01", 5.01e9)
    lab.folder("labB", chip_state())
    lab.write("labB", "qubits.qA1.f_01", 5.2e9)
    folder = lab.run(chip_state(f01=5.2e9))
    path = folder / "node.json"
    node = json.loads(path.read_text(encoding="utf-8"))
    node["patches"] = [{"op": "replace", "path": "/qubits/qA1/f_01", "value": 5.2e9}]
    path.write_text(json.dumps(node), encoding="utf-8")
    lab.sync("labA")
    points = lab.points("labA", "qubits.qA1.f_01")
    assert points[-1]["value"] == 5.2e9 and points[-1]["provenance"] == "run_proven"


def test_a_seam_reads_its_node_once_and_rereads_a_changed_file(tmp_path, monkeypatch):
    path = tmp_path / "node.json"
    path.write_text('{"patches": []}', encoding="utf-8")
    fact = dict(eid=1, state_hash="a", shape_hash="b", chash="c", error=None, state_ref=str(tmp_path))
    hub_lanes.clear_caches()
    calls, read = [], hub_build.read_node
    monkeypatch.setattr(hub_build, "read_node", lambda p: calls.append(p) or read(p))
    assert hub_lanes._seam_node("ledger", fact)["patches"] == []
    assert hub_lanes._seam_node("ledger", fact)["patches"] == []
    assert len(calls) == 1
    path.write_text('{"patches": [{"op": "remove", "path": "/v"}]}', encoding="utf-8")
    assert hub_lanes._seam_node("ledger", fact)["patches"] == [{"op": "remove", "path": "/v"}]
    assert len(calls) == 2
    path.write_text("broken", encoding="utf-8")
    assert hub_lanes._seam_node("ledger", fact) is None
