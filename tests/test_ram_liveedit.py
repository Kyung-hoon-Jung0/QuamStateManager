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
        assert V.analysis_for_store(st, man) == V.analyze_state(st.state, man), \
            f"env analysis diverged at step {step}"
    assert done >= 200


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
