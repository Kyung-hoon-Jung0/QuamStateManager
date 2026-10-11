"""SU-12 (needs adjudication): an agent request can flip "Ctrl+Z writes live".

``POST /settings/undo-live`` is not person-only: with ``X-SM-Agent`` it answers
200 and changes the person's consent setting for the live Ctrl+Z walk.
Harness: TOGGLE_UNDO_LIVE agent=True.
Contract question: VPLAN A.1 ("/settings/undo-live is not in PERSON_ONLY") --
the setting is the person's consent that a keypress writes the chip.
"""
import pytest

from tests.state_model.repros._rig import Chip


@pytest.mark.xfail(strict=True, reason="SU-12: the undo-live setting is not person-only")
def test_an_agent_cannot_change_the_undo_live_setting(tmp_path):
    chip = Chip(tmp_path)
    r = chip.c.post("/settings/undo-live", data={"enabled": "0"}, headers={"X-SM-Agent": "claude"})
    assert r.status_code in (403, 409), r.get_json()
