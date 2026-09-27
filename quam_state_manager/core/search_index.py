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
import threading
from dataclasses import dataclass
from typing import Any

from quam_state_manager.core.loader import natural_key

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
        return cls.from_leaves(_walk_leaves(merged), wiring_keys)

    @classmethod
    def from_leaves(cls, leaves, wiring_keys: set[str]) -> SearchIndex:
        """:meth:`build` over an already-flattened ``(dot_path, value)``
        sequence -- :class:`LazySearchIndex` snapshots the leaves under the
        store lock and builds from the copy with the lock released."""
        index = cls()
        # docs/2xx (RAM P10): one pass, same output as the per-leaf original
        # (pinned list-equal by tests/test_search_index_build_parity.py).
        # A 30-qubit chip has 313k leaves but only ~57k distinct value
        # strings, ~2k leaf keys and ~200 parent ids, so every derived string
        # is computed once per DISTINCT input and the index holds one copy of
        # each (the per-leaf copies were most of its ~10x-the-file RAM).
        entries = index.entries
        p2i = index.path_to_idx
        canon: dict[str, str] = {}            # one shared copy per distinct string
        vmemo: dict[tuple, str] = {}          # (type, value) -> value_str
        # parent_id reads only the first five dot segments (and the category,
        # itself a function of the first one), so those segments are its key
        pmemo: dict[tuple, str] = {}
        for dot_path, value in leaves:
            category = _categorize(dot_path)
            head = dot_path.split(".", 5)
            pkey = tuple(head[:5])
            parent_id = pmemo.get(pkey)
            if parent_id is None:
                parent_id = _extract_parent_id(dot_path, category)
                parent_id = pmemo[pkey] = canon.setdefault(parent_id, parent_id)
            leaf_key = dot_path.rsplit(".", 1)[-1]
            leaf_key = canon.setdefault(leaf_key, leaf_key)
            source = "wiring" if head[0] in wiring_keys else "state"
            if value is None:
                value_str = "none"
            else:
                # keyed by TYPE too (1, 1.0 and True are equal keys with
                # different strings); a float zero is never memoized, since
                # 0.0 == -0.0 yet they print differently.
                if value.__class__ is float and value == 0.0:
                    vkey, value_str = None, None
                else:
                    try:
                        vkey = (value.__class__, value)
                        value_str = vmemo.get(vkey)
                    except TypeError:         # unhashable scalar (never from JSON)
                        vkey, value_str = None, None
                if value_str is None:
                    value_str = str(value).lower()
                    value_str = canon.setdefault(value_str, value_str)
                    if vkey is not None:
                        vmemo[vkey] = value_str
            p2i[dot_path] = len(entries)
            entries.append(IndexEntry(dot_path, value_str, value, category,
                                      parent_id, leaf_key, source))

        _build_inverted_indexes(index)
        _build_prefix_map(index)
        index._trigram_built = False          # deferred — built on first fuzzy search

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

        # The shared grammar (core.search_query / docs 96): space = AND across
        # groups, a standalone `|` = OR within one. Grouping runs BEFORE the
        # MIN_PREFIX drop so the pipe can bind its neighbours; the sub-length
        # drop then applies per term exactly as it always did (a lone literal
        # pipe is itself sub-length, so an inert pipe vanishes silently — the
        # very thing that happened to it before the grammar existed). A query
        # with no operator parses to singleton groups, and the loop below is
        # then today's per-term AND, dead-term early-exit included.
        from quam_state_manager.core.search_query import group_by

        groups = group_by(query.split())
        groups = [[t for t in g if len(t) >= MIN_PREFIX] for g in groups]
        groups = [g for g in groups if g]
        if not groups:
            return []

        self._ensure_trigram()       # lazily build the fuzzy index on first use

        terms: list[str] = []        # every surviving term, original order —
        candidate_indices: set[int] | None = None
        for g in groups:
            g_matches: set[int] = set()
            for term in g:
                g_matches |= self._find_term(term)
            terms.extend(g)
            if not g_matches:        # an empty GROUP kills the query (AND)
                return []
            candidate_indices = (g_matches if candidate_indices is None
                                 else candidate_indices & g_matches)
            if not candidate_indices:
                return []
        assert candidate_indices is not None

        if category:
            cat_set = set(self.category_index.get(category, []))
            candidate_indices = candidate_indices & cat_set

        scored: list[tuple[float, int]] = []
        for idx in candidate_indices:
            score = self._score(idx, terms)
            scored.append((score, idx))

        # Tie-break on the dot path NATURALLY (customer rule 2026-09-09):
        # the score buckets are coarse, so ties are the norm and this key
        # decides both the printed order AND which `limit` results survive.
        # A plain string compare read `weights_imag.1009` before `.101` and
        # `qubits.q10` before `qubits.q2` (measured on a real 10-qubit chip).
        scored.sort(key=lambda x: (-x[0], natural_key(self.entries[x[1]].dot_path)))
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

    # Measured (tracemalloc, 2026-09-26): 483 B per entry on a 313k-leaf
    # chip, 536 B on a 30k-leaf one -- entries, path map, prefix lists and
    # inverted indexes together. An estimate for RAM accounting, not a bound.
    RAM_BYTES_PER_ENTRY = 500

    def ram_bytes(self) -> int:
        return len(self.entries) * self.RAM_BYTES_PER_ENTRY

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


