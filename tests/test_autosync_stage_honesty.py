"""docs/178 — with Auto-Sync push armed, "the live chip is untouched" is false.

Measured by the write-path stress round against a chip copy in real Chrome: arm
Auto-Sync (push only), then press either "stage only" door — State History's
**Load as working state** or the dataset State tab's **Stage only**. Both write
the live chip within the same second, because staging content sets
``working_dirty`` and the docs/117 observer flushes on exactly that.

That is the covenant working as designed — the arming press licenses the session
(docs/117) — so this is not a write without a press, and the lane confirmed it is
recoverable. What failed is honesty, twice:

1. the button title, the hx-confirm, the result line and the tray badge all said
   the live chip was untouched while it was being written;
2. the applied-to-live log — which docs/117 designates as *the* feedback after a
   flush, and which carries the per-write ✕ — listed nothing at all, because it
   filters on ``meta["src"] == "auto"`` while a wholesale unit's ``src`` names
   the gesture (``apply-staged``) rather than the session. A plain cell edit in
   the same armed session WAS listed, which is what made the omission specific
   and hard to notice.

These pins hold both halves. They deliberately assert the log CONTENT, not just
that a unit exists: the row is what gives the user the ✕.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(f01=5.0e9, t1=2.0e-5):
    return {
        "qubits": {"qA1": {"id": "qA1", "f_01": f01, "T1": t1}},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _ctx(env):
    with env["app"].app_context():
        return routes_mod._active_ctx()


def _newest_snapshot_ts(env) -> str:
    """The timestamp of the newest snapshot, read the way the page reads it."""
    with env["app"].app_context():
        snaps = routes_mod._history().list_snapshots(env["live"])
    assert snaps, "the snapshot door captured nothing"
    s0 = snaps[0]
    return getattr(s0, "timestamp", None) or s0["timestamp"]


class TestTheNote:
    """One sentence, one source — a label and a route message cannot drift."""

    def test_silent_when_nothing_is_armed(self, env):
        with env["app"].test_request_context():
            assert routes_mod._auto_push_note() == ""

    def test_it_says_what_will_happen_when_push_is_armed(self, env):
        env["client"].post("/auto-apply/arm")
        with env["app"].test_request_context():
            note = routes_mod._auto_push_note()
        assert "ARMED" in note
        assert "live chip" in note and "immediately" in note
        # …and it tells the reader the way out, not just the fact.
        assert "disarm" in note.lower()

    def test_a_pull_only_session_is_not_a_push_session(self, env):
        """`_auto_apply_state` reads a session only when it authorizes pushing
        (docs/120 item 8). A pull-only session writes nothing to the chip, so
        the note must stay silent — warning about a write that cannot happen
        teaches people to ignore the warning."""
        c = env["client"]
        r = c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "0", "push": "0"})
        assert r.status_code == 200, r.data[:300]
        assert _ctx(env).get("auto_apply"), "the pull-only session did not arm at all"
        with env["app"].test_request_context():
            assert routes_mod._auto_push_note() == ""
        # …and the moment push joins it, the note appears.
        assert c.post("/auto-sync/set",
                      data={"pull": "1", "pull_replace": "0", "push": "1"}).status_code == 200
        with env["app"].test_request_context():
            assert "ARMED" in routes_mod._auto_push_note()

    def test_the_label_and_the_route_say_the_same_thing(self, env):
        """The Jinja global exists so a button's title cannot fall out of step
        with the message the press produces."""
        c = env["client"]
        assert c.post("/state-history/snapshot").status_code == 200
        c.post("/auto-apply/arm")
        html = c.get("/state-history").data.decode()
        assert "Load as working state" in html, "no snapshot row rendered"
        with env["app"].test_request_context():
            note = routes_mod._auto_push_note()

        # PER ATTRIBUTE, not "somewhere in the page": the title and the confirm
        # are two separate promises on the same button, and a page-wide search
        # cannot tell which of them carries the warning.
        def _attr(name: str) -> str:
            i = html.index("Load as working state")
            frag = html[max(0, i - 1200):i]
            j = frag.rindex(name + '="') + len(name) + 2
            return frag[j:frag.index('"', j)]

        assert note.strip() in _attr("title"), _attr("title")
        assert note.strip() in _attr("hx-confirm"), _attr("hx-confirm")

        # …and it is gone again once nothing is armed, so the warning stays
        # meaningful rather than becoming furniture.
        c.post("/auto-apply/disarm")
        assert note.strip() not in c.get("/state-history").data.decode()


class TestTheLogNamesTheWholesaleWrite:
    """The row is the point: it carries the ✕ that puts the write back."""

    def _unit(self, src, *, auto, entries=None, too_large=0):
        meta = {"src": src, "wholesale": True, "at": 1.0}
        if auto:
            meta["auto"] = True
        if too_large:
            meta["too_large"] = too_large
        return {"id": "u1", "ts": 10 ** 12, "entries": entries or [], "meta": meta}

    def _rows(self, env, unit):
        ctx = _ctx(env)
        ctx["undo_units"] = [unit]
        ctx["undo_cursor"] = 1
        with env["app"].app_context():
            env["app"].config["process_start_ts"] = 0
            return routes_mod._applied_log_rows(ctx)

    def test_a_wholesale_write_inside_an_armed_session_is_listed(self, env):
        ent = [{"path": "qubits.qA1.f_01", "old": 5.0e9, "new": 6.0e9}]
        rows = self._rows(env, self._unit("apply-staged", auto=True, entries=ent))
        assert len(rows) == 1, "the log was silent about the largest write it can make"
        assert rows[0]["entries"] == ent
        assert rows[0]["wholesale"] is True
        assert rows[0]["id"] == "u1"          # so the ✕ has a unit to revert

    def test_the_same_write_outside_a_session_is_not(self, env):
        """Only writes the SESSION made belong in "Applied to live … this
        session" — a manual Apply press is already its own feedback."""
        ent = [{"path": "qubits.qA1.f_01", "old": 5.0e9, "new": 6.0e9}]
        assert self._rows(env, self._unit("apply-staged", auto=False, entries=ent)) == []

    def test_a_write_too_large_to_list_is_still_named(self, env):
        """A unit past `_WHOLESALE_UNIT_CAP` is stored with NO entries. Dropping
        it here would make the biggest writes the only invisible ones."""
        rows = self._rows(env, self._unit("restore-live", auto=True, too_large=5000))
        assert len(rows) == 1
        assert rows[0]["n"] == 5000 and rows[0]["too_large"] == 5000
        assert rows[0]["entries"] == []

    def test_an_empty_unit_that_is_not_too_large_is_still_dropped(self, env):
        """The old guard's real job — a unit that recorded nothing says
        nothing — survives."""
        assert self._rows(env, self._unit("apply-staged", auto=True)) == []

    def test_the_template_renders_both_shapes(self, env):
        """`_applied_log.html` opens a row with `r.entries[-1]`, so a
        no-entries row would raise. Render it rather than trusting the branch."""
        from flask import render_template
        big = {"id": "u9", "ts": 1.0, "n": 5000, "entries": [], "wholesale": True,
               "too_large": 5000, "reverted_by": None}
        small = {"id": "u8", "ts": 1.0, "n": 1, "wholesale": True, "too_large": 0,
                 "entries": [{"path": "qubits.qA1.T1", "old": 1.0, "new": 2.0}],
                 "reverted_by": None}
        with env["app"].test_request_context():
            html = render_template("_applied_log.html", applied_log=[big, small])
        assert "5000 values" in html
        assert "whole state" in html
        assert "qubits.qA1.T1" in html
        # the too-large row offers no ✕ — there is nothing to compare and swap
        assert html.count("btn-al-revert") == 1
        assert "no per-value undo" in html


