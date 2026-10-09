"""Z-line distortion (core/zline_filters.py): the filter model, cross-checked.

Every [derived] step of the module is checked here against something that did
not come from the module itself:

* qualang_tools' own correction taps (QM's filter-design library) -- the
  independent implementation, single exponential and the QOP<=3.4 cascade;
* a closed-form continuous-time step response derived by hand (sympy-free);
* the round trip: the line model H, built by a DIFFERENT construction
  (partial fractions via scipy.signal.invres), undoes the correction exactly;
* the documentation quotes in the module, checked verbatim against the QM docs
  repo when it is on this machine.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pytest
from scipy import signal

from quam_state_manager.core import zline_filters as Z

DOCS = Path(r"D:\work\documentation-website\docs\docs\docs\Guides")


def _pf(exp=(), ff=None, **port):
    p = {"exponential_filter": [list(e) for e in exp] if exp is not None else None,
         "feedforward_filter": ff, "sampling_rate": 1e9, "upsampling_mode": "pulse"}
    p.update(port)
    pf, notes = Z.parse_port_filter(p)
    return pf, notes


def _codes(notes):
    return [n["code"] for n in notes]


# ---------------------------------------------------------------------------
# independent implementation: qualang_tools
# ---------------------------------------------------------------------------

class TestAgainstQualangTools:
    def test_single_exponential_matches_qualang_taps(self):
        qt = pytest.importorskip("qualang_tools.digital_filters.filters")
        for a, tau in [(-0.1, 50.0), (0.3, 5.0), (-0.04, 1.6), (0.02, 2e5)]:
            ff, fb = qt.single_exponential_correction(a, tau, Ts=0.5, qop_version=qt.QOPVersion.NONE)
            x = np.r_[np.zeros(5), np.ones(4000)]
            ref = signal.lfilter(ff, [1.0, -fb[0]], x)      # doc: y[n] = sum a_m y[n-m] + ...
            pf, _ = _pf([(a, tau)])
            ours = Z.apply_filters(pf, x)
            assert np.max(np.abs(ours - ref)) < 1e-10, (a, tau)

    def test_cascade_model_matches_qualang_calc_filter_taps(self):
        qt = pytest.importorskip("qualang_tools.digital_filters.filters")
        exps = [(-0.1, 50.0), (0.05, 500.0), (-0.02, 8.0)]
        ff, fb = qt.calc_filter_taps(exponential=exps, Ts=0.5, qop_version=qt.QOPVersion.NONE)
        den = np.array([1.0])
        for a_m in fb:                                    # one feedback tap per cascaded stage
            den = np.polymul(den, [1.0, -a_m])
        x = np.r_[np.zeros(3), np.ones(20000)]
        ref = signal.lfilter(ff, den, x)
        pf, _ = _pf(exps)
        ours = Z.apply_filters(pf, x, model="cascade")
        assert np.max(np.abs(ours - ref)) < 1e-9

    def test_sum_and_cascade_differ_for_several_exponentials(self):
        # the QOP-version note is not decoration: the two models disagree
        pf, _ = _pf([(-0.1, 50.0), (0.05, 500.0)])
        x = np.ones(4000)
        d = np.max(np.abs(Z.apply_filters(pf, x) - Z.apply_filters(pf, x, model="cascade")))
        assert d > 1e-4


# ---------------------------------------------------------------------------
# closed form + round trip
# ---------------------------------------------------------------------------

class TestClosedForm:
    def test_single_exponential_step_matches_continuous_closed_form(self):
        # C(s) = (s + p) / ((1 + A) s + p)  ->  step: 1 - A/(1+A) * exp(-t p/(1+A))
        # (hand-derived; bilinear error is O((Ts/tau)^2), tiny for tau = 200 ns)
        a, tau = -0.2, 200.0
        pf, _ = _pf([(a, tau)])
        n = 8000
        y = Z.apply_filters(pf, np.ones(n))
        t = np.arange(n) * Z.TS_NS + Z.TS_NS / 2          # bilinear samples sit mid-interval
        p = 1.0 / tau
        ref = 1 - a / (1 + a) * np.exp(-t * p / (1 + a))
        assert np.max(np.abs(y[10:] - ref[10:])) < 2e-4

    def test_dc_limit_is_one_over_adc(self):
        pf, _ = _pf([(-0.1, 20.0), (0.05, 3.0)], exponential_dc_gain=0.8)
        y = Z.apply_filters(pf, np.ones(20000))
        assert abs(y[-1] - 1 / 0.8) < 1e-6

    def test_round_trip_through_independently_built_line_model(self):
        exps = [(-0.013, 194413.5), (-0.0175, 617.4), (-0.0216, 56.8), (-0.0012, 18.2), (-0.0418, 1.6)]
        pf, notes = _pf(exps)
        assert pf is not None, notes
        # H(s) = (A_dc + sum A) - sum A_i p_i / (s + p_i), p_i = 1/tau_i: applied here
        # in PARALLEL form (one bilinear first-order section per term, summed) --
        # a different construction from the module's series zpk. (A single ba
        # polynomial is too ill-conditioned for taus spanning 1 ns .. 0.2 ms.)
        x = np.r_[np.zeros(4), np.ones(30000)]
        corrected = Z.apply_filters(pf, x)
        back = (1.0 + sum(a for a, _ in exps)) * corrected
        for a, tau in exps:
            bd, ad = signal.bilinear([-a / tau], [1.0, 1.0 / tau], fs=1 / Z.TS_NS)
            back = back + signal.lfilter(bd, ad, corrected)
        assert np.max(np.abs(back - x)) < 1e-6

    def test_fir_is_taps_at_half_ns(self):
        pf, _ = _pf(None, ff=[0.5, 0.3, 0.2])
        y = Z.apply_filters(pf, np.r_[1.0, np.zeros(5)])
        assert np.allclose(y[:4], [0.5, 0.3, 0.2, 0.0])

    def test_real_chip_numbers(self):
        # lab-F q1's port (the real customer values, copied): the DC limit is
        # sum(FIR)/A_dc and the full-rate step settles onto it
        ff = [0.5173869923748633, 0.3791747071886605, 0.14943410284208183, 0.02643336106350454]
        pf, _ = _pf([(-0.0418, 1.6), (-0.0216, 56.8)], ff=ff)
        st = Z.step_response(pf)
        # 8 slowest-taus of horizon: settled to exp(-8) of a 2 % term
        assert abs(st["final"] - st["dc_limit"]) < 2e-5
        assert st["dc_limit"] == pytest.approx(sum(ff))
        assert st["t_ns"][0] == Z.TS_NS and len(st["t_ns"]) == len(st["both"])


# ---------------------------------------------------------------------------
# fault injection: a broken input gives a note, never a curve
# ---------------------------------------------------------------------------

class TestFaultInjection:
    @pytest.mark.parametrize("port,code", [
        ({"exponential_filter": [[-0.1, -50.0]]}, "exp_tau_nonpositive"),
        ({"exponential_filter": [[-0.1, 0.0]]}, "exp_tau_nonpositive"),
        ({"exponential_filter": [[-0.1, 5e-8]]}, "exp_tau_units"),          # seconds
        ({"exponential_filter": [[-0.1, 0.2]]}, "exp_tau_unrepresentable"),
        ({"exponential_filter": [[-0.1]]}, "exp_bad_pair"),
        ({"exponential_filter": [["x", 5]]}, "exp_not_number"),
        ({"exponential_filter": [[float("nan"), 5]]}, "exp_not_number"),
        ({"exponential_filter": "oops"}, "exp_not_list"),
        ({"exponential_filter": [[-0.01, 10.0 * (i + 1)] for i in range(7)]}, "too_many_iir"),
        ({"feedforward_filter": [0.5, 2.5]}, "ff_out_of_range"),
        ({"feedforward_filter": [0.0, 0.0]}, "ff_all_zero"),
        ({"feedforward_filter": [0.02] * 49}, "ff_too_long"),
        ({"feedforward_filter": [0.5, None]}, "ff_not_number"),
        ({"feedforward_filter": 0.5}, "ff_not_list"),
        ({"feedback_filter": [0.5]}, "feedback_unmodeled"),
        ({"high_pass_filter": 1000.0, "exponential_dc_gain": 0.5}, "hp_with_dc"),
        ({"high_pass_filter": -5}, "hp_bad"),
        ({"exponential_dc_gain": "one"}, "dc_not_number"),
        ({"sampling_rate": 5e8}, "bad_rate"),
    ])
    def test_blocks(self, port, code):
        pf, notes = Z.parse_port_filter(port)
        assert pf is None
        blocks = [n for n in notes if n["level"] == "block"]
        assert code in _codes(blocks), notes
        assert all(n["text"] for n in notes)

    def test_empty_taps_is_an_honest_identity(self):
        pf, notes = _pf(None, ff=[])
        assert pf is not None and not pf.has_fir
        assert "ff_empty" in _codes(notes)
        assert np.allclose(Z.apply_filters(pf, np.ones(10)), 1.0)

    def test_missing_filters_say_so(self):
        pf, notes = Z.parse_port_filter({"sampling_rate": 1e9})
        assert pf is not None and "no_filters" in _codes(notes)
        st = Z.step_response(pf)
        assert st["both"] == st["ideal"]

    def test_outside_doc_range_warns_but_draws(self):
        pf, notes = _pf([(-0.05, 0.8)])
        assert pf is not None and "exp_tau_outside_doc" in _codes(notes)

    def test_high_pass_is_an_integrator_not_a_crash(self):
        pf, notes = Z.parse_port_filter({"high_pass_filter": 1000.0})
        assert pf is not None and pf.dc_gain == 0.0 and "hp_ideal" in _codes(notes)
        y = Z.apply_filters(pf, np.ones(4000))
        assert y[-1] > y[100] > 1.0          # the documented ramp, not a settled step
        assert Z.step_response(pf)["dc_limit"] is None

    def test_mw_upsampling_is_flagged(self):
        pf, _ = _pf([(-0.1, 20.0)], upsampling_mode="mw")
        r = Z.pulse_response(pf, [0.1] * 20)
        assert "mw_upsampling" in _codes(r["notes"])
        pf2, _ = _pf([(-0.1, 20.0)])
        assert "mw_upsampling" not in _codes(Z.pulse_response(pf2, [0.1] * 20)["notes"])

    def test_clipping_is_flagged(self):
        pf, _ = _pf([(-0.3, 5.0)])                         # 1/(1-0.3) = 1.43x edge
        r = Z.pulse_response(pf, [0.45] * 40)
        assert r["peak"] > 0.5 and "clips" in _codes(r["notes"])
        pf2, _ = _pf([(-0.3, 5.0)], output_mode="amplified")
        assert "clips" not in _codes(Z.pulse_response(pf2, [0.45] * 40)["notes"])

    def test_pulse_doubling_and_timebase(self):
        pf, _ = Z.parse_port_filter({"upsampling_mode": "pulse"})
        r = Z.pulse_response(pf, [0.1, 0.2], pad_before_ns=1.0, pad_after_ns=1.0)
        assert r["ideal"] == [0, 0, 0.1, 0.1, 0.2, 0.2, 0, 0]
        assert r["t_ns"][:3] == [-1.0, -0.5, 0.0]


# ---------------------------------------------------------------------------
# pointer chain from the qubit to the port
# ---------------------------------------------------------------------------

def _merged(opx_output="#/wiring/qubits/q1/z/opx_output", wiring_target="#/ports/analog_outputs/con1/5/1"):
    return {
        "qubits": {"q1": {"z": {"opx_output": opx_output, "operations": {}}},
                   "q2": {"xy": {}}},
        "qubit_pairs": {"q1-2": {"coupler": {"opx_output": "#/ports/analog_outputs/con1/5/2"}}},
        "wiring": {"qubits": {"q1": {"z": {"opx_output": wiring_target}}}},
        "ports": {"analog_outputs": {"con1": {"5": {
            "1": {"exponential_filter": [[-0.1, 20.0]], "feedforward_filter": [1.0]},
            "2": {"exponential_filter": None}}}},
                  "mw_outputs": {"con1": {"3": {"1": {"full_scale_power_dbm": -11}}}}},
    }


class TestResolve:
    def test_two_hop_chain_reaches_the_port(self):
        r = Z.resolve_zline(_merged(), "qubits.q1.z")
        assert r["port_path"] == "ports.analog_outputs.con1.5.1"
        assert len(r["chain"]) == 2

    def test_dangling(self):
        r = Z.resolve_zline(_merged(wiring_target="#/ports/analog_outputs/con1/9/9"), "qubits.q1.z")
        assert r["port"] is None and _codes(r["notes"]) == ["dangling"]

    def test_mw_port_is_not_an_lf_port(self):
        r = Z.resolve_zline(_merged(wiring_target="#/ports/mw_outputs/con1/3/1"), "qubits.q1.z")
        assert _codes(r["notes"]) == ["not_lf_port"]

    def test_tuple_port(self):
        r = Z.resolve_zline(_merged(opx_output=["con1", 5]), "qubits.q1.z")
        assert _codes(r["notes"]) == ["tuple_port"]

    def test_entities_are_qubit_z_and_pair_couplers(self):
        ents = Z.zline_entities(_merged())
        assert [(e["id"], e["kind"]) for e in ents] == [("q1", "qubit"), ("q1-2", "coupler")]
        rows = [Z.zline_row(_merged(), e) for e in ents]
        assert rows[0]["ok"] and rows[0]["n_exp"] == 1
        assert rows[1]["ok"] and "no_filters" in _codes(rows[1]["notes"])


# ---------------------------------------------------------------------------
# the docstring quotes are verbatim
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _module_quotes():
    doc = Z.__doc__
    # indented quote blocks in the module docstring: lines starting with 4 spaces + "
    blocks = re.findall(r'\n    "(.+?)"\n', doc, flags=re.S)
    src = Path(Z.__file__).read_text(encoding="utf-8")
    src = re.sub(r"\n\s*#: ?", " ", src)                  # join comment continuations
    inline = re.findall(r'\[paper: [^"\]]*"([^"]+)"', src)
    inline = [q for q in inline if not q.startswith("`")]  # opx1000_fems range quotes checked below
    return [_norm(b.replace("\n    ", " ")) for b in blocks], [_norm(q) for q in inline]


class TestQuotesVerbatim:
    @pytest.fixture()
    def corpus(self):
        files = [DOCS / "output_filter.md", DOCS / "opx1000_fems.md"]
        if not all(f.exists() for f in files):
            pytest.skip("QM docs repo not on this machine")
        text = " ".join(f.read_text(encoding="utf-8") for f in files)
        try:
            import qualang_tools.digital_filters as dfm
            d = Path(dfm.__file__).parent
            text += " " + (d / "README_IIR_filters.md").read_text(encoding="utf-8")
            text += " " + (d / "filters.py").read_text(encoding="utf-8")
        except Exception:
            pytest.skip("qualang_tools not importable")
        return _norm(text)

    def test_every_quote_is_verbatim(self, corpus):
        blocks, inline = _module_quotes()
        assert len(blocks) >= 14 and len(inline) >= 4
        missing = [q for q in blocks + inline if q not in corpus]
        assert not missing, missing

    def test_the_checker_catches_a_changed_word(self, corpus):
        blocks, _ = _module_quotes()
        assert blocks[0].replace("48 taps", "44 taps") not in corpus


# ---------------------------------------------------------------------------
# verifier round 2 (2026-09-26): each class below is one reproduced defect
# ---------------------------------------------------------------------------

class TestRepeatedTau:
    """A repeated tau shares the factor (s + 1/tau) between H's numerator and
    denominator; built unmerged, a root of H sat exactly on a pole and the
    Newton polish divided by zero (a 500 on the whole page)."""

    @pytest.mark.parametrize("port", [
        {"high_pass_filter": 100.0, "exponential_filter": [[1.0, 100.0]], "exponential_dc_gain": None},
        {"exponential_filter": [[0.5, 50.0], [0.5, 50.0]], "exponential_dc_gain": 0.0},
        {"exponential_filter": [[0.3, 50.0], [0.2, 50.0], [-0.1, 7.0]], "exponential_dc_gain": 0.0},
    ])
    def test_duplicated_tau_with_dc_gain_zero_draws(self, port):
        pf, notes = Z.parse_port_filter(port)
        assert pf is not None, notes
        assert Z.step_response(pf)["dc_limit"] is None      # A_dc = 0: an integrator

    def test_repeated_tau_is_the_merged_set(self):
        pf2, n2 = _pf([(-0.05, 50.0), (-0.05, 50.0)])
        pf1, _ = _pf([(-0.1, 50.0)])
        assert pf2 is not None, n2
        assert "ill_conditioned" not in _codes(n2)
        assert np.allclose(Z.step_response(pf2)["both"], Z.step_response(pf1)["both"], atol=1e-12)

    def test_cascade_keeps_both_stages(self):
        # two cascaded stages with one tau are NOT one stage (QOP <= 3.4)
        pf2, _ = _pf([(-0.05, 50.0), (-0.05, 50.0)])
        pf1, _ = _pf([(-0.1, 50.0)])
        a = Z.step_response(pf2, model="cascade")["both"]
        b = Z.step_response(pf1, model="cascade")["both"]
        assert not np.allclose(a, b, atol=1e-6)

    def test_parse_never_raises(self, monkeypatch):
        def boom(_port):
            raise ZeroDivisionError("complex division by zero")
        monkeypatch.setattr(Z, "_parse_port_filter", boom)
        pf, notes = Z.parse_port_filter({"exponential_filter": [[-0.1, 20.0]]})
        assert pf is None and "model_error" in _codes(notes)
        assert notes[0]["level"] == "block"

    @pytest.mark.parametrize("model", Z.MODELS)
    def test_stability_never_raises(self, monkeypatch, model):
        def boom(_pf):
            raise ZeroDivisionError("complex division by zero")
        pf, _ = Z.parse_port_filter({"exponential_filter": [[-0.1, 20.0]]})
        monkeypatch.setattr(Z, "_correction_analog", boom)
        v = Z.model_stability(pf, model)
        assert Z.model_blocked(v) and _codes(v) == ["model_error"]


class TestModelDrawnIsModelReported:
    """dc_limit, the horizon and the notes describe the curve actually drawn."""

    PORTS = [
        {"exponential_filter": [[-0.05, 20.0], [0.03, 3000.0]]},
        {"exponential_filter": [[0.1, 40.0]], "exponential_dc_gain": 0.8},
        {"exponential_dc_gain": 0.8},
        {"exponential_filter": [[-0.2, 5.0]], "feedforward_filter": [0.7, 0.2, 0.05]},
        {"exponential_filter": [[-0.02, 12000.0]], "exponential_dc_gain": 1.1},
    ]

    @pytest.mark.parametrize("model", ["sum", "cascade"])
    @pytest.mark.parametrize("i", range(len(PORTS)))
    def test_final_reaches_the_reported_dc_limit(self, i, model):
        pf, notes = Z.parse_port_filter(self.PORTS[i])
        assert pf is not None, notes
        st = Z.step_response(pf, model=model)
        assert not st["truncated"]
        assert st["final"] == pytest.approx(st["dc_limit"], rel=2e-3), (i, model)

    def test_cascade_says_it_ignores_dc_gain(self):
        pf, _ = Z.parse_port_filter({"exponential_dc_gain": 0.8})
        assert "dc_gain_not_in_cascade" in _codes(Z.model_notes(pf, "cascade"))
        assert Z.model_notes(pf, "sum") == []

    def test_cascade_high_pass_has_the_documented_half_second_decay(self):
        pf, _ = Z.parse_port_filter({"high_pass_filter": 1e5})
        (z, p, k), = Z._analog_stages(pf, "cascade")
        assert len(p) == 1 and p[0].real == pytest.approx(-1.0 / 5e8, rel=1e-9)
        assert Z.step_response(pf, model="cascade")["dc_limit"] == pytest.approx(5e8 / 1e5)
        assert "hp_cascade_decay" in _codes(Z.model_notes(pf, "cascade"))
        # QOP >= 3.5: the ideal integrator, no DC limit
        assert Z.step_response(pf, model="sum")["dc_limit"] is None

    def test_horizon_follows_the_correction_not_the_taus(self):
        # QM's recommended leaky high-pass: settles with tau_hp / A_dc = 100 ms
        pf, _ = Z.parse_port_filter({"exponential_filter": [[0.999, 1e5]], "exponential_dc_gain": 0.001})
        st = Z.step_response(pf)
        assert st["truncated"] and st["horizon_ns"] == Z.STEP_MAX_NS
        assert Z._slowest_correction_ns(pf, "sum") == pytest.approx(1e5 / 0.001, rel=1e-6)


# ---------------------------------------------------------------------------
# verifier round 3 (2026-09-26): each class below is one reproduced defect
# ---------------------------------------------------------------------------

class TestKeyIsTheWholeModel:
    """The memo key left out ``high_pass``: a high-pass port and the explicit
    ``dc_gain = 0`` + ``[[1, h]]`` port shared a key but draw different
    cascades (a leaky 0.5 s stage vs an A_dc = 1 stage)."""

    def test_the_two_high_pass_spellings_have_different_keys(self):
        hp, _ = Z.parse_port_filter({"exponential_filter": [], "high_pass_filter": 1000.0})
        ex, _ = Z.parse_port_filter({"exponential_dc_gain": 0.0, "exponential_filter": [[1.0, 1000.0]]})
        assert hp.exponential == ex.exponential and hp.dc_gain == ex.dc_gain
        a = Z.step_response(hp, model="cascade")
        b = Z.step_response(ex, model="cascade")
        assert a["final"] != pytest.approx(b["final"], rel=1e-3)      # the models really differ
        assert hp.key() != ex.key()

    def test_every_field_is_in_the_key(self):
        import dataclasses
        base = Z.PortFilter(((-0.1, 20.0),), 1.0, (0.9, 0.1), 1e9, "mw", "direct", None)
        other = {"exponential": ((-0.2, 20.0),), "dc_gain": 0.5, "feedforward": (1.0,),
                 "sampling_rate": 2e9, "upsampling_mode": "pulse", "output_mode": "amplified",
                 "high_pass": 20.0}
        assert set(other) == {f.name for f in dataclasses.fields(Z.PortFilter)}
        for name, v in other.items():
            assert dataclasses.replace(base, **{name: v}).key() != base.key(), name


class TestStabilityIsPerModel:
    """Stability was judged with the QOP >= 3.5 sum model only, so a set that
    is a stable cascade got no curve under either model, and a cascade stage
    that is unstable surfaced as an anonymous, duplicated ValueError."""

    def test_unstable_sum_stable_cascade_draws_the_cascade(self):
        pf, notes, v = Z.analyze_port({"exponential_filter": [[-0.6, 10.0], [-0.6, 1000.0]]})
        assert pf is not None, notes
        assert Z.model_blocked(v["sum"]) and "unstable" in _codes(v["sum"])
        assert v["cascade"] == []
        st = Z.step_response(pf, model="cascade")
        assert st["final"] == pytest.approx(st["dc_limit"], rel=2e-3)

    def test_stable_sum_unstable_cascade_names_the_stage(self):
        pf, notes, v = Z.analyze_port({"exponential_filter": [[-1.2, 10.0], [0.5, 1000.0]]})
        assert pf is not None and v["sum"] == []
        assert _codes(v["cascade"]) == ["unstable"]
        t = v["cascade"][0]["text"]
        assert "stage 1 of 2" in t and "A = -1.2" in t and "QOP <= 3.4" in t

    @pytest.mark.parametrize("exps,sum_code,casc_code", [
        ([[-1.5, 50.0]], "unstable", "unstable"),
        ([[-1.0, 50.0]], "improper", "improper"),
        ([[-0.6, 50.0], [-0.6, 5.0]], "unstable", None),
        ([[-1.2, 10.0], [0.5, 1000.0]], None, "unstable"),
    ])
    def test_verdict_per_model(self, exps, sum_code, casc_code):
        pf, _, v = Z.analyze_port({"exponential_filter": exps})
        assert pf is not None
        for m, code in (("sum", sum_code), ("cascade", casc_code)):
            assert (code in _codes(v[m])) if code else v[m] == [], (m, v[m])
            # the verdict agrees with what the model can actually draw
            if code:
                with pytest.raises(ValueError):
                    Z.step_response(pf, model=m)
            else:
                assert math.isfinite(Z.step_response(pf, model=m)["final"])

    def test_row_blocks_only_when_no_model_draws(self):
        ent = {"id": "q", "kind": "qubit", "channel_path": "qubits.q.z"}
        def row(exps):
            return Z.row_from_resolved(ent, {"port_path": "p", "notes": [],
                                             "port": {"exponential_filter": exps}})
        r = row([[-0.6, 10.0], [-0.6, 1000.0]])
        assert r["ok"] and r["models"] == {"sum": "UNSTABLE", "cascade": "ok"}
        assert "block" not in [n["level"] for n in r["notes"]]
        r = row([[-1.5, 50.0]])
        assert not r["ok"] and r["models"] == {"sum": "UNSTABLE", "cascade": "UNSTABLE"}
        assert "block" in [n["level"] for n in r["notes"]]

