"""Customer report (2026-08-27): after "Apply to chip" on a run, the drift
banner sometimes said the live files "changed on disk" while "Review & sync"
showed "No differences -- the working state matches the live chip". The
banner flag (`live_diverged`) was only ever escalated False->True by the
poll and never lowered, so any transient True outlived the content it
described. These pins: the one-click apply leaves no banner behind under the
poll, and a stale True self-heals once the poll can PROVE the content
matches (clean context, live hash == sync point)."""
from __future__ import annotations

import json

from tests.test_dataset_apply_to_chip import (  # noqa: F401 -- fixture + helpers
    _ctx, _seed_run, _state, _uid, env,
)


def _poll(env):
    """One drift poll with the hash-recheck throttle lifted (the real poll
    is throttled to once per few seconds)."""
    ctx = _ctx(env)
    ctx.pop("_live_hash_checked_at", None)
    r = env["client"].get("/state/drift")
    assert r.status_code == 200
    return ctx


def test_apply_to_chip_leaves_no_drift_banner_under_the_poll(env):
    c = env["client"]
    root = env["tmp"] / "data"
    _seed_run(root, 41, _state(off_a=0.071))
    uid = _uid(env, root, 41)
    r = c.post(f"/dataset/{uid}/load-state?apply=1")
    assert r.status_code == 200, r.data[:300]
    live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    assert live["qubits"]["qA1"]["z"]["joint_offset"] == 0.071, "the push landed"
    for _ in range(3):
        ctx = _poll(env)
        assert ctx.get("live_diverged") is not True, "the poll must not flag SM's own apply"
    tray = c.get("/state/tray").data.decode()
    assert "changed on disk" not in tray


def test_a_stale_true_self_heals_when_content_provably_matches(env):
    """The state the screenshot showed: flag True, content identical."""
    ctx = _ctx(env)
    ctx["live_diverged"] = True                       # however it got there
    ctx = _poll(env)
    assert ctx.get("live_diverged") is False, (
        "a clean context whose live hash equals the sync point must drop the banner")
    tray = env["client"].get("/state/tray").data.decode()
    assert "changed on disk" not in tray


def test_a_real_divergence_is_still_raised_and_kept(env):
    """The heal must never hide a real change: rewrite live out-of-band."""
    st = _state(off_a=0.099)
    (env["live"] / "state.json").write_text(json.dumps(st), encoding="utf-8")
    ctx = _poll(env)
    assert ctx.get("live_diverged") is True
    ctx = _poll(env)
    assert ctx.get("live_diverged") is True, "still diverged -> still flagged"


def test_an_outside_write_is_seen_on_the_next_poll_not_30_s_later(env, monkeypatch):
    """QA regenerate-r2-36: the hash re-check is throttled to once / 30 s, and
    the cheap mtime check its comment relies on ran only on the Chip Status
    poll -- so on every other page an outside write read "Synced" for up to
    30 s and Re-generate built the stale working copy. A MOVED live pair is
    judged on the very next poll (throttle NOT lifted here), and only once."""
    import os
    import time

    from quam_state_manager.core import working_copy

    ctx = _poll(env)                                  # a hash check just ran
    assert ctx.get("live_diverged") is not True
    calls = []
    real = working_copy.live_diverged_now
    monkeypatch.setattr(working_copy, "live_diverged_now",
                        lambda wc: calls.append(1) or real(wc))
    c = env["client"]
    assert c.get("/state/drift").status_code == 200   # nothing moved: throttled
    assert calls == []
    p = env["live"] / "state.json"
    p.write_text(json.dumps(_state(off_a=0.123)), encoding="utf-8")
    t = time.time() + 5
    os.utime(p, (t, t))
    assert c.get("/state/drift").status_code == 200
    assert ctx.get("live_diverged") is True, "an outside write must not wait 30 s"
    assert calls == [1]
    for _ in range(3):                                # same mtimes: no re-hash
        c.get("/state/drift")
    assert calls == [1]


