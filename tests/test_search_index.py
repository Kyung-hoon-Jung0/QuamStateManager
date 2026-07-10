"""Tests for quam_state_manager.core.search_index.

Covers index building, single-term search, multi-term AND search,
category filtering, scoring/ranking, incremental updates, and
real-data performance validation.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.search_index import (
    IndexEntry,
    SearchIndex,
    SearchResult,
    _categorize,
    _extract_parent_id,
    _prefixes,
    _trigrams,
    _trigram_lookup,
)

# ---------------------------------------------------------------------------
# Paths to real quam_state folders (skip if not available)
# ---------------------------------------------------------------------------

_EXAMPLECHIP_ROOT = Path(r"<data-root>/qualibration_graphs/superconducting")
SMALL_FOLDER = _EXAMPLECHIP_ROOT / "quam_state"
LARGE_FOLDER = _EXAMPLECHIP_ROOT / "quam_states_arv" / "quam_state_examplechip_variantb"

has_small = SMALL_FOLDER.exists() and (SMALL_FOLDER / "state.json").exists()
has_large = LARGE_FOLDER.exists() and (LARGE_FOLDER / "state.json").exists()

skip_no_small = pytest.mark.skipif(not has_small, reason="Small quam_state not found")
skip_no_large = pytest.mark.skipif(not has_large, reason="Large quam_state not found")


# ---------------------------------------------------------------------------
# Synthetic fixture
# ---------------------------------------------------------------------------

SAMPLE_MERGED = {
    "qubits": {
        "qA1": {
            "f_01": 6255526125.489,
            "anharmonicity": -220000000,
            "T1": 8834,
            "T2ramsey": 1520,
            "xy": {
                "intermediate_frequency": 150000000,
                "opx_output": "#/wiring/qubits/qA1/xy/opx_output",
                "operations": {
                    "x180_DragCosine": {
                        "length": 40,
                        "amplitude": 0.1865,
                        "alpha": 0.6199,
                    },
                    "x90_DragCosine": {
                        "length": 40,
                        "amplitude": 0.09325,
                    },
                },
            },
            "resonator": {
                "f_01": 7200000000,
                "operations": {
                    "readout": {"amplitude": 0.042, "length": 2000},
                },
            },
        },
        "qA2": {
            "f_01": 5800000000,
            "anharmonicity": -210000000,
            "T1": 7500,
            "T2ramsey": 1200,
            "resonator": {
                "f_01": 7100000000,
            },
        },
    },
    "qubit_pairs": {
        "qA1-A2": {
            "macros": {"cz_unipolar": {"flux_pulse_qubit": {"amplitude": 0.25}}},
        },
    },
    "active_qubit_names": ["qA1", "qA2"],
    "wiring": {
        "qubits": {
            "qA1": {"xy": {"opx_output": "MW-FEM/1/2"}, "rr": {"opx_output": "MW-FEM/1/1"}},
        },
    },
    "network": {"host": "10.1.1.18", "cluster_name": "test_cluster"},
    "__class__": "quam_config.my_quam.Quam",
}


@pytest.fixture
def index() -> SearchIndex:
    return SearchIndex.build(SAMPLE_MERGED)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestPrefixes:
    def test_short_string(self):
        assert _prefixes("ab") == ["ab"]

    def test_long_string(self):
        p = _prefixes("amplitude")
        assert p[0] == "am"
        assert p[-1] == "amplitud"
        assert len(p) == 7

    def test_single_char(self):
        assert _prefixes("x") == []


class TestTrigrams:
    def test_basic(self):
        assert _trigrams("abcde") == ["abc", "bcd", "cde"]

    def test_short(self):
        assert _trigrams("ab") == []

    def test_exact_three(self):
        assert _trigrams("abc") == ["abc"]


class TestCategorize:
    @pytest.mark.parametrize("path,expected", [
        ("qubits.qA1.f_01", "qubit"),
        ("qubit_pairs.qA1-A2.macros.cz", "pair"),
        ("twpas.twpaA.pump.amplitude", "twpa"),
        ("ports.mw_outputs.con1.1.1", "port"),
        ("wiring.qubits.qA1.xy.opx_output", "wiring"),
        ("network.host", "network"),
        ("active_qubit_names.0", "config"),
        ("__class__", "config"),
    ])
    def test_categories(self, path, expected):
        assert _categorize(path) == expected


class TestExtractParentId:
    def test_qubit(self):
        assert _extract_parent_id("qubits.qA1.f_01", "qubit") == "qA1"

    def test_pair(self):
        assert _extract_parent_id("qubit_pairs.qA1-A2.macros.cz", "pair") == "qA1-A2"

    def test_config(self):
        assert _extract_parent_id("__class__", "config") == "__class__"


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------


class TestBuild:
    def test_entries_created(self, index):
        assert len(index.entries) > 0

    def test_path_to_idx_consistent(self, index):
        for i, entry in enumerate(index.entries):
            assert index.path_to_idx[entry.dot_path] == i

    def test_prefix_map_populated(self, index):
        assert len(index.prefix_map) > 0

    def test_trigram_index_populated(self, index):
        index._ensure_trigram()        # trigram is built lazily on first search
        assert len(index.trigram_index) > 0

    def test_trigram_index_deferred_until_search(self, index):
        # the expensive trigram index is NOT built eagerly (perf) — empty until
        # the first fuzzy search, then populated and stable.
        assert index._trigram_built is False
        assert len(index.trigram_index) == 0
        index.search("ramsey")
        assert index._trigram_built is True
        assert len(index.trigram_index) > 0

    def test_inverted_indexes_populated(self, index):
        assert len(index.key_index) > 0
        assert len(index.category_index) > 0
        assert len(index.parent_index) > 0

    def test_stats(self, index):
        index._ensure_trigram()        # trigram is built lazily on first search
        s = index.stats()
        assert s["entries"] > 0
        assert s["prefix_map_keys"] > 0
        assert s["trigram_keys"] > 0

    def test_category_qubit_entries(self, index):
        qubit_indices = index.category_index.get("qubit", [])
        assert len(qubit_indices) > 5

    def test_source_file_detection(self, index):
        wiring_entries = [e for e in index.entries if e.source_file == "wiring"]
        state_entries = [e for e in index.entries if e.source_file == "state"]
        assert len(wiring_entries) > 0
        assert len(state_entries) > 0

    def test_prefix_map_sorted(self, index):
        for key, lst in index.prefix_map.items():
            assert lst == sorted(lst), f"prefix_map[{key!r}] not sorted"

    def test_trigram_index_deduped(self, index):
        for key, lst in index.trigram_index.items():
            assert len(lst) == len(set(lst)), f"trigram_index[{key!r}] has duplicates"


# ---------------------------------------------------------------------------
# Single-term search
# ---------------------------------------------------------------------------


class TestSingleTermSearch:
    def test_search_qubit_name(self, index):
        results = index.search("qA1")
        assert len(results) > 0
        assert all(isinstance(r, SearchResult) for r in results)

    def test_search_property_name(self, index):
        results = index.search("amplitude")
        assert len(results) >= 3  # x180, x90, readout, pair

    def test_search_frequency(self, index):
        results = index.search("6255")
        assert len(results) >= 1

    def test_search_T1(self, index):
        results = index.search("T1")
        assert len(results) >= 1
        assert any(r.leaf_key == "T1" for r in results)

    def test_search_case_insensitive(self, index):
        r1 = index.search("qa1")
        r2 = index.search("qA1")
        assert len(r1) == len(r2)

    def test_search_empty(self, index):
        assert index.search("") == []

    def test_search_single_char(self, index):
        assert index.search("x") == []

    def test_search_no_match(self, index):
        assert index.search("zzzzzzzzz") == []

    def test_search_limit(self, index):
        results = index.search("qA1", limit=3)
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# Multi-term AND search
# ---------------------------------------------------------------------------


class TestMultiTermSearch:
    def test_two_terms(self, index):
        results = index.search("qA1 amplitude")
        assert len(results) >= 1
        for r in results:
            path_lower = r.dot_path.lower()
            assert "qa1" in path_lower or "qa1" in r.parent_id.lower()

    def test_qubit_plus_property(self, index):
        results = index.search("qA1 T1")
        assert len(results) >= 1
        assert any(r.leaf_key == "T1" for r in results)

    def test_three_terms(self, index):
        results = index.search("qA1 amplitude 0.1")
        assert len(results) >= 1

    def test_no_intersection(self, index):
        results = index.search("qA1 zzzznotexist")
        assert len(results) == 0

    def test_order_independent(self, index):
        r1 = index.search("qA1 amplitude")
        r2 = index.search("amplitude qA1")
        paths1 = {r.dot_path for r in r1}
        paths2 = {r.dot_path for r in r2}
        assert paths1 == paths2


# ---------------------------------------------------------------------------
# Category filtering
# ---------------------------------------------------------------------------


class TestCategoryFilter:
    def test_filter_qubit(self, index):
        results = index.search("amplitude", category="qubit")
        assert all(r.category == "qubit" for r in results)

    def test_filter_wiring(self, index):
        results = index.search("opx", category="wiring")
        assert all(r.category == "wiring" for r in results)

    def test_filter_no_match(self, index):
        results = index.search("qA1", category="twpa")
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Scoring and ranking
# ---------------------------------------------------------------------------


class TestScoring:
    def test_exact_key_match_ranks_highest(self, index):
        results = index.search("T1")
        assert len(results) >= 1
        assert results[0].leaf_key == "T1"

    def test_exact_parent_match_ranks_high(self, index):
        results = index.search("qA1")
        assert len(results) >= 1
        top_parents = [r.parent_id for r in results[:5]]
        assert "qA1" in top_parents

    def test_multi_term_scores_summed(self, index):
        results = index.search("qA1 T1")
        assert len(results) >= 1
        assert results[0].score > 100


# ---------------------------------------------------------------------------
# Incremental update
# ---------------------------------------------------------------------------


class TestIncrementalUpdate:
    def test_update_changes_value(self, index):
        path = "qubits.qA1.f_01"
        old_results = index.search("6255")
        assert len(old_results) >= 1

        index.update_entry(path, 9999999999)
        new_results = index.search("6255")
        new_paths = {r.dot_path for r in new_results}
        assert path not in new_paths

        nine_results = index.search("9999")
        nine_paths = {r.dot_path for r in nine_results}
        assert path in nine_paths

    def test_update_entry_preserves_others(self, index):
        before_count = len(index.search("amplitude"))
        index.update_entry("qubits.qA1.f_01", 1234567890)
        after_count = len(index.search("amplitude"))
        assert before_count == after_count

    def test_update_nonexistent_path(self, index):
        index.update_entry("nonexistent.path", 42)  # should not crash


# ---------------------------------------------------------------------------
# Trigram lookup
# ---------------------------------------------------------------------------


class TestTrigramLookup:
    def test_direct_lookup(self, index):
        index._ensure_trigram()        # trigram is built lazily on first search
        results = _trigram_lookup(index.trigram_index, "ramsey")
        assert len(results) >= 1
        for idx in results:
            e = index.entries[idx]
            assert "ramsey" in e.value_str or "ramsey" in e.leaf_key.lower() or "ramsey" in e.dot_path.lower()

    def test_no_match(self, index):
        results = _trigram_lookup(index.trigram_index, "xyzxyz")
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Real data tests
# ---------------------------------------------------------------------------


@skip_no_small
class TestSmallRealData:
    def test_build_from_real_store(self):
        store = QuamStore(SMALL_FOLDER)
        idx = SearchIndex.build(store.merged)
        assert len(idx.entries) > 100

    def test_search_qubit_name(self):
        store = QuamStore(SMALL_FOLDER)
        idx = SearchIndex.build(store.merged)
        results = idx.search("q1")
        assert len(results) > 0

    def test_search_property(self):
        store = QuamStore(SMALL_FOLDER)
        idx = SearchIndex.build(store.merged)
        results = idx.search("amplitude")
        assert len(results) > 0


@skip_no_large
class TestLargeRealData:
    def test_build_from_real_store(self):
        store = QuamStore(LARGE_FOLDER)
        idx = SearchIndex.build(store.merged)
        stats = idx.stats()
        assert stats["entries"] > 3000
        assert stats["prefix_map_keys"] > 1000
        assert stats["trigram_keys"] > 1000

    def test_build_performance(self):
        store = QuamStore(LARGE_FOLDER)
        start = time.perf_counter()
        idx = SearchIndex.build(store.merged)
        build_ms = (time.perf_counter() - start) * 1000
        assert build_ms < 500, f"Build took {build_ms:.0f}ms (budget: 500ms)"

    def test_search_performance(self):
        store = QuamStore(LARGE_FOLDER)
        idx = SearchIndex.build(store.merged)
        queries = ["qA1", "T2", "amplitude", "qA1 amplitude", "readout threshold", "6255"]
        for q in queries:
            start = time.perf_counter()
            idx.search(q)
            search_ms = (time.perf_counter() - start) * 1000
            assert search_ms < 50, f"Search '{q}' took {search_ms:.1f}ms (budget: 50ms)"

    def test_search_all_qubit_names(self):
        store = QuamStore(LARGE_FOLDER)
        idx = SearchIndex.build(store.merged)
        for qname in store.qubit_names:
            results = idx.search(qname)
            assert len(results) > 0, f"No results for qubit {qname}"

    def test_multi_term_real(self):
        store = QuamStore(LARGE_FOLDER)
        idx = SearchIndex.build(store.merged)
        results = idx.search("qA1 f_01")
        assert len(results) >= 1
        assert any(r.leaf_key == "f_01" and r.parent_id == "qA1" for r in results)

    def test_wiring_search(self):
        store = QuamStore(LARGE_FOLDER)
        idx = SearchIndex.build(store.merged)
        results = idx.search("opx_output", category="wiring")
        assert len(results) > 0
        assert all(r.source_file == "wiring" for r in results)

    def test_search_by_value(self):
        store = QuamStore(LARGE_FOLDER)
        idx = SearchIndex.build(store.merged)
        results = idx.search("variantb")
        assert len(results) >= 1
