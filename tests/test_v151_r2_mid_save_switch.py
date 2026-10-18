"""R2 MINOR 6: the conftest switch that turns SA-F7's mid-save guard off is
opt-out per test -- a test or module marked ``mid_save_guard`` runs with the
guard ON (a few door modules do), so the suite is not blind to it."""
from __future__ import annotations

import pytest

from quam_state_manager.core import safe_io


def test_an_unmarked_test_runs_with_the_guard_off():
    assert safe_io._MID_SAVE_WINDOW_S == 0.0


@pytest.mark.mid_save_guard
def test_a_marked_test_runs_with_it_on():
    assert safe_io._MID_SAVE_WINDOW_S > 0.0
