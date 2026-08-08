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


_ALIAS_HOPS = 8


def resolve_alias_path(path: str, value_of: Callable[[str], Any] | None
                       ) -> str | None:
    """Follow QUAM aliases so a write lands where the NODE writes.

    Real chips carry ``operations.x180 = "#./x180_DragCosine"`` — the run's
    ``operation`` parameter names the ALIAS while the node patches the TARGET
    (measured across three labs: 485 `x180_DragCosine` patches, zero `x180`
    ones). Writing to the alias path would store a field *under a pointer
    string*, so the operation segment has to be followed before the write.

    Only rewrites hops it can VERIFY by reading the result; a pointer it cannot
    follow returns ``None`` (refuse — writing INTO a pointer string is the
    hazard), while a segment the reader simply cannot answer is left alone: a
    path that does not exist fails loudly at the transactional write, whereas a
    silent rewrite would not.
    """
    if value_of is None:
        return path
    parts = path.split(".")
    i, hops = 0, 0
    # CONTAINER segments only — never the leaf. A leaf that currently holds a
    # pointer is a different question with a different answer: the nodes
    # themselves replace it with a number (`/quam/qubits/q/f_01`), so following
    # it would write somewhere the node never writes.
    while i < len(parts) - 1:
        prefix = parts[:i + 1]
        try:
            node = value_of(".".join(prefix))
        except Exception:  # noqa: BLE001 — unreadable prefix ⇒ nothing to follow
            i += 1
            continue
        if isinstance(node, str) and node.startswith("#"):
            hops += 1
            if hops > _ALIAS_HOPS:
                return None                      # pointer cycle — refuse
            if node.startswith("#/"):
                base, rest = [], node[2:].split("/")
            elif node.startswith("#./"):
                # the alias's own container, mirroring pointer_resolver's frame
                base, rest = prefix[:-1], node[3:].split("/")
            elif node.startswith("#../"):
                base, rest = prefix[:-2], node[4:].split("/")
            else:
                return None
            if not rest or any(not s for s in rest):
                return None
            parts = list(base) + rest + parts[i + 1:]
            i = len(base) + len(rest) - 1
            try:
                value_of(".".join(parts[:i + 1]))
            except Exception:  # noqa: BLE001 — unverifiable target ⇒ refuse
                return None
        i += 1
    return ".".join(parts)


def trend_path_for(fam, value_key: str, target: str,
                   run_parameters: dict | None = None,
                   value_of: Callable[[str], Any] | None = None) -> str | None:
    """The state dot-path whose HISTORY is this family's trend for ``value_key``.

    G5 asks "is this value drifting off its own history?" — the history that
    answers it lives at the field this family WRITES, so the anchor is resolved
    exactly the way :func:`resolve_updates` resolves the write itself: routed
    families (``z.flux_point`` picks independent vs joint offset) take the
    branch that will actually fire, because the two offsets carry genuinely
    different histories and comparing a value against the wrong one manufactures
    drift. Falls back to the plausibility entry's declared ``state_path``.

    Returns ``None`` — never a guess — when the family declares no writable home
    for the value (the verify-only coupler nodes), when an ``{operation}``
    placeholder cannot be filled from the run's own parameters, or when routing
    is undecidable.
    """
    ups = [u for u in fam.updates if u.fit_key == value_key]
    # The history at the written field is only THIS value's history when the
    # write is a plain assign. Ramsey writes `f_01 -= freq_offset`: the field
    # holds ~5 GHz while the fit key is a ~MHz offset, so comparing one against
    # the other reports a 450,000-sigma drift on a perfectly good run (measured).
    # That is the §15.5 defect one layer up — the fix there made G5 read the
    # right VALUE, this makes it read the right SERIES. No honest anchor exists
    # for an offset or a scaled write, so the gate abstains rather than invent
    # one (docs/78 §17).
    assigns = [u for u in ups if u.op == "assign" and u.factor == 1.0]
    if ups and not assigns:
        return None          # written, but not as this value — no anchor
    ups = assigns
    cand = None
    routed = [u for u in ups if u.route_on]
    if routed:
        def _sel(spec):
            if value_of is None:
                return None
            try:
                return value_of(spec.route_on.replace("{q}", target)
                                .replace("{pair}", target))
            except Exception:  # noqa: BLE001
                return None
        exact = [u for u in routed
                 if u.route_when and u.route_when != "*"
                 and str(_sel(u)) == u.route_when]
        if exact:
            cand = exact[0].path
        else:
            els = [u for u in routed if u.route_when == "*"]
            # An else-branch is only the answer once we could READ the selector;
            # with no reader we cannot tell "else" from "unknown".
            if els and value_of is not None and _sel(els[0]) is not None:
                cand = els[0].path
    elif ups:
        cand = ups[0].path
    if cand is None:
        for p in fam.plausibility:
            if p.key == value_key and p.state_path:
                cand = p.state_path
                break
    if not cand:
        return None
    path = cand.replace("{q}", target).replace("{pair}", target)
    if "{operation}" in path:
        op = (run_parameters or {}).get("operation")
        if not op:
            return None
        path = path.replace("{operation}", str(op))
    # the trend must be read where the value is WRITTEN, aliases included
    return resolve_alias_path(path, value_of)


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
    """One writable target, faithful to what the NODE itself does.

    Update targets are **run-derived, never hardcoded** (docs/78 D-14): the
    real nodes route by chip state and name their operation through a run
    parameter, and two families that look alike write differently.

    * ``op`` — ``assign`` | ``add_to_current`` | ``subtract_from_current`` |
      ``assign_ceil4``. Node 06 and node 09 both write a flux offset from the
      same fit key, but 06 ASSIGNS the joint offset while 09 INCREMENTS it;
      both increment-vs-assign errors are silent and compounding.
    * ``route_on`` / ``route_when`` — a state-routed path. The nodes pick the
      offset field from ``z.flux_point``; ``route_when="*"`` is the else-branch
      (node 06 writes joint for ANY non-"independent" value) and a family with
      no ``"*"`` spec writes NOTHING for an unmatched value (node 09's if/elif
      has no else — matching that matters more than "doing something").
    * ``guard`` — ``(fit_entry, run_parameters) -> bool``; the node's own
      pre-write conditions (an out-of-range offset, an opt-in flag).
    * ``factor`` — a fixed ratio the node itself applies (node 11 writes the
      π/2 amplitude as exactly half the fitted π amplitude: 477 of 479 real
      patch pairs are bit-exactly 0.5).
    """
    fit_key: str
    path: str                   # dot-path template with {q}/{pair}/{operation}
    op: str = "assign"
    label: str = ""
    route_on: str | None = None         # dot template of the deciding state field
    route_when: str | None = None       # its value; "*" = else-branch
    guard: Callable[[dict, dict], bool] | None = None
    factor: float = 1.0


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


