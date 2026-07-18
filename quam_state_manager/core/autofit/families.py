"""Autofit family registry — per-node-family calibration knowledge (docs/56 §4).

One code-curated registry (repo doctrine: code + parity tests, never YAML).
Each family bundles everything the gate pipeline and the decision policy need:

* ``match`` — normalized-name matching (same normalizer as fit_targets/recipes)
* ``metric_gates`` (G2) — bands over the node's own fit metrics
* ``value_key`` + ``plausibility`` (G4) — physical bands + max relative jump
  vs the chip's current value
* ``feature_check`` (G3) — the family-specific raw-data cross-check spec;
  ONLY families whose stored value is provably the swept-feature location get
  one (docs/47 A1); 2-D/cluster/oscillation families honestly opt out.
* ``updates`` — writable state targets with op semantics beyond fit_targets'
  ``value×scale`` model: ``assign | subtract_from_current | assign_ceil4``.
  Where FIT_TARGET_MAP already covers a (family, fit_key), the path here MUST
  agree (pinned by a parity test) — fit_targets stays the UI's source of truth.
* ``adaptations`` — failure-mode → parameter-override rules for the re-measure
  loop. Window math derives from the family's sweep parameters, never from
  any LLM output (docs/56 §1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# normalized family key (matches _normalize_node_name output of the node name)


def normalize_node_name(name: str) -> str:
    """Reuse the registry normalizer so autofit matches exactly like recipes/
    fit_targets do (graph prefixes 1Q_/2Q_, numeric node prefixes, case)."""
    from quam_state_manager.core.interactive_plots.registry import _normalize_node_name
    return _normalize_node_name(name or "")


@dataclass
class MetricGate:
    key: str                    # fit_results key
    min: float | None = None
    max: float | None = None
    reason: str = ""


@dataclass
class FeatureCheck:
    """G3 spec: locate the swept feature in ds_raw and compare to the claim."""
    var: str                    # data var to scan (first target row)
    axis_var: str               # coordinate carrying the claimed value's units
    mode: str = "peak"          # peak | dip
    claim_key: str = "frequency"
    tol_fwhm: float = 2.0       # |feature - claim| tolerance, in FWHM units
    fwhm_key: str = "fwhm"      # fit key giving the linewidth (fallback below)
    fallback_tol: float = 5e6   # absolute tolerance when no fwhm in fit
    # when the swept axis is RELATIVE (e.g. "detuning"), the absolute claim is
    # compared against axis + <pre-update state value at this path> — resolved
    # by the caller via the patches-first rule (measurement-time center).
    axis_offset_path: str | None = None


@dataclass
class UpdateSpec:
    fit_key: str
    path: str                   # dot-path template with {q}/{pair}/{operation}
    op: str = "assign"          # assign | subtract_from_current | assign_ceil4
    label: str = ""


@dataclass
class Plausibility:
    key: str
    lo: float | None = None     # hard physical band (value itself)
    hi: float | None = None
    max_abs_jump: float | None = None   # |new - anchor| ceiling (absolute units)
    max_rel_jump: float | None = None   # |new - anchor| / |anchor| ceiling
    state_path: str | None = None       # where the anchor lives (dot template);
                                        # the PRE-update value is preferred (the
                                        # node may have already applied itself)


@dataclass
class Family:
    key: str                    # normalized family key
    label: str
    kind: str                   # qubits | qubit_pairs
    value_key: str              # the family's headline fitted value
    metric_gates: list[MetricGate] = field(default_factory=list)
    plausibility: list[Plausibility] = field(default_factory=list)
    feature_check: FeatureCheck | None = None
    updates: list[UpdateSpec] = field(default_factory=list)
    adaptations: dict[str, Callable[[dict], dict]] = field(default_factory=dict)
    # cross-metric consistency checks: fit_entry -> failure reason | None.
    # Run in G2; a hit is a suspect(wrong_peak) — internally-inconsistent fits.
    consistency_checks: list[Callable[[dict], str | None]] = field(
        default_factory=list)


# ---------------------------------------------------------------------------
# Adaptation rule helpers (all pure param-dict → override-dict)
# ---------------------------------------------------------------------------

def _widen_span(factor: float, default_mhz: float):
    def rule(params: dict) -> dict:
        span = float(params.get("frequency_span_in_mhz", default_mhz))
        return {"frequency_span_in_mhz": span * factor,
                "num_shots": int(float(params.get("num_shots", 400)) * 2)}
    return rule


def _more_shots(params: dict) -> dict:
    return {"num_shots": int(float(params.get("num_shots", 400)) * 4)}


def _spec_wrong_peak(params: dict) -> dict:
    """wrong_peak for spectroscopy: the sweep center is PINNED to state
    (full_freq = detuning + RF_frequency) and no node param can recenter it —
    a narrow-without-recenter would evict a true peak sitting off-center
    (design-review physics #2). The honest knobs: denser+wider scan at HALF
    the drive amplitude, which kills the classic power-broadened / two-photon
    ghost lines while the revert has already re-centered the sweep on the
    pre-step (good) state value."""
    span = float(params.get("frequency_span_in_mhz", 60.0))
    step = float(params.get("frequency_step_in_mhz", span / 300.0))
    amp = float(params.get("operation_amplitude_factor", 1.0))
    return {"frequency_span_in_mhz": span * 2.0,
            "frequency_step_in_mhz": step / 2.0,
            "operation_amplitude_factor": amp / 2.0}


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

_R2_FLOOR = 0.75

FAMILIES: dict[str, Family] = {}


def _register(f: Family) -> None:
    FAMILIES[f.key] = f


_register(Family(
    key="resonator_spectroscopy",
    label="Resonator spectroscopy",
    kind="qubits",
    value_key="frequency",
    metric_gates=[MetricGate("r2", min=_R2_FLOOR, reason="fit quality")],
    plausibility=[Plausibility("frequency", lo=2e9, hi=15e9, max_abs_jump=50e6,
                               state_path="qubits.{q}.resonator.f_01")],
    # rotated-S21 channel: argmin of IQ_abs can be MHz off the node's fitted
    # dip (docs/47 §resonator DEFER) — still a valid *coarse* check at a wide
    # tolerance: it catches sidelobe/no-signal, never adjudicates kHz.
    feature_check=FeatureCheck(var="IQ_abs", axis_var="full_freq", mode="dip",
                               claim_key="frequency", tol_fwhm=8.0),
    updates=[UpdateSpec("frequency", "qubits.{q}.resonator.f_01",
                        label="Resonator frequency"),
             UpdateSpec("frequency", "qubits.{q}.resonator.RF_frequency",
                        label="Resonator RF frequency")],
    adaptations={"no_signal": _widen_span(2.0, 60.0), "noisy": _more_shots,
                 "wrong_peak": _spec_wrong_peak,
                 "out_of_band": _widen_span(2.0, 60.0)},
))

_register(Family(
    key="qubit_spectroscopy",
    label="Qubit spectroscopy",
    kind="qubits",
    value_key="frequency",
    metric_gates=[MetricGate("r2", min=_R2_FLOOR, reason="fit quality"),
                  MetricGate("contrast", min=0.05, reason="no discernible peak"),
                  MetricGate("fwhm", max=30e6, reason="peak too broad")],
    plausibility=[Plausibility("frequency", lo=1e9, hi=12e9, max_abs_jump=100e6,
                               state_path="qubits.{q}.f_01")],
    feature_check=FeatureCheck(var="IQ_abs", axis_var="full_freq", mode="peak",
                               claim_key="frequency", tol_fwhm=2.0),
    updates=[UpdateSpec("frequency", "qubits.{q}.f_01", label="Qubit f_01"),
             UpdateSpec("frequency", "qubits.{q}.xy.RF_frequency",
                        label="XY RF frequency")],
    adaptations={"no_signal": _widen_span(2.0, 100.0), "noisy": _more_shots,
                 "wrong_peak": _spec_wrong_peak,
                 "out_of_band": _widen_span(2.0, 100.0)},
))

_register(Family(
    key="power_rabi",
    label="Power Rabi",
    kind="qubits",
    value_key="opt_amp",
    # 2-D error-amplification — no honest 1-D peak check (docs/47 A4); a
    # prefactor outside [0.5, 2] can't come from the ±40% sweep and flags the
    # classic ×3 Rabi-harmonic lock.
    metric_gates=[],
    plausibility=[Plausibility("opt_amp", lo=0.0, hi=1.5, max_abs_jump=0.25,
                               state_path="qubits.{q}.xy.operations.x180.amplitude"),
                  Plausibility("opt_amp_prefactor", lo=0.5, hi=2.0)],
    feature_check=FeatureCheck(var="I", axis_var="amp_prefactor", mode="span",
                               claim_key="opt_amp"),
    updates=[UpdateSpec("opt_amp", "qubits.{q}.xy.operations.x180.amplitude",
                        label="x180 amplitude")],
    adaptations={"noisy": _more_shots, "no_signal": _more_shots},
))

_register(Family(
    key="ramsey",
    label="Ramsey (T2*)",
    kind="qubits",
    value_key="freq_offset",
    metric_gates=[],
    plausibility=[
        # a Ramsey offset beyond the artificial detuning scale is a beat/alias
        Plausibility("freq_offset", lo=-5e6, hi=5e6),
        Plausibility("decay", lo=0.5e-6, hi=1e-3, max_rel_jump=4.0,
                     state_path="qubits.{q}.T2ramsey"),
    ],
    feature_check=FeatureCheck(var="I", axis_var="idle_time", mode="span"),
    updates=[UpdateSpec("freq_offset", "qubits.{q}.f_01",
                        op="subtract_from_current", label="Qubit f_01 (−offset)"),
             UpdateSpec("freq_offset", "qubits.{q}.xy.RF_frequency",
                        op="subtract_from_current", label="XY RF (−offset)"),
             UpdateSpec("decay", "qubits.{q}.T2ramsey", label="T2*")],
    # ramsey has NO span param — its knobs are shots + the artificial
    # detuning (design-review physics #8)
    adaptations={"noisy": _more_shots, "no_signal": _more_shots,
                 "out_of_band": lambda p: {
                     "frequency_detuning_in_mhz":
                         float(p.get("frequency_detuning_in_mhz", 1.0)) * 2,
                     "num_shots": int(float(p.get("num_shots", 400)) * 2)}},
))

_register(Family(
    key="T1",
    label="T1 relaxation",
    kind="qubits",
    value_key="t1",
    metric_gates=[],            # error-bar RATIO is checked generically in gates.py
    plausibility=[Plausibility("t1", lo=0.5e-6, hi=1e-3, max_rel_jump=2.5,
                               state_path="qubits.{q}.T1")],
    feature_check=FeatureCheck(var="I", axis_var="idle_time", mode="span"),
    updates=[UpdateSpec("t1", "qubits.{q}.T1", label="T1")],
    adaptations={"noisy": _more_shots, "no_signal": _more_shots},
))

_register(Family(
    key="echo",
    label="Echo (T2echo)",
    kind="qubits",
    value_key="T2_echo",
    metric_gates=[],
    plausibility=[Plausibility("T2_echo", lo=0.5e-6, hi=1e-3, max_rel_jump=2.5,
                               state_path="qubits.{q}.T2echo")],
    feature_check=FeatureCheck(var="I", axis_var="idle_time", mode="span"),
    updates=[UpdateSpec("T2_echo", "qubits.{q}.T2echo", label="T2 echo")],
    adaptations={"noisy": _more_shots, "no_signal": _more_shots},
))

_register(Family(
    key="readout_frequency_optimization",
    label="Readout frequency optimization",
    kind="qubits",
    value_key="optimal_frequency",
    metric_gates=[],
    plausibility=[Plausibility("optimal_frequency", lo=2e9, hi=15e9,
                               max_abs_jump=30e6,
                               state_path="qubits.{q}.resonator.f_01")],
    feature_check=FeatureCheck(var="snr", axis_var="detuning", mode="peak",
                               claim_key="optimal_frequency", tol_fwhm=0.0,
                               fallback_tol=8e6,
                               axis_offset_path="qubits.{q}.resonator.RF_frequency"),
    updates=[UpdateSpec("optimal_frequency", "qubits.{q}.resonator.f_01",
                        label="Readout frequency"),
             UpdateSpec("optimal_frequency", "qubits.{q}.resonator.RF_frequency",
                        label="Readout RF frequency")],
    adaptations={"no_signal": _widen_span(2.0, 20.0), "noisy": _more_shots,
                 "wrong_peak": _spec_wrong_peak,
                 "out_of_band": _widen_span(2.0, 20.0)},
))

_register(Family(
    key="iq_blobs",
    label="IQ blobs",
    kind="qubits",
    value_key="iw_angle",
    metric_gates=[MetricGate("readout_fidelity", min=60.0,
                             reason="blobs unseparable")],
    plausibility=[Plausibility("iw_angle", lo=-7.0, hi=7.0)],
    # clusters — no swept axis, honestly no feature check (docs/47).
    # VERIFY-ONLY: node versions disagree on whether the fitted iw_angle is an
    # absolute angle or a correction DELTA the node *subtracts*
    # (16_iq_blobs.py: `integration_weights_angle -= iw_angle`) — an assign
    # write would be wrong on the delta-convention nodes whenever the current
    # angle ≠ 0 (design-review physics #9). Never guess a sign convention:
    # the node's own write stands; autofit gates it but stages nothing.
    updates=[],
    adaptations={"noisy": _more_shots},
))

def _chevron_len_vs_j(entry: dict) -> str | None:
    """cz_len must agree with the fitted coupling: half swap period = 1/(2J).
    A doubled/halved length with a consistent J is the classic wrong-fringe
    lock — internally inconsistent, so reject without any external oracle."""
    import math as _math
    j = entry.get("J")
    ln = entry.get("cz_len")
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool)
               and _math.isfinite(x) and x > 0 for x in (j, ln)):
        return None
    expected_ns = 1e9 / (2.0 * float(j))
    if abs(float(ln) - expected_ns) / expected_ns > 0.5:
        return (f"cz_len={ln:.1f} ns inconsistent with J={j / 1e6:.2f} MHz "
                f"(expected ≈{expected_ns:.1f} ns)")
    return None


_register(Family(
    key="chevron_11_02",
    label="CZ chevron (11↔02)",
    kind="qubit_pairs",
    value_key="cz_amp",
    metric_gates=[],
    consistency_checks=[_chevron_len_vs_j],
    plausibility=[Plausibility("cz_amp", lo=0.0, hi=1.0, max_abs_jump=0.3,
                               state_path="qubit_pairs.{pair}.macros.cz_unipolar.flux_pulse_qubit.amplitude"),
                  Plausibility("cz_len", lo=8.0, hi=400.0)],
    feature_check=FeatureCheck(var="state_target", axis_var="time", mode="span"),
    updates=[UpdateSpec("cz_amp",
                        "qubit_pairs.{pair}.macros.cz_unipolar.flux_pulse_qubit.amplitude",
                        label="CZ flux amplitude"),
             UpdateSpec("cz_len",
                        "qubit_pairs.{pair}.macros.cz_unipolar.flux_pulse_qubit.length",
                        op="assign_ceil4", label="CZ length (ceil 4 ns)")],
    adaptations={"noisy": _more_shots, "no_signal": _more_shots},
))

_register(Family(
    key="cz_conditional_phase",
    label="CZ conditional phase",
    kind="qubit_pairs",
    value_key="optimal_amplitude",
    metric_gates=[],
    plausibility=[Plausibility("optimal_amplitude", lo=0.0, hi=1.0,
                               max_abs_jump=0.2,
                               state_path="qubit_pairs.{pair}.macros.{operation}.flux_pulse_qubit.amplitude")],
    feature_check=FeatureCheck(var="state_target", axis_var="amp", mode="span"),
    updates=[UpdateSpec("optimal_amplitude",
                        "qubit_pairs.{pair}.macros.{operation}.flux_pulse_qubit.amplitude",
                        label="CZ amplitude (cond. phase)")],
    # out_of_band on the error-amp variant: fall back to a coarse-width scan
    # (the ±0.5–1% error-amp window can simply miss — physics review #4)
    adaptations={"noisy": _more_shots, "no_signal": _more_shots,
                 "out_of_band": lambda p: {
                     "amp_range": min(float(p.get("amp_range", 0.01)) * 4, 0.05),
                     "num_shots": int(float(p.get("num_shots", 400)) * 2)}},
))


_register(Family(
    key="resonator_spectroscopy_vs_power",
    label="Resonator spectroscopy vs power",
    kind="qubits",
    value_key="resonator_frequency",
    # the docs/47 "genuinely hard family" (dressed/bare branch, rotated S21):
    # NO raw-data localizer — G1/G4 + node-faithful refit + vision carry it
    metric_gates=[],
    plausibility=[Plausibility("resonator_frequency", lo=2e9, hi=15e9,
                               max_abs_jump=50e6,
                               state_path="qubits.{q}.resonator.f_01")],
    updates=[UpdateSpec("resonator_frequency", "qubits.{q}.resonator.f_01",
                        label="Resonator frequency"),
             UpdateSpec("resonator_frequency",
                        "qubits.{q}.resonator.RF_frequency",
                        label="Resonator RF frequency")],
    adaptations={"noisy": _more_shots, "no_signal": _widen_span(2.0, 30.0),
                 "out_of_band": _widen_span(2.0, 30.0)},
))

_register(Family(
    key="qubit_spectroscopy_vs_power",
    label="Qubit spectroscopy vs power",
    kind="qubits",
    value_key="frequency",
    # 2-D power sweep — vision's domain (a self-consistent noise fit fools a
    # replay; the real-archive #575 case is the canonical example)
    metric_gates=[],
    plausibility=[Plausibility("frequency", lo=1e9, hi=12e9, max_abs_jump=100e6,
                               state_path="qubits.{q}.f_01")],
    updates=[UpdateSpec("frequency", "qubits.{q}.f_01", label="Qubit f_01"),
             UpdateSpec("frequency", "qubits.{q}.xy.RF_frequency",
                        label="XY RF frequency")],
    adaptations={"noisy": _more_shots,
                 "no_signal": _widen_span(2.0, 10.0),
                 "wrong_peak": _spec_wrong_peak,
                 "out_of_band": _widen_span(2.0, 10.0)},
))


# aliases seen in real archives (graph-prefixed, lab-suffixed, _new variants)
_ALIASES = {
    "resonator_spectroscopy_vs_power_iq": "resonator_spectroscopy_vs_power",
    "qubit_spectroscopy_vs_power_adaptive": "qubit_spectroscopy_vs_power",
    "resonator_spectroscopy_single": "resonator_spectroscopy",
    "resonator_spectroscopy_wide": "resonator_spectroscopy",
    "qubit_spectroscopy_new": "qubit_spectroscopy",
    "chevron_1102": "chevron_11_02",
    "chevron": "chevron_11_02",
    "cz_conditional_phase_error_amp": "cz_conditional_phase",
    "t1": "T1",
}


def family_for(node_name: str) -> Family | None:
    """Longest-prefix family match over the normalized node name."""
    norm = normalize_node_name(node_name)
    best: tuple[str, Family] | None = None
    for key, fam in FAMILIES.items():
        nk = normalize_node_name(key)
        if norm.startswith(nk) and (best is None or len(nk) > len(best[0])):
            best = (nk, fam)
    if best:
        return best[1]
    for alias, target in _ALIASES.items():
        na = normalize_node_name(alias)
        if norm.startswith(na):
            return FAMILIES[target]
    return None


def resolve_updates(fam: Family, target: str, fit_entry: dict,
                    run_parameters: dict | None,
                    current_value_of: Callable[[str], Any]) -> list[dict]:
    """Turn a target's fit entry into concrete write rows.

    Returns ``[{path, value, old_hint, label, op}]`` — ``value`` fully computed
    (op semantics applied against the CURRENT state via ``current_value_of``).
    Rows whose fit key is missing/non-numeric, or whose ``{operation}``
    placeholder can't be filled from run parameters, are skipped (never guess
    — the fit_targets doctrine).
    """
    import math as _math

    rows: list[dict] = []
    for spec in fam.updates:
        v = fit_entry.get(spec.fit_key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not _math.isfinite(v):
            continue
        path = spec.path.replace("{q}", target).replace("{pair}", target)
        if "{operation}" in path:
            op_name = (run_parameters or {}).get("operation")
            if not op_name:
                continue
            path = path.replace("{operation}", str(op_name))
        try:
            current = current_value_of(path)
        except Exception:
            current = None
        if spec.op == "subtract_from_current":
            if not isinstance(current, (int, float)) or isinstance(current, bool):
                continue                      # can't subtract from a pointer/None
            value = current - v
        elif spec.op == "assign_ceil4":
            value = int(_math.ceil(float(v) / 4.0) * 4)
        else:
            value = v
        rows.append({"path": path, "value": value, "old_hint": current,
                     "label": spec.label or spec.fit_key, "op": spec.op})
    return rows
