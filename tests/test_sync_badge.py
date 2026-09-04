"""The sync pill carries the notifications, as a state (docs/167).

The user's directive: notifications must not fire per event, and must not be
popups — "a small visible mark on the sync button is enough". The first half of
delivering that was a deletion: `#new-run-popup` was already showing a card
once per detected run, on every page.

The chip's behaviour is driven under jsdom by `sync_badge_selfcheck.cjs`. What
is asserted here is the server half — that the count comes from a stamp the
CLIENT acknowledged, that it costs no extra I/O, and that a client which cannot
say "since when" gets no number rather than a fabricated one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "sync_badge_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_newrun_poll_selfcheck_passes():
    """The poller itself, driven against a fake server.

    This is the pin for the defect a design review found and the whole feature
    turns on: one variable was doing two jobs, so the count could never
    accumulate past one. It drives the REAL app.js rather than reading it.
    """
    r = subprocess.run(
        ["node", str(_ROOT / "tests" / "newrun_poll_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


class TestTheWiring:
    """Facts about which file talks to which. Cheap to assert, and each one is
    a way the chip silently stops working with everything else still green."""

    def test_the_badge_script_ships_on_every_page(self):
        base = (_ROOT / "quam_state_manager" / "web" / "templates"
                / "base.html").read_text(encoding="utf-8")
        assert "asset_url('sync-badge.js')" in base
        # a CORE script, not a page bundle: the pill is on every page and so
        # are both polls that feed it
        head = base[:base.index("</head>")]
        assert "sync-badge.js" in head

    def test_the_hand_rolled_tray_swap_tells_the_badge(self):
        """_swapPendingTray replaces the tray with outerHTML, which does not
        fire htmx:afterSwap — the reason _restoreTrayState exists. Without the
        event the chip vanishes on the next apply and never comes back."""
        js = (_ROOT / "quam_state_manager" / "web" / "static"
              / "app.js").read_text(encoding="utf-8")
        i = js.index("function _swapPendingTray(html) {")
        body = js[i:i + 2000]
        assert "sm:tray-swapped" in body


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_sync_badge_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


def _client(tmp_path, runs):
    """A client over an archive with the given (date, time) runs."""
    from quam_state_manager.web.app import create_app
    root = tmp_path / "data"
    for i, (d, t) in enumerate(runs):
        run = root / d / f"#{i + 1}_04_power_rabi_{t.replace(':', '')}"
        run.mkdir(parents=True, exist_ok=True)
        (run / "node.json").write_text(
            json.dumps({"id": i + 1, "name": "power_rabi"}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    r = c.post("/workspace/add", data={"folder": str(root)})
    assert r.status_code in (200, 204, 302), r.status_code
    return c


class TestTheCountComesFromAnAcknowledgedStamp:
    RUNS = [("2026-01-01", "10:00:00"), ("2026-01-01", "11:00:00"),
            ("2026-01-02", "09:00:00"), ("2026-01-02", "10:00:00")]

    def test_with_no_stamp_there_is_no_number_at_all(self, tmp_path):
        """Honest degradation: a client that cannot say "since when" must not
        be handed a 0 or a 1 it would then render as truth."""
        c = self._poll(tmp_path)
        assert "new_count" not in c, c

    def test_a_stamp_counts_everything_strictly_after_it(self, tmp_path):
        got = self._poll(tmp_path, "2026-01-01", "10:00:00")
        assert got.get("new_count") == 3, got
        got = self._poll(tmp_path, "2026-01-02", "09:00:00")
        assert got.get("new_count") == 1, got

    def test_the_newest_stamp_counts_nothing(self, tmp_path):
        """The acknowledge case — the chip must be able to reach zero."""
        got = self._poll(tmp_path, "2026-01-02", "10:00:00")
        assert got.get("new_count") == 0, got

    def test_the_count_accumulates_while_the_stamp_stands_still(self, tmp_path):
        """The requirement the feature exists for: a hundred runs are one chip
        reading 100. Two polls from the SAME acknowledged stamp must both see
        everything after it — if the stamp advanced on detection, the second
        would answer 0."""
        first = self._poll(tmp_path, "2026-01-01", "10:00:00")
        second = self._poll(tmp_path, "2026-01-01", "10:00:00")
        assert first["new_count"] == second["new_count"] == 3

    def test_half_a_stamp_is_no_stamp(self, tmp_path):
        for args in ("since_date=2026-01-01", "since_time=10:00:00",
                     "since_date=&since_time=10:00:00"):
            got = self._poll_raw(tmp_path, args)
            assert "new_count" not in got, (args, got)

    def test_the_latest_run_payload_is_unchanged(self, tmp_path):
        """Everything the old client read is still there, byte-for-byte in
        shape — the chip is additive."""
        got = self._poll(tmp_path)
        for k in ("uid", "run_id", "experiment_name", "qubits", "time", "date"):
            assert k in got, k

    # helpers -----------------------------------------------------------
    def _poll_raw(self, tmp_path, query=""):
        c = _client(tmp_path, self.RUNS)
        url = "/datasets/poll" + ("?" + query if query else "")
        return json.loads(c.get(url).get_data(as_text=True))

    def _poll(self, tmp_path, date=None, time=None):
        q = f"since_date={date}&since_time={time}" if date else ""
        return self._poll_raw(tmp_path, q)


class TestTheCountCostsNothing:
    def test_it_is_counted_in_the_walk_that_already_happened(self):
        """A second pass over every run of every folder on a 60-second poll is
        exactly the kind of cost this app has had to remove twice (docs/142,
        docs/155). The counter rides the existing loop."""
        src = (_ROOT / "quam_state_manager" / "web" / "routes.py").read_text(encoding="utf-8")
        body = src[src.index("def datasets_poll("):]
        body = body[:body.index("\n@bp.route")]
        assert body.count("runs_snapshot()") == 1, (
            "the count must ride the existing walk, not add one")
        assert body.count("_active_dataset_stores") == 1


class TestWhatWasDeliberatelyLeftOut:
    """Two announcements were designed and cut, and the reasons are about
    honesty rather than effort. Pinned as absences so a later round has to
    re-argue them rather than re-discover them."""

    def _js(self):
        return (_ROOT / "quam_state_manager" / "web" / "static"
                / "sync-badge.js").read_text(encoding="utf-8")

    def _kinds(self) -> set[str]:
        """The kinds the module will actually render, read from the registry
        rather than from the file — the reasons for the two cuts are WRITTEN in
        this file's comments, so a substring search would find them there and
        the pin would be about prose instead of behaviour."""
        js = self._js()
        block = js[js.index("var KINDS = {"):js.index("var ORDER =")]
        import re
        return set(re.findall(r"^\s{8}'?([A-Za-z_]+)'?: \{", block, re.M))

    def test_only_two_kinds_can_be_rendered(self):
        assert self._kinds() == {"rundone", "new"}, self._kinds()

    def test_live_changed_is_not_announced(self):
        """/state/drift's refresh returns early on a dirty context, so the
        5-second poll cannot keep the flag true there — a chip that is right
        only sometimes is worse than none. The pill's own server-rendered
        `state-status-drifted` covers the clean case honestly."""
        assert "drift" not in self._kinds()
        # The route READS the flag (it calls _refresh_live_diverged), so the
        # pin is on the PAYLOAD: adding the key there is the change that would
        # let a chip claim drift on a context whose flag the poll cannot keep
        # true, and it is the change this pin exists to make somebody argue for.
        import re
        routes = (_ROOT / "quam_state_manager" / "web" / "routes.py").read_text(
            encoding="utf-8")
        drift = routes[routes.index("def state_drift("):][:5200]
        assert '"live_diverged":' not in drift, sorted(
            set(re.findall(r'"[a-z_]+":', drift)))

    def test_needs_human_is_not_announced(self):
        """The engine flag is level-triggered with no clearing path, so the
        chip could be shown but never legitimately dismissed. It needs a
        server-side clear before it can be surfaced."""
        assert "needs_human" not in self._kinds()
        sched = (_ROOT / "quam_state_manager" / "web" / "routes.py").read_text(
            encoding="utf-8")
        body = sched[sched.index("def _sched_state("):][:2000]
        assert "needs_human" not in body

    def test_no_toast_no_modal_no_sound(self):
        js = self._js()
        for banned in ("showToast", "Audio(", "alert(", "ch-overlay"):
            assert banned not in js, f"the chip must not {banned}"


class TestTheRunnerAnnouncementIsTerminalOnly:
    def test_paused_is_not_done(self):
        """`paused` is a first-class runner status; a user pausing their own
        queue must not be told it finished. The terminal set is enumerated
        rather than derived by negating "running"."""
        base = (_ROOT / "quam_state_manager" / "web" / "templates"
                / "base.html").read_text(encoding="utf-8")
        i = base.index('SyncBadge.note("rundone"')
        guard = base[i - 400:i]
        assert 'st === "idle"' in guard and 'st === "done"' in guard
        assert 'st === "failed"' in guard and 'st === "aborted"' in guard
        assert 'st !== "running"' not in guard, (
            "negating 'running' announces a pause as a completion")

    def test_it_never_announces_on_load(self):
        base = (_ROOT / "quam_state_manager" / "web" / "templates"
                / "base.html").read_text(encoding="utf-8")
        assert "if (runStatus === null) {" in base
        assert "runStatus = st;                  // baseline" in base
