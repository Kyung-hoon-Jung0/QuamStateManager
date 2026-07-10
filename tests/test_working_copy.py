"""Tests for quam_state_manager.core.working_copy.

Covers the working-copy lifecycle: seeding from live, mtime-only change
detection, sync (pull), and apply-to-live (push) with the staleness guard.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from quam_state_manager.core import working_copy as wc_mod
from quam_state_manager.core.working_copy import (
    StaleLiveError,
    apply_to_live,
    create,
    discard,
    key_for,
    live_changed,
    load,
    sync_from_live,
)


def _seed(folder, state=None, wiring=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(
        json.dumps(state if state is not None else {"qubits": {}}), encoding="utf-8")
    (folder / "wiring.json").write_text(
        json.dumps(wiring if wiring is not None else {"wiring": {}}), encoding="utf-8")


def _touch_future(path, secs=100):
    """Force a file's mtime forward so a change is unambiguously detectable."""
    t = time.time() + secs
    os.utime(path, (t, t))


# ---------------------------------------------------------------------------
# key_for
# ---------------------------------------------------------------------------

def test_key_for_stable(tmp_path):
    live = tmp_path / "chipA" / "quam_state"
    _seed(live)
    assert key_for(live) == key_for(live)


def test_key_for_same_chip_different_paths(tmp_path):
    live1 = tmp_path / "dir1" / "chipA" / "quam_state"
    live2 = tmp_path / "dir2" / "chipA" / "quam_state"
    _seed(live1)
    _seed(live2)
    k1, k2 = key_for(live1), key_for(live2)
    assert k1 != k2                       # distinct working copies
    assert k1.startswith("chipA-") and k2.startswith("chipA-")


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

def test_create_seeds_working_copy(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"q1": {"f_01": 6e9}}}, wiring={"wiring": {"x": 1}})

    wc = create(inst, live)

    assert (wc.working_folder / "state.json").exists()
    assert (wc.working_folder / "wiring.json").exists()
    assert json.loads((wc.working_folder / "state.json").read_text()) == \
        {"qubits": {"q1": {"f_01": 6e9}}}
    assert wc.working_folder.parent == inst / "working_state"
    assert not live_changed(wc)


def test_create_overwrites_previous(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"q1": {}}})
    create(inst, live)

    _seed(live, state={"qubits": {"q2": {}}})
    wc = create(inst, live)
    assert json.loads((wc.working_folder / "state.json").read_text()) == \
        {"qubits": {"q2": {}}}


# ---------------------------------------------------------------------------
# live_changed
# ---------------------------------------------------------------------------

def test_live_changed_false_initially(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    wc = create(inst, live)
    assert live_changed(wc) is False


def test_live_changed_true_after_live_write(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    wc = create(inst, live)

    (live / "state.json").write_text(json.dumps({"qubits": {"q9": {}}}), encoding="utf-8")
    _touch_future(live / "state.json")
    assert live_changed(wc) is True


# ---------------------------------------------------------------------------
# sync_from_live
# ---------------------------------------------------------------------------

def test_sync_from_live(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"q1": {}}})
    wc = create(inst, live)

    new_state = {"qubits": {"q1": {}, "q2": {}}}
    (live / "state.json").write_text(json.dumps(new_state), encoding="utf-8")
    _touch_future(live / "state.json")
    assert live_changed(wc) is True

    state, wiring = sync_from_live(wc)
    assert state == new_state
    assert json.loads((wc.working_folder / "state.json").read_text()) == new_state
    assert live_changed(wc) is False


# ---------------------------------------------------------------------------
# apply_to_live
# ---------------------------------------------------------------------------

def test_apply_to_live(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"q1": {}}})
    wc = create(inst, live)

    edited = {"qubits": {"q1": {"f_01": 7e9}}}
    (wc.working_folder / "state.json").write_text(json.dumps(edited), encoding="utf-8")

    apply_to_live(wc)
    assert json.loads((live / "state.json").read_text()) == edited
    assert live_changed(wc) is False


