"""Map an experiment run's fitted values to writable QUAM state dot-paths.

The Dataset detail panel's Results tab shows each run's ``fit_results`` (a nested
``{qubit_or_pair: {fit_key: value}}``). Some of those fitted values are
calibration *outputs* the experiment writes back into the QUAM state (a readout
amplitude, a qubit frequency, a DRAG alpha, …); the rest are pure diagnostics
(``fidelity``, ``alpha`` of an RB decay, ``rms_error``, ``success``, …) that must
never be pushed.

This module is the curated source of truth for "which fit_key updates which state
field, and in what units" — the analogue of ``_QUBIT_PROPERTY_MAP`` (routes) and
the recipe ``clickable`` specs. Only mapped keys get an "Apply" affordance in the
UI; everything else stays copy-only.

``resolve_fit_targets(run)`` turns a run's fit_results into
``{qname: {fit_key: {"path", "value", "label"}}}`` ready for the template, applying
the per-entry ``scale`` and skipping non-numeric / NaN / boolean values.

IMPORTANT — every entry's ``scale`` and ``path`` here was validated against real
``data.json`` fit values vs the same run's ``quam_state/state.json`` field. Each
mapped value is in the SAME unit as the target field (``scale: 1.0``). Entries whose
state mapping needs a non-constant or state-dependent transform were deliberately
omitted rather than risk a wrong write:
  * ``ge_threshold`` / ``rus_threshold`` — stored as ``value * readout_length/2**12``
    (a per-qubit, length-dependent scale), not a constant.
  * ``freq_vs_flux_01_quad_term`` (ramsey-vs-flux ``quad_term``) — stored ×1000.
  * 2Q CZ phase compensation (``control_phase_correction`` / ``target_phase_correction``)
    — the node stores ``(old - fit) % 1``: it reads the current ``phase_shift_control`` /
    ``phase_shift_target`` and folds the fitted delta in. That is state-dependent, which
    the ``value = fit * scale`` model here cannot express, so it stays copy-only.
The 2Q CZ ``optimal_amplitude`` IS mapped, but operation-aware: the node writes it to
``qubit_pairs.{pair}.macros.{operation}.flux_pulse_qubit.amplitude`` where ``operation``
is a node parameter (``cz_flattop`` / ``cz_unipolar`` / …). ``resolve_fit_targets`` fills
``{operation}`` from ``run.parameters`` and SKIPS the target when no operation is recorded
— a static ``macros.cz`` path would mis-route, since ``macros.cz`` is a ``#./cz_unipolar``
alias and a run may have calibrated a different variant.
Paths that traverse a ``#./`` operation alias (e.g. ``operations.x180`` →
``x180_DragCosine``) are fine: ``_resolve_edit_path`` in routes resolves them at
write time, exactly as the power_rabi / drag recipe ``clickable`` specs rely on.
"""
from __future__ import annotations

import math
from typing import Any


def node_operation(params: Any) -> Any:
    """The operation a node run calibrated, read from its parameters: the
    ``arb_operation`` parameter when it is set, else ``operation``. Every SM
    reader that fills an ``{operation}`` path slot from a run asks this one
    function, so a run on a named gate lands on that gate's macro."""
    p = params if isinstance(params, dict) else {}
    return p.get("arb_operation") or p.get("operation")

