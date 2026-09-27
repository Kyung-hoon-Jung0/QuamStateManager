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
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quam_state_manager.core.experiment_data import ExperimentContext
from quam_state_manager.core.loader import QuamStore, flatten, natural_key
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
        ignore = ignore_keys if ignore_keys is not None else _DEFAULT_IGNORE
        doc_a = self._merged_side(a)
        doc_b = self._merged_side(b)
        try:
            entries = _tree_diff(doc_a, doc_b, ignore, float_tolerance)
        except _UnsafePaths:
            # A key that makes two different subtrees flatten to the same
            # dot-path (a dotted/empty/non-str key) -- only the flat
            # comparison below knows how the old flatten resolved those.
            entries = None
        if entries is not None:
            entries.sort(key=lambda e: natural_key(e.dot_path))
            return entries
        return self._diff_flat(flatten(doc_a), flatten(doc_b), ignore, float_tolerance)

    @staticmethod
    def _diff_flat(flat_a: dict, flat_b: dict, ignore, float_tolerance: float) -> list[DiffEntry]:
        """The reference algorithm: flatten both sides, compare every leaf.

        ``_tree_diff`` returns exactly this list (pinned by a randomized
        parity test against this function); it only skips subtrees that
        are provably identical instead of flattening them."""
        keys_a = set(flat_a.keys())
        keys_b = set(flat_b.keys())

        entries: list[DiffEntry] = []

        # F7: no per-bucket natural sort -- the final sort below is a total
        # order (natural_key ends in the raw string), so it alone decides.
        for key in keys_b - keys_a:
            if _leaf_key(key) in ignore:
                continue
            entries.append(DiffEntry(
                dot_path=key,
                old_value=None,
                new_value=flat_b[key],
                change_type="added",
            ))

        for key in keys_a - keys_b:
            if _leaf_key(key) in ignore:
                continue
            entries.append(DiffEntry(
                dot_path=key,
                old_value=flat_a[key],
                new_value=None,
                change_type="removed",
            ))

        for key in keys_a & keys_b:
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

        # customer report 2026-09-09: a list index is a NUMBER, so the rows
        # read 1009 · 101 · 1011 under a plain string sort. Every ordered
        # display of paths in SM goes through natural_key (q10 after q2,
        # weights_imag.101 before .1009).
        entries.sort(key=lambda e: natural_key(e.dot_path))
        return entries

    @staticmethod
    def _merged_side(side: Path | str | QuamStore | tuple[dict, dict]) -> dict:
        """The merged document :meth:`_flatten_side` flattens (unflattened)."""
        if isinstance(side, QuamStore):
            return side.merged
        if isinstance(side, tuple) and len(side) == 2:
            state, wiring = side
            if not isinstance(state, dict) or not isinstance(wiring, dict):
                raise TypeError("diff side tuple must be (state_dict, wiring_dict)")
            merged: dict = {**state}
            merged.update(wiring)
            return merged
        return QuamStore(side, validate=False).merged

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
    # N-way differences-only leaf table (docs/128)
    # ------------------------------------------------------------------

    def diff_n(
        self,
        sides: list[Path | str | QuamStore | tuple[dict, dict]],
        *,
        float_tolerance: float = 1e-12,
        ignore_keys: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """One row per dot-path where any two of N sides disagree.

        The versions panel's multi-pick Compare (customer, 2026-08-21):
        N snapshots of the SAME chip, every leaf considered (state + wiring,
        unlike :meth:`multi_diff`, which walks only the curated per-qubit
        properties), and rows where every present side agrees are DROPPED —
        comparison is meaningful in the differences.

        Returns dicts sorted by dot_path::

            {"dot_path": str,
             "values": [leaf-or-None per side, in the callers' side order],
             "present": [bool per side],
             "changed": [bool per side]}

        ``values[i] is None`` with ``present[i] is False`` means the leaf did
        not exist on that side; a real stored null keeps ``present[i]`` True.
        ``changed[i]`` says "this side differs from the side immediately
        before it" (``changed[0]`` is always False) — computed HERE with the
        same ``_values_equal`` the row verdict uses, so no SECOND equality
        rule exists for cells (the docs/118 two-rules trap). One honest
        caveat: the row verdict compares base-vs-each while ``changed``
        compares adjacent pairs, so a sub-tolerance chain (a≈b, b≈c yet
        a≉c at the 1e-12 relative tolerance) can in principle list a row
        whose ``changed`` flags are all False — accepted, the same
        tolerance semantics as :meth:`diff` itself.

        Equality is :meth:`diff`'s float tolerance, with ONE deliberate
        departure: two NaNs are treated as agreeing. ``_values_equal`` says
        NaN != NaN (IEEE), which would list a leaf as "differing" on a
        surface whose whole promise is that listed rows differ, showing the
        reader ``nan`` beside ``nan`` (docs/128 review). docs/118 already
        settled that question for comparison surfaces in
        :func:`compare_equal`; this follows it rather than inventing a third
        answer. ``ignore_keys`` defaults to :meth:`diff`'s ``__class__``
        ignore, but callers that claim completeness to a user should pass
        ``set()`` — a class migration (docs/94) is a real difference and
        hiding it makes "identical content" a lie.
        """
        flats = [self._flatten_side(s) for s in sides]

        def _agree(a: Any, b: Any) -> bool:
            if _values_equal(a, b, float_tolerance):
                return True
            return (isinstance(a, float) and isinstance(b, float)
                    and math.isnan(a) and math.isnan(b))

        ignore = ignore_keys if ignore_keys is not None else _DEFAULT_IGNORE
        all_keys: set[str] = set()
        for flat in flats:
            all_keys.update(flat.keys())
        rows: list[dict[str, Any]] = []
        for key in sorted(all_keys, key=natural_key):
            if _leaf_key(key) in ignore:
                continue
            present = [key in flat for flat in flats]
            values = [flat.get(key) for flat in flats]
            base_i = next(i for i, p in enumerate(present) if p)
            differs = False
            for i in range(len(flats)):
                if present[i] != present[base_i]:
                    differs = True
                    break
                if present[i] and not _agree(values[base_i], values[i]):
                    differs = True
                    break
            if not differs:
                continue
            changed = [False] * len(flats)
            for i in range(1, len(flats)):
                if present[i] != present[i - 1]:
                    changed[i] = True
                elif present[i] and not _agree(values[i - 1], values[i]):
                    changed[i] = True
            rows.append({"dot_path": key, "values": values,
                         "present": present, "changed": changed})
        return rows

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

        target_qubits = (sorted(qubit_filter, key=natural_key) if qubit_filter
                         else sorted(all_qubits, key=natural_key))

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
        include_equal: bool = False,
    ) -> list[dict[str, Any]]:
        """Compare experiment parameters across N runs.

        Returns rows like::

            {"key": "num_shots", "values": [{"label": ..., "value": 100}, ...],
             "same": False}

        Default (``include_equal=False``) keeps the historical
        differences-only behavior (the Trends caller). With
        ``include_equal=True`` (r16 ⑧: the dataset Compare Parameters tab)
        the FULL key union is returned and identical rows carry
        ``same=True`` — the union was always computed here and thrown away.
        """
        all_keys: list[str] = []
        for ctx in contexts:
            for k in ctx.parameters:
                if k not in all_keys:
                    all_keys.append(k)
        all_keys.sort(key=natural_key)

        rows: list[dict[str, Any]] = []
        for key in all_keys:
            values = [
                {"label": label, "value": ctx.parameters.get(key)}
                for ctx, label in zip(contexts, labels)
            ]
            # docs/118: this surface had no tolerance while every sibling did
            differs = _has_difference(values, tolerance=CMP_TOLERANCE)
            if differs or include_equal:
                rows.append({"key": key, "values": values,
                             "same": not differs})
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

        target_qubits = (sorted(qubit_filter, key=natural_key) if qubit_filter
                         else sorted(all_qubits, key=natural_key))
        sorted_props = sorted(all_props, key=natural_key)

        rows: list[dict[str, Any]] = []
        for qubit in target_qubits:
            for prop in sorted_props:
                values = []
                for ctx, label in zip(contexts, labels):
                    qvals = ctx.fit_results.get(qubit, {})
                    values.append({"label": label, "value": qvals.get(prop)})
                if _has_difference(values, tolerance=CMP_TOLERANCE):
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