def test_apply_to_live_stale_raises(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    wc = create(inst, live)

    # An experiment writes the live file after we loaded it.
    (live / "state.json").write_text(json.dumps({"qubits": {"exp": {}}}), encoding="utf-8")
    _touch_future(live / "state.json")

    with pytest.raises(StaleLiveError):
        apply_to_live(wc)


def test_apply_to_live_force_overwrites(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    wc = create(inst, live)

    edited = {"qubits": {"mine": {}}}
    (wc.working_folder / "state.json").write_text(json.dumps(edited), encoding="utf-8")
    (live / "state.json").write_text(json.dumps({"qubits": {"theirs": {}}}), encoding="utf-8")
    _touch_future(live / "state.json")

    apply_to_live(wc, force=True)
    assert json.loads((live / "state.json").read_text()) == edited


def test_apply_to_live_does_not_advance_synced_on_meta_failure(tmp_path, monkeypatch):
    """Red-team Phase 1 follow-up §4.4: if the post-write meta persist fails,
    the in-memory ``synced_state_mtime`` / ``synced_wiring_mtime`` must NOT
    have been advanced. Otherwise the current session would treat the live
    as in-sync while the on-disk meta still records the pre-apply state,
    and a future ``live_changed`` check would lie."""
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"q1": {}}})
    wc = create(inst, live)
    pre_state_mt = wc.synced_state_mtime
    pre_wiring_mt = wc.synced_wiring_mtime

    edited = {"qubits": {"q1": {"f_01": 7e9}}}
    (wc.working_folder / "state.json").write_text(json.dumps(edited), encoding="utf-8")

    def boom(self, *_a, **_kw):
        raise OSError("simulated meta write failure")

    monkeypatch.setattr(
        "quam_state_manager.core.working_copy.WorkingCopy._write_meta_pair",
        boom,
    )

    from quam_state_manager.core.safe_io import LiveFileError
    with pytest.raises(LiveFileError):
        apply_to_live(wc)

    # In-memory copy still matches the pre-apply meta — no divergence.
    assert wc.synced_state_mtime == pre_state_mt
    assert wc.synced_wiring_mtime == pre_wiring_mt


def test_apply_to_live_raises_if_post_mtime_read_fails(tmp_path, monkeypatch):
    """If we wrote successfully but can't read back the live mtimes (folder
    vanished, permissions flipped), apply_to_live must raise rather than
    silently mark the working copy as in-sync.
    """
    from quam_state_manager.core import working_copy as wc_mod
    from quam_state_manager.core.safe_io import LiveFileError

    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"q1": {}}})
    wc = create(inst, live)

    pre_apply_state_mt = wc.synced_state_mtime

    real_mtimes = wc_mod.safe_io.state_wiring_mtimes
    real_write = wc_mod.safe_io.write_state_wiring
    # Fail the first LIVE-folder mtime stat that happens AFTER the write — i.e.
    # the post-write read-back — regardless of how many reads precede the write
    # (apply now also content-confirms the live before overwriting).
    live_resolved = live.resolve()
    written = {"done": False}

    def tracking_write(folder, state, wiring):
        real_write(folder, state, wiring)
        written["done"] = True

    def flaky_mtimes(folder):
        from pathlib import Path as _P
        if _P(folder).resolve() == live_resolved and written["done"]:
            raise OSError("simulated post-write stat failure")
        return real_mtimes(folder)

    monkeypatch.setattr(wc_mod.safe_io, "write_state_wiring", tracking_write)
    monkeypatch.setattr(wc_mod.safe_io, "state_wiring_mtimes", flaky_mtimes)

    edited = {"qubits": {"q1": {"f_01": 7e9}}}
    (wc.working_folder / "state.json").write_text(json.dumps(edited), encoding="utf-8")

    with pytest.raises(LiveFileError):
        apply_to_live(wc)

    # The synced mtime must NOT have been advanced -- next session must see
    # the divergence and prompt re-sync rather than treat the partial write
    # as authoritative.
    assert wc.synced_state_mtime == pre_apply_state_mt


# ---------------------------------------------------------------------------
# meta / load / discard
# ---------------------------------------------------------------------------

