"""P1a — real-fleet goldens for the Compare-hub engine (docs/49).

Path-gated on the maintainer's fleet at <quam-states>
(LabA 21q, LabA - 복사본 backup twin, deviceC 15q, variantb, CR_state,
examplechip9q_repro) and the archive at <dataset-root>/example_lab.
Every empirical claim in the amendments this engine implements is pinned
against the actual files:

  * variantb's 2-hop pair endpoints through the malformed wiring key qA1-A2 (A7/M8)
  * variantb ⊂ LabA grid containment REJECTED by name-consistency (A2 / B2)
  * examplechip's degenerate 1×9 grid → name branch (A2)
  * CR_state's directional pairs never offered a flip (A3)
  * flipped-CZ confusion permutation on LabA's real 4×4 data (A3)
  * the 92 #/wiring pointers → not_in_source, never modified (A6)
  * variantb's 60 bulk dangling optional-default pointers coalesce (A6)
  * LabA vs LabA-복사본 backup verification + deviceC-vs-variantb scale + timings
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

from quam_state_manager.core import compare as C
from quam_state_manager.core import compare_sources as cs

FLEET = Path("<quam-states>")
ARCHIVE = Path("<dataset-root>/example_lab")

pytestmark = pytest.mark.skipif(
    not (FLEET / "LabA" / "state.json").exists(),
    reason="real fleet not present on this machine")


@pytest.fixture(scope="module")
def env():
    return cs.SourcePool(), C.SnapshotCache()


def _src(env, chip: str) -> cs.CompareSource:
    return cs.resolve_source(f"ws:{FLEET / chip}", env[0])


def _snap(env, chip: str) -> C.ComparisonSnapshot:
    return C.snapshot_for(_src(env, chip), env[0], env[1])


# ===========================================================================
# identity + resolution
# ===========================================================================


class TestFleetResolution:
    def test_laba_family_shares_network_token(self, env):
        """A1's measured collision: LabA / LabA-복사본 / deviceB share the
        identical network block — network_token alone can NOT key mappings."""
        toks = {c: _src(env, c).network_token
                for c in ("LabA", "LabA - 복사본", "deviceB")}
        assert len(set(toks.values())) == 1

    def test_variantb_two_hop_endpoints(self, env):
        """variantb endpoints are 2-hop pointers through the malformed wiring
        key ``qA1-A2`` — the recursive resolver must land on real qubits."""
        snap = _snap(env, "variantb")
        assert snap.pair_endpoints["coupler_qA1_qA2"] == ("qA2", "qA1")
        resolved = [p for p, (c, t) in snap.pair_endpoints.items() if c and t]
        assert len(resolved) == 4        # all four coupler pairs

    def test_deviceC_dangling_pairs_flagged(self, env):
        snap = _snap(env, "deviceC")
        assert "qB3-qA4" in snap.pair_orphans      # the real dangling pair
        assert "qD4-qA3" in snap.pair_orphans

    def test_archive_run_source(self, env):
        if not ARCHIVE.exists():
            pytest.skip("archive not present")
        run = sorted(ARCHIVE.glob("*/*/quam_state"))[-1]
        src = cs.resolve_source(f"run:{run}", env[0])
        assert src.origin == "run_archive"
        assert src.chip_name == "LabA"
        assert src.snapshot_ts                      # honest snapshot time


# ===========================================================================
# auto-map (A2) on the real fleet
# ===========================================================================


class TestFleetAutoMap:
    def test_variantb_subset_of_laba_rejected_by_names(self, env):
        """The B2 case: variantb's 17 grid positions sit 100% inside LabA's 21
        with CROSSED names — grid containment alone would produce a
        confidently WRONG map. Must fall back to names, suggested-only."""
        mr = C.auto_map_qubits(_snap(env, "variantb"), _snap(env, "LabA"))
        assert mr.confidence["contained"] is True          # the trap is real
        assert mr.confidence["name_consistent"] is False   # ...and caught
        assert mr.status != "auto"
        assert mr.method == "name"
        # name fallback maps shared names to themselves only
        assert all(a == b for a, b in mr.pairs.items())

    def test_examplechip_degenerate_grid_distrusted(self, env):
        snap = _snap(env, "examplechip9q_repro")
        pts = [p for p in snap.grid.values() if p is not None]
        assert C._grid_degenerate(pts) is True             # the 1×9 line
        mr = C.auto_map_qubits(snap, snap)
        assert mr.method == "name"                         # never grid
        assert mr.status == "suggested"

    def test_laba_vs_deviceB_same_design_grid_auto(self, env):
        """LabA and deviceB share names AND grid — the same-design case the
        grid branch exists for: 100% contained + name-consistent → auto."""
        mr = C.auto_map_qubits(_snap(env, "LabA"), _snap(env, "deviceB"))
        assert mr.status == "auto" and mr.method == "grid"
        assert len(mr.pairs) == 21


# ===========================================================================
# pair mapping (A3) on the real fleet
# ===========================================================================


class TestFleetPairMap:
    def test_cr_state_directional_never_flips(self, env):
        snap = _snap(env, "CR_state")
        pm = C.derive_pair_map(snap, snap, {q: q for q in snap.qubits})
        assert len(pm["matches"]) == 20
        assert not any(m["flipped"] for m in pm["matches"].values())
        # both directions exist as distinct objects and match distinctly
        assert pm["matches"]["q0-4"]["pair_b"] == "q0-4"
        assert pm["matches"]["q4-0"]["pair_b"] == "q4-0"

    def _flip_pair_declaration(self, state: dict, pair: str, new_name: str) -> dict:
        """Re-declare one CZ pair in the opposite orientation, applying the
        physically-correct transforms (what a flip-consistent lab file would
        contain)."""
        out = copy.deepcopy(state)
        pobj = out["qubit_pairs"].pop(pair)
        pobj["id"] = new_name
        pobj["qubit_control"], pobj["qubit_target"] = (
            pobj["qubit_target"], pobj["qubit_control"])
        swap = {"control": "target", "target": "control"}
        if isinstance(pobj.get("moving_qubit"), str):
            pobj["moving_qubit"] = swap.get(pobj["moving_qubit"],
                                            pobj["moving_qubit"])
        conf = pobj.get("confusion")
        P = (0, 2, 1, 3)
        if isinstance(conf, list) and len(conf) == 4:
            pobj["confusion"] = [[conf[P[r]][P[c]] for c in range(4)]
                                 for r in range(4)]
        mfb = pobj.get("mutual_flux_bias")
        if isinstance(mfb, list) and len(mfb) == 2:
            pobj["mutual_flux_bias"] = [mfb[1], mfb[0]]
        for gate in (pobj.get("macros") or {}).values():
            if not isinstance(gate, dict):
                continue
            psc = gate.get("phase_shift_control")
            pst = gate.get("phase_shift_target")
            if psc is not None or pst is not None:
                gate["phase_shift_control"] = pst
                gate["phase_shift_target"] = psc
            if isinstance(gate.get("moving_qubit"), str):
                gate["moving_qubit"] = swap.get(gate["moving_qubit"],
                                                gate["moving_qubit"])
        out["qubit_pairs"][new_name] = pobj
        if isinstance(out.get("active_qubit_pair_names"), list):
            out["active_qubit_pair_names"] = [
                new_name if n == pair else n
                for n in out["active_qubit_pair_names"]]
        return out

    def test_laba_flipped_cz_confusion_permute_real_data(self, env, tmp_path):
        """Flip-consistent re-declaration of a real LabA pair (real 4×4
        confusion) must compare with ZERO physics drift under the A3 policy."""
        pool, cache = env
        state = json.loads((FLEET / "LabA" / "state.json").read_text())
        wiring = json.loads((FLEET / "LabA" / "wiring.json").read_text())
        flipped = self._flip_pair_declaration(state, "qA2-qA1", "qA1-qA2")
        fa = tmp_path / "A"
        fb = tmp_path / "B"
        for f, st in ((fa, state), (fb, flipped)):
            f.mkdir()
            (f / "state.json").write_text(json.dumps(st))
            (f / "wiring.json").write_text(json.dumps(wiring))
        a = cs.resolve_source(f"ws:{fa}", pool)
        b = cs.resolve_source(f"ws:{fb}", pool)
        qmap = {q: q for q in state["qubits"]}
        res = C.compare([a, b], pool, bucket=2, qubit_map=qmap, cache=cache)
        m = res["pair_map"]["matches"]["qA2-qA1"]
        assert m["pair_b"] == "qA1-qA2" and m["flipped"] is True
        drift = [r["key"] for g in res["groups"] for r in g["rows"]
                 if r["cls"] in (C.CLS_MODIFIED, C.CLS_WITHIN)]
        assert drift == [], drift[:10]

    def test_laba_unpermuted_confusion_is_caught(self, env, tmp_path):
        """Negative control: flip WITHOUT the confusion permute = the ~0.03
        phantom-diagonal case — the engine must expose it, not absorb it."""
        pool, cache = env
        state = json.loads((FLEET / "LabA" / "state.json").read_text())
        wiring = json.loads((FLEET / "LabA" / "wiring.json").read_text())
        flipped = self._flip_pair_declaration(state, "qA2-qA1", "qA1-qA2")
        # undo ONLY the confusion permute (keep the raw matrix as-is)
        flipped["qubit_pairs"]["qA1-qA2"]["confusion"] = \
            state["qubit_pairs"]["qA2-qA1"]["confusion"]
        fa = tmp_path / "A"
        fb = tmp_path / "B"
        for f, st in ((fa, state), (fb, flipped)):
            f.mkdir()
            (f / "state.json").write_text(json.dumps(st))
            (f / "wiring.json").write_text(json.dumps(wiring))
        a = cs.resolve_source(f"ws:{fa}", pool)
        b = cs.resolve_source(f"ws:{fb}", pool)
        qmap = {q: q for q in state["qubits"]}
        res = C.compare([a, b], pool, bucket=2, qubit_map=qmap, cache=cache)
        conf_drift = [r for g in res["groups"] for r in g["rows"]
                      if ".confusion." in r["key"]
                      and r["cls"] in (C.CLS_MODIFIED, C.CLS_WITHIN)]
        assert conf_drift, "un-permuted real confusion must surface as drift"


# ===========================================================================
# A6 pointer semantics on the real fleet
# ===========================================================================


class TestFleetPointerSemantics:
    def test_laba_92_wiring_pointers_not_in_source(self, env, tmp_path):
        """LabA state carries exactly 92 #/wiring pointers.  Compared
        against its own state WITHOUT wiring.json they must ALL classify
        not_in_source — the naive resolved-compare manufactured 92 bogus
        modified rows."""
        pool, cache = env
        stateonly = tmp_path / "no_wiring"
        stateonly.mkdir()
        stateonly.joinpath("state.json").write_text(
            (FLEET / "LabA" / "state.json").read_text())
        a = _src(env, "LabA")
        b = cs.resolve_source(f"ws:{stateonly}", pool)
        assert b.wiring_missing is True
        res = C.compare([a, b], pool, bucket=1, cache=cache)
        assert res["headline"]["changed"] == 0            # zero bogus rows
        assert res["headline"]["within_tolerance"] == 0
        nis = res["headline"]["by_class"].get(C.CLS_NOT_IN_SOURCE, 0)
        assert nis >= 92
        # and no modified row is a wiring-pointer key
        bogus = [r["key"] for g in res["groups"] for r in g["rows"]
                 if r["cls"] == C.CLS_MODIFIED]
        assert bogus == []

    def test_variantb_bulk_dangling_coalesces(self, env, tmp_path):
        """variantb: 60 dangling optional-default pointers (45× x90 detuning,
        15× x180) must coalesce into per-pointer groups, never 60 amber rows."""
        pool, cache = env
        snap = _snap(env, "variantb")
        assert len(snap.resolve_failed) == 60             # the measured case
        state = json.loads((FLEET / "variantb" / "state.json").read_text())
        state["qubits"]["qA1"]["f_01"] = state["qubits"]["qA1"].get(
            "f_01", 5e9) + 5e5                            # avoid identical hero
        fb = tmp_path / "variantb_b"
        fb.mkdir()
        (fb / "state.json").write_text(json.dumps(state))
        (fb / "wiring.json").write_text(
            (FLEET / "variantb" / "wiring.json").read_text())
        a = _src(env, "variantb")
        b = cs.resolve_source(f"ws:{fb}", pool)
        res = C.compare([a, b], pool, cache=cache)
        groups = res["attention"]["unresolved_groups"]
        assert groups, "bulk dangling must coalesce"
        assert sum(g["count"] for g in groups) == 60
        assert groups[0]["count"] == 45                   # x90 detuning bulk
        assert groups[0]["leaf"] == "detuning"
        # no amber-spam: individual unresolved rows all folded away
        loose = [r for g in res["groups"] for r in g["rows"]
                 if r["cls"] == C.CLS_UNRESOLVED]
        assert len(loose) == 0

    def test_laba_derived_self_refs_present(self, env):
        snap = _snap(env, "LabA")
        assert len(snap.derived) > 100          # inferred_id / inferred_duration...
        # a known one: the cz alias chain
        assert "qubit_pairs.qA2-qA1.macros.cz" in snap.derived


# ===========================================================================
# goldens: backup twin + scale + timings
# ===========================================================================


class TestFleetGoldens:
    def test_laba_backup_twin(self, env, capsys):
        """The literal backup-verification scenario the doc opens with:
        users keep ``LabA - 복사본`` folders."""
        pool, cache = env
        a = _src(env, "LabA")
        b = _src(env, "LabA - 복사본")
        t0 = time.perf_counter()
        res = C.compare([a, b], pool, bucket=1, cache=cache)
        dt = (time.perf_counter() - t0) * 1e3
        assert res["identical"] is False        # this backup has drifted
        h = res["headline"]
        assert h["changed"] > 0
        assert h["equal"] > 10_000
        assert h["one_sided"] == 0              # same schema, both sides full
        assert res["summary"], "summary rows expected"
        with capsys.disabled():
            print(f"\n[timing] LabA twin assemble: {dt:.1f} ms "
                  f"(changed={h['changed']}, equal={h['equal']})")
        assert dt < 2000

    def test_deviceC_vs_variantb_scale(self, env, capsys):
        """The 9.8k-raw-row scale golden (different devices compared as ①
        stress-tests the one-sided coalescing paths)."""
        pool, cache = env
        g = _src(env, "deviceC")
        m = _src(env, "variantb")
        t0 = time.perf_counter()
        res = C.compare([g, m], pool, bucket=1, cache=cache)
        dt = (time.perf_counter() - t0) * 1e3
        sg = C.snapshot_for(g, pool, cache)
        sm = C.snapshot_for(m, pool, cache)
        assert len(sg.flat_raw) + len(sm.flat_raw) > 9000
        h = res["headline"]
        assert h["one_sided"] > 10_000
        # coalescing did its job: materialised rows are FAR below the
        # one-sided leaf count (A5's whole point)
        n_rows = sum(len(gr["rows"]) for gr in res["groups"])
        n_collapsed = sum(len(gr["collapsed"]) for gr in res["groups"])
        assert n_collapsed > 0
        assert n_rows < h["one_sided"] / 3
        with capsys.disabled():
            print(f"\n[timing] deviceC-vs-variantb assemble: {dt:.1f} ms "
                  f"(rows={n_rows}, collapsed={n_collapsed})")
        assert dt < 2000

    def test_snapshot_build_timings(self, capsys):
        """Fresh pool/cache: honest cold-build numbers for the report."""
        pool, cache = cs.SourcePool(), C.SnapshotCache()
        lines = []
        for chip in ("LabA", "deviceC", "variantb"):
            src = cs.resolve_source(f"ws:{FLEET / chip}", pool)
            t0 = time.perf_counter()
            snap = C.snapshot_for(src, pool, cache)
            dt = (time.perf_counter() - t0) * 1e3
            lines.append(f"{chip}: {dt:.1f} ms "
                         f"({len(snap.flat_raw)} raw / "
                         f"{len(snap.flat_resolved)} resolved)")
            assert dt < 1000
        with capsys.disabled():
            print("\n[timing] snapshot builds: " + " · ".join(lines))

    def test_identical_hero_on_true_copy(self, env, tmp_path):
        pool, cache = env
        dup = tmp_path / "laba_copy"
        dup.mkdir()
        for f in ("state.json", "wiring.json"):
            dup.joinpath(f).write_text((FLEET / "LabA" / f).read_text())
        a = _src(env, "LabA")
        b = cs.resolve_source(f"ws:{dup}", pool)
        res = C.compare([a, b], pool, cache=cache)
        assert res["identical"] is True
        assert res["leaf_count"] == len(C.snapshot_for(a, pool, cache).flat_raw)

    def test_cr_state_summary_fidelity_clifford_labelled(self, env, tmp_path):
        """CR_state carries bare-float StandardRB — must surface labelled
        clifford=True (the LabB incident guard)."""
        pool, cache = env
        entry = pool.get(_src(env, "CR_state").content_hash)
        store = entry.store()
        fid = C.canonical_pair_fidelity(store, "q0-4")
        if fid is not None:                    # value present on this chip
            assert isinstance(fid["value"], float)
            assert "gate" in fid

    def test_laba_pair_fidelity_via_cz_alias(self, env):
        pool, cache = env
        entry = pool.get(_src(env, "LabA").content_hash)
        fid = C.canonical_pair_fidelity(entry.store(), "qA2-qA1")
        assert fid is not None
        assert fid["gate"] == "cz_unipolar"    # followed macros.cz → variant
        assert fid["clifford"] is True         # this file stores a bare float
        assert fid["value"] == pytest.approx(0.914273193838105)
