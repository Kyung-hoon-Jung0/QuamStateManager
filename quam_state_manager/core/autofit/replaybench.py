"""Future-blind replay for the shape-read families (docs/131).

The punch-out family has its own specialised reader (``pathreplay``); every
other spectroscopy family shares one, because they share one question — find
the feature, see what its position does along the second axis. This module is
the loop around that shared reader:

    session -> reveal(k) -> mapcases.signal_for -> pack.signal_map -> case
            -> action -> (adopt | retune | abstain) -> score

Three properties carry over from ``pathreplay`` unchanged, and they are the
reason any of the numbers mean something:

* **future-blindness is structural** — the session guard is reused verbatim,
  so reaching past the cursor raises rather than quietly scoring better;
* **the case decision and the numbers stay apart** — the reader returns a
  semantic signal, the manual names it, and the knob arithmetic lives here in
  bounded fractions of the CURRENT window. A vision judge replacing the reader
  changes which case is chosen and nothing else;
* **an abstention never adopts.**

What is deliberately NOT taken from the manual is the adopt/retune decision:
the manuals' prescriptions are prose written for people, and turning prose
into an action at load time would be a guess wearing a rule's clothes. The
reader decides what IT can vouch for (``_ACTIONS`` below), which is a claim
about measurement rather than about physics, and the manual's prescription
rides along in the ledger so a human can see what it would have advised.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from quam_state_manager.core.autofit import knowledge, mapcases as MC
from quam_state_manager.core.autofit.pathreplay import (
    FutureBlindError, RunView, Session, load_run)

logger = logging.getLogger(__name__)

FREQ_TOL_HZ = 2e6
SWEEP_TOL_FRAC = 0.10          # of the swept range

# What the READER is willing to vouch for, per shape it measured. Not read
# from the manual: see the module docstring.
ADOPT_FREQ = "adopt_frequency"
ADOPT_BOTH = "adopt_frequency_and_sweep_value"
RETUNE = "retune"

_ACTIONS: dict[str, str] = {
    MC.LINE_CLEAN: ADOPT_FREQ,
    MC.CURVE_ARCH: ADOPT_BOTH,
    MC.CURVE_FULL_SWING: ADOPT_BOTH,
    # the ridge never turned inside the window, so the frequency it carries is
    # measured but the operating point derived from a turning point is not
    MC.CURVE_MONOTONIC: ADOPT_FREQ,
    # A flat map answers the SWEEP question ("this knob does not move it") and
    # still measures the frequency perfectly well — the feature is right
    # there, it simply does not move. Refusing the whole run because the
    # shape was uninformative about the sweep threw away 8 of 11 coupler
    # frequencies the answer keys call adoptable.
    MC.CURVE_FLAT: ADOPT_FREQ,
}

# Bounded knob moves, per shape. Every one is a fraction of the CURRENT
# window — the manual's chip-independence rule, made executable.
_FREQ_SPAN = ("frequency_span_in_mhz", "frequency_step_in_mhz")
_FLUX_RANGE = ("min_flux_offset_in_v", "max_flux_offset_in_v")
_CURRENT_RANGE = ("min_current", "max_current")


def _widen_freq(p: dict, factor: float) -> dict:
    span, step = p.get(_FREQ_SPAN[0]), p.get(_FREQ_SPAN[1])
    out = {}
    if isinstance(span, (int, float)):
        out[_FREQ_SPAN[0]] = span * factor
        if isinstance(step, (int, float)):
            out[_FREQ_SPAN[1]] = step * factor
    return out


def _widen_sweep(p: dict, factor: float) -> dict:
    for lo_k, hi_k in (_FLUX_RANGE, _CURRENT_RANGE):
        lo, hi = p.get(lo_k), p.get(hi_k)
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi > lo:
            mid, half = 0.5 * (lo + hi), 0.5 * (hi - lo) * factor
            return {lo_k: mid - half, hi_k: mid + half}
    return {}


def _more_shots(p: dict, factor: float = 2.0) -> dict:
    n = p.get("num_shots")
    return {"num_shots": int(n * factor)} if isinstance(n, (int, float)) else {}


def _softer_drive(p: dict) -> dict:
    a = p.get("operation_amplitude_factor")
    return {"operation_amplitude_factor": a / 2.0} \
        if isinstance(a, (int, float)) else {}


def _moves(key: str, params: dict) -> dict:
    """The knob move this shape asks for, in bounded relative terms."""
    if key == MC.LINE_EMPTY:
        return _widen_freq(params, 3.0) or _more_shots(params)
    if key == MC.LINE_EDGE:
        # re-centring is a SEED change, not a knob: say so rather than
        # inventing a frequency
        return {"recenter_on_feature": True}
    if key == MC.LINE_MULTI:
        return {**_widen_freq(params, 1 / 3.0), **_softer_drive(params)}
    if key == MC.LINE_WEAK:
        return _more_shots(params, 4.0)
    if key == MC.LINE_SPLIT:
        return _softer_drive(params) or _more_shots(params)
    if key == MC.CURVE_EMPTY:
        return _widen_freq(params, 3.0) or _more_shots(params)
    if key == MC.CURVE_FLAT:
        return _widen_sweep(params, 3.0)
    if key == MC.CURVE_PARTIAL:
        return {**_more_shots(params), **_widen_freq(params, 2.0)}
    if key == MC.CURVE_BROKEN:
        return _more_shots(params)
    if key == MC.CURVE_MULTI:
        return _widen_sweep(params, 1 / 3.0)
    return {}


@dataclass
class Step:
    index: int
    run_id: str
    signal: str | None
    case: str | None
    flags: list[str]
    action: str
    adopted: dict
    refused: list[str]
    next_params: dict
    proposal_matched: str | None
    prescription: str
    reasons: list[str]


@dataclass
class Result:
    family: str
    session_id: str
    target: str
    steps: list[Step]
    final_state: dict
    first_value: dict
    first_value_at: str | None
    runs_to_first_value: int
    runs_consumed: int
    unresolved: bool
    revisions: list[dict]
    unscoreable_proposals: int


def _params_match(proposal: dict, params: dict, rel: float = 0.15) -> bool:
    keys = [k for k in proposal if k in params
            and isinstance(proposal[k], (int, float))
            and isinstance(params[k], (int, float))]
    if not keys:
        return False
    for k in keys:
        a, b = float(proposal[k]), float(params[k])
        if a == b:
            continue
        scale = max(abs(a), abs(b))
        if scale == 0 or abs(a - b) / scale > rel:
            return False
    return True


def replay(family: str, session: Session, target: str, *,
           pack: dict | None = None,
           signal_fn: Callable[..., MC.ShapeSignal] = MC.signal_for) -> Result:
    """Walk one target of one session, future-blind."""
    if pack is None:
        pack = knowledge.load_family(family) or {}
    smap = pack.get("signal_map") or {}
    by_id = {c.get("id"): c for c in (pack.get("cases") or [])}
    value_field, sweep_field = MC.VALUE_FIELDS.get(family, (None, None))

    steps: list[Step] = []
    state: dict[str, Any] = {}
    prior_keys: list[str] = []
    first_value: dict = {}
    first_at: str | None = None
    runs_to_first = 0
    held: float | None = None
    revisions: list[dict] = []
    unscoreable = 0
    idxs = session.runs_for(target)

    for pos, k in enumerate(idxs):
        view = session.at(k)                    # guarded: never past k
        fit = view.fit(target)
        sig = signal_fn(family, view.folder, target, fit=fit,
                        params=view.params, outcomes=view.outcomes,
                        prior_keys=prior_keys)
        prior_keys.append(sig.key or "")
        case_id = smap.get(sig.key or "")
        case = by_id.get(case_id) or {}
        action = _ACTIONS.get(sig.key or "", RETUNE)
        adopted: dict = {}
        refused: list[str] = []
        reasons = list(sig.reasons)

        # flags that veto an adoption outright
        if MC.FLAG_BATCH_MOSTLY_FAILED in sig.flags or \
                MC.FLAG_OFF_FEATURE in sig.flags:
            action = RETUNE
        if action in (ADOPT_FREQ, ADOPT_BOTH) and value_field:
            v = fit.get(value_field)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                adopted[value_field] = float(v)
            else:
                action = RETUNE
                reasons.append(f"the record carries no usable {value_field}")
        if action == ADOPT_BOTH and sweep_field:
            sv = fit.get(sweep_field)
            bad = (MC.FLAG_OUT_OF_WINDOW in sig.flags
                   or MC.FLAG_EDGE_VALUE in sig.flags
                   or MC.FLAG_EXTRAPOLATED in sig.flags)
            if bad:
                refused.append(sweep_field)
                reasons.append(f"refusing the chosen {sweep_field}: it is an "
                               f"artifact of where the sweep ended, not a "
                               f"measurement")
            elif isinstance(sv, (int, float)) and not isinstance(sv, bool):
                adopted[sweep_field] = float(sv)
        elif action == ADOPT_FREQ and sweep_field:
            refused.append(sweep_field)
            reasons.append(f"the ridge never turned inside the window, so no "
                           f"{sweep_field} is derivable from it")

        # A frequency-only adoption does not end the question the sweep was
        # asked: the knob move still stands, and the walk continues.
        next_params = ({} if (adopted and action == ADOPT_BOTH)
                       else _moves(sig.key or "", view.params))

        matched = None
        if next_params:
            for k2 in idxs[pos + 1:]:
                if _params_match(next_params, session.at(k2).params):
                    matched = session.at(k2).run_id
                    break
            if matched is None:
                unscoreable += 1

        if adopted:
            got = adopted.get(value_field)
            if first_at is None:
                first_at, first_value, runs_to_first = view.run_id, dict(adopted), len(steps) + 1
            elif isinstance(got, (int, float)) and isinstance(held, (int, float)) \
                    and abs(got - held) > FREQ_TOL_HZ:
                revisions.append({"run": view.run_id, "from": held, "to": got,
                                  "why": "a later readable map puts the "
                                         "feature elsewhere — the chip moved, "
                                         "so the held value is re-measured"})
            if isinstance(got, (int, float)):
                held = float(got)
            state.update(adopted)

        steps.append(Step(
            index=pos, run_id=view.run_id, signal=sig.key, case=case_id,
            flags=list(sig.flags),
            action="adopt" if adopted else ("retune" if sig.key else "abstain"),
            adopted=dict(adopted), refused=refused, next_params=dict(next_params),
            proposal_matched=matched,
            prescription=(case.get("prescription") or "")[:400],
            reasons=reasons[:4]))

    return Result(family=family, session_id=session.session_id, target=target,
                  steps=steps, final_state=state, first_value=first_value,
                  first_value_at=first_at, runs_to_first_value=runs_to_first,
                  runs_consumed=len(steps), unresolved=first_at is None,
                  revisions=revisions, unscoreable_proposals=unscoreable)


def score(result: Result, key: dict) -> dict:
    """Compare a replay against the hand-built answer key for that target."""
    family = result.family
    value_field, sweep_field = MC.VALUE_FIELDS.get(family, (None, None))
    term = key.get("termination") or {}
    want_f = term.get("final_frequency")
    want_s = term.get("final_sweep_value")
    key_unresolved = bool(term.get("unresolved"))
    got_f = result.final_state.get(value_field) if value_field else None
    got_s = result.final_state.get(sweep_field) if sweep_field else None

    out: dict[str, Any] = {
        "family": family, "session": result.session_id, "target": result.target,
        "key_confidence": key.get("confidence"),
        "runs_consumed": result.runs_consumed,
        "runs_to_first_value": result.runs_to_first_value,
        "ideal_length": key.get("ideal_length"),
        "actual_operator_length": key.get("actual_length"),
        "terminated_at": result.first_value_at,
        "key_terminates_at": term.get("at_run"),
        "sm_unresolved": result.unresolved,
        "key_unresolved": key_unresolved,
        "unscoreable_proposals": result.unscoreable_proposals,
        "revisions": len(result.revisions),
    }
    if key_unresolved:
        out["frequency_verdict"] = ("correctly_abstained" if result.unresolved
                                    else "adopted_where_key_says_unresolved")
    elif not isinstance(want_f, (int, float)):
        out["frequency_verdict"] = "unscoreable_key_has_no_value"
    elif not isinstance(got_f, (int, float)):
        out["frequency_verdict"] = "missed_no_value_adopted"
    else:
        out["frequency_delta_hz"] = abs(got_f - want_f)
        out["frequency_verdict"] = ("match" if abs(got_f - want_f) <= FREQ_TOL_HZ
                                    else "wrong_value")

    if isinstance(want_s, (int, float)) and isinstance(got_s, (int, float)):
        scale = max(abs(want_s), 1e-9)
        out["sweep_delta"] = abs(got_s - want_s)
        out["sweep_verdict"] = ("match"
                                if abs(got_s - want_s) <= SWEEP_TOL_FRAC * max(scale, 0.05)
                                else "wrong_value")
    elif isinstance(want_s, (int, float)):
        out["sweep_verdict"] = "missed_no_value_adopted"
    else:
        out["sweep_verdict"] = "unscoreable_key_has_no_value"

    il = key.get("ideal_length")
    if isinstance(il, (int, float)) and il > 0:
        out["length_vs_ideal"] = (result.runs_to_first_value
                                  or result.runs_consumed) - int(il)
    al = key.get("actual_length")
    if isinstance(al, (int, float)) and al > 0:
        out["runs_saved_vs_operator"] = int(al) - (result.runs_to_first_value
                                                   or result.runs_consumed)

    exp = {p.get("run", "").split("_")[0]: p.get("expected_case")
           for p in (key.get("ideal_path") or []) if p.get("run")}
    agree = total = 0
    for s in result.steps:
        want = exp.get(s.run_id.split("_")[0])
        if not want:
            continue
        total += 1
        if s.case and (s.case == want or str(want).startswith(s.case)):
            agree += 1
    out["case_agreement"] = f"{agree}/{total}" if total else "n/a"
    return out