def test_an_unjudged_move_is_judged_again_on_the_next_poll(env, monkeypatch):
    """QA review of r2-36: the moved mtimes were recorded as "checked" BEFORE
    the verdict, so a live pair caught mid-write (live_diverged_now -> None:
    unreadable / torn) was never re-hashed until the 30 s throttle -- exactly
    the actively-writing case the fix targets. The poll now also carries the
    flag, so an open page's pill can stop claiming Synced."""
    import os
    import time

    from quam_state_manager.core import working_copy

    _poll(env)                                        # a hash check just ran
    c = env["client"]
    assert c.get("/state/drift").get_json()["live_diverged"] is False
    verdicts = [None]                                 # first look: torn write
    calls = []
    real = working_copy.live_diverged_now
    monkeypatch.setattr(
        working_copy, "live_diverged_now",
        lambda wc: calls.append(1) or (verdicts.pop(0) if verdicts else real(wc)))
    p = env["live"] / "state.json"
    p.write_text(json.dumps(_state(off_a=0.456)), encoding="utf-8")
    t = time.time() + 5
    os.utime(p, (t, t))
    assert c.get("/state/drift").get_json()["live_diverged"] is False
    assert calls == [1]
    body = c.get("/state/drift").get_json()          # same mtimes, NOT throttled
    assert calls == [1, 1], "an unjudged move must be judged on the next poll"
    assert body["live_diverged"] is True
    c.get("/state/drift")
    assert calls == [1, 1], "a judged move is not re-hashed every poll"


def test_drift_pill_selfcheck_passes():
    """The client half: a pill still reading Synced under a diverged live is
    re-rendered from the poll (node + jsdom drive the REAL app.js)."""
    import shutil
    import subprocess
    from pathlib import Path

    import pytest

    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(["node", str(root / "tests" / "drift_pill_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(root), timeout=180)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "assertions passed" in r.stdout, (r.stdout + r.stderr)


def test_the_poll_carries_the_flag_only_for_a_clean_context(env):
    """A dirty context's refresh returns early, so the poll cannot keep the
    flag current there (test_sync_badge's pin) -- the payload says False and
    the pill (already "Working state") is never asked to repaint from it."""
    (env["live"] / "state.json").write_text(json.dumps(_state(off_a=0.321)),
                                             encoding="utf-8")
    ctx = _poll(env)
    assert ctx.get("live_diverged") is True
    c = env["client"]
    assert c.get("/state/drift").get_json()["live_diverged"] is True
    ctx["working_dirty"] = True
    assert c.get("/state/drift").get_json()["live_diverged"] is False


def test_pending_edits_do_not_silence_an_outside_write(env):
    """QA diagnostics-r2-12: with one staged edit, an outside rewrite of the
    live chip raised no banner at all -- not on the poll, not after F5 --
    because the refresh returned early for every dirty context that had not
    armed an Auto-Sync pull. A dirty context is now RAISE-only: the flag goes
    up, the banner renders on the next full page, the edit survives, nothing
    is pulled, and the flag is never lowered while the edit is pending."""
    c = env["client"]
    r = c.post("/field/edit", data={"dot_path": "qubits.qA1.f_01", "value": "5.1e9"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    ctx = _ctx(env)
    store = ctx["store"]
    assert len(store.change_log) == 1, "the staged edit is what makes it dirty"
    assert not (ctx.get("auto_apply") or {}).get("pull"), "no Auto-Sync pull armed"
    (env["live"] / "state.json").write_text(json.dumps(_state(off_a=0.222)),
                                             encoding="utf-8")
    ctx = _poll(env)
    assert ctx.get("live_diverged") is True, (
        "an outside write must be flagged although edits are pending")
    page = c.get("/diagnostics").get_data(as_text=True)          # the F5
    assert 'id="live-diverged-banner"' in page
    assert len(store.change_log) == 1, "the edit survives"
    assert c.get("/state/drift").get_json()["auto_pull"] is False, "nothing pulls"
    # the live chip goes back to the synced content: a dirty context is never
    # LOWERED by the poll (the explicit sync / apply / discard paths own that)
    (env["live"] / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    ctx = _poll(env)
    assert ctx.get("live_diverged") is True
    assert len(store.change_log) == 1
