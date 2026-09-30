"""RAM P5 (docs/2xx): the chip's derived models are patched from the change
feed instead of recomputed, and a patched answer must NEVER differ from a
cold recompute.

* ``core/store_revs`` -- the change feed itself: one event per mutation_seq,
  plain vs structural, contiguity, an unexplained seq bump, chunk tokens.
* A randomized >=200-step event sequence over a realistic synthetic chip
  (``_ram_chip``): after EVERY step the incremental lint, the chunked
  stored-as-text scan and the chunked env analysis equal a cold recompute.
* The all-values ETag answers a 304 without building rows, and a policy
  re-attach moves it even at an unchanged assignment count (F6).
* The scheduler guard no longer imports the autofit engine on the first edit.
* The sidebar project submenu equals the full listing's view of the same
  config, with a stat per project instead of a TOML read.
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import diagnostics as D
from quam_state_manager.core import state_env_validate as V
from quam_state_manager.core import store_revs as SR
from quam_state_manager.core.loader import QuamStore, flatten
from quam_state_manager.core.modifier import Modifier

from tests import _ram_chip

_GOLDEN = Path(__file__).resolve().parent / "golden" / "state_schema_modern.json"


def _store(n=6, seed=1):
    s, w = _ram_chip.build(n, seed)
    return QuamStore.from_dicts(s, w)


# ---------------------------------------------------------------------------
# the change feed
# ---------------------------------------------------------------------------

class TestChangeFeed:
    def test_one_event_per_seq_and_plain_classification(self):
        st = _store()
        m = Modifier(st)
        s0 = st.mutation_seq
        m.set_value("qubits.q1.T1", 1e-5)                                 # plain
        m.set_value("qubits.q1.xy.operations.x180", "#./x90_DragCosine")  # pointer
        m.create_subtree("qubits.q1.extras.new", 1)                      # structural
        m.set_value("qubits.q2.chi", "text", coerce=False, enforce=False) # plain (text)
        ev = SR.changes_since(st, s0)
        assert [e[0] for e in ev] == [s0 + 1, s0 + 2, s0 + 3, s0 + 4]
        assert [e[2] for e in ev] == [True, False, False, True]
        assert SR.plain_paths(ev) is None
        assert SR.plain_paths(ev[3:]) == ["qubits.q2.chi"]

    def test_class_and_container_writes_are_structural(self):
        st = _store()
        m = Modifier(st)
        s0 = st.mutation_seq
        m.set_value("qubits.q1.__class__", "x.Y", coerce=False, enforce=False)
        m.set_value("qubits.q1.resonator.confusion_matrix", [[1, 0], [0, 1]],
                    coerce=False, enforce=False)
        assert [e[2] for e in SR.changes_since(st, s0)] == [False, False]

    def test_undo_is_recorded_with_its_real_before_value(self):
        st = _store()
        m = Modifier(st)
        m.set_value("qubits.q1.xy.operations.x180", "#./x90_DragCosine")
        s0 = st.mutation_seq
        m.undo()      # pointer -> pointer: structural, even though it is a "set"
        assert [e[2] for e in SR.changes_since(st, s0)] == [False]
        m.set_value("qubits.q1.T1", 3e-5)
        s1 = st.mutation_seq
        m.undo()      # scalar back to scalar: plain
        assert [e[2] for e in SR.changes_since(st, s1)] == [True]

    def test_an_unexplained_seq_bump_is_never_vouched_for(self):
        st = _store()
        s0 = st.mutation_seq
        tok = SR.chunk_token(st, "qubits", "q3")
        st.merged["qubits"]["q3"]["T1"] = 1.0      # a write the hooks never saw
        st.mutation_seq += 1
        assert SR.changes_since(st, s0) is None or \
            SR.plain_paths(SR.changes_since(st, s0)) is None
        assert SR.chunk_token(st, "qubits", "q3") != tok     # global move

    def test_a_log_that_no_longer_reaches_back_is_none(self, monkeypatch):
        st = _store()
        m = Modifier(st)
        s0 = st.mutation_seq
        r = SR.revs_of(st)
        for i in range(SR.LOG_MAX + 5):
            m.set_value("qubits.q1.T1", 1e-6 * (i + 1))
        assert SR.changes_since(st, s0) is None
        assert len(r.events) == SR.LOG_MAX

    def test_chunk_tokens_move_only_for_their_own_chunk(self):
        st = _store()
        m = Modifier(st)
        t1, t2 = SR.chunk_token(st, "qubits", "q1"), SR.chunk_token(st, "qubits", "q2")
        m.set_value("qubits.q1.T1", 2e-5)
        assert SR.chunk_token(st, "qubits", "q1") != t1
        assert SR.chunk_token(st, "qubits", "q2") == t2
        m.set_value("extras.chip_name", "x")                  # depth 2: global
        assert SR.chunk_token(st, "qubits", "q2") != t2

    def test_structure_token_ignores_plain_writes(self):
        st = _store()
        m = Modifier(st)
        s = SR.struct_token(st)
        m.set_value("qubits.q1.T1", 2e-5)
        assert SR.struct_token(st) == s
        m.set_value("qubit_pairs.q1-2.qubit_control", "#/qubits/q3")
        assert SR.struct_token(st) != s

    def test_path_closure_follows_pointers_transitively(self):
        st = _store()
        deps = SR.path_closure(st, ["qubit_pairs.q1-2.macros"])
        # the macro's flux pulse lives in q1's z channel
        assert "qubits.q1.z.operations.cz_flattop_q1_q2" in deps
        assert SR.path_affected(deps, "qubits.q1.z.operations.cz_flattop_q1_q2.amplitude")
        assert not SR.path_affected(deps, "qubits.q1.T1")

    def test_a_different_store_is_a_different_serial(self):
        a, b = _store(), _store()
        assert SR.store_serial(a) != SR.store_serial(b)
        assert SR.seq_token(a) != SR.seq_token(b)


# ---------------------------------------------------------------------------
# the randomized staleness sequence
# ---------------------------------------------------------------------------

def _dump(fs):
    return [f.as_dict() for f in fs]


def _manifest():
    from quam_state_manager.core.state_env_schema import _decorate
    return _decorate(json.loads(_GOLDEN.read_text(encoding="utf-8")))


def _random_step(rng, st, m, nums, strs, ptrs, step):
    x = rng.random()
    if x < 0.40:
        p = rng.choice(nums)
        v = st.get_value(p)
        nv = rng.choice([v * 1.37 if isinstance(v, (int, float)) and v else 0.5,
                         float("nan"), 1e9, -3.0, 0.0])
        m.set_value(p, nv, coerce=False, enforce=False)
    elif x < 0.48:
        m.set_value(rng.choice(nums), rng.choice(["0.25", "abc"]), coerce=False, enforce=False)
    elif x < 0.54:
        m.set_value(rng.choice(strs), rng.choice(["12", "zz", None]), coerce=False, enforce=False)
    elif x < 0.58:
        m.set_value(rng.choice(nums), None, coerce=False, enforce=False)
    elif x < 0.63:
        m.set_value(rng.choice(ptrs), rng.choice(["#/qubits/nowhere/x", "#./x90_DragCosine",
                                                  "#/qubits/q2"]), coerce=False, enforce=False)
    elif x < 0.68:
        p = rng.choice(nums)
        m.create_subtree(p.rsplit(".", 1)[0] + f".zz{step}", rng.choice([1.5, "7", {"a": 1}]))
    elif x < 0.72:
        p = rng.choice(nums)
        if p.count(".") >= 3:
            m.delete_subtree(p)
    elif x < 0.78 and st.change_log:
        m.undo()
    elif x < 0.82 and st.change_log:
        m.discard(rng.randrange(len(st.change_log)))
    elif x < 0.85:
        k = rng.choice([p for p in flatten(st.merged) if p.endswith("__class__")])
        m.set_value(k, rng.choice([_ram_chip.SQUARE, _ram_chip.DRAG]), coerce=False, enforce=False)
    elif x < 0.88:
        # a write the hooks never see (the seq guard must catch it)
        p = rng.choice(nums)
        parent, leaf = p.rsplit(".", 1)
        node = st.merged
        for seg in parent.split("."):
            node = node[int(seg)] if isinstance(node, list) else node[seg]
        if isinstance(node, dict):
            node[leaf] = 4.25
        st.mutation_seq += 1
    elif x < 0.94:
        # values near the DAC bounds, and the port mode that sets the bound --
        # a finding moves when EITHER side moves
        amps = [p for p in nums if p.endswith(".amplitude")]
        modes = [p for p in strs if p.endswith(".output_mode")]
        if rng.random() < 0.5 and modes:
            m.set_value(rng.choice(modes), rng.choice(["direct", "amplified"]),
                        coerce=False, enforce=False)
        else:
            m.set_value(rng.choice(amps), rng.choice([0.4, 0.6, 1.2, 3.0, -0.7]),
                        coerce=False, enforce=False)
    else:
        m.set_value(rng.choice(nums), rng.random(), coerce=False, enforce=False)


@pytest.mark.parametrize("seed", [11, 12])
def test_randomized_sequence_equals_cold_after_every_step(seed, monkeypatch):
    """>= 200 random events of every kind; after EACH the incremental answer
    equals a cold recompute (lint, stored-as-text, env analysis)."""
    monkeypatch.delenv("SM_RAM_VERIFY", raising=False)
    st = _store(7, seed)
    m = Modifier(st)
    man = _manifest()
    rng = random.Random(seed)
    flat = flatten(st.merged)
    nums = [p for p, v in flat.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    strs = [p for p, v in flat.items() if isinstance(v, str) and not v.startswith("#")
            and not p.endswith("__class__")]
    ptrs = [p for p, v in flat.items() if isinstance(v, str) and v.startswith("#")]
    D.lint_state(st)
    V.analysis_for_store(st, man)
    done = 0
    for step in range(260):
        try:
            _random_step(rng, st, m, nums, strs, ptrs, step)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        done += 1
        inc = D.lint_state(st)
        with st._lock:
            cold = D._lint_state_uncached(st, incremental=False)
        assert _dump(inc) == _dump(cold), f"lint diverged at step {step}"
        assert D.numeric_string_leaves(st.state, st) == D.numeric_string_leaves(st.state)
        assert D.numeric_string_leaves(st.merged, st) == D.numeric_string_leaves(st.merged)
        a_inc, a_cold = V.analysis_for_store(st, man), V.analyze_state(st.state, man)
        assert a_inc == a_cold, f"env analysis diverged at step {step}"
        # the chunked types are kept as parts (merged on first read): same
        # entries in the SAME order as the cold walk's insertion order
        assert list(a_inc["types"].items()) == list(a_cold["types"].items()), \
            f"env types diverged at step {step}"
    assert done >= 200


def test_chunked_types_keep_the_cold_order_around_root_scalars(monkeypatch):
    """A chunked analysis keeps its types as ordered parts: typed leaves the
    walk writes OUTSIDE any chunk (before the first, after the last) sit
    exactly where a cold walk puts them, whether a chunk was walked fresh or
    replayed from its memo."""
    from quam_state_manager.core.state_env_schema import _decorate
    monkeypatch.delenv("SM_RAM_VERIFY", raising=False)
    raw = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    fspec = {"default": None, "has_default": False, "optional": False, "raw": "<class 'float'>",
             "type": {"base": "float", "class": None, "enum": None, "item": None,
                      "optional": False, "raw": "<class 'float'>", "union": None}}
    raw["classes"]["quam_config.my_quam.Quam"].update(importable=True, fields={"a_head": fspec, "z_tail": fspec})
    man = _decorate(raw)
    s, w = _ram_chip.build(6, 1)
    s = {"__class__": s["__class__"], "a_head": 1.5,
         **{k: v for k, v in s.items() if k != "__class__"}, "z_tail": 2.5}
    st = QuamStore.from_dicts(s, w)
    for step in range(3):                 # fresh chunks, then replayed ones
        if step:
            Modifier(st).set_value("qubits.q1.T1", 1e-5 * (step + 1))
        a_inc, a_cold = V.analysis_for_store(st, man), V.analyze_state(st.state, man)
        cold = list(a_cold["types"].items())
        assert cold[0][0] == "a_head" and cold[-1][0] == "z_tail"
        assert list(a_inc["types"].items()) == cold, f"types order diverged at step {step}"


def test_shadow_mode_raises_on_a_stale_chunk(monkeypatch):
    """SM_RAM_VERIFY compares every chunked analysis with a cold one: a chunk
    served under a token that did not move when it should have is caught."""
    st = _store()
    man = _manifest()
    V.analysis_for_store(st, man)
    monkeypatch.setenv("SM_RAM_VERIFY", "1")
    # corrupt: change content WITHOUT any seq or token movement, then force a
    # new seq through a hooked write elsewhere so the outer memo misses
    st.state["qubits"]["q4"]["xy"]["operations"]["x180_DragCosine"]["length"] = "forty"
    Modifier(st).set_value("qubits.q1.T1", 5e-5)
    from quam_state_manager.core.ramcache import StaleCacheError
    with pytest.raises(StaleCacheError):
        V.analysis_for_store(st, man)


def test_incremental_lint_recomputes_only_what_an_edit_can_reach(monkeypatch):
    """The point of P5: after a plain edit to one qubit's T1, no pulse is
    re-synthesized and no entity is re-walked."""
    st = _store(8, 3)
    D.lint_state(st)
    calls = {"wf": 0}
    real = D._waveform_findings_ex

    def counted(store, only=None):
        calls["wf"] += 1
        return real(store, only=only)
    monkeypatch.setattr(D, "_waveform_findings_ex", counted)
    Modifier(st).set_value("qubits.q3.T1", 7e-5)
    D.lint_state(st)
    assert calls["wf"] == 0
    Modifier(st).set_value("qubits.q3.xy.operations.x180_DragCosine.amplitude", 0.9)
    D.lint_state(st)
    assert calls["wf"] == 1                     # q3 only: no pair pulse points at it
    Modifier(st).set_value("qubits.q3.z.operations.cz_flattop_q3_q4.amplitude", 0.3)
    D.lint_state(st)
    assert calls["wf"] == 3                     # q3 itself + the pair q3-4


def test_a_pair_pulse_follows_its_control_qubits_port_mode():
    """The pair's CZ flux pulse plays through the CONTROL qubit's z line, read
    by name (``qubit_control`` -> ``qubits.<ctrl>.z.opx_output``) and not
    through any pointer the pulse itself carries. Flipping that port's
    output_mode (a plain string write, far from the pair) must still move the
    pair's finding."""
    st = _store(4, 5)
    m = Modifier(st)
    slot = "qubit_pairs.q1-2.macros.cz_flattop.flux_pulse_qubit"
    # an INLINE pulse in the macro slot (not an alias into q1.z): its row is
    # owned by the pair, and its port is found through qubit_control
    m.set_value(slot, {"__class__": _ram_chip.SQUARE, "length": 48, "amplitude": 0.8},
                coerce=False, enforce=False)
    port = st.get_value("wiring.qubits.q1.z.opx_output")[2:].replace("/", ".")
    m.set_value(port + ".output_mode", "direct")
    first = [f.location for f in D.lint_state(st) if f.category == "waveform_range"]
    assert slot in first
    m.set_value(port + ".output_mode", "amplified")
    inc = D.lint_state(st)
    with st._lock:
        cold = D._lint_state_uncached(st, incremental=False)
    assert _dump(inc) == _dump(cold)
    assert slot not in [f.location for f in inc if f.category == "waveform_range"]


