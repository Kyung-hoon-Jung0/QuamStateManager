"""What the agent is allowed to change, and inside which bounds (docs/78 P4).

The classification axis is **not** "is it a number?" — it is **"can a wrong
choice lie to us?"** (D-3). The whole safety story rests on the judge SEEING the
consequence, so what matters is whether a bad value produces an obviously bad
figure (self-revealing, cost = one run) or a plausible-looking invalid
measurement (deceptive, the error propagates):

* **class A — self-revealing.** Spans, steps, shots, flux ranges, drive power.
  ``num_shots = 3`` is a number and is perfectly safe: wrong ⇒ visibly noisy.
  The agent picks REAL NUMBERS here, inside code-owned bounds.
* **class B — deceptive.** ``reset_type``, ``use_state_discrimination``,
  ``multiplexed``. ``use_state_discrimination = True`` without calibrated IQ
  blobs is a boolean and is dangerous: clean-looking populations that are
  garbage. The agent may PROPOSE; code checks the precondition and may refuse.
* **frozen.** ``line_attenuation_in_db``, ``input_line_impedance_in_ohm`` —
  facts about the wiring. Changing one silently rescales every power.
* **reserved.** ``simulate``, the targets keys, and **``load_data_id``** — the
  one docs/78 D-3 wrongly claimed was already blocked (§17.6). A node given it
  replays archived data instead of measuring, so an agent could "calibrate" a
  chip with the fridge idle and report success. Nothing sets it today; the risk
  arrives exactly here, with an agent choosing parameters.

Bounds are **code-owned and data-derived** (D-5), never taken from the schema's
defaults — observed values leave those far behind (``num_shots = 3`` against a
default of 100; flux ±2.5 V against ±0.5 V). Sources, in order of authority:
hardware reach (:mod:`core.spec_constraints`), then what this lab has ACTUALLY
used per family (:mod:`core.autofit.corpus`).
"""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# --- D-3 classification ----------------------------------------------------
CLASS_A = frozenset({
    "frequency_span_in_mhz", "frequency_step_in_mhz", "num_shots",
    "num_averages", "num_flux_points", "num_freq_points", "num_power_points",
    "min_flux_offset_in_v", "max_flux_offset_in_v", "flux_offset_span_in_v",
    "min_flux", "max_flux", "frequency_detuning_in_mhz",
    "operation_amplitude_factor", "min_amp_factor", "max_amp_factor",
    "amp_factor_step", "max_number_pulses_per_sweep", "nb_of_pulses",
    "min_power_dbm", "max_power_dbm", "power_step_dbm", "max_amp", "max_power",
    "num_time_points", "num_amps", "num_frames", "idle_time_in_ns",
    "wait_time_in_ns", "min_wait_time_in_ns", "max_wait_time_in_ns",
})
CLASS_B = frozenset({
    "reset_type", "reset_type_thermal_or_active", "use_state_discrimination",
    "multiplexed", "use_waveform_report", "update_x90",
})
FROZEN = frozenset({
    "line_attenuation_in_db", "input_line_impedance_in_ohm",
})
RESERVED = frozenset({
    "simulate", "simulation_duration_ns", "timeout", "load_data_id",
    "qubits", "qubit_pairs", "targets",
})


def classify(key: str) -> str:
    """``A`` | ``B`` | ``frozen`` | ``reserved`` | ``unknown``.

    An UNKNOWN key is not class A by default. A parameter nobody has classified
    is a parameter nobody has thought about, and the deceptive ones are exactly
    the ones that look harmless.
    """
    if key in RESERVED:
        return "reserved"
    if key in FROZEN:
        return "frozen"
    if key in CLASS_B:
        return "B"
    if key in CLASS_A:
        return "A"
    return "unknown"


def sanitize(params: dict | None, *, targets_name: str | None = None
             ) -> tuple[dict, list[dict]]:
    """Strip everything the agent may not set. Returns ``(clean, dropped)``.

    ``dropped`` is never empty-and-silent: every removal carries its reason so
    the ledger can show that a proposal was narrowed rather than obeyed.
    """
    clean, dropped = {}, []
    for k, v in (params or {}).items():
        kind = classify(k)
        if k == targets_name:
            kind = "reserved"
        if kind in ("reserved", "frozen", "unknown"):
            dropped.append({"key": k, "value": v, "class": kind,
                            "reason": _DROP_REASON[kind]})
            continue
        clean[k] = v
    return clean, dropped


_DROP_REASON = {
    "reserved": "reserved — the agent never sets this (load_data_id would "
                "replay archived data instead of measuring)",
    "frozen": "frozen — a fact about the wiring; changing it silently "
              "rescales every power",
    # `classify` says an unclassified key is one nobody has thought about, and
    # `reduced_schema` already refuses to expose one — but `sanitize` used to
    # let it THROUGH, and sanitize is the function that runs on the real
    # backend path. Two halves of one policy disagreeing means the stricter
    # half was decorative. Dropping is the safe direction: a key we cannot
    # classify is one whose wrong value we cannot predict.
    "unknown": "unclassified — no one has judged whether a wrong value here "
               "would be self-revealing or deceptive, so it is not offered",
}


