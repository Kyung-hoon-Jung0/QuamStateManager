"""docs/248 -- the dBm follows the pulse CLASS.

B-05 (agent validation campaign): a lab's area-normalised readout class stored
amplitude 0.0526 on an FSP -2 port; SM printed -27.6 dBm (the plain identity
``FSP + 20*log10|amp|``) while the waveform peaks 7 dB higher. The power is
now read off the synthesized waveform -- the golden-pinned transcription in
``waveform_synth`` -- and only for a class SM transcribed at its full dotted
home; anything else is a blank that says why.

Three groups of pins:

* [derived] which catalog classes peak exactly at ``|amplitude|``, and the
  DRAG centre conditions, checked numerically over randomized parameters;
* the annotation (``physical_units.amp_annotation``): unknown / lab / missing
  class -> blank + reason, known class -> the waveform peak, alias shape wins,
  the RAM P6 read set, LF sign;
* the surfaces: grid cell, ``/bulk/phys``, inspector, components tables.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core import physical_units
from quam_state_manager.core import pulse_catalog as pc
from quam_state_manager.core import waveform_synth as ws
from quam_state_manager.web.app import create_app

from tests.test_physical_units import _WIRING, _state, _write_chip

_QC = "quam.components.pulses."
_QB_ARCH = "quam_builder.architecture.superconducting.components.pulses."
#: a lab's own readout class (module + name invented for the test)
_LAB = "lab_pulses.LabAreaNormReadoutPulse"


def _peak(key: str, p: dict) -> float:
    """max |I + iQ| of the golden-pinned transcription."""
    z = np.asarray(ws.synthesize_raw(key, p), dtype=complex)
    return float(np.max(np.abs(z)))


# ---------------------------------------------------------------------------
# [derived] which classes peak at |amplitude|
# ---------------------------------------------------------------------------

class TestPeakEqualsAmplitude:
    @pytest.mark.parametrize("key", ["SquarePulse", "SquareReadoutPulse"])
    def test_constant_classes(self, key):
        rng = random.Random(1)
        for _ in range(300):
            amp = rng.choice([-1, 1]) * rng.uniform(1e-4, 1.0)
            ang = rng.choice([None, rng.uniform(-math.pi, math.pi)])
            p = {"amplitude": amp, "length": rng.randint(16, 20000), "axis_angle": ang}
            assert abs(_peak(key, p) / abs(amp) - 1.0) <= 1e-12

    @pytest.mark.parametrize("key", ["FlatTopGaussianPulse", "FlatTopCosinePulse",
                                     "FlatTopTanhPulse", "FlatTopBlackmanPulse"])
    def test_flat_tops_with_a_flat_part(self, key):
        rng = random.Random(2)
        for _ in range(200):
            fl, rf = rng.randint(1, 300), rng.randint(1, 40)
            amp = rng.uniform(-1, 1) or 0.3
            p = {"amplitude": amp, "length": fl + 2 * rf, "flat_length": fl,
                 "axis_angle": None}
            assert abs(_peak(key, p) / abs(amp) - 1.0) <= 1e-12

    def test_a_flat_top_without_a_flat_part_peaks_below(self):
        p = {"amplitude": 0.5, "length": 40, "flat_length": 0, "axis_angle": None}
        assert _peak("FlatTopGaussianPulse", p) / 0.5 < 1.0 - 1e-6

    def test_net_zero_and_filtered_classes(self):
        rng = random.Random(3)
        for _ in range(200):
            amp = rng.uniform(-1, 1) or 0.2
            fl = 2 * rng.randint(1, 60)
            snz = {"amplitude": amp, "flat_length": fl, "t_phi_eff": rng.uniform(0, 8),
                   "padding": rng.randint(0, 8), "axis_angle": None}
            snz["length"] = pc.inferred_length("SNZPulse", snz)
            assert abs(_peak("SNZPulse", snz) / abs(amp) - 1) <= 1e-12
            for key in ("GaussianFilteredSquarePulse",
                        "GaussianFilteredSymmetricBipolarPulse"):
                g = {"amplitude": amp, "pulse_length": fl,
                     "post_zero_padding_length": rng.randint(0, 20),
                     "gaussian_filter_frequency_mhz": rng.uniform(5, 300),
                     "sample_rate": 1e9, "axis_angle": None}
                g["length"] = pc.inferred_length(key, g)
                assert abs(_peak(key, g) / abs(amp) - 1) <= 1e-12, key
            cb = {"amplitude": amp, "flat_length": fl,
                  "length": fl + rng.randint(0, 60), "axis_angle": None}
            assert abs(_peak("CosineBipolarPulse", cb) / abs(amp) - 1) <= 1e-12
            sm, pad = rng.randint(0, 40), rng.randint(0, 20)
            dep = {"amplitude": amp, "flat_length": fl, "smoothing_length": sm,
                   "post_zero_padding_length": pad, "axis_angle": None,
                   "length": fl + sm + pad}
            assert abs(_peak("_CosineBipolarPulse", dep) / abs(amp) - 1) <= 1e-12
            sm2 = 2 * rng.randint(0, 20)
            ft = {"amplitude": amp, "flat_length": fl, "smoothing_length": sm2,
                  "post_zero_padding_length": pad, "axis_angle": None,
                  "length": fl + sm2 + pad}
            assert abs(_peak("_FlatTopGaussianPulse", ft) / abs(amp) - 1) <= 1e-12

    def test_gaussian_needs_no_subtraction_and_an_odd_length(self):
        rng = random.Random(4)
        for _ in range(200):
            L = 2 * rng.randint(4, 100) + 1
            sig = rng.uniform(L / 8, L / 3)
            base = {"amplitude": 0.4, "length": L, "sigma": sig, "axis_angle": None}
            assert abs(_peak("GaussianPulse", dict(base, subtracted=False)) / 0.4 - 1) <= 1e-12
            # subtracted: peak = amp * (1 - g_end), strictly below
            c = (L - 1) / 2
            g_end = math.exp(-(c ** 2) / (2 * sig ** 2))
            r = _peak("GaussianPulse", dict(base, subtracted=True)) / 0.4
            assert abs(r - (1 - g_end)) <= 1e-12 and r < 1
            # an even length has no centre sample
            assert _peak("GaussianPulse", dict(base, length=L + 1, subtracted=False)) / 0.4 < 1


class TestDragCentreCondition:
    """[derived] when the DRAG |I+iQ| maximum stays at the pulse centre."""

    def test_drag_cosine(self):
        """|z|^2 = (A/2)^2 [(1-cos th)^2 + k^2 sin^2 th] with
        k = alpha*1e9 / ((L-1)(anh-det)): the centre (r = 1) iff k^2 <= 2,
        else r = k^2 / (2 sqrt(k^2-1)) in continuous time (sampling lowers it)."""
        rng = random.Random(5)
        for _ in range(1500):
            L = 2 * rng.randint(5, 150) + 1          # odd: the centre is a sample
            anh, det = rng.uniform(-400e6, -50e6), rng.uniform(-30e6, 30e6)
            k = rng.uniform(0, 3) * rng.choice([1, -1])
            alpha = k * (L - 1) * (anh - det) / 1e9
            r = _peak("DragCosinePulse", {"amplitude": 0.37, "length": L, "alpha": alpha,
                                          "anharmonicity": anh, "detuning": det,
                                          "axis_angle": rng.uniform(-3, 3)}) / 0.37
            if k * k <= 2:
                assert abs(r - 1) <= 1e-12, (k, r)
            else:
                assert 1 - 1e-12 <= r <= k * k / (2 * math.sqrt(k * k - 1)) * (1 + 1e-12)
        for k in (1.5, 2.0, 3.0):                     # fine sampling reaches it
            L, anh = 4001, -2e8
            r = _peak("DragCosinePulse", {"amplitude": 1.0, "length": L,
                                          "alpha": k * (L - 1) * anh / 1e9,
                                          "anharmonicity": anh, "detuning": 0.0,
                                          "axis_angle": 0.0})
            assert abs(r - k * k / (2 * math.sqrt(k * k - 1))) < 1e-6

    def test_drag_gaussian_unsubtracted(self):
        """|z|^2 = A^2 e^{-x^2} (1 + kappa^2 x^2), kappa = alpha*1e9 /
        (2 pi sigma (anh-det)): the centre iff kappa^2 <= 1, else
        r = kappa exp(-(kappa^2-1) / (2 kappa^2))."""
        rng = random.Random(6)
        for _ in range(1500):
            L = 2 * rng.randint(5, 150) + 1
            sig = rng.uniform(L / 8, L / 4)
            anh, det = rng.uniform(-400e6, -50e6), rng.uniform(-30e6, 30e6)
            kap = rng.uniform(0, 2.5)
            alpha = kap * 2 * math.pi * sig * (anh - det) / 1e9 * rng.choice([1, -1])
            r = _peak("DragGaussianPulse", {"amplitude": 0.37, "length": L, "sigma": sig,
                                            "alpha": alpha, "anharmonicity": anh,
                                            "detuning": det, "subtracted": False,
                                            "axis_angle": 0.0}) / 0.37
            if kap <= 1:
                assert abs(r - 1) <= 1e-12
            else:
                assert r <= kap * math.exp(-(kap * kap - 1) / (2 * kap * kap)) * (1 + 1e-12)
        for kap in (1.5, 2.0):
            L, sig, anh = 4001, 400.0, -2e8
            r = _peak("DragGaussianPulse", {"amplitude": 1.0, "length": L, "sigma": sig,
                                            "alpha": kap * 2 * math.pi * sig * anh / 1e9,
                                            "anharmonicity": anh, "detuning": 0.0,
                                            "subtracted": False, "axis_angle": 0.0})
            assert abs(r - kap * math.exp(-(kap * kap - 1) / (2 * kap * kap))) < 1e-5

    def test_drag_gaussian_subtracted_keeps_its_centre(self):
        """kappa^2 <= 1 - g_end is necessary at the centre; that it is also
        sufficient is checked here numerically, not proven."""
        rng = random.Random(7)
        for _ in range(1500):
            L = 2 * rng.randint(5, 150) + 1
            sig = rng.uniform(L / 8, L / 3)
            c = (L - 1) / 2
            g_end = math.exp(-(c ** 2) / (2 * sig ** 2))
            kap = rng.uniform(0, math.sqrt(1 - g_end))
            p = {"amplitude": 1.0, "length": L, "sigma": sig, "subtracted": True,
                 "alpha": kap * 2 * math.pi * sig * -2e8 / 1e9, "anharmonicity": -2e8,
                 "detuning": 0.0, "axis_angle": 0.0}
            z = np.abs(np.asarray(ws.synthesize_raw("DragGaussianPulse", p), dtype=complex))
            assert z.max() <= z[int(c)] * (1 + 1e-12)

    def test_realistic_drag_shapes_differ_from_amp_by_a_hair_at_most(self):
        """The rig chip's own DragCosine (length 48, alpha -0.3, anharmonicity
        about -200 MHz): an EVEN length has no centre sample, so the peak is
        cos^2(pi / (2 (L-1))) of the amplitude -- 0.01 dB, below the 0.1 dB
        the grid prints, but real."""
        r = _peak("DragCosinePulse", {"amplitude": 1.0, "length": 48, "alpha": -0.3,
                                      "anharmonicity": -2.1e8, "detuning": 0.0,
                                      "axis_angle": 0.0})
        assert abs(r - math.cos(math.pi / (2 * 47)) ** 2) < 1e-5
        assert -0.02 < 20 * math.log10(r) < 0


class TestPeakIsLinearInAmplitude:
    """[derived] every catalog waveform is homogeneous of degree 1 in its
    amplitude -- what lets ``r = peak(amp=1)`` be cached per SHAPE and a
    TYPED amplitude be multiplied by it on the client."""

    _SHAPES = {
        "SquarePulse": {"length": 100, "axis_angle": 0.7},
        "GaussianPulse": {"length": 41, "sigma": 7.0, "subtracted": True, "axis_angle": None},
        "DragGaussianPulse": {"length": 40, "sigma": 8.0, "alpha": -1.4,
                              "anharmonicity": -2e8, "detuning": 3e6,
                              "subtracted": True, "axis_angle": 0.2},
        "DragCosinePulse": {"length": 48, "alpha": -0.3, "anharmonicity": -2.1e8,
                            "detuning": 0.0, "axis_angle": 0.0},
        "FlatTopTanhPulse": {"length": 40, "flat_length": 0, "axis_angle": None},
        "ErfSquarePulse": {"flat_length": 6, "risetime_samples": 16, "sample_rate": 1e9,
                           "phase": 0.0, "detuning": 0.0, "positive_polarity": False,
                           "length": 24},
        "GaussianFilteredSymmetricBipolarPulse": {
            "pulse_length": 40, "post_zero_padding_length": 4,
            "gaussian_filter_frequency_mhz": 80.0, "sample_rate": 1e9,
            "axis_angle": None, "length": 44},
        "CosineBipolarPulse": {"length": 31, "flat_length": 0, "axis_angle": None},
    }

    @pytest.mark.parametrize("key", sorted(_SHAPES))
    def test_peak_scales_with_amplitude(self, key):
        shape = self._SHAPES[key]
        r1 = _peak(key, dict(shape, amplitude=1.0))
        for amp in (0.0013, 0.37, -0.52, 1.7, -3.0):
            got = _peak(key, dict(shape, amplitude=amp))
            assert abs(got - abs(amp) * r1) <= 1e-12 * abs(amp) * max(r1, 1)


# ---------------------------------------------------------------------------
# the annotation
# ---------------------------------------------------------------------------

def _chip_with(op: dict, *, channel: str = "resonator") -> dict:
    """A one-qubit merged doc whose *channel* plays ``op`` as ``probe`` (MW
    resonator / drive, or the LF ``z`` line); FSP -2 on both MW ports."""
    st = _state()
    for port in ("1", "2"):
        st["ports"]["mw_outputs"]["con1"]["1"][port]["full_scale_power_dbm"] = -2
    st["qubits"]["qA1"][channel]["operations"]["probe"] = op
    m = json.loads(json.dumps(st))
    m.update(json.loads(json.dumps(_WIRING)))
    return m


def _ann(m: dict, op: str = "probe", channel: str = "resonator", amp=None, **kw):
    path = f"qubits.qA1.{channel}.operations.{op}.amplitude"
    if amp is None:
        amp = m["qubits"]["qA1"][channel]["operations"][op]["amplitude"]
    return physical_units.amp_annotation(m, path, amp, **kw)


class TestPeakFollowsTheClass:
    def test_b05_a_lab_class_is_blank_with_its_reason(self):
        """The finding: a lab readout class on an FSP -2 port. The plain
        identity would say -27.6 dBm; SM now says nothing, and why."""
        m = _chip_with({"__class__": _LAB, "amplitude": 0.0526, "length": 1556,
                        "resonator_kappa_hz": 408736, "sample_rate": 1e9})
        why: list = []
        assert _ann(m, why=why) is None
        assert why == [{"mark": "dBm ?", "text": "dBm unknown: pulse class "
                        "LabAreaNormReadoutPulse is not one SM can synthesize"}]

    def test_a_lab_class_named_like_a_known_one_is_still_unknown(self):
        m = _chip_with({"__class__": "lab_pulses.SquarePulse", "amplitude": 0.1,
                        "length": 100})
        why: list = []
        assert _ann(m, why=why) is None
        # the full path: "SquarePulse is not one SM can synthesize" reads as nonsense
        assert "lab_pulses.SquarePulse" in why[0]["text"]

    def test_a_missing_class_is_blank(self):
        why: list = []
        assert _ann(_chip_with({"amplitude": 0.1, "length": 100}), why=why) is None
        assert "no __class__" in why[0]["text"]

    def test_a_field_the_transcription_does_not_model_is_blank(self):
        m = _chip_with({"__class__": _QC + "SquareReadoutPulse", "amplitude": 0.1,
                        "length": 100, "area_norm": True})
        why: list = []
        assert _ann(m, why=why) is None
        assert "area_norm" in why[0]["text"]

    def test_a_waveform_that_will_not_synthesize_is_blank(self):
        m = _chip_with({"__class__": _QC + "GaussianPulse", "amplitude": 0.1,
                        "length": 40, "sigma": "#/qubits/qA1/nowhere"}, channel="xy")
        why: list = []
        assert _ann(m, channel="xy", why=why) is None
        assert "cannot be synthesized" in why[0]["text"]

    def test_the_env_roster_does_not_vouch_for_a_waveform(self):
        """The selected env's roster may place a ``SquarePulse`` at a lab
        home -- that proves the class EXISTS there, not its waveform."""
        assert pc.spec_at_known_home("lab_pulses.SquarePulse") is None
        pc.apply_env_overlay({"SquarePulse": {"homes": ["lab_pulses"]}})
        try:
            assert pc.resolve_qclass("lab_pulses.SquarePulse")[1] == "env"
            assert pc.spec_at_known_home("lab_pulses.SquarePulse") is None
            m = _chip_with({"__class__": "lab_pulses.SquarePulse", "amplitude": 0.1,
                            "length": 100})
            assert _ann(m) is None
        finally:
            pc.apply_env_overlay(None)

    def test_known_homes(self):
        assert pc.spec_at_known_home(_QC + "SquarePulse").key == "SquarePulse"
        assert pc.spec_at_known_home(_QB_ARCH + "DragCosinePulse").key == "DragCosinePulse"
        # a golden-verified full-path alias
        assert pc.spec_at_known_home(_QC + "DragPulse").key == "DragGaussianPulse"
        # a bare key or a name-only match is not a home
        assert pc.spec_at_known_home("SquarePulse") is None
        assert pc.spec_at_known_home("some.fork.pulses.DragCosinePulse") is None
        assert pc.spec_at_known_home(None) is None

    def test_a_peak_equal_to_amp_class_is_the_plain_identity_bit_for_bit(self):
        # axis_angle 2.3: |e^{2.3i}| is 0.9999999999999999 in floats -- the
        # unit snap is what keeps this class bit-identical to the old identity
        m = _chip_with({"__class__": _QC + "SquareReadoutPulse", "amplitude": 0.0526,
                        "length": 1556, "axis_angle": 2.3})
        a = _ann(m)
        assert a["peak"] == 1.0
        assert a["dbm"] == -2 + 20 * math.log10(0.0526)
        assert a["text"] == "-27.6 dBm"

    def test_a_shaped_class_reads_its_waveform_peak(self):
        """A subtracted Gaussian never reaches its amplitude: the power is the
        waveform's, from the transcription the golden tests pin."""
        op = {"__class__": _QC + "GaussianPulse", "amplitude": 0.3, "length": 40,
              "sigma": 8.0, "subtracted": True, "axis_angle": None}
        a = _ann(_chip_with(op, channel="xy"), channel="xy")
        want = _peak("GaussianPulse", {k: v for k, v in op.items() if k != "__class__"})
        assert abs(a["peak"] - want / 0.3) <= 1e-12 and a["peak"] < 1
        assert abs(a["dbm"] - (-2 + 20 * math.log10(want))) <= 1e-9

    def test_a_drag_peak_above_its_amplitude_is_stated(self):
        """|k| > sqrt(2): the DRAG quadrature lifts the peak ABOVE the
        amplitude -- the plain identity would under-state the power."""
        L, anh, k = 41, -1e8, 2.0
        op = {"__class__": _QC + "DragCosinePulse", "amplitude": 0.2, "length": L,
              "alpha": k * (L - 1) * anh / 1e9, "anharmonicity": anh,
              "detuning": 0.0, "axis_angle": 0.0}
        a = _ann(_chip_with(op, channel="xy"), channel="xy")
        assert a["peak"] > 1.1
        assert a["dbm"] > -2 + 20 * math.log10(0.2) + 0.8

    def test_the_alias_pulse_shape_wins(self):
        """``-y90.amplitude`` points at ``x90``'s NUMBER; the waveform played
        is ``-y90``'s own (here a different length), so its shape decides."""
        m = _chip_with({"__class__": _QC + "GaussianPulse", "amplitude": 0.3,
                        "length": 40, "sigma": 8.0, "subtracted": True})
        m["qubits"]["qA1"]["resonator"]["operations"]["other"] = {
            "__class__": _QC + "GaussianPulse", "length": 80,
            "amplitude": "#../probe/amplitude", "sigma": 8.0, "subtracted": True}
        alias = "qubits.qA1.resonator.operations.other.amplitude"
        resolved = "qubits.qA1.resonator.operations.probe.amplitude"
        a = physical_units.amp_annotation(m, resolved, 0.3, alias_path=alias)
        want = _peak("GaussianPulse", {"amplitude": 0.3, "length": 80, "sigma": 8.0,
                                       "subtracted": True}) / 0.3
        assert abs(a["peak"] - want) <= 1e-12
        # without the alias, the resolved pulse's own (length 40) shape
        assert physical_units.amp_annotation(m, resolved, 0.3)["peak"] != a["peak"]

    def test_reads_name_the_pulse_dict_and_every_pointer_target(self):
        """RAM P6: a write to the class, a shape field, or a shape field's
        pointer target must re-annotate the cell."""
        m = _chip_with({"__class__": _QC + "DragCosinePulse", "amplitude": 0.1,
                        "length": 41, "alpha": -0.3, "axis_angle": 0.0,
                        "anharmonicity": "#/qubits/qA1/anharmonicity", "detuning": 0.0,
                        "id": "#/qubits/qA1/id"}, channel="xy")
        m["qubits"]["qA1"]["anharmonicity"] = -2e8
        reads: list = []
        _ann(m, channel="xy", reads=reads)
        assert "qubits.qA1.xy.operations.probe" in reads
        assert "qubits.qA1.anharmonicity" in reads
        # a field the catalog declares shape-free is not a read
        assert "qubits.qA1.id" not in reads
        # a lab class: its dict (and with it its __class__) is still read
        reads = []
        _ann(_chip_with({"__class__": _LAB, "amplitude": 0.05, "length": 100}),
             reads=reads)
        assert "qubits.qA1.resonator.operations.probe" in reads

    def test_a_shape_write_moves_the_answer(self):
        m = _chip_with({"__class__": _QC + "GaussianPulse", "amplitude": 0.3,
                        "length": 40, "sigma": 8.0, "subtracted": True}, channel="xy")
        before = _ann(m, channel="xy")["dbm"]
        m["qubits"]["qA1"]["xy"]["operations"]["probe"]["subtracted"] = False
        m["qubits"]["qA1"]["xy"]["operations"]["probe"]["length"] = 41
        assert _ann(m, channel="xy")["dbm"] == -2 + 20 * math.log10(0.3)
        assert before < -2 + 20 * math.log10(0.3)

    def test_lf_a_lab_class_is_blank_too(self):
        """An LF lab class may offset or rescale its lobes (a real chip's
        two-flux SNZ class carries a ``neg_offset_v``): its amplitude is then
        not the output voltage either."""
        m = _chip_with({"__class__": "lab_pulses.LabTwoFluxPulse", "amplitude": 0.06,
                        "flat_length": 44, "neg_offset_v": 0.01, "length": 60},
                       channel="z")
        why: list = []
        assert _ann(m, channel="z", why=why) is None
        assert why[0]["mark"] == "V ?" and why[0]["text"].startswith("volts unknown: ")

    def test_lf_reads_the_signed_peak_sample(self):
        """A negative-polarity erf pulse puts out MINUS its amplitude."""
        op = {"__class__": _QB_ARCH + "ErfSquarePulse", "amplitude": 0.05,
              "flat_length": 40, "risetime_samples": 16, "positive_polarity": False,
              "length": "#./inferred_length"}
        a = _ann(_chip_with(op, channel="z"), channel="z")
        assert a["kind"] == "lf" and a["peak"] < 0
        assert a["text"] == "-50 mV"

    def test_lf_a_square_pulse_is_unchanged(self):
        m = _chip_with({"__class__": _QC + "SquarePulse", "amplitude": 0.012,
                        "length": 200}, channel="z")
        a = _ann(m, channel="z")
        assert a["volts"] == 0.012 and a["peak"] == 1.0 and a["text"] == "12 mV"

    def test_lf_an_iq_waveform_is_blank(self):
        m = _chip_with({"__class__": _QC + "SquarePulse", "amplitude": 0.05,
                        "length": 40, "axis_angle": 0.5}, channel="z")
        why: list = []
        assert _ann(m, channel="z", why=why) is None
        assert "I/Q" in why[0]["text"]

    def test_amp_zero_and_a_broken_chain_stay_silent(self):
        """The blanks that are NOT the class's doing keep saying nothing."""
        m = _chip_with({"__class__": _LAB, "amplitude": 0.0, "length": 100})
        why: list = []
        assert _ann(m, why=why) is None and why == []
        m2 = _chip_with({"__class__": _LAB, "amplitude": 0.1, "length": 100})
        m2["wiring"]["qubits"]["qA1"]["rr"]["opx_output"] = "#/ports/mw_outputs/con9/9/9"
        assert _ann(m2, why=why) is None and why == []