# ---------------------------------------------------------------------------
# web: all-values ETag first, subnav, scheduler guard
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    from quam_state_manager.web.app import create_app
    s, w = _ram_chip.build(5, 2)
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(s), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(w), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(chip)})
    assert r.status_code in (200, 302)
    return c


class TestAllValues:
    def test_a_revalidation_builds_nothing(self, client, monkeypatch):
        from quam_state_manager.core import all_values
        e1 = client.get("/bulk/all-values").headers["ETag"]
        n = {"k": 0}
        real = all_values.build_all_values_rows

        def counted(*a, **kw):
            n["k"] += 1
            return real(*a, **kw)
        monkeypatch.setattr(all_values, "build_all_values_rows", counted)
        r = client.get("/bulk/all-values", headers={"If-None-Match": e1})
        assert r.status_code == 304 and n["k"] == 0
        r = client.get("/bulk/all-values")            # 200 from the RAM body
        assert r.status_code == 200 and n["k"] == 0
        client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.q1.T1", "value": "3e-5"}]})
        r = client.get("/bulk/all-values", headers={"If-None-Match": e1})
        assert r.status_code == 200 and n["k"] == 1

    def test_a_policy_reattach_moves_the_etag_at_an_equal_assignment_count(self, client):
        from quam_state_manager.core import type_policy
        from quam_state_manager.web import routes as R
        e1 = client.get("/bulk/all-values").headers["ETag"]
        ctx = client.application.config["contexts"]
        store = next(c["store"] for c in ctx.values() if c.get("store") is not None)
        old = store.type_policy
        store.type_policy = type_policy.TypePolicy(
            getattr(old, "manifest", None), dict(getattr(old, "assignments", {}) or {}))
        e2 = client.get("/bulk/all-values").headers["ETag"]
        assert e1 != e2

    def test_the_etag_carries_a_process_token(self, client):
        from quam_state_manager.web import routes as R
        e1 = client.get("/bulk/all-values").headers["ETag"]
        assert R._BOOT_TOKEN in e1