# --- D-5 bounds ------------------------------------------------------------
# hardware reach, from spec_constraints. Only the ones an agent can actually
# reach through node parameters; the rest of that module guards the wiring.
def _hardware_bounds() -> dict[str, tuple[float | None, float | None]]:
    from quam_state_manager.core import spec_constraints as sc

    if_mhz = float(getattr(sc, "IF_LIMIT_XY_HZ", 500e6)) / 1e6
    return {
        # a span wider than the IF window cannot be produced by the hardware
        "frequency_span_in_mhz": (0.1, 2 * if_mhz),
        "frequency_detuning_in_mhz": (-if_mhz, if_mhz),
        "operation_amplitude_factor": (0.0, 2.0),
        "min_amp_factor": (0.0, 4.0),
        "max_amp_factor": (0.0, 4.0),
        "num_shots": (1, 1_000_000),
        "num_averages": (1, 1_000_000),
    }


# A corpus envelope is a sample, not a limit. Measured (docs/78 §22.1): a
# zero-slack `[min_observed, max_observed]` is vacuous on the data it was built
# from (0 rejections in 636 runs) and rejects 2-22% of real usage the moment it
# meets an archive it was not built from. So an observed range is widened
# before it is enforced.
_CORPUS_SLACK = 3.0

# Sweep EDGES. Their danger is ONE-SIDED — a narrower span, a coarser floor, a
# smaller start power are all strictly safer than the observed extreme, and
# bounding them from both sides rejected `min_power_dbm = -40` for being
# "above the allowed -50" and `num_flux_points = 41` for being "below the
# allowed 101". Only the outward side is constrained.
_EDGE_ONLY_MAX = ("max_power_dbm", "max_amp", "max_amp_factor", "max_flux",
                  "max_flux_offset_in_v", "max_wait_time_in_ns",
                  "max_number_pulses_per_sweep", "flux_offset_span_in_v",
                  "num_flux_points", "num_freq_points", "num_power_points",
                  "num_time_points", "num_amps", "num_frames",
                  "amp_factor_step", "frequency_step_in_mhz", "power_step_dbm")
_EDGE_ONLY_MIN = ("min_power_dbm", "min_amp_factor", "min_flux",
                  "min_flux_offset_in_v", "min_wait_time_in_ns")


def bounds_for(family: str, corpus_ranges: dict | None = None,
               schema_defaults: dict | None = None) -> dict:
    """``{param: {"min": x, "max": y, "source": "..."}}`` for one family.

    The corpus WIDENS the hardware envelope where the lab has genuinely gone
    further; hardware reach is never widened past its physical limit. Three
    rules keep an observed sample from becoming a false constraint:

    * **slack** — an observed range is stretched by ``_CORPUS_SLACK`` before it
      binds, because the next legitimate run is not obliged to fall inside the
      last hundred;
    * **one-sided edges** — a sweep edge is only bounded on its dangerous side;
    * **no default-derived edge** — a knob nobody varied puts its schema
      DEFAULT at both ends of its own envelope, which is precisely the source
      the docstring promises never to use (measured: 69 of 101 corpus edges
      landed exactly on a recorded default). Pass ``schema_defaults`` and a
      degenerate range that merely echoes one is dropped.
    """
    hw = _hardware_bounds()
    out: dict[str, dict] = {}
    for k, (lo, hi) in hw.items():
        out[k] = {"min": lo, "max": hi, "source": "hardware"}

    defaults = (schema_defaults or {}).get(family) or {}
    observed = ((corpus_ranges or {}).get(family) or {})
    for k, rng in observed.items():
        lo, hi = _range_of(rng)
        if lo is None and hi is None:
            continue
        # a knob nobody varied: its "range" is one value, and that value is the
        # schema default. Enforcing it would enforce the default.
        if lo is not None and hi is not None and lo == hi:
            d = _num(defaults.get(k))
            if d is None or d == lo:
                continue
        lo, hi = _with_slack(lo, hi)
        if k in _EDGE_ONLY_MAX:
            lo = None
        elif k in _EDGE_ONLY_MIN:
            hi = None
        cur = out.get(k)
        if cur is None:
            out[k] = {"min": lo, "max": hi, "source": "corpus"}
            continue
        new_lo = cur["min"] if cur["min"] is None else (
            min(cur["min"], lo) if lo is not None else cur["min"])
        new_hi = cur["max"] if cur["max"] is None else (
            max(cur["max"], hi) if hi is not None else cur["max"])
        if k in ("frequency_span_in_mhz", "frequency_detuning_in_mhz"):
            new_hi = cur["max"]          # physical ceiling — never widened
        out[k] = {"min": new_lo, "max": new_hi, "source": "hardware+corpus"}
    return out


def _with_slack(lo, hi):
    """Widen an observed range about its own centre. A range of one point is
    widened about that point, so a single observation does not become a pin."""
    if lo is None or hi is None:
        return lo, hi
    if hi == lo:
        pad = abs(lo) * (_CORPUS_SLACK - 1.0) or 1.0
        return lo - pad, hi + pad
    mid, half = (lo + hi) / 2.0, (hi - lo) / 2.0 * _CORPUS_SLACK
    return mid - half, mid + half


