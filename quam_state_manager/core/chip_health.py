"""Chip-health derivations for the Chip Status dashboard.

Pure — no Flask, no store mutation. Three jobs:

1. **Recency** — parse QUAM's calibration timestamps
   (``"2026-05-16 03:02:28 GMT+2"`` and ISO-8601) into a UTC epoch, so each
   metric can show "measured N days ago". The *client* renders the age (it owns
   the clock); the server just supplies the epoch.
2. **Thresholds** — default per-metric spec thresholds (pass / warn / fail) in
   STORED units. Shipped to the client, which owns the *live* verdict + colour
   (thresholds are UI-editable and persisted to localStorage). :func:`verdict`
   is the same logic in Python for tests / any server-side reuse.
3. **Aggregates** — chip-wide ``{min,max,avg,median,count}`` per metric,
   computed ONCE here instead of being re-derived in JS on every view switch.

Stored units (verified against real state): ``T1``/``T2ramsey``/``T2echo`` are in
**seconds**; fidelities are **fractions** in ``[0, 1]``; ``f_01`` is in Hz.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

# "2026-05-16 03:02:28 GMT+2"  /  "... GMT-5:30"  /  "...T..."  (offset optional)
_TS_RE = re.compile(
    r"^\s*(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})"
    r"(?:\s*(?:GMT|UTC)?\s*([+-]\d{1,2})(?::?(\d{2}))?)?\s*$"
)


def parse_quam_timestamp(s: Any) -> datetime | None:
    """Parse QUAM's ``"YYYY-MM-DD HH:MM:SS GMT±N"`` (or ISO-8601) → aware UTC datetime.

    Returns ``None`` for anything unparseable (real state has missing / odd
    values; a bad timestamp must never raise).
    """
    if not isinstance(s, str) or not s.strip():
        return None
    m = _TS_RE.match(s)
    if m:
        y, mo, d, h, mi, se, off_h, off_m = m.groups()
        total_off_min = 0
        if off_h is not None:
            oh = int(off_h)
            total_off_min = oh * 60 + (1 if oh >= 0 else -1) * (int(off_m) if off_m else 0)
        try:
            dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(se),
                          tzinfo=timezone(timedelta(minutes=total_off_min)))
        except ValueError:
            return None
        return dt.astimezone(timezone.utc)
    # Fallback: ISO-8601 (e.g. "2026-05-16T03:02:28+02:00" or naive).
    try:
        dt = datetime.fromisoformat(s.strip())
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def epoch_ms(s: Any) -> int | None:
    """Calibration timestamp → Unix epoch milliseconds (UTC), or ``None``."""
    dt = parse_quam_timestamp(s)
    return int(dt.timestamp() * 1000) if dt else None


def newest_epoch_ms(node: Any) -> int | None:
    """Newest ``*updated_at`` epoch anywhere in a (possibly nested) dict/list.

    A qubit/pair's "last calibrated" headline: most fields lack their own
    timestamp, so we take the freshest ``updated_at`` in its subtree.
    """
    best: int | None = None

    def _walk(o: Any) -> None:
        nonlocal best
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(k, str) and k.endswith("updated_at"):
                    e = epoch_ms(v)
                    if e is not None and (best is None or e > best):
                        best = e
                else:
                    _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)

    _walk(node)
    return best


# ── Metric glossary (THE single source of truth for per-metric metadata) ──────
# One entry per metric key carried by query._NODE_METRIC_KEYS + _EDGE_METRIC_KEYS.
# Each entry: {label, abbr, direction, blurb}.
#   * label   — long human name (panels, overview tiles, histogram axes, editor).
#   * abbr    — terse name for space-tight qubit cards / popup rows.
#   * direction — "higher" (bigger is better), "lower" (smaller is better), or
#     "neutral" (informational — no good/bad, so NO good-direction arrow and no
#     spec colour). ``verdict()`` treats anything != "lower" as higher-is-better,
#     and neutral metrics carry no thresholds, so this stays verdict-compatible.
#   * blurb   — one-line plain-language tooltip for non-experts.
# Deliberately NOT stored here: unit strings (µs / GHz / %). units.py is the unit
# canon; render sites derive the suffix there so it can't fork.
# This map collapses what used to be 4–5 disjoint label/direction sources
# (DEFAULT_THRESHOLDS, the client CARD_PROPS, PANEL_DEFS, histogram axes,
# METRIC_DISPLAY) into one — the arrow and the verdict colour now read ONE
# direction and can never disagree.
METRIC_META: dict[str, dict[str, Any]] = {
    # coherence — longer is better
    "T1":          {"label": "T1",                 "abbr": "T1",    "direction": "higher",
                    "blurb": "Energy-relaxation time — how long the qubit holds |1⟩ before decaying. Longer is better."},
    "T2ramsey":    {"label": "T2 Ramsey",          "abbr": "T2r",   "direction": "higher",
                    "blurb": "Dephasing time from a Ramsey scan (no echo). Sensitive to low-frequency noise. Longer is better."},
    "T2echo":      {"label": "T2 echo",            "abbr": "T2e",   "direction": "higher",
                    "blurb": "Dephasing time with a refocusing echo — removes slow noise, so usually ≥ T2 Ramsey. Longer is better."},
    # frequencies / spectroscopy — informational
    "f_01":        {"label": "Qubit frequency f₀₁", "abbr": "f₀₁",  "direction": "neutral",
                    "blurb": "0→1 transition frequency — the qubit's drive frequency."},
    "f_12":        {"label": "f₁₂ transition",      "abbr": "f₁₂",  "direction": "neutral",
                    "blurb": "1→2 transition frequency; f₀₁−f₁₂ gives the anharmonicity."},
    "anharmonicity": {"label": "Anharmonicity",     "abbr": "anharm", "direction": "neutral",
                    "blurb": "Spacing between the 0→1 and 1→2 transitions — keeps the qubit a two-level system. Typically negative."},
    "chi":         {"label": "Dispersive shift χ",  "abbr": "χ",     "direction": "neutral",
                    "blurb": "Qubit-state-dependent shift of the readout resonator — sets readout contrast."},
    "readout_frequency": {"label": "Readout frequency", "abbr": "f_ro", "direction": "neutral",
                    "blurb": "Resonator frequency used to read the qubit out."},
    # pulse calibration — informational (set by calibration, no spec direction)
    "x180_amplitude": {"label": "x180 amplitude",   "abbr": "x180",  "direction": "neutral",
                    "blurb": "Drive amplitude of the π (x180) pulse."},
    "x180_length": {"label": "x180 length",         "abbr": "x180 len", "direction": "neutral",
                    "blurb": "Duration of the π (x180) pulse."},
    "x180_alpha":  {"label": "x180 DRAG α",         "abbr": "x180 α", "direction": "neutral",
                    "blurb": "DRAG coefficient of the x180 pulse — suppresses leakage to |2⟩."},
    "x90_amplitude": {"label": "x90 amplitude",     "abbr": "x90",   "direction": "neutral",
                    "blurb": "Drive amplitude of the π/2 (x90) pulse."},
    "saturation_amplitude": {"label": "Saturation amplitude", "abbr": "sat", "direction": "neutral",
                    "blurb": "Amplitude of the long saturation pulse used in spectroscopy."},
    "readout_amplitude": {"label": "Readout amplitude", "abbr": "RO amp", "direction": "neutral",
                    "blurb": "Probe amplitude of the readout pulse."},
    "readout_length": {"label": "Readout length",   "abbr": "RO len", "direction": "neutral",
                    "blurb": "Duration of the readout pulse."},
    "readout_threshold": {"label": "Readout threshold", "abbr": "RO thr", "direction": "neutral",
                    "blurb": "IQ discrimination threshold separating |g⟩ from |e⟩."},
    # readout / gate fidelities — higher is better
    "assignment_fidelity": {"label": "Readout assignment fidelity", "abbr": "Assign F", "direction": "higher",
                    "blurb": "Single-shot fidelity of assigning the measured state from the IQ blobs. Higher is better."},
    "ro_fidelity_g": {"label": "Readout fidelity |g⟩", "abbr": "RO_fg", "direction": "higher",
                    "blurb": "Probability of correctly reading |g⟩ when the qubit is in |g⟩. Higher is better."},
    "ro_fidelity_e": {"label": "Readout fidelity |e⟩", "abbr": "RO_fe", "direction": "higher",
                    "blurb": "Probability of correctly reading |e⟩ when the qubit is in |e⟩. Higher is better."},
    "gate_fidelity_avg": {"label": "1Q gate fidelity", "abbr": "Gate F", "direction": "higher",
                    "blurb": "Average single-qubit gate fidelity from randomized benchmarking. Higher is better."},
    "gate_fidelity_x180": {"label": "1Q gate fidelity x180", "abbr": "GF x180", "direction": "higher",
                    "blurb": "Single-qubit fidelity for the π (x180) gate. Higher is better."},
    "gate_fidelity_x90": {"label": "1Q gate fidelity x90", "abbr": "GF x90", "direction": "higher",
                    "blurb": "Single-qubit fidelity for the π/2 (x90) gate. Higher is better."},
    # two-qubit (edge) metrics
    "cz_fidelity": {"label": "CZ Bell fidelity",    "abbr": "CZ F",  "direction": "higher",
                    "blurb": "Two-qubit CZ gate quality (best of the pair's candidate gates). Higher is better."},
    "detuning":    {"label": "Detuning",            "abbr": "detuning", "direction": "neutral",
                    "blurb": "Frequency detuning applied to the pair during the two-qubit gate."},
    "coupler_decouple_offset": {"label": "Coupler decouple offset", "abbr": "decouple", "direction": "neutral",
                    "blurb": "Coupler bias that turns the qubit-qubit interaction off (idle)."},
    "mutual_flux_bias": {"label": "Mutual flux bias", "abbr": "flux bias", "direction": "neutral",
                    "blurb": "Cross-flux compensation between the two qubits of the pair."},
}


def metric_meta(key: str) -> dict[str, Any]:
    """Return the glossary entry for *key*, or a safe neutral fallback.

    Fallback ``{label: key, abbr: key, direction: "neutral", blurb: ""}`` so an
    unknown / future metric key never crashes a render site or implies a verdict.
    """
    m = METRIC_META.get(key)
    if m is None:
        return {"label": key, "abbr": key, "direction": "neutral", "blurb": ""}
    return m


# Default spec thresholds in STORED units. ``direction`` and ``label`` are NOT
# duplicated here — they come from METRIC_META so the arrow, the editor label and
# the verdict colour share one definition. The CLIENT owns the live verdict +
# colour; these are the seed defaults, editable in the UI / persisted to
# localStorage. (pass ≥ warn > fail for higher-is-better.)
_THRESHOLD_BOUNDS: dict[str, dict[str, float]] = {
    "T1":                  {"warn": 30e-6, "fail": 10e-6},
    "T2ramsey":            {"warn": 20e-6, "fail": 5e-6},
    "T2echo":              {"warn": 30e-6, "fail": 10e-6},
    "assignment_fidelity": {"warn": 0.95,  "fail": 0.90},
    "gate_fidelity_avg":   {"warn": 0.99,  "fail": 0.95},
    "cz_fidelity":         {"warn": 0.95,  "fail": 0.90},
}
DEFAULT_THRESHOLDS: dict[str, dict[str, Any]] = {
    k: {"warn": b["warn"], "fail": b["fail"],
        "direction": metric_meta(k)["direction"], "label": metric_meta(k)["label"]}
    for k, b in _THRESHOLD_BOUNDS.items()
}


def verdict(value: Any, thresh: dict[str, Any] | None) -> str | None:
    """``"pass"`` / ``"warn"`` / ``"fail"`` for *value* against *thresh*, or ``None``.

    Mirrors the client logic so it can be unit-tested and reused server-side.
    """
    if thresh is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        warn = float(thresh["warn"])
        fail = float(thresh["fail"])
    except (KeyError, TypeError, ValueError):
        return None
    v = float(value)
    if thresh.get("direction", "higher") != "lower":
        return "pass" if v >= warn else ("warn" if v >= fail else "fail")
    return "pass" if v <= warn else ("warn" if v <= fail else "fail")


# ── Physicality (trust floor) ──────────────────────────────────────────────
# A value outside its physical bound (or non-finite) is a FAILED FIT, not a real
# measurement, and must never be averaged in, painted pass-green, or color the
# relative gradient. We do NOT change the stored value (CLAUDE.md: trust
# researcher input) — we QUARANTINE it from aggregation/colour/verdict and show
# it raw + hatched as "likely failed fit". Metrics not listed get only the
# finite-number check (e.g. anharmonicity is legitimately negative).
_FIDELITY_KEYS = frozenset({
    "assignment_fidelity", "gate_fidelity_avg", "gate_fidelity_x180",
    "gate_fidelity_x90", "ro_fidelity_g", "ro_fidelity_e", "cz_fidelity",
})
_POSITIVE_KEYS = frozenset({"T1", "T2ramsey", "T2echo"})
_FIDELITY_EPS = 1e-6  # tolerate float overshoot of an exact 1.0


def physicality(key: str, value: Any) -> bool:
    """Is *value* physically possible for metric *key*?

    ``None`` (missing) is physical-N/A → ``True`` (missing ≠ unphysical). A
    non-finite number, a fidelity outside ``(0, 1]``, or a non-positive
    coherence time is ``False``.
    """
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    if not math.isfinite(value):
        return False
    if key in _FIDELITY_KEYS:
        return 0.0 < value <= 1.0 + _FIDELITY_EPS
    if key in _POSITIVE_KEYS:
        return value > 0.0
    return True


def make_record(key: str, value: Any, *, updated_at: int | None = None,
                n: int | None = None, sigma: float | None = None,
                provenance: dict | None = None,
                thresholds: dict[str, dict] | None = None,
                unresolved: bool = False) -> dict[str, Any]:
    """The ONE constructor for a MetricRecord — the universal per-metric carrier.

    Fixed shape from day one (later phases populate ``n``/``sigma``/``provenance``):
    ``{value, raw, physical, unresolved, verdict, updated_at, n, sigma, provenance}``.
    ``value`` is the usable number (``None`` when missing / unphysical /
    unresolved → excluded from aggregates & colour); ``raw`` keeps the original
    for the "likely failed fit" tooltip; ``verdict`` is the DEFAULT-spec seed
    (the client recomputes the live verdict from ``value`` + its own thresholds)
    and is forced ``None`` whenever the value is not trustworthy.
    """
    finite_num = (isinstance(value, (int, float)) and not isinstance(value, bool)
                  and math.isfinite(value))
    phys = physicality(key, value)
    usable = value if (finite_num and phys and not unresolved) else None
    th = (thresholds or DEFAULT_THRESHOLDS).get(key)
    return {
        "value": usable,
        "raw": value if finite_num else None,
        "physical": bool(phys),
        "unresolved": bool(unresolved),
        "verdict": verdict(usable, th),
        "updated_at": updated_at,
        "n": n,
        "sigma": sigma,
        "provenance": provenance,
    }


def aggregate_records(rows: list[dict], metric_keys: list[str], *,
                      records_key: str = "metrics") -> dict[str, dict]:
    """Physical-gated aggregate over MetricRecords → honest counts + stats.

    For each metric key, reads ``row[records_key][key]`` and returns
    ``{measured, missing, bad, unresolved, total[, min, max, avg, median, count]}``
    where only PHYSICAL, resolved, non-None values feed min/max/avg/median (so a
    -473µs T2 never pollutes the mean and a dangling pointer is counted missing,
    not NaN). ``count`` == ``measured`` (kept for back-compat with the existing
    summary readers). A 30%-uncalibrated chip and a 30%-broken chip now produce
    visibly different counts.
    """
    out: dict[str, dict] = {}
    total = len(rows)
    for key in metric_keys:
        vals: list[float] = []
        measured = missing = bad = unres = 0
        for r in rows:
            rec = (r.get(records_key) or {}).get(key)
            if rec is None:
                missing += 1
                continue
            if rec.get("unresolved"):
                unres += 1
                missing += 1            # a dangling ref is "not measured", not "bad"
            elif not rec.get("physical"):
                bad += 1                # resolved but impossible → likely failed fit
            elif rec.get("value") is not None:
                vals.append(float(rec["value"]))
                measured += 1
            else:
                missing += 1            # physical & resolved but absent
        stats: dict[str, Any] = {"measured": measured, "missing": missing,
                                 "bad": bad, "unresolved": unres, "total": total}
        if vals:
            sv = sorted(vals)
            n = len(sv)
            mid = n // 2
            med = sv[mid] if n % 2 else (sv[mid - 1] + sv[mid]) / 2.0
            stats.update({"min": sv[0], "max": sv[-1], "avg": sum(sv) / n,
                          "median": med, "count": n})
        out[key] = stats
    return out


def aggregate(rows: list[dict], skip: set[str] | None = None) -> dict[str, dict]:
    """Per-key ``{min,max,avg,median,count}`` over the numeric values across rows.

    Booleans are skipped (``bool`` is an ``int`` subclass). Keys in *skip* (e.g.
    id/source/target) are ignored. The min/max double as a relative-colour
    domain so the client never has to scan all nodes to find it.
    """
    skip = skip or set()
    cols: dict[str, list[float]] = {}
    for r in rows:
        for k, v in r.items():
            if k in skip or isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                cols.setdefault(k, []).append(float(v))
    out: dict[str, dict] = {}
    for k, vals in cols.items():
        sv = sorted(vals)
        n = len(sv)
        mid = n // 2
        med = sv[mid] if n % 2 else (sv[mid - 1] + sv[mid]) / 2.0
        out[k] = {"min": sv[0], "max": sv[-1], "avg": sum(sv) / n, "median": med, "count": n}
    return out