def test_the_first_edit_does_not_import_the_autofit_engine(client, monkeypatch):
    for k in [k for k in sys.modules if k.startswith("quam_state_manager.core.autofit.engine")]:
        monkeypatch.delitem(sys.modules, k)
    pkg = sys.modules.get("quam_state_manager.core.autofit")
    if pkg is not None:
        # `from package import engine` would otherwise find the attribute
        monkeypatch.delattr(pkg, "engine", raising=False)
    r = client.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.q1.T1", "value": "4e-5"}]})
    assert r.status_code == 200
    assert "quam_state_manager.core.autofit.engine" not in sys.modules


def _half_built_engine(monkeypatch, *, with_locks_chip):
    """Put the engine into sys.modules the way a concurrent importer leaves
    it: present, `__spec__._initializing` True, and its body not yet run
    past some point. A thread holds the module's REAL import lock and, after
    a pause, finishes the body (names appear, the flag drops) -- the same
    order CPython's `_load_unlocked` uses."""
    import importlib.machinery
    import threading
    import time
    import types
    from importlib import _bootstrap
    from quam_state_manager.core.autofit import engine as real
    name = "quam_state_manager.core.autofit.engine"
    stub = types.ModuleType(name)
    spec = importlib.machinery.ModuleSpec(name, None)
    spec._initializing = True
    stub.__spec__ = spec
    if with_locks_chip:
        # defined, but what it calls is further down the body -- not yet run
        stub.locks_chip = lambda inst: stub.get_engine(inst) is not None
    monkeypatch.setitem(sys.modules, name, stub)
    holding, done = threading.Event(), {}

    def importer():
        with _bootstrap._ModuleLockManager(name):
            holding.set()
            time.sleep(0.4)
            stub.get_engine = real.get_engine
            stub.locks_chip = real.locks_chip
            spec._initializing = False
            done["t"] = time.monotonic()

    t = threading.Thread(target=importer, daemon=True)
    t.start()
    assert holding.wait(5)
    return t, done