# experiment-name prefix -> { fit_key: {"path": <dot-template with {q}/{pair}>,
#                                       "scale": float, "label": str} }
FIT_TARGET_MAP: dict[str, dict[str, dict[str, Any]]] = {
    "1Q_03_resonator_spectroscopy": {
        "frequency": {"path": "qubits.{q}.resonator.f_01", "scale": 1.0,
                      "label": "Resonator frequency"},
    },
    "1Q_05_resonator_spectroscopy_vs_power": {
        "resonator_frequency": {"path": "qubits.{q}.resonator.f_01", "scale": 1.0,
                                "label": "Resonator frequency"},
    },
    "1Q_08_qubit_spectroscopy": {
        "frequency": {"path": "qubits.{q}.f_01", "scale": 1.0, "label": "Qubit f_01"},
        "iw_angle": {"path": "qubits.{q}.resonator.operations.readout.integration_weights_angle",
                     "scale": 1.0, "label": "IW angle"},
        "x180_amp": {"path": "qubits.{q}.xy.operations.x180.amplitude", "scale": 1.0,
                     "label": "x180 amplitude"},
        "saturation_amp": {"path": "qubits.{q}.xy.operations.saturation.amplitude", "scale": 1.0,
                           "label": "Saturation amplitude"},
    },
    "1Q_09_qubit_spectroscopy_vs_flux": {
        "qubit_frequency": {"path": "qubits.{q}.f_01", "scale": 1.0, "label": "Qubit f_01"},
    },
    "1Q_11_power_rabi": {
        "opt_amp": {"path": "qubits.{q}.xy.operations.x180.amplitude", "scale": 1.0,
                    "label": "x180 amplitude"},
    },
    "1Q_13_drag_calibration": {
        "alpha": {"path": "qubits.{q}.xy.operations.x180.alpha", "scale": 1.0,
                  "label": "DRAG alpha"},
    },
    "1Q_15a_readout_frequency_optimization": {
        "optimal_frequency": {"path": "qubits.{q}.resonator.f_01", "scale": 1.0,
                              "label": "Readout frequency"},
    },
    "1Q_15b_readout_power_optimization": {
        "optimal_amplitude": {"path": "qubits.{q}.resonator.operations.readout.amplitude",
                              "scale": 1.0, "label": "Readout amplitude"},
        "iw_angle": {"path": "qubits.{q}.resonator.operations.readout.integration_weights_angle",
                     "scale": 1.0, "label": "IW angle"},
    },
    "1Q_16_iq_blobs": {
        "iw_angle": {"path": "qubits.{q}.resonator.operations.readout.integration_weights_angle",
                     "scale": 1.0, "label": "IW angle"},
    },
    "1Q_30a_gef_readout_power_optimization": {
        "optimal_amplitude": {"path": "qubits.{q}.resonator.operations.readout_GEF.amplitude",
                              "scale": 1.0, "label": "GEF readout amplitude"},
    },
    "2Q_20b_cz_conditional_phase_error_amp": {
        # Node writes optimal_amplitude straight into the calibrated CZ variant:
        # qp.macros[operation].flux_pulse_qubit.amplitude. ``{operation}`` is
        # filled from run.parameters (see resolve_fit_targets) so we hit the
        # exact macro the run calibrated, not the default the macros.cz alias
        # points at.
        "optimal_amplitude": {"path": "qubit_pairs.{pair}.macros.{operation}.flux_pulse_qubit.amplitude",
                              "scale": 1.0, "label": "CZ flux-pulse amplitude"},
    },
}


def curated_fit_keys() -> list[str]:
    """Fit-result key names that map to a known state field (the union of all
    second-level keys in FIT_TARGET_MAP, in map order). The Datasets Sort banner
    floats these meaningful metrics before the long alphabetical tail of one-off
    diagnostic keys."""
    seen: list[str] = []
    for spec in FIT_TARGET_MAP.values():
        for fk in spec:
            if fk not in seen:
                seen.append(fk)
    return seen


def _match(experiment_name: str) -> dict[str, dict[str, Any]]:
    """The fit_key spec for the longest experiment-prefix that matches ``name``.

    Match through registry._normalize_node_name on BOTH sides: FIT_TARGET_MAP keys are
    graph-prefixed ("1Q_03_resonator_spectroscopy"), so a plain startswith missed
    standalone-launched runs ("03_resonator_spectroscopy_single") and their fit rows
    silently lost the Apply affordance — the same normalization the recipe registry
    got for exactly this reason (doc 48)."""
    from quam_state_manager.core.interactive_plots.registry import _normalize_node_name
    norm_name = _normalize_node_name(experiment_name)
    best: tuple[str, dict] | None = None
    for prefix, spec in FIT_TARGET_MAP.items():
        np = _normalize_node_name(prefix)
        if norm_name.startswith(np) and (best is None or len(np) > len(best[0])):
            best = (np, spec)
    return best[1] if best else {}