@dataclass(frozen=True)
class Rung:
    """One rung of an adaptation ladder (docs/56 v2 — the human escalation
    vocabulary reconstructed from the real archive's operator loops).

    kinds:
      ``params``     — ``rule(params) → overrides`` (the v1 model)
      ``seed_shift`` — deterministic sweep-window relocation via an audited,
                       ledgered state write (rail ①: the shift magnitude is
                       window math over the family's span param; the DIRECTION
                       may come from a qualitative hint — edge evidence or
                       vision — never a number). The engine restores the seed
                       from its recorded pre-values if the step still fails
                       (rail ③); a success is overwritten by the node itself.
      ``escalate``   — insert a prerequisite family step (cross-node re-cal:
                       e.g. qubit visibility restored by re-centering readout)
                       before re-running this one.
    """
    kind: str = "params"
    rule: Callable[[dict], dict] | None = None
    seed_paths: tuple[str, ...] = ()      # dot templates ({q}/{pair}) to shift
    span_param: str = "frequency_span_in_mhz"
    span_default: float = 60.0
    shift_frac: float = 0.75              # |shift| = shift_frac × span
    escalate_family: str | None = None
    escalate_params: dict | None = None
    note: str = ""


@dataclass
class Family:
    key: str                    # normalized family key
    label: str
    kind: str                   # qubits | qubit_pairs
    value_key: str              # the family's headline fitted value
    metric_gates: list[MetricGate] = field(default_factory=list)
    plausibility: list[Plausibility] = field(default_factory=list)
    feature_check: FeatureCheck | None = None
    # fit key -> multiplier applied ONCE when that key is read, so the gate and
    # the write can never disagree about units (docs/78 §22.4 item 1). That
    # disagreement is exactly how the T1 defect survived: the band was written
    # in seconds, the write inherited the fit's nanoseconds, and neither had
    # been exercised. Measured: the chips store T1/T2 in SECONDS (n=8,379,
    # p50 3e-5) while node 05's fit reports ~3e4 — so an ungated write would
    # have put 30,000 SECONDS into a field where 30 microseconds belongs.
    # Scoped by measurement, not by family shape: ramsey's `decay` (n=635) and
    # echo's `T2_echo` (n=143) are already in seconds and are NOT scaled.
    fit_scale: dict[str, float] = field(default_factory=dict)
    updates: list[UpdateSpec] = field(default_factory=list)
    # Paths the node ALSO writes that the forward path deliberately does not
    # compute, each with why (measured against the archive, 2026-08-08). The
    # forward path only runs when the node wrote nothing, and writing half of
    # what the node writes is the "quiet partial" r12 forbids — so the gap is
    # DECLARED and ledgered, never silently skipped. Reverse-engineering the
    # node's formula to close it is what D-14 forbids; the honest move is to
    # say the write is incomplete.
    forward_gaps: dict[str, str] = field(default_factory=dict)
    # failure_mode → rule | [rung, …] — a bare callable is the v1 single
    # params rule (re-applied every retry); a list is a LADDER walked one
    # rung per use of that failure mode (rung index clamps at the end)
    adaptations: dict[str, Any] = field(default_factory=dict)
    # cross-metric consistency checks: fit_entry -> failure reason | None.
    # Run in G2; a hit is a suspect(wrong_peak) — internally-inconsistent fits.
    consistency_checks: list[Callable[[dict], str | None]] = field(
        default_factory=list)
    # post-discovery wide verification (docs/56 v2 — LOOP_STUDY case A's
    # "recover-then-verify"): after a target passes on a RETRY attempt whose
    # earlier failures were window-class (no_signal/wrong_peak/
    # feature_present_fit_failed), the engine inserts a one-shot wide scan
    # of the same family before trusting the discovery.
    verify_wide: dict | None = None


