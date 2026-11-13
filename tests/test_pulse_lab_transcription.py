"""The Pulses page draws a lab pulse class SM transcribed for the CZ gate plan.

With no lab drawing and no generated config, the page used to say
"unrecognized pulse class" for a two-flux SNZ pulse the CZ gate plan of the
same chip draws sample for sample. The page now shows that SAME transcription
(``waveform_synth.lab_transcription_payload`` = ``resolved_lab_pulse`` +
``lab_waveform``, the plan's own path), labelled as SM's transcription -- never
as quam's synthesis or the lab's own code. The generated config still wins.
"""

from __future__ import annotations

import pytest

from tests.test_pulse_unknown_class import (  # noqa: F401 -- fixtures
    GNZ_PATH, KNOWN_PATH, SNZ_PATH, configured, plain,
)

LABEL = "SM&#39;s transcription of the lab class"


def test_the_detail_draws_the_plan_s_transcription_and_says_so(plain):
    from quam_state_manager.core.waveform_synth import lab_waveform, resolved_lab_pulse
    from quam_state_manager.web import routes as R
    _, c, store = plain
    body = c.get("/pulse/detail", query_string={"path": SNZ_PATH}).get_data(as_text=True)
    assert LABEL in body
    assert "preview (synthesized)" not in body
    assert "unrecognized pulse class" not in body
    tp = R._pulse_transcription(store, SNZ_PATH)
    plan = lab_waveform(resolved_lab_pulse(store.merged, SNZ_PATH))
    assert tp["ok"] and tp["i"] == plan and len(plan) == 88


def test_the_row_thumbnail_is_titled_as_the_transcription(plain):
    _, c, _ = plain
    row = c.get("/pulse/row", query_string={"path": SNZ_PATH}).get_data(as_text=True)
    assert "transcription of the lab class" in row
    assert ">88</td>" in row


def test_the_ground_truth_route_hands_it_over_under_its_own_key(plain):
    _, c, _ = plain
    r = c.get("/api/pulse/ground-truth", query_string={"path": SNZ_PATH})
    j = r.get_json()
    assert j["ok"] is False and j["status"] == "absent"
    assert j["transcription"]["ok"] and j["transcription"]["length"] == 88


def test_a_generated_config_still_wins(configured):
    _, c, _ = configured
    body = c.get("/pulse/detail", query_string={"path": SNZ_PATH}).get_data(as_text=True)
    assert "waveform from the generated config" in body
    assert LABEL not in body


@pytest.mark.parametrize("path", [GNZ_PATH, KNOWN_PATH])
def test_nothing_else_is_drawn_by_it(plain, path):
    _, c, _ = plain
    body = c.get("/pulse/detail", query_string={"path": path}).get_data(as_text=True)
    assert LABEL not in body


def test_the_client_takes_the_transcription_when_the_config_has_none():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static"
          / "pulses.js").read_text(encoding="utf-8")
    i = js.index("function groundTruthInto(")
    body = js[i:i + 3000]
    assert "data.transcription && data.transcription.ok" in body
    assert "setPlotLabel(root, TRANSCRIPTION_LABEL)" in body
    assert "sec.fromConfig = false" in body
