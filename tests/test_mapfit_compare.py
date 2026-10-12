import pytest
from quam_state_manager.core.mapfit.compare import compare,tolerance,contract_verdict


def test_comparison_verdict_delta_and_missing():
    assert compare(.98,.983,.003,"reference constant")["verdict"] == "agree"
    out = compare(.98,1.,.003,"reference constant",node_name="opt_amp_prefactor")
    assert out["verdict"] == "disagree"
    assert out["delta"] == pytest.approx(.02)
    assert out["node_name"] == "opt_amp_prefactor"
    assert compare(None,.9,.003,"reference constant")["reason"] == "no node value"
    assert compare(.9,None,.003,"reference constant")["verdict"] == "n/a"
    assert compare(.9,.9,None,"no sweep step")["reason"] == "no valid tolerance"


def test_tolerance_formulas_and_sources():
    assert tolerance("erroramp","a_pi") == (.003,"reference constant")
    assert tolerance("ridge","f_01",step=2,hwhm=40)[0] == 4
    assert tolerance("chevron","two_j_hz",node_error=2)[0] == 6
    assert tolerance("chevron","two_j_hz",node_value=100)[0] == 5
    assert tolerance("landscape","x",step=.2) == (.2,"one sweep step")
    assert tolerance("erroramp","a_pi",step=.01,circular=True) == (.01,"one sweep step")


def test_fault_wrong_old_is_display_only():
    assert contract_verdict({"old":.9,"value":.95},.8) == "display-only"
    assert contract_verdict({"old":.9,"value":.95},.9) == "verified"
    assert contract_verdict(None,.9) == "display-only"