def test_meta_persisted(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    wc = create(inst, live)

    assert wc.meta_path().exists()
    meta = json.loads(wc.meta_path().read_text())
    assert meta["live_folder"] == str(live)


def test_load_reconstructs(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    wc = create(inst, live)

    loaded = load(inst, live)
    assert loaded is not None
    assert loaded.key == wc.key
    assert loaded.synced_state_mtime == wc.synced_state_mtime


def test_load_missing_returns_none(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    assert load(inst, live) is None


def test_discard(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    wc = create(inst, live)

    discard(wc)
    assert not wc.working_folder.exists()
    assert not wc.meta_path().exists()


# ---------------------------------------------------------------------------
# Content hash + reconcile_with_live — the stale-chip fix. A live folder whose
# files are REPLACED out-of-band (different chip dropped in) must be detected
# by content, not just mtime: a clean working copy auto-refreshes, one with
# (possible) edits is kept and flagged, and a legacy (pre-hash) meta is never
# allowed to clobber what might be user edits.
# ---------------------------------------------------------------------------

from quam_state_manager.core.working_copy import (  # noqa: E402
    RECONCILE_IN_SYNC,
    RECONCILE_LIVE_UNREADABLE,
    RECONCILE_STALE,
    RECONCILE_SYNCED,
    content_hash,
    gc_working_copies,
    reconcile_with_live,
    scan_working_copies,
)


def _strip_hash_from_meta(wc):
    """Rewrite the meta sidecar WITHOUT synced_live_hash — a legacy meta."""
    meta = json.loads(wc.meta_path().read_text(encoding="utf-8"))
    meta.pop("synced_live_hash", None)
    wc.meta_path().write_text(json.dumps(meta), encoding="utf-8")


def _replace_live(live, state, wiring=None):
    """Out-of-band replacement of the live files, future mtime (chip swap)."""
    _seed(live, state=state, wiring=wiring)
    _touch_future(live / "state.json")
    _touch_future(live / "wiring.json")


def test_content_hash_ignores_serialization():
    a = content_hash({"b": 1, "a": 2}, {"w": []})
    b = content_hash({"a": 2, "b": 1}, {"w": []})
    assert a == b
    assert a != content_hash({"a": 2, "b": 99}, {"w": []})


def test_create_records_live_hash(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {}}})
    wc = create(inst, live)
    assert wc.synced_live_hash == content_hash({"qubits": {"qA1": {}}}, {"wiring": {}})
    meta = json.loads(wc.meta_path().read_text())
    assert meta["synced_live_hash"] == wc.synced_live_hash


def test_load_roundtrips_live_hash(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    wc = create(inst, live)
    loaded = load(inst, live)
    assert loaded.synced_live_hash == wc.synced_live_hash


def test_load_legacy_meta_has_none_hash(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    wc = create(inst, live)
    _strip_hash_from_meta(wc)
    assert load(inst, live).synced_live_hash is None


def test_reconcile_in_sync_short_circuit(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    wc = create(inst, live)
    assert reconcile_with_live(wc) == RECONCILE_IN_SYNC


def test_reconcile_touch_only_refreshes_mtimes(tmp_path):
    # Same content re-saved (atomic re-write, backup restore of identical
    # data): mtimes move but the hash matches -> in_sync, and the recorded
    # mtimes advance so the cheap stat check goes quiet again.
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    state = {"qubits": {"qA1": {"f_01": 6e9}}}
    _seed(live, state=state)
    wc = create(inst, live)
    _replace_live(live, state)            # identical content, future mtime
    assert live_changed(wc)
    assert reconcile_with_live(wc) == RECONCILE_IN_SYNC
    assert not live_changed(wc)           # mtimes refreshed


def test_reconcile_live_replaced_clean_auto_syncs(tmp_path):
    # THE bug scenario, clean copy: chip qA1 loaded, live replaced by chip
    # q0 -> the working copy auto-refreshes to the new chip.
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {}}})
    wc = create(inst, live)
    _replace_live(live, {"qubits": {"q0": {}, "q1": {}}})
    assert reconcile_with_live(wc) == RECONCILE_SYNCED
    working = json.loads((wc.working_folder / "state.json").read_text())
    assert set(working["qubits"]) == {"q0", "q1"}
    assert wc.synced_live_hash == content_hash({"qubits": {"q0": {}, "q1": {}}},
                                               {"wiring": {}})
    assert not live_changed(wc)


def test_reconcile_different_network_chip_prompts_not_autopull(tmp_path):
    # C30: a clean copy whose live folder is replaced with a chip on a DIFFERENT
    # cluster (network host differs) must NOT silently auto-pull — surface the
    # "live changed — sync?" prompt (STALE) so the user confirms the swap.
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {}}},
          wiring={"wiring": {}, "network": {"host": "10.0.0.1", "cluster_name": "A"}})
    wc = create(inst, live)
    _replace_live(live, {"qubits": {"qB1": {}}},
                  wiring={"wiring": {}, "network": {"host": "10.9.9.9", "cluster_name": "B"}})
    assert reconcile_with_live(wc) == RECONCILE_STALE
    # The working copy is NOT clobbered with the foreign chip.
    working = json.loads((wc.working_folder / "state.json").read_text())
    assert set(working["qubits"]) == {"qA1"}


