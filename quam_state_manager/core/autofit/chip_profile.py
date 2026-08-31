"""Chip profile — the design facts a human operator knows before calibrating.

The docs/134 doctrine, verbatim: a chip's design facts are ENUMERATED CASES,
never universal rules and never a customer's rules. This module owns the case
space (code-curated, like ``families.py`` owns metric bands); the CHIP's
answers live in its own ``state.json`` under ``extras.sm_profile`` so the
profile travels with the chip. SM parses; when a consumed field is missing,
the GUI asks — knowledge rules gated on an unanswered field stay INACTIVE and
surface the question instead of guessing (the conservative branch is silence,
not a default case).

Three invariants:

* **Unknown is an answer.** Every reader must behave identically with no
  profile at all; a field's absence activates nothing and silences nothing
  except the rules explicitly gated on it.
* **A contradiction is an alarm, never a correction.** When a measured map
  shape contradicts the declared case (the profile says the resonator barely
  responds to coupler flux and the map sweeps a full arch), the finding is
  reported and stamped; the profile is never rewritten by code.
* **The answers are part of a verdict's warrant.** ``profile_hash`` joins the
  verification context: a judgment branched on one answer set is not
  comparable to a judgment branched on another (verification.py doctrine).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

# where the answers live inside state.json — extras is free-form by doctrine
# (docs/81), so string cases store verbatim with no numeric-string alarm
PROFILE_PATH = "extras.sm_profile"

UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProfileField:
    key: str
    question: str                 # what the GUI asks, in the user's terms
    cases: tuple[str, ...]        # the enumerated case space
    consumers: tuple[str, ...]    # family keys whose knowledge branches on it
    help: str = ""                # one line of why this matters


# The seed registry — the six fields the pilot chip proved are real branch
# points (docs/134 §3). Extending this tuple is how a new branch point enters
# the product; a knowledge rule may only gate on keys that exist here.
FIELDS: tuple[ProfileField, ...] = (
    ProfileField(
        key="two_dip_identity",
        question="When a resonator spectroscopy window shows TWO dips, what "
                 "is the second one on this chip?",
        cases=("purcell_companion", "rival_neighbor", "none_expected"),
        consumers=("resonator_spectroscopy",),
        help="A Purcell-filter companion is usually the WIDER (lower-Q) dip "
             "and sits at a chip-fixed offset from its own resonator; a "
             "rival is a neighbouring qubit's resonator caught by a wide "
             "multiplexed window. Same picture, opposite correct choice.",
    ),
    ProfileField(
        key="coupler_position",
        question="Where does the coupler frequency sit on this chip?",
        cases=("below_qubit", "between_qubit_and_resonator",
               "above_resonator"),
        consumers=("qubit_spectroscopy_vs_flux",),
        help="A coupler below the qubit gives a clean dispersive pull in "
             "qubit-spec-vs-coupler-flux; one between qubit and resonator "
             "hybridizes and an avoided crossing inside the window is "
             "EXPECTED, not a defect.",
    ),
    ProfileField(
        key="res_vs_coupler_response",
        question="Is a nearly-flat resonator-vs-coupler-flux map NORMAL on "
                 "this chip?",
        cases=("weak_flat_normal", "strong"),
        consumers=("resonator_spectroscopy_vs_coupler_flux",),
        help="On a weak-response chip the 07-style map is verification only "
             "and the coupler decision lives in the qubit-spec map; flat "
             "must then not be punished as no-signal.",
    ),
    ProfileField(
        key="coupler_parking_rule",
        question="What is this chip's coupler parking rule?",
        cases=("minima_below_anticrossing", "not_applicable"),
        consumers=(),   # consumed by the upcoming pair/jazz round
        help="The pilot chip parks the coupler at the flux minima below the "
             "avoided crossing (set by hand from the map, refined by the "
             "zz sequence).",
    ),
    ProfileField(
        key="res_vs_flux_parking",
        question="Where does resonator-spectroscopy-vs-flux PARK the qubit "
                 "flux on this chip?",
        cases=("resonator_freq_maxima", "resonator_freq_minima"),
        consumers=("resonator_spectroscopy_vs_flux",),
        help="Verified on the pilot archive: the node cosine-fits the dip "
             "ridge and applies the MAX ('max offset' in its own legend) — "
             "physically the qubit upper sweet spot when the qubit sits "
             "below the resonator. Another chip may legitimately be the "
             "minima case; a rule must branch here, never hard-code either.",
    ),
    ProfileField(
        key="pair_work_1q_recal",
        question="Does pair work on this chip require single-qubit "
                 "re-calibration (rabi/ramsey) before AND after?",
        cases=("required_before_after", "not_required"),
        consumers=(),   # consumed by the upcoming pair/jazz round
        help="On the pilot chip every per-pair cell re-runs spec/rabi/"
             "ramsey/blobs after moving the coupler point and after the zz "
             "sequence.",
    ),
)

FIELD_BY_KEY: dict[str, ProfileField] = {f.key: f for f in FIELDS}


@dataclass
class ProfileReading:
    """What ``state.json`` actually said, split honestly."""
    answers: dict[str, str] = field(default_factory=dict)   # valid only
    invalid: dict[str, Any] = field(default_factory=dict)   # present, not a case
    missing: list[str] = field(default_factory=list)        # unanswered keys
    unknown_keys: list[str] = field(default_factory=list)   # not in FIELDS


def read_profile(state: dict | None) -> ProfileReading:
    """Parse ``extras.sm_profile`` out of a state dict. Never raises."""
    r = ProfileReading()
    raw: Any = None
    if isinstance(state, dict):
        extras = state.get("extras")
        if isinstance(extras, dict):
            raw = extras.get("sm_profile")
    if not isinstance(raw, dict):
        raw = {}
    for k, v in raw.items():
        f = FIELD_BY_KEY.get(k)
        if f is None:
            r.unknown_keys.append(k)        # preserved, never deleted
        elif isinstance(v, str) and (v in f.cases or v == UNKNOWN):
            if v != UNKNOWN:
                r.answers[k] = v
        else:
            r.invalid[k] = v                # present but not a listed case
    for f in FIELDS:
        if f.key not in r.answers:
            r.missing.append(f.key)
    return r


def profile_hash(answers: dict[str, str] | None) -> str | None:
    """Identity of the answer set for the verification context.

    ``None`` when nothing is answered — an absent profile is the SAME
    context as before profiles existed, so old verdicts stay comparable.
    """
    if not answers:
        return None
    basis = json.dumps(dict(sorted(answers.items())),
                       sort_keys=True).encode("utf-8")
    return hashlib.sha1(basis).hexdigest()[:16]


def questions(reading: ProfileReading,
              families: list[str] | None = None) -> list[dict]:
    """The GUI question list: unanswered (or invalid) fields, optionally
    only those a given set of families actually consumes. Fields nothing
    consumes yet are still asked when ``families`` is None — the form is
    also how the upcoming rounds' answers get collected."""
    out = []
    for f in FIELDS:
        if f.key in reading.answers:
            continue
        if families is not None and not (set(f.consumers) & set(families)):
            continue
        out.append({"key": f.key, "question": f.question,
                    "cases": list(f.cases), "help": f.help,
                    "invalid_value": reading.invalid.get(f.key)})
    return out