def fit_value(fam, fit_entry: dict, key: str) -> float | None:
    """THE one read of a fit number, with the family's unit scale applied.

    Every consumer goes through here — the plausibility band, the jump limit,
    the history trend and the write. That is the whole point: the T1 defect
    (docs/78 §22.4) existed because the band and the write reached the same key
    by two paths and disagreed about its unit, and neither path had been
    exercised on real data. One reader cannot disagree with itself.

    Returns None for anything that is not a finite real number — bools
    included, since ``True`` is an ``int`` in Python and has no business in a
    physical band.
    """
    import math as _math

    v = (fit_entry or {}).get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) \
            or not _math.isfinite(v):
        return None
    scale = (getattr(fam, "fit_scale", None) or {}).get(key)
    return float(v) * float(scale) if scale else float(v)


def rungs_for(fam: "Family", mode: str) -> list[Rung]:
    """Normalize an adaptations entry to its rung ladder. Legacy bare
    callables become a single params rung (re-applied every retry — the v1
    compounding behavior, unchanged)."""
    spec = (fam.adaptations or {}).get(mode)
    if spec is None:
        return []
    if callable(spec):
        return [Rung(kind="params", rule=spec)]
    out: list[Rung] = []
    for r in spec:
        out.append(Rung(kind="params", rule=r) if callable(r) else r)
    return out


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


def _rabi_narrow_window(params: dict) -> dict:
    """wrong_peak for power rabi: the classic failure is locking a Rabi
    HARMONIC — the fit reports an amplitude an integer fraction of the true one.
    The node's own knobs are the prefactor window and its step, so the rung
    halves the window about the parked amplitude (prefactor 1.0, which the
    revert has already restored to the pre-step value) and halves the step.
    A harmonic outside the tightened window can no longer be locked; the number
    itself is never edited."""
    lo = float(params.get("min_amp_factor", 0.8))
    hi = float(params.get("max_amp_factor", 1.2))
    step = float(params.get("amp_factor_step", max((hi - lo) / 100.0, 1e-4)))
    return {"min_amp_factor": 1.0 - (1.0 - lo) / 2.0,
            "max_amp_factor": 1.0 + (hi - 1.0) / 2.0,
            "amp_factor_step": step / 2.0,
            "num_shots": int(float(params.get("num_shots", 100)) * 2)}


def _step_refine(params: dict) -> dict:
    """feature_present_fit_failed: the archive's #194 class — a dip clearly
    visible in the window but the fit died on a too-coarse grid (step 0.05→0.5
    undersampled the linewidth; the operator burned 3 drive-strength attempts
    before densifying). The machine prescription, first try: HALVE the step,
    double the shots, keep the window."""
    span = float(params.get("frequency_span_in_mhz", 60.0))
    step = float(params.get("frequency_step_in_mhz", span / 300.0))
    return {"frequency_step_in_mhz": step / 2.0,
            "num_shots": int(float(params.get("num_shots", 400)) * 2)}


def _power_up(params: dict) -> dict:
    """no_feature ladder rung 2 — 'drive harder' (LOOP_STUDY case B: after a
    widen didn't surface the dip, the operator raised max_power −25→+5 and
    amp ×2.5). Only turns knobs the plan actually exposes (never invents a
    power axis on a node without one); magnitudes are fixed grid moves."""
    out: dict = {"num_shots": int(float(params.get("num_shots", 400)) * 2)}
    if "max_power_dbm" in params:
        out["max_power_dbm"] = min(float(params["max_power_dbm"]) + 10.0, 10.0)
    if "max_amp" in params:
        out["max_amp"] = min(float(params["max_amp"]) * 2.0, 1.0)
    if "operation_amplitude_factor" in params:
        out["operation_amplitude_factor"] = \
            float(params["operation_amplitude_factor"]) * 4.0
    return out


# seed-shift path sets (the sweep center each family is pinned to)
_XY_SEED = ("qubits.{q}.f_01", "qubits.{q}.xy.RF_frequency")
_RES_SEED = ("qubits.{q}.resonator.f_01", "qubits.{q}.resonator.RF_frequency")


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
    # CORPUS-CALIBRATED (docs/78 §15): dip_snr separates cleanly here —
    # accepted [8.4, 677] over 23 real targets, every one of 7 node-rejects
    # below it (their median 0). r² is safe as shipped (accepted floor 0.82).
    metric_gates=[MetricGate("r2", min=_R2_FLOOR, reason="fit quality"),
                  MetricGate("dip_snr", min=5.0,
                             reason="no significant dip above the noise")],
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
    adaptations={
        # no_feature ladder (LOOP_STUDY): widen → drive/average up → seed-
        # shift the state-pinned window (direction from edge evidence or a
        # vision hint; magnitude = window math)
        "no_signal": [_widen_span(2.0, 60.0), _more_shots,
                      Rung(kind="seed_shift", seed_paths=_RES_SEED,
                           span_default=60.0)],
        "noisy": _more_shots,
        "wrong_peak": _spec_wrong_peak,
        "feature_present_fit_failed": [_step_refine, _spec_wrong_peak],
        "out_of_band": _widen_span(2.0, 60.0)},
    verify_wide={"span_param": "frequency_span_in_mhz", "factor": 4.0,
                 "span_default": 60.0},
))