class LazySearchIndex:
    """A :class:`SearchIndex` built on first USE rather than at chip open
    (RAM P10).

    The index serves exactly one surface -- the topbar search -- yet was
    built on every open, re-open after LRU eviction, sync and restore: 3-6 s
    and ~150 MB on a 30-qubit chip (313k leaves), paid by users who never
    typed into the box. This proxy holds nothing until something asks, then
    builds a fresh index from the store's CURRENT content -- a cold compute,
    so there is no older answer it could serve.

    The incremental hooks (``update_entry`` / ``add_entry`` / ``remove_entry``)
    are no-ops until the build: the build reads the already-edited store.
    The build snapshots the leaves under ``store._lock``, builds from the
    copy with the lock released (edits never wait seconds), and installs only
    if ``store.mutation_seq`` did not move meanwhile -- every edit, undo and
    reload bumps it BEFORE calling a hook, so an edit whose hook ran against
    the unbuilt proxy always forces a rebuild. After a few losing races it
    builds holding the lock.
    """

    __slots__ = ("_store", "_wiring_keys", "_index", "_build_lock", "builds", "_want", "__weakref__")

    _OPTIMISTIC_TRIES = 3

    def __init__(self, store: Any, wiring_keys: set[str] | None = None):
        self._store = store
        self._wiring_keys = wiring_keys
        self._index: SearchIndex | None = None
        self._build_lock = threading.Lock()
        self.builds = 0
        self._want = 0      # foreground callers waiting in get(); a paced
                            # (background) build never pauses while > 0

    @property
    def built(self) -> bool:
        return self._index is not None

    def _keys(self) -> set[str]:
        if self._wiring_keys is not None:
            return self._wiring_keys
        return set(self._store.wiring.keys())

    def get(self) -> SearchIndex:
        """The built index, building it now if nobody has yet."""
        idx = self._index
        if idx is not None:
            return idx
        with _WANT_LOCK:
            self._want += 1
        try:
            return self._get(None)
        finally:
            with _WANT_LOCK:
                self._want -= 1

    def _get(self, pace) -> SearchIndex:
        """:meth:`get`'s body. *pace* (background prewarm only) is called
        between chunks of the snapshot build -- never with the store lock
        held -- and may sleep there to yield the GIL to foreground requests."""
        if self._index is not None:
            return self._index
        with self._build_lock:
            if self._index is not None:
                return self._index
            store = self._store
            for _ in range(self._OPTIMISTIC_TRIES):
                with store._lock:
                    token = (store.mutation_seq, id(store.merged))
                    leaves = list(_walk_leaves(store.merged))
                    keys = self._keys()
                built = SearchIndex.from_leaves(
                    leaves if pace is None else _paced(leaves, pace), keys)
                with store._lock:
                    if (store.mutation_seq, id(store.merged)) == token:
                        self._index = built
                        self.builds += 1
                        return built
            with store._lock:
                built = SearchIndex.build(store.merged, wiring_keys=self._keys())
                self._index = built
                self.builds += 1
                return built

    def prewarm(self, pre=None) -> None:
        """Ask the background worker to build this index soon (the most
        recently opened chip wins; see :func:`_prewarm_worker`). Laziness kept
        the open fast but moved a 2.9 s build onto the first keystroke on a
        30-qubit chip (measured); prewarming moves it off both. The build is
        :meth:`get` itself, so the mutation-seq token guards it exactly as a
        foreground build.

        *pre*: other warm steps for the same chip, run by the same worker
        BEFORE the index build, each as ``step(store, pace)`` (the chip's
        pointer cache and lint, for the first Chip Status visit). They run
        even when the index is already built."""
        if self._index is None or pre:
            _prewarm_submit(self, pre)

    # -- the SearchIndex surface ------------------------------------------
    def search(self, query: str, limit: int = 50, category: str | None = None):
        return self.get().search(query, limit=limit, category=category)

    def update_entry(self, dot_path: str, new_value: Any) -> None:
        idx = self._index
        if idx is not None:
            idx.update_entry(dot_path, new_value)

    def add_entry(self, dot_path: str, value: Any, *, source_file: str = "state") -> None:
        idx = self._index
        if idx is not None:
            idx.add_entry(dot_path, value, source_file=source_file)

    def remove_entry(self, dot_path: str) -> None:
        idx = self._index
        if idx is not None:
            idx.remove_entry(dot_path)

    def ram_bytes(self) -> int:
        idx = self._index
        return idx.ram_bytes() if idx is not None else 0

    def __getattr__(self, name: str):
        # entries / prefix_map / stats() ...: anything else reads the real
        # index. Private names never trigger a build (an unset slot during
        # copy/pickle would otherwise recurse through get()).
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.get(), name)

    def __repr__(self) -> str:
        return f"LazySearchIndex(built={self.built})"


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


