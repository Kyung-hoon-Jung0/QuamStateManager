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
                        # docs/166: an alias ON TOP of a pulse whose own field
                        # points elsewhere -- the real chip's two-hop chain
                        # (x90 -> x90_DragCosine -> x180_DragCosine.length).
                        "x90": "#./x90_DragCosine",
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

    def test_pulses_nested_under_live_edit_nav(self):
        # r15 IA (docs/69): Pulses moved into the Live-State-Edit subnav and the
        # old "Add pulse" nav row is gone (the page's "+ New pulse" button and
        # /pulses?create=1 auto-open remain the create entries). The new subnav
        # must be restore-registered so its collapse choice round-trips.
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
        app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
        base = (root / "templates" / "base.html").read_text(encoding="utf-8")
        assert 'id="live-edit-subnav"' in base
        assert "{ id: 'live-edit-subnav'" in app_js
        assert "quam_liveedit_nav_collapsed" in base and \
               "quam_liveedit_nav_collapsed" in app_js
        # The old nav row (and its subnav) must be fully gone.
        assert 'hx-get="/pulses?create=1"' not in base
        assert "pulses-subnav" not in base
        # Pulses + Json Tree View are the group's children.
        i = base.index('id="live-edit-subnav"')
        seg = base[i:base.index("</ul>", i)]
        assert ">Json Tree View</a>" in seg and ">Pulses</a>" in seg

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

    # Stress round 2026-09-16 (docs/190): a plain "inf" / "nan" typed into a
    # field reached int(float(...)) and 500'd; a float field set to Infinity
    # answered 200 with a bare `Infinity` token that is not JSON.
    @pytest.mark.parametrize("field,raw", [
        ("length", "inf"), ("length", "-inf"), ("length", "nan"),
        ("amplitude", "inf"), ("amplitude", "nan"),
    ])
    def test_synth_non_finite_is_a_param_error_not_a_500(self, loaded_client, field, raw):
        resp = loaded_client.post("/api/pulse/synth", json={
            "path": f"{XY}.saturation", "params": {field: raw}})
        assert resp.status_code == 200
        assert b"Infinity" not in resp.data and b"NaN" not in resp.data
        data = resp.get_json()
        assert not data["ok"]
        assert field in data["param_errors"]

    def test_synth_overflow_from_finite_params_is_an_error_not_infinity(self, loaded_client):
        # JSON cannot carry inf, but finite params can overflow inside the
        # synth (a DRAG pulse with amplitude 1e308 / anharmonicity 1e-300
        # measurably does); the reply must stay parseable JSON with ok=False.
        resp = loaded_client.post("/api/pulse/synth", json={
            "qclass": "DragCosinePulse",
            "params": {"length": 40, "amplitude": 1e308, "alpha": 1e308,
                       "anharmonicity": 1e-300, "detuning": 0.0, "axis_angle": 0.0},
        })
        assert resp.status_code == 200
        assert b"Infinity" not in resp.data and b"NaN" not in resp.data
        data = resp.get_json()
        # docs/190 F32: the first cut of this guard FAILED the whole payload,
        # which turned quam's own NaN (a length-1 BlackmanIntegralPulse emits
        # one and raises nothing) into a red Diagnostics finding claiming
        # generate_config would crash. The transport rule is what mattered and
        # it still holds: no bare Infinity/NaN token anywhere in the reply, the
        # unusable samples carried as null, and the payload SAYS how many.
        assert data["ok"]
        assert any("not finite" in w for w in (data.get("warnings") or [])), data
        traces = (data.get("plot") or {}).get("traces") or []
        ys = [v for t in traces for v in (t.get("y") or [])]
        assert ys and any(v is None for v in ys)

    def test_synth_raw_json_nan_length_on_an_inferred_length_class(self, loaded_client):
        # Python's json parser accepts bare NaN/Infinity tokens, so a script
        # can hand an inferred-length class a float NaN as its stored length
        # (server log 2026-09-16: "cannot convert float NaN to integer").
        for tok in ("NaN", "Infinity", "-Infinity"):
            body = ('{"qclass": "SNZPulse", "params": {"length": %s, '
                    '"amplitude": 0.1, "flat_length": 20, "b_over_a": 0.5, "t_phi": 0}}' % tok)
            resp = loaded_client.post("/api/pulse/synth", data=body,
                                      content_type="application/json")
            assert resp.status_code == 200, tok
            assert b"Infinity" not in resp.data and b"NaN" not in resp.data
            resp.get_json()

    def test_synth_route_never_500s_on_an_unexpected_exception(self, loaded_client, monkeypatch):
        # The docstring's contract, pinned directly: whatever the synth
        # raises, the client gets ok=False and keeps its last good plot.
        from quam_state_manager.core import waveform_synth as ws
        def boom(*a, **k):
            raise RuntimeError("synthetic failure")
        monkeypatch.setattr(ws, "synth_for_operation", boom)
        resp = loaded_client.post("/api/pulse/synth", json={
            "path": f"{XY}.saturation", "params": {"amplitude": "0.1"}})
        assert resp.status_code == 200
        data = resp.get_json()
        assert not data["ok"] and "synthetic failure" in data["error"]


