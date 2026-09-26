"""The Pulses page's RAM layer (docs/2xx pulses RAM): ``PulseIndex`` keeps its
rows, reverse index and sparklines across edits and updates them
INCREMENTALLY on a value write -- and must never serve anything a cold
recompute would not.

The global pin is the randomized event sequence: value writes, pointer
moves, literalized pointers, ``__class__`` swaps, create / delete, undo,
reload, env-overlay swaps, bare ``invalidate()`` calls. After EVERY step the
served rows, ``used_by`` and sparklines equal a cold recompute, and the
incremental path must actually have run (a sequence that always fell back
to cold would pass vacuously).
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

from quam_state_manager.core import pulse_index as pi
from quam_state_manager.core.loader import QuamStore, is_value_only_write
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.pulse_catalog import apply_env_overlay
from quam_state_manager.core.pointer_resolver import is_pointer
from quam_state_manager.core.ramcache import StaleCacheError

QC = "quam.components.pulses."
ARCH = "quam_builder.architecture.superconducting.components.pulses."
GOLDEN = Path(__file__).parent / "golden"
LAB_HOME = "otherlab.custom.pulses"
NEXT_HOME = "quam_builder.next_gen.components.pulses"


def _state() -> dict:
    def drag(cls, amp, **kw):
        d = {"length": 48, "axis_angle": 0, "amplitude": amp, "alpha": -0.3,
             "anharmonicity": "#../../../anharmonicity", "__class__": cls + "DragCosinePulse"}
        d.update(kw)
        return d

    qubits = {}
    for i, cls in enumerate((QC, ARCH, ARCH)):
        q = f"q{i + 1}"
        qubits[q] = {
            "anharmonicity": -200e6 - 1e6 * i,
            "f_01": 5e9 + 1e8 * i,
            "xy": {"operations": {
                "x180_DragCosine": drag(cls, 0.3),
                "x90_DragCosine": drag(cls, 0.15, length="#../x180_DragCosine/length",
                                       alpha="#../x180_DragCosine/alpha"),
                # two hops: y90.amplitude -> x90.amplitude
                "y90_DragCosine": drag(cls, "#../x90_DragCosine/amplitude",
                                       length="#../x90_DragCosine/length", axis_angle=90),
                "x180": "#./x180_DragCosine",
                "x90": "#./x90_DragCosine",
                # a pointer THROUGH an alias: x180 is itself a pointer string
                "x180_copy": {"length": f"#/qubits/{q}/xy/operations/x180/length",
                              "amplitude": 0.2, "__class__": QC + "SquarePulse"},
                "sat": {"length": 2000, "amplitude": 0.004, "__class__": QC + "SquarePulse"},
                "mystery": {"length": 10, "amplitude": 0.1,
                            "__class__": "labx.custom.WeirdPulse"},
                # known to the env overlay only
                "wiggle": {"length": 40, "amplitude": 0.1, "flat_length": 8,
                           "__class__": LAB_HOME + ".LabWigglePulse"},
                "snz_next": {"length": "#./inferred_length", "amplitude": 0.06,
                             "flat_length": 20, "t_phi_eff": 2.0, "padding": 0,
                             "__class__": NEXT_HOME + ".SNZPulse"},
            }},
            "z": {"operations": {
                "const": {"length": 100, "amplitude": 0.05, "__class__": QC + "SquarePulse"},
                "snz": {"length": "#./inferred_length", "amplitude": 0.06, "flat_length": 20,
                        "t_phi_eff": 2.0, "padding": 0, "__class__": ARCH + "SNZPulse"},
            }},
            "resonator": {"operations": {
                "readout": {"length": 1024, "amplitude": 0.01,
                            "integration_weights": "#./default_integration_weights",
                            "__class__": QC + "SquareReadoutPulse"},
            }},
        }
    # a cross-qubit absolute pointer (q3 borrows q1's readout length)
    qubits["q3"]["resonator"]["operations"]["readout"]["length"] = \
        "#/qubits/q1/resonator/operations/readout/length"
    return {
        "qubits": qubits,
        "qubit_pairs": {
            "q1-2": {"macros": {
                "cz_unipolar": {"flux_pulse_qubit": {"amplitude": 0.05, "length": 100},
                                "coupler_flux_pulse": None},
                "cz_flat": {"flux_pulse_qubit": {
                    "amplitude": "#/qubits/q1/z/operations/const/amplitude",
                    "flat_length": 48, "smoothing_length": 8, "length": 68}},
                "cz": "#./cz_unipolar",
            }},
        },
    }


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SM_RAM_VERIFY", raising=False)
    apply_env_overlay(None)
    yield
    apply_env_overlay(None)


@pytest.fixture
def chip(tmp_path) -> Path:
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"network": {}}), encoding="utf-8")
    return tmp_path


@pytest.fixture(scope="module")
def roster() -> dict:
    """The modern roster plus a class ONLY the env knows (the overlay test
    file's recipe), so an overlay swap really changes a row."""
    data = json.loads((GOLDEN / "state_schema_modern.json").read_text(encoding="utf-8"))
    r = copy.deepcopy(data["pulse_roster"])
    rec = copy.deepcopy(r["CosineBipolarPulse"])
    rec["canonical"] = LAB_HOME + ".LabWigglePulse"
    rec["homes"] = [LAB_HOME]
    r["LabWigglePulse"] = rec
    # a catalog class at a module home only the env knows: class_match goes
    # "leaf" -> "env" when the overlay is installed
    r["SNZPulse"]["homes"].append(NEXT_HOME)
    return r


def _render(store, path):
    from quam_state_manager.core.waveform_synth import sparkline_svg, synth_for_operation
    return lambda: sparkline_svg(synth_for_operation(store, path))


def _leaves(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, f"{prefix}.{k}" if prefix else k)
    else:
        yield prefix, node


def _assert_cold_equal(store, idx, rng):
    cold = pi.list_pulses(store.merged)
    served = idx.rows()
    assert served == cold
    paths = [r["path"] for r in cold]
    for p in rng.sample(paths, min(6, len(paths))):
        assert idx.used_by(p) == pi.used_by(store.merged, p)
        assert idx.row(p) == next(r for r in cold if r["path"] == p)
        assert idx.sparkline(p, _render(store, p)) == _render(store, p)()


# ---------------------------------------------------------------------------
# The global pin: a randomized event sequence
# ---------------------------------------------------------------------------

def _step(rng, store, mod, roster, created):
    merged = store.merged
    leaves = list(_leaves(merged))
    numeric = [(p, v) for p, v in leaves
               if isinstance(v, (int, float)) and not isinstance(v, bool)
               and not p.endswith("__class__")]
    pointers = [(p, v) for p, v in leaves if is_pointer(v)]
    kind = rng.choices(
        ["value", "value", "value", "value", "value", "pointer", "literal", "klass",
         "create", "delete", "undo", "reload", "overlay", "invalidate"],
        k=1)[0]
    if kind == "value":
        p, v = rng.choice(numeric)
        new = v + rng.choice((1, 2, 4)) if isinstance(v, int) else v * (1 + rng.random() / 10) + 1e-6
        mod.set_value(p, new)
    elif kind == "pointer" and pointers:
        p, _ = rng.choice(pointers)
        q, _ = rng.choice(numeric)
        mod.set_value(p, "#/" + q.replace(".", "/"), coerce=False, enforce=False)
    elif kind == "literal" and pointers:
        p, _ = rng.choice(pointers)
        mod.set_value(p, rng.random(), coerce=False, enforce=False)
    elif kind == "klass":
        ps = [p for p, v in leaves if p.endswith("__class__")]
        p = rng.choice(ps)
        mod.set_value(p, rng.choice((QC + "SquarePulse", ARCH + "DragCosinePulse",
                                     "labx.custom.WeirdPulse")), coerce=False, enforce=False)
    elif kind == "create":
        q = rng.choice(list(merged["qubits"]))
        name = f"new{len(created)}_{rng.randrange(10**6)}"
        path = f"qubits.{q}.xy.operations.{name}"
        body = {"length": 40, "amplitude": 0.1, "__class__": QC + "SquarePulse"}
        if rng.random() < 0.5:
            body["amplitude"] = f"#/qubits/{q}/xy/operations/sat/amplitude"
        mod.create_subtree(path, body)
        created.append(path)
    elif kind == "delete" and created:
        path = created.pop(rng.randrange(len(created)))
        try:
            mod.delete_subtree(path)
        except KeyError:
            pass
    elif kind == "undo" and store.change_log:
        try:
            mod.undo()
        except KeyError:
            pass
    elif kind == "reload":
        # an outside rewrite of the working files, then the store's reload
        folder = Path(store.folder_path)
        st = copy.deepcopy(store.state)
        st["qubits"]["q1"]["xy"]["operations"]["sat"]["amplitude"] = rng.random()
        (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
        store.reload()
        created.clear()
    elif kind == "overlay":
        apply_env_overlay(None if rng.random() < 0.5 else roster)
    elif kind == "invalidate":
        return kind
    return kind


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_randomized_event_sequence_matches_cold(chip, roster, seed):
    rng = random.Random(seed)
    store = QuamStore(str(chip))
    mod = Modifier(store)
    idx = pi.PulseIndex(store)
    idx.rows()
    created: list[str] = []
    kinds = []
    for _ in range(220):
        kind = _step(rng, store, mod, roster, created)
        if kind == "invalidate":
            idx.invalidate()
        kinds.append(kind)
        _assert_cold_equal(store, idx, rng)
    # non-vacuous: the incremental path carried most of the value writes,
    # and the sparkline cache served hits across mutations
    assert idx.stats["incremental"] >= 40, idx.stats
    assert idx.stats["spark_hit"] >= 100, idx.stats
    assert {"reload", "overlay", "create", "undo", "pointer", "klass"} <= set(kinds)


def test_overlay_swap_changes_rows_so_its_key_component_is_live(chip, roster):
    """The overlay component of the stamp is only pinned if a swap changes a
    row -- otherwise dropping it could never be caught."""
    store = QuamStore(str(chip))
    before = pi.list_pulses(store.merged)
    apply_env_overlay(roster)
    after = pi.list_pulses(store.merged)
    assert before != after
    idx = pi.PulseIndex(store)
    apply_env_overlay(None)
    assert idx.rows() == before
    apply_env_overlay(roster)
    assert idx.rows() == after


# ---------------------------------------------------------------------------
# Incremental reach: each dependency route, by name
# ---------------------------------------------------------------------------

class TestIncrementalReach:
    def _edit(self, chip, path, new):
        store = QuamStore(str(chip))
        idx = pi.PulseIndex(store)
        idx.rows()
        before = {r["path"]: r for r in idx.rows()}
        Modifier(store).set_value(path, new)
        rows = idx.rows()
        assert idx.stats["cold"] == 1 and idx.stats["incremental"] == 1
        assert rows == pi.list_pulses(store.merged)
        return {r["path"] for r in rows if r is not before[r["path"]]}

    def test_a_value_inside_a_pulse_recomputes_that_row(self, chip):
        changed = self._edit(chip, "qubits.q1.xy.operations.sat.amplitude", 0.009)
        assert changed == {"qubits.q1.xy.operations.sat"}

    def test_a_two_hop_chain_reaches_the_far_end(self, chip):
        changed = self._edit(chip, "qubits.q2.xy.operations.x90_DragCosine.amplitude", 0.11)
        assert "qubits.q2.xy.operations.y90_DragCosine" in changed
        assert "qubits.q2.xy.operations.x90" in changed          # alias row
        assert not any(p.startswith("qubits.q1.") for p in changed)

    def test_a_pointer_through_an_alias_is_reached(self, chip):
        changed = self._edit(chip, "qubits.q1.xy.operations.x180_DragCosine.length", 52)
        assert "qubits.q1.xy.operations.x180_copy" in changed

    def test_a_qubit_field_reaches_the_pulses_that_point_at_it(self, chip):
        changed = self._edit(chip, "qubits.q3.anharmonicity", -190e6)
        assert changed == set()  # anharmonicity is not a row column ...
        # ... but the rows were recomputed and equal cold (asserted in _edit)

    def test_a_cross_container_pointer_is_reached(self, chip):
        changed = self._edit(chip, "qubits.q1.z.operations.const.amplitude", 0.07)
        assert "qubit_pairs.q1-2.macros.cz_flat.flux_pulse_qubit" in changed

    def test_an_unrelated_edit_recomputes_no_row(self, chip):
        assert self._edit(chip, "qubits.q2.f_01", 5.5e9) == set()


def test_value_only_classification():
    assert is_value_only_write("a.b", 1, 2)
    assert is_value_only_write("a.b", 0.1, "text")
    assert not is_value_only_write("a.b", 1, "#../x/length")
    assert not is_value_only_write("a.b", "#./x", 3)
    assert not is_value_only_write("a.b", None, 3)
    assert not is_value_only_write("a.b", 3, None)
    assert not is_value_only_write("a.b", {"x": 1}, 3)
    assert not is_value_only_write("a.b", [1], [2])
    assert not is_value_only_write("a.__class__", "x.A", "x.B")


class TestMutationJournal:
    def test_contiguous_steps_are_vouched_for(self, chip):
        store = QuamStore(str(chip))
        m = Modifier(store)
        s0 = store.mutation_seq
        m.set_value("qubits.q1.f_01", 5.1e9)
        m.set_value("qubits.q1.xy.operations.x180", "#./x90_DragCosine", coerce=False)
        steps = store.mutations_since(s0)
        assert [(p, vo) for _, p, vo in steps] == [
            ("qubits.q1.f_01", True), ("qubits.q1.xy.operations.x180", False)]
        assert store.mutations_since(store.mutation_seq) == []

    def test_an_unjournaled_bump_is_a_gap(self, chip):
        store = QuamStore(str(chip))
        s0 = store.mutation_seq
        store.mutation_seq += 1   # a writer that did not journal
        assert store.mutations_since(s0) is None

    def test_reload_and_undo_are_journaled(self, chip):
        store = QuamStore(str(chip))
        m = Modifier(store)
        s0 = store.mutation_seq
        m.set_value("qubits.q1.f_01", 5.1e9)
        m.undo()
        store.reload()
        steps = store.mutations_since(s0)
        assert [(p, vo) for _, p, vo in steps] == [
            ("qubits.q1.f_01", True), ("qubits.q1.f_01", True), (None, False)]

    def test_aged_out_steps_are_a_gap(self, chip, monkeypatch):
        from collections import deque
        store = QuamStore(str(chip))
        store._mut_journal = deque(maxlen=3)
        m = Modifier(store)
        s0 = store.mutation_seq
        for k in range(5):
            m.set_value("qubits.q1.f_01", 5e9 + k)
        assert store.mutations_since(s0) is None
        assert len(store.mutations_since(store.mutation_seq - 3)) == 3


def test_a_journal_gap_recomputes_cold(chip):
    store = QuamStore(str(chip))
    idx = pi.PulseIndex(store)
    idx.rows()
    # a write the journal never heard of (a direct dict poke + a bare bump)
    store.merged["qubits"]["q1"]["xy"]["operations"]["sat"]["amplitude"] = 0.5
    store.mutation_seq += 1
    assert idx.rows() == pi.list_pulses(store.merged)
    assert idx.stats["cold"] == 2


def test_invalidate_without_a_seq_move_drops_the_rows(chip):
    store = QuamStore(str(chip))
    idx = pi.PulseIndex(store)
    idx.rows()
    store.merged["qubits"]["q1"]["xy"]["operations"]["sat"]["amplitude"] = 0.5
    idx.invalidate()
    assert idx.rows() == pi.list_pulses(store.merged)


class TestShadowMode:
    def test_verify_mode_passes_a_correct_sequence(self, chip, roster, monkeypatch):
        monkeypatch.setenv("SM_RAM_VERIFY", "1")
        rng = random.Random(7)
        store = QuamStore(str(chip))
        mod = Modifier(store)
        idx = pi.PulseIndex(store)
        idx.rows()
        created: list[str] = []
        for _ in range(60):
            if _step(rng, store, mod, roster, created) == "invalidate":
                idx.invalidate()
            idx.rows()
            for r in idx.rows()[:8]:
                idx.sparkline(r["path"], _render(store, r["path"]))
        assert idx.stats["incremental"] > 5

    def test_verify_mode_catches_a_reach_bug(self, chip, monkeypatch):
        """A planted bug (the chain walk sees nothing) must raise in shadow
        mode instead of serving a stale row."""
        monkeypatch.setenv("SM_RAM_VERIFY", "1")
        monkeypatch.setattr(pi.PulseIndex, "_holders_touching", lambda self, p: [])
        store = QuamStore(str(chip))
        idx = pi.PulseIndex(store)
        idx.rows()
        Modifier(store).set_value("qubits.q1.xy.operations.x180_DragCosine.length", 60)
        with pytest.raises(StaleCacheError):
            idx.rows()


def test_a_value_written_at_a_scalar_row_body_is_structural(chip):
    """An alias op literalized to a number is an "(invalid)" row whose BODY is
    the scalar; a later scalar->scalar write there is value-only to the
    journal but IS that row's whole body."""
    store = QuamStore(str(chip))
    m = Modifier(store)
    idx = pi.PulseIndex(store)
    m.set_value("qubits.q1.xy.operations.x180", 3.0, coerce=False, enforce=False)
    idx.rows()
    m.set_value("qubits.q1.xy.operations.x180", 4.0)
    assert idx.rows() == pi.list_pulses(store.merged)


@pytest.mark.parametrize("op", ["create", "delete"])
def test_undoing_a_create_or_delete_is_structural(chip, op):
    store = QuamStore(str(chip))
    m = Modifier(store)
    idx = pi.PulseIndex(store)
    path = "qubits.q2.xy.operations.sat"
    if op == "create":
        path = "qubits.q2.xy.operations.brand_new"
        m.create_subtree(path, {"length": 40, "amplitude": 0.1,
                                "__class__": QC + "SquarePulse"})
    else:
        m.delete_subtree(path)
    assert idx.rows() == pi.list_pulses(store.merged)
    m.undo()
    assert idx.rows() == pi.list_pulses(store.merged)
