"""Tests for quam_state_manager.core.pointer_resolver.

Covers all three pointer flavors (#/, #../, #./), cache behavior,
cycle detection, and edge cases drawn from real QUAM state data.
"""

from __future__ import annotations

import logging

import pytest

from quam_state_manager.core.pointer_resolver import (
    clear_cache,
    is_pointer,
    is_self_ref,
    resolve_pointer,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_ROOT = {
    "qubits": {
        "qA1": {
            "f_01": 6_255_526_125.489,
            "anharmonicity": -220_000_000,
            "T1": 8834,
            "xy": {
                "intermediate_frequency": 150_000_000,
                "RF_frequency": "#./inferred_RF_frequency",
                "opx_output": "#/wiring/qubits/qA1/xy/opx_output",
                "operations": {
                    "x180_DragCosine": {
                        "length": 40,
                        "amplitude": 0.1865,
                        "alpha": 0.6199,
                        "anharmonicity": "#/qubits/qA1/anharmonicity",
                        "detuning": 0,
                    },
                    "x90_DragCosine": {
                        "length": "#../x180_DragCosine/length",
                        "amplitude": 0.09325,
                        "alpha": "#../x180_DragCosine/alpha",
                        "anharmonicity": "#../x180_DragCosine/anharmonicity",
                    },
                },
            },
            "resonator": {
                "f_01": 7_200_000_000,
                "opx_output": "#/wiring/qubits/qA1/rr/opx_output",
            },
        },
    },
    "qubit_pairs": {
        "qA1-A2": {
            "macros": {
                "cz_unipolar": {
                    "flux_pulse_qubit": {
                        "length": 100,
                        "amplitude": 0.25,
                    },
                },
            },
        },
    },
    "wiring": {
        "qubits": {
            "qA1": {
                "xy": {"opx_output": "MW-FEM/1"},
                "rr": {"opx_output": "MW-FEM/2", "opx_input": "MW-FEM/1"},
            },
        },
    },
}


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Clear the resolver cache before and after every test."""
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# Absolute pointers  (#/)
# ---------------------------------------------------------------------------


class TestAbsolutePointers:
    def test_simple_scalar(self):
        val = resolve_pointer(
            SAMPLE_ROOT,
            "#/qubits/qA1/anharmonicity",
            ("somewhere",),
        )
        assert val == -220_000_000

    def test_nested_object(self):
        val = resolve_pointer(
            SAMPLE_ROOT,
            "#/wiring/qubits/qA1/xy/opx_output",
            ("qubits", "qA1", "xy", "opx_output"),
        )
        assert val == "MW-FEM/1"

    def test_returns_dict_when_pointing_at_object(self):
        val = resolve_pointer(
            SAMPLE_ROOT,
            "#/qubits/qA1/xy/operations/x180_DragCosine",
            ("somewhere",),
        )
        assert isinstance(val, dict)
        assert val["length"] == 40

    def test_missing_key_returns_raw_pointer(self, caplog):
        pointer = "#/qubits/nonexistent/f_01"
        with caplog.at_level(logging.DEBUG):
            val = resolve_pointer(SAMPLE_ROOT, pointer, ("x",))
        assert val == pointer
        assert "not found" in caplog.text


# ---------------------------------------------------------------------------
# Relative-up pointers  (#../)
# ---------------------------------------------------------------------------


class TestRelativeUpPointers:
    def test_sibling_scalar(self):
        """#../x180_DragCosine/length from x90's length field."""
        val = resolve_pointer(
            SAMPLE_ROOT,
            "#../x180_DragCosine/length",
            ("qubits", "qA1", "xy", "operations", "x90_DragCosine", "length"),
        )
        assert val == 40

    def test_sibling_float(self):
        """#../x180_DragCosine/alpha from x90's alpha field."""
        val = resolve_pointer(
            SAMPLE_ROOT,
            "#../x180_DragCosine/alpha",
            ("qubits", "qA1", "xy", "operations", "x90_DragCosine", "alpha"),
        )
        assert val == 0.6199

    def test_chained_through_absolute(self):
        """x90's anharmonicity -> #../x180_DragCosine/anharmonicity -> #/qubits/qA1/anharmonicity."""
        val = resolve_pointer(
            SAMPLE_ROOT,
            "#../x180_DragCosine/anharmonicity",
            ("qubits", "qA1", "xy", "operations", "x90_DragCosine", "anharmonicity"),
        )
        assert val == -220_000_000

    def test_through_pointer_mid_path(self):
        """A path that CROSSES a pointer mid-way must be followed (CR wiring:
        cross_resonance.LO_frequency = #/qubits/qc/xy/opx_output/upconverters/2/
        frequency, where qc/xy/opx_output is itself a pointer to a shared port).
        """
        root = {
            "qubits": {
                "qc": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"}},
            },
            "ports": {
                "mw_outputs": {"con1": {"1": {"2": {
                    "upconverters": {"1": {"frequency": 8.6e9},
                                     "2": {"frequency": 9.095e9}},
                }}}},
            },
            "qubit_pairs": {
                "q0-4": {"cross_resonance": {
                    "LO_frequency": "#/qubits/qc/xy/opx_output/upconverters/2/frequency",
                }},
            },
        }
        val = resolve_pointer(
            root,
            "#/qubits/qc/xy/opx_output/upconverters/2/frequency",
            ("qubit_pairs", "q0-4", "cross_resonance", "LO_frequency"),
        )
        assert val == 9.095e9

    def test_through_pointer_dangling_mid_path_returns_raw(self):
        """If the intermediate pointer is dangling, the whole path is unresolved."""
        root = {
            "qubits": {"qc": {"xy": {"opx_output": "#/ports/nope"}}},
            "ports": {},
        }
        ptr = "#/qubits/qc/xy/opx_output/upconverters/2/frequency"
        assert resolve_pointer(root, ptr, ("x",)) == ptr

    def test_short_current_path_returns_raw(self, caplog):
        pointer = "#../something/value"
        with caplog.at_level(logging.WARNING):
            val = resolve_pointer(SAMPLE_ROOT, pointer, ("only_one_segment",))
        assert val == pointer
        assert "too short" in caplog.text


# ---------------------------------------------------------------------------
# Self-ref pointers  (#./)
# ---------------------------------------------------------------------------


class TestSelfRefPointers:
    def test_self_ref_returned_as_is(self):
        val = resolve_pointer(
            SAMPLE_ROOT,
            "#./inferred_RF_frequency",
            ("qubits", "qA1", "xy", "RF_frequency"),
        )
        assert val == "#./inferred_RF_frequency"

    def test_self_ref_not_traversed(self):
        val = resolve_pointer(
            SAMPLE_ROOT,
            "#./upconverter_frequency",
            ("some", "path"),
        )
        assert val == "#./upconverter_frequency"


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


class TestCache:
    """Per-store cache semantics. Callers without a cache get correct uncached
    results; callers that pass an explicit cache + lock get a hit on the second
    call to the same (pointer, path) pair."""

    def _fresh_cache(self):
        import threading
        return {}, threading.Lock()

    def test_cache_hit_returns_same_result(self):
        path = ("qubits", "qA1", "xy", "opx_output")
        pointer = "#/wiring/qubits/qA1/xy/opx_output"
        cache, lock = self._fresh_cache()

        val1 = resolve_pointer(SAMPLE_ROOT, pointer, path, cache=cache, lock=lock)
        val2 = resolve_pointer(SAMPLE_ROOT, pointer, path, cache=cache, lock=lock)
        assert val1 == val2 == "MW-FEM/1"
        # Cache populated with exactly the requested entry.
        assert (pointer, path) in cache

    def test_clear_cache_invalidates(self):
        path = ("qubits", "qA1", "xy", "opx_output")
        pointer = "#/wiring/qubits/qA1/xy/opx_output"
        cache, lock = self._fresh_cache()

        resolve_pointer(SAMPLE_ROOT, pointer, path, cache=cache, lock=lock)
        with lock:
            cache.clear()

        mutated = {**SAMPLE_ROOT, "wiring": {
            "qubits": {"qA1": {"xy": {"opx_output": "CHANGED"}, "rr": {"opx_output": "x", "opx_input": "y"}}},
        }}
        val = resolve_pointer(mutated, pointer, path, cache=cache, lock=lock)
        assert val == "CHANGED"

    def test_different_current_paths_cached_separately(self):
        pointer = "#../x180_DragCosine/length"
        path_a = ("qubits", "qA1", "xy", "operations", "x90_DragCosine", "length")
        path_b = ("qubits", "qA1", "xy", "operations", "x90_DragCosine", "alpha")
        cache, lock = self._fresh_cache()

        val_a = resolve_pointer(SAMPLE_ROOT, pointer, path_a, cache=cache, lock=lock)
        val_b = resolve_pointer(SAMPLE_ROOT, pointer, path_b, cache=cache, lock=lock)
        assert val_a == 40
        assert val_b == 40

    def test_uncached_call_returns_correct_value(self):
        """A caller that omits ``cache`` still gets a correct resolution
        (just no caching). The default keyword args must not break callers
        that resolve ad-hoc (e.g. ``Differ`` and tests)."""
        val = resolve_pointer(
            SAMPLE_ROOT,
            "#/qubits/qA1/anharmonicity",
            ("somewhere",),
        )
        assert val == -220_000_000

    def test_two_caches_isolated_for_same_named_qubits(self):
        """Regression for red-team Phase 2 finding 0.1.

        Two roots with identically-named qubits and different concrete
        values must NOT poison each other's cache entries. The fix routes
        the cache through a per-store dict; before that, the module-level
        cache returned chip A's value when chip B was queried.
        """
        root_a = {
            "qubits": {"qA1": {"f_01": 5_000_000_000.0}},
            "wiring": {},
        }
        root_b = {
            "qubits": {"qA1": {"f_01": 5_500_000_000.0}},
            "wiring": {},
        }
        cache_a, lock_a = self._fresh_cache()
        cache_b, lock_b = self._fresh_cache()

        pointer = "#/qubits/qA1/f_01"
        path = ("qubits", "qA1", "xy", "operations", "x90_DragCosine", "frequency")
        val_a = resolve_pointer(root_a, pointer, path, cache=cache_a, lock=lock_a)
        val_b = resolve_pointer(root_b, pointer, path, cache=cache_b, lock=lock_b)
        assert val_a == 5_000_000_000.0
        assert val_b == 5_500_000_000.0
        # Each cache holds only its own resolution.
        assert cache_a[(pointer, path)] == 5_000_000_000.0
        assert cache_b[(pointer, path)] == 5_500_000_000.0


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


class TestCycleDetection:
    def test_direct_cycle_returns_raw_pointer(self, caplog):
        root = {"a": {"val": "#/b/val"}, "b": {"val": "#/a/val"}}
        with caplog.at_level(logging.WARNING):
            val = resolve_pointer(root, "#/a/val", ("start",))
        assert "Cycle" in caplog.text
        assert val == "#/a/val"

    def test_self_pointing_cycle(self, caplog):
        root = {"x": {"y": "#/x/y"}}
        with caplog.at_level(logging.WARNING):
            val = resolve_pointer(root, "#/x/y", ("start",))
        assert "Cycle" in caplog.text
        assert val == "#/x/y"


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


class TestUtilities:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("#/qubits/qA1/f_01", True),
            ("#../x180/length", True),
            ("#./inferred", True),
            ("not_a_pointer", False),
            (42, False),
            (None, False),
            ("", False),
        ],
    )
    def test_is_pointer(self, value, expected):
        assert is_pointer(value) is expected

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("#./inferred_RF_frequency", True),
            ("#/qubits/qA1/f_01", False),
            ("#../x180/length", False),
            ("not_a_pointer", False),
            (42, False),
        ],
    )
    def test_is_self_ref(self, value, expected):
        assert is_self_ref(value) is expected


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_pointer_to_list_index(self):
        root = {"data": {"items": [10, 20, 30]}}
        val = resolve_pointer(root, "#/data/items/1", ("x",))
        assert val == 20

    def test_pointer_to_list_bad_index(self, caplog):
        root = {"data": {"items": [10, 20]}}
        with caplog.at_level(logging.WARNING):
            val = resolve_pointer(root, "#/data/items/5", ("x",))
        assert val == "#/data/items/5"

    def test_unknown_pointer_format(self, caplog):
        with caplog.at_level(logging.WARNING):
            val = resolve_pointer(SAMPLE_ROOT, "#??invalid", ("x",))
        assert val == "#??invalid"
        assert "Unknown pointer format" in caplog.text

    def test_resolve_into_non_dict_non_list(self, caplog):
        root = {"a": {"b": 42}}
        with caplog.at_level(logging.DEBUG):
            val = resolve_pointer(root, "#/a/b/c", ("x",))
        assert val == "#/a/b/c"
        assert "cannot traverse" in caplog.text


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    """Module-level cache is accessed from Flask workers + pywebview UI thread."""

    def test_concurrent_reads_do_not_lose_entries(self):
        from concurrent.futures import ThreadPoolExecutor

        root = {
            "qubits": {f"qA{i}": {"f_01": 6.0e9 + i, "anharm": -2.2e8} for i in range(20)},
        }
        pointers = [f"#/qubits/qA{i}/f_01" for i in range(20)]

        def worker(p):
            clear_cache()
            for _ in range(50):
                resolve_pointer(root, p, ("x",))
            return resolve_pointer(root, p, ("x",))

        clear_cache()
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(worker, pointers * 4))
        # No exception is the main assertion; verify values too.
        assert all(isinstance(r, float) for r in results)

    def test_concurrent_clear_and_resolve_no_keyerror(self):
        from concurrent.futures import ThreadPoolExecutor

        root = {"qubits": {"qA1": {"f_01": 6.0e9}}}

        def reader():
            for _ in range(200):
                resolve_pointer(root, "#/qubits/qA1/f_01", ("x",))

        def clearer():
            for _ in range(200):
                clear_cache()

        clear_cache()
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = [ex.submit(reader) for _ in range(3)] + [ex.submit(clearer) for _ in range(3)]
            for f in futs:
                f.result()  # raises if any thread blew up