class TestPulseEdit:
    def test_edit_plain_value(self, loaded_client):
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation",
            "dot_path": f"{XY}.saturation.amplitude",
            "mode": "value", "value": "0.009",
        })
        assert resp.status_code == 200
        # docs/141 4j: a VALUE commit names the row it touched (JSON form)
        import json as _json
        trig = _json.loads(resp.headers.get("HX-Trigger"))
        assert trig["pulses-rows-changed"]["paths"] == [f"{XY}.saturation"] and trig["diagnostics-changed"] is True and "pulses-changed" not in trig
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

    @pytest.mark.parametrize("ptr", ["#../", "#/qubits/qA1/xy", "#./", "#../x180_DragCosine"])
    def test_edit_pointer_mode_rejects_a_container_target(self, loaded_client, ptr):
        # docs/190 F27: syntactically fine, but it resolves to a dict -- the
        # field became a dump of its parent and the row rendered "-".
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation",
            "dot_path": f"{XY}.saturation.length",
            "mode": "pointer", "value": ptr,
        })
        assert resp.status_code == 400, ptr
        assert b"container" in resp.data or b"target segment" in resp.data
        html = loaded_client.get(f"/pulse/detail?path={XY}.saturation").data.decode()
        assert ptr not in html

    def test_edit_pointer_mode_dangling_is_still_allowed(self, loaded_client):
        # real chips carry dangling pointers; the badge says so, the write lands
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation",
            "dot_path": f"{XY}.saturation.length",
            "mode": "pointer", "value": "#../nope/length",
        })
        assert resp.status_code == 200

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

    def test_create_into_none_coupler_slot_needs_a_coupler(self, loaded_client):
        """2026-09-27: a coupler pulse on a pair with no coupler was written
        into the macro, whose apply() then calls ``qubit_pair.coupler.play``
        on None. Refused now, and the slot stays empty."""
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "pair", "pair": "qA1-qA2", "gate": "cz_unipolar",
            "slot": "coupler_flux_pulse", "pulse_type": "SquarePulse",
            "length": "100", "amplitude": "0.1",
        })
        assert resp.status_code == 409
        assert b"no coupler" in resp.data

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
        # Scoped to the pulse TABLE, not the whole page. The Review drawer now
        # renders on a full page load (it used to be silently empty — the
        # `_ctx()` / `_render_tray` field mismatch), and it correctly lists the
        # pending deletion by path. A whole-page `not in` therefore fails on the
        # very evidence the drawer exists to show.
        import re as _re
        table = _re.search(r"<table[^>]*pulse[^>]*>.*?</table>", html, _re.S | _re.I)
        body = table.group(0) if table else html.split('id="pending-tray"')[0]
        assert "saturation" not in body, "the deleted pulse is still listed"
        # ...and the deletion IS reported where a user reviews changes — a
        # delete the review surface hid would be the real defect. sync-ux
        # 2026-09-25: that surface is the sync panel now, not a page drawer.
        panel = loaded_client.get("/state/review").data.decode()
        mine = panel.split("sp-group-mine", 1)[1]
        assert "tray-change-item" in mine
        assert f'data-path="{XY}.saturation"' in mine
        assert "deleted" in mine.split(f'data-path="{XY}.saturation"', 1)[1].split("</tr>", 1)[0]

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

    def test_rename_to_own_name_is_a_409_not_a_500(self, loaded_client):
        # Stress round 2026-09-16: Enter on the unchanged name raised the
        # modifier's ValueError straight through the route.
        resp = loaded_client.post("/api/pulse/rename", data={
            "path": f"{XY}.saturation", "new_name": "saturation"})
        assert resp.status_code == 409
        assert b"Already named" in resp.data
        # and nothing was renamed or logged as a change
        html = loaded_client.get(f"/pulse/detail?path={XY}.saturation").data.decode()
        assert "saturation" in html

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
        _h = resp.headers.get("HX-Trigger") or ""
        assert ("pulses-rows-changed" in _h) or ("pulses-changed" in _h)   # docs/141 4j: a value change names its rows

    def test_discard_fires_pulses_changed(self, loaded_client):
        loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation",
            "dot_path": f"{XY}.saturation.amplitude",
            "mode": "value", "value": "0.009"})
        resp = loaded_client.post("/discard", data={"index": "0"})
        _h = resp.headers.get("HX-Trigger") or ""
        assert ("pulses-rows-changed" in _h) or ("pulses-changed" in _h)   # docs/141 4j: a value change names its rows

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

    def test_search_pipe_or(self, loaded_client):
        # docs/96: space = AND, standalone | = OR — one query, both families
        r = loaded_client.get("/pulses?rows=50&q=x180 | readout")
        html = r.data.decode()
        assert "x180_DragCosine" in html and "readout" in html
        assert "saturation" not in html

    def test_search_or_binds_tighter_than_and(self, loaded_client):
        # qA1 AND (x180 | readout): still scoped to qA1's rows
        r = loaded_client.get("/pulses?rows=50&q=qA1 x180 | readout")
        assert r.status_code == 200
        html = r.data.decode()
        assert "x180_DragCosine" in html and "readout" in html
        assert "saturation" not in html
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


# ---------------------------------------------------------------------------
# r15 (docs/71 §2): env-aware create — roster-driven form, env-only classes,
# the never-silent env-compat gate, and the discovery strip.
# ---------------------------------------------------------------------------


class TestCreateEnvAware:
    @pytest.fixture(autouse=True)
    def _overlay_hygiene(self):
        from quam_state_manager.core import pulse_catalog as pc
        pc.apply_env_overlay(None)
        yield
        pc.apply_env_overlay(None)

    @pytest.fixture
    def modern_roster(self):
        data = json.loads((Path(__file__).parent / "golden"
                           / "state_schema_modern.json").read_text(
            encoding="utf-8"))
        return data["pulse_roster"]

    def test_no_roster_form_is_static_catalog(self, loaded_client):
        html = loaded_client.get("/pulse/new").data.decode()
        assert "pulse-env-strip" in html
        assert "static catalog" in html or "not probed" in html
        assert "From environment" not in html
        cat = json.loads(html.split('id="pulse-catalog-data"'
                                    ' type="application/json">')[1]
                         .split("</script>")[0])
        assert all(e.get("verify") is None for e in cat.values())

    def test_roster_adds_env_class_and_verdicts(self, loaded_client,
                                                modern_roster):
        # The roster-only group rides a synthetic lab class since docs/126 ⑦a
        # promoted CosineBipolarPulse into the static catalog (which is itself
        # pinned here: env-verified, NOT env_only, creatable as a regular
        # Flux/Bipolar entry).
        import copy as _copy
        from quam_state_manager.core import pulse_catalog as pc
        roster = _copy.deepcopy(modern_roster)
        rec = _copy.deepcopy(roster["CosineBipolarPulse"])
        rec["canonical"] = "otherlab.custom.pulses.LabWigglePulse"
        rec["homes"] = ["otherlab.custom.pulses"]
        roster["LabWigglePulse"] = rec
        pc.apply_env_overlay(roster)
        html = loaded_client.get("/pulse/new").data.decode()
        assert "From environment" in html
        assert ">LabWigglePulse</option>" in html
        cat = json.loads(html.split('id="pulse-catalog-data"'
                                    ' type="application/json">')[1]
                         .split("</script>")[0])
        assert cat["LabWigglePulse"]["env_only"] is True
        assert cat["LabWigglePulse"]["verify"] == "env"
        assert cat["CosineBipolarPulse"].get("env_only") is not True
        assert cat["CosineBipolarPulse"]["verify"] == "env"
        assert cat["SquarePulse"]["verify"] == "env"
        # the one creatable catalog class the modern roster does NOT ship
        assert cat["ErfSquarePulse"]["verify"] == "missing"
        assert "channels" in cat["SquarePulse"]

    def test_env_only_create_writes_roster_canonical(self, loaded_client,
                                                     modern_roster, app):
        from quam_state_manager.core import pulse_catalog as pc
        pc.apply_env_overlay(modern_roster)
        r = loaded_client.post("/api/pulse/create", data={
            "pulse_type": "CosineBipolarPulse", "target_kind": "qubit",
            "qubit": "qA1", "channel": "xy", "op_name": "cbp_test",
            "amplitude": "0.1", "length": "80", "flat_length": "40",
        })
        assert r.status_code == 200, r.data[:300]
        ctx = next(iter(app.config["contexts"].values()))
        op = ctx["store"].state["qubits"]["qA1"]["xy"]["operations"]["cbp_test"]
        assert op["__class__"] == modern_roster["CosineBipolarPulse"]["canonical"]
        assert op["amplitude"] == 0.1 and op["flat_length"] == 40
        assert op["length"] == 80          # explicit length, not a pointer

    def test_missing_in_env_class_gates_409_then_force(self, loaded_client,
                                                       modern_roster, app):
        from quam_state_manager.core import pulse_catalog as pc
        pc.apply_env_overlay(modern_roster)
        form = {"pulse_type": "ErfSquarePulse",
                "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
                "op_name": "erf_test", "amplitude": "0.1",
                "flat_length": "60", "risetime_samples": "8"}
        r = loaded_client.post("/api/pulse/create", data=form)
        assert r.status_code == 409
        assert b"not importable in the selected" in r.data
        ctx = next(iter(app.config["contexts"].values()))
        ops = ctx["store"].state["qubits"]["qA1"]["xy"]["operations"]
        assert "erf_test" not in ops                  # nothing committed
        r2 = loaded_client.post("/api/pulse/create", data=dict(form, force="1"))
        assert r2.status_code == 200, r2.data[:300]

    def test_catalog_class_in_roster_never_gates(self, loaded_client,
                                                 modern_roster):
        from quam_state_manager.core import pulse_catalog as pc
        pc.apply_env_overlay(modern_roster)
        r = loaded_client.post("/api/pulse/create", data={
            "pulse_type": "SquarePulse", "target_kind": "qubit",
            "qubit": "qA1", "channel": "xy", "op_name": "sq_env_ok",
            "amplitude": "0.2", "length": "60"})
        assert r.status_code == 200, r.data[:300]

    def test_env_strip_states(self, loaded_client, modern_roster, app):
        from quam_state_manager.core import config_generator as cg
        from quam_state_manager.core import pulse_catalog as pc
        # no env selected
        html = loaded_client.get("/pulse/new/env-strip").data.decode()
        assert "no Python environment selected" in html
        # env selected but interpreter gone
        cg.set_selected_env(app.instance_path, r"C:\nope\python.exe")
        html = loaded_client.get("/pulse/new/env-strip").data.decode()
        assert "no longer exists" in html
        # selected + roster warm ⇒ count + shared-with note
        import tempfile, os
        fake = Path(tempfile.mkdtemp()) / "python.exe"
        fake.write_text("x", encoding="utf-8")
        cg.set_selected_env(app.instance_path, str(fake))
        pc.apply_env_overlay(modern_roster)
        # env_card.warm needs the store's type policy manifest — the strip
        # falls to "not probed" without it; the roster count line is the
        # warm-path pin, so fake warmth via the policy attribute.
        ctx = next(iter(app.config["contexts"].values()))
        policy = getattr(ctx["store"], "type_policy", None)
        if policy is None or policy.manifest is None:
            class _P:  # minimal manifest carrier
                manifest = {"versions": {"quam": "0.6.0"}}
            ctx["store"].type_policy = _P()
        html = loaded_client.get("/pulse/new/env-strip").data.decode()
        assert "pulse class" in html and "shared with Generate Config" in html

    def test_qclass_field_is_hidden_not_editable(self, loaded_client):
        html = loaded_client.get("/pulse/new").data.decode()
        assert 'type="hidden" name="qclass" id="pulse-create-qclass"' in html
        assert 'id="pulse-create-qclass-display"' in html