def test_reconcile_same_network_new_values_still_autosyncs(tmp_path):
    # C30 must NOT block a same-chip (same network) value update — the common
    # qualibrate-fit case still auto-pulls a clean copy.
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    net = {"wiring": {}, "network": {"host": "10.0.0.1", "cluster_name": "A"}}
    _seed(live, state={"qubits": {"qA1": {"f_01": 6.0e9}}}, wiring=net)
    wc = create(inst, live)
    _replace_live(live, {"qubits": {"qA1": {"f_01": 6.3e9}}}, wiring=net)
    assert reconcile_with_live(wc) == RECONCILE_SYNCED
    working = json.loads((wc.working_folder / "state.json").read_text())
    assert working["qubits"]["qA1"]["f_01"] == 6.3e9


def test_reconcile_live_replaced_dirty_keeps_working(tmp_path):
    # Working copy holds a saved edit; live replaced by another chip ->
    # NEVER clobber the edit: keep the working copy, report stale.
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {"f_01": 6e9}}})
    wc = create(inst, live)
    edited = {"qubits": {"qA1": {"f_01": 5e9}}}
    (wc.working_folder / "state.json").write_text(json.dumps(edited), encoding="utf-8")
    _replace_live(live, {"qubits": {"q0": {}}})
    assert reconcile_with_live(wc) == RECONCILE_STALE
    working = json.loads((wc.working_folder / "state.json").read_text())
    assert working == edited              # untouched


def test_reconcile_clean_but_sync_disallowed(tmp_path):
    # Caller with unsaved in-memory edits passes sync_if_clean=False: even a
    # clean on-disk copy must not be replaced under it.
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {}}})
    wc = create(inst, live)
    _replace_live(live, {"qubits": {"q0": {}}})
    assert reconcile_with_live(wc, sync_if_clean=False) == RECONCILE_STALE
    working = json.loads((wc.working_folder / "state.json").read_text())
    assert set(working["qubits"]) == {"qA1"}


def test_reconcile_live_unreadable_serves_working(tmp_path):
    import shutil as _shutil
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {}}})
    wc = create(inst, live)
    _shutil.rmtree(live)
    assert reconcile_with_live(wc) == RECONCILE_LIVE_UNREADABLE
    assert (wc.working_folder / "state.json").exists()


def test_reconcile_legacy_meta_upgraded_when_live_unchanged(tmp_path):
    # Pre-hash meta + live untouched since the sync: the live content IS the
    # sync-point content, so the hash is retrofitted -> future swaps become
    # auto-detectable.
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {}}})
    wc = create(inst, live)
    _strip_hash_from_meta(wc)
    legacy = load(inst, live)
    assert legacy.synced_live_hash is None
    assert reconcile_with_live(legacy) == RECONCILE_IN_SYNC
    assert legacy.synced_live_hash == content_hash({"qubits": {"qA1": {}}},
                                                   {"wiring": {}})
    meta = json.loads(legacy.meta_path().read_text())
    assert meta["synced_live_hash"] == legacy.synced_live_hash


def test_reconcile_legacy_diverged_is_stale(tmp_path):
    # The user's exact LabA case: legacy meta, live replaced with a
    # different chip, working copy holds the old one. A legacy meta cannot
    # prove the working copy is edit-free -> kept + stale (prompt), never
    # silently replaced.
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {}, "qA2": {}}})
    wc = create(inst, live)
    _strip_hash_from_meta(wc)
    legacy = load(inst, live)
    _replace_live(live, {"qubits": {"q0": {}, "q1": {}}})
    assert reconcile_with_live(legacy) == RECONCILE_STALE
    working = json.loads((legacy.working_folder / "state.json").read_text())
    assert set(working["qubits"]) == {"qA1", "qA2"}