def _walk_leaves(obj: Any, prefix: str = ""):
    """``loader._walk`` without the path tuples: ``(dot_path, value)`` for
    every leaf, same depth-first order (the index never used the tuples)."""
    if isinstance(obj, dict):
        items = obj.items()
    elif isinstance(obj, list):
        items = enumerate(obj)
    else:
        return
    for key, value in items:
        child = f"{prefix}.{key}" if prefix else f"{key}"
        if isinstance(value, (dict, list)):
            yield from _walk_leaves(value, child)
        else:
            yield child, value


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
    """prefix -> sorted entry indices, one occurrence per (entry, string)
    that has the prefix -- an entry whose value AND leaf key share a prefix
    is listed twice, exactly as the incremental add/remove helpers expect.

    Built per DISTINCT string (value_str / leaf key / parent id) and merged:
    the per-leaf loop did ~7 dict updates for each of 3 strings of every leaf
    (5.5M appends on a 313k-leaf chip); here each distinct string's prefixes
    are cut once. Requires the inverted indexes (key_index / parent_index are
    exactly the leaf-key and parent-id groupings)."""
    by_value: dict[str, list[int]] = {}
    for idx, entry in enumerate(index.entries):
        lst = by_value.get(entry.value_str)
        if lst is None:
            by_value[entry.value_str] = [idx]
        else:
            lst.append(idx)
    runs: dict[str, list[list[int]]] = {}
    for groups in (by_value, index.key_index, index.parent_index):
        for s, idxs in groups.items():
            for prefix in _prefixes(s):
                r = runs.get(prefix)
                if r is None:
                    runs[prefix] = [idxs]
                else:
                    r.append(idxs)
    pm: dict[str, list[int]] = {}
    for prefix, rs in runs.items():
        if len(rs) == 1:
            pm[prefix] = list(rs[0])          # a group is already ascending
        else:
            out: list[int] = []
            for r in rs:
                out.extend(r)
            out.sort()                        # timsort merges the ascending runs
            pm[prefix] = out
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


