"""docs/138 — a 2Q RB number is per-Clifford or per-gate, and they differ.

Measured on the customer's real chip
(`CQT/data/2026-08-19/#2259_30_cz_iswap_flux_bootstrap_052751`):

    StandardRB          0.9671621994719876   = 1 - EPC   (per CLIFFORD)
    StandardRB_alpha    0.9562162659626501   = decay base, not a fidelity
    InterleavedRB       0.9856971280318174   = 1 - EPG   (per GATE)
    InterleavedRB_alpha 0.9379807475280403   = decay base, not a fidelity

All four were emitted identically and rendered as four percentages under one
"Gate fidelity" heading, and the edge was drawn GREY because only Bell_State
fed it — on a pair with a perfectly good measured CZ gate fidelity.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from quam_state_manager.core.pointer_resolver import resolve_pointer
from quam_state_manager.core.query import QueryEngine, _rb_level

# The real numbers, so the arithmetic below is checked against a real fit.
SRB, SRB_A = 0.9671621994719876, 0.9562162659626501
IRB, IRB_A = 0.9856971280318174, 0.9379807475280403


class _Store:
    """Enough store for get_topology. `resolve_pointer` is the REAL resolver,
    not a stub — the pair's control/target are pointers and the edge is built
    by following them."""

    def __init__(self, merged):
        self._lock = threading.RLock()
        self.merged = merged
        self.mutation_seq = 0
        self._pointer_cache: dict = {}
        self._pointer_cache_lock = threading.RLock()

    def resolve_pointer(self, pointer, current_path):
        return resolve_pointer(self.merged, pointer, current_path,
                               cache=self._pointer_cache,
                               lock=self._pointer_cache_lock)

    @property
    def qubit_names(self):
        return list((self.merged.get("qubits") or {}).keys())

    @property
    def qubit_pair_names(self):
        return list((self.merged.get("qubit_pairs") or {}).keys())


def _chip(fidelity: dict) -> dict:
    return {
        "qubits": {"q19": {"id": "q19"}, "q20": {"id": "q20"}},
        "qubit_pairs": {"q19-20": {
            "id": "q19-20",
            "qubit_control": "#/qubits/q19",
            "qubit_target": "#/qubits/q20",
            "macros": {"cz_unipolar": {"fidelity": dict(fidelity)}},
        }},
        "wiring": {"qubits": {}},
    }


def _edge(fidelity: dict) -> dict:
    topo = QueryEngine(_Store(_chip(fidelity))).get_topology()
    return topo["edges"][0]


REAL = {"StandardRB": SRB, "StandardRB_alpha": SRB_A,
        "InterleavedRB": IRB, "InterleavedRB_alpha": IRB_A,
        "StandardRB_load_id": 2256}


class TestTheArithmeticTheyRepresent:
    """Not a re-derivation for its own sake: it is what proves the two stored
    numbers are different quantities rather than two estimates of one."""

    def test_standard_rb_is_one_minus_error_per_clifford(self):
        d = 4                                   # two qubits
        epc = (d - 1) / d * (1 - SRB_A)
        assert 1 - epc == pytest.approx(SRB, abs=1e-12)

    def test_interleaved_rb_is_one_minus_error_per_gate(self):
        d = 4
        epg = (d - 1) / d * (1 - IRB_A / SRB_A)
        assert 1 - epg == pytest.approx(IRB, abs=1e-12)

    def test_and_so_they_are_not_the_same_number(self):
        """~1.9 points apart on this pair. Showing them as two comparable
        fidelities says the gate is worse than it is."""
        assert IRB - SRB > 0.018


class TestEachRowSaysWhatItMeasures:
    def test_the_levels(self):
        assert _rb_level("StandardRB") == "clifford"
        assert _rb_level("InterleavedRB") == "gate"
        assert _rb_level("IRB") == "gate"            # LabA's name
        assert _rb_level("Bell_State") == "state"

    def test_alpha_is_a_decay_parameter_not_a_fidelity(self):
        assert _rb_level("StandardRB_alpha") == "decay"
        assert _rb_level("InterleavedRB_alpha") == "decay"

    def test_an_unknown_metric_claims_nothing(self):
        assert _rb_level("SomeLabsOwnMetric") is None
        assert _rb_level(None) is None

    def test_the_rows_carry_it(self):
        rows = {r["metric"]: r for r in _edge(REAL)["gate_fidelities"]}
        assert rows["StandardRB"]["level"] == "clifford"
        assert rows["InterleavedRB"]["level"] == "gate"
        assert rows["StandardRB_alpha"]["level"] == "decay"
        assert rows["InterleavedRB_alpha"]["level"] == "decay"

    def test_the_load_id_is_still_not_a_row(self):
        metrics = {r["metric"] for r in _edge(REAL)["gate_fidelities"]}
        assert "StandardRB_load_id" not in metrics


class TestTheEdgeUsesTheGateNumber:
    def test_interleaved_rb_colours_the_edge(self):
        """The pair has a real measured CZ gate fidelity and was drawn grey."""
        e = _edge(REAL)
        assert e["cz_fidelity"] == pytest.approx(IRB)
        assert e["fidelity_source"] == "interleaved_rb"
        assert e["best_gate"] == "cz_unipolar"

    def test_standard_rb_alone_never_colours_it(self):
        """1-EPC is a Clifford number. A Clifford is ~1.5 two-qubit gates, so
        painting the edge with it understates every gate on the chip — and the
        divisor that would convert it is in neither state.json nor the run."""
        e = _edge({"StandardRB": SRB, "StandardRB_alpha": SRB_A})
        assert e["cz_fidelity"] is None
        assert e["fidelity_source"] is None

    def test_bell_state_still_wins(self):
        """Unchanged precedence: Bell_State was the only source before, and a
        chip that has it must keep the number it had."""
        e = _edge(dict(REAL, Bell_State={"Fidelity": 0.93}))
        assert e["cz_fidelity"] == pytest.approx(0.93)
        assert e["fidelity_source"] == "macro"

    def test_an_unphysical_interleaved_value_is_refused(self):
        e = _edge({"InterleavedRB": 1.9})
        assert e["cz_fidelity"] is None

    def test_a_string_never_reaches_the_comparison(self):
        """A dangling pointer once 500'd the whole topology."""
        e = _edge({"InterleavedRB": "#/somewhere"})
        assert e["cz_fidelity"] is None

    def test_a_chip_with_no_rb_is_unchanged(self):
        e = _edge({})
        assert e["cz_fidelity"] is None and e["gate_fidelities"] == []


