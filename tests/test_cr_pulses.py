"""CR/ZZ pair-channel pulse enumeration + routes (docs/54, Phase 3).

The real CR drive pulses live at ``qubit_pairs.<p>.cross_resonance.operations.*``
— these tests pin that they surface on the Pulses page with full row/detail/
synth/used_by/rename/create support, that the target-xy cancellation stubs'
pointers gain rename-impact disclosure, and that flux-slot creation into a
CR-shaped macro is refused. CZ chips must see zero new rows (regression pin
lives in test_pulse_index/test_pulses_routes staying green unmodified).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from quam_state_manager.core.pulse_index import (
    PAIR_PULSE_CHANNELS,
    _op_path_of,
    build_reverse_pointer_index,
    list_pulses,
    used_by,
)
from quam_state_manager.web.app import create_app

sys.path.insert(0, str(Path(__file__).parent))
from cr_fixtures import make_cz_reference, make_flavor_b, write_folder  # noqa: E402

CR_OP = "qubit_pairs.q0-1.cross_resonance.operations.square"
STUB = "qubits.q1.xy.operations.cr_square_q0-1"


# ── core enumeration ─────────────────────────────────────────────────────────

class TestEnumeration:
    def test_cr_ops_enumerated(self):
        state, wiring = make_flavor_b()
        merged = {**state, **wiring}
        rows = {r["path"]: r for r in list_pulses(merged)}
        assert CR_OP in rows
        row = rows[CR_OP]
        assert row["owner_kind"] == "pair" and row["owner"] == "q0-1"
        assert row["channel"] == "cross_resonance"
        assert row["known"] is True                # SquarePulse — catalog class
        assert row["class_match"] == "exact"
        # 4 directed pairs × 2 ops each
        cr_rows = [r for r in rows.values() if r["channel"] == "cross_resonance"]
        assert len(cr_rows) == 8

    def test_zz_ops_enumerated_under_real_key(self):
        state, wiring = make_flavor_b(with_zz=True)
        merged = {**state, **wiring}
        paths = {r["path"] for r in list_pulses(merged)}
        assert "qubit_pairs.q0-1.zz_drive.operations.square" in paths
        # xy_detuned is a QUBIT channel on the target
        assert "qubits.q1.xy_detuned.operations.zz_square_q0-1" in paths

    def test_null_channels_add_nothing(self):
        state, wiring = make_flavor_b()        # zz_drive explicit null
        merged = {**state, **wiring}
        assert not any(".zz_drive." in r["path"] or ".zz." in r["path"]
                       for r in list_pulses(merged))

    def test_cz_chip_rows_unchanged(self):
        state, wiring = make_cz_reference()
        merged = {**state, **wiring}
        rows = list_pulses(merged)
        assert not any(r["channel"] in PAIR_PULSE_CHANNELS for r in rows)

    def test_op_path_of_maps_pair_channels(self):
        assert _op_path_of(CR_OP + ".length") == CR_OP
        assert _op_path_of("qubit_pairs.p.zz.operations.square.amplitude") == \
            "qubit_pairs.p.zz.operations.square"
        assert _op_path_of("qubit_pairs.p.confusion.0.1") is None

    def test_used_by_links_cancel_stubs(self):
        state, wiring = make_flavor_b()
        merged = {**state, **wiring}
        idx = build_reverse_pointer_index(merged)
        referrers = used_by(merged, CR_OP, idx)
        assert f"{STUB}.length" in referrers


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def cr_client(tmp_path):
    folder = write_folder(tmp_path / "chip", *make_flavor_b())
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(folder)})
    return client


class TestCrPulseRoutes:
    def test_page_lists_cr_rows_and_tabs(self, cr_client):
        html = cr_client.get("/pulses", headers={"HX-Request": "true"}).data.decode()
        assert "Pair CR/ZZ" in html
        assert "Pair flux" not in html            # no flux-slot rows on this chip
        rows = cr_client.get("/pulses?rows=1&channel=pair_drive").data.decode()
        assert "cross_resonance" in rows and "q0-1" in rows

    def test_detail_and_synth(self, cr_client):
        html = cr_client.get(f"/pulse/detail?path={CR_OP}").data.decode()
        assert "square" in html and "SquarePulse" in html

    def test_rename_retargets_cancel_stub(self, cr_client):
        resp = cr_client.post("/api/pulse/rename", data={
            "path": CR_OP, "new_name": "square_v2", "retarget": "1"})
        assert resp.status_code == 200
        assert "re-pointed" in resp.data.decode()
        # the renamed op renders, and the cancel stub still resolves through
        # its (re-pointed) length pointer — used_by lists it on the new path
        new_path = "qubit_pairs.q0-1.cross_resonance.operations.square_v2"
        detail = cr_client.get(f"/pulse/detail?path={new_path}").data.decode()
        assert "cr_square_q0-1" in detail          # stub shown as referrer

    def test_duplicate_pair_channel_op(self, cr_client):
        resp = cr_client.post("/api/pulse/duplicate", data={
            "path": CR_OP, "new_name": "square_copy"})
        assert resp.status_code == 200
        detail = cr_client.get(
            "/pulse/detail?path=qubit_pairs.q0-1.cross_resonance.operations.square_copy")
        assert detail.status_code == 200

    def test_gate_slot_rename_still_refused(self, cr_client):
        resp = cr_client.post("/api/pulse/rename", data={
            "path": "qubit_pairs.q0-1.macros.cr", "new_name": "x"})
        assert resp.status_code == 400

    def test_create_on_pair_channel(self, cr_client):
        resp = cr_client.post("/api/pulse/create", data={
            "pulse_type": "SquarePulse", "target_kind": "pair_channel",
            "pc_pair": "q0-1", "pc_channel": "cross_resonance",
            "op_name": "probe", "length": "100", "amplitude": "0.5",
            "axis_angle": "0",
        })
        assert resp.status_code == 200, resp.data.decode()[:300]
        detail = cr_client.get(
            "/pulse/detail?path=qubit_pairs.q0-1.cross_resonance.operations.probe")
        assert detail.status_code == 200

    def test_create_never_fabricates_channel(self, cr_client):
        # zz_drive is null on this chip — creating into it must refuse.
        resp = cr_client.post("/api/pulse/create", data={
            "pulse_type": "SquarePulse", "target_kind": "pair_channel",
            "pc_pair": "q0-1", "pc_channel": "zz_drive",
            "op_name": "probe", "length": "100", "amplitude": "0.5",
            "axis_angle": "0",
        })
        assert resp.status_code == 400
        assert b"cannot hold pulses yet" in resp.data

    def test_flux_slot_into_cr_macro_refused(self, cr_client):
        resp = cr_client.post("/api/pulse/create", data={
            "pulse_type": "SquarePulse", "target_kind": "pair",
            "pair": "q0-1", "gate": "cr", "slot": "flux_pulse_qubit",
            "length": "100", "amplitude": "0.5", "axis_angle": "0",
        })
        assert resp.status_code == 409
        assert b"CR/Stark" in resp.data

    def test_delete_cr_op_discloses_stub_referrers(self, cr_client):
        resp = cr_client.post("/api/pulse/delete", data={"path": CR_OP})
        assert resp.status_code == 409             # stub pointers reference it
        assert b"cr_square_q0-1" in resp.data