@pytest.mark.parametrize("with_locks_chip", [False, True],
                         ids=["no-attribute-yet", "attribute-but-body-unfinished"])
def test_a_half_built_engine_is_waited_for_never_read(client, monkeypatch,
                                                      with_locks_chip):
    """w7 final QA (P2): every open page's first /scheduler/status poll
    imports the engine (~1 s). The P5 fast path read `sys.modules` directly,
    where the module sits HALF-BUILT for that whole second, so the first edit
    after a server start raised AttributeError -> HTTP 500 (5/5 on the rig).
    The guard must wait for the import to finish, like a real import does."""
    import time
    t, done = _half_built_engine(monkeypatch, with_locks_chip=with_locks_chip)
    r = client.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.q1.T1", "value": "4e-5"}]})
    answered = time.monotonic()
    t.join(5)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    # it WAITED for the body to finish, not raced past it
    assert "t" in done and done["t"] <= answered


# ---------------------------------------------------------------------------
# the sidebar project submenu
# ---------------------------------------------------------------------------

def _subnav_views(app):
    from quam_state_manager.web import routes as R
    with app.test_request_context("/qualibrate/subnav"):
        full = R._qualibrate_listing()
        lite = R._qualibrate_subnav_listing()

    def v(listing):
        return {p["name"]: (p["active"], p["state_path"]["raw"], p["state_path"]["native"],
                            p["state_path"]["exists"], p["loaded_in_sm"])
                for p in listing["projects"]}
    return v(full), v(lite), full.get("config_exists"), lite.get("config_exists")


