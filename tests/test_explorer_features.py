"""docs/126 ④ — Json Tree quick patches, port-owner chips, ⧉ row copy.

Server side: ``routes._port_owner_map`` derives ``ports.* dot path → owner
label`` purely from the wiring document's ``#/ports/...`` pointers, and the
``/explorer`` render injects it as ``window._treePortOwners``. Client side is
pinned by ``tests/explorer_chips_selfcheck.cjs`` (real app.js under jsdom):
the chip bar's honesty rule (only terms that occur in the documents), the
SHARED custom-patch store with Live Edit, the owner chip on port nodes, and
the ⧉ copy action on every row.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.routes import _port_owner_map

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "explorer_chips_selfcheck.cjs"


class TestPortOwnerMap:
    def _wiring(self):
        return {
            "network": {"host": "1.2.3.4"},
            "wiring": {
                "qubits": {
                    "q2": {"z": {"opx_output": "#/ports/analog_outputs/con1/4/1"},
                           "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2",
                                  "digital_output": "#/ports/digital_outputs/con1/1/3"}},
                },
                "qubit_pairs": {
                    "q1-2": {"c": {"opx_output": "#/ports/analog_outputs/con1/5/2"}},
                },
                "twpas": {
                    "t1": {"pump": {"opx_output": "#/ports/mw_outputs/con1/2/1"}},
                },
            },
        }

    def test_owners_derived_from_pointers(self):
        m = _port_owner_map(self._wiring())
        assert m["ports.analog_outputs.con1.4.1"] == "q2 · z"
        assert m["ports.mw_outputs.con1.1.2"] == "q2 · xy"
        # every pointer under a channel counts (digital markers too)
        assert m["ports.digital_outputs.con1.1.3"] == "q2 · xy"
        # role short-names: 'c' is spelled out
        assert m["ports.analog_outputs.con1.5.2"] == "q1-2 · coupler"
        assert m["ports.mw_outputs.con1.2.1"] == "t1 · pump"

    def test_shared_port_lists_every_owner(self):
        w = self._wiring()
        w["wiring"]["qubits"]["q4"] = {
            "z": {"opx_output": "#/ports/analog_outputs/con1/4/1"}}
        m = _port_owner_map(w)
        assert m["ports.analog_outputs.con1.4.1"] == "q2 · z + q4 · z"

    def test_broken_shapes_never_crash(self):
        assert _port_owner_map(None) == {}
        assert _port_owner_map({}) == {}
        assert _port_owner_map({"wiring": {"qubits": {"q1": None,
                                                      "q2": {"z": "oops"},
                                                      "q3": {"z": {"opx_output": 7}}}}}) == {}

    def test_non_port_pointers_ignored(self):
        m = _port_owner_map({"wiring": {"qubits": {
            "q1": {"z": {"opx_output": "#/wiring/somewhere/else"}}}}})
        assert m == {}


class TestExplorerRoute:
    def test_port_owners_injected(self, tmp_path):
        from quam_state_manager.web.app import create_app
        state = {"qubits": {"q2": {"f_01": 5e9}},
                 "ports": {"analog_outputs": {"con1": {"4": {"1": {"offset": 0.0}}}}}}
        wiring = {"network": {"host": "1.1.1.1", "cluster_name": "t"},
                  "wiring": {"qubits": {"q2": {"z": {
                      "opx_output": "#/ports/analog_outputs/con1/4/1"}}}}}
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path)})
        html = c.get("/explorer", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "window._treePortOwners" in html
        assert "ports.analog_outputs.con1.4.1" in html
        assert "explorer-chipbar" in html
        assert "ExplorerChips.mount" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_explorer_chips_selfcheck():
    node = shutil.which("node")
    probe = subprocess.run([node, "-e", "require('jsdom')"],
                           capture_output=True, timeout=30, cwd=_ROOT)
    if probe.returncode != 0:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(_SELFCHECK)], capture_output=True, text=True,
                       encoding="utf-8", timeout=120, cwd=_ROOT)
    assert r.returncode == 0, (r.stdout or "") + "\n" + (r.stderr or "")