# ══════════════════════════════════════════════════════════════════════════════
# The per-GATE number a Standard-RB run computed and then discarded
# ══════════════════════════════════════════════════════════════════════════════

from quam_state_manager.core import rb_gate_fidelity as RGF   # noqa: E402

_REAL_RUN = Path(r"D:\work\Customer_Codes\CQT\data\2026-08-19"
                 r"\#2271_37a_two_qubit_standard_rb_054106")


class TestPairKeySpelling:
    """A pair is `q19-20` on the chip and may be `q19-q20` in a run. Trying one
    spelling is how a whole class of data goes silently missing — twice already
    (docs/136 §18, docs/137 §3)."""

    def test_both_spellings_are_tried(self):
        assert RGF.pair_key_variants("q19-20") == ["q19-20", "q19-q20"]
        assert RGF.pair_key_variants("q19-q20") == ["q19-q20", "q19-20"]

    def test_a_non_pair_id_is_left_alone(self):
        assert RGF.pair_key_variants("q19") == ["q19"]


class TestReadingTheRun:
    def _run(self, tmp_path, fits):
        (tmp_path / "data.json").write_text(
            json.dumps({"fit_results": fits}), encoding="utf-8")
        return tmp_path

    def test_the_run_states_the_gate_fidelity_and_the_divisor(self, tmp_path):
        d = self._run(tmp_path, {"q19-20": {
            "average_gate_fidelity": 0.9944230963325416,
            "epg": 0.005576903667458468,
            "average_gates_per_clifford": 5.370984455958549}})
        got = RGF.from_run_folder(d, "q19-20")
        assert got["average_gate_fidelity"] == pytest.approx(0.9944230963325416)
        assert got["average_gates_per_clifford"] == pytest.approx(5.370984455958549)

    def test_a_short_keyed_pair_is_found(self, tmp_path):
        d = self._run(tmp_path, {"q19-20": {"average_gate_fidelity": 0.99}})
        assert RGF.from_run_folder(d, "q19-q20")["average_gate_fidelity"] == 0.99

    def test_an_older_run_with_only_epg_still_answers(self, tmp_path):
        d = self._run(tmp_path, {"q19-20": {"epg": 0.01}})
        assert RGF.from_run_folder(d, "q19-20")["average_gate_fidelity"] == pytest.approx(0.99)

    def test_a_pair_the_run_never_measured(self, tmp_path):
        d = self._run(tmp_path, {"q19-20": {"average_gate_fidelity": 0.99}})
        assert RGF.from_run_folder(d, "q1-2") is None

    def test_a_broken_fit_is_not_handed_out(self, tmp_path):
        """Outside (0,1] is not a measurement. The topology quarantines those."""
        for bad in (1.9, 0.0, -0.5):
            d = self._run(tmp_path, {"q19-20": {"average_gate_fidelity": bad}})
            assert RGF.from_run_folder(d, "q19-20") is None, bad

    def test_a_string_is_not_a_fidelity(self, tmp_path):
        d = self._run(tmp_path, {"q19-20": {"average_gate_fidelity": "0.99"}})
        assert RGF.from_run_folder(d, "q19-20") is None

    def test_absent_unreadable_and_none_all_mean_no_number(self, tmp_path):
        assert RGF.from_run_folder(None, "q19-20") is None
        assert RGF.from_run_folder(tmp_path / "nope", "q19-20") is None
        (tmp_path / "data.json").write_text("{not json", encoding="utf-8")
        assert RGF.from_run_folder(tmp_path, "q19-20") is None

    @pytest.mark.skipif(not _REAL_RUN.is_dir(),
                        reason="the customer's RB run is not on this machine")
    def test_against_the_real_run(self):
        """The number is READ, never recomputed — this is the run's own answer."""
        got = RGF.from_run_folder(_REAL_RUN, "q19-20")
        assert got["average_gate_fidelity"] == pytest.approx(0.9944230963325416)
        assert got["average_gates_per_clifford"] == pytest.approx(5.370984455958549)
        # and it really is EPC / that divisor, not something else
        epc = 1 - 0.9700465370897023
        assert got["epg"] == pytest.approx(epc / got["average_gates_per_clifford"],
                                           rel=1e-9)


