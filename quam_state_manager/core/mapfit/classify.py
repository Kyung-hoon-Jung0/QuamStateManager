"""Classify maps by coordinate meaning, never by experiment names."""
from __future__ import annotations

import numpy as np

VOCABULARY = {
    "count": ("nb_of_pulses", "n_gates", "number_of_", "number of pulses", "number of gates", "nb of pulses", "pulse count", "gate count", "n"),
    "amplitude": ("amplitude", "prefactor", "a_scan", "b_over_a", "amp"),
    "frequency": ("frequency", "detuning", "freq", "hz"),
    "time": ("time", "duration", " ns", " us", " ms"),
    "control": ("flux", "bias", "voltage", " dv ", "(mv)", "power", "amplitude", "a_scan", "b_over_a", "t_phi", "neg_offset", "detuning"),
}
MENUS = {"erroramp": ("erroramp", "extremum"), "ridge": ("ridge", "extremum"),
         "chevron": ("chevron", "extremum"), "landscape": ("landscape", "extremum"),
         "unknown": ()}


def circular_period(z, meta):
    """Detect circle units by range, including turns mislabeled as degrees."""
    label = str(meta.get("signal_label", "")) + " " + str(meta.get("signal_units", ""))
    if not any(word in label.lower() for word in ("phase", "deg", "turn", "rad")):
        return None
    finite = np.asarray(z, float)
    finite = finite[np.isfinite(finite)]
    if not finite.size:
        return None
    if "rad" in label.lower():
        return 2 * np.pi
    if "turn" in label.lower() or np.max(np.abs(finite)) <= 1.01:
        return 1.0
    return 360.0


def _roles(values, descriptor, vocabulary):
    text = " ".join(str(descriptor.get(k, "")) for k in ("name", "label", "units", "dim")).lower().strip()
    padded = " " + text + " "  # unit words such as " ns" also match a units-only descriptor
    roles = {role for role, words in vocabulary.items()
             if any((text == word if word == "n" else word in padded) for word in words)}
    a = np.asarray(values, float)
    # Only infer an unlabeled count; a labeled voltage grid of small integers is not a count.
    if not text and 1 < a.size <= 64 and np.all(np.isfinite(a)) and np.all(a >= 0) and np.max(a) <= 512:
        if np.allclose(a, np.round(a)) and np.allclose(np.diff(a), np.diff(a)[0]):
            roles.add("count")
    return roles


def classify(x, y, meta=None, z=None, vocabulary=None):
    """Return type, oriented axis roles, circular period and the permitted model menu."""
    meta = meta or {}
    rx = _roles(x, meta.get("x", {}), vocabulary or VOCABULARY)
    ry = _roles(y, meta.get("y", {}), vocabulary or VOCABULARY)
    labels = " ".join(str(meta.get(dim, {})) for dim in ("x", "y")).lower()
    signal = (str(meta.get("map_label", "")) + " " + str(meta.get("signal_label", ""))).lower()
    reason = "No supported model for these coordinate quantities; smoothing and contours only."
    unsupported = any(word in labels + " " + signal for word in
                      ("idle time", "idle_time", "relaxation", "echo", "drag coefficient", "drag alpha"))
    metric = any(word in signal for word in ("blob_radius", "blob radius", "outlier", "distance_map", "fidelity"))
    kind, feature_axis = "unknown", None
    for first, second, candidate in (("count", "amplitude", "erroramp"),
                                     ("time", "frequency", "chevron"),
                                     ("time", "control", "chevron"),
                                     ("frequency", "control", "ridge")):
        if first in ry and second in rx:
            kind, feature_axis = candidate, "y"
            break
        if first in rx and second in ry:
            kind, feature_axis = candidate, "x"
            break
    if kind == "unknown" and "control" in rx and "control" in ry:
        kind = "landscape"
    if unsupported or metric:
        kind, feature_axis = "unknown", None
        reason = ("No supported decay model; smoothing and contours only." if unsupported and "drag" not in labels
                  else "No supported model for this figure family; smoothing and contours only.")
    return {"type": kind, "feature_axis": feature_axis, "models": list(MENUS[kind]),
            "reason": reason if kind == "unknown" else "",
            "circular": circular_period(z, meta) if z is not None else None}