@pytest.mark.usefixtures("any_project_env_chosen")   # opening needs a chosen env
def test_the_submenu_equals_the_full_listing_and_reads_no_toml(tmp_path, monkeypatch):
    from tests.test_qualibrate_routes import _tree
    from quam_state_manager.core import qualibrate_config as QC
    from quam_state_manager.web.app import create_app
    paths = _tree(tmp_path)
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(paths["cfg"]))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    full, lite, ce1, ce2 = _subnav_views(app)
    assert full == lite and ce1 == ce2 is True
    # a chip opened in SM: the [SM] marker agrees
    assert c.post("/qualibrate/open", data={"project": "beta"}).status_code == 302
    full, lite, _, _ = _subnav_views(app)
    assert full == lite and lite["beta"][4] is True
    # a folder appearing with NO config change flips `exists` at once
    (tmp_path / "chips" / "missing").mkdir()
    full, lite, _, _ = _subnav_views(app)
    assert full == lite and lite["alpha"][3] is True
    # steady state reads no TOML at all
    n = {"k": 0}
    real = QC._load_toml

    def counted(p):
        n["k"] += 1
        return real(p)
    monkeypatch.setattr(QC, "_load_toml", counted)
    assert c.get("/qualibrate/subnav").status_code == 200
    assert n["k"] == 0


# ---------------------------------------------------------------------------
# RAM P6: the Live-Edit grids patch themselves (routes._grid_memo)
# ---------------------------------------------------------------------------

def _grid_state(R, st, ctx):
    """Every grid the /bulk page renders, via the memo (patched) and cold."""
    mod = R._modified_map_of(st)
    q = R._bulk_grid_entry(st, set(), mod, ctx)["grid"]
    p = R._pair_grid_entry(st, mod, ctx)["grid"]
    e = R._extra_grids_entry(st, mod, "state", ctx)["grid"]
    cq = R._qubit_bulk_grid(st, set(), mod)
    cp = R._pair_bulk_grid(st, mod)
    return (q, p, e), (cq, cp)


def _canon(x):
    return json.dumps(x, sort_keys=True, default=repr)


