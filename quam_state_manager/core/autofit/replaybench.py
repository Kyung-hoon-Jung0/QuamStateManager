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

import re

from quam_state_manager.core.autofit import knowledge, mapcases as MC
from quam_state_manager.core.autofit.families import normalize_node_name
from quam_state_manager.core.autofit.pathreplay import (
    FutureBlindError, RunView, Session, load_run)

logger = logging.getLogger(__name__)

FREQ_TOL_HZ = 2e6

_RUN_PREFIX = re.compile(r"^#\d+_")
_RUN_SUFFIX = re.compile(r"_\d{6}$")
# node names that are the same measurement under a different spelling
_PACK_ALIASES = {
    "resonator_spectroscopy_single": "resonator_spectroscopy",
    "resonator_spectroscopy_wide_pyloop": "resonator_spectroscopy",
    "resonator_spectroscopy_wide_python_loop": "resonator_spectroscopy",
}


def pack_family_for(run_id: str) -> str:
    """Knowledge-pack family for an archived run folder name."""
    name = _RUN_SUFFIX.sub("", _RUN_PREFIX.sub("", run_id or ""))
    norm = normalize_node_name(name)
    return _PACK_ALIASES.get(norm, norm)


# Where a session mixes node types, the frequency answer of one is checkable
# against the other. These two are ONE workflow in the lab: the power sweep
# chooses the frequency AND the drive power, the 1-D sweep then measures at
# that power. So a 1-D value can be tested against what the power runs of the
# same session established — which is the only way to catch the trap below.
JOINT_QUBIT_SPEC = ("qubit_spectroscopy", "qubit_spectroscopy_vs_power")
# how close to (held frequency minus half the anharmonicity) a candidate must
# sit before it is called the two-photon line rather than the fundamental
TWO_PHOTON_GUARD = 0.20
# How much colder (dB) a run must be before its claim is exempted from the
# guard. A multi-photon line only exists ABOVE a drive threshold, so a value
# measured at LESS drive than the one already held cannot be that artifact —
# refusing it would be refusing the better measurement of the two.
COLDER_BY_DB = 1.0


def drive_power_dbm(view: Any, target: str) -> float | None:
    """Physical drive power for one target of one run, from its own snapshot.

    P = full_scale_power + 20*log10|amplitude|. Reading the amplitude alone
    gets the SIGN of a change wrong, and demonstrably so in this corpus: on
    one real day a qubit's stored drive amplitude rose by half again between
    two runs while the port's full-scale power was written down further, so
    the power at the qubit FELL — and the line duly came back narrower, which
    is nonsense if amplitude is all you read.
    """
    import json as _json
    import math as _math
    from pathlib import Path as _Path

    snap = getattr(view, "snapshot", None) or {}
    q = (snap.get("qubits") or {}).get(target) or {}
    xy = q.get("xy") or {}
    op = ((xy.get("operations") or {}).get("saturation") or {})
    amp = op.get("amplitude")
    factor = (view.params or {}).get("operation_amplitude_factor")
    if isinstance(factor, (int, float)) and not isinstance(factor, bool):
        amp = (amp or 0) * factor
    if not isinstance(amp, (int, float)) or isinstance(amp, bool) or not amp:
        return None

    # The port reference is a chain that crosses FILES: state.json points into
    # wiring.json, which points at the port entry back in state.json. Reading
    # only the state snapshot resolves none of it.
    merged = dict(snap)
    try:
        wpath = _Path(view.folder) / "quam_state" / "wiring.json"
        wiring = _json.loads(wpath.read_text(encoding="utf-8"))
        merged["wiring"] = wiring.get("wiring", wiring)
    except (OSError, ValueError, TypeError):
        pass

    node: Any = xy.get("opx_output")
    for _hop in range(4):
        if not isinstance(node, str) or not node.startswith("#/"):
            break
        cur: Any = merged
        for part in node[2:].split("/"):
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(part)
        node = cur
    fsp = node.get("full_scale_power_dbm") if isinstance(node, dict) else None
    if not isinstance(fsp, (int, float)) or isinstance(fsp, bool):
        fsp = xy.get("full_scale_power_dbm")
    if not isinstance(fsp, (int, float)) or isinstance(fsp, bool):
        return None
    import math as _math
    return float(fsp) + 20.0 * _math.log10(abs(float(amp)))
