"""Runner+agent P7 (docs/78 D-9): four events, best-effort delivery."""
from __future__ import annotations

import json

from quam_state_manager.core.autofit import notify


class TestEvents:
    def test_only_four_events_exist(self):
        """A notifier that fires on everything is one the user mutes, and a
        muted notifier reads as coverage while delivering nothing."""
        assert set(notify.EVENTS) == {"plan_done", "target_halted",
                                      "plan_stopped", "needs_human"}

    def test_an_unknown_event_is_refused_not_guessed(self, tmp_path):
        out = notify.notify(tmp_path, "everything_is_fine")
        assert out["sent"] == [] and "unknown event" in out["skipped"]

    def test_a_disabled_event_is_skipped_with_a_reason(self, tmp_path):
        notify.save_settings(tmp_path, {"events": ["plan_done"]})
        out = notify.notify(tmp_path, "target_halted", {"target": "qA1"})
        assert out["sent"] == [] and "disabled" in out["skipped"]

    def test_a_corrupt_settings_file_falls_back_to_defaults(self, tmp_path):
        (tmp_path / "autofit_notify.json").write_text("{ not json",
                                                      encoding="utf-8")
        st = notify.load_settings(tmp_path)
        assert st["browser"] is True and set(st["events"]) == set(notify.EVENTS)


class TestBrowserQueue:
    def test_events_persist_so_a_closed_laptop_keeps_the_night(self, tmp_path):
        notify.notify(tmp_path, "plan_done", {"status": "done"})
        notify.notify(tmp_path, "needs_human", {"question": "two hypotheses"})
        assert [e["event"] for e in notify.peek(tmp_path)] == \
            ["plan_done", "needs_human"]

    def test_drain_returns_and_clears(self, tmp_path):
        notify.notify(tmp_path, "plan_done", {"status": "done"})
        assert len(notify.drain(tmp_path)) == 1
        assert notify.drain(tmp_path) == []

    def test_the_queue_is_capped(self, tmp_path):
        for i in range(notify._QUEUE_CAP + 25):
            notify.notify(tmp_path, "target_halted", {"i": i})
        q = notify.peek(tmp_path)
        assert len(q) == notify._QUEUE_CAP
        assert q[-1]["i"] == notify._QUEUE_CAP + 24      # newest kept


class TestWebhook:
    def test_a_dead_webhook_never_raises(self, tmp_path):
        """A failed webhook must not fail a calibration."""
        notify.save_settings(tmp_path,
                             {"webhook_url": "http://127.0.0.1:1/none",
                              "timeout_s": 1})
        out = notify.notify(tmp_path, "plan_done", {"status": "done"})
        assert "webhook" not in out["sent"]
        assert "browser" in out["sent"]      # the other channel still worked

    def test_the_payload_carries_the_event_name(self, tmp_path, monkeypatch):
        seen = {}

        def fake_post(url, body, timeout):
            seen.update(url=url, body=body)
            return True

        monkeypatch.setattr(notify, "_post", fake_post)
        notify.save_settings(tmp_path, {"webhook_url": "http://x/hook"})
        notify.notify(tmp_path, "plan_stopped", {"tier": 2, "reason": "flat"})
        assert seen["body"]["event"] == "plan_stopped"
        assert seen["body"]["tier"] == 2


class TestEngineHooks:
    def test_the_engine_fires_on_the_events_that_matter(self):
        import inspect

        from quam_state_manager.core.autofit import engine
        src = inspect.getsource(engine)
        for ev in ("plan_done", "target_halted", "plan_stopped"):
            assert f'_notify("{ev}"' in src, ev

    def test_notify_failure_cannot_kill_a_plan(self, tmp_path, monkeypatch):
        from quam_state_manager.core.autofit import engine

        def boom(*a, **k):
            raise RuntimeError("notifier exploded")

        monkeypatch.setattr(engine.notify, "notify", boom)
        eng = engine.PlanEngine.__new__(engine.PlanEngine)
        eng.instance_path = str(tmp_path)
        eng.plan_run_id = "af_test"
        eng._notify("plan_done", status="done")      # must not raise