_register(Family(
    key="qubit_spectroscopy",
    label="Qubit spectroscopy",
    kind="qubits",
    value_key="frequency",
    # CORPUS-CALIBRATED (docs/78 §15): over 49 real targets the node ACCEPTED
    # r² as low as 0.452, so the old `r2 >= 0.75` flagged 12/34 good fits as
    # suspects — the node's v3 gate is peak-SNR + dominance and stopped gating
    # on r² at all. peak_snr separates perfectly here (accepted [6.6, 24.3],
    # every one of 15 node-rejects below it, their median 3.0), so it leads and
    # r² stays only as a coarse garbage backstop under the observed floor.
    metric_gates=[MetricGate("peak_snr", min=5.0,
                             reason="no significant peak above the noise"),
                  MetricGate("r2", min=0.30, reason="fit quality (coarse floor)"),
                  MetricGate("contrast", min=0.05, reason="no discernible peak"),
                  MetricGate("fwhm", max=30e6, reason="peak too broad")],
    # jump limit measured against the nodes' OWN accepted patches (docs/78
    # §15.2b): 2 of 114 accepted moves exceeded the old 100 MHz (max 139 MHz).
    # No node-rejected target produces a patch at all, so the limit has zero
    # measured detection value — it is a sanity envelope, and real drift
    # detection is G5's job now that it is actually wired.
    plausibility=[Plausibility("frequency", lo=1e9, hi=12e9, max_abs_jump=200e6,
                               state_path="qubits.{q}.f_01")],
    feature_check=FeatureCheck(var="IQ_abs", axis_var="full_freq", mode="peak",
                               claim_key="frequency", tol_fwhm=2.0),
    updates=[UpdateSpec("frequency", "qubits.{q}.f_01", label="Qubit f_01"),
             UpdateSpec("frequency", "qubits.{q}.xy.RF_frequency",
                        label="XY RF frequency")],
    adaptations={
        # ladder rung 4 = cross-node escalation (LOOP_STUDY case A: qubit
        # visibility came back only after the READOUT was re-calibrated —
        # a same-node knob can't fix a mis-centered readout)
        "no_signal": [_widen_span(2.0, 100.0), _power_up,
                      Rung(kind="seed_shift", seed_paths=_XY_SEED,
                           span_default=100.0),
                      Rung(kind="escalate",
                           escalate_family="resonator_spectroscopy",
                           note="re-center readout, then retry")],
        "noisy": _more_shots,
        "wrong_peak": _spec_wrong_peak,
        "feature_present_fit_failed": [_step_refine, _spec_wrong_peak],
        "out_of_band": _widen_span(2.0, 100.0)},
    verify_wide={"span_param": "frequency_span_in_mhz", "factor": 4.0,
                 "span_default": 100.0},
))