class TestTheRealWholesaleWriteIsStamped:
    """The end-to-end half. Every pin above builds its unit by hand, so nothing
    ran `_wholesale_unit` — the flag it stamps was pinned only by the code that
    reads it, and deleting the stamp changed nothing any test could see."""

    def _journal_units(self, env):
        from quam_state_manager.core import undo_journal
        with env["app"].app_context():
            path = undo_journal.sidecar_path(env["app"].instance_path,
                                             routes_mod._active_ctx()["path"])
        return undo_journal.load_state(path)[0] if path.exists() else []

    def test_an_armed_stage_then_flush_stamps_and_lists_the_write(self, env):
        c = env["client"]
        assert c.post("/state-history/snapshot").status_code == 200
        ts = _newest_snapshot_ts(env)

        # move live away from the snapshot, so staging it is a real change
        assert c.post("/field/edit",
                      data={"dot_path": "qubits.qA1.T1", "value": "9e-5"}).status_code == 200
        assert c.post("/state/apply-to-live").status_code in (200, 409)

        c.post("/auto-apply/arm")
        assert c.post(f"/state-history/{ts}/stage?force=1").status_code == 200
        # In a browser the docs/117 observer presses this door on `working_dirty`.
        # Here the test presses the same door; the point is what gets STAMPED.
        assert c.post("/state/apply-to-live").status_code == 200

        whole = [u for u in self._journal_units(env)
                 if (u.get("meta") or {}).get("wholesale")]
        assert whole, "the wholesale write journaled no unit at all"
        meta = whole[-1]["meta"]
        assert meta.get("auto") is True, meta
        assert meta.get("src") == "apply-staged", \
            "src must keep naming the gesture — story.py reads it"

        with env["app"].app_context():
            env["app"].config["process_start_ts"] = 0
            rows = routes_mod._applied_log_rows(routes_mod._active_ctx())
        assert any(r["id"] == whole[-1]["id"] for r in rows), \
            "the log is still silent about the largest write it can make"

    def test_the_same_stage_without_a_session_is_not_stamped(self, env):
        c = env["client"]
        assert c.post("/state-history/snapshot").status_code == 200
        ts = _newest_snapshot_ts(env)
        assert c.post("/field/edit",
                      data={"dot_path": "qubits.qA1.T1", "value": "9e-5"}).status_code == 200
        assert c.post("/state/apply-to-live").status_code in (200, 409)

        assert c.post(f"/state-history/{ts}/stage?force=1").status_code == 200
        assert c.post("/state/apply-to-live").status_code == 200

        whole = [u for u in self._journal_units(env)
                 if (u.get("meta") or {}).get("wholesale")]
        assert whole
        assert not (whole[-1]["meta"]).get("auto")


