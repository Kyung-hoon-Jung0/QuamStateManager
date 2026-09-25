"""Z-line distortion page (/zline, /zline/data): the route layer.

The physics is pinned in test_zline_filters.py; here: the sidebar entry, the
table, the JSON a line hands the plots, the honest refusals reaching the page,
and that the content-keyed memo can never serve a curve for filters the chip
no longer has (a randomized edit sequence against a cold recompute).
"""
from __future__ import annotations

import json
import random

import pytest

from quam_state_manager.core import zline_filters as zf
from quam_state_manager.web.app import create_app


def _chip():
    ops = {"const": {"length": 40, "amplitude": 0.3, "__class__": "quam.components.pulses.SquarePulse"},
           "cz_flux": {"length": 20, "amplitude": -0.2, "__class__": "quam.components.pulses.SquarePulse"}}
    state = {
        "qubits": {
            "q1": {"id": "q1", "z": {"opx_output": "#/wiring/qubits/q1/z/opx_output", "operations": ops}},
            "q2": {"id": "q2", "z": {"opx_output": "#/wiring/qubits/q2/z/opx_output", "operations": {}}},
            "q3": {"id": "q3", "z": {"opx_output": "#/wiring/qubits/q3/z/opx_output", "operations": {}}},
        },
        "qubit_pairs": {"q1-2": {"id": "q1-2", "coupler": {"opx_output": "#/ports/analog_outputs/con1/5/4",
                                                          "operations": {}}}},
        "ports": {"analog_outputs": {"con1": {"5": {
            "1": {"exponential_filter": [[-0.05, 20.0], [-0.02, 400.0]],
                  "feedforward_filter": [0.6, 0.3, 0.1], "sampling_rate": 1e9, "upsampling_mode": "pulse"},
            "2": {"exponential_filter": [[-1.5, 50.0]], "feedforward_filter": [1.0]},
            "4": {"feedforward_filter": []},
        }}}},
    }
    wiring = {"wiring": {"qubits": {
        "q1": {"z": {"opx_output": "#/ports/analog_outputs/con1/5/1"}},
        "q2": {"z": {"opx_output": "#/ports/analog_outputs/con1/5/2"}},
        "q3": {"z": {"opx_output": "#/ports/analog_outputs/con1/5/9"}},     # dangling
    }}}
    return state, wiring


@pytest.fixture
def client(tmp_path):
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    s, w = _chip()
    (live / "state.json").write_text(json.dumps(s), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(w), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return c


def _data(c, line, **kw):
    q = "&".join(f"{k}={v}" for k, v in kw.items())
    r = c.get(f"/zline/data?line={line}" + ("&" + q if q else ""))
    return r.status_code, r.get_json()


class TestPage:
    def test_sidebar_entry_under_live_state_edit(self, client):
        html = client.get("/zline").get_data(as_text=True)
        i_p, i_z = html.index('href="/pulses"'), html.index('href="/zline"')
        assert i_p < i_z and "Z-line distortion" in html
        assert 'id="live-edit-subnav"' in html and "nav-subitems-collapsed" not in html.split('id="live-edit-subnav"')[0][-200:]

    def test_table_lists_every_flux_line_with_its_state(self, client):
        html = client.get("/zline", headers={"HX-Request": "true"}).get_data(as_text=True)
        for line in ("qubits.q1.z", "qubits.q2.z", "qubits.q3.z", "qubit_pairs.q1-2.coupler"):
            assert f'data-line="{line}"' in html
        assert "UNSTABLE" in html and "DANGLING" in html
        assert 'data-selected="qubits.q1.z"' in html          # first usable line

    def test_line_param_selects(self, client):
        html = client.get("/zline?line=qubits.q2.z", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'data-selected="qubits.q2.z"' in html


class TestData:
    def test_good_line_ships_both_figures(self, client):
        st, d = _data(client, "qubits.q1.z")
        assert st == 200 and d["ok"] and d["port_path"] == "ports.analog_outputs.con1.5.1"
        assert d["op"] == "const" and d["ops"] == ["const", "cz_flux"]
        s = d["step"]
        assert len(s["t_ns"]) == len(s["both"]) == len(s["iir_only"]) == len(s["fir_only"])
        assert s["dc_limit"] == pytest.approx(1.0)
        p = d["pulse"]
        assert max(p["ideal"]) == pytest.approx(0.3) and p["peak"] > 0.3   # the edge overshoot
        assert p["t_ns"][1] - p["t_ns"][0] == zf.TS_NS

    def test_op_and_model_switch(self, client):
        _, a = _data(client, "qubits.q1.z", op="cz_flux")
        assert a["op"] == "cz_flux" and min(a["pulse"]["ideal"]) == pytest.approx(-0.2)
        _, s = _data(client, "qubits.q1.z", model="sum")
        _, c = _data(client, "qubits.q1.z", model="cascade")
        assert s["step"]["both"] != c["step"]["both"]

    def test_unstable_line_gives_notes_no_curve(self, client):
        _, d = _data(client, "qubits.q2.z")
        assert d["step"] is None and d["pulse"] is None
        assert "unstable" in [n["code"] for n in d["notes"] if n["level"] == "block"]

    def test_dangling_line_gives_notes_no_curve(self, client):
        _, d = _data(client, "qubits.q3.z")
        assert d["step"] is None and [n["code"] for n in d["notes"]] == ["dangling"]

    def test_empty_taps_coupler_is_identity_with_note(self, client):
        _, d = _data(client, "qubit_pairs.q1-2.coupler")
        assert d["step"]["both"] == d["step"]["ideal"]
        assert {"ff_empty", "no_filters"} & {n["code"] for n in d["notes"]}
        assert d["pulse"] is None                              # no operations

    def test_unknown_line_is_404(self, client):
        st, d = _data(client, "qubits.q9.z")
        assert st == 404 and not d["ok"]


class TestNeverStale:
    """The memo keys on content; an edit between two reads must change the
    answer exactly as a cold recompute does. Randomized edit sequence."""

    def _store(self, client):
        from quam_state_manager.web import routes
        with client.application.test_request_context("/"):
            pass
        ctxs = client.application.config["contexts"]
        return next(iter(ctxs.values()))["store"]

    def test_randomized_edits_match_cold(self, client):
        store = self._store(client)
        rng = random.Random(20260926)
        port = store.merged["ports"]["analog_outputs"]["con1"]["5"]["1"]
        for step in range(12):
            with store._lock:
                k = rng.choice(["amp", "tau", "tap", "op"])
                if k == "amp":
                    port["exponential_filter"][0][0] = round(rng.uniform(-0.3, 0.3), 3)
                elif k == "tau":
                    port["exponential_filter"][1][1] = round(rng.uniform(5, 900), 1)
                elif k == "tap":
                    port["feedforward_filter"][1] = round(rng.uniform(-0.5, 0.5), 3)
                else:
                    store.merged["qubits"]["q1"]["z"]["operations"]["const"]["amplitude"] = round(rng.uniform(-0.4, 0.4), 3)
            _, d = _data(client, "qubits.q1.z")
            pf, _n = zf.parse_port_filter(json.loads(json.dumps(port)))
            if pf is None:
                assert d["step"] is None
                continue
            cold = zf.step_response(pf)
            assert d["step"]["both"] == cold["both"], step
            amp = store.merged["qubits"]["q1"]["z"]["operations"]["const"]["amplitude"]
            assert max(d["pulse"]["ideal"], key=abs) == pytest.approx(amp), step
            assert d["pulse"]["both"] == zf.pulse_response(pf, [amp] * 40)["both"], step