class TestEnrichingTheEdges:
    def _edges(self):
        return [{"pair_id": "q19-20", "gate_fidelities": [
            {"gate": "cz_flattop", "metric": "StandardRB", "value": 0.970608,
             "level": "clifford", "load_id": 111},
            {"gate": "cz_flattop", "metric": "InterleavedRB", "value": 0.993363,
             "level": "gate", "load_id": 111},
            {"gate": "cz_flattop", "metric": "StandardRB_alpha", "value": 0.96,
             "level": "decay", "load_id": 111},
        ]}]

    def _resolver(self, tmp_path, fid=0.9944):
        (tmp_path / "data.json").write_text(json.dumps(
            {"fit_results": {"q19-20": {"average_gate_fidelity": fid,
                                        "average_gates_per_clifford": 5.371}}}),
            encoding="utf-8")
        return lambda load_id: tmp_path if load_id == 111 else None

    def test_only_the_clifford_row_is_enriched(self, tmp_path):
        edges = self._edges()
        assert RGF.derive_for_edges(edges, self._resolver(tmp_path)) == 1
        rows = {r["metric"]: r for r in edges[0]["gate_fidelities"]}
        assert rows["StandardRB"]["derived_gate_fidelity"] == pytest.approx(0.9944)
        assert rows["StandardRB"]["average_gates_per_clifford"] == pytest.approx(5.371)
        assert "derived_gate_fidelity" not in rows["InterleavedRB"]
        assert "derived_gate_fidelity" not in rows["StandardRB_alpha"]

    def test_the_clifford_value_itself_is_left_alone(self, tmp_path):
        """Two different quantities. The derived one is added BESIDE it."""
        edges = self._edges()
        RGF.derive_for_edges(edges, self._resolver(tmp_path))
        rows = {r["metric"]: r for r in edges[0]["gate_fidelities"]}
        assert rows["StandardRB"]["value"] == 0.970608
        assert rows["StandardRB"]["level"] == "clifford"

    def test_a_missing_run_is_a_blank_not_a_guess(self):
        edges = self._edges()
        assert RGF.derive_for_edges(edges, lambda i: None) == 0
        assert all("derived_gate_fidelity" not in r
                   for r in edges[0]["gate_fidelities"])

    def test_a_resolver_that_raises_never_breaks_the_render(self):
        def boom(_):
            raise RuntimeError("dataset layer is down")
        edges = self._edges()
        assert RGF.derive_for_edges(edges, boom) == 0

    def test_a_row_with_no_load_id_is_skipped(self, tmp_path):
        edges = self._edges()
        for r in edges[0]["gate_fidelities"]:
            r.pop("load_id", None)
        assert RGF.derive_for_edges(edges, self._resolver(tmp_path)) == 0

    def test_the_run_is_read_once_per_load_id(self, tmp_path):
        """A 30-pair chip must not re-read one run thirty times."""
        calls = []
        resolver = self._resolver(tmp_path)

        def counting(load_id):
            calls.append(load_id)
            return resolver(load_id)

        edges = self._edges()
        edges[0]["gate_fidelities"].append(
            {"gate": "cz_unipolar", "metric": "StandardRB", "value": 0.9671,
             "level": "clifford", "load_id": 111})
        assert RGF.derive_for_edges(edges, counting) == 2
        assert calls == [111], calls

    def test_malformed_input_is_survivable(self):
        assert RGF.derive_for_edges(None, lambda i: None) == 0
        assert RGF.derive_for_edges([None, {}, {"gate_fidelities": None}],
                                    lambda i: None) == 0