def _pushable(v: Any) -> bool:
    """A fit value is pushable only if it's a finite real number (not bool)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _attr(run, key, default=None):
    """Read ``key`` from a run that may be a RunInfo object OR a plain dict
    (``DatasetStore.get_run`` returns the dict form for the detail view)."""
    if isinstance(run, dict):
        return run.get(key, default)
    return getattr(run, key, default)


def resolve_fit_targets(run, names=None) -> dict[str, dict[str, dict[str, Any]]]:
    """Map a run's pushable fit_results to state dot-paths.

    Returns ``{qname: {fit_key: {"path", "value" (scaled), "label"}}}`` for every
    ``(qubit-or-pair, fit_key)`` in ``run.fit_results`` that the curated map covers
    AND whose value is a finite number. Diagnostics, non-numeric, pointer-string,
    boolean and NaN values are omitted, so only mappable rows get an Apply button.
    Accepts either a RunInfo object or the dict that ``get_run`` returns.

    ``names`` (docs/296, a ``run_names.RunNames``): the run's names in the
    LOADED chip's names. ``qname`` stays the run's own key (it labels the
    run's fit), the path is spelled in today's names -- a run saved before a
    rename names its qubits by their old ids. A row with no qubit or pair on
    the loaded chip is ``{"refused": <sentence>, "label"}`` instead (no
    ``path``: never a guessed target).
    """
    spec = _match(_attr(run, "experiment_name", "") or "")
    if not spec:
        return {}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    fit_results = _attr(run, "fit_results", None) or {}
    for qname, qres in fit_results.items():
        if not isinstance(qres, dict):
            continue
        rows: dict[str, dict[str, Any]] = {}
        for fit_key, entry in spec.items():
            if fit_key not in qres or not _pushable(qres[fit_key]):
                continue
            # qname is the qubit name (1Q) or the pair name (2Q); fill whichever
            # placeholder the template uses.
            path = entry["path"].replace("{q}", str(qname)).replace("{pair}", str(qname))
            if "{operation}" in path:
                # The node wrote this value into the SPECIFIC operation it
                # calibrated (a node parameter, e.g. cz_flattop). Fill it from
                # the run so we hit the same macro; skip rather than guess when
                # it's missing — a static ``macros.cz`` would mis-route, since
                # ``macros.cz`` is a ``#./cz_unipolar`` alias and the run may
                # have calibrated a different variant.
                op = node_operation(_attr(run, "parameters", None))
                if not op:
                    continue
                path = path.replace("{operation}", str(op))
            if names is not None and not names.identity:
                now = names.target(path)
                if now is None:
                    rows[fit_key] = {"refused": names.refusal(str(qname)),
                                     "label": entry.get("label", fit_key)}
                    continue
                path = now
            scale = entry.get("scale", 1.0)
            rows[fit_key] = {"path": path, "value": qres[fit_key] * scale,
                             "label": entry.get("label", fit_key)}
        if rows:
            out[str(qname)] = rows
    return out


# Fitted values a node does not write as-is but folds into a state field with a
# known rule: (fit key, written attribute, rule). The CZ phase-compensation node
# stores ``(old - fit) % 1`` (see the module docstring). [derived] -- checked
# against a real run's recorded state change (old 0.0, fit 0.5953376949413515,
# written 0.40466230505864853) and pinned by tests/test_fit_written_by_run.py.
_FOLDED_WRITES = {
    "control_phase_correction": ("phase_shift_control", "(old - fit) mod 1"),
    "target_phase_correction": ("phase_shift_target", "(old - fit) mod 1"),
}


def _dot_path(raw: Any) -> str | None:
    """A recorded write's path as a state dot path (``#/a/b`` / ``/quam/a/b``)."""
    if not isinstance(raw, str) or not raw:
        return None
    p = raw
    for head in ("#/", "/quam/", "/"):
        if p.startswith(head):
            p = p[len(head):]
            break
    return p.replace("/", ".") if p else None


def recorded_writes(folder) -> list[dict[str, Any]]:
    """The writes a run's ``node.json`` records: ``[{"path", "old", "new"}]``.

    Two spellings exist: the ``patches`` list (``{"op", "path", "value", "old"}``)
    and ``state_changes.changes`` (``{"key", "old", "new"}``). ``state_updates.json``
    is deliberately not read: it holds proposals, not writes.
    """
    from pathlib import Path

    from quam_state_manager.core import safe_io
    try:
        node = safe_io.read_json(Path(folder) / "node.json")
    except (OSError, ValueError):
        return []
    if not isinstance(node, dict):
        return []
    return [{"path": w["path"], "old": w["old"], "new": w["new"]}
            for w in _patch_writes(node) + _change_writes(node)]


def _ops_writes(entries: Any, path_key: str, new_key: str) -> list[dict[str, Any]]:
    """One recorded write list as ``[{"path", "old", "new", "has_old"}]``."""
    out: list[dict[str, Any]] = []
    for e in entries if isinstance(entries, list) else ():
        if not isinstance(e, dict) or e.get("op", "replace") not in ("replace", "add"):
            continue
        path = _dot_path(e.get(path_key))
        if path:
            out.append({"path": path, "old": e.get("old"), "new": e.get(new_key),
                        "has_old": "old" in e})
    return out


def _patch_writes(node: Any) -> list[dict[str, Any]]:
    """node.json ``patches`` (``{"op", "path", "value", "old"}``)."""
    return _ops_writes(node.get("patches") if isinstance(node, dict) else None,
                       "path", "value")


def _change_writes(node: Any) -> list[dict[str, Any]]:
    """node.json ``state_changes.changes`` (``{"key", "old", "new"}``)."""
    sc = node.get("state_changes") if isinstance(node, dict) else None
    return _ops_writes(sc.get("changes") if isinstance(sc, dict) else None, "key", "new")


def _proposal_writes(doc: Any) -> list[dict[str, Any]]:
    """``state_updates.json`` ``operations`` (``{"op", "path", "value", "old"}``)."""
    return _ops_writes(doc.get("operations") if isinstance(doc, dict) else None,
                       "path", "value")


# The places a run records the values it wrote, in the order they are trusted.
# The first one that holds any write is the run's write list.
RUN_WRITE_SOURCES = ("patches", "state_changes", "state_updates")


def run_writes(folder) -> tuple[list[dict[str, Any]], str | None]:
    """The values one run wrote, from the first source that records any.

    Order: node.json ``patches``, then node.json ``state_changes.changes``, then
    the run's ``state_updates.json`` ``operations``. Returns ``(writes, source)``;
    each write is ``{"path", "old", "new", "has_old"}`` (``path`` a state dot
    path) and ``source`` one of :data:`RUN_WRITE_SOURCES`, or ``([], None)`` when
    no source records a write. ``has_old`` is False when the source did not
    record the value the write replaced: such a write is named, never reverted
    blind. An unreadable file counts as absent.
    """
    from pathlib import Path

    from quam_state_manager.core import safe_io
    folder = Path(folder)
    try:
        node = safe_io.read_json(folder / "node.json")
    except (OSError, ValueError):
        node = None
    for source, writes in (("patches", _patch_writes(node)),
                           ("state_changes", _change_writes(node))):
        if writes:
            return writes, source
    proposals = folder / "state_updates.json"
    if proposals.is_file():
        try:
            writes = _proposal_writes(safe_io.read_json(proposals))
        except (OSError, ValueError):
            writes = []
        if writes:
            return writes, "state_updates"
    return [], None


def _same(a: Any, b: Any) -> bool:
    return _pushable(a) and _pushable(b) and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-15)