def test_pulses_create_selfcheck_passes():
    """Drives tests/pulses_create_selfcheck.cjs (r15, docs/71 §2) — the
    env-aware create-form JS against the real pulses.js in jsdom."""
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        ["node", str(root / "tests" / "pulses_create_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(root), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "ALL OK" in (r.stdout or "")


# ---------------------------------------------------------------------------
# r15 (docs/71 §3): CZ-first pair flow — pairs_info island, orientation,
# slot occupancy, roster-gated gate variants, one-shot new-gate create.
# ---------------------------------------------------------------------------


def _pairs_state() -> dict:
    """Two-qubit chip where the STORED control is the LOWER-f qubit (warn
    case) plus a healthy pair; both flux-archetyped (coupler present)."""
    return {
        "qubits": {
            "q1": {"id": "q1", "f_01": 4.8e9, "z": {"operations": {}},
                   "xy": {"operations": {}}},
            "q2": {"id": "q2", "f_01": 5.1e9, "z": {"operations": {}},
                   "xy": {"operations": {}}},
        },
        "qubit_pairs": {
            "q1-q2": {                          # control q1 (LOWER f) -> warn
                "qubit_control": "#/qubits/q1",
                "qubit_target": "#/qubits/q2",
                "coupler": {"decouple_offset": 0.1},
                "macros": {
                    "cz_unipolar": {
                        "flux_pulse_qubit": {"amplitude": 0.05, "length": 100},
                        "coupler_flux_pulse": None,
                    },
                },
            },
            "q2-q1": {                          # control q2 (higher f) -> ok
                "qubit_control": "#/qubits/q2",
                "qubit_target": "#/qubits/q1",
                "coupler": {"decouple_offset": 0.1},
                "macros": {},
            },
        },
    }


@pytest.fixture
def pairs_client(tmp_path):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_pairs_state()),
                                       encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()),
                                        encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    c._app = app
    return c