_register(Family(
    key="power_rabi",
    label="Power Rabi",
    kind="qubits",
    value_key="opt_amp",
    # 2-D error-amplification — no honest 1-D peak check (docs/47 A4).
    # CORPUS-CALIBRATED (docs/78 §15) over 63 real targets:
    #  * multipulse_fit_quality separates (accepted [0.37, 0.82], rejected
    #    median 0.12) — the strongest numeric signal this family has;
    #  * the OLD prefactor band [0.5, 2.0] was a HARD G4 fail and rejected
    #    4/55 fits the node accepted (real accepted prefactors run down to
    #    0.20 during bring-up, when the parked amplitude is far off). The
    #    honest check is not a fixed window but the node's own
    #    `prefactor_extrapolated` flag — an optimum outside the SWEPT range —
    #    so the band is now only a sanity envelope and the flag does the work.
    metric_gates=[MetricGate("multipulse_fit_quality", min=0.30,
                             reason="error-amplification fit does not track the raw data")],
    #  * the jump limit was measured the same way (docs/78 §15.2b): 35 of 1004
    #    accepted pi-amplitude moves exceeded the old 0.25 (max 0.538), because
    #    a first successful Rabi after bring-up legitimately moves the parked
    #    amplitude a long way. 0.8 keeps a sanity envelope under the 1.5 band.
    plausibility=[Plausibility("opt_amp", lo=0.0, hi=1.5, max_abs_jump=0.8,
                               state_path="qubits.{q}.xy.operations.{operation}.amplitude"),
                  Plausibility("opt_amp_prefactor", lo=0.05, hi=5.0)],
    feature_check=FeatureCheck(var="I", axis_var="amp_prefactor", mode="span",
                               claim_key="opt_amp"),
    # The operation is a RUN PARAMETER, not a constant: this chip's pulse is
    # `x180_DragCosine`, so the old hardcoded `…operations.x180.amplitude`
    # addressed a field that does not exist here (docs/78 D-14). `{operation}`
    # is filled from the run's own parameters, and the row is SKIPPED rather
    # than guessed when the run does not name one.
    # The node writes BOTH amplitudes when `update_x90` is set — and it is set
    # in every one of 222 real runs. π/2 is exactly half of π (477 of 479 real
    # patch pairs bit-exact), so omitting it would leave every x90 gate stale
    # behind a freshly calibrated x180 (docs/78 §15.4b).
    updates=[UpdateSpec("opt_amp", "qubits.{q}.xy.operations.{operation}.amplitude",
                        label="pi amplitude"),
             UpdateSpec("opt_amp", "qubits.{q}.xy.operations.x90.amplitude",
                        factor=0.5,
                        guard=lambda e, p: bool(p.get("update_x90")),
                        label="pi/2 amplitude (half of pi)")],
    consistency_checks=[
        lambda e: ("raw fit and multipulse estimator disagree"
                   if e.get("raw_fit_consistent") is False else None),
        lambda e: ("pi prefactor lies outside the swept range (extrapolated)"
                   if e.get("prefactor_extrapolated") else None),
        lambda e: ("pi amplitude exceeds what the port can play"
                   if e.get("pi_amp_reachable") is False else None),
    ],
    adaptations={"noisy": _more_shots,
                 "no_signal": [_more_shots, _power_up],
                 "feature_present_fit_failed": _more_shots,
                 # Every consistency-check hit is emitted as `wrong_peak`
                 # (gates G2), and this family declares three — so without a
                 # rung a harmonic lock DEFERS instead of re-measuring, which
                 # is exactly the case §15.7 re-based from fail to suspect.
                 # The honest knob is the node's own window: a locked harmonic
                 # is resolved by scanning tighter and finer around the parked
                 # amplitude, never by editing the fitted number.
                 "wrong_peak": _rabi_narrow_window},
    # docs/78 §17.6 — the family had NO wide verification, and the obvious
    # generalization ("span x 4", as the four spectroscopy families use) is
    # REFUTED by the corpus (230 real runs, 899 accepted prefactors):
    #
    #  * power_rabi is TWO experiments, not one with different settings. The
    #    survey mode runs 1 pulse over the full prefactor window (103 of 122
    #    coarse runs use exactly [0.001, 1.99], step 0.005); the
    #    error-amplification mode runs 20-160 pulses over a window whose
    #    median width is 0.3. Pulse count and window width are anti-correlated
    #    and physically coupled — N pulses alias unless the range stays inside
    #    roughly 1/N of a Rabi period about 1.0.
    #  * so widening the NARROW window x4 is wrong twice over: it still stops
    #    at [0.6, 1.6] (short of the survey range that accepted optima span,
    #    0.0024-2.366) AND it keeps the high pulse count, where that range
    #    folds. It would not survey; it would alias.
    #
    # The honest broad survey here is the lab's OWN survey mode — and it is
    # exactly the measurement that separates a true pi amplitude from a locked
    # harmonic, because a full single-pulse Rabi curve shows the whole
    # oscillation. `num_shots` is deliberately NOT pinned: whatever averaging
    # the ladder climbed to is kept (more shots never weakens a confirmation).
    verify_wide={"survey_params": {"max_number_pulses_per_sweep": 1,
                                   "min_amp_factor": 0.001,
                                   "max_amp_factor": 1.99,
                                   "amp_factor_step": 0.005},
                 "note": "full single-pulse Rabi survey about the newly "
                         "parked amplitude — a harmonic lock cannot survive it"},
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
    # the node reports t1 in NANOSECONDS and the chip stores seconds — the band
    # above (correct, and matching the 8,379 stored values) rejected 6 of 6
    # accepted fits, and the write below would have been off by 1e9
    fit_scale={"t1": 1e-9},
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
                 "feature_present_fit_failed": [_step_refine, _spec_wrong_peak],
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
    # NO raw-data localizer — G1/G4 + node-faithful refit + vision carry it.
    # CORPUS (docs/78 §15): `optimal_power` does NOT separate (accepted
    # [-49.3, -21.8] overlaps the rejects' [-53.0, -32.8]) — inventing a floor
    # there would only buy false-rejects. What DOES separate perfectly over 38
    # real targets is whether the node emitted its power split at all: every
    # accepted target carries `target_full_scale_power_dbm`/`target_amplitude`,
    # every rejected one carries neither. That is the same fact docs/56 §6G
    # relies on to refuse a power write without node-authored numbers.
    metric_gates=[],
    plausibility=[Plausibility("resonator_frequency", lo=2e9, hi=15e9,
                               max_abs_jump=50e6,
                               state_path="qubits.{q}.resonator.f_01")],
    # The readout amplitude + shared FSP + feedline siblings are carried by
    # `power_rows.coupled_power_rows` (this IS the coupled family), so the only
    # write the forward path was missing is the bare-resonator frequency —
    # `bare_resonator_frequency` matched what the node wrote in 21 of 21
    # archived writes (2026-08-08), i.e. it is the node's own number.
    updates=[UpdateSpec("resonator_frequency", "qubits.{q}.resonator.f_01",
                        label="Resonator frequency"),
             UpdateSpec("resonator_frequency",
                        "qubits.{q}.resonator.RF_frequency",
                        label="Resonator RF frequency"),
             UpdateSpec("bare_resonator_frequency",
                        "qubits.{q}.resonator.frequency_bare",
                        label="Bare resonator frequency")],
    consistency_checks=[
        lambda e: ("the node produced no power split (target full-scale / "
                   "amplitude absent) — its own analysis declined this fit"
                   if (e.get("target_full_scale_power_dbm") is None
                       and e.get("target_amplitude") is None) else None),
    ],
    adaptations={
        "noisy": _more_shots,
        # LOOP_STUDY case B: widen-on-fail → drive harder (max_power/max_amp
        # are this node's real knobs) → seed-shift the resonator window
        "no_signal": [_widen_span(2.0, 30.0), _power_up,
                      Rung(kind="seed_shift", seed_paths=_RES_SEED,
                           span_default=30.0)],
        "feature_present_fit_failed": [_step_refine, _spec_wrong_peak],
        # "the node produced no power split" is emitted as wrong_peak by G2;
        # without a rung the target defers. Re-measuring finer and at half
        # drive is the same ladder its fit-failed case takes (docs/78 §17).
        "wrong_peak": [_step_refine, _spec_wrong_peak],
        "out_of_band": _widen_span(2.0, 30.0)},
    verify_wide={"span_param": "frequency_span_in_mhz", "factor": 4.0,
                 "span_default": 15.0},
))