# ---------------------------------------------------------------- prewarm
# ONE daemon worker, ONE pending slot: a burst of chip switches builds only
# the chip the user ended on (earlier requests are overwritten, and build
# lazily on first search if ever needed). The slot holds a weakref, so a
# chip that left the LRU is never kept alive by a pending prewarm.
import time  # noqa: E402
import weakref as _weakref  # noqa: E402

from quam_state_manager.core import activity  # noqa: E402

_PREWARM_CV = threading.Condition()
_PREWARM_SLOT: list = [None]
_PREWARM_PRE: list = [()]      # the pending submission's pre-build steps
PREWARM_STEPS = [0]            # pre-build steps completed (tests / debugging)
_PREWARM_THREAD: list = [None]
PREWARM_BUILDS = [0]


_WANT_LOCK = threading.Lock()
_PACE_CHUNK = 2048


def _paced(leaves, pace):
    """Yield *leaves*, calling ``pace()`` every :data:`_PACE_CHUNK` items."""
    for i, item in enumerate(leaves):
        if i and not i % _PACE_CHUNK:
            pace()
        yield item


def _make_pace(lazy: "LazySearchIndex"):
    ref = _weakref.ref(lazy)

    def pace() -> None:
        # Pause while a foreground request runs; resume at once when a
        # foreground get() is waiting on this very index (it holds no lock we
        # hold, but it is blocked on our build lock -- pausing would stall it).
        while activity.busy(quiet_s=0.0):
            it = ref()
            if it is None or it._want > 0:
                return
            del it
            time.sleep(0.02)
    return pace


def _prewarm_submit(lazy: "LazySearchIndex", pre=None) -> None:
    with _PREWARM_CV:
        _PREWARM_SLOT[0] = _weakref.ref(lazy)
        _PREWARM_PRE[0] = tuple(pre or ())
        t = _PREWARM_THREAD[0]
        if t is None or not t.is_alive():
            t = threading.Thread(target=_prewarm_worker, name="search-index-prewarm",
                                 daemon=True)
            _PREWARM_THREAD[0] = t
            t.start()
        _PREWARM_CV.notify()


def _prewarm_worker() -> None:
    while True:
        with _PREWARM_CV:
            while _PREWARM_SLOT[0] is None:
                _PREWARM_CV.wait()
            ref, _PREWARM_SLOT[0] = _PREWARM_SLOT[0], None
            pre, _PREWARM_PRE[0] = _PREWARM_PRE[0], ()
        superseded = lambda: _PREWARM_SLOT[0] is not None  # noqa: E731
        # Start only on a quiet server (a newer submission replaces this one
        # while we wait), then pause between chunks whenever a foreground
        # request is running -- unless someone is waiting for THIS index.
        activity.wait_quiet(stop=superseded)
        if superseded():
            continue            # superseded while waiting: take the newer one
        lazy = ref()
        if lazy is None:
            continue
        # The pre-build steps (pointer cache, lint) come first: they are what
        # the first Chip Status visit waits on. Each starts on a quiet server
        # and a newer submission abandons the rest.
        pace = _make_pace(lazy)
        for step in pre:
            activity.wait_quiet(stop=superseded)
            if superseded():
                break
            try:
                step(lazy._store, pace)
                PREWARM_STEPS[0] += 1
            except Exception:  # noqa: BLE001 -- a warm step never raises; readers compute on demand
                logger.debug("chip prewarm step failed", exc_info=True)
        if superseded() or lazy._index is not None:
            continue
        activity.wait_quiet(stop=superseded)
        if superseded():
            continue
        try:
            lazy._get(_make_pace(lazy))
            PREWARM_BUILDS[0] += 1
        except Exception:  # noqa: BLE001 -- a prewarm never raises; search builds on demand
            logger.debug("search index prewarm failed", exc_info=True)
        del lazy