class TestTheRouteHelperThatResolvesTheRun:
    """`_rb_run_folder` was the one link the unit tests above did NOT cover:
    they hand `derive_for_edges` their own resolver, so the real one could be —
    and was — wrong. `DatasetStore.get_run` returns a **dict** with a
    stringified `folder_path`, not the `RunInfo` object, so
    `getattr(run, "folder_path", None)` yielded None for every run and every
    derived value came back blank while every test stayed green.
    """

    def _helper(self, monkeypatch, stores):
        from quam_state_manager.web import routes
        monkeypatch.setattr(routes, "_active_dataset_stores",
                            lambda **kw: stores)
        return routes._rb_run_folder

    def test_the_dict_shape_get_run_actually_returns(self, monkeypatch, tmp_path):
        class Store:
            def get_run(self, rid):
                return {"run_id": rid, "folder_path": str(tmp_path)} if rid == 7 else None

        helper = self._helper(monkeypatch, [{"key": "k", "store": Store()}])
        assert helper(7) == str(tmp_path)

    def test_an_object_shaped_run_still_works(self, monkeypatch, tmp_path):
        """Belt and braces — a caller elsewhere may hand back the RunInfo."""
        class Run:
            folder_path = tmp_path

        class Store:
            def get_run(self, rid):
                return Run() if rid == 7 else None

        assert self._helper(monkeypatch, [{"key": "k", "store": Store()}])(7) == tmp_path

    def test_a_run_in_no_loaded_folder(self, monkeypatch):
        class Store:
            def get_run(self, rid):
                return None

        assert self._helper(monkeypatch, [{"key": "k", "store": Store()}])(7) is None

    def test_a_non_numeric_load_id(self, monkeypatch):
        helper = self._helper(monkeypatch, [])
        assert helper(None) is None and helper("nope") is None

    def test_one_broken_folder_never_breaks_the_render(self, monkeypatch, tmp_path):
        class Bad:
            def get_run(self, rid):
                raise OSError("the share went away")

        class Good:
            def get_run(self, rid):
                return {"folder_path": str(tmp_path)}

        helper = self._helper(monkeypatch, [{"key": "a", "store": Bad()},
                                            {"key": "b", "store": Good()}])
        assert helper(7) == str(tmp_path)

    def test_the_enrichment_never_mutates_the_cached_topology(self, monkeypatch, tmp_path):
        """`get_topology` is cached and shared; enriching it in place would
        leak derived values into every later reader, including ones with no
        dataset folder loaded."""
        from quam_state_manager.web import routes

        (tmp_path / "data.json").write_text(json.dumps(
            {"fit_results": {"q1-2": {"average_gate_fidelity": 0.99}}}),
            encoding="utf-8")

        cached = {"edges": [{"pair_id": "q1-2", "gate_fidelities": [
            {"gate": "cz", "metric": "StandardRB", "value": 0.97,
             "level": "clifford", "load_id": 7}]}]}

        class Engine:
            def get_topology(self):
                return cached

        class Store:
            def get_run(self, rid):
                return {"folder_path": str(tmp_path)}

        monkeypatch.setattr(routes, "_active_dataset_stores",
                            lambda **kw: [{"key": "k", "store": Store()}])
        out = routes._topology_with_derived_rb(Engine())
        assert out["edges"][0]["gate_fidelities"][0]["derived_gate_fidelity"] == 0.99
        assert "derived_gate_fidelity" not in cached["edges"][0]["gate_fidelities"][0]
