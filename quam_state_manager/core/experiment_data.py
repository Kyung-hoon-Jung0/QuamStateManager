"""Load experiment metadata and results from data.json / node.json.

These files live alongside the ``quam_state/`` directory inside each
experiment run folder.  They carry information that ``QuamStore`` does
not cover: execution parameters, per-qubit fit results, timing, and
outcome status.

The :func:`load_experiment_context` helper is the single entry point.
It returns an :class:`ExperimentContext` that the comparison routes can
use to display structured sections for metadata, parameters, fit
results, and outcomes.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quam_state_manager.core import safe_io

logger = logging.getLogger(__name__)


@dataclass
class ExperimentContext:
    """Structured view of one experiment's data.json + node.json."""

    metadata: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    outcomes: dict[str, str] = field(default_factory=dict)
    fit_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    experiment_name: str = ""
    has_data: bool = False


def load_experiment_context(quam_state_path: str | Path) -> ExperimentContext:
    """Load experiment data from the parent of *quam_state_path*.

    Reads ``node.json`` and ``data.json`` from ``quam_state_path / ..``.
    Returns a populated :class:`ExperimentContext`, or an empty one if
    neither file exists.
    """
    qs = Path(quam_state_path)
    parent = qs.parent if qs.name == "quam_state" else qs

    node_path = parent / "node.json"
    data_path = parent / "data.json"

    ctx = ExperimentContext()

    if node_path.is_file():
        try:
            node = safe_io.read_json(node_path)
        except (OSError, ValueError) as exc:
            logger.warning("Failed to read %s: %s", node_path, exc)
            node = {}

        meta = node.get("metadata", {})
        ctx.metadata = {
            "name": meta.get("name", ""),
            "description": meta.get("description", "").strip(),
            "status": meta.get("status", ""),
            "run_start": meta.get("run_start", ""),
            "run_end": meta.get("run_end", ""),
            "data_path": meta.get("data_path", ""),
        }
        ctx.experiment_name = ctx.metadata["name"]

        data_section = node.get("data", {})
        params_raw = data_section.get("parameters", {})
        params_model = params_raw.get("model", {}) if isinstance(params_raw, dict) else {}
        # Prefer the nested model dict; fall back to the full parameters dict
        ctx.parameters = dict(params_model) if params_model else dict(params_raw) if isinstance(params_raw, dict) else {}

        outcomes = data_section.get("outcomes", {})
        ctx.outcomes = dict(outcomes) if isinstance(outcomes, dict) else {}
        ctx.has_data = True

    if data_path.is_file():
        try:
            data = safe_io.read_json(data_path)
        except (OSError, ValueError) as exc:
            logger.warning("Failed to read %s: %s", data_path, exc)
            data = {}

        raw_fit = data.get("fit_results", {})
        if isinstance(raw_fit, dict):
            ctx.fit_results = {
                qname: _sanitize_fit(qvals)
                for qname, qvals in raw_fit.items()
                if isinstance(qvals, dict)
            }
        ctx.has_data = True

    return ctx


def _sanitize_fit(vals: dict[str, Any]) -> dict[str, Any]:
    """Sanitize fit-result values for safe display and JSON serialisation.

    - NaN / Inf floats become ``None``.
    - Strings starting with ``"./"`` are treated as relative-path
      references (e.g. an HDF5 dataset path embedded in data.json); they
      become ``None`` rather than being silently dropped, so the display
      layer can render them as "–" instead of looking like the key never
      existed (red-team Phase 2 finding §3.6).
    """
    out: dict[str, Any] = {}
    for k, v in vals.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            out[k] = None
        elif isinstance(v, str) and v.startswith("./"):
            logger.debug("fit result %r is a relative-path reference (%s); displaying as null", k, v)
            out[k] = None
        else:
            out[k] = v
    return out