# ---------------------------------------------------------------------------
# the QM docs quotes are verbatim
# ---------------------------------------------------------------------------

class TestQuotesAreVerbatim:
    _DOCS = Path(r"D:\work\documentation-website\docs\docs\docs")
    _QUOTES = [
        ("Guides/opx1000_fems.md",
         "This will set the power delivered to a 50 Ω load when the waveform is "
         "set to full scale (`{-1, 1}`)."),
        ("Guides/opx1000_fems.md", "The amplitude is linear in voltage, not power."),
        ("Introduction/config.md",
         "For a MW-FEM using the waveform, the sample values give the output "
         "amplitude as a fraction of the `full_scale_power_dbm` and must be "
         "within [-1.0, 1.0]."),
        ("Introduction/config.md",
         "For the OPX+ or a LF-FEM using the waveform, the sample values give "
         "the output in volts"),
    ]

    @pytest.mark.parametrize("rel,quote", _QUOTES)
    def test_quote(self, rel, quote):
        doc = " ".join((physical_units.__doc__ or "").split())
        assert quote in doc, "the docstring no longer carries this quote"
        src = self._DOCS / rel
        if not src.exists():
            pytest.skip("QM docs repo not on this machine")
        assert quote in " ".join(src.read_text(encoding="utf-8").split())