def _range_of(rng: Any) -> tuple[float | None, float | None]:
    if isinstance(rng, dict):
        return _num(rng.get("min")), _num(rng.get("max"))
    if isinstance(rng, (list, tuple)) and len(rng) >= 2:
        return _num(rng[0]), _num(rng[1])
    return None, None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) else None


# --- D-4 reduced schema ----------------------------------------------------
def reduced_schema(node_schema: dict | None, family: str, *,
                   corpus_ranges: dict | None = None,
                   targets_name: str | None = None) -> dict:
    """A JSON Schema the agent's proposal must validate against.

    Built FROM the node's own recorded schema, with class-B keys **removed**
    (they are proposed separately and precondition-checked) and class-A keys
    **annotated with bounds**. Validation is then mechanical — we never have to
    trust the agent, which is `auditor.py`'s trick in reverse: there the schema
    makes a number structurally impossible, here it opens exactly the fields we
    allow and nothing else.
    """
    props_in = ((node_schema or {}).get("properties")
                if isinstance(node_schema, dict) else None) or {}
    limits = bounds_for(family, corpus_ranges)
    props: dict[str, dict] = {}
    for key, spec in props_in.items():
        if classify(key) != "A" or key == targets_name:
            continue
        entry = {k: v for k, v in (spec or {}).items()
                 if k in ("type", "description", "items")}
        b = limits.get(key)
        if b:
            if b.get("min") is not None:
                entry["minimum"] = b["min"]
            if b.get("max") is not None:
                entry["maximum"] = b["max"]
            entry["x-bound-source"] = b.get("source")
        props[key] = entry
    return {"type": "object", "additionalProperties": False,
            "properties": props}


def validate_proposal(proposal: dict | None, schema: dict
                      ) -> tuple[dict, list[str]]:
    """Keep only what the schema allows and what fits its bounds.

    Returns ``(accepted, rejections)``. A value outside its bound is REJECTED,
    not clamped: clamping would hand the loop a number nobody chose and hide
    that the agent asked for something impossible.
    """
    props = (schema or {}).get("properties") or {}
    ok, bad = {}, []
    for k, v in (proposal or {}).items():
        spec = props.get(k)
        if spec is None:
            bad.append(f"{k}: not in the allowed set for this step")
            continue
        n = _num(v)
        if n is None:
            if isinstance(v, (list, tuple)):
                ok[k] = v
            else:
                bad.append(f"{k}: {v!r} is not a number")
            continue
        lo, hi = spec.get("minimum"), spec.get("maximum")
        if lo is not None and n < lo:
            bad.append(f"{k}={v} below the allowed {lo}")
            continue
        if hi is not None and n > hi:
            bad.append(f"{k}={v} above the allowed {hi}")
            continue
        ok[k] = v
    return ok, bad


# --- D-3 class-B preconditions ---------------------------------------------
# The recorded schema states these in PROSE ("Must be implemented as a method
# of Quam.qubit"). Prose is the specification; enforcement has to be code.

def check_class_b(key: str, value: Any, *, chip_facts: dict | None = None
                  ) -> tuple[bool, str]:
    """``(allowed, reason)`` for one class-B proposal.

    Refusing is the default when the precondition cannot be CHECKED — an
    unverifiable precondition is not a satisfied one.
    """
    facts = chip_facts or {}
    if key in ("reset_type", "reset_type_thermal_or_active"):
        if value in (None, "thermal"):
            return True, "thermal reset needs nothing"
        available = facts.get("reset_methods")
        if not isinstance(available, (list, tuple, set)):
            return False, ("cannot verify that this chip implements "
                           f"{value!r} as a reset method — refusing")
        return (value in available,
                f"{value!r} " + ("is implemented" if value in available
                                 else "is NOT implemented on this chip"))
    if key == "use_state_discrimination":
        if not value:
            return True, "off needs nothing"
        if facts.get("iq_blobs_calibrated") is True:
            return True, "IQ blobs are calibrated"
        return False, ("state discrimination without calibrated IQ blobs "
                       "produces clean-looking populations that are garbage")
    if key == "multiplexed":
        if not value:
            return True, "off needs nothing"
        if facts.get("multiplexed_ready") is True:
            return True, "readout lines support multiplexing"
        return False, "cannot verify multiplexed readout is safe on this chip"
    if key == "update_x90":
        return True, "writes a second amplitude the node itself computes"
    if key == "use_waveform_report":
        return True, "diagnostic output only"
    return False, f"{key!r} is class B with no precondition check — refusing"


def apply_class_b(proposals: dict | None, *, chip_facts: dict | None = None
                  ) -> tuple[dict, list[dict]]:
    """Filter class-B proposals through their preconditions."""
    ok, refused = {}, []
    for k, v in (proposals or {}).items():
        if classify(k) != "B":
            continue
        allowed, why = check_class_b(k, v, chip_facts=chip_facts)
        if allowed:
            ok[k] = v
        else:
            refused.append({"key": k, "value": v, "reason": why})
    return ok, refused
