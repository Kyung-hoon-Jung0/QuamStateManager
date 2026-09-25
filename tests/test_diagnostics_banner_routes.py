"""Route tests for the on-load diagnostics surfacing: the auto error-banner,
the tray health badge, and the waveform findings appearing in the Explorer feed.
"""

from __future__ import annotations

import json

from quam_state_manager.web.app import create_app

READOUT = "quam.components.pulses.SquareReadoutPulse"


def _state(readout_amp: float) -> dict:
    return {
        "qubits": {"q1": {
            "id": "q1",
            "resonator": {
                "opx_output": "#/wiring/qubits/q1/rr/opx_output",
                "opx_input": "#/wiring/qubits/q1/rr/opx_input",
                "operations": {"readout": {"__class__": READOUT,
                                           "length": 640, "amplitude": readout_amp}},
            },
        }},
        "qubit_pairs": {},
        "ports": {
            "mw_outputs": {"con1": {"1": {"1": {
                "band": 2, "upconverter_frequency": 7.0e9, "full_scale_power_dbm": 0}}}},
            "analog_outputs": {},
            "mw_inputs": {"con1": {"1": {"1": {}}}},
        },
    }


def _wiring() -> dict:
    return {"wiring": {"qubits": {"q1": {
        "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1",
               "opx_input": "#/ports/mw_inputs/con1/1/1"},
    }}}, "network": {"host": "x", "cluster_name": "t"}}


