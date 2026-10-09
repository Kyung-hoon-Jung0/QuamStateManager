"""S10 walk, round 3 (the v1.2.0 candidate in a real browser) -- the history
surfaces' polish. Each pin is mutation-checked.

1. one display rule for a delta and the values beside it (pinned in
   tests/test_value_delta.py, JS parity included);
3. the link dialog's close control (tests/hub_link_folder_selfcheck.cjs);
4. the Calibration log's unreadable note has the same single manual press;
5. a Take snapshot press that changes nothing writes nothing (and says so),
   the badge follows a press, a state the ledger holds is never "unrecorded",
   one press is never listed twice;
6. the baseline badge names the banner's own baseline time;
7. Revert last apply says what the user did;
8. a run whose saved state could not be read says why in plain words;
9. an apply taken back by Revert last apply is marked "reverted later".
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import hub, hub_lanes, hub_versions, story
from quam_state_manager.web import journal_routes, routes
from tests.test_hub_drawer import chip_state, make_app, write_chip
from tests.test_hub_folder_view import _apply, _edit, _load

pytestmark = pytest.mark.usefixtures("_inline")
HX = {"HX-Request": "true"}
SH = '<div class="sh-entry'


@pytest.fixture
def _inline():
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    hub_lanes.clear_caches()
    yield
    hub.set_inline(old)


def entries(html: str, sep: str = SH) -> list[str]:
    return re.split(re.escape(sep) + r'(?=[ "])', html)[1:]


def text(html: str) -> str:
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&#39;", "'").strip()


@pytest.fixture
def live_chip(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    write_chip(live, chip_state(), None)
    app = make_app(tmp_path)
    c = app.test_client()
    _load(c, live)
    c.get("/state/drift")
    return {"app": app, "client": c, "live": live}


def _snaps(env):
    with env["app"].app_context():
        return routes._history().list_snapshots(env["live"])


# ----------------------------------------------------------------------
# 4. the Calibration log's unreadable note
# ----------------------------------------------------------------------

def test_the_calibration_logs_unreadable_note_has_one_manual_press(tmp_path, monkeypatch):
    from tests.test_calibration_log_hub import DAY, REAL_LEDGER_CONTEXT
    live = tmp_path / "chip" / "quam_state"
    write_chip(live, chip_state(), None)
    app = make_app(tmp_path)
    c = app.test_client()
    _load(c, live)
    monkeypatch.setattr(journal_routes, "_ledger_context", REAL_LEDGER_CONTEXT)
    monkeypatch.setattr(routes, "_hub_chip_dir", lambda path: None)
    app.config["contexts"][app.config["active_context"]].pop("hub_chip_dir", None)
    html = c.get(f"/journal/day?day={DAY}").get_data(as_text=True)
    note = re.search(r'<p class="jr-note warn jr-history-status">(.*?)</p>', html, flags=re.S)
    assert note and "unavailable" in note.group(1)
    again = re.findall(r'<button type="button" class="btn-sm vh-try-again" hx-get="([^"]+)" '
                       r'hx-target="#jr-body" hx-swap="innerHTML">Try again</button>', note.group(1))
    assert again == [f"/journal/day?day={DAY}&amp;q=&amp;author="], note.group(1)
    assert "hx-trigger" not in note.group(1), "one press, never an automatic re-ask"


# ----------------------------------------------------------------------
# 5. Take snapshot
# ----------------------------------------------------------------------

def test_a_press_that_changes_nothing_writes_nothing(live_chip):
    c = live_chip["client"]
    assert c.post("/state-history/snapshot", headers=HX).status_code == 200
    before = len(_snaps(live_chip))
    msg = "No change since the last recorded state — nothing new to list."
    for url in ("/state-history/snapshot", "/api/history/snapshot"):
        r = c.post(url, headers=HX)
        assert msg in r.get_data(as_text=True), url
        assert r.headers.get("HX-Trigger") == "stateHistoryChanged", url
    assert len(_snaps(live_chip)) == before, "no snapshot folder for a press that changes nothing"
    write_chip(live_chip["live"], chip_state(t1=7e-5), None)            # a change outside SM
    r = c.post("/state-history/snapshot", headers=HX)
    assert msg not in r.get_data(as_text=True) and len(_snaps(live_chip)) == before + 1


def test_a_state_the_ledger_holds_is_never_unrecorded(live_chip, monkeypatch):
    """With no snapshot written for a press that adds nothing, "unrecorded"
    (no snapshot holds the live content) must not deny a state the change
    history holds."""
    c = live_chip["client"]
    c.post("/state-history/snapshot", headers=HX)
    with live_chip["app"].app_context():
        hm = routes._history()
        monkeypatch.setattr(type(hm), "snapshot_ts_for_current_content", lambda self, path: None)
        ver = routes._state_version_now(routes._active_ctx())
    assert ver["unmatched"] is False and hub_versions.is_ref(ver["ts"]), ver
    assert "unrecorded" not in c.get("/state/version").get_data(as_text=True)


def test_one_press_is_never_listed_twice(live_chip, monkeypatch):
    """A summary read before a snapshot's import beside rows read after it
    listed the snapshot twice (its event row and an older row): an observed
    event listed is its snapshot; and a ledger that moves under the read is
    read again."""
    c = live_chip["client"]
    write_chip(live_chip["live"], chip_state(t1=6e-5), None)
    c.post("/state-history/snapshot", headers=HX)
    stamp = _snaps(live_chip)[0].timestamp
    real = hub_versions._summary

    def stale(store, directory, snapshots, st, binding=None):
        out = real(store, directory, snapshots, st, binding)
        return dict(out, older=frozenset(out["older"]) | {stamp})     # read before the import
    monkeypatch.setattr(hub_versions, "_summary", stale)
    hub_versions._SUMMARIES.clear()
    html = c.get("/state-history?body=1", headers=HX).get_data(as_text=True)
    assert stamp not in [m for m in re.findall(r'data-ts="([^"]+)"', html)], "no older row for it"
    assert "older snapshot" not in text(html)
    # a token that moves under the read: the summary is read again
    monkeypatch.setattr(hub_versions, "_summary", real)
    hub_versions._SUMMARIES.clear()
    calls, tokens = [], iter(range(1000))
    real_token = hub_versions._version_token
    monkeypatch.setattr(hub_versions, "_version_token",
                        lambda b: real_token(b) + ((next(tokens),) if len(calls) < 1 else ()))
    monkeypatch.setattr(hub_versions, "_summary", lambda *a, **k: calls.append(1) or real(*a, **k))
    c.get("/state-history?body=1", headers=HX)
    assert len(calls) >= 2, "the read was retried once the token moved"


# ----------------------------------------------------------------------
# 6. the baseline badge's time
# ----------------------------------------------------------------------

def test_the_baseline_badge_names_the_banners_time(live_chip):
    c = live_chip["client"]
    c.post("/state-history/snapshot", headers=HX)
    time.sleep(1.1)
    assert c.post("/state/baseline/reset").get_json()["ok"]
    sh = c.get("/state-history?body=1", headers=HX).get_data(as_text=True)
    badge = re.search(r'<span class="snap-baseline[^"]*"[^>]*>(.*?)</span></span>', sh, flags=re.S)
    assert badge and "baseline of the live-change tracker, set" in badge.group(1)
    badge_utc = re.search(r'data-utc="([^"]+)"', badge.group(1)).group(1)
    banner = c.get("/state/drift/view?embed=1").get_data(as_text=True)
    banner_utc = re.search(r'baseline: <span class="ts-local" data-utc="([^"]+)"', banner).group(1)
    assert badge_utc == banner_utc, (badge_utc, banner_utc)


# ----------------------------------------------------------------------
# 7 + 9. Revert last apply
# ----------------------------------------------------------------------

def _last_apply(env):
    with env["app"].app_context():
        return dict(routes._active_ctx().get("last_apply") or {})


def test_revert_last_apply_says_what_the_user_did_and_marks_the_apply(live_chip):
    c = live_chip["client"]
    c.post("/state-history/snapshot", headers=HX)        # the state the apply starts from
    time.sleep(1.1)
    _edit(c, "qubits.qA1.T1", "4e-05")
    _apply(c)
    last = _last_apply(live_chip)
    assert last.get("pre_ts")
    r = c.post(f"/state-history/{last['pre_ts']}/stage?from=tray", headers=HX)
    msg = text(r.get_data(as_text=True))
    with live_chip["app"].test_request_context():
        at, snap = routes.zone_ts_text(last["at"]), routes.zone_ts_text(last["pre_ts"][:22])
    assert f"Restored the state from before your apply at {at}" in msg, msg
    if snap != at:
        assert f"(same content as the snapshot of {snap})" in msg, msg
    assert not msg.startswith("Snapshot ")
    # ...apply it: the reverted apply is marked like an undone one
    _apply(c)
    rows = entries(c.get("/state-history?body=1", headers=HX).get_data(as_text=True))
    flagged = [r for r in rows if "reverted later" in r]
    assert len(flagged) == 1, [text(r)[:120] for r in rows[:4]]
    assert "Applied to the chip" in text(flagged[0]) and "Reverted the last apply" not in text(flagged[0])
    reverting = [r for r in rows if "Reverted the last apply" in text(r)]
    assert reverting and "reverted later" not in reverting[0]


# ----------------------------------------------------------------------
# 8. a run's unreadable saved state, in plain words
# ----------------------------------------------------------------------

def test_an_unreadable_run_state_is_said_in_plain_words():
    raw = ("[WinError 3] The system cannot find the path specified: "
           "'D:\\\\data\\\\lab\\\\2026-10-01\\\\#237_scan_025832\\\\quam_state\\\\state.json'")
    (flag,) = [f for f in story._flags_of({"error": raw, "flags": 0}) if f["key"] == "no-state"]
    assert flag["note"] == ("Its saved state could not be read: the file is missing: "
                            "D:\\data\\lab\\2026-10-01\\#237_scan_025832\\quam_state\\state.json.")
    assert "WinError" not in flag["note"] and "\\\\" not in flag["note"]
    assert flag["title"] == raw, "the OS text stays one hover away"
    assert story.read_error_text("Expecting value: line 1 column 1 (char 0)") == \
        "the file is not a readable state file"
    assert story.read_error_text("[Errno 13] Permission denied: '/x/state.json'") == \
        "the file could not be opened (access denied): /x/state.json"


# ----------------------------------------------------------------------
# 2. the top bar with edits staged (1600 x 950, measured in real Chrome)
# ----------------------------------------------------------------------

def _fit_rules():
    css = (Path(routes.__file__).parent / "static" / "style.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
            if ".topbar.tb-fit-" in sel]


def _level_of(fragment: str) -> int:
    levels = [int(m) for sel, body in _fit_rules() if fragment in sel and "display: none" in body
              for m in re.findall(r"\.topbar\.tb-fit-(\d+) [^,]*" + re.escape(fragment), sel)]
    assert levels, fragment
    return min(levels)


def test_the_top_bar_frees_space_before_any_label_goes():
    """With one edit staged at 1600 px the bar dropped "Versions", "-Sync" and
    "ask-writes" while every item still carried its full side padding and the
    full-size title (measured: level 0 short by ~140 px, levels 1-2 now free it)."""
    rules = _fit_rules()
    assert any(sel == ".topbar.tb-fit-1 > nav > ul > li" and "padding-left: .15rem" in body
               for sel, body in rules), "level 1 tightens the items"
    title = [body for sel, body in rules if sel == ".topbar.tb-fit-2 .app-title"]
    size = float(re.search(r"font-size:\s*([\d.]+)rem", title[0]).group(1))
    assert size < 0.9, "level 2 makes the title smaller than it already is (.9rem changed nothing)"
    assert any(sel == ".topbar.tb-fit-2 .sync-control .state-status-name" and "max-width" in body
               for sel, body in rules)
    for label in (".state-version-id", ".aa-long", ".agent-pill-mode", ".state-version-none"):
        assert _level_of(label) >= 3, f"{label} goes before the space-freeing steps"


def test_the_armed_apply_keeps_its_width():
    tpl = (Path(routes.__file__).parent / "templates" / "_sync_control.html").read_text(encoding="utf-8")
    labels = re.findall(r'data-arm-label="([^"]+)"', tpl)
    assert labels == ["&#8593; Confirm"], labels