_register(Family(
    key="qubit_spectroscopy_vs_power",
    label="Qubit spectroscopy vs power",
    kind="qubits",
    value_key="frequency",
    # 2-D power sweep — vision's domain (a self-consistent noise fit fools a
    # replay; the real-archive #575 case is the canonical example)
    metric_gates=[],
    # same physical quantity and bring-up regime as node 08, and this family's
    # own patch corpus is thin (29 accepted moves, max 47 MHz) — it inherits the
    # WIDER of the two measurements rather than the accident of a small sample.
    plausibility=[Plausibility("frequency", lo=1e9, hi=12e9, max_abs_jump=200e6,
                               state_path="qubits.{q}.f_01")],
    # Measured against every archived write this node made (2026-08-08): the
    # node writes SIX fields per target, and the forward path used to compute
    # two. `anharmonicity_fitted` matched the written `anharmonicity` in 36 of
    # 36 cases, so that one is the node's own number and is safe to carry; the
    # rest are NOT in the fit entry and are declared as gaps below rather than
    # guessed (D-14: run-derived or skipped, never reverse-engineered).
    updates=[UpdateSpec("frequency", "qubits.{q}.f_01", label="Qubit f_01"),
             UpdateSpec("frequency", "qubits.{q}.xy.RF_frequency",
                        label="XY RF frequency"),
             UpdateSpec("anharmonicity_fitted", "qubits.{q}.anharmonicity",
                        label="Anharmonicity")],
    forward_gaps={
        "qubits.{q}.xy.operations.saturation.amplitude":
            "the node re-derives the saturation drive; `optimal_amplitude` "
            "matched what it wrote in only 10 of 38 archived writes, so the "
            "rest comes from a formula we do not have",
        "qubits.{q}.xy.operations.x180_DragCosine.amplitude":
            "no fit key reports it (0 of 23) — the node rescales the pi "
            "amplitude alongside the saturation drive",
        "qubits.{q}.xy.operations.x90_DragCosine.amplitude":
            "no fit key reports it (0 of 23) — rescaled with x180",
    },
    adaptations={
        "noisy": _more_shots,
        # case A's full ladder incl. the cross-node rung: #578's feature came
        # back after ANOTHER node fixed readout fullscale — same-node knobs
        # alone provably weren't the cause
        "no_signal": [_widen_span(2.0, 10.0), _power_up,
                      Rung(kind="seed_shift", seed_paths=_XY_SEED,
                           span_default=10.0),
                      Rung(kind="escalate",
                           escalate_family="resonator_spectroscopy_vs_power",
                           note="re-calibrate readout power, then retry")],
        "wrong_peak": _spec_wrong_peak,
        "feature_present_fit_failed": [_step_refine, _spec_wrong_peak],
        "out_of_band": _widen_span(2.0, 10.0)},
    verify_wide={"span_param": "frequency_span_in_mhz", "factor": 4.0,
                 "span_default": 10.0},
))


# ---------------------------------------------------------------------------
# The flux families (docs/78 P2). Every band below is CORPUS-DERIVED — the
# harvest replays real runs of each family and compares the fitted fields on
# the node-accepted side against the node-rejected side; a band that would
# reject what the node accepted is a production false-reject, so the floors sit
# under the observed accepted range with margin.
#
# The two COUPLER families are verify-only: their nodes write no calibration
# scalar at all (07's update_state is an empty stub, 10 writes only a
# bookkeeping `extras` key), so `updates=[]` is the faithful answer. Inventing a
# write target here would be exactly the "figure axis is not the state value"
# trap docs/78 D-1 refuses.
# ---------------------------------------------------------------------------

def _flux_in_range(entry: dict, params: dict) -> bool:
    """Node 09's own pre-write condition: an idle offset beyond half the swept
    span is not a sweet spot the run actually saw."""
    span = params.get("flux_offset_span_in_v")
    off = entry.get("idle_offset")
    if not isinstance(span, (int, float)) or not isinstance(off, (int, float)):
        return True
    return abs(float(off)) <= abs(float(span)) / 2.0


def _widen_flux(factor: float):
    """More flux span + more averaging — the only knobs these nodes expose."""
    def rule(params: dict) -> dict:
        out: dict = {"num_shots": int(float(params.get("num_shots", 100)) * 2)}
        if "flux_offset_span_in_v" in params:
            out["flux_offset_span_in_v"] = \
                float(params["flux_offset_span_in_v"]) * factor
        for lo, hi in (("min_flux_offset_in_v", "max_flux_offset_in_v"),
                       ("min_flux", "max_flux")):
            if lo in params and hi in params:
                out[lo] = float(params[lo]) * factor
                out[hi] = float(params[hi]) * factor
        if "num_flux_points" in params:
            out["num_flux_points"] = int(float(params["num_flux_points"]) * 1.5)
        return out
    return rule