def _client(tmp_path, readout_amp):
    (tmp_path / "state.json").write_text(json.dumps(_state(readout_amp)), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    return client


def test_banner_pops_when_chip_has_error(tmp_path):
    body = _client(tmp_path, 1.5).get("/diagnostics/banner").get_data(as_text=True)
    assert "diag-error-banner" in body
    assert "would crash a node run" in body


def test_review_diagnostics_lands_on_the_first_error(tmp_path):
    """QA F-H: Review diagnostics landed on the Values warnings with the
    crash errors below the fold. The button scrolls to #diag-first-error, which
    the page renders exactly once, on the (now first) domain holding the error."""
    client = _client(tmp_path, 1.5)
    banner = client.get("/diagnostics/banner").get_data(as_text=True)
    assert 'hx-swap="innerHTML show:#diag-first-error:top"' in banner
    diag = client.get("/diagnostics", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert diag.count('id="diag-first-error"') == 1
    import re
    tag = re.search(r'<details[^>]*id="diag-first-error"[^>]*>', diag).group(0)
    assert 'data-domain="waveforms"' in tag
    first = re.search(r'<details[^>]*class="detail-section diag-domain"[^>]*>', diag).group(0)
    assert first == tag, "the error domain is the first section"


def test_banner_empty_when_chip_is_clean(tmp_path):
    # QA F-A: an EMPTY 200, never a 204 -- htmx 2.x does not swap a 204
    # (responseHandling {code:"204", swap:false}), so the slot kept the old
    # red banner after the error set emptied, until a full page reload.
    resp = _client(tmp_path, 0.3).get("/diagnostics/banner")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == ""


def test_banner_no_chip_is_empty_200(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    resp = app.test_client().get("/diagnostics/banner")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == ""


def test_shipped_htmx_still_skips_204_swaps():
    # the premise the 200-always contract rests on (if a later htmx swaps a
    # 204 the contract is merely redundant, never wrong)
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
          / "static" / "htmx.min.js").read_text(encoding="utf-8")
    assert '{code:"204",swap:false}' in js


# QA F-B: the dismissal signature must name WHICH values are errors, not only
# how many -- a new same-count error set used to match the dismissed
# "count:chip" string and stay hidden for the rest of the tab session.
def _two_qubit_client(tmp_path, amp1, amp2):
    st = _state(amp1)
    q2 = json.loads(json.dumps(st["qubits"]["q1"]))
    q2["id"] = "q2"
    q2["resonator"]["operations"]["readout"]["amplitude"] = amp2
    st["qubits"]["q2"] = q2
    (tmp_path / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    return client


def _sig(client):
    import re
    body = client.get("/diagnostics/banner").get_data(as_text=True)
    m = re.search(r'data-diag-sig="([^"]*)"', body)
    assert m, body[:300]
    return m.group(1)


def test_dismissal_sig_differs_for_a_new_same_count_error_set(tmp_path):
    c = _two_qubit_client(tmp_path, 1.5, 0.3)
    sig_q1 = _sig(c)
    c.post("/field/edit", data={"dot_path": "qubits.q1.resonator.operations.readout.amplitude",
                                "value": "0.3"})
    c.post("/field/edit", data={"dot_path": "qubits.q2.resonator.operations.readout.amplitude",
                                "value": "1.6"})
    sig_q2 = _sig(c)
    # same count, same chip ...
    assert sig_q1.split(":")[:2] == sig_q2.split(":")[:2]
    # ... but a different error set, so a dismissal of the first must not hide it
    assert sig_q1 != sig_q2


def test_dismissal_sig_is_stable_while_lowering_a_still_bad_value(tmp_path):
    # A13: the signature must NOT carry the value, or the banner would re-pop
    # on every edit while the user is fixing the chip
    c = _two_qubit_client(tmp_path, 1.5, 0.3)
    before = _sig(c)
    c.post("/field/edit", data={"dot_path": "qubits.q1.resonator.operations.readout.amplitude",
                                "value": "1.3"})
    assert _sig(c) == before


def test_summary_badge_reflects_error(tmp_path):
    body = _client(tmp_path, 1.5).get("/diagnostics/summary").get_data(as_text=True)
    assert "diag-error" in body and "issue" in body


def test_summary_badge_healthy_when_clean(tmp_path):
    body = _client(tmp_path, 0.3).get("/diagnostics/summary").get_data(as_text=True)
    assert "healthy" in body


def test_waveform_finding_in_explorer_feed(tmp_path):
    feed = _client(tmp_path, 1.5).get("/diagnostics/findings.json").get_json()
    # the waveform range finding is Explorer-jumpable → in the value_spec bucket
    cats = [f["category"] for f in feed["value_spec"]]
    assert "waveform_range" in cats
    assert any(f["jump_path"].endswith("readout.amplitude") for f in feed["value_spec"])


# QA F-N: the banner counts ERROR FINDINGS -- one edited amplitude that two
# pulses read is two errors, not "2 values" -- and its example is the first of
# THOSE errors, never the fixed "(e.g. a waveform sample outside the DAC
# range)" whatever the error kind (an unknown field, a missing port, ...).
_FIXED_EXAMPLE = "(e.g. a waveform sample outside the DAC range)"


def _client_for(tmp_path, st, wi):
    (tmp_path / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(wi), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    return client


def test_banner_counts_errors_not_values_for_one_shared_amplitude(tmp_path):
    import re
    st = _state(1.5)
    ops = st["qubits"]["q1"]["resonator"]["operations"]
    ops["readout2"] = dict(ops["readout"], amplitude="#../readout/amplitude")
    body = _client_for(tmp_path, st, _wiring()).get("/diagnostics/banner").get_data(as_text=True)
    text = re.sub(r"\s+", " ", body)
    assert "<b>2</b> errors on" in text, text[:400]
    assert " values on" not in text
    assert _FIXED_EXAMPLE not in text
    # the example names one of the counted errors
    assert "<code>qubits.q1.resonator.operations.readout" in text


def test_banner_example_follows_the_error_kind(tmp_path):
    import re
    wi = _wiring()
    wi["wiring"]["qubits"]["q1"]["rr"]["opx_output"] = "#/ports/mw_outputs/con1/1/7"
    body = _client_for(tmp_path, _state(0.3), wi).get("/diagnostics/banner").get_data(as_text=True)
    text = re.sub(r"\s+", " ", body)
    assert "<b>1</b> error on" in text, text[:400]
    assert "would crash a node run" in text
    assert "DAC range" not in text and "waveform" not in text
    assert "port 7" in text           # the missing port is what it names
    # QA F-N (review): an env finding's message opens with its own
    # "<Class>.<field>: " (state_env_validate composes it so), the same text
    # the banner prints as the location -- it read "FluxTunableTransmon.T1:
    # FluxTunableTransmon.T1: expected float ...". Once, also when the
    # aggregated location carries its " (N×)" suffix.
    from quam_state_manager.core import type_policy as tp
    from tests.test_type_policy import MANIFEST, _state as _env_state

    def _env_banner(tag, st):
        d = tmp_path / tag
        d.mkdir()
        c = _client_for(d, st, {"wiring": {}})
        app = c.application
        with app.app_context():
            ctx = app.config["contexts"][app.config["active_context"]]
            ctx["store"].type_policy = tp.TypePolicy(MANIFEST, {})
        return re.sub(r"\s+", " ", c.get("/diagnostics/banner").get_data(as_text=True))

    st = _env_state()
    del st["custom"]                            # the type mismatch is the only error
    st["qubits"]["qA1"]["f_01"] = "6.25e9"      # str in a float field: Quam.load() raises
    text = _env_banner("env1", st)
    assert "<b>1</b> error on" in text, text[:400]
    assert "<code>Transmon.f_01</code>: expected float, got str" in text, text[:400]
    assert text.count("Transmon.f_01") == 1, text[:400]
    st["qubits"]["qA2"] = dict(st["qubits"]["qA1"])
    text = _env_banner("env2", st)
    assert "<code>Transmon.f_01 (2×)</code>: expected float, got str" in text, text[:400]
    assert text.count("Transmon.f_01") == 1, text[:400]


def test_review_diagnostics_shows_the_errors_a_saved_filter_hid(tmp_path):
    """QA F-D: the pill choice persists (one global localStorage key), and the
    crash banner's "Review diagnostics" landed on a page whose saved filter hid
    the very errors it was about. The banner turns the error bucket back on, and
    any page whose filter hides errors says so next to the pills
    (diag_filter_entry_fragcheck.cjs, real app.js + real fragments)."""
    import shutil
    import subprocess
    from pathlib import Path

    import pytest
    if shutil.which("node") is None:
        pytest.skip("node not available")
    client = _client(tmp_path, 1.5)
    banner = client.get("/diagnostics/banner").get_data(as_text=True)
    assert "_diagShowBucket('error')" in banner
    diag = client.get("/diagnostics", headers={"HX-Request": "true"}).get_data(as_text=True)
    bp, dp = tmp_path / "banner.html", tmp_path / "diag.html"
    bp.write_text(banner, encoding="utf-8")
    dp.write_text(diag, encoding="utf-8")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(["node", str(root / "tests" / "diag_filter_entry_fragcheck.cjs"), str(bp), str(dp)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(root), timeout=180)
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "diag_filter_entry_fragcheck ok" in r.stdout


def test_the_crash_banner_is_not_shown_on_diagnostics_itself():
    """QA F-I: on /diagnostics the crash banner repeated the error pill, its
    Review button reloaded the page you were on, and at 1366x768 it pushed the
    pills below the fold. One CSS rule hides the slot while the LIVE pane holds
    the Diagnostics findings slot -- which exists only in _diagnostics.html, so
    the drag-drop previews (which include _diagnostics_list.html) keep it."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
    css = (root / "static" / "style.css").read_text(encoding="utf-8")
    # merged with QA F6: a class on <html> (app.js _syncDiagPageClass), never a
    # body:has() rule; the class is driven under jsdom in
    # diag_filter_entry_fragcheck.cjs (E7), real app.js + real fragments
    m = re.search(r"html\.diag-page-live\s+#diagnostics-banner-slot\s*\{([^}]*)\}", css)
    assert m, "the rule that hides the banner on /diagnostics is gone"
    assert re.search(r"display\s*:\s*none", m.group(1))
    app = (root / "static" / "app.js").read_text(encoding="utf-8")
    assert "!!document.querySelector('#table-pane #diag-findings')" in app
    owners = [p.name for p in (root / "templates").glob("*.html")
              if 'id="diag-findings"' in p.read_text(encoding="utf-8")]
    assert owners == ["_diagnostics.html"], owners