# --- profile-vs-measurement contradictions ---------------------------------
# (field, declared case, family) -> {signal -> alarm text}. Only signals
# mapcases actually emits appear here; an entry is an ALARM, never a rewrite.
_CONTRADICTIONS: dict[tuple[str, str, str], dict[str, str]] = {
    ("res_vs_coupler_response", "weak_flat_normal",
     "resonator_spectroscopy_vs_coupler_flux"): {
        "curve_full_swing": "profile declares the resonator barely responds "
                            "to coupler flux, but this map sweeps a full "
                            "swing — the declared case or the wiring is wrong",
        "curve_arch_vertex_inside": "profile declares weak coupler-flux "
                                    "response, but this map traces a full "
                                    "arch with its vertex inside the window",
    },
    ("res_vs_coupler_response", "strong",
     "resonator_spectroscopy_vs_coupler_flux"): {
        "curve_flat_no_response": "profile declares a strong coupler-flux "
                                  "response, but this map is flat — the "
                                  "window may be too narrow, or the declared "
                                  "case is wrong; widen before concluding",
    },
    ("two_dip_identity", "none_expected", "resonator_spectroscopy"): {
        "line_multi_feature": "profile declares single-dip resonators, but "
                              "this trace resolves more than one feature",
    },
}


def contradiction(family: str, signal: str,
                  answers: dict[str, str]) -> str | None:
    """One alarm string when the measured signal contradicts a declared
    case, else None. Reported and stamped, never acted on."""
    for (fkey, case, fam), sigmap in _CONTRADICTIONS.items():
        if fam == family and answers.get(fkey) == case and signal in sigmap:
            return f"profile contradiction [{fkey}={case}]: {sigmap[signal]}"
    return None