def test_reconcile_legacy_working_equals_live_adopts(tmp_path):
    # Legacy meta, live re-written with content identical to the working
    # copy -> nothing to preserve or pull; adopt it and record the hash.
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    state = {"qubits": {"qA1": {}}}
    _seed(live, state=state)
    wc = create(inst, live)
    _strip_hash_from_meta(wc)
    legacy = load(inst, live)
    _replace_live(live, state)
    assert reconcile_with_live(legacy) == RECONCILE_IN_SYNC
    assert legacy.synced_live_hash is not None
    assert not live_changed(legacy)


def test_apply_to_live_updates_hash(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {"f_01": 6e9}}})
    wc = create(inst, live)
    edited = {"qubits": {"qA1": {"f_01": 5e9}}}
    (wc.working_folder / "state.json").write_text(json.dumps(edited), encoding="utf-8")
    apply_to_live(wc)
    assert wc.synced_live_hash == content_hash(edited, {"wiring": {}})
    assert reconcile_with_live(wc) == RECONCILE_IN_SYNC


# ---------------------------------------------------------------------------
# Working-copy GC
# ---------------------------------------------------------------------------

def test_scan_classifies_copies(tmp_path):
    inst = tmp_path / "instance"

    clean_live = tmp_path / "c1" / "quam_state"
    _seed(clean_live)
    create(inst, clean_live)

    dirty_live = tmp_path / "c2" / "quam_state"
    _seed(dirty_live)
    dirty_wc = create(inst, dirty_live)
    (dirty_wc.working_folder / "state.json").write_text(
        json.dumps({"qubits": {"edited": {}}}), encoding="utf-8")

    legacy_live = tmp_path / "c3" / "quam_state"
    _seed(legacy_live, state={"qubits": {"qL": {}}})
    legacy_wc = create(inst, legacy_live)
    _strip_hash_from_meta(legacy_wc)
    _replace_live(legacy_live, {"qubits": {"qX": {}}})   # diverged + unprovable

    broken_live = tmp_path / "c4" / "quam_state"
    _seed(broken_live)
    broken_wc = create(inst, broken_live)
    (broken_wc.working_folder / "state.json").unlink()

    by_key = {r["key"]: r["status"] for r in scan_working_copies(inst)}
    assert by_key[key_for(clean_live)] == "clean"
    assert by_key[key_for(dirty_live)] == "dirty"
    assert by_key[key_for(legacy_live)] == "unverifiable"
    assert by_key[key_for(broken_live)] == "broken"


def test_scan_legacy_clean_provable_against_live(tmp_path):
    # Legacy meta whose working content still equals live -> provably clean.
    inst = tmp_path / "instance"
    live = tmp_path / "c" / "quam_state"
    _seed(live)
    wc = create(inst, live)
    _strip_hash_from_meta(wc)
    assert scan_working_copies(inst)[0]["status"] == "clean"


def test_gc_deletes_only_clean_and_broken(tmp_path):
    inst = tmp_path / "instance"
    clean_live = tmp_path / "c1" / "quam_state"
    _seed(clean_live)
    clean_wc = create(inst, clean_live)
    dirty_live = tmp_path / "c2" / "quam_state"
    _seed(dirty_live)
    dirty_wc = create(inst, dirty_live)
    (dirty_wc.working_folder / "state.json").write_text(
        json.dumps({"qubits": {"edited": {}}}), encoding="utf-8")

    result = gc_working_copies(inst)
    assert result["deleted"] == 1
    assert not clean_wc.working_folder.exists()
    assert not clean_wc.meta_path().exists()
    assert dirty_wc.working_folder.exists()
    assert dirty_wc.meta_path().exists()