class TestCzGateFirst:
    @pytest.fixture(autouse=True)
    def _overlay_hygiene(self):
        from quam_state_manager.core import pulse_catalog as pc
        pc.apply_env_overlay(None)
        yield
        pc.apply_env_overlay(None)

    @pytest.fixture
    def modern_roster(self):
        data = json.loads((Path(__file__).parent / "golden"
                           / "state_schema_modern.json").read_text(
            encoding="utf-8"))
        return data["pulse_roster"]

    def _island(self, html):
        return json.loads(html.split('id="pulse-pairs-info-data"'
                                     ' type="application/json">')[1]
                          .split("</script>")[0])

    def test_pairs_info_carries_freqs_orientation_and_slots(self, pairs_client):
        html = pairs_client.get("/pulse/new").data.decode()
        info = self._island(html)
        bad = info["q1-q2"]
        assert bad["control"] == "q1" and bad["target"] == "q2"
        assert bad["f_control"] == 4.8e9 and bad["f_target"] == 5.1e9
        assert bad["orient_ok"] is False          # control is LOWER f
        assert info["q2-q1"]["orient_ok"] is True
        slots = bad["gates"]["cz_unipolar"]["slots"]
        assert slots["flux_pulse_qubit"]["state"] == "held"
        assert slots["flux_pulse_qubit"]["class"] == "SquarePulse (implicit)"
        assert slots["flux_pulse_qubit"]["path"] == (
            "qubit_pairs.q1-q2.macros.cz_unipolar.flux_pulse_qubit")
        # 2026-09-27: this pair's coupler has no operations dict -- nothing
        # could play a coupler pulse, so the slot is not offered at all
        assert "coupler_flux_pulse" not in slots
        # and "+ new gate" is withdrawn (its gates could not play)
        assert info["q2-q1"]["new_gates"] == []

    def test_no_new_gate_is_offered_with_or_without_a_roster(self, pairs_client,
                                                             modern_roster):
        from quam_state_manager.core import pulse_catalog as pc
        info = self._island(pairs_client.get("/pulse/new").data.decode())
        assert all(i["new_gates"] == [] for i in info.values())
        pc.apply_env_overlay(modern_roster)
        html = pairs_client.get("/pulse/new").data.decode()
        info = self._island(html)
        assert all(i["new_gates"] == [] for i in info.values())
        assert "__new__:" not in html

    def test_pair_gate_form_gains_verified_variants(self, pairs_client,
                                                    modern_roster):
        from quam_state_manager.core import pulse_catalog as pc
        html = pairs_client.get("/pair/q1-q2/gate/new").data.decode()
        assert "cz_snz" not in html               # no roster -> legacy list
        pc.apply_env_overlay(modern_roster)
        html = pairs_client.get("/pair/q1-q2/gate/new").data.decode()
        assert 'value="cz_snz"' in html and 'value="cz_bipolar"' in html
        assert 'value="cz_flattop_erf"' not in html

    def test_a_new_gate_is_refused_and_nothing_is_written(self, pairs_client,
                                                          modern_roster):
        from quam_state_manager.core import pulse_catalog as pc
        pc.apply_env_overlay(modern_roster)
        ctx = next(iter(pairs_client._app.config["contexts"].values()))
        r = pairs_client.post("/api/pulse/create", data={
            "pulse_type": "SNZPulse", "target_kind": "pair",
            "pair": "q2-q1", "gate": "__new__:cz_snz",
            "new_gate_name": "cz_snz", "slot": "flux_pulse_qubit",
            "amplitude": "0.07", "flat_length": "120", "t_phi_eff": "0",
            "padding": "16"})
        assert r.status_code == 410, r.data[:400]
        assert "cz_snz" not in ctx["store"].state["qubit_pairs"]["q2-q1"]["macros"]

    def test_held_slot_still_409s(self, pairs_client):
        r = pairs_client.post("/api/pulse/create", data={
            "pulse_type": "SquarePulse", "target_kind": "pair",
            "pair": "q1-q2", "gate": "cz_unipolar",
            "slot": "flux_pulse_qubit",
            "amplitude": "0.05", "length": "100"})
        assert r.status_code == 409
        assert b"already holds" in r.data

    def test_env_gate_leaves_subset_of_capability_map(self):
        # the roster gating must stay in lockstep with the generator's
        # capability model — a variant added on one side only would either
        # never be offered or be offered unverifiable.
        from quam_state_manager.core.capabilities import _CZ_VARIANT_CAPS
        from quam_state_manager.web.routes import (_ENV_GATE_LEAVES,
                                                   _ENV_GATE_TYPES,
                                                   _SLOT_LEAVES)
        assert set(_ENV_GATE_TYPES) == set(_ENV_GATE_LEAVES)
        mapped = {"cz_bipolar": "bipolar", "cz_snz": "SNZ",
                  "cz_flattop_erf": "flattop_erf"}
        for gid, variant in mapped.items():
            assert variant in _CZ_VARIANT_CAPS, gid
            assert gid in _SLOT_LEAVES, gid