def _denser_flux(params: dict) -> dict:
    """The ridge is there but the fit died — sample the flux axis harder."""
    return {"num_flux_points": int(float(params.get("num_flux_points", 51)) * 2),
            "num_shots": int(float(params.get("num_shots", 100)) * 2)}


_register(Family(
    key="resonator_spectroscopy_vs_flux",
    label="Resonator spectroscopy vs flux",
    kind="qubits",
    value_key="idle_offset",
    # 82 real targets (40 accepted / 42 rejected). ridge_amp_snr put 27 of the
    # 42 rejects outside the accepted range [3.2, 118]; ridge_coverage another
    # 17 outside [0.62, 1.0]. Floors sit below the accepted minima.
    metric_gates=[MetricGate("ridge_amp_snr", min=2.5,
                             reason="no resonator ridge above the noise"),
                  MetricGate("ridge_coverage", min=0.55,
                             reason="the ridge is only traceable over part of the sweep"),
                  MetricGate("ridge_r2", min=0.40, reason="sinusoidal flux fit quality")],
    # idle/min offsets are DC volts on this hardware; the physical envelope is
    # the flux line's own range, and a value outside the swept window is caught
    # by the consistency check rather than a chip-specific constant.
    plausibility=[Plausibility("idle_offset", lo=-10.0, hi=10.0),
                  Plausibility("frequency_shift", lo=-500e6, hi=500e6)],
    # 2-D map: no honest 1-D localizer (docs/47) — signal presence only.
    feature_check=FeatureCheck(var="IQ_abs", axis_var="flux_bias", mode="span",
                               claim_key="idle_offset"),
    # Node 06: the offset field is routed by z.flux_point and ASSIGNED, with a
    # true `else` (any non-"independent" value writes the joint offset); the
    # frequencies are INCREMENTS. min_offset is opt-in via update_flux_min.
    updates=[
        UpdateSpec("idle_offset", "qubits.{q}.z.independent_offset",
                   route_on="qubits.{q}.z.flux_point", route_when="independent",
                   label="Flux idle offset (independent)"),
        UpdateSpec("idle_offset", "qubits.{q}.z.joint_offset",
                   route_on="qubits.{q}.z.flux_point", route_when="*",
                   label="Flux idle offset (joint)"),
        UpdateSpec("min_offset", "qubits.{q}.z.min_offset",
                   guard=lambda e, p: bool(p.get("update_flux_min")),
                   label="Flux min offset"),
        UpdateSpec("frequency_shift", "qubits.{q}.resonator.f_01",
                   op="add_to_current", label="Resonator f_01 (+shift)"),
        UpdateSpec("frequency_shift", "qubits.{q}.resonator.RF_frequency",
                   op="add_to_current", label="Resonator RF (+shift)"),
    ],
    consistency_checks=[
        lambda e: ("flat flux response — no dispersive shift to fit"
                   if e.get("flat_response") else None),
    ],
    adaptations={"noisy": _more_shots,
                 "no_signal": [_widen_flux(2.0), _power_up],
                 "feature_present_fit_failed": _denser_flux,
                 # this family's consistency check ("flat flux response") is
                 # emitted as wrong_peak by G2; with no rung the target DEFERS
                 # instead of re-measuring. A flat response is a signal
                 # problem, so it takes the signal ladder (docs/78 §17).
                 "wrong_peak": [_widen_flux(2.0), _power_up],
                 "out_of_band": _widen_flux(2.0)},
))

_register(Family(
    key="resonator_spectroscopy_vs_coupler_flux",
    label="Resonator spectroscopy vs coupler flux",
    kind="qubit_pairs",
    value_key="idle_offset",
    # 15 real targets, ALL node-accepted — no rejected side exists in the
    # corpus, so there is nothing to calibrate a discriminating metric against.
    # Declaring an invented band here would be guessing; the honest gates are
    # the physical envelope plus signal presence, and the family is marked as
    # having no measured false-accept coverage (docs/78 §15).
    metric_gates=[],
    plausibility=[Plausibility("idle_offset", lo=-10.0, hi=10.0),
                  Plausibility("resonator_frequency", lo=2e9, hi=15e9),
                  Plausibility("frequency_shift", lo=-500e6, hi=500e6)],
    feature_check=FeatureCheck(var="IQ_abs", axis_var="flux_bias", mode="span",
                               claim_key="idle_offset"),
    updates=[],          # node 07's update_state is an empty stub — verify-only
    adaptations={"noisy": _more_shots,
                 "no_signal": [_widen_flux(2.0), _power_up],
                 "feature_present_fit_failed": _denser_flux},
))

