"""What the CZ plan and the Pulses page draw for a gate slot that stores its own
pulse copy, and three drawing rules around it.

(1) quam_builder's CZGate plays ``get_pulse_name(flux_pulse_qubit)`` on the
moving qubit's z, which is the pulse's ``id`` when one is set -- the channel op
of that name, not the dict stored in the macro. When the copy and the played op
differ, the plan and the Pulses page draw the played op and say both names.
(2) A shaped flux pulse whose edges start far from 0 V says so, and the plot
axis always includes 0 V.
(3) A class added to the chip after the env was probed is looked up in the same
env's catalogue; the SNZ description follows its class's fields; Verify vs
config re-runs after a regenerate (pulses_played_selfcheck.cjs).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import cz_gate_plan as czp
from quam_state_manager.core import state_env_validate as sev
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_BIP = "quam.components.pulses._CosineBipolarPulse"
SLOT = "qubit_pairs.q1-q2.macros.cz_bipolar.flux_pulse_qubit"
PLAYED = "qubits.q1.z.operations.cz_bipolar_pulse_q2"
_MACRO = "#/qubit_pairs/q1-q2/macros/cz_bipolar/flux_pulse_qubit/"


def _state(*, same=False, pointer=False) -> dict:
    """q1 moves; the macro stores a 52 ns square-edged bipolar copy whose id names
    a channel op with cosine edges (smoothing = the copy's length): 104 ns played.
    *same*: the channel op equals the copy. *pointer*: the slot points at the op."""
    copy_pulse = {"length": "#./inferred_total_length", "id": "cz_bipolar_pulse_q2",
                  "digital_marker": None, "amplitude": 0.23, "axis_angle": None,
                  "flat_length": 50, "smoothing_length": 0, "post_zero_padding_length": 0,
                  "__class__": _BIP}
    played = {"length": "#./inferred_total_length", "id": None, "digital_marker": None,
              "amplitude": _MACRO + "amplitude", "axis_angle": None,
              "flat_length": _MACRO + "flat_length",
              "smoothing_length": _MACRO + "length",
              "post_zero_padding_length": 0, "__class__": _BIP}
    if same:
        played = dict(copy_pulse, id=None)
    gaussian = {"pulse_length": 30, "padding_length": 20, "amplitude": 0.2,
                "gaussian_filter_frequency_mhz": 20.0, "sample_rate": 1e9, "axis_angle": None,
                "length": "#./inferred_length", "id": None, "digital_marker": None,
                "__class__": "quam_builder.common.pulses.GaussianFilteredSquarePulse"}
    return {
        "qubits": {
            "q1": {"f_01": 5.0e9, "anharmonicity": -200e6,
                   "z": {"operations": {"cz_bipolar_pulse_q2": played, "cz_gauss": gaussian,
                                        "const": {"amplitude": 0.1, "length": 100,
                                                  "__class__": "quam.components.pulses.SquarePulse"}}},
                   "xy": {"operations": {"x180": {"amplitude": 0.2, "length": 40,
                                                  "__class__": "quam.components.pulses.SquarePulse"}}}},
            "q2": {"f_01": 4.8e9, "anharmonicity": -200e6, "z": {"operations": {}}},
        },
        "qubit_pairs": {
            "q1-q2": {
                "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                "moving_qubit": "control",
                "macros": {"cz_bipolar": {
                    "flux_pulse_qubit": ("#/qubits/q1/z/operations/cz_bipolar_pulse_q2" if pointer
                                         else copy_pulse),
                    "coupler_flux_pulse": None, "phase_shift_control": 0.0,
                    "phase_shift_target": 0.0, "duration": "#./inferred_duration",
                    "__class__": ("quam_builder.architecture.superconducting.custom_gates."
                                  "flux_tunable_transmon_pair.two_qubit_gates.CZGate")}},
            },
        },
        "active_qubit_names": ["q1", "q2"],
    }


# ---------------------------------------------------------------- core rule

class TestWhichOpPlays:
    def test_a_copy_with_an_id_plays_the_channel_op_of_that_name(self):
        assert czp.slot_played_op(_state(), SLOT) == PLAYED

    def test_the_note_names_both_pulses_and_how_they_differ(self):
        note = czp.slot_copy_note(_state(), SLOT, PLAYED)
        assert note and PLAYED in note and SLOT in note
        assert "length 104 ns played vs 52 ns in the copy" in note
        assert "smoothing_length 52 played vs 0 in the copy" in note

    def test_a_pointer_slot_is_not_a_copy(self):
        assert czp.slot_played_op(_state(pointer=True), SLOT) is None

    def test_a_copy_equal_to_the_played_op_has_no_note(self):
        doc = _state(same=True)
        assert czp.slot_played_op(doc, SLOT) == PLAYED
        assert czp.slot_copy_note(doc, SLOT, PLAYED) is None

    def test_a_copy_without_an_id_names_no_channel_op(self):
        doc = _state()
        doc["qubit_pairs"]["q1-q2"]["macros"]["cz_bipolar"]["flux_pulse_qubit"]["id"] = None
        assert czp.slot_played_op(doc, SLOT) is None

    def test_the_plan_draws_the_played_op_and_says_the_copy_differs(self):
        rows, _ = czp.operations(_state())
        row = next(r for r in rows if r["operation"] == "cz_bipolar")
        assert row["lines"][0]["pulse_path"] == PLAYED
        assert row["duration_ns"] == 104
        assert any("the gate plays " + PLAYED in n and "104 ns played vs 52 ns" in n for n in row["notes"])

    @pytest.mark.parametrize("kw", [{"same": True}, {"pointer": True}])
    def test_the_plan_says_nothing_when_nothing_differs(self, kw):
        rows, _ = czp.operations(_state(**kw))
        row = next(r for r in rows if r["operation"] == "cz_bipolar")
        assert row["lines"][0]["pulse_path"] == PLAYED
        assert not any("the gate plays" in n for n in row["notes"])


class TestEdgeStep:
    def test_a_shaped_edge_cut_short_is_reported(self):
        note = czp.edge_step_note([0.046, 0.1, 0.2, 0.2, 0.1, 0.046])
        assert note and "+46.0 mV" in note and "23%" in note and "200.0 mV peak" in note

    @pytest.mark.parametrize("samples", [
        [0.2, 0.2, 0.2],                    # a square edge is the pulse's own step
        [0.23, 0.23, -0.23, -0.23],         # a square bipolar too
        [0.0, 0.1, 0.2, 0.1, 0.001],        # a smooth edge from 0 V
        [], [0.0, 0.0],
    ])
    def test_square_or_smooth_edges_say_nothing(self, samples):
        assert czp.edge_step_note(samples) is None


# ---------------------------------------------------------------- routes

@pytest.fixture
def client_for(tmp_path):
    def make(**kw):
        d = tmp_path / ("chip_" + "_".join(sorted(kw)) if kw else "chip")
        d.mkdir()
        (d / "state.json").write_text(json.dumps(_state(**kw)), encoding="utf-8")
        (d / "wiring.json").write_text("{}", encoding="utf-8")
        app = create_app(testing=True, instance_path=str(d / "_app_instance"))
        c = app.test_client()
        r = c.post("/load", data={"folder": str(d)})
        assert r.status_code in (200, 302), r.data[:300]
        return c
    return make


def _synth(c, path, params=None):
    return c.post("/api/pulse/synth", json={"path": path, "params": params or {}}).get_json()


def _detail(c, path):
    html = c.get("/pulse/detail?path=" + path).data.decode()
    m = re.search(r'<script id="pulse-detail-data"[^>]*>(.*?)</script>', html, re.S)
    assert m
    return html, json.loads(m.group(1))


class TestThePulsesPageDrawsWhatPlays:
    def test_synth_of_the_copy_is_the_played_op(self, client_for):
        d = _synth(client_for(), SLOT)
        assert d["ok"] and d["played_path"] == PLAYED and "104 ns played" in d["played_note"]
        assert d["plot"]["length"] == 104

    def test_a_typed_copy_value_reaches_the_played_op(self, client_for):
        # the played op reads flat_length and (through the copy's length) its
        # smoothing from the copy: flat 60 -> smoothing 60 -> 120 samples
        d = _synth(client_for(), SLOT, {"flat_length": 60})
        assert d["ok"] and d["played_path"] == PLAYED and d["plot"]["length"] == 120

    def test_detail_draws_the_played_op_with_the_note(self, client_for):
        html, data = _detail(client_for(), SLOT)
        assert data["pulses"][0]["plot_source"] == "played"
        assert data["pulses"][0]["plot"]["length"] == 104
        assert 'data-played-note="1"' in html and "Macro pulse and played op differ" in html
        assert "the op the gate plays: <code>" + PLAYED + "</code>" in html

    def test_the_table_row_draws_the_played_op(self, client_for):
        html = client_for().get("/pulse/row?path=" + SLOT).data.decode()
        assert 'data-spark-played="1"' in html and PLAYED in html

    @pytest.mark.parametrize("kw", [{"same": True}])
    def test_a_copy_that_equals_the_played_op_draws_as_before(self, client_for, kw):
        c = client_for(**kw)
        d = _synth(c, SLOT)
        assert d["ok"] and d["played_path"] is None and d["played_note"] is None
        html, data = _detail(c, SLOT)
        assert data["pulses"][0]["plot_source"] == "synth" and "data-played-note" not in html
        assert 'data-spark-played' not in c.get("/pulse/row?path=" + SLOT).data.decode()


class TestFluxEdgeNote:
    def test_a_cut_gaussian_flux_pulse_says_where_its_edges_are(self, client_for):
        c = client_for()
        d = _synth(c, "qubits.q1.z.operations.cz_gauss")
        assert d["ok"] and d["plot"]["edge_note"] and "% of the" in d["plot"]["edge_note"]
        html, _ = _detail(c, "qubits.q1.z.operations.cz_gauss")
        m = re.search(r'<span class="pulse-synth-err" data-edge-note="1"\s*>([^<]+)<', html)
        assert m and "steps from 0 V" in m.group(1)

    def test_a_square_flux_pulse_and_an_xy_pulse_say_nothing(self, client_for):
        c = client_for()
        assert _synth(c, "qubits.q1.z.operations.const")["plot"].get("edge_note") is None
        assert "edge_note" not in _synth(c, "qubits.q1.xy.operations.x180")["plot"]


# ---------------------------------------------------------------- env schema lookup, SNZ text

class TestCatalogueAnswersANewClass:
    _CLS = "quam_builder.common.pulses.GaussianFilteredSquarePulse"

    def _manifest(self):
        f = {"type": {"base": "int"}, "optional": False, "has_default": False}
        fl = {"type": {"base": "float"}, "optional": False, "has_default": False}
        entry = {"importable": True, "canonical": self._CLS, "fields": {
            "pulse_length": f, "padding_length": dict(f, has_default=True, default=0),
            "amplitude": fl, "gaussian_filter_frequency_mhz": fl,
            "sample_rate": dict(fl, has_default=True, default=1e9),
            "axis_angle": dict(fl, optional=True, has_default=True, default=None),
            "length": dict(f, has_default=True, default="#./inferred_length"),
            "id": {"type": {"base": "str"}, "optional": True, "has_default": True, "default": None},
            "digital_marker": {"type": {"base": "any"}, "optional": True, "has_default": True, "default": None}}}
        return {"classes": {}, "versions": {"quam": "x"}}, entry

    def _node(self):
        return {"pulse": dict(_state()["qubits"]["q1"]["z"]["operations"]["cz_gauss"])}

    def test_without_a_catalogue_the_class_is_unchecked(self):
        m, _ = self._manifest()
        kinds = [f["kind"] for f in sev.analyze_state(self._node(), m)["findings"]]
        assert kinds == ["unknown_class"]

    def test_the_same_envs_catalogue_answers_it(self):
        m, entry = self._manifest()
        m["catalog"] = {self._CLS: entry}
        assert sev.analyze_state(self._node(), m)["findings"] == []

    def test_a_catalogue_changes_the_analysis_key(self):
        m, entry = self._manifest()
        before = sev._manifest_key(m)
        m["catalog"] = {self._CLS: entry}
        assert sev._manifest_key(m) != before


def test_the_snz_description_follows_its_fields():
    from quam_state_manager.core.pulse_catalog import by_qclass
    spec = by_qclass("quam_builder.architecture.superconducting.components.pulses.SNZPulse")
    doc, names = spec.doc, {p.name for p in spec.params}
    assert "B-sample" not in doc and "t_phi_eff" in names and "tphi_eff" in doc


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_pulses_played_selfcheck():
    if subprocess.run(["node", "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT),
                      timeout=300).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run(["node", str(_ROOT / "tests" / "pulses_played_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=300)
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ok -" in res.stdout and "FAIL" not in res.stdout + res.stderr


def test_the_plan_figure_axis_includes_zero():
    src = (_ROOT / "quam_state_manager" / "web" / "static" / "cz-plan.js").read_text(encoding="utf-8")
    block = src[src.index("if (ampCount) over.yaxis3"):]
    block = block[:block.index("};") + 2]
    assert "rangemode: 'tozero'" in block