# docs/118: the tolerance the RUN-comparison surfaces use. Its siblings
# already had one (/chip-compare 1e-9, Differ.diff 1e-12) and this path did
# not, so sub-ppb float noise between two runs was a "difference" here and
# nowhere else.
CMP_TOLERANCE = 1e-9


def compare_equal(a: Any, b: Any, tolerance: float | None = CMP_TOLERANCE) -> bool:
    """Are these two values the SAME for the purpose of a run comparison?

    docs/118: this is the one rule. It used to live in two places that
    disagreed — a server-side row verdict (exact, plus a type check) and a
    template expression (`v.value != ref_val`, exact, no type check) — so a
    row could be listed as a difference with no cell highlighted, and vice
    versa. Both now call this.

    Three deliberate answers:

    * **NaN equals NaN.** `float('nan') != float('nan')` is true in Python, so
      a fit that failed in BOTH runs was reported as a difference — on a
      surface whose header says it shows differences only. Two failures are
      not a change.
    * **int vs float is not a difference.** `100` and `100.0` are the same
      measurement stored differently (docs/76 says the same thing when it
      renders "same numeric value (stored type differs)").
    * **numbers compare with a relative tolerance**, like every sibling
      surface; `tolerance=None` restores exact comparison for callers that
      want it.
    """
    a_num = _is_number(a)
    b_num = _is_number(b)
    if a_num and b_num:
        fa, fb = float(a), float(b)
        if math.isnan(fa) and math.isnan(fb):
            return True                 # both failed — not a change
        if math.isnan(fa) or math.isnan(fb):
            return False
        if tolerance is None:
            return fa == fb
        return abs(fa - fb) <= max(tolerance * max(abs(fa), abs(fb)),
                                   _TOLERANCE_ABS_FLOOR)
    if a_num != b_num:
        return False
    return a == b


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
            if not compare_equal(first, val, tolerance):
                return True
            continue
        # docs/118: two NaNs are never a difference — a fit that failed in BOTH
        # runs is not a change, and `nan != nan` made it one. Everything else on
        # the EXACT path is untouched: `tolerance=None` still means exact,
        # including the int-vs-float type mismatch that /compare's "Exact"
        # preset deliberately surfaces (pinned in test_compare_hub_p0).
        if (_is_number(first) and _is_number(val)
                and math.isnan(float(first)) and math.isnan(float(val))):
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


