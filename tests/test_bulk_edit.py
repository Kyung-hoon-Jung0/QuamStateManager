"""Tests for the Bulk-Edit panel (/bulk).

The panel is a denser entry surface over the SAME /field/edit-batch path the
inspector uses — so these assert the render (rows × high-churn columns, correct
per-cell dot_paths) and that a bulk-shaped multi-qubit batch round-trips.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _state() -> dict:
    def _q(qid, f01, ro_amp):
        return {
            "id": qid, "f_01": f01, "anharmonicity": -220e6,
            # high-value scalars the curated grid used to omit (feedback #1 quick-win)
            "f_12": None, "chi": -0.5e6,
            "T1": 2.4e-5, "T2ramsey": 2.2e-5, "T2echo": 2.0e-5,
            "phi0_voltage": 0.94, "phi0_current": 46.9,
            "gate_fidelity": {"averaged": 0.999},
            "grid_location": "2,4",
            "xy": {"RF_frequency": f01,
                   # op aliases (#./) like real QUAM — the bulk x180/x90 columns
                   # resolve through these, so they work on non-DragCosine chips too.
                   "operations": {"x180": "#./x180_DragCosine",
                                  "x90": "#./x90_DragCosine",
                                  "x180_DragCosine": {"amplitude": 0.11},
                                  "x90_DragCosine": {"amplitude": 0.055},
                                  "saturation": {"amplitude": 0.04}}},
            "resonator": {"f_01": 7.6e9, "RF_frequency": 7.6e9,
                          "time_of_flight": 376, "depletion_time": 3000,
                          "operations": {"readout": {"amplitude": ro_amp, "length": 1000, "threshold": -1e-4}}},
            "z": {"joint_offset": 0.05, "min_offset": 0.0, "settle_time": 64, "flux_point": "joint"},
        }
    return {"qubits": {"qA1": _q("qA1", 6.25e9, 0.042), "qA2": _q("qA2", 5.80e9, 0.050)},
            "qubit_pairs": {}, "active_qubit_names": ["qA1", "qA2"]}


def _wiring() -> dict:
    return {"wiring": {"qubits": {}}, "network": {"host": "10.1.1.1"}}


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    return c


class TestBulkRender:
    def test_renders_a_row_per_qubit(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert body.count('data-qubit="qA1"') == 1
        assert body.count('data-qubit="qA2"') == 1

    def test_cells_carry_the_templated_dot_paths(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        row = re.search(r'data-qubit="qA1"(.*?)</tr>', body, re.S).group(1)
        paths = set(re.findall(r'data-dot-path="([^"]+)"', row))
        assert "qubits.qA1.f_01" in paths
        assert "qubits.qA1.resonator.f_01" in paths           # "readout_frequency"
        # drive amplitude uses the op ALIAS (.x180.amplitude), not a hardcoded
        # _DragCosine suffix, so non-DragCosine chips work
        assert "qubits.qA1.xy.operations.x180.amplitude" in paths
        assert "qubits.qA1.resonator.operations.readout.amplitude" in paths
        assert "qubits.qA1.z.joint_offset" in paths
        # port columns present (delay, power, sampling rate, …)
        assert "qubits.qA1.xy.opx_output.delay" in paths
        assert "qubits.qA1.resonator.opx_input.downconverter_frequency" in paths

    def test_headers_carry_physical_units(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        # every column shows its stored unit in the header (mandatory)
        assert "(Hz)" in body and "(ns)" in body and "(dBm)" in body

    def test_port_columns_default_hidden(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        # a port column header carries the hidden class until opted-in via the menu
        m = re.search(r'<th[^>]*data-col-key="xy_delay"[^>]*>', body)
        assert m and "bulk-col-hidden" in m.group(0)

    def test_has_apply_controls(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="bulk-apply-all"' in body
        assert 'class="btn-xs bulk-row-apply"' in body

    def test_group_header_band(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        # a spanning group row labels each section over its columns
        assert "bulk-group-row" in body
        freq = re.search(r'<th[^>]*data-group="Frequencies"[^>]*>', body)
        assert freq and "bulk-group-head" in freq.group(0)
        # a visible section spans its columns and isn't collapsed
        assert "colspan=" in freq.group(0) and "bulk-col-hidden" not in freq.group(0)
        # an all-default-off port section's group head is collapsed until opted in
        port = re.search(r'<th[^>]*data-group="XY Port"[^>]*>', body)
        assert port and "bulk-col-hidden" in port.group(0)
        # the group boundary separator class is carried into the cells
        assert "bulk-col-group-start" in body

    def test_readability_controls_present(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        # letter-spacing slider + the help popover (r6 item 5: a <details>
        # dropdown at the ⓘ left of Properties — the boxed hint is gone)
        assert 'id="bulk-ls-slider"' in body and "BulkEdit.setLetterSpacing" in body
        assert 'id="bulk-help-pop"' in body and "bulk-help-menu" in body
        assert 'class="bulk-hint muted"' not in body
        # the popover sits BEFORE the Properties menu in the controls row
        assert body.index('id="bulk-help-pop"') < body.index('id="bulk-colvis-menu"')

    def test_cells_use_readable_mono_no_pointer_italic(self):
        css = (Path(__file__).resolve().parent.parent
               / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
        # .bulk-cell uses the app's good monospace + tabular figures (not generic monospace)
        assert "font-family: var(--font-mono); font-variant-numeric: tabular-nums;" in css
        # the pointer-cell italic that made amp numbers look "different" is gone
        assert ".bulk-td-pointer .bulk-cell { font-style: italic" not in css

    def test_no_context_warns(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        body = app.test_client().get("/bulk").get_data(as_text=True)
        assert "No chip loaded" in body

    def test_plain_and_comma_integer_both_apply_to_same_value(self, client):
        # Bug report: "24000" (no comma) failed while "24,000" worked. Both forms
        # must parse + coerce to the same stored value/type, for int AND float fields.
        # f_01 is float; readout_length is int.
        for path, plain, grouped, expect in [
            ("qubits.qA1.f_01", "24000", "24,000", 24000.0),
            ("qubits.qA1.resonator.operations.readout.length", "24000", "24,000", 24000),
        ]:
            for form in (plain, grouped):
                jb = client.post("/field/edit-batch",
                                 json={"updates": [{"dot_path": path, "value": form}]}).get_json()
                assert jb["ok"] is True, f"{path} <- {form!r} failed: {jb}"
                got = client.get(f"/field/peek?dot_path={path}").get_json()["values"][path]
                assert got == expect and type(got) is type(expect), \
                    f"{path} <- {form!r}: got {got!r}, want {expect!r}"


class TestBulkCompletenessPhase0:
    """Feedback #1 quick-win: high-value per-qubit scalars the curated grid used to
    omit are now columns. time_of_flight (the user's flagship complaint) is default-ON;
    the rest (f_12, chi, depletion_time, z extras, T1/T2*, gate fidelity, phi0, grid loc)
    are opt-in via the Properties menu. Same _build_bulk_cell + /field/edit-batch path."""

    def test_time_of_flight_is_a_default_on_editable_column(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        th = re.search(r'<th[^>]*data-col-key="time_of_flight"[^>]*>', body)
        assert th, "time_of_flight column header must render"
        assert "bulk-col-hidden" not in th.group(0), "time_of_flight must be default-ON"
        for qid in ("qA1", "qA2"):
            row = re.search(rf'data-qubit="{qid}"(.*?)</tr>', body, re.S).group(1)
            assert f"qubits.{qid}.resonator.time_of_flight" in row

    def test_new_optin_scalar_columns_render_but_hidden(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        for key in ("f_12", "chi", "depletion_time", "z_settle_time", "z_flux_point",
                    "T1", "T2ramsey", "T2echo", "gate_fidelity_avg", "phi0_voltage", "grid_location"):
            th = re.search(rf'<th[^>]*data-col-key="{key}"[^>]*>', body)
            assert th, f"{key} column must render"
            assert "bulk-col-hidden" in th.group(0), f"{key} must be opt-in (default hidden)"

    def test_time_of_flight_round_trips_through_edit_batch(self, client):
        jb = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.resonator.time_of_flight", "value": "284"},
        ]}).get_json()
        assert jb["ok"] is True and jb["results"][0]["applied"] is True
        assert jb["results"][0]["new_value"] == 284
        peek = client.get("/field/peek?dot_path=qubits.qA1.resonator.time_of_flight").get_json()
        assert peek["values"]["qubits.qA1.resonator.time_of_flight"] == 284

    def test_coherence_scalar_round_trips(self, client):
        # T1 is a float in seconds — a representative opt-in scalar that was unreachable
        jb = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA2.T1", "value": "3.1e-05"},
        ]}).get_json()
        assert jb["ok"] is True
        peek = client.get("/field/peek?dot_path=qubits.qA2.T1").get_json()
        assert peek["values"]["qubits.qA2.T1"] == 3.1e-05


class TestBulkLOBand:
    """LO/band metadata on port cells (the state→wiring→ports.* double-pointer chain)."""

    @pytest.fixture
    def lo_client(self, tmp_path: Path):
        def _q(qid, band, freq, out_port):
            return {"id": qid, "f_01": freq,
                    "xy": {"opx_output": "#/wiring/qubits/%s/xy/opx_output" % qid,
                           "operations": {"x180": "#./x180_DragCosine",
                                          "x180_DragCosine": {"amplitude": 0.1}}}}
        state = {"qubits": {"qA1": _q("qA1", 2, 5.05e9, 2), "qA2": _q("qA2", 2, 5.8e9, 3)},
                 "qubit_pairs": {},
                 # ports tree (resolved targets); qA1.xy→port2, qA2.xy→port3 (an LO pair)
                 "ports": {"mw_outputs": {"con1": {"1": {
                     "2": {"band": 2, "upconverter_frequency": 5.05e9},
                     "3": {"band": 2, "upconverter_frequency": 5.8e9}}}}}}
        wiring = {"wiring": {"qubits": {
            "qA1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"}},
            "qA2": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/3"}}}},
            "network": {"host": "1.1.1.1"}}
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path)})
        return c

    def test_lo_peer_and_band_on_band_cell(self, lo_client):
        body = lo_client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        row = re.search(r'data-qubit="qA1"(.*?)</tr>', body, re.S).group(1)
        cell = re.search(r'<input[^>]*data-dot-path="qubits.qA1.xy.opx_output.band"[^>]*>', row).group(0)
        assert 'data-lo-field="band"' in cell
        assert 'data-peer="qA2"' in cell          # Out2 ↔ Out3 LO pair → qA2
        assert 'data-peer-band="2"' in cell
        assert 'data-band="2"' in cell
        assert "Shares the LO with qA2 (band 2)" in row


class TestBulkLinkable:
    """`data-linkable` marks cells that resolve to a real shared WRITABLE leaf —
    so qubits on one physical port link + mirror + dedup. It is keyed on
    resolvability ALONE (value-independent), INCLUDING a leaf stored as null —
    the regression the re-audit caught: gain_db=null must still link, else the
    mirror gate (links nothing) and the dedup gate (still collapses by
    data-resolved) disagree and a second typed value is silently dropped. A
    dead-ended optional leaf (resolved_path falls back to the bare parent port
    path, shared by several distinct unset fields) must NOT be linkable."""

    @pytest.fixture
    def shared_in_client(self, tmp_path: Path):
        def _q(qid, f01):
            return {"id": qid, "f_01": f01,
                    "resonator": {"f_01": 7.6e9,
                                  "opx_input": "#/wiring/qubits/%s/resonator/opx_input" % qid,
                                  "operations": {"readout": {"amplitude": 0.04}}}}
        # qA1 + qA2 share ONE physical RO-in port: downconverter_frequency carries a
        # value; gain_db is present-but-NULL; sampling_rate + band are ABSENT (dead-end
        # optional leaves → resolve_field_target falls back to the parent port path).
        state = {"qubits": {"qA1": _q("qA1", 6.25e9), "qA2": _q("qA2", 5.8e9)},
                 "qubit_pairs": {},
                 "ports": {"mw_inputs": {"con1": {"1": {"1": {
                     "downconverter_frequency": 7.5e9, "gain_db": None}}}}}}
        wiring = {"wiring": {"qubits": {
            "qA1": {"resonator": {"opx_input": "#/ports/mw_inputs/con1/1/1"}},
            "qA2": {"resonator": {"opx_input": "#/ports/mw_inputs/con1/1/1"}}}},
            "network": {"host": "1.1.1.1"}}
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path)})
        return c

    @staticmethod
    def _cell(body, qid, dot_path):
        row = re.search(rf'data-qubit="{qid}"(.*?)</tr>', body, re.S).group(1)
        return re.search(rf'<input[^>]*data-dot-path="{re.escape(dot_path)}"[^>]*>', row).group(0)

    def test_valued_shared_leaf_links(self, shared_in_client):
        body = shared_in_client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        for qid in ("qA1", "qA2"):
            cell = self._cell(body, qid, f"qubits.{qid}.resonator.opx_input.downconverter_frequency")
            assert 'data-linkable="1"' in cell
            assert 'data-resolved="ports.mw_inputs.con1.1.1.downconverter_frequency"' in cell

    def test_null_valued_shared_leaf_still_links(self, shared_in_client):
        # THE REGRESSION: gain_db is shared + writable but stored null. It must
        # still carry data-linkable so it mirrors + dedups like its siblings —
        # otherwise typing distinct values into two qubits' gain silently drops one.
        body = shared_in_client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        for qid in ("qA1", "qA2"):
            cell = self._cell(body, qid, f"qubits.{qid}.resonator.opx_input.gain_db")
            assert 'data-linkable="1"' in cell, f"{qid} null gain_db must stay linkable"
            assert 'data-resolved="ports.mw_inputs.con1.1.1.gain_db"' in cell
            assert 'placeholder="not set"' in cell        # still styled as unset (value-aware)

    def test_deadend_optional_leaves_not_linkable(self, shared_in_client):
        # sampling_rate + band are absent from the port node → both dead-end onto
        # the SAME bare parent path. They must NOT be linkable, so they post
        # independently (no silent collapse of two genuinely-distinct fields).
        body = shared_in_client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        samp = self._cell(body, "qA1", "qubits.qA1.resonator.opx_input.sampling_rate")
        band = self._cell(body, "qA1", "qubits.qA1.resonator.opx_input.band")
        assert 'data-linkable="1"' not in samp
        assert 'data-linkable="1"' not in band
        # the trap the gate guards: two distinct fields collapsing onto one path
        rs = re.search(r'data-resolved="([^"]*)"', samp).group(1)
        rb = re.search(r'data-resolved="([^"]*)"', band).group(1)
        assert rs == rb == "ports.mw_inputs.con1.1.1"


class TestBulkApply:
    def _paths(self, client, qid):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        row = re.search(rf'data-qubit="{qid}"(.*?)</tr>', body, re.S).group(1)
        return re.findall(r'data-dot-path="([^"]+)"', row)

    def test_multi_qubit_batch_applies(self, client):
        # what Apply-all POSTs: dirty cells across qubits, using rendered dot-paths
        updates = [
            {"dot_path": "qubits.qA1.f_01", "value": "5.11e9"},
            {"dot_path": "qubits.qA2.resonator.operations.readout.amplitude", "value": "0.066"},
        ]
        jb = client.post("/field/edit-batch", json={"updates": updates}).get_json()
        assert jb["ok"] is True
        assert all(r["applied"] for r in jb["results"])
        peek = client.get("/field/peek?dot_path=qubits.qA1.f_01"
                          "&dot_path=qubits.qA2.resonator.operations.readout.amplitude").get_json()
        assert peek["values"]["qubits.qA1.f_01"] == 5.11e9
        assert peek["values"]["qubits.qA2.resonator.operations.readout.amplitude"] == 0.066

    def test_comma_grouped_input_applies_and_echoes(self, client):
        # Bulk cells display comma-grouped digits; submitting that string must
        # parse back to the number, and the result echoes the committed value.
        jb = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.f_01", "value": "5,123,456,789.5"},
        ]}).get_json()
        assert jb["ok"] is True
        r0 = jb["results"][0]
        assert r0["applied"] is True
        assert r0["new_value"] == 5123456789.5
        assert r0["display"] == "5,123,456,789.5"          # trusted re-render string
        assert r0["resolved_path"] == "qubits.qA1.f_01"
        # response carries the modified delta keyed by resolved path
        assert any(m["resolved_path"] == "qubits.qA1.f_01" for m in jb["modified"])
        peek = client.get("/field/peek?dot_path=qubits.qA1.f_01").get_json()
        assert peek["values"]["qubits.qA1.f_01"] == 5123456789.5

    def test_pointer_aliased_modified_marker_matches_resolved_path(self, client):
        # Editing a pointer-aliased column (x180 amp → x180_DragCosine) records the
        # RESOLVED path in the change log; the bulk render must mark the cell modified
        # by matching that resolved path (the old alias-path match silently missed).
        client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.xy.operations.x180.amplitude", "value": "0.2"},
        ]})
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        row = re.search(r'data-qubit="qA1"(.*?)</tr>', body, re.S).group(1)
        cell = re.search(r'data-dot-path="qubits.qA1.xy.operations.x180.amplitude"[^>]*'
                         r'class="[^"]*"|class="([^"]*)"[^>]*data-dot-path="qubits.qA1.xy.operations.x180.amplitude"', row)
        # simplest robust check: the modified class is present somewhere on that cell's input
        seg = re.search(r'(<input[^>]*data-dot-path="qubits.qA1.xy.operations.x180.amplitude"[^>]*>)', row)
        assert seg and "bulk-cell-modified" in seg.group(1)

    def test_bad_cell_rolls_back_its_batch(self, client):
        updates = [
            {"dot_path": "qubits.qA1.f_01", "value": "5.5e9"},
            {"dot_path": "qubits.qNOPE.f_01", "value": "9e9"},
        ]
        jb = client.post("/field/edit-batch", json={"updates": updates}).get_json()
        assert jb["ok"] is False
        # the valid edit must NOT persist (atomic rollback)
        peek = client.get("/field/peek?dot_path=qubits.qA1.f_01").get_json()
        assert peek["values"]["qubits.qA1.f_01"] == 6.25e9