# ---------------------------------------------------------------------------
# the surfaces
# ---------------------------------------------------------------------------

_WHY = "dBm unknown: pulse class LabAreaNormReadoutPulse is not one SM can synthesize"


@pytest.fixture
def client(tmp_path):
    st = _state()
    st["qubits"]["qA1"]["resonator"]["operations"]["readout"] = {
        "__class__": _LAB, "amplitude": 0.0526, "length": 1556,
        "resonator_kappa_hz": 408736}
    st["qubits"]["qA1"]["xy"]["operations"]["x90"] = {
        "__class__": _QC + "GaussianPulse", "amplitude": 0.05, "length": 40,
        "sigma": 8.0, "subtracted": True}
    # the -y90 shape: its NUMBER is x90's, its waveform (length 80) its own
    st["qubits"]["qA1"]["xy"]["operations"]["other"] = {
        "__class__": _QC + "GaussianPulse", "amplitude": "#../x90/amplitude",
        "length": 80, "sigma": 8.0, "subtracted": True}
    live = tmp_path / "chips" / "lab"
    _write_chip(live, st)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return c


class TestBlankSurfaces:
    def test_the_grid_cell_marks_a_class_blank(self, client):
        html = client.get("/bulk").get_data(as_text=True)
        cell = html.split('data-dot-path="qubits.qA1.resonator.operations.readout.amplitude"')[1]
        cell = cell.split("</td>")[0]
        assert 'class="bulk-phys-why"' in cell and f'title="{_WHY}"' in cell
        assert "data-phys-kind" not in cell         # the client can never repaint it
        assert f"— {_WHY}" in cell                  # the box's own title says it too

    def test_the_grid_stamps_a_shape_peak_and_omits_a_unit_one(self, client):
        html = client.get("/bulk").get_data(as_text=True)
        x90 = html.split('data-dot-path="qubits.qA1.xy.operations.x90.amplitude"')[1].split(">")[0]
        assert "data-phys-peak=" in x90
        x180 = html.split('data-dot-path="qubits.qA1.xy.operations.x180.amplitude"')[1].split(">")[0]
        assert 'data-phys-kind="mw"' in x180 and "data-phys-peak" not in x180

    def test_the_route_answers_the_reason(self, client):
        p = "qubits.qA1.resonator.operations.readout.amplitude"
        d = client.post("/bulk/phys", data=json.dumps({"paths": [p]}),
                        content_type="application/json").get_json()
        assert d["phys"][p] is None
        assert d["blank"][p] == {"mark": "dBm ?", "text": _WHY}

    def test_the_route_and_the_page_agree_on_a_shaped_pulse(self, client):
        p = "qubits.qA1.xy.operations.x90.amplitude"
        d = client.post("/bulk/phys", data=json.dumps({"paths": [p]}),
                        content_type="application/json").get_json()
        ann = d["phys"][p]
        assert ann["peak"] < 1
        html = client.get("/bulk").get_data(as_text=True)
        assert f'data-phys-peak="{ann["peak"]}"' in html
        assert ann["text"] in html

    def test_the_route_and_the_grid_read_the_alias_shape(self, client):
        """``/bulk/phys`` and the grid's cell builder both hand the ALIAS to
        the annotation: the pulse played is ``other`` (length 80), not the
        ``x90`` its amplitude points at."""
        from quam_state_manager.web import routes as R
        p = "qubits.qA1.xy.operations.other.amplitude"
        want = _peak("GaussianPulse", {"amplitude": 1.0, "length": 80, "sigma": 8.0,
                                       "subtracted": True})
        d = client.post("/bulk/phys", data=json.dumps({"paths": [p]}),
                        content_type="application/json").get_json()
        assert abs(d["phys"][p]["peak"] - want) <= 1e-12
        store = client.application.config["contexts"][
            client.application.config["active_context"]]["store"]
        cell = R._build_bulk_cell(store.merged, p, {}, {}, "qA1", [])
        assert cell["resolved_path"] == "qubits.qA1.xy.operations.x90.amplitude"
        assert abs(cell["phys"]["peak"] - want) <= 1e-12

    def test_the_inspector_and_the_components_tables_say_why(self, client):
        html = client.get("/qubit/qA1").get_data(as_text=True)
        assert "phys-note-why" in html and _WHY in html
        for page in ("/qubits", "/resonators"):
            assert _WHY in client.get(page).get_data(as_text=True), page

    @pytest.mark.parametrize("macro", ["qubit_cell", "pair_cell"])
    def test_both_cell_macros_carry_peak_and_reason(self, client, macro):
        """The qubit and the pair grid render cells through two macros with the
        same markup; both must stamp the peak and show the reason."""
        app = client.application
        with app.test_request_context():
            mod = app.jinja_env.get_template("_bulk_cell_macros.html").module
            fn = getattr(mod, macro)
            col = {"maxlen": 12}
            base = {"dot_path": "x.operations.p.amplitude",
                    "resolved_path": "x.operations.p.amplitude", "display": "0.1"}
            args = (col, "qA1-qA2") if macro == "pair_cell" else (col,)
            shaped = str(fn(dict(base, phys={"kind": "mw", "fsp": -2.0, "dbm": -22.4,
                                             "text": "-22.4 dBm", "fsp_path": "f",
                                             "peak": 0.95}), *args))
            assert 'data-phys-peak="0.95"' in shaped
            assert "pulse-shape peak = 0.95 × amp" in shaped
            blank = str(fn(dict(base, phys=None,
                                phys_blank={"mark": "V ?", "text": "volts unknown: x"}), *args))
            assert '<span class="bulk-phys-why" aria-hidden="true" title="volts unknown: x">V ?</span>' in blank
            assert "— volts unknown: x" in blank and "data-phys-kind" not in blank

    def test_the_report_says_why(self, client):
        assert _WHY in client.get("/chip-status/report").get_data(as_text=True)