class TestTheStageDoorsSayIt:
    """End to end through the real routes, on a real chip copy."""

    def test_the_state_history_stage_message_warns_while_armed(self, env):
        c = env["client"]
        assert c.post("/state-history/snapshot").status_code == 200
        ts = _newest_snapshot_ts(env)
        # `force=1` on BOTH presses, so the two messages are like for like: the
        # first stage leaves the working state staged, and an unforced second
        # press answers the "you have unsaved edits" confirm instead of staging.
        plain = c.post(f"/state-history/{ts}/stage?force=1").data.decode()
        assert "loaded as the working state" in plain, plain[:300]
        assert "ARMED" not in plain

        c.post("/auto-apply/arm")
        armed = c.post(f"/state-history/{ts}/stage?force=1").data.decode()
        assert "loaded as the working state" in armed, armed[:300]
        assert "ARMED" in armed and "immediately" in armed, armed[:400]

    def test_the_dataset_stage_only_button_warns_while_armed(self, env):
        """The other door. Its title is static markup, so it takes the note
        through the Jinja global — which is the whole reason there is one."""
        c = env["client"]
        html = c.get("/state-history").data.decode()
        assert "auto-apply-pill" in html          # the page renders the tray
        from flask import render_template_string
        tpl = '<button title="stage only.{{ auto_push_note }}">Stage only</button>'
        with env["app"].test_request_context():
            assert "ARMED" not in render_template_string(tpl)
        c.post("/auto-apply/arm")
        with env["app"].test_request_context():
            assert "ARMED" in render_template_string(tpl)