_register(Family(
    key="qubit_spectroscopy_vs_flux",
    label="Qubit spectroscopy vs flux",
    kind="qubits",
    value_key="qubit_frequency",
    # 47 real targets (30 accepted / 17 rejected). NO numeric field separates
    # the two sides: every reject carries non-finite fields, because the node's
    # own gate is finiteness plus the swept-range check. Encoding a fake metric
    # band would add false-rejects without adding detection, so the gates here
    # are the physical envelope, the range guard and signal presence.
    metric_gates=[],
    plausibility=[Plausibility("qubit_frequency", lo=1e9, hi=12e9,
                               max_abs_jump=500e6, state_path="qubits.{q}.f_01"),
                  Plausibility("idle_offset", lo=-10.0, hi=10.0),
                  Plausibility("frequency_shift", lo=-1e9, hi=1e9)],
    feature_check=FeatureCheck(var="IQ_abs", axis_var="flux_bias", mode="span",
                               claim_key="idle_offset"),
    # Node 09 differs from node 06 on the SAME field: independent ASSIGNS,
    # joint INCREMENTS, and an unrecognised flux_point writes NOTHING (its
    # if/elif has no else). Both frequencies are absolute assigns, and the whole
    # block is gated on the offset lying inside the swept span.
    updates=[
        UpdateSpec("idle_offset", "qubits.{q}.z.independent_offset",
                   route_on="qubits.{q}.z.flux_point", route_when="independent",
                   guard=_flux_in_range, label="Flux idle offset (independent)"),
        UpdateSpec("idle_offset", "qubits.{q}.z.joint_offset",
                   op="add_to_current",
                   route_on="qubits.{q}.z.flux_point", route_when="joint",
                   guard=_flux_in_range, label="Flux idle offset (joint, +=)"),
        UpdateSpec("qubit_frequency", "qubits.{q}.f_01",
                   guard=_flux_in_range, label="Qubit f_01"),
        UpdateSpec("qubit_frequency", "qubits.{q}.xy.RF_frequency",
                   guard=_flux_in_range, label="XY RF frequency"),
    ],
    # `vertex_extrapolated` is deliberately NOT a check: the node's own analysis
    # treats it as a warning and still accepts the fit, and the corpus confirms
    # it (one accepted target carries it). Flagging it was this batch's only
    # measured false-reject.
    adaptations={"noisy": _more_shots,
                 "no_signal": [_widen_flux(2.0), _power_up],
                 "feature_present_fit_failed": _denser_flux,
                 "out_of_band": _widen_flux(2.0)},
))

_register(Family(
    key="qubit_spectroscopy_vs_coupler_flux",
    label="Qubit spectroscopy vs coupler flux",
    kind="qubit_pairs",
    value_key="num_crossings",
    # 17 real targets (3 accepted / 14 rejected) and the separation is total:
    # every accepted target found exactly one avoided crossing, every rejected
    # one found zero. That IS the family's verdict — it is a structure-finding
    # node, not a scalar calibration.
    metric_gates=[MetricGate("num_crossings", min=1,
                             reason="no avoided crossing found in the swept window")],
    plausibility=[Plausibility("num_crossings", lo=0, hi=20)],
    feature_check=FeatureCheck(var="IQ_abs", axis_var="flux_bias", mode="span",
                               claim_key="num_crossings"),
    updates=[],          # node 10 writes only a bookkeeping extras key
    adaptations={"noisy": _more_shots,
                 "no_signal": [_widen_flux(2.0), _power_up],
                 "feature_present_fit_failed": _denser_flux},
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
    # Which route-branch fires is decided ONCE per (fit_key, route_on) group so
    # an else-branch ("*") only writes when no exact match did — mirroring the
    # nodes' if/elif/else rather than writing both fields.
    matched: set[tuple[str, str]] = set()
    for spec in fam.updates:
        if spec.route_on and spec.route_when and spec.route_when != "*":
            try:
                sel = current_value_of(
                    spec.route_on.replace("{q}", target).replace("{pair}", target))
            except Exception:  # noqa: BLE001
                sel = None
            if str(sel) == spec.route_when:
                matched.add((spec.fit_key, spec.route_on))

    for spec in fam.updates:
        v = fit_value(fam, fit_entry, spec.fit_key)
        if v is None:
            continue
        if spec.guard is not None:
            try:
                if not spec.guard(fit_entry, run_parameters or {}):
                    continue
            except Exception:  # noqa: BLE001 — a guard that cannot decide refuses
                continue
        if spec.route_on:
            key = (spec.fit_key, spec.route_on)
            try:
                sel = current_value_of(
                    spec.route_on.replace("{q}", target).replace("{pair}", target))
            except Exception:  # noqa: BLE001
                sel = None
            if spec.route_when == "*":
                if key in matched:
                    continue                  # an exact branch already fired
            elif str(sel) != spec.route_when:
                continue
        path = spec.path.replace("{q}", target).replace("{pair}", target)
        if "{operation}" in path:
            op_name = (run_parameters or {}).get("operation")
            if not op_name:
                continue
            path = path.replace("{operation}", str(op_name))
        # the run names an ALIAS; the node writes its target (docs/78 §15.4b)
        resolved = resolve_alias_path(path, current_value_of)
        if resolved is None:
            continue                          # unresolvable alias ⇒ never guess
        path = resolved
        v = v * spec.factor if spec.factor != 1.0 else v
        try:
            current = current_value_of(path)
        except Exception:
            current = None
        if spec.op in ("subtract_from_current", "add_to_current"):
            if not isinstance(current, (int, float)) or isinstance(current, bool):
                continue                      # can't offset a pointer/None
            value = current - v if spec.op == "subtract_from_current" else current + v
        elif spec.op == "assign_ceil4":
            value = int(_math.ceil(float(v) / 4.0) * 4)
        else:
            value = v
        rows.append({"path": path, "value": value, "old_hint": current,
                     "label": spec.label or spec.fit_key, "op": spec.op})
    return rows
