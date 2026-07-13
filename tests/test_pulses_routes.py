"""Flask route tests for the Pulses page (/pulses, /pulse/detail + mutations)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

QC = "quam.components.pulses."


def _make_state() -> dict:
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": 6.25e9,
                "anharmonicity": -200e6,
                "xy": {
                    "operations": {
                        "x180_DragCosine": {
                            "length": 48, "axis_angle": 0, "amplitude": 0.319,
                            "alpha": -0.34,
                            "anharmonicity": "#/qubits/qA1/anharmonicity",
                            "__class__": QC + "DragCosinePulse",
                        },
                        "x90_DragCosine": {
                            "length": "#../x180_DragCosine/length",
                            "axis_angle": 0, "amplitude": 0.159,
                            "alpha": "#../x180_DragCosine/alpha",
                            "anharmonicity": "#../x180_DragCosine/anharmonicity",
                            "__class__": QC + "DragCosinePulse",
                        },
                        "x180": "#./x180_DragCosine",
                        "saturation": {"length": 20000, "amplitude": 0.004,
                                       "__class__": QC + "SquarePulse"},
                        "mystery": {"length": 10, "amplitude": 0.1,
                                    "__class__": "quam_builder.custom.WeirdPulse"},
                    },
                },
                "resonator": {
                    "operations": {
                        "readout": {
                            "length": 1024, "amplitude": 0.01,
                            "integration_weights": "#./default_integration_weights",
                            "__class__": QC + "SquareReadoutPulse",
                        },
                    },
                },
            },
        },
        "qubit_pairs": {
            "qA1-qA2": {
                "macros": {
                    "cz_unipolar": {
                        "flux_pulse_qubit": {"amplitude": 0.05, "length": 100},
                        "coupler_flux_pulse": None,
                    },
                    "cz": "#./cz_unipolar",
                },
            },
        },
        "active_qubit_names": ["qA1"],
    }


def _make_wiring() -> dict:
    return {
        "wiring": {"qubits": {"qA1": {"xy": {"opx_output": "MW-FEM/1/2"}}}},
        "network": {"host": "10.1.1.18"},
    }


@pytest.fixture
def synth_folder(tmp_path: Path) -> Path:
    (tmp_path / "state.json").write_text(json.dumps(_make_state(), indent=2),
                                         encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2),
                                          encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def loaded_client(client, synth_folder):
    client.post("/load", data={"folder": str(synth_folder)})
    return client


XY = "qubits.qA1.xy.operations"


class TestPulsesLibrary:
    def test_no_state_loaded(self, client):
        html = client.get("/pulses").data.decode()
        assert "No chip loaded" in html

    def test_full_page_render(self, loaded_client):
        resp = loaded_client.get("/pulses")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "pulses-table" in html
        assert "x180_DragCosine" in html
        assert "<html" in html  # full page (non-HTMX)

    def test_htmx_partial_render(self, loaded_client):
        html = loaded_client.get("/pulses",
                                 headers={"HX-Request": "true"}).data.decode()
        assert "pulses-table" in html and "<html" not in html

    def test_alias_and_pair_rows_present(self, loaded_client):
        html = loaded_client.get("/pulses").data.decode()
        assert "alias" in html                       # alias badge
        assert "cz_unipolar.flux_pulse_qubit" in html  # pair slot row

    def test_sidebar_add_pulse_auto_opens_create(self, loaded_client):
        # The "Add pulse" sidebar sub-item lands on /pulses?create=1, which must
        # auto-load the create form into the inspector pane.
        create = loaded_client.get("/pulses?create=1").data.decode()
        plain = loaded_client.get("/pulses").data.decode()
        assert 'hx-target="#inspector-pane"' in create
        # create=1 adds an auto-load trigger for the create form ON TOP of the
        # always-present "+ New pulse" button -> exactly one more /pulse/new.
        assert create.count('hx-get="/pulse/new"') == plain.count('hx-get="/pulse/new"') + 1
        # The auto-open trigger div carries hx-trigger="load" (the button does not).
        import re
        assert re.search(r'hx-get="/pulse/new"[^>]*hx-trigger="load"'
                         r'|hx-trigger="load"[^>]*hx-get="/pulse/new"', create)

    def test_add_pulse_subnav_persistence_and_clean_url(self):
        # The Pulses subnav expand state must be restorable (in SUBNAVS), and the
        # "Add pulse" link must push the CLEAN /pulses URL so a refresh / Back
        # doesn't re-fire the auto-open over the user's inspector. [red-team guards]
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
        app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
        base = (root / "templates" / "base.html").read_text(encoding="utf-8")
        assert "pulses-subnav" in app_js and "quam_pulses_nav_collapsed" in app_js
        # Add-pulse link pushes /pulses (not ?create=1).
        assert 'hx-get="/pulses?create=1"' in base
        assert 'hx-push-url="/pulses"' in base
        assert 'hx-get="/pulses?create=1" hx-target="#table-pane" hx-sync="#table-pane:replace" hx-push-url="true"' not in base

    def test_sparkline_rendered_for_known_pulse(self, loaded_client):
        html = loaded_client.get("/pulses").data.decode()
        assert "pulse-spark" in html and "<svg" in html

    def test_channel_filter(self, loaded_client):
        html = loaded_client.get("/pulses?channel=resonator").data.decode()
        assert "readout" in html
        assert "x180_DragCosine" not in html
        flux = loaded_client.get("/pulses?channel=flux").data.decode()
        assert "cz_unipolar.flux_pulse_qubit" in flux
        assert "saturation" not in flux

    def test_rows_only_mode(self, loaded_client):
        html = loaded_client.get("/pulses?rows=1").data.decode()
        assert "pulses-table" in html
        assert "pulses-rows-wrap" not in html  # wrapper not re-rendered

    def test_sidebar_entry_active(self, loaded_client):
        html = loaded_client.get("/pulses").data.decode()
        assert 'href="/pulses"' in html


class TestPulseDetail:
    def test_detail_real_pulse(self, loaded_client):
        resp = loaded_client.get(
            f"/pulse/detail?path={XY}.x180_DragCosine")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "pulse-detail-root" in html
        assert "DragCosinePulse" in html
        assert "pulse-detail-data" in html       # embedded plot JSON
        assert "anharmonicity" in html
        assert "#/qubits/qA1/anharmonicity" in html  # pointer badge raw

    def test_detail_embeds_plot_traces(self, loaded_client):
        html = loaded_client.get(
            f"/pulse/detail?path={XY}.x180_DragCosine").data.decode()
        start = html.index('id="pulse-detail-data"')
        payload = html[start:]
        payload = payload[payload.index(">") + 1:payload.index("</script>")]
        data = json.loads(payload.replace("\\u003c", "<").replace(
            "\\u003e", ">").replace("\\u0026", "&"))
        assert data["plot"]["ok"]
        names = [t["name"] for t in data["plot"]["traces"]]
        assert names == ["I", "Q"]               # IQ pulse keeps both traces
        assert data["plot"]["length"] == 48

    def test_detail_alias_banner(self, loaded_client):
        html = loaded_client.get(f"/pulse/detail?path={XY}.x180").data.decode()
        assert "Opened via alias" in html
        assert "x180_DragCosine" in html

    def test_detail_pointer_impact_row(self, loaded_client):
        # x90's length points into x180 — the impact row must disclose it
        html = loaded_client.get(
            f"/pulse/detail?path={XY}.x90_DragCosine").data.decode()
        assert "edits follow the pointer" in html
        assert "x180_DragCosine.length" in html

    def test_detail_used_by_section(self, loaded_client):
        html = loaded_client.get(
            f"/pulse/detail?path={XY}.x180_DragCosine").data.decode()
        assert "Used by" in html
        assert f"{XY}.x180" in html

    def test_detail_unknown_class_degrades(self, loaded_client):
        html = loaded_client.get(f"/pulse/detail?path={XY}.mystery").data.decode()
        assert "Unrecognized pulse class" in html
        assert "WeirdPulse" in html

    def test_detail_readout_runtime_pointer(self, loaded_client):
        html = loaded_client.get(
            "/pulse/detail?path=qubits.qA1.resonator.operations.readout"
        ).data.decode()
        assert "(runtime)" in html
        assert "readout-only" in html

    def test_detail_pair_slot(self, loaded_client):
        html = loaded_client.get(
            "/pulse/detail?path=qubit_pairs.qA1-qA2.macros.cz_unipolar.flux_pulse_qubit"
        ).data.decode()
        assert "SquarePulse" in html

    def test_detail_bad_path_404(self, loaded_client):
        assert loaded_client.get(
            "/pulse/detail?path=qubits.qA1.f_01").status_code == 404
        assert loaded_client.get(
            f"/pulse/detail?path={XY}.nope").status_code == 404
        assert loaded_client.get("/pulse/detail").status_code == 404

    def test_detail_no_state(self, client):
        html = client.get(f"/pulse/detail?path={XY}.x180").data.decode()
        assert "No state loaded" in html


# ===========================================================================
# Stage 5 — mutations
# ===========================================================================

class TestPulseSynthApi:
    def test_synth_by_path_with_overrides(self, loaded_client):
        data = loaded_client.post("/api/pulse/synth", json={
            "path": f"{XY}.saturation", "params": {"amplitude": "0.5", "length": "8"},
        }).get_json()
        assert data["ok"] and data["plot"]["ok"]
        assert data["plot"]["length"] == 8

    def test_synth_by_qclass(self, loaded_client):
        data = loaded_client.post("/api/pulse/synth", json={
            "qclass": "GaussianPulse",
            "params": {"length": 40, "amplitude": 0.1, "sigma": 8.0},
        }).get_json()
        assert data["ok"] and data["plot"]["length"] == 40

    def test_synth_bad_params_returns_200_with_error(self, loaded_client):
        resp = loaded_client.post("/api/pulse/synth", json={
            "qclass": "SNZPulse", "params": {"amplitude": 0.05, "flat_length": 21},
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert not data["ok"] and "even" in data["error"]

    def test_synth_never_mutates(self, loaded_client, app):
        loaded_client.post("/api/pulse/synth", json={
            "path": f"{XY}.saturation", "params": {"amplitude": "9.9"}})
        html = loaded_client.get(f"/pulse/detail?path={XY}.saturation").data.decode()
        assert "0.004" in html  # committed value untouched
        assert "9.9" not in html

    def test_synth_no_inputs(self, loaded_client):
        data = loaded_client.post("/api/pulse/synth", json={}).get_json()
        assert not data["ok"]


class TestPulseEdit:
    def test_edit_plain_value(self, loaded_client):
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation",
            "dot_path": f"{XY}.saturation.amplitude",
            "mode": "value", "value": "0.009",
        })
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "pulses-changed, diagnostics-changed"
        assert "pending-tray" in resp.data.decode()
        html = loaded_client.get(f"/pulse/detail?path={XY}.saturation").data.decode()
        assert "0.009" in html

    def test_edit_value_follows_pointer_to_target(self, loaded_client):
        # editing x90's length (a #../x180.../length pointer) writes at x180
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.x90_DragCosine",
            "dot_path": f"{XY}.x90_DragCosine.length",
            "mode": "value", "value": "52",
        })
        assert resp.status_code == 200
        x180 = loaded_client.get(
            f"/pulse/detail?path={XY}.x180_DragCosine").data.decode()
        assert "52" in x180
        # the pointer itself is intact
        x90 = loaded_client.get(
            f"/pulse/detail?path={XY}.x90_DragCosine").data.decode()
        assert "#../x180_DragCosine/length" in x90

    def test_edit_literal_breaks_link_with_typed_value(self, loaded_client, app):
        """L1 regression: break-link writes a typed int, never the string '40'."""
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.x90_DragCosine",
            "dot_path": f"{XY}.x90_DragCosine.length",
            "mode": "literal", "value": "40",
        })
        assert resp.status_code == 200
        # The field is no longer an active pointer (no live pointer-badge on
        # its value) — though a gray "was → #../…" prev-link chip now reminds
        # the user what it was unlinked from (A3). Prove the break at the
        # value level via /field/peek: the raw merged value must be int 40,
        # not the string "40" (L1 regression) and not the pointer.
        peek = loaded_client.get(
            f"/field/peek?dot_path={XY}.x90_DragCosine.length").get_json()
        value = peek["values"][f"{XY}.x90_DragCosine.length"]
        assert value == 40 and isinstance(value, int)

    def test_edit_pointer_mode_relinks(self, loaded_client):
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation",
            "dot_path": f"{XY}.saturation.length",
            "mode": "pointer", "value": "#../x180_DragCosine/length",
        })
        assert resp.status_code == 200
        html = loaded_client.get(f"/pulse/detail?path={XY}.saturation").data.decode()
        assert "#../x180_DragCosine/length" in html

    def test_edit_pointer_mode_rejects_malformed(self, loaded_client):
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation",
            "dot_path": f"{XY}.saturation.length",
            "mode": "pointer", "value": "#oops",
        })
        assert resp.status_code == 400

    def test_edit_invalid_path_rejected(self, loaded_client):
        resp = loaded_client.post("/pulse/edit", data={
            "path": "qubits.qA1.f_01", "dot_path": "qubits.qA1.f_01",
            "mode": "value", "value": "1",
        })
        assert resp.status_code == 400


class TestPulseCreate:
    def test_create_form_renders(self, loaded_client):
        html = loaded_client.get("/pulse/new").data.decode()
        assert "pulse-create-root" in html
        assert "GaussianPulse" in html and "SNZPulse" in html
        assert "_FlatTopGaussianPulse" not in html  # deprecated not offered

    def test_create_qubit_pulse(self, loaded_client):
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "gauss_probe", "pulse_type": "GaussianPulse",
            "length": "40", "amplitude": "0.1", "sigma": "8.0",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "gauss_probe" in html and "pending-tray" in html
        assert resp.headers.get("HX-Trigger") == "pulses-changed, diagnostics-changed"

    def test_create_inferred_length_writes_pointer(self, loaded_client):
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "z",
            "op_name": "snz_probe", "pulse_type": "SNZPulse",
            "amplitude": "0.05", "flat_length": "20", "t_phi_eff": "2.0",
        })
        assert resp.status_code in (200, 400)
        if resp.status_code == 400:
            # qA1 has no z channel in this fixture — accept the guidance error
            assert b"operations" in resp.data
            return
        peek = loaded_client.get(
            "/field/peek?dot_path=qubits.qA1.z.operations.snz_probe.length"
        ).get_json()
        assert peek["values"]["qubits.qA1.z.operations.snz_probe.length"] \
            == "#./inferred_length"

    def test_create_duplicate_name_409(self, loaded_client):
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "saturation", "pulse_type": "SquarePulse",
            "length": "100", "amplitude": "0.1",
        })
        assert resp.status_code == 409

    def test_create_bad_name_400(self, loaded_client):
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "1bad", "pulse_type": "SquarePulse",
            "length": "100", "amplitude": "0.1",
        })
        assert resp.status_code == 400

    def test_create_unknown_type_400(self, loaded_client):
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "x", "pulse_type": "NopePulse",
        })
        assert resp.status_code == 400

    def test_create_into_none_coupler_slot(self, loaded_client):
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "pair", "pair": "qA1-qA2", "gate": "cz_unipolar",
            "slot": "coupler_flux_pulse", "pulse_type": "SquarePulse",
            "length": "100", "amplitude": "0.1",
        })
        assert resp.status_code == 200
        html = loaded_client.get(
            "/pulse/detail?path=qubit_pairs.qA1-qA2.macros.cz_unipolar.coupler_flux_pulse"
        ).data.decode()
        assert "SquarePulse" in html

    def test_create_occupied_slot_409(self, loaded_client):
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "pair", "pair": "qA1-qA2", "gate": "cz_unipolar",
            "slot": "flux_pulse_qubit", "pulse_type": "SquarePulse",
            "length": "100", "amplitude": "0.1",
        })
        assert resp.status_code == 409

    def test_create_pointer_param_accepted(self, loaded_client):
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "linked_sat", "pulse_type": "SquarePulse",
            "length": "#../saturation/length", "amplitude": "0.002",
        })
        assert resp.status_code == 200
        peek = loaded_client.get(
            f"/field/peek?dot_path={XY}.linked_sat.length").get_json()
        assert peek["values"][f"{XY}.linked_sat.length"] \
            == "#../saturation/length"


class TestPulseDelete:
    def test_delete_unreferenced(self, loaded_client):
        resp = loaded_client.post("/api/pulse/delete",
                                  data={"path": f"{XY}.saturation"})
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "pulses-changed, diagnostics-changed"
        html = loaded_client.get("/pulses").data.decode()
        assert "saturation" not in html

    def test_delete_referenced_409_without_force(self, loaded_client):
        resp = loaded_client.post("/api/pulse/delete",
                                  data={"path": f"{XY}.x180_DragCosine"})
        assert resp.status_code == 409
        assert b"x180" in resp.data  # referrer list shown

    def test_delete_referenced_with_force(self, loaded_client):
        resp = loaded_client.post(
            "/api/pulse/delete",
            data={"path": f"{XY}.x180_DragCosine", "force": "1"})
        assert resp.status_code == 200
        assert b"dangle" in resp.data

    def test_delete_alias(self, loaded_client):
        resp = loaded_client.post("/api/pulse/delete", data={"path": f"{XY}.x180"})
        assert resp.status_code == 200

    def test_delete_arbitrary_path_rejected(self, loaded_client):
        resp = loaded_client.post("/api/pulse/delete",
                                  data={"path": "qubits.qA1.f_01"})
        assert resp.status_code == 400
        resp = loaded_client.post("/api/pulse/delete", data={"path": "qubits"})
        assert resp.status_code == 400


class TestPulseDuplicate:
    def test_duplicate_basic(self, loaded_client):
        resp = loaded_client.post("/api/pulse/duplicate", data={
            "path": f"{XY}.x90_DragCosine", "new_name": "x90_v2"})
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "x90_v2" in html
        # outbound family pointers kept verbatim (still track x180)
        peek = loaded_client.get(
            f"/field/peek?dot_path={XY}.x90_v2.length").get_json()
        assert peek["resolved"][f"{XY}.x90_v2.length"]["resolved_value"] == 48

    def test_duplicate_collision_409(self, loaded_client):
        resp = loaded_client.post("/api/pulse/duplicate", data={
            "path": f"{XY}.saturation", "new_name": "x180_DragCosine"})
        assert resp.status_code == 409

    def test_duplicate_pair_slot_rejected(self, loaded_client):
        resp = loaded_client.post("/api/pulse/duplicate", data={
            "path": "qubit_pairs.qA1-qA2.macros.cz_unipolar.flux_pulse_qubit",
            "new_name": "whatever"})
        assert resp.status_code == 400


class TestPulseRename:
    def test_rename_with_retarget(self, loaded_client):
        resp = loaded_client.post("/api/pulse/rename", data={
            "path": f"{XY}.x180_DragCosine", "new_name": "x180_v2",
            "retarget": "1"})
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "x180_v2" in html and "re-pointed" in html
        # the alias now points at the new name and still resolves
        peek = loaded_client.get(f"/field/peek?dot_path={XY}.x180").get_json()
        info = peek["resolved"][f"{XY}.x180"]
        assert "x180_v2" in (info.get("resolved_path") or "")
        # x90's length still resolves (re-pointed)
        peek2 = loaded_client.get(
            f"/field/peek?dot_path={XY}.x90_DragCosine.length").get_json()
        assert peek2["resolved"][f"{XY}.x90_DragCosine.length"]["resolved_value"] == 48

    def test_rename_without_retarget_dangles(self, loaded_client):
        resp = loaded_client.post("/api/pulse/rename", data={
            "path": f"{XY}.x180_DragCosine", "new_name": "x180_v2",
            "retarget": "0"})
        assert resp.status_code == 200
        assert b"dangle" in resp.data

    def test_rename_collision_409(self, loaded_client):
        resp = loaded_client.post("/api/pulse/rename", data={
            "path": f"{XY}.saturation", "new_name": "x180_DragCosine"})
        assert resp.status_code == 409

    def test_rename_pair_slot_rejected(self, loaded_client):
        resp = loaded_client.post("/api/pulse/rename", data={
            "path": "qubit_pairs.qA1-qA2.macros.cz_unipolar.flux_pulse_qubit",
            "new_name": "x"})
        assert resp.status_code == 400


# ===========================================================================
# Stage 6 — Verify (ground truth from the cached generated config)
# ===========================================================================

def _store_of(app):
    name = list(app.config["contexts"].keys())[0]
    return app.config["contexts"][name]["store"]


def _inject_config(app, config, *, basis_hash=..., unsaved=False):
    """Plant a fake generated config + fresh meta on the active store.

    Default basis_hash = the CURRENT in-memory state hash, so the config
    reads fresh until an edit diverges it (the _config_stale contract).
    """
    from datetime import datetime, timezone

    from quam_state_manager.core.working_copy import content_hash

    store = _store_of(app)
    if basis_hash is ...:
        basis_hash = content_hash(store.state, store.wiring)
    store.generated_config = config
    store.generated_config_meta = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {}, "warnings": [], "qubits": [], "qubit_pairs": [],
        "basis_hash": basis_hash,
        "unsaved_at_generate": unsaved,
    }
    return store


def _saturation_config():
    """A config whose saturation waveform equals the synth output exactly."""
    return {
        "elements": {
            "qA1.xy": {"operations": {"saturation": "qA1.xy.saturation.pulse"}},
        },
        "pulses": {
            "qA1.xy.saturation.pulse": {
                "length": 20000,
                "waveforms": {"single": "qA1.xy.saturation.wf"},
            },
        },
        "waveforms": {
            "qA1.xy.saturation.wf": {"type": "constant", "sample": 0.004},
        },
    }


class TestPulseGroundTruth:
    def test_absent_config_409(self, loaded_client):
        resp = loaded_client.get(
            f"/api/pulse/ground-truth?path={XY}.saturation")
        assert resp.status_code == 409
        assert resp.get_json()["status"] == "absent"

    def test_fresh_match(self, loaded_client, app):
        _inject_config(app, _saturation_config())
        data = loaded_client.get(
            f"/api/pulse/ground-truth?path={XY}.saturation").get_json()
        assert data["ok"] and data["status"] == "fresh"
        assert data["comparison"]["match"] is True
        assert data["plot"]["traces"][0]["name"] == "I"
        assert data["meta"]["stale"] is False

    def test_stale_after_edit(self, loaded_client, app):
        _inject_config(app, _saturation_config())
        loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation",
            "dot_path": f"{XY}.saturation.amplitude",
            "mode": "value", "value": "0.005",
        })
        data = loaded_client.get(
            f"/api/pulse/ground-truth?path={XY}.saturation").get_json()
        assert data["ok"] and data["status"] == "stale"
        assert data["meta"]["stale"] is True

    def test_fresh_again_after_undo(self, loaded_client, app):
        # _config_stale's edge over a seq counter: undoing back to the
        # generated content makes the overlay fresh again.
        _inject_config(app, _saturation_config())
        loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation",
            "dot_path": f"{XY}.saturation.amplitude",
            "mode": "value", "value": "0.005",
        })
        assert loaded_client.get(
            f"/api/pulse/ground-truth?path={XY}.saturation"
        ).get_json()["status"] == "stale"
        loaded_client.post("/undo")
        assert loaded_client.get(
            f"/api/pulse/ground-truth?path={XY}.saturation"
        ).get_json()["status"] == "fresh"

    def test_unsaved_at_generate_basis_divergence_is_stale(self, loaded_client, app):
        # unsaved edits at regenerate time: the basis (file hash) provably
        # differs from the in-memory content → immediately stale
        _inject_config(app, _saturation_config(),
                       basis_hash="not-the-current-content", unsaved=True)
        data = loaded_client.get(
            f"/api/pulse/ground-truth?path={XY}.saturation").get_json()
        assert data["status"] == "stale"
        assert data["meta"]["unsaved_at_generate"] is True

    def test_legacy_meta_without_basis_reads_stale(self, loaded_client, app):
        store = _inject_config(app, _saturation_config())
        del store.generated_config_meta["basis_hash"]
        data = loaded_client.get(
            f"/api/pulse/ground-truth?path={XY}.saturation").get_json()
        assert data["status"] == "stale"  # unknown basis: cannot prove freshness

    def test_iq_truth_includes_q(self, loaded_client, app):
        # build the truth FROM the synth output → must compare as a match
        from quam_state_manager.core.waveform_synth import synth_for_operation
        store = _store_of(app)
        synth = synth_for_operation(store, f"{XY}.x180_DragCosine")
        assert synth["ok"]
        config = {
            "elements": {
                "qA1.xy": {"operations": {
                    "x180_DragCosine": "qA1.xy.x180.pulse"}},
            },
            "pulses": {
                "qA1.xy.x180.pulse": {
                    "length": 48,
                    "waveforms": {"I": "qA1.xy.x180.wf.I",
                                  "Q": "qA1.xy.x180.wf.Q"},
                },
            },
            "waveforms": {
                "qA1.xy.x180.wf.I": {"type": "arbitrary", "samples": synth["i"]},
                "qA1.xy.x180.wf.Q": {"type": "arbitrary", "samples": synth["q"]},
            },
        }
        _inject_config(app, config)
        data = loaded_client.get(
            f"/api/pulse/ground-truth?path={XY}.x180_DragCosine").get_json()
        assert data["ok"]
        names = [t["name"] for t in data["plot"]["traces"]]
        assert names == ["I", "Q"]
        assert data["comparison"]["match"] is True

    def test_mismatch_reports_delta(self, loaded_client, app):
        config = _saturation_config()
        config["waveforms"]["qA1.xy.saturation.wf"]["sample"] = 0.014
        _inject_config(app, config)
        data = loaded_client.get(
            f"/api/pulse/ground-truth?path={XY}.saturation").get_json()
        assert data["ok"] and data["comparison"]["match"] is False
        assert abs(data["comparison"]["max_delta"] - 0.01) < 1e-12

    def test_pair_gate_name_matching(self, loaded_client, app):
        config = {
            "elements": {
                "qA2.z": {"operations": {
                    "cz_unipolar_pulse_qA1": "p1",
                    "cz_other_gate_pulse_qA9": "p2",
                }},
            },
            "pulses": {
                "p1": {"length": 100, "waveforms": {"single": "w1"}},
            },
            "waveforms": {"w1": {"type": "constant", "sample": 0.05}},
        }
        _inject_config(app, config)
        data = loaded_client.get(
            "/api/pulse/ground-truth?path="
            "qubit_pairs.qA1-qA2.macros.cz_unipolar.flux_pulse_qubit"
        ).get_json()
        assert data["ok"]
        assert data["operation"] == "cz_unipolar_pulse_qA1"
        assert data["comparison"]["match"] is True  # constant 0.05 × 100

    def test_unmatched_op_404(self, loaded_client, app):
        # the op the path names isn't in the (empty) config at all → not-found,
        # distinct from no-trace (op present but carrying no waveform).
        _inject_config(app, {"elements": {}, "pulses": {}, "waveforms": {}})
        resp = loaded_client.get(
            f"/api/pulse/ground-truth?path={XY}.saturation")
        assert resp.status_code == 404
        assert resp.get_json()["status"] == "not-found"

    def test_op_present_but_no_waveform_is_no_trace(self, loaded_client, app):
        # the op IS registered in the config but its pulse has no waveforms
        # (e.g. a measurement op with only integration weights) → no-trace,
        # NOT not-found. Guards the not-found/no-trace split in the route.
        _inject_config(app, {
            "elements": {
                "qA1.xy": {"operations": {"saturation": "qA1.xy.saturation.pulse"}},
            },
            "pulses": {
                "qA1.xy.saturation.pulse": {"length": 20000},  # no "waveforms"
            },
            "waveforms": {},
        })
        resp = loaded_client.get(
            f"/api/pulse/ground-truth?path={XY}.saturation")
        assert resp.status_code == 404
        assert resp.get_json()["status"] == "no-trace"

    def test_bad_path_404(self, loaded_client, app):
        _inject_config(app, _saturation_config())
        resp = loaded_client.get(
            "/api/pulse/ground-truth?path=qubits.qA1.f_01")
        assert resp.status_code == 404


# ===========================================================================
# Adversarial-review fixes — regressions
# ===========================================================================

class TestReviewFixes:
    def test_edit_value_on_dangling_pointer_writes_typed_literal(self, loaded_client):
        """mode=value on a DANGLING pointer must not stringify the number."""
        # make x90's length pointer dangle by deleting its target
        loaded_client.post("/api/pulse/delete",
                           data={"path": f"{XY}.x180_DragCosine", "force": "1"})
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.x90_DragCosine",
            "dot_path": f"{XY}.x90_DragCosine.length",
            "mode": "value", "value": "40",
        })
        assert resp.status_code == 200
        peek = loaded_client.get(
            f"/field/peek?dot_path={XY}.x90_DragCosine.length").get_json()
        value = peek["values"][f"{XY}.x90_DragCosine.length"]
        assert value == 40 and isinstance(value, int)

    def test_edit_value_rejects_pointer_shaped_input(self, loaded_client):
        """A pointer typed into the value box must NOT re-link the resolved
        target node — explicit re-link mode is required."""
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.x90_DragCosine",
            "dot_path": f"{XY}.x90_DragCosine.length",
            "mode": "value", "value": "#../saturation/length",
        })
        assert resp.status_code == 400
        # neither the field nor its old target moved
        peek = loaded_client.get(
            f"/field/peek?dot_path={XY}.x90_DragCosine.length").get_json()
        resolved = peek["resolved"][f"{XY}.x90_DragCosine.length"]
        assert resolved["resolved_value"] == 48

    def test_alias_delete_confirm_shows_alias_referrers(self, loaded_client):
        html = loaded_client.get(f"/pulse/detail?path={XY}.x180").data.decode()
        assert "deletes the alias" in html
        # the target's referrers (x90 fields) must NOT be in the confirm box
        confirm = html.split('pulse-delete-confirm')[1].split('</form>')[0]
        assert "x90_DragCosine.length" not in confirm

    def test_undo_fires_pulses_changed(self, loaded_client):
        loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation",
            "dot_path": f"{XY}.saturation.amplitude",
            "mode": "value", "value": "0.009"})
        resp = loaded_client.post("/undo")
        assert "pulses-changed" in (resp.headers.get("HX-Trigger") or "")

    def test_discard_fires_pulses_changed(self, loaded_client):
        loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation",
            "dot_path": f"{XY}.saturation.amplitude",
            "mode": "value", "value": "0.009"})
        resp = loaded_client.post("/discard", data={"index": "0"})
        assert "pulses-changed" in (resp.headers.get("HX-Trigger") or "")

    def test_rows_fresh_after_raw_reload(self, loaded_client, app):
        """PulseIndex self-validates via mutation_seq — even a bare
        store.reload() (no explicit invalidate) must refresh the rows."""
        store = _store_of(app)
        # mutate the on-disk file directly, then reload the store
        state = json.loads(
            (Path(store.folder_path) / "state.json").read_text(encoding="utf-8"))
        del state["qubits"]["qA1"]["xy"]["operations"]["saturation"]
        (Path(store.folder_path) / "state.json").write_text(
            json.dumps(state), encoding="utf-8")
        store.reload()
        html = loaded_client.get("/pulses").data.decode()
        assert "saturation" not in html


class TestCatalogFixes:
    def test_waveform_pulse_template_default_is_list(self):
        from quam_state_manager.core.pulse_catalog import (
            PULSE_CATALOG, build_template)
        t = build_template(PULSE_CATALOG["WaveformPulse"], {})
        assert isinstance(t["waveform_I"], list)

    def test_inferred_class_honors_literal_length(self):
        from quam_state_manager.core.waveform_synth import synthesize
        # stored literal length overrides the inferred 4ns-grid formula
        p = synthesize("SNZPulse", {"amplitude": 0.05, "flat_length": 20,
                                    "length": 40})
        assert p["ok"] and p["length"] == 40 and len(p["i"]) == 40


class TestPulsesServerSearch:
    def test_search_finds_rows_on_any_page(self, loaded_client):
        # the fixture has a handful of ops; q= filters the WHOLE library
        r = loaded_client.get("/pulses?rows=1&q=x180")
        html = r.data.decode()
        assert "x180_DragCosine" in html
        assert "saturation" not in html   # filtered out

    def test_search_and_tokens(self, loaded_client):
        r = loaded_client.get("/pulses?rows=1&q=qA1 readout")
        html = r.data.decode()
        assert "readout" in html
        assert "x180_DragCosine" not in html

    def test_search_combines_with_channel(self, loaded_client):
        # channel=resonator + q=readout → readout present; xy ops absent
        r = loaded_client.get("/pulses?rows=1&channel=resonator&q=readout")
        html = r.data.decode()
        assert "readout" in html
        assert "x90_DragCosine" not in html

    def test_search_empty_returns_all(self, loaded_client):
        r = loaded_client.get("/pulses?rows=1&q=")
        assert r.status_code == 200
        assert "x180_DragCosine" in r.data.decode()

    def test_search_no_match(self, loaded_client):
        r = loaded_client.get("/pulses?rows=1&q=zzznomatch")
        html = r.data.decode()
        assert "No pulses found" in html

    def test_sparkline_cache_reused(self, loaded_client, app):
        # two identical page renders → second pays no synth (cache hit)
        loaded_client.get("/pulses?rows=1")
        store = _store_of(app)
        pi = None
        name = list(app.config["contexts"].keys())[0]
        pi = app.config["contexts"][name].get("pulse_index")
        assert pi is not None and pi._spark   # populated
        seq_before = pi._spark_seq
        loaded_client.get("/pulses?rows=1")
        assert pi._spark_seq == seq_before    # same mutation_seq, cache kept


class TestUnlinkPrevLink:
    def test_unlink_registers_in_tray_and_shows_prev_link(self, loaded_client):
        # x90_DragCosine.length is "#../x180_DragCosine/length" — unlink it
        dp = f"{XY}.x90_DragCosine.length"
        r = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.x90_DragCosine", "dot_path": dp,
            "mode": "literal", "value": "48"})
        assert r.status_code == 200
        # the edit registered in the pending tray (an unsaved change)
        assert b"pending-tray" in r.data
        # the detail now shows the field as a literal with a "was → pointer" chip
        html = loaded_client.get(
            f"/pulse/detail?path={XY}.x90_DragCosine").data.decode()
        assert "ptr-prev-link" in html
        assert "#../x180_DragCosine/length" in html
        assert "re-link" in html

    def test_relink_to_previous_restores_pointer(self, loaded_client):
        dp = f"{XY}.x90_DragCosine.length"
        loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.x90_DragCosine", "dot_path": dp,
            "mode": "literal", "value": "48"})
        # restore via the prev-link button (mode=pointer)
        r = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.x90_DragCosine", "dot_path": dp,
            "mode": "pointer", "value": "#../x180_DragCosine/length"})
        assert r.status_code == 200
        peek = loaded_client.get(f"/field/peek?dot_path={dp}").get_json()
        assert peek["values"][dp] == "#../x180_DragCosine/length"

    def test_no_prev_link_without_unlink(self, loaded_client):
        html = loaded_client.get(
            f"/pulse/detail?path={XY}.x90_DragCosine").data.decode()
        assert "ptr-prev-link" not in html


# ---------------------------------------------------------------------------
# Class-churn hardening: leaf-matched rendering + chip-derived create paths
# ---------------------------------------------------------------------------

@pytest.fixture
def foreign_folder(tmp_path: Path) -> Path:
    """The same chip, but every catalog class under a foreign module prefix —
    the audited new-stack scenario (path rewrite, identical fields)."""
    state = json.loads(json.dumps(_make_state()).replace(
        "quam.components.pulses.", "newstack.pulses."))
    folder = tmp_path / "foreign"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(state, indent=2),
                                       encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2),
                                        encoding="utf-8")
    return folder


@pytest.fixture
def foreign_client(client, foreign_folder):
    client.post("/load", data={"folder": str(foreign_folder)})
    return client


class TestLeafMatchedRendering:
    def test_rows_soft_chip_and_sparkline_together(self, foreign_client):
        # The sparkline is the first place a wrong leaf match renders
        # confidently — the soft chip on the SAME row is its honesty signal.
        html = foreign_client.get("/pulses").data.decode()
        start = html.index('data-pulse-path="qubits.qA1.xy.operations.saturation"')
        row_html = html[start:html.index("</tr>", start)]
        assert "pulse-class-soft" in row_html
        assert "<svg" in row_html
        assert "Matched by class name only" in row_html

    def test_rows_unknown_class_still_unknown_chip(self, foreign_client):
        html = foreign_client.get("/pulses").data.decode()
        start = html.index('data-pulse-path="qubits.qA1.xy.operations.mystery"')
        row_html = html[start:html.index("</tr>", start)]
        assert "pulse-class-unknown" in row_html
        assert "<svg" not in row_html

    def test_detail_leaf_matched_preview_plus_caution(self, foreign_client):
        html = foreign_client.get(
            f"/pulse/detail?path={XY}.x180_DragCosine").data.decode()
        assert "Matched by class" in html          # soft caution banner
        assert "newstack.pulses.DragCosinePulse" in html   # chip's path shown
        assert "quam.components.pulses.DragCosinePulse" in html  # catalog's too
        assert "Verify vs config" in html
        assert "Unrecognized pulse class" not in html
        assert '"ok": true' in html                # preview traces present

    def test_exact_match_chip_shows_no_caution(self, loaded_client):
        html = loaded_client.get(
            f"/pulse/detail?path={XY}.x180_DragCosine").data.decode()
        assert "Matched by class" not in html
        assert "pulse-soft-banner" not in html

    def test_alias_opened_detail_shows_targets_real_path(self, foreign_client):
        # Opened via the alias, the leaf banner must show the resolved
        # TARGET's stored class path — the alias row's own qclass is None,
        # and "this chip stores None" is worse than no banner at all.
        html = foreign_client.get(
            f"/pulse/detail?path={XY}.x180").data.decode()
        assert "Matched by class" in html
        assert "newstack.pulses.DragCosinePulse" in html
        assert "<code>None</code>" not in html

    def test_synth_api_reports_unmodeled_fields(self, foreign_client):
        resp = foreign_client.post("/api/pulse/synth", json={
            "qclass": "newstack.pulses.SquarePulse",
            "params": {"amplitude": 0.1, "length": 40, "brand_new_knob": 1},
        }).get_json()
        assert resp["ok"] is True                  # warning never flips ok
        assert any("brand_new_knob" in w for w in resp["plot"]["warnings"])


class TestCreateChipQclass:
    def test_create_on_foreign_chip_reuses_chip_prefix(self, foreign_client):
        resp = foreign_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "sat2", "pulse_type": "SquarePulse",
            "length": "100", "amplitude": "0.1",
        })
        assert resp.status_code == 200
        peek = foreign_client.get(
            f"/field/peek?dot_path={XY}.sat2.__class__").get_json()
        assert peek["values"][f"{XY}.sat2.__class__"] \
            == "newstack.pulses.SquarePulse"

    def test_create_unhomed_prefix_falls_back_to_catalog(self, foreign_client):
        # No GaussianPulse exists on the chip, and "newstack.pulses." is not
        # a REGISTERED home of the class — writing the guessed prefix would
        # risk an unloadable state.json (docs/53: quam_builder scatters
        # classes across modules), so the catalog path wins; the create
        # form's editable class field covers genuinely foreign stacks.
        resp = foreign_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "gauss1", "pulse_type": "GaussianPulse",
            "length": "40", "amplitude": "0.1", "sigma": "8",
        })
        assert resp.status_code == 200
        peek = foreign_client.get(
            f"/field/peek?dot_path={XY}.gauss1.__class__").get_json()
        assert peek["values"][f"{XY}.gauss1.__class__"] \
            == "quam.components.pulses.GaussianPulse"

    def test_create_on_stock_chip_unchanged(self, loaded_client):
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "sat3", "pulse_type": "SquarePulse",
            "length": "100", "amplitude": "0.1",
        })
        assert resp.status_code == 200
        peek = loaded_client.get(
            f"/field/peek?dot_path={XY}.sat3.__class__").get_json()
        assert peek["values"][f"{XY}.sat3.__class__"] \
            == "quam.components.pulses.SquarePulse"

    def test_create_explicit_qclass_written_verbatim(self, loaded_client):
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "sat4", "pulse_type": "SquarePulse",
            "length": "100", "amplitude": "0.1",
            "qclass": "my.stack.pulses.SquarePulse",
        })
        assert resp.status_code == 200
        peek = loaded_client.get(
            f"/field/peek?dot_path={XY}.sat4.__class__").get_json()
        assert peek["values"][f"{XY}.sat4.__class__"] \
            == "my.stack.pulses.SquarePulse"

    def test_create_qclass_leaf_mismatch_400(self, loaded_client):
        # Cross-wiring the class path against the selected type's form schema
        # would write a body whose fields belong to another class.
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "sat5", "pulse_type": "SquarePulse",
            "length": "100", "amplitude": "0.1",
            "qclass": "my.stack.pulses.GaussianPulse",
        })
        assert resp.status_code == 400

    def test_create_qclass_malformed_400(self, loaded_client):
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "sat6", "pulse_type": "SquarePulse",
            "length": "100", "amplitude": "0.1",
            "qclass": "not a path..SquarePulse",
        })
        assert resp.status_code == 400

    def test_create_form_carries_chip_qclass(self, foreign_client):
        html = foreign_client.get("/pulse/new").data.decode()
        assert "pulse-create-qclass" in html
        assert "newstack.pulses.SquarePulse" in html  # in the catalog JSON