class TestPulseRow:
    """docs/141 4j: a value change re-renders ONE row through /pulse/row
    instead of re-fetching the whole table; the trigger carries the paths."""

    def test_one_row_renders_alone_with_its_sparkline(self, loaded_client):
        html = loaded_client.get(f"/pulse/row?path={XY}.saturation").data.decode()
        assert html.count("<tr") == 1 and f'data-pulse-path="{XY}.saturation"' in html
        assert "<svg" in html, "the sparkline is part of the row"
        assert loaded_client.get("/pulse/row?path=qubits.qA1.xy.operations.nope").status_code == 404
        assert loaded_client.get("/pulse/row").status_code == 404

    def test_the_row_partial_is_the_table_row(self, loaded_client):
        table = loaded_client.get("/pulses?rows=1").data.decode()
        row = loaded_client.get(f"/pulse/row?path={XY}.saturation").data.decode()
        tr = row[row.index("<tr"):]
        assert tr.split("\n")[0].strip() in table, "one template renders both"

    def test_a_pulse_edit_and_its_undo_name_the_rows_they_touched(self, loaded_client):
        import json as _json
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.x180_DragCosine", "dot_path": f"{XY}.x180_DragCosine.length",
            "mode": "value", "value": "52",
        })
        trig = _json.loads(resp.headers["HX-Trigger"])
        # x90's length POINTS at x180's: both rows changed
        assert set(trig["pulses-rows-changed"]["paths"]) >= {f"{XY}.x180_DragCosine", f"{XY}.x90_DragCosine"}
        assert trig["diagnostics-changed"] is True
        und = _json.loads(loaded_client.post("/undo").headers["HX-Trigger"])
        assert set(und["pulses-rows-changed"]["paths"]) >= {f"{XY}.x180_DragCosine", f"{XY}.x90_DragCosine"}

    def test_the_expansion_follows_a_pointer_chain_all_the_way(self, loaded_client):
        """docs/166 (customer): "length나 amplitude를 수정했는데 pulse list에
        반영이 안된다."

        Reproduced in real Chrome on the 20Q chip: editing
        ``x180_DragCosine.length`` 44 -> 48 repainted that row and the ``x180``
        alias, and left ``x90`` and ``y180`` reading 44 -- values the chip did
        not hold. The expansion was one hop, and the chain is two:

            x90 (alias) -> x90_DragCosine -> x180_DragCosine.length

        The first hop reaches ``x90_DragCosine``; nothing then asked who points
        at THAT, so the alias above it never learned. A row left showing a
        stale number is worse than a whole-table re-fetch, which is what the
        24-root cap still degrades to.
        """
        import json as _json
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.x180_DragCosine", "dot_path": f"{XY}.x180_DragCosine.length",
            "mode": "value", "value": "56",
        })
        paths = set(_json.loads(resp.headers["HX-Trigger"])["pulses-rows-changed"]["paths"])
        assert f"{XY}.x90_DragCosine" in paths, "one hop"
        assert f"{XY}.x90" in paths,             "the alias two hops out still shows the old length until it is named"
        # and it stays honest about what it did NOT touch
        assert f"{XY}.saturation" not in paths

    def test_structural_changes_and_pointer_moves_refetch_the_table(self, loaded_client):
        import json as _json
        # a re-link names the OLD target's row too (its used_by changed) -- docs/141 4l-review
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.x90_DragCosine", "dot_path": f"{XY}.x90_DragCosine.length",
            "mode": "pointer", "value": "#../saturation/length",
        })
        assert resp.status_code == 200, resp.data[:200]
        trig = _json.loads(resp.headers["HX-Trigger"])
        assert set(trig["pulses-rows-changed"]["paths"]) >= {f"{XY}.x90_DragCosine", f"{XY}.x180_DragCosine", f"{XY}.saturation"}
        # its undo moves a pointer back: the row patch cannot see the other end -> structural
        und = _json.loads(loaded_client.post("/undo").headers["HX-Trigger"])
        assert und.get("pulses-changed") is True and "pulses-rows-changed" not in und
        # a path no pulse row owns (the anharmonicity a DRAG pulse points at) -> structural
        r = loaded_client.post("/field/edit", data={"dot_path": "qubits.qA1.anharmonicity", "value": "-210000000"})
        assert r.status_code == 200, r.data[:200]
        und2 = _json.loads(loaded_client.post("/undo").headers["HX-Trigger"])
        assert und2.get("pulses-changed") is True and "pulses-rows-changed" not in und2

    def test_a_row_outside_the_chip_map_pick_answers_204(self, loaded_client):
        """docs/166: the map pick is part of the page's filter, so a patched
        row belonging to another entity leaves the table like any other."""
        assert loaded_client.get(f"/pulse/row?path={XY}.saturation&owner=qA1").status_code == 200
        assert loaded_client.get(f"/pulse/row?path={XY}.saturation&owner=qZZ").status_code == 204

    def test_a_row_that_left_the_page_filter_answers_204(self, loaded_client):
        assert loaded_client.get(f"/pulse/row?path={XY}.saturation&q=saturation").status_code == 200
        assert loaded_client.get(f"/pulse/row?path={XY}.saturation&q=zzz_no_such").status_code == 204
        assert loaded_client.get(f"/pulse/row?path={XY}.saturation&channel=resonator").status_code == 204
        assert loaded_client.get(f"/pulse/row?path={XY}.saturation&channel=xy").status_code == 200

    def test_view_paths_are_split_per_param_not_per_comma(self):
        from quam_state_manager.web.routes import _view_paths_arg, _view_paths_split
        a, b = f"{XY}.x180", f"{XY}.x90"
        assert _view_paths_arg([a, b]) == [a, b]
        assert _view_paths_arg(f"{a},{b}") == [a, b], "a comma-joined value is still accepted"
        assert _view_paths_arg([a, a, b]) == [a, b]
        assert _view_paths_split(["qubits.qA1.xy.operations.a,b"]) == ["qubits.qA1.xy.operations.a,b"], "a real op name with a comma is ONE path"
        assert _view_paths_arg([f"{XY}.p{i}" for i in range(6)]) == [f"{XY}.p{i}" for i in range(4)]

    def test_the_table_only_refetches_for_a_structural_change(self):
        from pathlib import Path as _P
        tpl = (_P(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "templates" / "_pulses.html").read_text(encoding="utf-8")
        # two event NAMES (an htmx [filter] needs eval, which the CSP forbids -- measured: ignored)
        assert 'hx-trigger="pulses-changed from:body delay:400ms"' in tpl and "[" not in tpl.split('hx-trigger="pulses-changed')[1].split('"')[0]
        app = (_P(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        assert 'document.addEventListener("pulses-rows-changed", function (evt) {' in app and '"/pulse/row?path="' in app
        cs = (_P(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static" / "chip-status.js").read_text(encoding="utf-8")
        assert "addEventListener('pulses-rows-changed', onStateMutated)" in cs


class TestPulseView:
    """docs/141 4k: the inspector is a VIEW of one to four pulses -- one plot,
    one editable parameter section per pulse, each in its trace colour."""

    def test_two_pulses_render_two_sections_on_one_plot(self, loaded_client):
        html = loaded_client.get(f"/pulse/detail?paths={XY}.x180_DragCosine,{XY}.x90_DragCosine").data.decode()
        assert html.count('class="detail-section pulse-sec"') == 2
        assert f'data-pulse-path="{XY}.x180_DragCosine"' in html and f'data-pulse-path="{XY}.x90_DragCosine"' in html
        assert html.count("--sec-color: var(--pico-primary)") == 1 and "--sec-color: #e67e22" in html
        # docs/141 4l-review: one hidden input per pulse in view, never a comma-joined value
        assert html.count('name="view_paths"') >= 4 and f'<input type="hidden" name="view_paths" value="{XY}.x180_DragCosine"><input type="hidden" name="view_paths" value="{XY}.x90_DragCosine">' in html
        import json as _json
        data = _json.loads(html.split('<script id="pulse-detail-data" type="application/json">')[1].split("</script>")[0])
        assert data["mode"] == "compare" and [p["path"] for p in data["pulses"]] == [f"{XY}.x180_DragCosine", f"{XY}.x90_DragCosine"]
        assert all(p["plot"]["ok"] for p in data["pulses"])

    def test_a_commit_keeps_the_view(self, loaded_client):
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.x90_DragCosine", "dot_path": f"{XY}.x90_DragCosine.amplitude",
            "mode": "value", "value": "0.2",
            "view_main": f"{XY}.x180_DragCosine", "view_paths": f"{XY}.x180_DragCosine,{XY}.x90_DragCosine",
        })
        html = resp.data.decode()
        assert html.count('class="detail-section pulse-sec"') == 2
        assert f'data-pulse-path="{XY}.x180_DragCosine"' in html.split('class="detail-section pulse-sec"')[1], "the main pulse stays first"
        assert 'data-committed="0.2"' in html

    def test_single_and_the_view_cap(self, loaded_client):
        html = loaded_client.get(f"/pulse/detail?path={XY}.saturation").data.decode()
        assert html.count('class="detail-section pulse-sec"') == 1 and '"mode": "single"' in html
        many = ",".join([f"{XY}.x180_DragCosine", f"{XY}.x90_DragCosine", f"{XY}.saturation", f"{XY}.mystery", "qubits.qA1.resonator.operations.readout"])
        html = loaded_client.get(f"/pulse/detail?paths={many}").data.decode()
        # docs/141 4l-review: exactly four, and the fifth is NAMED, never silently dropped
        assert html.count('class="detail-section pulse-sec"') == 4
        assert 'class="muted pulse-view-note"' in html and "readout" in html.split('pulse-view-note')[1].split("</span>")[0]
        # repeated paths= params (a comma is legal inside a foreign op name) render the same view
        rep = "&".join("paths=" + p for p in many.split(","))
        html2 = loaded_client.get(f"/pulse/detail?{rep}").data.decode()
        assert html2.count('class="detail-section pulse-sec"') == 4
        # a stale main pulse on an edit: the write lands and the view re-renders around the edited pulse
        resp = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation", "dot_path": f"{XY}.saturation.length", "mode": "value", "value": "64",
            "view_main": f"{XY}.gone_meanwhile", "view_paths": [f"{XY}.gone_meanwhile", f"{XY}.saturation"],
        })
        assert resp.status_code == 200 and 'data-committed="64"' in resp.data.decode()


class TestAnUnchangedCommitIsNotAnEdit:
    """docs/190 §8 (stress round 2026-09-17): Enter on a value the user did not
    change staged a phantom entry into the Review tray -- so tabbing through a
    pulse to READ it left a trail of edits, each one an undo step and each one
    part of the next Apply."""

    def _log(self, app):
        ctx = next(iter(app.config["contexts"].values()))
        return list(ctx["store"].change_log)

    def test_enter_on_the_same_value_logs_nothing(self, loaded_client, app):
        cur = loaded_client.get(f"/field/peek?dot_path={XY}.saturation.length").get_json()
        cur = cur["values"][f"{XY}.saturation.length"]
        before = len(self._log(app))
        r = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation", "dot_path": f"{XY}.saturation.length",
            "mode": "value", "value": str(cur)})       # the committed value
        assert r.status_code == 200
        assert len(self._log(app)) == before, "a no-op commit logged a change"
        assert b"pulses-changed" not in (r.headers.get("HX-Trigger") or "").encode()

    def test_a_real_change_still_logs(self, loaded_client, app):
        before = len(self._log(app))
        loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation", "dot_path": f"{XY}.saturation.length",
            "mode": "value", "value": "640"})
        assert len(self._log(app)) == before + 1

    def test_the_same_value_through_a_POINTER_is_also_a_no_op(self, loaded_client, app):
        # x90's length is a pointer at x180's; typing the RESOLVED value back
        # must not write to the target either.
        peek = loaded_client.get(f"/field/peek?dot_path={XY}.x90_DragCosine.length").get_json()
        res = peek["resolved"][f"{XY}.x90_DragCosine.length"]["resolved_value"]
        before = len(self._log(app))
        r = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.x90_DragCosine", "dot_path": f"{XY}.x90_DragCosine.length",
            "mode": "value", "value": str(res)})
        assert r.status_code == 200
        assert len(self._log(app)) == before

    def test_an_int_typed_as_a_float_of_the_same_value_is_not_swallowed(self, loaded_client, app):
        """600 -> 600.0 is a TYPE change on disk, so the no-op guard must not
        eat it: the field either takes it (a logged change) or refuses it with
        a message. What it may never do is answer 200 and record nothing."""
        cur = loaded_client.get(f"/field/peek?dot_path={XY}.saturation.length").get_json()
        cur = cur["values"][f"{XY}.saturation.length"]
        before = len(self._log(app))
        r = loaded_client.post("/pulse/edit", data={
            "path": f"{XY}.saturation", "dot_path": f"{XY}.saturation.length",
            "mode": "value", "value": f"{float(cur)}"})
        assert (len(self._log(app)) == before + 1) or r.status_code >= 400,             "a type change answered 200 and logged nothing"