SWEEP_TOL_FRAC = 0.10          # of the swept range
# How close two vouched values must be to count as the same answer when the
# session is asked what it AGREES on rather than what it said last.
AGREE_HZ = 2e6


def _largest_cluster(vals: list[float]) -> float | None:
    """The value the most readings agree on; ties broken by the later one, so
    recency still decides where agreement does not.

    Non-finite values are dropped first: a NaN does not even cluster with
    itself, so leaving one in empties the cluster it is supposed to seed.
    """
    import math as _math
    vals = [v for v in vals if isinstance(v, (int, float))
            and not isinstance(v, bool) and _math.isfinite(v)]
    best, bestn = None, 0
    for v in vals:
        near = [w for w in vals if abs(w - v) <= AGREE_HZ]
        if len(near) >= bestn:
            best, bestn = sum(near) / len(near), len(near)
    return best


# What the READER is willing to vouch for, per shape it measured. Not read
# from the manual: see the module docstring.
ADOPT_FREQ = "adopt_frequency"
ADOPT_BOTH = "adopt_frequency_and_sweep_value"
RETUNE = "retune"

# Where the reader's own value is strong enough to outrank a flag that says
# the RECORD is wrong. Only the shapes measured across many slices qualify:
# a power plateau is a stationary line agreed on by at least eight independent
# drive powers, whereas the corrected value on a 1-D trace is one peak of one
# trace. Measured, and the distinction is not cosmetic — relaxing the veto for
# 1-D traces too converted an abstention into a wrong answer on the readout
# benchmark and gained nothing anywhere.
_MEASURED_ACROSS_SLICES = {"power_stationary_then_broadening",
                           "power_second_line_below",
                           "power_multiphoton_ladder"}

_ACTIONS: dict[str, str] = {
    MC.LINE_CLEAN: ADOPT_FREQ,
    # a Fano-asymmetric trace still carries a resolved resonance; the flags
    # decide whether the RECORD found it rather than the companion
    MC.LINE_FANO: ADOPT_FREQ,
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
    # A power map that shows a stationary low-power stretch has answered BOTH
    # of its questions: where the line is, and how hard to drive it. Every
    # other power shape has not seen the onset, so neither answer is derivable.
    MC.POWER_PLATEAU: ADOPT_BOTH,
    MC.POWER_TWO_RIDGES: ADOPT_BOTH,
    # the ladder DOES carry a frequency — the bottom rung, measured over its
    # own stretch — but the power the node chose is the one that climbed the
    # ladder, so only the frequency is adoptable
    MC.POWER_LADDER: ADOPT_FREQ,
}

# Bounded knob moves, per shape. Every one is a fraction of the CURRENT
# window — the manual's chip-independence rule, made executable.
_FREQ_SPAN = ("frequency_span_in_mhz", "frequency_step_in_mhz")
_FLUX_RANGE = ("min_flux_offset_in_v", "max_flux_offset_in_v")
_CURRENT_RANGE = ("min_current", "max_current")
_POWER_RANGE = ("min_power_dbm", "max_power_dbm")


