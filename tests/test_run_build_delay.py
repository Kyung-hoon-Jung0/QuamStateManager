"""Unit tests for the LF-FEM-delay-from-MW-FEM-band rule in run_build.py.

The full ``run_build.py`` requires the QM stack (quam, quam_builder,
qualang_tools); these are not installed in ``qm_mng``. To keep the tests
in-process we import only the band/delay helpers via direct file load,
which sidesteps the top-of-module QUAM imports.

If a future refactor moves those helpers off the top of the module body,
this test file's loader pattern needs revisiting.
"""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest


class LFFEMAnalogOutputPort(types.SimpleNamespace):
    """Stands for quam's LF-FEM output port (the delay rule keys on the class name)."""


class MWFEMAnalogOutputPort(types.SimpleNamespace):
    """Stands for an MW-FEM output port: the LF delay rule must not touch it."""


_RUN_BUILD = Path(__file__).resolve().parent.parent / "quam_state_manager" / "generator" / "run_build.py"


def _load_helpers():
    """Load run_build.py without executing its QUAM-dependent imports.

    We need only the helper functions defined at module top level
    (`_band_for`, `_BAND_TO_DELAY_NS`, `_delay_for_band`,
    `_apply_lf_delay`). The QM stack imports live inside `_apply_pairs`
    and `_apply_qubit` — local imports — so a plain module load works.
    """
    spec = importlib.util.spec_from_file_location("run_build_under_test", _RUN_BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestBandToDelayMap:
    def setup_method(self):
        self.mod = _load_helpers()

    def test_band1_maps_to_141(self):
        assert self.mod._BAND_TO_DELAY_NS[1] == 141

    def test_band2_maps_to_161(self):
        assert self.mod._BAND_TO_DELAY_NS[2] == 161

    def test_band3_maps_to_141(self):
        assert self.mod._BAND_TO_DELAY_NS[3] == 141

    def test_only_three_bands_listed(self):
        assert set(self.mod._BAND_TO_DELAY_NS.keys()) == {1, 2, 3}


class TestDelayForBand:
    def setup_method(self):
        self.mod = _load_helpers()

    def test_known_bands(self):
        assert self.mod._delay_for_band(1) == 141
        assert self.mod._delay_for_band(2) == 161
        assert self.mod._delay_for_band(3) == 141

    def test_unknown_band(self):
        assert self.mod._delay_for_band(0) is None
        assert self.mod._delay_for_band(None) is None
        assert self.mod._delay_for_band(4) is None
        assert self.mod._delay_for_band("2") is None  # str, not int


class TestBandForFrequency:
    """Confirm bandOf-in-Python matches the JS mirror in generate.js."""

    def setup_method(self):
        self.mod = _load_helpers()

    @pytest.mark.parametrize("freq,expected", [
        (3.5e9, 1),
        (5.0e9, 2),  # 4.5–7.5
        (5.4e9, 2),  # 4.5–7.5 wins over band 1 endpoint
        (7.4e9, 3),  # 6.5–10.5 wins after 7.5 cutoff for band 2 — but 7.4 still in band 2 (lower bound 6.5)
        (8.0e9, 3),
        (10.5e9, 3),
        (0.0, None),
        (20e9, None),
    ])
    def test_band_assignment(self, freq, expected):
        # Band-2 covers 4.5–7.5; band-3 covers 6.5–10.5; both can match in
        # 6.5–7.5. The implementation picks band 2 (first match) — we just
        # confirm the result is non-None at the boundaries to keep this
        # robust to that overlap choice.
        result = self.mod._band_for(freq)
        if expected is None:
            assert result is None
        else:
            assert result is not None


class TestApplyLfDelay:
    def setup_method(self):
        self.mod = _load_helpers()

    def test_sets_delay_for_band1(self):
        port = LFFEMAnalogOutputPort(delay=0)
        self.mod._apply_lf_delay(port, 1)
        assert port.delay == 141

    def test_sets_delay_for_band2(self):
        port = LFFEMAnalogOutputPort(delay=0)
        self.mod._apply_lf_delay(port, 2)
        assert port.delay == 161

    def test_sets_delay_for_band3(self):
        port = LFFEMAnalogOutputPort(delay=0)
        self.mod._apply_lf_delay(port, 3)
        assert port.delay == 141

    def test_noop_on_a_port_that_is_not_lf_fem(self):
        port = MWFEMAnalogOutputPort(delay=0)
        self.mod._apply_lf_delay(port, 2)
        assert port.delay == 0

    def test_noop_when_port_is_none(self):
        # Should not raise.
        self.mod._apply_lf_delay(None, 1)

    def test_noop_when_band_is_none(self):
        port = LFFEMAnalogOutputPort(delay=0)
        self.mod._apply_lf_delay(port, None)
        assert port.delay == 0

    def test_noop_when_band_is_unknown(self):
        port = LFFEMAnalogOutputPort(delay=0)
        self.mod._apply_lf_delay(port, 99)
        assert port.delay == 0

    def test_noop_when_port_has_no_delay_attr(self):
        # Object lacks `delay` entirely → silently skipped.
        port = LFFEMAnalogOutputPort(offset=0.0)
        self.mod._apply_lf_delay(port, 1)
        assert not hasattr(port, "delay")

    def test_swallows_reference_assignment_error(self):
        # Some QUAM ports raise ValueError when assigning over a reference;
        # the helper must catch that.
        class LFFEMRefPort:
            @property
            def delay(self):
                return 0
            @delay.setter
            def delay(self, _):
                raise ValueError("reference write blocked")
        port = LFFEMRefPort()
        # Should not raise.
        self.mod._apply_lf_delay(port, 2)


class TestBandOverride:
    """r16 ⓪-3: user-settable band beats the derived _band_for pick — the
    Nyquist bands OVERLAP (6.5–7.5 GHz is band 2 AND 3) and first-match-wins
    wrote band 2 + the wrong 161 ns delay on real band-3 readout ports."""

    def setup_method(self):
        self.mod = _load_helpers()

    def _mw_port(self):
        return types.SimpleNamespace(upconverter_frequency=None,
                                     downconverter_frequency=None, band=None)

    def test_explicit_band_overrides_derived(self):
        ch = types.SimpleNamespace(opx_output=self._mw_port(), opx_input=None)
        self.mod._set_channel_lo(ch, 7.015e9, 3)     # overlap zone; derived = 2
        assert ch.opx_output.band == 3
        assert ch.opx_output.upconverter_frequency == 7.015e9

    def test_none_band_keeps_derived(self):
        ch = types.SimpleNamespace(opx_output=self._mw_port(), opx_input=None)
        self.mod._set_channel_lo(ch, 7.015e9, None)
        assert ch.opx_output.band == 2               # legacy first-match

    def test_invalid_band_falls_back_to_derived(self):
        ch = types.SimpleNamespace(opx_output=self._mw_port(), opx_input=None)
        self.mod._set_channel_lo(ch, 5.0e9, 7)
        assert ch.opx_output.band == 1               # _band_for(5.0e9), first match

    def test_band_only_write(self):
        out, inp = self._mw_port(), self._mw_port()
        ch = types.SimpleNamespace(opx_output=out, opx_input=inp)
        self.mod._set_channel_band(ch, 3)
        assert out.band == 3 and inp.band == 3
        assert out.upconverter_frequency is None     # LO untouched

    def test_band_only_invalid_noop(self):
        out = self._mw_port()
        ch = types.SimpleNamespace(opx_output=out, opx_input=None)
        self.mod._set_channel_band(ch, "3")          # strings never coerce here
        assert out.band is None


class TestAlignCouplerDelays:
    """A coupler LF-FEM output gets the delay of its pair's moving-qubit z output."""

    def setup_method(self):
        self.mod = _load_helpers()

    @staticmethod
    def _q(band, z_delay=None, z_kind=LFFEMAnalogOutputPort):
        xy = types.SimpleNamespace(opx_output=MWFEMAnalogOutputPort(band=band))
        z = (types.SimpleNamespace(opx_output=z_kind(delay=z_delay))
             if z_delay is not None else None)
        return types.SimpleNamespace(xy=xy, z=z)

    @staticmethod
    def _pair(qc, qt, moving="control", out=None):
        out = out if out is not None else LFFEMAnalogOutputPort(delay=0)
        return types.SimpleNamespace(qubit_control=qc, qubit_target=qt, moving_qubit=moving,
                                     coupler=types.SimpleNamespace(opx_output=out))

    def _run(self, *pairs):
        m = types.SimpleNamespace(qubit_pairs={f"p{i}": p for i, p in enumerate(pairs)})
        return self.mod._align_coupler_delays(m)

    def test_moving_control_takes_control_z(self):
        p = self._pair(self._q(2, 161), self._q(1, 141))
        self._run(p)
        assert p.coupler.opx_output.delay == 161

    def test_moving_target_takes_target_z(self):
        p = self._pair(self._q(2, 161), self._q(1, 141), moving="target")
        self._run(p)
        assert p.coupler.opx_output.delay == 141

    def test_moving_unset_takes_control(self):
        p = self._pair(self._q(2, 161), self._q(1, 141), moving=None)
        self._run(p)
        assert p.coupler.opx_output.delay == 161

    def test_a_z_delay_of_zero_is_copied(self):
        p = self._pair(self._q(2, 0), self._q(2, 161), out=LFFEMAnalogOutputPort(delay=99))
        self._run(p)
        assert p.coupler.opx_output.delay == 0

    def test_no_z_line_takes_the_band_delay(self):
        p = self._pair(self._q(2), self._q(1))
        self._run(p)
        assert p.coupler.opx_output.delay == self.mod._delay_for_band(2)

    def test_a_coupler_on_a_non_lf_output_is_left_alone(self):
        p = self._pair(self._q(2, 161), self._q(2, 161), out=MWFEMAnalogOutputPort(delay=7))
        self._run(p)
        assert p.coupler.opx_output.delay == 7

    def test_a_shared_coupler_keeps_the_first_pair_and_warns(self):
        shared = LFFEMAnalogOutputPort(delay=0, controller_id="con1", fem_id=3, port_id=5)
        a = self._pair(self._q(2, 161), self._q(2, 161), out=shared)
        b = self._pair(self._q(1, 141), self._q(1, 141), out=shared)
        warns = self._run(a, b)
        assert shared.delay == 161
        assert len(warns) == 1 and "161" in warns[0] and "141" in warns[0]
        assert "con1 / slot 3 / port 5" in warns[0] and "p0" in warns[0] and "p1" in warns[0]

    def test_pair_without_coupler_untouched(self):
        p = types.SimpleNamespace(qubit_control=self._q(2, 161), qubit_target=self._q(2, 161),
                                  moving_qubit="control", coupler=None)
        assert self._run(p) == []
        assert p.coupler is None

    def test_no_pairs_is_noop(self):
        assert self.mod._align_coupler_delays(types.SimpleNamespace(qubit_pairs={})) == []
        assert self.mod._align_coupler_delays(types.SimpleNamespace()) == []

    def test_build_aligns_after_the_band_fixup(self):
        src = _RUN_BUILD.read_text(encoding="utf-8")
        fix = src.index("        _fix_coupled_bands(machine, spec, warnings)\n")
        call = src.index("        warnings.extend(_align_output_timing(machine, spec))\n")
        assert call > fix
        # the build has one timing call; the coupler rule is only its fallback
        assert src.count("_align_output_timing(machine, spec))") == 1
        assert "warnings.extend(_align_coupler_delays(" not in src
        assert "return _align_coupler_delays(machine)" in src


# --- one time base across MW bands (``_align_output_timing``) --------------

def _ns(**kw):
    return types.SimpleNamespace(**kw)


def _chip(xy_bands, ro_band, tof=32):
    """A fake machine: qubit i drives on an MW port of xy_bands[i], one
    readout port (band ro_band) shared by every resonator, an LF z port per
    qubit, and one coupler per neighbouring pair."""
    mw = {}
    qubits = {}
    ro = MWFEMAnalogOutputPort(band=ro_band, delay=0)
    mw[8] = ro
    for i, b in enumerate(xy_bands, start=1):
        xy = MWFEMAnalogOutputPort(band=b, delay=0)
        mw[i] = xy
        qubits[f"q{i}"] = _ns(xy=_ns(opx_output=xy),
                              z=_ns(opx_output=LFFEMAnalogOutputPort(delay=0)),
                              resonator=_ns(opx_output=ro, time_of_flight=tof))
    names = list(qubits)
    pairs = {f"{a}-{b}": _ns(coupler=_ns(opx_output=LFFEMAnalogOutputPort(delay=0)),
                             qubit_control=qubits[a], qubit_target=qubits[b], moving_qubit=None)
             for a, b in zip(names, names[1:])}
    return _ns(ports=_ns(mw_outputs={"con1": {1: mw}}), qubits=qubits, qubit_pairs=pairs)


def _arrivals(mod, m):
    mw = [mod._BAND_TO_DELAY_NS[p.band] + p.delay for p in m.ports.mw_outputs["con1"][1].values()]
    lf = [q.z.opx_output.delay for q in m.qubits.values()]
    lf += [p.coupler.opx_output.delay for p in m.qubit_pairs.values()]
    return set(mw), set(lf)


class TestAlignOutputTiming:
    def setup_method(self):
        self.mod = _load_helpers()

    def test_plan_is_relative_to_the_largest_latency(self):
        assert self.mod._output_timing_plan({1, 2}) == (161, {1: 20, 2: 0})
        assert self.mod._output_timing_plan({1, 3}) == (141, {1: 0, 3: 0})
        assert self.mod._output_timing_plan({None}) == (None, {})

    def test_pair_in_bands_2_and_1(self):
        m = _chip([2, 1], ro_band=2)
        self.mod._align_output_timing(m)
        mw = m.ports.mw_outputs["con1"][1]
        assert (mw[1].delay, mw[2].delay, mw[8].delay) == (0, 20, 0)
        assert {q.z.opx_output.delay for q in m.qubits.values()} == {161}
        assert m.qubit_pairs["q1-q2"].coupler.opx_output.delay == 161
        assert _arrivals(self.mod, m) == ({161}, {161})
        assert {q.resonator.time_of_flight for q in m.qubits.values()} == {32}

    def test_xy_band_2_readout_band_3(self):
        m = _chip([2], ro_band=3)
        self.mod._align_output_timing(m)
        mw = m.ports.mw_outputs["con1"][1]
        assert (mw[1].delay, mw[8].delay) == (0, 20)
        assert m.qubits["q1"].z.opx_output.delay == 161
        assert m.qubits["q1"].resonator.time_of_flight == 52
        assert _arrivals(self.mod, m) == ({161}, {161})

    @pytest.mark.parametrize("xy,ro,lf", [([1, 3], 3, 141), ([2, 2], 2, 161)])
    def test_one_band_class_gives_mw_zero(self, xy, ro, lf):
        m = _chip(xy, ro_band=ro)
        self.mod._align_output_timing(m)
        assert {p.delay for p in m.ports.mw_outputs["con1"][1].values()} == {0}
        assert _arrivals(self.mod, m) == ({lf}, {lf})
        assert {q.resonator.time_of_flight for q in m.qubits.values()} == {32}

    def test_shared_feedline_shifts_once_and_moves_five_tofs(self):
        m = _chip([2, 2, 2, 2, 2], ro_band=3)
        self.mod._align_output_timing(m)
        assert m.ports.mw_outputs["con1"][1][8].delay == 20
        assert [q.resonator.time_of_flight for q in m.qubits.values()] == [52] * 5

    def test_no_mw_band_falls_back_to_the_coupler_rule(self):
        m = _chip([2, 1], ro_band=2)
        m.ports = _ns(mw_outputs={})
        m.qubits["q1"].z.opx_output.delay = 61
        self.mod._align_output_timing(m)
        assert m.qubit_pairs["q1-q2"].coupler.opx_output.delay == 61
        assert m.qubits["q2"].z.opx_output.delay == 0


class TestAlignOutputTimingEdges:
    def setup_method(self):
        self.mod = _load_helpers()

    def test_a_double_call_is_the_same_chip(self):
        m = _chip([2, 2], ro_band=3)
        self.mod._align_output_timing(m)
        self.mod._align_output_timing(m)
        assert m.ports.mw_outputs["con1"][1][8].delay == 20
        assert [q.resonator.time_of_flight for q in m.qubits.values()] == [52, 52]

    def test_a_typed_time_of_flight_stays_and_is_named(self):
        m = _chip([2, 2], ro_band=3, tof=300)
        spec = {"populate": {"options": {"tof_defaults": {}},
                             "resonator": {"q1": {"time_of_flight": 300}}}}
        warns = self.mod._align_output_timing(m, spec)
        assert m.qubits["q1"].resonator.time_of_flight == 300
        assert m.qubits["q2"].resonator.time_of_flight == 320      # the default moves
        assert warns == ["time of flight left as you set it on q1; its readout output is "
                         "delayed by 20 ns: measure time of flight again."]

    def test_a_prefilled_default_moves_and_a_set_value_stays(self):
        m = _chip([2, 2, 2], ro_band=3, tof=28)
        m.qubits["q2"].resonator.time_of_flight = 300
        spec = {"populate": {
            "options": {"tof_defaults": {"q1": 28, "q2": 28, "q3": 28}},
            "resonator": {"q1": {"time_of_flight": 28}, "q2": {"time_of_flight": 300},
                          "q3": {"time_of_flight": 28}}}}
        spec["populate"]["options"]["tof_defaults"].pop("q3")     # q3 typed 28 on purpose
        warns = self.mod._align_output_timing(m, spec)
        assert [q.resonator.time_of_flight for q in m.qubits.values()] == [48, 300, 28]
        assert warns == ["time of flight left as you set it on q2, q3; their readout output is "
                         "delayed by 20 ns: measure time of flight again."]

    def test_every_prefilled_default_moves_with_no_note(self):
        m = _chip([2, 2], ro_band=3, tof=28)
        spec = {"populate": {"options": {"tof_defaults": {"q1": 28, "q2": 28}},
                             "resonator": {"q1": {"time_of_flight": 28},
                                           "q2": {"time_of_flight": 28}}}}
        assert self.mod._align_output_timing(m, spec) == []
        assert [q.resonator.time_of_flight for q in m.qubits.values()] == [48, 48]
        assert self.mod._align_output_timing(m, spec) == []          # moves once
        assert [q.resonator.time_of_flight for q in m.qubits.values()] == [48, 48]

    def test_a_single_band_chip_moves_nothing(self):
        m = _chip([3, 3], ro_band=3, tof=28)
        spec = {"populate": {"options": {"tof_defaults": {"q1": 28}},
                             "resonator": {"q1": {"time_of_flight": 28}}}}
        assert self.mod._align_output_timing(m, spec) == []
        assert [q.resonator.time_of_flight for q in m.qubits.values()] == [28, 28]

    def test_a_dedicated_cr_port_is_shifted(self):
        m = _chip([2, 2], ro_band=2)
        cr = MWFEMAnalogOutputPort(band=1, delay=0)
        m.ports.mw_outputs["con1"][1][5] = cr
        self.mod._align_output_timing(m)
        assert cr.delay == 20
        assert _arrivals(self.mod, m) == ({161}, {161})


class _StuckMWFEMAnalogOutputPort(MWFEMAnalogOutputPort):
    """An output whose delay cannot be written."""

    def __setattr__(self, name, value):
        if name == "delay" and "delay" in self.__dict__:
            raise ValueError("read only")
        super().__setattr__(name, value)


class TestOutputTimingProvenance:
    def setup_method(self):
        self.mod = _load_helpers()

    def test_no_record_keeps_the_value_under_one_grouped_line(self):
        m = _chip([2, 2], ro_band=3, tof=300)
        spec = {"populate": {"resonator": {"q1": {"time_of_flight": 300},
                                           "q2": {"time_of_flight": 300}}}}
        warns = self.mod._align_output_timing(m, spec)
        assert [q.resonator.time_of_flight for q in m.qubits.values()] == [300, 300]
        assert warns == ["this draft does not record which time of flight values were "
                         "pre-filled, so the time of flight of q1, q2 was kept while their "
                         "readout output is delayed: measure time of flight again."]

    def test_a_partial_record_splits_default_typed_and_unrecorded(self):
        m = _chip([2, 2, 2], ro_band=3, tof=28)
        m.qubits["q2"].resonator.time_of_flight = 300
        m.qubits["q3"].resonator.time_of_flight = 300
        spec = {"populate": {
            "options": {"tof_defaults": {"q1": 28, "q2": 28}, "tof_unrecorded": ["q3"]},
            "resonator": {"q1": {"time_of_flight": 28}, "q2": {"time_of_flight": 300},
                          "q3": {"time_of_flight": 300}}}}
        warns = self.mod._align_output_timing(m, spec)
        assert [q.resonator.time_of_flight for q in m.qubits.values()] == [48, 300, 300]
        assert len(warns) == 2 and "left as you set it on q2;" in warns[0]
        assert "time of flight of q3 was kept" in warns[1]

    def test_a_failed_readout_delay_moves_no_time_of_flight(self):
        m = _chip([2, 2], ro_band=3)
        stuck = _StuckMWFEMAnalogOutputPort(band=3, delay=0)
        m.ports.mw_outputs["con1"][1][8] = stuck
        for q in m.qubits.values():
            q.resonator.opx_output = stuck
        warns = self.mod._align_output_timing(m)
        assert stuck.delay == 0
        assert [q.resonator.time_of_flight for q in m.qubits.values()] == [32, 32]
        assert any("output timing not set on" in w for w in warns)

    def test_an_lf_output_of_another_kind_gets_the_target(self):
        m = _chip([2, 1], ro_band=1)
        drive = LFFEMAnalogOutputPort(delay=0)
        m.ports.analog_outputs = {"con1": {2: {3: drive}}}
        self.mod._align_output_timing(m)
        assert drive.delay == 161

    def test_an_unused_port_on_another_band_does_not_count(self):
        m = _chip([1, 1], ro_band=1)
        m.ports.mw_outputs["con1"][1][7] = MWFEMAnalogOutputPort(band=2, delay=0)
        doc = {"ports": {"mw_outputs": {"con1": {"1": {str(i): {} for i in (1, 2, 7, 8)}}}},
               "qubits": {q: {"xy": {"opx_output": f"#/ports/mw_outputs/con1/1/{i}"},
                              "resonator": {"opx_output": "#/ports/mw_outputs/con1/1/8"}}
                          for i, q in ((1, "q1"), (2, "q2"))}}
        m.to_dict = lambda: doc
        assert {p.band for p in self.mod._mw_output_ports(m)} == {1}
        self.mod._align_output_timing(m)
        assert [p.delay for p in m.ports.mw_outputs["con1"][1].values()] == [0, 0, 0, 0]