class TestCreateRefusesAnUnknownTarget:
    """docs/190 F54: an unrecognised target_kind fell through to the qubit
    branch, so a typo (or a stale form) silently created a qubit pulse."""

    @pytest.mark.parametrize("kind", ["garbage", "QUBIT", "", "pair_channels", "qubit "])
    def test_unknown_kind_is_a_400_and_writes_nothing(self, loaded_client, app, kind):
        ctx = next(iter(app.config["contexts"].values()))
        ops = ctx["store"].state["qubits"]["qA1"]["xy"]["operations"]
        before = set(ops)
        r = loaded_client.post("/api/pulse/create", data={
            "pulse_type": "SquarePulse", "target_kind": kind,
            "qubit": "qA1", "channel": "xy", "op_name": "unknown_kind_probe",
            "length": "100", "amplitude": "0.1"})
        assert r.status_code == 400, (kind, r.status_code)
        assert b"target kind" in r.data
        assert set(ops) == before, f"{kind!r} created something"

    def test_the_three_real_kinds_are_not_refused(self, loaded_client, app):
        r = loaded_client.post("/api/pulse/create", data={
            "pulse_type": "SquarePulse", "target_kind": "qubit",
            "qubit": "qA1", "channel": "xy", "op_name": "known_kind_probe",
            "length": "100", "amplitude": "0.1"})
        assert r.status_code == 200, r.data[:200]