def _extend_power_down(p: dict, frac: float) -> dict:
    """Push the bottom of the power sweep down by a fraction of its own span.

    A fraction of the current range, not a step in dB: the manual's
    chip-independence rule applies to prescriptions as much as to cases, and
    what counts as "a bit lower" depends on the attenuation in front of the
    line, which differs by lab.
    """
    lo, hi = p.get(_POWER_RANGE[0]), p.get(_POWER_RANGE[1])
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return {}
    span = abs(hi - lo)
    if span <= 0:
        return {}
    out = {_POWER_RANGE[0]: lo - frac * span}
    n = p.get("num_power_points")
    if isinstance(n, int) and n > 0:
        out["num_power_points"] = int(round(n * (1.0 + frac)))
    return out


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
    if key == MC.LINE_FANO:
        # the notch is there and resolved; what a Fano trace wants is a
        # narrower window around it so the companion stops dominating
        return _widen_freq(params, 1 / 3.0)
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
    if key == MC.POWER_EMPTY:
        return {**_widen_freq(params, 3.0), **_more_shots(params)}
    if key == MC.POWER_TOP_ONLY:
        # the only feature sits at the top of the drive range, where a
        # two-photon partner lives; widen the frequency window so the
        # fundamental half an anharmonicity above it comes into view
        return _widen_freq(params, 2.0)
    if key == MC.POWER_NO_ANCHOR:
        return _extend_power_down(params, 1 / 3.0)
    if key == MC.POWER_LADDER:
        # the ladder was climbed because the drive went too high; the useful
        # next sweep is the same window with a lower ceiling
        lo, hi = params.get("min_power_dbm"), params.get("max_power_dbm")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi > lo:
            return {"max_power_dbm": hi - (hi - lo) / 3.0}
        return {}
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
    # Every value the walk vouched for, in order, and the one the largest
    # cluster of them agrees on. A ratchet IS recency-following, so which of
    # these two a session ends on is a real choice and is made by the caller.
    adopted_values: dict = field(default_factory=dict)
    consensus_state: dict = field(default_factory=dict)


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
    # ``family`` may be a callable, so one session can mix node types: the
    # qubit-spectroscopy pair is one workflow and reading either half alone
    # throws away the only cross-check either has.
    joint = callable(family)
    packs: dict[str, dict] = {}
    if not joint:
        packs[family] = pack if pack is not None else (
            knowledge.load_family(family) or {})

    def pack_for(f: str) -> dict:
        if f not in packs:
            packs[f] = knowledge.load_family(f) or {}
        return packs[f]

    steps: list[Step] = []
    state: dict[str, Any] = {}
    prior_keys: list[str] = []
    first_value: dict = {}
    first_at: str | None = None
    runs_to_first = 0
    held: float | None = None
    held_power: float | None = None
    anh: float | None = None
    revisions: list[dict] = []
    unscoreable = 0
    idxs = session.runs_for(target)
    seen_families: list[str] = []

    for pos, k in enumerate(idxs):
        view = session.at(k)                    # guarded: never past k
        fam = pack_family_for(view.run_id) if joint else family
        seen_families.append(fam)
        fpack = pack_for(fam)
        smap = fpack.get("signal_map") or {}
        by_id = {c.get("id"): c for c in (fpack.get("cases") or [])}
        value_field, sweep_field = MC.VALUE_FIELDS.get(fam, (None, None))
        fit = view.fit(target)
        # the anharmonicity the RUN carries, never one this module chose
        a = fit.get("anharmonicity_stored")
        if isinstance(a, (int, float)) and not isinstance(a, bool) and a > 0:
            anh = float(a)
        sig = signal_fn(fam, view.folder, target, fit=fit,
                        params=view.params, outcomes=view.outcomes,
                        prior_keys=prior_keys)
        prior_keys.append(sig.key or "")
        case_id = smap.get(sig.key or "")
        case = by_id.get(case_id) or {}
        action = _ACTIONS.get(sig.key or "", RETUNE)
        adopted: dict = {}
        refused: list[str] = []
        reasons = list(sig.reasons)

        # Flags that veto an adoption. "The record is off the feature" and
        # "the record fell for the companion" are statements about the RECORD,
        # so they stop vetoing the moment the reader has measured the answer
        # itself — otherwise the reader is silenced in exactly the cases where
        # it knows better, which is the whole failure this round is about.
        vetoes = [f for f in (MC.FLAG_BATCH_MOSTLY_FAILED,
                              MC.FLAG_OFF_FEATURE,
                              MC.FLAG_OVER_BROADENED,
                              MC.FLAG_FIT_ON_WRONG_SIDE) if f in sig.flags]
        about_the_record = {MC.FLAG_OFF_FEATURE, MC.FLAG_FIT_ON_WRONG_SIDE}
        if (sig.key in _MEASURED_ACROSS_SLICES and value_field
                and isinstance(sig.corrected.get(value_field), (int, float))):
            vetoes = [f for f in vetoes if f not in about_the_record]
        if vetoes:
            action = RETUNE
        if action in (ADOPT_FREQ, ADOPT_BOTH) and value_field:
            # WHICH value, not merely whether to accept one. Where the reader
            # measured the answer itself it outranks the record: on the
            # power family the node's own frequency is right on 52 of 103
            # targets with an independent consensus truth and the reader's
            # vouched plateau on 31 of the 38 it will speak for at all.
            own = sig.corrected.get(value_field)
            v = own if isinstance(own, (int, float)) else fit.get(value_field)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                # THE trap of this pair of node types. The two-photon 0->2
                # transition sits half an anharmonicity BELOW the fundamental
                # and grows faster with drive, so at too much power a 1-D fit
                # lands on it and looks perfect — narrow, high SNR, high r2.
                # Nothing inside that single run can tell the two apart; what
                # can is the anharmonicity a power run of the same session
                # already reported, which is why these two are replayed
                # together.
                this_power = drive_power_dbm(view, target)
                colder = (isinstance(this_power, float)
                          and isinstance(held_power, float)
                          and this_power < held_power - COLDER_BY_DB)
                if (anh and isinstance(held, (int, float)) and not colder
                        and abs((held - anh / 2) - float(v))
                        <= TWO_PHOTON_GUARD * anh / 2
                        and abs(held - float(v)) > TWO_PHOTON_GUARD * anh / 2):
                    action = RETUNE
                    refused.append(value_field)
                    reasons.append("refusing this frequency: it sits half the "
                                   "reported anharmonicity below the value "
                                   "this session already established, which "
                                   "is where the two-photon transition lives")
                else:
                    adopted[value_field] = float(v)
                    if isinstance(own, (int, float)):
                        reasons.append(f"adopting the {value_field} the map "
                                       f"carries, not the one the record "
                                       f"claims")
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
                held_power = drive_power_dbm(view, target)
            state.update(adopted)

        steps.append(Step(
            index=pos, run_id=view.run_id, signal=sig.key, case=case_id,
            flags=list(sig.flags),
            action="adopt" if adopted else ("retune" if sig.key else "abstain"),
            adopted=dict(adopted), refused=refused, next_params=dict(next_params),
            proposal_matched=matched,
            prescription=(case.get("prescription") or "")[:400],
            reasons=reasons[:4]))

    vouched: dict[str, list[float]] = {}
    for st in steps:
        for k, v in st.adopted.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vouched.setdefault(k, []).append(float(v))
    consensus = {k: c for k, vs in vouched.items()
                 if (c := _largest_cluster(vs)) is not None}
    return Result(family=("+".join(sorted(set(seen_families))) if joint
                          else family),
                  session_id=session.session_id, target=target,
                  steps=steps, final_state=state, first_value=first_value,
                  first_value_at=first_at, runs_to_first_value=runs_to_first,
                  runs_consumed=len(steps), unresolved=first_at is None,
                  revisions=revisions, unscoreable_proposals=unscoreable,
                  adopted_values=vouched, consensus_state=consensus)


