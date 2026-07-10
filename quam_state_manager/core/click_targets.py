"""Click→state candidate registry for the N-D data viewer.

Given (experiment name, plot axis dims, entity kind) → ranked candidate state
fields a clicked point's coordinate can be written to. Two tiers:

  1. NODE tier — normalized experiment-name match (same normalization as the
     recipe registry: graph prefix + numeric index stripped, case-folded)
     against a curated map. Precise paths, highest rank.
  2. COORD tier — dim-NAME heuristics that need no node match (freq→f_01,
     flux→z offset). High-confidence classes only; a wrong suggestion is worse
     than none (the popup shows current-vs-clicked before any write, but the
     ranking must still be trustworthy).

IMPORTANT unit contract: ndview ships RAW coordinate values (axis TICKS are
raw; only titles carry units) — a clicked x/y is already in state units, so
candidates carry NO scale factor. (The legacy pipeline scaled axis values and
invited ×10⁶ paste errors; ndview deliberately does not.)

Paths use ``{q}`` (qubit name) / ``{p}`` (pair name) placeholders, substituted
with the clicked point's entity client-side.
"""

from __future__ import annotations

import re

# ── tier 1: node-name → dim-bound targets ──────────────────────────────────
# {normalized-name-prefix: [{dim_match, path, label}]}
#
# ``dim_match`` is a POSITIVE dim-NAME pattern the target binds to. The axis a
# dim lands on is sweep-size driven (largest→x) and can swap between runs of
# the same node (e.g. a vs_power run whose power sweep out-sizes freq), so a
# target must never assume "x" — it declares WHICH dim carries its quantity
# and the emitted candidate carries the resolved dim name for the client to
# match against the rendered axes.
_FREQ_DIM = re.compile(r"full_freq|rf_frequency|full_rf|freq", re.I)
_NODE_TARGETS: dict[str, list[dict]] = {
    "resonator_spectroscopy": [
        {"dim_match": _FREQ_DIM, "path": "qubits.{q}.resonator.f_01",
         "label": "Resonator frequency"},
        {"dim_match": _FREQ_DIM, "path": "qubits.{q}.resonator.RF_frequency",
         "label": "Resonator RF frequency"},
    ],
    "qubit_spectroscopy": [
        {"dim_match": _FREQ_DIM, "path": "qubits.{q}.f_01",
         "label": "Qubit f_01"},
        {"dim_match": _FREQ_DIM, "path": "qubits.{q}.xy.RF_frequency",
         "label": "Qubit drive RF frequency"},
    ],
    # NOTE: no ramsey/t1 entries — T2*/T1 are FIT-derived (never a clicked
    # point), and the idle_time axis is in ns while the state stores seconds
    # (a ×1e9 trap). Deliberately absent.
}

# ── tier 2: coordinate-name heuristics (high-confidence classes only) ──────
# ABSOLUTE-quantity axes only. A `detuning` axis is RELATIVE to the run-time
# RF — staging its raw value (~2e6) into f_01 would be catastrophically wrong;
# such transforms need the run's provenance and belong to the recipe-layer
# click contracts (interactive_plots/contracts.py), not this generic tier.
_COORD_TARGETS: list[dict] = [
    {"match": re.compile(r"full_freq|rf_frequency|full_rf", re.I),
     "exclude": re.compile(r"detuning|shift|offset", re.I),
     "paths": [("qubits.{q}.f_01", "Qubit f_01"),
               ("qubits.{q}.resonator.f_01", "Resonator f_01")]},
    {"match": re.compile(r"flux_bias|flux$", re.I),
     "exclude": re.compile(r"span|step", re.I),
     "paths": [("qubits.{q}.z.joint_offset", "Flux joint offset")]},
]


def _normalize(name: str) -> str:
    n = re.sub(r"^[12]Q_", "", name or "")
    n = re.sub(r"^[0-9]+[a-z]?_", "", n)
    return n.lower()


def candidates_for(experiment_name: str, x_dim: str | None,
                   y_dim: str | None, entity_kind: str | None) -> list[dict]:
    """Ranked candidates: ``[{axis, dim, path, label, tier}]`` (≤4).

    ``dim`` is the exact dim NAME the candidate's value lives on — the client
    resolves it against the rendered axes by name (never by index/position)
    and skips the candidate when neither axis carries that dim.

    ``entity_kind`` is the entity dim name ("qubit"/"qubit_pair") — pair-keyed
    runs get no qubit-path heuristics (a {q} path would be wrong for a pair)."""
    out: list[dict] = []
    seen: set[str] = set()
    norm = _normalize(experiment_name)

    # The generic ndview chip stages the RAW clicked coordinate — so a target
    # is only offered when the axis it reads is an ABSOLUTE quantity. A
    # relative axis (detuning/shift/Δ) would stage ~MHz offsets into ~GHz
    # fields; those transforms live in the recipe-layer click contracts
    # (interactive_plots/contracts.py) where run provenance is available.
    _RELATIVE = re.compile(r"detuning|shift|delta|prefactor", re.I)

    def _bind_dim(pattern: re.Pattern) -> tuple[str | None, str | None]:
        """First axis (x, then y) whose dim name POSITIVELY matches the
        target's pattern and is absolute → ``(axis, dim_name)``."""
        for axis, dim in (("x", x_dim), ("y", y_dim)):
            if dim and pattern.search(dim) and not _RELATIVE.search(dim):
                return axis, dim
        return None, None

    for prefix, targets in _NODE_TARGETS.items():
        if norm.startswith(prefix):
            for t in targets:
                axis, dim = _bind_dim(t["dim_match"])
                if dim is None:
                    continue   # no absolute dim carries this quantity → no staging
                if t["path"] not in seen:
                    seen.add(t["path"])
                    out.append({"axis": axis, "dim": dim, "path": t["path"],
                                "label": t["label"], "tier": "node"})
            break

    if entity_kind in (None, "qubit", "spec_qubit"):
        for h in _COORD_TARGETS:
            for axis, dim in (("x", x_dim), ("y", y_dim)):
                if not dim or not h["match"].search(dim):
                    continue
                if h.get("exclude") is not None and h["exclude"].search(dim):
                    continue
                for path, label in h["paths"]:
                    if path not in seen:
                        seen.add(path)
                        out.append({"axis": axis, "dim": dim, "path": path,
                                    "label": label, "tier": "coord"})
    return out[:4]
