"""v1.3.0 A8 (browser check follow-up): the chip's first recorded state is its
starting state. Its card says so, and the day neither dims it as another
chip's run nor counts it apart ("1 maybe another chip's" beside a card that
says "starting values")."""
from __future__ import annotations

from quam_state_manager.core import hub_store as hs
from tests.test_calibration_log_hub import DAY, _card_html, _run_event, world  # noqa: F401


def test_the_first_state_is_counted_with_the_days_runs(world):
    _, flat = _run_event(world, {"v": 1, "w": 1}, clock="10:00:00", run_id=1, flags=hs.CHIP_UNCERTAIN)
    _run_event(world, {"v": 2, "w": 1}, clock="11:00:00", run_id=2, prev=flat)
    data = world["build"]()
    cards = {c["run_id"]: c for c in data["cards"] if c["kind"] == "run"}
    assert cards[1]["first_state_n"] == 2 and cards[1]["writes"] == []
    assert [f["label"] for f in cards[1]["flags"]][:1] == ["starting values"]
    assert not cards[1]["other_chip"]
    assert data["counts"]["runs"] == 2 and data["counts"]["other_chip"] == 0
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "maybe another chip&#39;s" not in html and "maybe another chip's" not in html
    first = _card_html(html, 'id="card-1"')
    assert "jr-other-chip" not in first and "starting values" in first
    # a LATER run of an uncertain identity is still counted apart
    _run_event(world, {"v": 3, "w": 1}, clock="12:00:00", run_id=3, prev=flat, flags=hs.CHIP_UNCERTAIN)
    data = world["build"]()
    assert data["counts"]["other_chip"] == 1 and data["counts"]["runs"] == 2