def score(result: Result, key: dict, *, rule: str = "recency") -> dict:
    """Compare a replay against the hand-built answer key for that target.

    ``rule`` chooses what the session's answer IS: ``"recency"`` keeps the
    last value the walk vouched for, ``"agreement"`` keeps the one the largest
    cluster of vouched values agrees on. The default is recency because every
    published number in this study was measured that way; see docs/133 for the
    comparison, and for why measuring the two against a clustering-derived
    truth is partly circular.
    """
    family = result.family
    # A joint replay names itself after every node type it read, joined by
    # "+", so a straight lookup finds nothing and every target scores as
    # "adopted no value" — a silent zero that looks like a measurement.
    value_field, sweep_field = MC.VALUE_FIELDS.get(family, (None, None))
    if value_field is None and "+" in family:
        for part in family.split("+"):
            vf, sf = MC.VALUE_FIELDS.get(part, (None, None))
            if vf:
                value_field = vf
                sweep_field = sweep_field or sf
                break
    term = key.get("termination") or {}
    want_f = term.get("final_frequency")
    want_s = term.get("final_sweep_value")
    key_unresolved = bool(term.get("unresolved"))
    end = result.consensus_state if rule == "agreement" else result.final_state
    got_f = end.get(value_field) if value_field else None
    got_s = end.get(sweep_field) if sweep_field else None

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
        "end_rule": rule,
    }
    if key_unresolved:
        out["frequency_verdict"] = ("correctly_abstained" if result.unresolved
                                    else "adopted_where_key_says_unresolved")
    elif not isinstance(want_f, (int, float)):
        out["frequency_verdict"] = "unscoreable_key_has_no_value"
    elif not isinstance(got_f, (int, float)):
        out["frequency_verdict"] = "missed_no_value_adopted"
    else:
        # A key may state how close counts as right for ITS target, derived
        # from the linewidth that target actually showed. Where it does, that
        # beats a module-wide constant; where it does not, nothing changes.
        tol = key.get("frequency_tolerance_hz")
        tol = float(tol) if isinstance(tol, (int, float)) and tol > 0             else FREQ_TOL_HZ
        out["frequency_tolerance_hz"] = tol
        out["frequency_delta_hz"] = abs(got_f - want_f)
        out["frequency_verdict"] = ("match" if abs(got_f - want_f) <= tol
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