class TestTheDetailPutsValuesWhereTheyCanBeRead:
    """docs/190 F18/F20/F53/N10 (stress round 2026-09-17), all measured in real
    Chrome first: a fixed 260px plot left ZERO property rows on screen at
    1280x800 and 1000x700; the Gaussian CZ form was ~600px of full-width inputs
    for four 2-digit numbers with its only submit below the fold; and the one
    surface in the app whose editable values had no value-history clock."""

    def test_the_plot_height_follows_the_pane(self):
        import pathlib
        js = pathlib.Path("quam_state_manager/web/static/pulses.js").read_text(encoding="utf-8")
        assert "height: plotHeight()" in js
        i = js.index("function plotHeight")
        body = js[i:i + 400]
        assert "inspector-pane" in body and "PLOT_H_MAX" in body and "PLOT_H_MIN" in body
        # and a pane too short for both gets the VALUES first
        assert "pulse-detail-compact" in js and "COMPACT_PANE_H" in js
        css = pathlib.Path("quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
        assert ".pulse-detail-compact > #pulse-detail-plot { order: 9; }" in css
        assert ".pulse-detail-plot { height: auto;" in css

    def test_every_editable_pulse_value_carries_the_history_clock(self, loaded_client):
        html = loaded_client.get(f"/pulse/detail?path={XY}.saturation").data.decode()
        n_inputs = html.count('class="edit-input"')
        n_clocks = html.count('class="field-hist-btn"')
        assert n_inputs > 0 and n_clocks == n_inputs, (n_inputs, n_clocks)
        # the button must carry its own path: it sits OUTSIDE the inline-edit
        # form, so FieldHistory.openInspector (which reads the form) finds none
        assert 'data-dot-path="' in html
        assert "PulsesPage.openFieldHistory(this)" in html

    def test_the_gaussian_cz_form_is_a_row_with_a_reachable_submit(self):
        # the fixture chip has no cz_flattop pair, so the FORM renders only on a
        # real chip; pin the shipped template and its rules instead
        import pathlib
        html = pathlib.Path("quam_state_manager/web/templates/_pulse_gaussian_cz.html").read_text(encoding="utf-8")
        assert "gcz-params-row" in html          # four short numbers on one row
        assert "gcz-about" in html               # the six-line paragraph folds away
        css = pathlib.Path("quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
        assert ".gcz-params-row { display: flex;" in css
        assert "#gcz-root button[type=submit].btn-sm { width: auto; }" in css
        assert "#gcz-root .inspector-header-line { display: flex;" in css



# ---------------------------------------------------------------------------
# 2026-09-27: a gate slot is filled the way the chip lays out its own gates --
# the pulse is an operation of the channel that plays it, the slot links to it
# ---------------------------------------------------------------------------

def _slot_state() -> dict:
    return {
        "qubits": {
            "q1": {"id": "q1", "f_01": 5.2e9,
                   "z": {"__class__": "quam_builder.architecture.superconducting.components.flux_line.FluxLine",
                         "operations": {"cz_old_flux_pulse_q1_q2": {"amplitude": 0.1, "length": 40}}},
                   "xy": {"operations": {}}},
            "q2": {"id": "q2", "f_01": 4.9e9, "z": {"operations": {}},
                   "xy": {"operations": {}}},
        },
        "qubit_pairs": {
            "q1-q2": {
                "qubit_control": "#/qubits/q1",
                "qubit_target": "#/qubits/q2",
                "moving_qubit": "control",
                "coupler": {"operations": {}},
                "macros": {
                    "cz_empty": {"flux_pulse_qubit": None, "coupler_flux_pulse": None},
                    "cz_old": {"flux_pulse_qubit": "#/qubits/q1/z/operations/cz_old_flux_pulse_q1_q2",
                               "coupler_flux_pulse": None},
                },
            },
        },
    }


@pytest.fixture
def slot_client(tmp_path):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_slot_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    c._app = app
    return c


def _slot_store(c):
    return next(iter(c._app.config["contexts"].values()))["store"]


class TestGateSlotFillFollowsTheChipLayout:
    def _island(self, html):
        return json.loads(html.split('id="pulse-pairs-info-data"'
                                     ' type="application/json">')[1]
                          .split("</script>")[0])

    def test_a_linked_slot_is_held_by_the_pulse_it_links_to(self, slot_client):
        info = self._island(slot_client.get("/pulse/new").data.decode())
        slots = info["q1-q2"]["gates"]["cz_old"]["slots"]
        assert slots["flux_pulse_qubit"]["state"] == "held"
        assert slots["flux_pulse_qubit"]["path"] == \
            "qubits.q1.z.operations.cz_old_flux_pulse_q1_q2"
        empty = info["q1-q2"]["gates"]["cz_empty"]["slots"]
        assert empty["flux_pulse_qubit"]["state"] == "empty"
        assert empty["coupler_flux_pulse"]["state"] == "empty"

    def test_a_linked_slot_is_refused_as_held(self, slot_client):
        r = slot_client.post("/api/pulse/create", data={
            "target_kind": "pair", "pair": "q1-q2", "gate": "cz_old",
            "slot": "flux_pulse_qubit", "pulse_type": "SquarePulse",
            "length": "40", "amplitude": "0.1"})
        assert r.status_code == 409 and b"already holds" in r.data

    def test_the_flux_slot_pulse_lands_on_the_moving_qubit_z(self, slot_client):
        r = slot_client.post("/api/pulse/create", data={
            "target_kind": "pair", "pair": "q1-q2", "gate": "cz_empty",
            "slot": "flux_pulse_qubit", "pulse_type": "SquarePulse",
            "length": "48", "amplitude": "0.12"})
        assert r.status_code == 200, r.data[:300]
        st = _slot_store(slot_client).state
        op = st["qubits"]["q1"]["z"]["operations"]["cz_empty_flux_pulse_q1_q2"]
        assert op["amplitude"] == 0.12 and op["length"] == 48
        assert st["qubit_pairs"]["q1-q2"]["macros"]["cz_empty"]["flux_pulse_qubit"] == \
            "#/qubits/q1/z/operations/cz_empty_flux_pulse_q1_q2"
        # one Ctrl+Z takes both back
        s = _slot_store(slot_client)
        gids = {e.group_id for e in s.change_log}
        assert len(gids) == 1

    def test_the_coupler_slot_pulse_lands_on_the_coupler(self, slot_client):
        r = slot_client.post("/api/pulse/create", data={
            "target_kind": "pair", "pair": "q1-q2", "gate": "cz_empty",
            "slot": "coupler_flux_pulse", "pulse_type": "SquarePulse",
            "length": "48", "amplitude": "0.02"})
        assert r.status_code == 200, r.data[:300]
        st = _slot_store(slot_client).state
        pair = st["qubit_pairs"]["q1-q2"]
        assert pair["coupler"]["operations"]["cz_empty_coupler_flux_pulse_q1_q2"]["amplitude"] == 0.02
        assert pair["macros"]["cz_empty"]["coupler_flux_pulse"] == \
            "#/qubit_pairs/q1-q2/coupler/operations/cz_empty_coupler_flux_pulse_q1_q2"


class TestNoIqPulseOnASingleChannel:
    """2026-09-27 (measured on the KRS 5Q chip): an IQ waveform on a single
    (LF) channel makes quam's generate_config() raise ``Waveform type 'IQ'
    not allowed for SingleChannel`` for the WHOLE machine."""

    def test_drag_on_a_flux_line_is_refused(self, slot_client):
        r = slot_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "q1", "channel": "z",
            "op_name": "bad_drag", "pulse_type": "DragCosinePulse",
            "length": "40", "amplitude": "0.1", "alpha": "0", "axis_angle": "0",
            "anharmonicity": "-2e8"})
        assert r.status_code == 400 and b"single-output" in r.data
        assert "bad_drag" not in _slot_store(slot_client).state["qubits"]["q1"]["z"]["operations"]

    def test_an_axis_angle_on_a_flux_line_is_refused(self, slot_client):
        r = slot_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "q1", "channel": "z",
            "op_name": "sq_ax", "pulse_type": "SquarePulse",
            "length": "40", "amplitude": "0.1", "axis_angle": "0.5"})
        assert r.status_code == 400

    def test_a_plain_square_on_a_flux_line_is_fine(self, slot_client):
        r = slot_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "q1", "channel": "z",
            "op_name": "sq", "pulse_type": "SquarePulse",
            "length": "40", "amplitude": "0.1"})
        assert r.status_code == 200, r.data[:300]


class TestCopyAPulseToAnotherChannel:
    def test_copy_follows_the_chip_layout(self, slot_client):
        st = _slot_store(slot_client).state
        st["qubits"]["q1"]["anharmonicity"] = -2.0e8
        st["qubits"]["q2"]["anharmonicity"] = -2.1e8
        st["qubits"]["q1"]["xy"]["operations"].update({
            "x180_D": {"amplitude": 0.3, "length": 40,
                       "anharmonicity": "#/qubits/q1/anharmonicity",
                       "detuning": "#../x90_D/detuning",
                       "global_ref": "#/qubit_pairs/q1-q2/moving_qubit",
                       "self_len": "#./length"},
            "x90_D": {"amplitude": 0.15, "length": 40, "detuning": 1.0e6},
            "x180": "#./x180_D",
        })
        r = slot_client.post("/api/pulse/copy", data={
            "path": "qubits.q1.xy.operations.x180", "target_kind": "qubit",
            "qubit": "q2", "channel": "xy", "op_name": "x180_D"})
        assert r.status_code == 200, r.data[:300]
        cp = st["qubits"]["q2"]["xy"]["operations"]["x180_D"]
        assert cp["amplitude"] == 0.3
        assert cp["anharmonicity"] == "#/qubits/q2/anharmonicity"  # its OWN qubit
        assert cp["detuning"] == 1.0e6        # q2.xy has no x90_D: the value
        assert cp["global_ref"] == "#/qubit_pairs/q1-q2/moving_qubit"
        assert cp["self_len"] == "#./length"
        src = st["qubits"]["q1"]["xy"]["operations"]           # source untouched
        assert src["x180"] == "#./x180_D"
        assert src["x180_D"]["anharmonicity"] == "#/qubits/q1/anharmonicity"
        assert b"written as values" in r.data and b"detuning" in r.data

    def test_copy_refuses_an_existing_name_and_a_missing_source(self, slot_client):
        r = slot_client.post("/api/pulse/copy", data={
            "path": "qubits.q1.z.operations.cz_old_flux_pulse_q1_q2",
            "target_kind": "qubit", "qubit": "q1", "channel": "z",
            "op_name": "cz_old_flux_pulse_q1_q2"})
        assert r.status_code == 409
        r = slot_client.post("/api/pulse/copy", data={
            "path": "qubits.q1.z.operations.nope", "target_kind": "qubit",
            "qubit": "q2", "channel": "z", "op_name": "x"})
        assert r.status_code == 400

    def test_copy_of_an_iq_pulse_onto_a_flux_line_is_refused(self, slot_client):
        st = _slot_store(slot_client).state
        st["qubits"]["q1"]["xy"]["operations"]["d"] = {
            "__class__": "quam_builder.architecture.superconducting.components.pulses.DragCosinePulse",
            "amplitude": 0.1, "length": 40, "axis_angle": 0.0}
        r = slot_client.post("/api/pulse/copy", data={
            "path": "qubits.q1.xy.operations.d", "target_kind": "qubit",
            "qubit": "q1", "channel": "z", "op_name": "d"})
        assert r.status_code == 400 and b"single-output" in r.data


