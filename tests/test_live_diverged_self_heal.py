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