# ----------------------------------------------------------------------
# The tree diff (w7/livewrite): Differ.diff without flattening what is equal
# ----------------------------------------------------------------------
#
# The reference algorithm flattens BOTH documents (~0.4 s each on a 19 MB
# chip) and natural-sorts every key (~1.4 s) to report the handful of leaves
# that differ. This walks the two documents together instead and skips any
# subtree pair that is provably identical, so the cost follows the size of the
# DIFFERENCE, not of the chip.
#
# "Provably identical" must be stricter than Python's ``==``, which says
# ``1 == 1.0 == True`` and short-circuits on identity (the json decoder hands
# every parsed ``NaN`` out as ONE shared float object, so two NaN leaves compare
# equal inside a container while the leaf rule, ``_values_equal``, reports them
# as modified). So a pair is skipped only when ``==`` holds AND their marshal
# (format 2: no refcount-dependent back-references) bytes agree -- which pins
# every leaf's type and bits and every key's order -- AND those bytes hold no
# float whose exponent is all ones (NaN; +-inf is caught too and merely
# recursed into). Anything else is recursed into and compared leaf by leaf
# with the reference rule, so a false "not identical" costs time, never
# correctness.
#
# Path identity: the reference keys every leaf by its dotted path, so a key
# that contains a dot, is empty, or is not a string can make two different
# subtrees produce the SAME path. Whenever a visited level holds such a key the
# tree walk gives up (``_UnsafePaths``) and the caller runs the reference
# algorithm. (A subtree that is skipped is identical on both sides, so its own
# keys cannot change the outcome -- only a sibling at a visited level could
# reach into its paths.)

import marshal as _marshal
import re as _re

