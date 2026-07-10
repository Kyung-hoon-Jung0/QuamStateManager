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