class TestOneCreateButton:
    """2026-09-27 (user): "버튼이 2개였던 것 같다. 2개로 하지 말고 하나로 통일해"."""

    def test_the_toolbar_has_one_create_button(self, slot_client):
        html = slot_client.get("/pulses", headers={"HX-Request": "true"}).data.decode()
        assert html.count('hx-get="/pulse/new"') >= 1
        assert "pulse-gcz-btn" not in html
        assert "+ Gaussian CZ" not in html

    def test_every_create_kind_carries_the_same_choice(self, slot_client):
        for url, kind in (("/pulse/new", "pulse"), ("/pulse/new?kind=copy", "copy"),
                          ("/pulse/new?kind=gaussian_cz", "gaussian_cz"),
                          ("/pulse/gaussian-cz", "gaussian_cz")):
            html = slot_client.get(url).data.decode()
            assert 'id="pulse-create-choice"' in html, url
            for k in ("pulse", "copy", "gaussian_cz"):
                assert f'data-create-kind="{k}"' in html, (url, k)
            lit = html.split('aria-selected="true"')[0].rsplit('data-create-kind="', 1)[1]
            assert lit.startswith(kind), (url, lit[:20])


class TestANewClassReprobesTheEnv:
    """2026-09-27: a pulse of a class new to the chip raised "N errors ...
    would crash a node run (harvest drift)" on Diagnostics for a class the
    env imports fine -- nothing re-probed until the chip was reopened."""

    def _spy(self, monkeypatch, manifest):
        import quam_state_manager.web.routes as rm
        kicked = []
        monkeypatch.setattr(rm, "_live_env_manifest", lambda store: manifest)
        monkeypatch.setattr(rm, "_kick_env_reprobe",
                            lambda *a, **k: kicked.append(a))
        return kicked

    def test_a_class_the_manifest_never_probed_kicks_one_reprobe(
            self, slot_client, monkeypatch):
        kicked = self._spy(monkeypatch, {"classes": {}})
        r = slot_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "q2", "channel": "xy",
            "op_name": "g1", "pulse_type": "GaussianPulse",
            "length": "40", "amplitude": "0.1", "sigma": "8"})
        assert r.status_code == 200, r.data[:300]
        assert len(kicked) == 1

    def test_a_fully_probed_chip_is_left_alone(self, slot_client, monkeypatch):
        from quam_state_manager.core import state_env_schema
        st = _slot_store(slot_client).state
        kicked = self._spy(monkeypatch, None)
        slot_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "q2", "channel": "xy",
            "op_name": "g2", "pulse_type": "GaussianPulse",
            "length": "40", "amplitude": "0.1", "sigma": "8"})
        known = {c: {"importable": True}
                 for c in state_env_schema.harvest_classes(st)}
        kicked = self._spy(monkeypatch, {"classes": known})
        slot_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "q2", "channel": "xy",
            "op_name": "g3", "pulse_type": "GaussianPulse",
            "length": "40", "amplitude": "0.1", "sigma": "8"})
        assert kicked == []


class TestALabClassIsCheckedByItsOwnCode:
    """2026-09-27 (measured on the KRS 5Q chip): GaussianNZTwoFluxPulse's own
    waveform code refuses a flat_length under 12 sigma of its filter, and a
    pulse SM wrote with such values made generate_config() raise for the
    WHOLE chip. The create runs the class's own code first."""

    LAB = "mylab.g.NZPulse"

    @pytest.fixture
    def lab_env(self, slot_client, monkeypatch):
        from quam_state_manager.core import config_generator, lab_waveform, pulse_catalog
        pulse_catalog.apply_env_overlay(None)
        pulse_catalog.apply_chip_classes({self.LAB: {
            "importable": True, "canonical": self.LAB,
            "bases": ["quam.components.pulses.Pulse"],
            "fields": {"amplitude": {"type": {"base": "float"}, "has_default": False},
                       "flat_length": {"type": {"base": "int"}, "has_default": False}}}})
        calls = []
        answer = {"rec": {"ok": True}}
        monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: "py.exe")

        def draw(py, items, spawn=True):
            calls.append(items)
            return [dict(answer["rec"])]
        monkeypatch.setattr(lab_waveform, "draw", draw)
        yield calls, answer
        pulse_catalog.apply_chip_classes(None)

    def _post(self, c, name):
        return c.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "q1", "channel": "z", "op_name": name,
            "pulse_type": "NZPulse", "amplitude": "0.15", "flat_length": "32"})

    def test_a_value_the_class_refuses_is_not_written(self, slot_client, lab_env):
        calls, answer = lab_env
        answer["rec"] = {"ok": False, "error": "ValueError: half the flat length is below 6*sigma"}
        r = self._post(slot_client, "nz_bad")
        assert r.status_code == 400 and b"6*sigma" in r.data
        assert calls and calls[0][0][0] == self.LAB
        assert calls[0][0][1]["flat_length"] == 32
        assert "nz_bad" not in _slot_store(slot_client).state["qubits"]["q1"]["z"]["operations"]

    def test_values_it_draws_are_written(self, slot_client, lab_env):
        r = self._post(slot_client, "nz_ok")
        assert r.status_code == 200, r.data[:300]

    def test_an_uncheckable_class_is_not_blocked(self, slot_client, lab_env):
        calls, answer = lab_env
        answer["rec"] = {"ok": False, "reason": "run-failed", "error": "env gone"}
        assert self._post(slot_client, "nz_unchecked").status_code == 200
