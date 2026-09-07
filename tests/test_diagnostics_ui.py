"""UI-layer regressions for the diagnostics list: the category→domain taxonomy,
the ``advisory`` flag plumbing, and the grouped/filter-pill render of
``_diagnostics_list.html`` (collapsible domain sections + severity/advisory pills,
advisory-only domains collapsed by default while error/warning domains open)."""

from __future__ import annotations

import re

from quam_state_manager.core import diagnostics
from quam_state_manager.core.diagnostics import Finding, domain_of, summarize


def test_domain_of_mapping():
    assert domain_of("connectivity_band_edge") == "connectivity"
    assert domain_of("connectivity_downconverter") == "connectivity"
    assert domain_of("port_missing") == "connectivity"
    assert domain_of("downconverter_literal") == "connectivity"
    assert domain_of("value_spec_if_floor") == "values"
    assert domain_of("value_nan") == "values"
    assert domain_of("waveform_range") == "waveforms"
    assert domain_of("waveform_invalid") == "waveforms"
    assert domain_of("dangling_pointer") == "references"
    assert domain_of("config_iw_value") == "config"
    assert domain_of("something_unknown") == "other"


def test_finding_as_dict_carries_advisory():
    assert Finding("warning", "connectivity_band_edge", "p", "m", advisory=True).as_dict()["advisory"] is True
    assert Finding("error", "port_missing", "p", "m").as_dict()["advisory"] is False


def _details_open(html: str, domain: str) -> bool:
    m = re.search(r'<details\b[^>]*data-domain="' + domain + r'"[^>]*>', html)
    assert m, f"no <details> for domain {domain}"
    return " open" in m.group(0)


def _render(findings):
    from quam_state_manager.web.app import create_app
    from flask import render_template
    app = create_app(testing=True)
    with app.app_context():
        return render_template("_diagnostics_list.html", findings=findings,
                               diag_summary=summarize(findings), allow_jump=True)


class TestDiagnosticsListRender:
    def test_pills_grouping_and_default_open(self):
        findings = [
            # connectivity: ONLY an advisory → collapsed by default (tames the
            # by-design band-edge noise)
            Finding("warning", "connectivity_band_edge", "con1/p1",
                    "near edge. Optional, not required.", jump_path="ports.a", advisory=True),
            # waveforms: a crash-class error → open by default
            Finding("error", "waveform_range", "q1.readout", "sample>1", jump_path="q1.amp"),
            # values: a real (non-advisory) warning → open by default
            Finding("warning", "value_spec_if_floor", "q1.resonator.intermediate_frequency",
                    "IF below floor", jump_path="q1.resonator.RF_frequency"),
        ]
        html = _render(findings)

        # filter pills exist for each present bucket
        for bucket in ("error", "warning", "advisory"):
            assert f'data-bucket="{bucket}"' in html
        assert 'data-bucket="info"' not in html        # no info finding → no pill
        assert 'class="diag-shown-count' in html

        # advisory row gets the distinct tier (chip + dashed rail), not a warning badge
        assert "diag-row-advisory" in html
        assert ">Recommendation<" in html

        # default-open logic: error/warning domains open, advisory-only collapsed
        assert _details_open(html, "waveforms") is True
        assert _details_open(html, "values") is True
        assert _details_open(html, "connectivity") is False

    def test_clean_chip_shows_ok_and_no_pills(self):
        html = _render([])
        assert "No structural issues found" in html
        assert 'class="diag-pill' not in html


class TestAcknowledgedRowsReadAsSettled:
    """docs/168 + on-site 2026-09-07: after every env finding was confirmed the
    page still showed "3 errors", "Environment match (3)" and red Error chips --
    only the header badge went healthy. Acknowledged findings are now LISTED
    (collapsed under an "N acknowledged" toggle, revocable) but never COUNTED:
    no pill, no domain badge, a muted "acknowledged" chip instead of red."""

    def _acked(self, loc):
        from quam_state_manager.core.diagnostics import Finding
        return Finding(severity="error", category="env_unknown_field", location=loc,
                       message="not a field", detail="x -- acknowledged by you on 2026-09-07",
                       jump_path=loc, acknowledged={"at": 1}, ack_key="k|" + loc)

    def _active(self, loc):
        from quam_state_manager.core.diagnostics import Finding
        return Finding(severity="error", category="env_unknown_field", location=loc,
                       message="not a field", jump_path=loc)

    def test_all_acknowledged_reads_healthy(self):
        html = _render([self._acked("Quam.twpa_ext"), self._acked("Quam.flux_crosstalk_max_v")])
        assert "No active issues" in html and "2 acknowledged by you" in html
        assert "diag-pill diag-error" not in html              # no "2 errors" pill
        assert 'class="diag-mini diag-error"' not in html       # no red domain badge
        assert html.count("diag-badge diag-acknowledged") == 2
        assert "diag-badge diag-error" not in html
        assert html.count("diag-row-collapsed") == 2
        assert "2 acknowledged &mdash; show" in html

    def test_mixed_counts_only_the_active_and_lists_it_first(self):
        html = _render([self._acked("Quam.twpa_ext"), self._active("Quam.other")])
        assert "1 error" in html and "2 errors" not in html
        assert "1 acknowledged" in html
        assert html.index("Quam.other") < html.index("diag-acked-toggle") < html.index("Quam.twpa_ext")

    def test_unacknowledged_render_carries_none_of_it(self):
        html = _render([self._active("Quam.x")])
        assert "diag-acked-toggle" not in html and "acknowledged" not in html
