"""Real-time search index for QUAM state data.

Flattens the merged JSON tree into a list of IndexEntry objects, then builds
three lookup structures for sub-millisecond search:

  1. **Bounded prefix map** -- prefixes of length 2..8 for fast typeahead.
  2. **Trigram index** -- all 3-char substrings for arbitrary substring matching.
  3. **Inverted indexes** -- by leaf_key, category, and parent_id.

Supports multi-term AND queries: ``"qA1 amplitude"`` splits into two terms,
each searched independently, then the result sets are intersected.
"""

from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass
from typing import Any

from quam_state_manager.core.loader import _walk

logger = logging.getLogger(__name__)

MIN_PREFIX = 2
MAX_PREFIX = 8


@dataclass(slots=True)
class IndexEntry:
    """One indexed leaf value from the merged JSON tree."""

    dot_path: str
    value_str: str
    raw_value: Any
    category: str
    parent_id: str
    leaf_key: str
    source_file: str


@dataclass(slots=True)
class SearchResult:
    """A single search hit returned to the UI."""

    dot_path: str
    value_str: str
    raw_value: Any
    category: str
    parent_id: str
    leaf_key: str
    source_file: str
    score: float
    matched_terms: list[str]


class SearchIndex:
    """In-memory search index over a flattened QUAM state dict.

    Build once at load time via :meth:`build`, then query with :meth:`search`.
    Incremental updates via :meth:`update_entry` after value modifications.
    """

    __slots__ = (
        "category_index",
        "entries",
        "key_index",
        "parent_index",
        "path_to_idx",
        "prefix_map",
        "trigram_index",
        "_trigram_built",
    )

    def __init__(self) -> None:
        self.entries: list[IndexEntry] = []
        self.path_to_idx: dict[str, int] = {}
        self.prefix_map: dict[str, list[int]] = {}
        self.trigram_index: dict[str, list[int]] = {}
        # The trigram index (the fuzzy-search fallback) is the single most
        # expensive structure to build (~135 ms on a 21-qubit chip), yet only the
        # rarer substring/typo searches touch it — the per-keystroke prefix search
        # never does. So it is built LAZILY on the first search that needs it, not
        # eagerly in build(); incremental edits are skipped until then (the lazy
        # build reads the already-updated entries). This keeps load / sync /
        # reconcile fast. True = up to date (an empty index is trivially built).
        self._trigram_built: bool = True
        self.key_index: dict[str, list[int]] = {}
        self.category_index: dict[str, list[int]] = {}
        self.parent_index: dict[str, list[int]] = {}

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    @classmethod
    def build(cls, merged: dict, wiring_keys: set[str] | None = None) -> SearchIndex:
        """Flatten *merged* dict and build all index structures.

        Args:
            merged: The combined state+wiring dict from QuamStore.
            wiring_keys: Top-level keys that come from wiring.json
                (default: ``{"wiring", "network"}``).
        """
        if wiring_keys is None:
            wiring_keys = {"wiring", "network"}

        index = cls()
        leaves = _walk(merged)

        for dot_path, value, _path_tuple in leaves:
            category = _categorize(dot_path)
            parent_id = _extract_parent_id(dot_path, category)
            leaf_key = dot_path.rsplit(".", 1)[-1]
            top_key = dot_path.split(".", 1)[0]
            source = "wiring" if top_key in wiring_keys else "state"
            value_str = str(value).lower() if value is not None else "none"

            entry = IndexEntry(
                dot_path=dot_path,
                value_str=value_str,
                raw_value=value,
                category=category,
                parent_id=parent_id,
                leaf_key=leaf_key,
                source_file=source,
            )
            idx = len(index.entries)
            index.entries.append(entry)
            index.path_to_idx[dot_path] = idx

        _build_prefix_map(index)
        index._trigram_built = False          # deferred — built on first fuzzy search
        _build_inverted_indexes(index)

        logger.info(
            "Search index built: %d entries, %d prefix keys, %d trigram keys",
            len(index.entries),
            len(index.prefix_map),
            len(index.trigram_index),
        )
        return index

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 50, category: str | None = None) -> list[SearchResult]:
        """Multi-term AND search.  Called on every keystroke via HTMX.

        Args:
            query: User input (e.g. ``"qA1 amplitude"``).
            limit: Maximum results.
            category: Optional category filter (``"qubit"``, ``"pair"``, etc.).

        Returns:
            Scored and ranked list of :class:`SearchResult`.
        """
        query = query.strip().lower()
        if not query:
            return []

        terms = query.split()
        terms = [t for t in terms if len(t) >= MIN_PREFIX]
        if not terms:
            return []

        self._ensure_trigram()       # lazily build the fuzzy index on first use

        term_match_sets: list[set[int]] = []
        for term in terms:
            matches = self._find_term(term)
            if not matches:
                return []
            term_match_sets.append(matches)

        if len(term_match_sets) == 1:
            candidate_indices = term_match_sets[0]
        else:
            candidate_indices = term_match_sets[0]
            for s in term_match_sets[1:]:
                candidate_indices = candidate_indices & s
                if not candidate_indices:
                    return []

        if category:
            cat_set = set(self.category_index.get(category, []))
            candidate_indices = candidate_indices & cat_set

        scored: list[tuple[float, int]] = []
        for idx in candidate_indices:
            score = self._score(idx, terms)
            scored.append((score, idx))

        scored.sort(key=lambda x: (-x[0], self.entries[x[1]].dot_path))
        results: list[SearchResult] = []
        for score, idx in scored[:limit]:
            e = self.entries[idx]
            results.append(SearchResult(
                dot_path=e.dot_path,
                value_str=e.value_str,
                raw_value=e.raw_value,
                category=e.category,
                parent_id=e.parent_id,
                leaf_key=e.leaf_key,
                source_file=e.source_file,
                score=score,
                matched_terms=terms,
            ))
        return results

    def _find_term(self, term: str) -> set[int]:
        """Find all entry indices matching a single search term."""
        results: set[int] = set()

        if len(term) <= MAX_PREFIX:
            prefix_hits = self.prefix_map.get(term)
            if prefix_hits:
                results.update(prefix_hits)

        if len(term) >= 3:
            trigram_hits = _trigram_lookup(self.trigram_index, term)
            results.update(trigram_hits)

        return results

    def _score(self, idx: int, terms: list[str]) -> float:
        """Score an entry against search terms. Higher = better match."""
        e = self.entries[idx]
        total = 0.0
        lk = e.leaf_key.lower()
        pid = e.parent_id.lower()

        for term in terms:
            if lk == term:
                total += 100
            elif pid == term:
                total += 90
            elif lk.startswith(term):
                total += 70
            elif pid.startswith(term):
                total += 60
            elif term in e.value_str:
                total += 40
            elif term in e.dot_path.lower():
                total += 20
            else:
                total += 10

        return total

    # ------------------------------------------------------------------
    # Incremental update (called by modifier.py after set_value)
    # ------------------------------------------------------------------

    def _ensure_trigram(self) -> None:
        """Build the deferred trigram index from the current entries, once."""
        if not self._trigram_built:
            _build_trigram_index(self)
            self._trigram_built = True

    def update_entry(self, dot_path: str, new_value: Any) -> None:
        """Update a single entry's value in-place and rebuild its index keys.

        O(1) per call -- no full rebuild needed.
        """
        idx = self.path_to_idx.get(dot_path)
        if idx is None:
            logger.warning("update_entry: dot_path %r not found in index", dot_path)
            return

        entry = self.entries[idx]
        old_value_str = entry.value_str

        new_value_str = str(new_value).lower() if new_value is not None else "none"
        entry.raw_value = new_value
        entry.value_str = new_value_str

        _remove_from_prefix_map(self.prefix_map, old_value_str, idx)
        _add_to_prefix_map(self.prefix_map, new_value_str, idx)

        if self._trigram_built:   # else: the lazy build will read the updated entry
            _remove_from_trigram_index(self.trigram_index, old_value_str, idx)
            _add_to_trigram_index(self.trigram_index, new_value_str, idx)

    # ------------------------------------------------------------------
    # Incremental add / remove (called by modifier.py after create / delete)
    # ------------------------------------------------------------------

    def add_entry(self, dot_path: str, value: Any, *, source_file: str = "state") -> None:
        """Register a newly-created leaf in the index.

        If *dot_path* is already indexed, falls back to :meth:`update_entry`.
        """
        if dot_path in self.path_to_idx:
            logger.debug("add_entry: %r already indexed, deferring to update_entry", dot_path)
            self.update_entry(dot_path, value)
            return

        category = _categorize(dot_path)
        parent_id = _extract_parent_id(dot_path, category)
        leaf_key = dot_path.rsplit(".", 1)[-1]
        value_str = str(value).lower() if value is not None else "none"

        entry = IndexEntry(
            dot_path=dot_path,
            value_str=value_str,
            raw_value=value,
            category=category,
            parent_id=parent_id,
            leaf_key=leaf_key,
            source_file=source_file,
        )
        idx = len(self.entries)
        self.entries.append(entry)
        self.path_to_idx[dot_path] = idx

        # Mirror _build_prefix_map: index value_str AND leaf_key/parent_id prefixes,
        # else a freshly-created leaf isn't findable by its key/qubit via a short
        # prefix query (and remove_entry's matching strip would be asymmetric).
        for s in (value_str, leaf_key.lower(), parent_id.lower()):
            _add_to_prefix_map(self.prefix_map, s, idx)
        if self._trigram_built:   # else: deferred build reads this new entry
            _add_to_trigram_index(self.trigram_index, value_str, idx)
            for s in (leaf_key.lower(), parent_id.lower()):
                _add_to_trigram_index(self.trigram_index, s, idx)

        self.key_index.setdefault(leaf_key.lower(), []).append(idx)
        self.category_index.setdefault(category, []).append(idx)
        self.parent_index.setdefault(parent_id.lower(), []).append(idx)

    def remove_entry(self, dot_path: str) -> None:
        """Remove a leaf from the index (used after deletion via undo).

        The entry slot in ``entries`` is left in place (we never compact),
        but it is unreachable from every lookup structure.
        """
        idx = self.path_to_idx.pop(dot_path, None)
        if idx is None:
            return
        entry = self.entries[idx]

        # Strip value_str AND leaf_key/parent_id prefixes (add_entry/_build_prefix_map
        # index all three) — else a deleted/renamed leaf keeps surfacing via a short
        # prefix of its key or qubit name (prefix hits are UNIONed with trigram hits).
        for s in (entry.value_str, entry.leaf_key.lower(), entry.parent_id.lower()):
            _remove_from_prefix_map(self.prefix_map, s, idx)
        if self._trigram_built:   # else: deferred build skips this removed slot
            _remove_from_trigram_index(self.trigram_index, entry.value_str, idx)
            for s in (entry.leaf_key.lower(), entry.parent_id.lower()):
                _remove_from_trigram_index(self.trigram_index, s, idx)

        for inv, key in (
            (self.key_index, entry.leaf_key.lower()),
            (self.category_index, entry.category),
            (self.parent_index, entry.parent_id.lower()),
        ):
            lst = inv.get(key)
            if lst is not None and idx in lst:
                lst.remove(idx)
                if not lst:
                    del inv[key]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Return summary statistics about the index."""
        return {
            "entries": len(self.entries),
            "prefix_map_keys": len(self.prefix_map),
            "trigram_keys": len(self.trigram_index),
            "unique_leaf_keys": len(self.key_index),
            "categories": len(self.category_index),
            "parent_ids": len(self.parent_index),
        }

    def __repr__(self) -> str:
        return f"SearchIndex(entries={len(self.entries)})"


# ======================================================================
# Category and parent_id extraction
# ======================================================================

_CATEGORY_PREFIXES = [
    ("qubits.", "qubit"),
    ("qubit_pairs.", "pair"),
    ("twpas.", "twpa"),
    ("ports.", "port"),
    ("wiring.", "wiring"),
    ("network.", "network"),
]


def _categorize(dot_path: str) -> str:
    for prefix, cat in _CATEGORY_PREFIXES:
        if dot_path.startswith(prefix):
            return cat
    return "config"


def _extract_parent_id(dot_path: str, category: str) -> str:
    parts = dot_path.split(".")
    if category in ("qubit", "pair", "twpa") and len(parts) >= 2:
        return parts[1]
    if category == "port" and len(parts) >= 5:
        return "/".join(parts[1:5])
    if category == "wiring" and len(parts) >= 3:
        return parts[2] if parts[1] in ("qubits", "qubit_pairs", "twpas") and len(parts) >= 4 else parts[1]
    return parts[0]


# ======================================================================
# Prefix map
# ======================================================================


def _prefixes(s: str) -> list[str]:
    """Generate bounded prefixes (length MIN_PREFIX..MAX_PREFIX) from a string."""
    n = min(len(s), MAX_PREFIX)
    return [s[:i] for i in range(MIN_PREFIX, n + 1)]


def _build_prefix_map(index: SearchIndex) -> None:
    pm: dict[str, list[int]] = {}
    for idx, entry in enumerate(index.entries):
        for s in (entry.value_str, entry.leaf_key.lower(), entry.parent_id.lower()):
            for prefix in _prefixes(s):
                if prefix not in pm:
                    pm[prefix] = []
                pm[prefix].append(idx)
    for key in pm:
        pm[key].sort()
    index.prefix_map = pm


def _add_to_prefix_map(pm: dict[str, list[int]], value_str: str, idx: int) -> None:
    for prefix in _prefixes(value_str):
        lst = pm.get(prefix)
        if lst is None:
            pm[prefix] = [idx]
        else:
            pos = bisect.bisect_left(lst, idx)
            if pos >= len(lst) or lst[pos] != idx:
                lst.insert(pos, idx)


def _remove_from_prefix_map(pm: dict[str, list[int]], value_str: str, idx: int) -> None:
    for prefix in _prefixes(value_str):
        lst = pm.get(prefix)
        if lst is not None:
            pos = bisect.bisect_left(lst, idx)
            if pos < len(lst) and lst[pos] == idx:
                lst.pop(pos)


# ======================================================================
# Trigram index
# ======================================================================


def _trigrams(s: str) -> list[str]:
    """Extract all 3-character substrings from a string."""
    if len(s) < 3:
        return []
    return [s[i:i + 3] for i in range(len(s) - 2)]


def _build_trigram_index(index: SearchIndex) -> None:
    ti: dict[str, list[int]] = {}
    p2i = index.path_to_idx
    for idx, entry in enumerate(index.entries):
        # Skip slots left behind by remove_entry (which leaves the entry in
        # `entries` but pops it from path_to_idx) — otherwise a deferred rebuild
        # after a deletion would re-index the removed leaf. A no-op for a fresh
        # build (every entry is reachable).
        if p2i.get(entry.dot_path) != idx:
            continue
        for s in (entry.value_str, entry.leaf_key.lower(), entry.parent_id.lower()):
            for tri in _trigrams(s):
                if tri not in ti:
                    ti[tri] = []
                ti[tri].append(idx)
    for key in ti:
        ti[key].sort()
    # deduplicate sorted lists (same idx may appear multiple times)
    for key in ti:
        lst = ti[key]
        if len(lst) > 1:
            ti[key] = _dedup_sorted(lst)
    index.trigram_index = ti


def _dedup_sorted(lst: list[int]) -> list[int]:
    """Remove consecutive duplicates from a sorted list."""
    result = [lst[0]]
    for i in range(1, len(lst)):
        if lst[i] != lst[i - 1]:
            result.append(lst[i])
    return result


def _trigram_lookup(ti: dict[str, list[int]], term: str) -> set[int]:
    """Find entries matching all trigrams in *term* (AND intersection)."""
    tris = _trigrams(term)
    if not tris:
        return set()

    lists = []
    for tri in tris:
        lst = ti.get(tri)
        if lst is None:
            return set()
        lists.append(lst)

    lists.sort(key=len)
    result = set(lists[0])
    for lst in lists[1:]:
        result &= set(lst)
        if not result:
            return set()
    return result


def _add_to_trigram_index(ti: dict[str, list[int]], value_str: str, idx: int) -> None:
    for tri in _trigrams(value_str):
        lst = ti.get(tri)
        if lst is None:
            ti[tri] = [idx]
        else:
            pos = bisect.bisect_left(lst, idx)
            if pos >= len(lst) or lst[pos] != idx:
                lst.insert(pos, idx)


def _remove_from_trigram_index(ti: dict[str, list[int]], value_str: str, idx: int) -> None:
    for tri in _trigrams(value_str):
        lst = ti.get(tri)
        if lst is not None:
            pos = bisect.bisect_left(lst, idx)
            if pos < len(lst) and lst[pos] == idx:
                lst.pop(pos)


# ======================================================================
# Inverted indexes
# ======================================================================


def _build_inverted_indexes(index: SearchIndex) -> None:
    ki: dict[str, list[int]] = {}
    ci: dict[str, list[int]] = {}
    pi: dict[str, list[int]] = {}

    for idx, entry in enumerate(index.entries):
        lk = entry.leaf_key.lower()
        ki.setdefault(lk, []).append(idx)
        ci.setdefault(entry.category, []).append(idx)
        pi.setdefault(entry.parent_id.lower(), []).append(idx)

    index.key_index = ki
    index.category_index = ci
    index.parent_index = pi
