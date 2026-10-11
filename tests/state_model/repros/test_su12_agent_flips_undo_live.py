"""SU-12 (needs adjudication): an agent request can flip "Ctrl+Z writes live".

``POST /settings/undo-live`` is not person-only: with ``X-SM-Agent`` it answers
200 and changes the person's consent setting for the live Ctrl+Z walk.
Harness: TOGGLE_UNDO_LIVE agent=True.
Contract question: VPLAN A.1 ("/settings/undo-live is not in PERSON_ONLY") --
the setting is the person's consent that a keypress writes the chip.
"""
import pytest

from tests.state_model.repros._rig import Chip


@pytest.mark.parametrize("enabled", ["0", "1"])
def test_an_agent_cannot_change_the_undo_live_setting(tmp_path, enabled):
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    with chip.app.app_context():
        before = routes._undo_live_enabled()
        settings = routes._undo_live_path()
    bytes_before = settings.read_bytes() if settings.exists() else None
    r = chip.c.post("/settings/undo-live", data={"enabled": enabled}, headers={"X-SM-Agent": "claude"})
    # v1.3.0: agent changes live-write consent -> person-only 403, consent stays unchanged.
    assert r.status_code == 403, r.get_json()
    with chip.app.app_context():
        assert routes._undo_live_enabled() == before
    assert (settings.read_bytes() if settings.exists() else None) == bytes_before


def test_a_person_can_still_change_the_undo_live_setting(tmp_path):
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    for enabled in ("0", "1"):
        response = chip.c.post("/settings/undo-live", data={"enabled": enabled})
        assert response.status_code == 200
        with chip.app.app_context():
            assert routes._undo_live_enabled() == (enabled == "1")