_MARSHAL_V = 2
# TYPE_BINARY_FLOAT ('g', 0x67; 0xe7 with FLAG_REF, unused at format 2) then an
# IEEE-754 little-endian double whose exponent bits are all ones.
_NONFINITE_RE = _re.compile(rb"[\x67\xe7][\x00-\xff]{6}[\xf0-\xff][\x7f\xff]", _re.DOTALL)
# TYPE_BINARY_COMPLEX ('y') never occurs in JSON content; if it ever does the
# pattern above simply does not match it and the ``==`` + bytes test still holds.


class _UnsafePaths(Exception):
    """A visited level has a key that can collide in dotted-path space."""


def _strictly_identical(x: Any, y: Any) -> bool:
    """True only when no leaf under *x*/*y* can differ by ``_values_equal``."""
    try:
        if x != y:
            return False
        mx = _marshal.dumps(x, _MARSHAL_V)
        if mx != _marshal.dumps(y, _MARSHAL_V):
            return False
    except (ValueError, TypeError, RecursionError):
        return False
    return _NONFINITE_RE.search(mx) is None


def _check_keys(d: dict) -> None:
    for k in d:
        if type(k) is not str or not k or "." in k:
            raise _UnsafePaths(k)


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def _flat_under(obj: Any, prefix: str) -> dict:
    """The reference ``flatten`` restricted to one subtree at *prefix*
    (a leaf yields itself; an empty container yields nothing)."""
    if isinstance(obj, (dict, list)):
        from quam_state_manager.core.loader import _walk
        return {p: v for p, v, _t in _walk(obj, prefix)}
    return {prefix: obj}


def _tree_diff(doc_a: dict, doc_b: dict, ignore, tol: float) -> list[DiffEntry]:
    out: list[DiffEntry] = []

    def leaf_pair(path: str, va: Any, vb: Any) -> None:
        if _leaf_key(path) in ignore:
            return
        if not _values_equal(va, vb, tol):
            out.append(DiffEntry(path, va, vb, "modified"))

    def removed(obj: Any, path: str) -> None:
        for p, v in _flat_under(obj, path).items():
            if _leaf_key(p) not in ignore:
                out.append(DiffEntry(p, v, None, "removed"))

    def added(obj: Any, path: str) -> None:
        for p, v in _flat_under(obj, path).items():
            if _leaf_key(p) not in ignore:
                out.append(DiffEntry(p, None, v, "added"))

    def mismatch(va: Any, vb: Any, path: str) -> None:
        # container vs leaf, dict vs list: the reference rule on this node only
        fa, fb = _flat_under(va, path), _flat_under(vb, path)
        for p, v in fb.items():
            if p not in fa and _leaf_key(p) not in ignore:
                out.append(DiffEntry(p, None, v, "added"))
        for p, v in fa.items():
            if p not in fb and _leaf_key(p) not in ignore:
                out.append(DiffEntry(p, v, None, "removed"))
        for p, v in fa.items():
            if p in fb:
                leaf_pair(p, v, fb[p])

    def node(va: Any, vb: Any, path: str) -> None:
        da, la = isinstance(va, dict), isinstance(va, list)
        db, lb = isinstance(vb, dict), isinstance(vb, list)
        if not (da or la) and not (db or lb):
            leaf_pair(path, va, vb)
            return
        if (da and db) or (la and lb):
            if _strictly_identical(va, vb):
                return
            if da:
                walk_dict(va, vb, path)
            else:
                walk_list(va, vb, path)
            return
        mismatch(va, vb, path)

    def walk_dict(a: dict, b: dict, prefix: str) -> None:
        _check_keys(a)
        _check_keys(b)
        for k, va in a.items():
            p = _join(prefix, k)
            if k in b:
                node(va, b[k], p)
            else:
                removed(va, p)
        for k, vb in b.items():
            if k not in a:
                added(vb, _join(prefix, k))

    def walk_list(a: list, b: list, prefix: str) -> None:
        n = min(len(a), len(b))
        for i in range(n):
            node(a[i], b[i], _join(prefix, str(i)))
        for i in range(n, len(a)):
            removed(a[i], _join(prefix, str(i)))
        for i in range(n, len(b)):
            added(b[i], _join(prefix, str(i)))

    if not _strictly_identical(doc_a, doc_b):
        walk_dict(doc_a, doc_b, "")
    return out