def written_by_run(run, names=None) -> dict[str, dict[str, dict[str, Any]]]:
    """Fitted values the run itself recorded writing into the state.

    ``{qname: {fit_key: {"path", "new", "basis"}}}`` for a fit value the run's
    own ``node.json`` records as written: the same number under a path that
    names this entity and this key (``basis`` "same value"), or a value folded
    by the node's known rule (``_FOLDED_WRITES``; ``basis`` names the rule).
    It is what the Results tab says instead of "copy-only" for such a value.
    ``names`` as in :func:`resolve_fit_targets`: a path the loaded chip cannot
    spell is left out rather than guessed.
    """
    folder = _attr(run, "folder_path", None)
    if not folder:
        return {}
    writes = recorded_writes(folder)
    if not writes:
        return {}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for qname, qres in (_attr(run, "fit_results", None) or {}).items():
        if not isinstance(qres, dict):
            continue
        mine = [w for w in writes if str(qname) in w["path"].split(".")]
        rows: dict[str, dict[str, Any]] = {}
        for fit_key, val in qres.items():
            if not _pushable(val):
                continue
            hit = None
            folded = _FOLDED_WRITES.get(fit_key)
            for w in mine:
                segs = w["path"].split(".")
                if fit_key in segs and _same(w["new"], val):
                    hit = (w, "same value")
                    break
                if (folded and segs[-1] == folded[0] and _pushable(w["old"])
                        and _same(w["new"], (w["old"] - val) % 1.0)):
                    hit = (w, folded[1])
                    break
            if hit is None:
                continue
            path = hit[0]["path"]
            if names is not None and not names.identity:
                path = names.target(path)
                if path is None:
                    continue
            rows[fit_key] = {"path": path, "new": hit[0]["new"], "basis": hit[1]}
        if rows:
            out[str(qname)] = rows
    return out
