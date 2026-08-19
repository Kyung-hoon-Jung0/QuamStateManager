"""Auto-apply mode (docs/117).

The user amended the covenant on 2026-08-12: a direct live write happens on an
explicit Apply press OR inside a user-enabled auto-apply session. These pins
hold the amendment to its terms.

  - armed is a SERVER fact stamped on the tray, so the pill and the behaviour
    cannot disagree and F5 keeps the truth;
  - with no session every byte of the manual path is what it was (the apply
    route's HX-Trigger string is pinned literally);
  - the session NEVER forces: a live chip that moved is refused, the session
    disarms itself, and the edit survives in the working copy;
  - "revert last apply" anchors to the SESSION (the user's choice), which is
    also what stops a long session writing a snapshot per edit;
  - the applied log is the undo journal, labelled — per chip, newest first;
  - its X is compare-and-swap: it refuses rather than clobbering a later write.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import undo_journal, working_copy
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


def _live_f01(env) -> float:
    doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    return doc["qubits"]["qA1"]["f_01"]


def _edit(env, path="qubits.qA1.f_01", value="5.1e9"):
    return env["client"].post("/field/edit", data={"dot_path": path, "value": value})


def _snapshot_dirs(env) -> list[str]:
    hist = Path(env["app"].instance_path) / "history"
    if not hist.exists():
        return []
    return sorted(str(p) for p in hist.rglob("*") if p.is_dir() and (p / "state.json").exists())


class TestArming:
    def test_arm_stamps_the_tray_and_disarm_clears_it(self, env):
        c = env["client"]
        assert 'data-auto-apply="1"' not in c.get("/state/tray").data.decode()
        r = c.post("/auto-apply/arm")
        assert r.status_code == 200
        assert 'data-auto-apply="1"' in r.data.decode()
        assert "Auto-apply" in r.data.decode()
        assert 'data-auto-apply="1"' in c.get("/state/tray").data.decode()
        r = c.post("/auto-apply/disarm")
        assert 'data-auto-apply="1"' not in r.data.decode()
        assert _ctx(env).get("auto_apply") is None

    def test_a_full_page_render_carries_the_pill(self, env):
        """base.html includes the tray directly, so a full page gets its
        context from _ctx() — not from _render_tray. Both must stamp the
        session or the pill disappears on every navigation while the mode is
        still ON (the trap mutation_seq hit in docs/110; caught in a real
        browser here too)."""
        c = env["client"]
        c.post("/auto-apply/arm")
        for url in ("/qubits", "/bulk", "/explorer"):
            html = c.get(url).data.decode()
            assert 'data-auto-apply="1"' in html, url
            assert "auto-apply-pill" in html, url
            # docs/126 r3: the bolt is an SVG (CSS-recolorable: gray OFF,
            # orange ON), never the emoji, which no stylesheet can tint.
            assert "icon-bolt" in html, url
            assert "⚡" not in html, url

    def test_archive_can_never_arm(self, env):
        ctx = _ctx(env)
        ctx["origin"] = "dataset_archive"
        r = env["client"].post("/auto-apply/arm")
        assert r.status_code == 409
        assert _ctx(env).get("auto_apply") is None

    def test_read_only_live_folder_cannot_arm(self, env):
        _ctx(env)["live_readonly_hint"] = True
        assert env["client"].post("/auto-apply/arm").status_code == 409

    def test_a_diverged_chip_cannot_arm(self, env):
        """Arming into a guaranteed immediate conflict is a trap, not a mode."""
        _ctx(env)["live_diverged"] = True
        r = env["client"].post("/auto-apply/arm")
        assert r.status_code == 409
        assert b"resolve" in r.data.lower()

    def test_the_teaching_line_cannot_contradict_the_mode(self, env):
        """docs/115's sentence says edits stay private until you press Apply.
        While auto-apply is ON that is false, and a false explanation is worse
        than none."""
        c = env["client"]
        off = c.get("/state/tray").data.decode()
        assert "until you press" in off
        c.post("/auto-apply/arm")
        on = c.get("/state/tray").data.decode()
        assert "until you press" not in on
        assert "Auto-apply is ON" in on

    def test_gate_route_reports_without_blocking(self, env):
        body = env["client"].get("/auto-apply/gate").get_json()
        assert body["ok"] is True and body["armable"] is True
        assert body["armed"] is False
        assert "run_active" in body       # reported, never a block (docs/86)


class TestFlush:
    def test_flush_lands_on_live_and_is_labelled(self, env):
        c = env["client"]
        c.post("/auto-apply/arm")
        _edit(env)
        r = c.post("/state/apply-to-live")
        assert r.status_code == 200
        assert _live_f01(env) == 5.1e9
        units = _ctx(env)["undo_units"]
        assert units and (units[-1].get("meta") or {}).get("src") == "auto"
        # the applied log shows it
        assert "Applied to live" in c.get("/state/tray").data.decode()

    def test_armed_response_is_quiet_and_signals(self, env):
        c = env["client"]
        c.post("/auto-apply/arm")
        _edit(env)
        r = c.post("/state/apply-to-live")
        assert "autoApplyApplied" in r.headers.get("HX-Trigger", "")
        # one success toast per edit would be noise the user can't outrun
        assert "Applied to the live chip." not in r.data.decode()

    def test_manual_path_is_byte_identical(self, env):
        """No session ⇒ the apply route is exactly what it was."""
        c = env["client"]
        _edit(env)
        r = c.post("/state/apply-to-live")
        assert r.headers.get("HX-Trigger") == "liveDriftChanged, stateHistoryChanged"
        assert "Applied to the live chip." in r.data.decode()
        assert 'data-auto-apply="1"' not in r.data.decode()

    def test_never_forces(self, env, monkeypatch):
        seen = []
        real = working_copy.apply_to_live

        def spy(wc, *, force=False):
            seen.append(force)
            return real(wc, force=force)

        monkeypatch.setattr(working_copy, "apply_to_live", spy)
        c = env["client"]
        c.post("/auto-apply/arm")
        _edit(env)
        c.post("/state/apply-to-live")
        assert seen and not any(seen), "an auto flush must never force"


class TestConflictDisarms:
    def test_a_moved_chip_refuses_disarms_and_keeps_the_edit(self, env):
        c = env["client"]
        c.post("/auto-apply/arm")
        _edit(env)
        # something else rewrites live between the edit and the flush
        _write_chip(env["live"], _state(f01=7.7e9))
        r = c.post("/state/apply-to-live")

        assert _live_f01(env) == 7.7e9, "never clobbered"
        assert "pending-tray-conflict" in r.data.decode()
        assert "autoApplyDisarm" in r.headers.get("HX-Trigger", "")
        assert _ctx(env).get("auto_apply") is None, "the session is off"
        # and the edit is still recoverable
        ctx = _ctx(env)
        assert ctx.get("working_dirty") or ctx.get("pending_reapply")

    def test_after_a_conflict_the_user_can_resolve_and_land_it(self, env):
        c = env["client"]
        c.post("/auto-apply/arm")
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        c.post("/state/apply-to-live")                       # conflict, disarmed
        r = c.post("/state/apply-to-live?force=1")           # the user chooses
        assert r.status_code == 200
        assert _live_f01(env) == 5.1e9


class TestSnapshotPolicy:
    def test_one_anchor_per_session_not_per_flush(self, env):
        c = env["client"]
        c.post("/auto-apply/arm")
        before = len(_snapshot_dirs(env))
        for v in ("5.1e9", "5.2e9", "5.3e9"):
            _edit(env, value=v)
            c.post("/state/apply-to-live")
        after = len(_snapshot_dirs(env))
        # one pre-apply anchor + at most one throttled post-apply for the burst
        assert after - before <= 2, f"snapshot flood: {after - before} new"
        assert _ctx(env)["auto_apply"]["pre_ts"], "the session has ONE anchor"
        anchor = _ctx(env)["auto_apply"]["pre_ts"]
        assert _ctx(env)["last_apply"]["pre_ts"] == anchor

    def test_the_revert_label_follows_the_anchor(self, env):
        """The anchor is the session, so the button must not promise "the
        last apply" — that would be a different, smaller thing than what it
        does."""
        c = env["client"]
        c.post("/auto-apply/arm")
        _edit(env)
        c.post("/state/apply-to-live")
        html = c.get("/state/tray").data.decode()
        assert "Revert this session" in html
        assert "Revert last apply" not in html
        c.post("/auto-apply/disarm")
        assert "Revert last apply" in c.get("/state/tray").data.decode()

    def test_disarm_closes_the_session_with_a_snapshot(self, env):
        c = env["client"]
        c.post("/auto-apply/arm")
        _edit(env)
        c.post("/state/apply-to-live")
        n = len(_snapshot_dirs(env))
        c.post("/auto-apply/disarm")
        assert len(_snapshot_dirs(env)) >= n


class TestAppliedLog:
    def test_newest_first_and_only_auto_units(self, env):
        c = env["client"]
        # a MANUAL apply first — it must not appear in the applied log
        _edit(env, value="5.05e9")
        c.post("/state/apply-to-live")
        c.post("/auto-apply/arm")
        _edit(env, value="5.11e9")
        c.post("/state/apply-to-live")
        _edit(env, value="5.22e9")
        c.post("/state/apply-to-live")

        with env["app"].app_context():
            rows = routes_mod._applied_log_rows()
        assert len(rows) == 2, "only the auto flushes"
        assert rows[0]["ts"] >= rows[1]["ts"], "newest first"

    def test_rows_do_not_leak_across_chips(self, env):
        c = env["client"]
        c.post("/auto-apply/arm")
        _edit(env)
        c.post("/state/apply-to-live")
        other = env["tmp"] / "chips" / "other"
        _write_chip(other, _state(f01=6.0e9))
        c.post("/load", data={"folder": str(other)})
        with env["app"].app_context():
            assert routes_mod._applied_log_rows() == []


class TestRevertOneRow:
    def _one_applied(self, env):
        c = env["client"]
        c.post("/auto-apply/arm")
        _edit(env, value="5.1e9")
        c.post("/state/apply-to-live")
        with env["app"].app_context():
            rows = routes_mod._applied_log_rows()
        assert rows
        return rows[0]["id"]

    def test_revert_stages_the_inverse_and_marks_the_row(self, env):
        uid = self._one_applied(env)
        r = env["client"].post("/auto-apply/revert", data={"unit_id": uid})
        assert r.status_code == 200
        ctx = _ctx(env)
        # staged, not written behind the user's back
        assert ctx["store"].merged["qubits"]["qA1"]["f_01"] == 5.0e9
        gids = {e.group_id for e in ctx["store"].change_log}
        assert any(isinstance(g, str) and g.startswith("alr:") for g in gids)
        assert not any(isinstance(g, str) and g.startswith(undo_journal.GID_PREFIX)
                       for g in gids), "must not use the journal's own prefix"
        with env["app"].app_context():
            rows = routes_mod._applied_log_rows()
        assert rows[0]["reverted_by"], "the row says it was reverted"

    def test_refuses_when_the_value_moved_since(self, env):
        uid = self._one_applied(env)
        _edit(env, value="9.9e9")          # a later change to the same path
        env["client"].post("/state/apply-to-live")
        r = env["client"].post("/auto-apply/revert", data={"unit_id": uid})
        assert r.status_code == 409
        assert b"changed since" in r.data
        assert _ctx(env)["store"].merged["qubits"]["qA1"]["f_01"] == 9.9e9

    def test_cas_tolerates_a_float_roundtrip(self, env):
        from quam_state_manager.core import edit_policy
        assert edit_policy.cas_equal(5.1e9, 5.1e9 * (1 + 1e-12))
        assert not edit_policy.cas_equal(5.1e9, 5.1e9 + 1e3)
        # bools deliberately never enter the numeric branch (the autofit
        # writer's long-standing contract, now shared) — so no tolerance is
        # ever applied to one.
        assert not edit_policy.cas_equal(True, 1.000000000001)
        assert not edit_policy.cas_equal("5.1e9", 5.1e9), "text is not a number"

    def test_unknown_or_already_reverted_unit_is_refused(self, env):
        uid = self._one_applied(env)
        assert env["client"].post("/auto-apply/revert",
                                  data={"unit_id": "nope"}).status_code == 409
        env["client"].post("/auto-apply/revert", data={"unit_id": uid})
        assert env["client"].post("/auto-apply/revert",
                                  data={"unit_id": uid}).status_code == 409


def test_client_flusher_is_a_separate_file_and_is_loaded():
    root = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
    js = (root / "static" / "auto-apply.js").read_text(encoding="utf-8")
    assert "state/apply-to-live" in js, "the ONE live writer is what it presses"
    assert "MutationObserver" in js
    assert "_applyInFlight" in js, "shares the existing double-submit guard"
    base = (root / "templates" / "base.html").read_text(encoding="utf-8")
    assert "auto-apply.js" in base
