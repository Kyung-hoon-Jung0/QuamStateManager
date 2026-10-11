"""Pins for acceptance checks of rendered notes and legacy smoke inputs."""
from __future__ import annotations

import pytest

from tools.trends_oracle.html_checks import saved_writer_is_not_proven, validate_legacy_runs


def test_a_saved_note_allows_html_wrapping_but_requires_both_provenance_claims():
    body = ('<p class="ds-provenance-note" role="note">Opened from a value whose writer is not\n'
            ' proven: this run&rsquo;s saved state is the first to carry it, but its '
            '<code>node.json</code> records no patch that set it, so it may not be the '
            'run that measured it.</p>')
    assert saved_writer_is_not_proven(body)
    assert not saved_writer_is_not_proven(body.replace("writer is not\n proven", "writer is proven"))
    assert not saved_writer_is_not_proven(body.replace("ds-provenance-note", "unrelated"))


def test_legacy_smoke_requires_existing_saved_states(tmp_path):
    run = tmp_path / "run" / "quam_state"
    run.mkdir(parents=True)
    (run / "state.json").write_text("{}", encoding="utf-8")
    validate_legacy_runs([str(run)])
    with pytest.raises(ValueError, match="existing saved-state folders"):
        validate_legacy_runs([str(tmp_path / "old-rig" / "quam_state")])
    with pytest.raises(ValueError, match="existing saved-state folders"):
        validate_legacy_runs([])
