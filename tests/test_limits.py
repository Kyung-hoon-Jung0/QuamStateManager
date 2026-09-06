"""docs/173 S3b: per-chip Limits and the default mode."""

from __future__ import annotations

from datetime import datetime

import pytest

from quam_state_manager.core import journal, limits
from quam_state_manager.web.app import create_app

_H = {"Origin": "http://localhost"}


class TestStore:
    def test_a_new_chip_is_ask_writes(self, tmp_path):
        lim = limits.load(tmp_path, "c")
        assert lim["mode"] == "ask-writes" and lim["max_writes_per_plan"] == 200 and lim["stop_by"] == ""

    def test_save_validates_and_journals_a_mode_change_with_who(self, tmp_path):
        lim = limits.save(tmp_path, "c", {"mode": "auto", "stop_by": "07:00", "max_delta": {"power_rabi": "0.05"}},
                          who="human:이OO")
        assert lim["mode"] == "auto" and lim["stop_by"] == "07:00" and lim["max_delta"] == {"power_rabi": 0.05}
        assert "mode ask-writes -> auto (set by human:이OO)" in journal.read(tmp_path, "c")
        limits.save(tmp_path, "c", {"max_writes_per_plan": 5}, who="x")
        assert journal.read(tmp_path, "c").count("mode ") == 1, "only a MODE change is journaled"
        assert limits.load(tmp_path, "c")["mode"] == "auto", "reload keeps it"

    @pytest.mark.parametrize("bad", [{"mode": "yolo"}, {"stop_by": "25:00"}, {"max_writes_per_plan": -1},
                                     {"max_delta": [1]}, {"webhook_url": "ftp://x"}, {"stoploss_plan": "many"}])
    def test_bad_values_are_refused(self, tmp_path, bad):
        with pytest.raises(limits.LimitError):
            limits.save(tmp_path, "c", bad)

    def test_unknown_keys_are_ignored_and_a_corrupt_file_falls_back(self, tmp_path):
        assert "nope" not in limits.save(tmp_path, "c", {"nope": 1, "mode": "ask-all"})
        limits.path_for(tmp_path, "c").write_text("{not json", encoding="utf-8")
        assert limits.load(tmp_path, "c")["mode"] == "ask-writes"


class TestJudgements:
    def test_past_stop_by(self):
        lim = {"stop_by": "07:00"}
        assert limits.past_stop_by(lim, datetime(2026, 9, 6, 7, 0)) is True
        assert limits.past_stop_by(lim, datetime(2026, 9, 6, 6, 59)) is False
        assert limits.past_stop_by({"stop_by": ""}, datetime(2026, 9, 6, 23, 59)) is False

    def test_delta_exceeds_only_for_a_family_with_a_band(self):
        lim = {"max_delta": {"power_rabi": 0.05}}
        assert limits.delta_exceeds(lim, "power_rabi", 0.30, 0.36) is True
        assert limits.delta_exceeds(lim, "power_rabi", 0.30, 0.34) is False
        assert limits.delta_exceeds(lim, "ramsey", 1, 2) is False
        assert limits.delta_exceeds(lim, "power_rabi", "a", 1) is False

    def test_notify_is_gated_by_the_chips_own_limits(self, tmp_path, monkeypatch):
        sent = []
        from quam_state_manager.core.autofit import notify as nmod
        monkeypatch.setattr(nmod, "_post", lambda url, body, t: sent.append((url, body["event"])) or True)
        assert limits.notify(tmp_path, "c", "agent_failure", {})["skipped"]
        limits.save(tmp_path, "c", {"webhook_url": "https://hooks.example/x", "notify_events": ["agent_failure"]})
        assert limits.notify(tmp_path, "c", "agent_failure", {"a": 1})["sent"] == ["https://hooks.example/x"]
        assert limits.notify(tmp_path, "c", "agent_stalled", {})["skipped"], "an event the lab turned off is not sent"
        assert sent == [("https://hooks.example/x", "agent_failure")]


class TestTheRoute:
    def test_get_post_and_refusal(self, tmp_path):
        c = create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()
        d = c.get("/api/agent/limits").get_json()
        assert d["limits"]["mode"] == "ask-writes" and "auto" in d["modes"]
        r = c.post("/api/agent/limits", json={"mode": "auto", "max_delta": {"ramsey": 2e6}}, headers={**_H, "X-SM-Actor": "박OO"})
        assert r.status_code == 200 and r.get_json()["limits"]["mode"] == "auto"
        assert c.post("/api/agent/limits", json={"stop_by": "9pm"}, headers=_H).status_code == 400
        assert c.post("/api/agent/limits", data={"max_delta": "{bad"}, headers=_H).status_code == 400
        assert "set by human:박OO" in journal.read(tmp_path / "inst", "chip")
