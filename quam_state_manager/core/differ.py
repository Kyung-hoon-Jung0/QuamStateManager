"""Compare two or more QUAM state snapshots.

Provides:
  - ``Differ.diff()``  -- 2-way structured diff between two quam_state folders
  - ``Differ.multi_compare()`` -- extract a property across N QuamStores for
    time-series trend plotting
  - ``Differ.compare_parameters()`` -- compare experiment parameters across N runs
  - ``Differ.compare_fit_results()`` -- compare per-qubit fit results across N runs
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quam_state_manager.core.experiment_data import ExperimentContext
from quam_state_manager.core.loader import QuamStore, flatten
from quam_state_manager.core.query import QueryEngine

logger = logging.getLogger(__name__)

_DEFAULT_IGNORE = {"__class__"}


@dataclass(slots=True)
class DiffEntry:
    """One difference between two quam_state snapshots."""

    dot_path: str
    old_value: Any  # from state A
    new_value: Any  # from state B
    change_type: str  # "added" | "removed" | "modified"


class Differ:
    """Compare QUAM state snapshots."""

    # ------------------------------------------------------------------
    # 2-way diff
    # ------------------------------------------------------------------

    def diff(
        self,
        a: Path | str | QuamStore | tuple[dict, dict],
        b: Path | str | QuamStore | tuple[dict, dict],
        *,
        float_tolerance: float = 1e-12,
        ignore_keys: set[str] | None = None,
    ) -> list[DiffEntry]:
        """Compute a structured diff between two quam_state snapshots.

        Args:
            a, b: One of:

                - Path to a ``quam_state/`` folder (loaded via QuamStore).
                - A pre-loaded :class:`QuamStore`.
                - A ``(state_dict, wiring_dict)`` tuple — for callers that
                  already have the merged content in memory and want to
                  skip the tmp-dir-and-disk round trip (red-team Phase 2
                  finding §5.2; used by ``state_review`` to diff the live
                  state against the working copy without writing to disk).
            float_tolerance: Relative tolerance for float comparisons.
                ``abs(x - y) / max(abs(x), abs(y), 1e-300) < tol`` -> equal.
            ignore_keys: Leaf key names to skip (default: ``{"__class__"}``).

        Returns:
            Sorted list of DiffEntry (by dot_path).
        """
        flat_a = self._flatten_side(a)
        flat_b = self._flatten_side(b)

        ignore = ignore_keys if ignore_keys is not None else _DEFAULT_IGNORE

        keys_a = set(flat_a.keys())
        keys_b = set(flat_b.keys())

        entries: list[DiffEntry] = []

        for key in sorted(keys_b - keys_a):
            if _leaf_key(key) in ignore:
                continue
            entries.append(DiffEntry(
                dot_path=key,
                old_value=None,
                new_value=flat_b[key],
                change_type="added",
            ))

        for key in sorted(keys_a - keys_b):
            if _leaf_key(key) in ignore:
                continue
            entries.append(DiffEntry(
                dot_path=key,
                old_value=flat_a[key],
                new_value=None,
                change_type="removed",
            ))

        for key in sorted(keys_a & keys_b):
            if _leaf_key(key) in ignore:
                continue
            val_a = flat_a[key]
            val_b = flat_b[key]
            if _values_equal(val_a, val_b, float_tolerance):
                continue
            entries.append(DiffEntry(
                dot_path=key,
                old_value=val_a,
                new_value=val_b,
                change_type="modified",
            ))

        entries.sort(key=lambda e: e.dot_path)
        return entries

    @staticmethod
    def _flatten_side(
        side: Path | str | QuamStore | tuple[dict, dict],
    ) -> dict[str, Any]:
        """Coerce one side of a diff into the flat ``{dot_path: leaf}`` map.

        Accepts a folder path / QuamStore / ``(state, wiring)`` tuple. The
        tuple variant is the cheap path: no QuamStore construction, no
        disk I/O — the caller has already loaded the dicts.
        """
        if isinstance(side, QuamStore):
            return flatten(side.merged)
        if isinstance(side, tuple) and len(side) == 2:
            state, wiring = side
            if not isinstance(state, dict) or not isinstance(wiring, dict):
                raise TypeError("diff side tuple must be (state_dict, wiring_dict)")
            # Same merge rule as QuamStore._merge: wiring shadows state on
            # the rare key collision.
            merged: dict = {**state}
            merged.update(wiring)
            return flatten(merged)
        return flatten(QuamStore(side, validate=False).merged)

    # ------------------------------------------------------------------
    # N-way multi-compare (trend extraction)
    # ------------------------------------------------------------------

    def multi_compare(
        self,
        stores: list[QuamStore],
        labels: list[str],
        properties: list[str],
        *,
        qubit_filter: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Extract property values across N snapshots for trend analysis.

        Args:
            stores: List of QuamStore objects (loaded lazily by Workspace).
            labels: Human-readable label per store (e.g. ``"#34 qubit_spectroscopy 17:13"``).
            properties: Flat property keys from ``QueryEngine.get_qubit()``
                (e.g. ``["f_01", "T2ramsey"]``).
            qubit_filter: If given, only include these qubit IDs.

        Returns:
            List of dicts, one per (qubit, property) combination::

                {
                    "qubit": "qA1",
                    "property": "f_01",
                    "values": [
                        {"label": "#34 ...", "value": 6.255e9},
                        {"label": "#45 ...", "value": 6.256e9},
                    ]
                }

            Directly plottable by Plotly as time-series line charts.
        """
        if len(stores) != len(labels):
            raise ValueError(f"stores ({len(stores)}) and labels ({len(labels)}) must have same length")

        all_qubits: set[str] = set()
        engines: list[QueryEngine] = []
        qubit_dicts: list[dict[str, dict[str, Any]]] = []

        for store in stores:
            eng = QueryEngine(store)
            engines.append(eng)

            qd: dict[str, dict[str, Any]] = {}
            for name in store.qubit_names:
                try:
                    qd[name] = eng.get_qubit(name)
                except Exception:
                    continue
                all_qubits.add(name)
            qubit_dicts.append(qd)

        target_qubits = sorted(qubit_filter) if qubit_filter else sorted(all_qubits)

        results: list[dict[str, Any]] = []
        for qubit in target_qubits:
            for prop in properties:
                values: list[dict[str, Any]] = []
                for i, label in enumerate(labels):
                    qd = qubit_dicts[i]
                    q = qd.get(qubit)
                    val = q.get(prop) if q else None
                    values.append({"label": label, "value": val})
                results.append({
                    "qubit": qubit,
                    "property": prop,
                    "values": values,
                })

        return results

    # ------------------------------------------------------------------
    # N-way multi-diff (differences only)
    # ------------------------------------------------------------------

    def multi_diff(
        self,
        stores: list[QuamStore],
        labels: list[str],
        properties: list[str],
        *,
        qubit_filter: list[str] | None = None,
        tolerance: float | None = None,
    ) -> list[dict[str, Any]]:
        """Like ``multi_compare`` but returns only rows where values differ.

        A row is kept when at least one value in the row differs from any
        other value (ignoring ``None``).  Rows where all stores agree (or
        all are ``None``) are dropped.

        Args:
            tolerance: When given, numeric values compare with this
                *relative* tolerance (plus a tiny absolute floor, matching
                :meth:`diff`'s spirit) and int-vs-float alone is NOT a
                difference (``40`` vs ``40.0`` agree). ``None`` (default)
                keeps the historical exact comparison.
        """
        all_rows = self.multi_compare(
            stores, labels, properties, qubit_filter=qubit_filter,
        )
        return [row for row in all_rows
                if _has_difference(row["values"], tolerance=tolerance)]

    # ------------------------------------------------------------------
    # Experiment parameter comparison
    # ------------------------------------------------------------------

    @staticmethod
    def compare_parameters(
        contexts: list[ExperimentContext],
        labels: list[str],
    ) -> list[dict[str, Any]]:
        """Compare experiment parameters across N runs (differences only).

        Returns rows like::

            {"key": "num_shots", "values": [{"label": ..., "value": 100}, ...]}
        """
        all_keys: list[str] = []
        for ctx in contexts:
            for k in ctx.parameters:
                if k not in all_keys:
                    all_keys.append(k)
        all_keys.sort()

        rows: list[dict[str, Any]] = []
        for key in all_keys:
            values = [
                {"label": label, "value": ctx.parameters.get(key)}
                for ctx, label in zip(contexts, labels)
            ]
            if _has_difference(values):
                rows.append({"key": key, "values": values})
        return rows

    # ------------------------------------------------------------------
    # Experiment fit-result comparison
    # ------------------------------------------------------------------

    @staticmethod
    def compare_fit_results(
        contexts: list[ExperimentContext],
        labels: list[str],
        *,
        qubit_filter: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Compare per-qubit fit results across N runs (differences only).

        Returns rows like::

            {"qubit": "qC1", "property": "frequency",
             "values": [{"label": ..., "value": 7.04e9}, ...]}
        """
        all_qubits: set[str] = set()
        all_props: set[str] = set()
        for ctx in contexts:
            for qname, qvals in ctx.fit_results.items():
                all_qubits.add(qname)
                all_props.update(qvals.keys())

        target_qubits = sorted(qubit_filter) if qubit_filter else sorted(all_qubits)
        sorted_props = sorted(all_props)

        rows: list[dict[str, Any]] = []
        for qubit in target_qubits:
            for prop in sorted_props:
                values = []
                for ctx, label in zip(contexts, labels):
                    qvals = ctx.fit_results.get(qubit, {})
                    values.append({"label": label, "value": qvals.get(prop)})
                if _has_difference(values):
                    rows.append({"qubit": qubit, "property": prop, "values": values})
        return rows

    # ------------------------------------------------------------------
    # Convenience: diff summary stats
    # ------------------------------------------------------------------

    @staticmethod
    def summary(entries: list[DiffEntry]) -> dict[str, int]:
        """Return counts by change_type."""
        counts = {"added": 0, "removed": 0, "modified": 0, "total": len(entries)}
        for e in entries:
            counts[e.change_type] = counts.get(e.change_type, 0) + 1
        return counts


# ======================================================================
# Internal helpers
# ======================================================================


def _leaf_key(dot_path: str) -> str:
    """Extract the last segment of a dot-separated path."""
    return dot_path.rsplit(".", 1)[-1]


# Absolute floor for tolerant numeric comparison: values within this of each
# other are always "equal" regardless of relative tolerance (guards the
# near-zero case where a relative test degenerates).
_TOLERANCE_ABS_FLOOR = 1e-12


def _is_number(x: Any) -> bool:
    """True for int/float but NOT bool (bool is an int subclass)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _has_difference(values: list[dict[str, Any]],
                    *, tolerance: float | None = None) -> bool:
    """Return True if at least one value in the list differs from the others.

    With ``tolerance`` set, two numeric values are equal when
    ``|a - b| <= max(tolerance * max(|a|, |b|), _TOLERANCE_ABS_FLOOR)`` —
    int-vs-float type mismatch alone is not a difference. Non-numeric values
    (and ``tolerance=None``) keep the historical exact comparison.
    """
    concrete = [v["value"] for v in values if v["value"] is not None]
    if not concrete:
        return False
    first = concrete[0]
    for val in concrete[1:]:
        if tolerance is not None and _is_number(first) and _is_number(val):
            a, b = float(first), float(val)
            if abs(a - b) > max(tolerance * max(abs(a), abs(b)),
                                _TOLERANCE_ABS_FLOOR):
                return True
            continue
        if type(first) is not type(val):
            return True
        if isinstance(first, float) and isinstance(val, float):
            if first != val:
                return True
        elif first != val:
            return True
    if len(concrete) != len(values):
        return True
    return False


def _values_equal(a: Any, b: Any, float_tolerance: float) -> bool:
    """Compare two values, applying float tolerance for numeric types."""
    if type(a) is not type(b):
        return False

    if isinstance(a, float) and isinstance(b, float):
        if a == b:
            return True
        denom = max(abs(a), abs(b), 1e-300)
        return abs(a - b) / denom < float_tolerance

    return a == b