def test_gc_respects_keep_keys(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "c1" / "quam_state"
    _seed(live)
    wc = create(inst, live)
    result = gc_working_copies(inst, keep_keys={wc.key})
    assert result["deleted"] == 0
    assert wc.working_folder.exists()


def test_reconcile_live_replaced_with_working_content_adopts(tmp_path):
    # Hash-present meta, working copy holds saved edits, and the live is
    # then replaced with EXACTLY that content (external writer applied the
    # same edits): nothing to pull or preserve -> adopt, no false alarm.
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {"f_01": 6e9}}})
    wc = create(inst, live)
    edited = {"qubits": {"qA1": {"f_01": 5e9}}}
    (wc.working_folder / "state.json").write_text(json.dumps(edited), encoding="utf-8")
    _replace_live(live, edited)                # live now == working
    assert reconcile_with_live(wc) == RECONCILE_IN_SYNC
    assert wc.synced_live_hash == content_hash(edited, {"wiring": {}})
    assert not live_changed(wc)
    # And it stays settled: a second reconcile is a pure stat short-circuit.
    assert reconcile_with_live(wc) == RECONCILE_IN_SYNC


# ---------------------------------------------------------------------------
# Review-hardening round: transient-failure handling + GC guards
# ---------------------------------------------------------------------------

def test_reconcile_working_transient_oserror_keeps_copy(tmp_path, monkeypatch):
    # A transiently locked WORKING copy (AV/backup) is not proof it's
    # worthless -- it may hold saved edits. Keep + prompt, never re-seed.
    from pathlib import Path as _P
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {}}})
    wc = create(inst, live)
    _replace_live(live, {"qubits": {"q0": {}}})
    real = wc_mod.safe_io.read_state_wiring

    def flaky(folder):
        if _P(folder) == wc.working_folder:
            raise OSError("transient AV lock")
        return real(folder)

    monkeypatch.setattr(wc_mod.safe_io, "read_state_wiring", flaky)
    assert reconcile_with_live(wc) == RECONCILE_STALE
    monkeypatch.setattr(wc_mod.safe_io, "read_state_wiring", real)
    working = json.loads((wc.working_folder / "state.json").read_text())
    assert set(working["qubits"]) == {"qA1"}        # untouched


def test_reconcile_live_unreadable_after_move_is_stale(tmp_path, monkeypatch):
    # Step 1's stat PROVES the mtimes moved; only the content read fails.
    # We affirmatively know something changed -> STALE (prompt), not the
    # silent LIVE_UNREADABLE serve.
    from pathlib import Path as _P
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {}}})
    wc = create(inst, live)
    _replace_live(live, {"qubits": {"q0": {}}})
    real = wc_mod.safe_io.read_state_wiring

    def flaky(folder):
        if _P(folder) == _P(wc.live_folder):
            raise OSError("mid-replace lock")
        return real(folder)

    monkeypatch.setattr(wc_mod.safe_io, "read_state_wiring", flaky)
    assert reconcile_with_live(wc) == RECONCILE_STALE


def test_try_sync_failure_restores_presync_pair(tmp_path, monkeypatch):
    # sync_from_live dies after replacing state.json but before wiring.json:
    # the pre-sync pair is put back so the served copy is never a torn
    # never-existed chip (new state + old wiring).
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"qA1": {}}})
    wc = create(inst, live)
    _replace_live(live, {"qubits": {"q0": {}}})

    def torn_sync(w):
        (w.working_folder / "state.json").write_text(
            json.dumps({"qubits": {"q0": {}}}), encoding="utf-8")
        raise OSError("wiring write failed")

    monkeypatch.setattr(wc_mod, "sync_from_live", torn_sync)
    assert reconcile_with_live(wc) == RECONCILE_STALE
    working = json.loads((wc.working_folder / "state.json").read_text())
    assert set(working["qubits"]) == {"qA1"}        # restored pre-sync pair


def test_scan_transient_working_read_is_unverifiable(tmp_path, monkeypatch):
    # Transient OSError during the scan must classify as kept-unverifiable,
    # NOT deletable-broken -- a passing AV lock must never become a deletion.
    inst = tmp_path / "instance"
    live = tmp_path / "c" / "quam_state"
    _seed(live)
    create(inst, live)

    def locked(folder):
        raise OSError("locked")

    monkeypatch.setattr(wc_mod.safe_io, "read_state_wiring", locked)
    recs = scan_working_copies(inst)
    assert recs[0]["status"] == "unverifiable"
    result = gc_working_copies(inst)
    assert result["deleted"] == 0