def _cold_extras(R, st, doc):
    from quam_state_manager.core import entity_grids
    mod = R._modified_map_of(st)
    out = []
    for spec in entity_grids.discover(st.merged, doc):
        cols, groups, rows = R._entity_bulk_grid(st, spec["root"], spec["ids"], mod,
                                                 spec["expand_ports"])
        if cols and rows:
            out.append({"key": spec["key"], "root": spec["root"], "label": spec["label"],
                        "columns": cols, "column_groups": groups, "rows": rows})
    return out


def _grid_step(rng, st, m, nums, strs, ptrs, step):
    """The lint pin's event mix plus the writes the grids read OUTSIDE a
    cell's own leaf: the FSP an amplitude's dBm reads, LO bands and
    frequencies (the LO-peer pass), grid_location (the row picker), a list
    ELEMENT (a list cell reads the whole list), None flips (a column null on
    every entity is dropped) and a change-log reset without a seq move."""
    x = rng.random()
    now = flatten(st.merged)
    flat = [p for p in nums if p in now]
    if x < 0.12:
        fsp = [p for p in flat if p.endswith("full_scale_power_dbm")]
        if fsp:
            m.set_value(rng.choice(fsp), rng.choice([-11, -8, 1, -20.5]), coerce=False, enforce=False)
    elif x < 0.22:
        lo = [p for p in flat if p.rsplit(".", 1)[-1] in ("band", "upconverter_frequency")]
        if lo:
            m.set_value(rng.choice(lo), rng.choice([1, 2, 3, 5.1e9, 6.2e9]), coerce=False, enforce=False)
    elif x < 0.27:
        q = rng.choice(sorted(st.merged["qubits"]))
        m.set_value(f"qubits.{q}.grid_location", rng.choice(["0,0", "2,1", None, "x"]),
                    coerce=False, enforce=False)
    elif x < 0.32:
        cm = [p for p in flat if ".confusion" in p]
        if cm:
            m.set_value(rng.choice(cm), rng.random(), coerce=False, enforce=False)
    elif x < 0.37:
        # every qubit's copy of one leaf to None, then back: the column drops
        leaf = rng.choice(["T2echo", "chi", "freq_vs_flux_01_quad_term"])
        for q in sorted(st.merged["qubits"]):
            if leaf in st.merged["qubits"][q]:
                m.set_value(f"qubits.{q}.{leaf}", None if rng.random() < 0.8 else 1.5,
                            coerce=False, enforce=False)
    elif x < 0.40 and st.change_log:
        del st.change_log[rng.randrange(len(st.change_log))]     # a reset: no seq move
    else:
        _random_step(rng, st, m, nums, strs, ptrs, step)