def test_gc_orphan_grace_skips_fresh_dirs(tmp_path):
    # create() writes the working files BEFORE the meta sidecar -- a fresh
    # meta-less dir may be a load in flight, not junk.
    inst = tmp_path / "instance"
    root = inst / "working_state"
    root.mkdir(parents=True)
    orphan = root / "inflight-12345678"
    orphan.mkdir()
    (orphan / "state.json").write_text("{}", encoding="utf-8")

    result = gc_working_copies(inst)                 # default 600 s grace
    assert result["deleted"] == 0
    assert orphan.exists()

    result = gc_working_copies(inst, orphan_grace_s=0.0)
    assert result["deleted"] == 1
    assert not orphan.exists()


def test_gc_keep_fn_checked_at_delete_time(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "c1" / "quam_state"
    _seed(live)
    wc = create(inst, live)
    calls = []

    def keep_fn():
        calls.append(1)
        return {wc.key}

    result = gc_working_copies(inst, keep_fn=keep_fn)
    assert calls, "keep_fn must be consulted at delete time"
    assert result["deleted"] == 0
    assert wc.working_folder.exists()


# ---------------------------------------------------------------------------
# live_diverged_now — the ground-truth probe that backstops mtime-only detection
# (fixes the "SM stops detecting qualibrate updates" tracking-drift bug).
# ---------------------------------------------------------------------------

def _rewrite_live_same_mtime(live, state=None, wiring=None):
    """Rewrite live CONTENT but pin the mtime back — simulates a coarse / same-
    second external rewrite (editor save, atomic re-save) that the pure-mtime
    live_changed() check cannot see."""
    s = (live / "state.json").stat()
    w = (live / "wiring.json").stat()
    _seed(live, state=state, wiring=wiring)
    os.utime(live / "state.json", ns=(s.st_atime_ns, s.st_mtime_ns))
    os.utime(live / "wiring.json", ns=(w.st_atime_ns, w.st_mtime_ns))


def test_live_changed_misses_same_mtime_rewrite_but_probe_catches_it(tmp_path):
    # THE reproduction of the tracking-drift bug + its fix.
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"q1": {"f": 1}}})
    wc = create(inst, live)
    assert live_changed(wc) is False
    _rewrite_live_same_mtime(live, state={"qubits": {"q1": {"f": 2}}})
    assert live_changed(wc) is False                  # the mtime blind spot
    assert wc_mod.live_diverged_now(wc) is True        # the ground-truth probe catches it


def test_live_diverged_now_false_when_in_sync(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    wc = create(inst, live)
    assert wc_mod.live_diverged_now(wc) is False


def test_live_diverged_now_defers_when_no_baseline(tmp_path):
    # No sync-point baseline (legacy meta): the probe DEFERS (None) rather than risk
    # a spurious "live changed" prompt — it can't tell a live change from the working
    # copy legitimately holding the user's saved-but-unapplied edits.
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"q1": {"f": 1}}})
    wc = create(inst, live)
    wc.synced_live_hash = None
    assert wc_mod.live_diverged_now(wc) is None
    _rewrite_live_same_mtime(live, state={"qubits": {"q1": {"f": 9}}})
    assert wc_mod.live_diverged_now(wc) is None          # still defers without a baseline


def test_live_diverged_now_unreadable_returns_none(tmp_path):
    import shutil
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live)
    wc = create(inst, live)
    shutil.rmtree(live)
    assert wc_mod.live_diverged_now(wc) is None          # caller keeps its prior verdict


def test_live_diverged_now_is_read_only(tmp_path):
    inst = tmp_path / "instance"
    live = tmp_path / "chip" / "quam_state"
    _seed(live, state={"qubits": {"q1": {"f": 1}}})
    wc = create(inst, live)
    meta_before = wc.meta_path().read_text(encoding="utf-8")
    base = (wc.synced_live_hash, wc.synced_state_mtime, wc.synced_wiring_mtime)
    _rewrite_live_same_mtime(live, state={"qubits": {"q1": {"f": 2}}})
    assert wc_mod.live_diverged_now(wc) is True
    assert (wc.synced_live_hash, wc.synced_state_mtime, wc.synced_wiring_mtime) == base
    assert wc.meta_path().read_text(encoding="utf-8") == meta_before