@pytest.mark.parametrize("seed", [21, 22])
def test_patched_grids_equal_a_cold_build_after_every_step(seed):
    """>= 200 random events of every kind: after EACH, the memo's qubit, pair
    and extra grids equal a cold build -- and plain writes were actually
    served by the patch (a memo that always rebuilt would pass the equality
    and fail the counter)."""
    from quam_state_manager.web import routes as R
    st = _store(7, seed)
    m = Modifier(st)
    rng = random.Random(seed)
    flat = flatten(st.merged)
    nums = [p for p, v in flat.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    strs = [p for p, v in flat.items() if isinstance(v, str) and not v.startswith("#")
            and not p.endswith("__class__")]
    ptrs = [p for p, v in flat.items() if isinstance(v, str) and v.startswith("#")]
    ctx: dict = {}
    _grid_state(R, st, ctx)
    serials = []
    done = 0
    for step in range(240):
        try:
            _grid_step(rng, st, m, nums, strs, ptrs, step)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            continue
        done += 1
        (q, p, e), (cq, cp) = _grid_state(R, st, ctx)
        # canonical JSON: a NaN (a nan dBm) is equal to itself here, as it is
        # to the page, which renders both the same
        assert _canon(q) == _canon(cq), f"qubit grid diverged at step {step}"
        assert _canon(p) == _canon(cp), f"pair grid diverged at step {step}"
        assert _canon(e) == _canon(_cold_extras(R, st, "state")),             f"extra grids diverged at step {step}"
        serials.append(ctx["bulk_grid_cache"]["serial"])
    assert done >= 200
    # the patch did the serving: far fewer rebuilds than steps
    assert len(set(serials)) < done * 0.6, (len(set(serials)), done)


def test_an_fsp_write_reannotates_the_amplitude_cells_it_feeds():
    """The dBm under an amplitude is FSP + 20*log10|amp|: an FSP write must
    reach the amplitude cell although the cell's own leaf did not move."""
    from quam_state_manager.web import routes as R
    st = _store(4, 3)
    m = Modifier(st)
    ctx: dict = {}
    mod = R._modified_map_of(st)
    ent = R._bulk_grid_entry(st, set(), mod, ctx)
    serial = ent["serial"]
    port = st.merged["wiring"]["qubits"]["q1"]["xy"]["opx_output"][2:].replace("/", ".")
    m.set_value(port + ".full_scale_power_dbm", 4, coerce=False, enforce=False)
    ent = R._bulk_grid_entry(st, set(), R._modified_map_of(st), ctx)
    assert ent["serial"] == serial, "served by the patch"
    cells = {c["dot_path"]: c for r in ent["grid"]["rows"] if r["id"] == "q1" for c in r["cells"]}
    amp = [c for p, c in cells.items() if p.endswith("x180_DragCosine.amplitude")]
    assert amp and amp[0]["phys"]["fsp"] == 4.0
    assert _canon(ent["grid"]) == _canon(R._qubit_bulk_grid(st, set(), R._modified_map_of(st)))


# ---------------------------------------------------------------------------
# RAM P6: /bulk spliced from cached compressed fragments == the classic render
# ---------------------------------------------------------------------------

def _bulk_pair(c, q=""):
    import gzip as _gz
    fr = c.get("/bulk" + q, headers={"Accept-Encoding": "gzip"})
    assert fr.headers.get("Content-Encoding") == "gzip"
    cl = c.get("/bulk" + q)
    assert cl.headers.get("Content-Encoding") is None
    return _gz.decompress(fr.data).decode("utf-8"), cl.get_data(as_text=True)


def test_the_spliced_page_is_byte_identical_to_the_rendered_one(tmp_path, monkeypatch):
    """Warm, after every kind of edit, with and without a viewport hint (a
    different cold set), after an undo and a structural write: the gzip page
    assembled from cached pieces decompresses to EXACTLY the page the
    template renders in one pass. The grid is wide enough to go cold (the
    fragment cache's cold-map blocks and per-variant pieces run)."""
    from quam_state_manager.core import bulk_virt
    from quam_state_manager.web.app import create_app
    monkeypatch.setattr(bulk_virt, "MIN_CELLS", 10)
    monkeypatch.setattr(bulk_virt, "MIN_COLD", 1)
    s, w = _ram_chip.build(6, 5)
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(s), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(w), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    port = w["wiring"]["qubits"]["q2"]["xy"]["opx_output"][2:].replace("/", ".")
    steps = [None, ("qubits.q1.chi", "1.5e6"), ("qubits.q3.T1", "3e-5"),
             (port + ".full_scale_power_dbm", "4"), (port + ".band", "3"),
             ("qubits.q2.grid_location", "4,4"), ("qubit_pairs.q1-2.detuning", "7e6"),
             "undo", ("qubits.q1.chi", "123456789012345.6"),     # a column width moves
             "note", ("qubits.q1.extras.brand_new", "5")]
    seen = 0
    for step in steps:
        if step == "undo":
            assert c.post("/undo").status_code in (200, 204, 409)
        elif step == "note":                                    # a row head's note mark
            r = c.post("/note", data={"subject": "qubits.q3", "text": "check T1"})
            assert r.status_code == 200, r.get_data(as_text=True)[:200]
        elif step is not None:
            data = {"dot_path": step[0], "value": step[1]}
            if step[0].endswith("brand_new"):
                data["create"] = "1"
            c.post("/field/edit", data=data)
        for q in ("", "?vw=700", ""):
            fr, cl = _bulk_pair(c, q)
            assert fr == cl, f"spliced page differs after {step!r} {q}"
            seen += 1
        assert "bulk-cold-map" in cl
    assert seen == 3 * len(steps)


# ---------------------------------------------------------------------------
# RAM P5: get_topology recomputes only the nodes / edges whose inputs moved
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [31, 32])
def test_the_incremental_topology_equals_a_cold_one_after_every_step(seed):
    """>= 200 random events (the grid pin's mix: FSP, bands, grid_location,
    list elements, None flips, pointers, structure, undo, unexplained seq
    moves): after each, the part-memoized topology equals a cold one, and the
    parts were actually reused."""
    from quam_state_manager.core import store_revs as SR
    from quam_state_manager.core.query import QueryEngine
    st = _store(7, seed)
    m = Modifier(st)
    rng = random.Random(seed)
    flat = flatten(st.merged)
    nums = [p for p, v in flat.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    strs = [p for p, v in flat.items() if isinstance(v, str) and not v.startswith("#")
            and not p.endswith("__class__")]
    ptrs = [p for p, v in flat.items() if isinstance(v, str) and v.startswith("#")]
    eng = QueryEngine(st)
    eng.get_topology()
    reused = done = 0
    for step in range(240):
        try:
            _grid_step(rng, st, m, nums, strs, ptrs, step)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            continue
        done += 1
        eng.invalidate_cache()
        before = {k: v[2] for k, v in SR.revs_of(st).memo.get("topology_parts", {}).items()}
        inc = eng.get_topology()
        after = SR.revs_of(st).memo.get("topology_parts", {})
        reused += sum(1 for k, v in after.items() if before.get(k) is v[2])
        eng.invalidate_cache()
        cold = eng.get_topology(_parts=False)
        eng.invalidate_cache()
        assert _canon(inc) == _canon(cold), f"topology diverged at step {step}"
    assert done >= 200
    assert reused > done, reused       # the memo served parts, not only rebuilt them


def test_a_topology_node_follows_a_value_it_reads_through_a_pointer():
    """q2's anharmonicity IS q1's (a pointer): a write under q1 must reach the
    kept q2 node, and an unrelated write must not recompute it."""
    from quam_state_manager.core import store_revs as SR
    from quam_state_manager.core.query import QueryEngine
    st = _store(4, 9)
    m = Modifier(st)
    eng = QueryEngine(st)
    q2 = {n["id"]: n for n in eng.get_topology()["nodes"]}["q2"]
    m.set_value("qubits.q4.T1", 1e-4, coerce=False, enforce=False)
    eng.invalidate_cache()
    assert {n["id"]: n for n in eng.get_topology()["nodes"]}["q2"] is q2, "kept"
    m.set_value("qubits.q1.anharmonicity", -1.5e8, coerce=False, enforce=False)
    eng.invalidate_cache()
    now = {n["id"]: n for n in eng.get_topology()["nodes"]}["q2"]
    assert now["anharmonicity"] == -1.5e8
    assert SR.revs_of(st).memo["topology_parts"][("n", "q2")][2] is now


def test_a_structural_event_above_a_read_recomputes_the_topology_part():
    """A delete ABOVE a path a part reads through a pointer is caught by the
    structural-event rule (a path test alone only looks at and above the
    written path)."""
    from quam_state_manager.core.query import QueryEngine
    st = _store(4, 9)
    m = Modifier(st)
    m.set_value("qubits.q2.xy.operations.x180_DragCosine.amplitude",
                "#/qubits/q1/xy/operations/x180_DragCosine/amplitude",
                coerce=False, enforce=False)
    eng = QueryEngine(st)
    eng.get_topology()
    m.delete_subtree("qubits.q1.xy.operations.x180_DragCosine")
    eng.invalidate_cache()
    inc = eng.get_topology()
    eng.invalidate_cache()
    assert _canon(inc) == _canon(eng.get_topology(_parts=False))


# ---------------------------------------------------------------------------
# RAM P6: the Json Tree's state text is composed from kept chunk texts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [41, 42])
def test_the_composed_state_json_equals_json_dumps_after_every_step(seed):
    from quam_state_manager.core import store_revs as SR
    from quam_state_manager.web import routes as R
    st = _store(6, seed)
    m = Modifier(st)
    rng = random.Random(seed)
    flat = flatten(st.merged)
    nums = [p for p, v in flat.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    strs = [p for p, v in flat.items() if isinstance(v, str) and not v.startswith("#")
            and not p.endswith("__class__")]
    ptrs = [p for p, v in flat.items() if isinstance(v, str) and v.startswith("#")]
    assert R._state_json_text(st) == json.dumps(st.state)
    done = reused = 0
    for step in range(240):
        try:
            _grid_step(rng, st, m, nums, strs, ptrs, step)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            continue
        done += 1
        before = dict(SR.revs_of(st).memo.get("state_json", {}))
        assert R._state_json_text(st) == json.dumps(st.state), f"diverged at step {step}"
        after = SR.revs_of(st).memo["state_json"]
        reused += sum(1 for k, v in after.items() if before.get(k) is v)
    assert done >= 200 and reused > done * 5, (done, reused)


def test_templates_are_compiled_before_the_first_request(tmp_path):
    """The first cell commit in a process paid ~100 ms compiling the Review
    tray (measured). create_app starts warm_templates off the request path
    (same gate as the env warm-up); after it, the tray is already compiled."""
    import inspect
    from quam_state_manager.web import app as A
    app = A.create_app(testing=True, instance_path=str(tmp_path / "inst"))
    assert not any(k[1] == "_pending_tray.html" for k in app.jinja_env.cache.keys())
    n = A.warm_templates(app)
    names = {k[1] for k in app.jinja_env.cache.keys()}
    assert "_pending_tray.html" in names and "bulkedit.html" in names and n >= 40, (n, len(names))
    src = inspect.getsource(A.create_app)
    assert "target=warm_templates" in src and 'name="sm-tpl-warm"' in src
