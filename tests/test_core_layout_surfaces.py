"""The static core budget on its three surfaces: Chip Status diagnostics, the
Load check route, and the wizard's build review."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator, core_layout as cl, diagnostics, project_env
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.web.app import create_app
from tests.test_core_layout import Chip
from tests.test_load_check import probe, _check  # noqa: F401  (fixture)

SPEC = Path("D:/work_laptop/study/2026-11-14_qsm-core-layout-spec/decisions.md")
EXTRACT = SPEC.parent / "sm_impl" / "extract_surface_sentences.py"

# Copied from the spec by the extraction script (word for word).
SPEC_SENTENCES = {
    "LINE_PREFIX": "Core layout warning: {message}",
    "FIX_REVIEW": "Fix: tick Share one core per qubit (xy + readout), or move elements off this FEM.",
    "RISK_NOTE": ("Known risk: on QOP 3.8.1 (another device), with xy and resonator on one core, a resonator "
                  "update_frequency inside a program made later x180 pulses have no effect; the simulator did "
                  "not show it; it depended on the program layout. Not tested on QOP 3.7.3; a hardware test is "
                  "planned."),
    "PANEL_STILL_OVER": ("Static count still {firm_after} / {limit} on {con} FEM {fem} after the "
                         "recommendation; move elements off that FEM."),
    "PANEL_ROUTES": ("Core layout does not change digital routes; remove digital outputs or move elements to "
                     "another FEM."),
}
OVER = "10 / 8 cores on con1 FEM 1 (MW-FEM); routes 0 / 10 (estimated)"


def _over_chip(n=5, core=False):
    c = Chip()
    for i in range(1, n + 1):
        name = f"q{i}_core" if core else None
        c.mw_qubit(f"q{i}", core_xy=name, core_rr=name)
    return c.s


# --- exact strings ------------------------------------------------------------
def test_surface_sentences_match_spec():
    for k, v in SPEC_SENTENCES.items():
        assert getattr(cl, k) == v, k


def test_surface_sentences_match_the_spec_file_by_script():
    if not (SPEC.is_file() and EXTRACT.is_file()):
        pytest.skip("spec folder not on this machine")
    ns: dict = {}
    exec(compile(EXTRACT.read_text(encoding="utf-8"), str(EXTRACT), "exec"), ns)
    got = ns["extract"](SPEC.read_text(encoding="utf-8"))
    assert got == SPEC_SENTENCES


def test_wizard_risk_note_is_the_constant():
    html = Path("quam_state_manager/web/templates/_generate.html").read_text(encoding="utf-8")
    assert cl.RISK_NOTE in html


# --- Chip Status / diagnostics ----------------------------------------------------
def test_diagnostics_collects_the_core_finding_and_warns():
    store = QuamStore.from_dicts(_over_chip(), {})
    found = [f for f in diagnostics._lint_state_uncached(store, incremental=False)
             if f.category == "core_layout"]
    assert [f.message for f in found] == [OVER]
    lines = found[0].detail.split("\n")
    assert lines[1] == cl.FIX_DIAGNOSTICS and lines[-1] == cl.DETAIL_OTHER_CAUSE
    assert diagnostics.summarize(found)["warning"] == 1 and found[0].severity == "warning"
    assert diagnostics.domain_of("core_layout") == "connectivity"


def test_diagnostics_quiet_within_limit():
    store = QuamStore.from_dicts(_over_chip(core=True), {})
    assert not [f for f in diagnostics._lint_state_uncached(store, incremental=False)
                if f.category == "core_layout"]


# --- Load check -----------------------------------------------------------------
@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip"
    live.mkdir()
    (live / "state.json").write_text(json.dumps(_over_chip()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.1"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    ctx = next(iter(app.config["contexts"].values()))
    ctx["qualibrate_project"] = "alpha"
    project_env.remember(app.instance_path, "alpha", sys.executable, "confirmed")
    return {"app": app, "client": c, "live": live, "ctx": ctx}


def test_load_check_carries_core_findings_and_stays_ok(env, probe):  # noqa: F811
    body = _check(env).get_json()
    assert body["ok"] is True
    cf = body["core_findings"]
    assert [f["line"] for f in cf] == ["Core layout warning: " + OVER]
    assert cf[0]["kind"] == "fem_budget" and cl.FIX_RECOMMEND in cf[0]["detail"]
    assert cl.FIX_DIAGNOSTICS not in cf[0]["detail"]


def test_diagnostics_page_lists_the_finding_line_by_line(env):
    html = env["client"].get("/diagnostics").get_data(as_text=True)
    if OVER not in html:
        html = env["client"].get("/diagnostics/list").get_data(as_text=True)
    assert OVER in html
    # The Diagnostics page has no Recommend button: its line says where the button is.
    assert "<br><code>" + cl.FIX_DIAGNOSTICS + "</code>" in html
    assert cl.FIX_RECOMMEND not in html


def test_load_check_js_renders_the_warning_under_loads_ok():
    js = Path("quam_state_manager/web/static/load_check.js").read_text(encoding="utf-8")
    assert re.search(r"setStatus\('ok', 'Loads OK'[^;]*coreWarnHtml\(data\.core_findings\)", js)


# --- Review -------------------------------------------------------------------------
def test_review_summary_shape():
    s = cl.review_summary(_over_chip())
    assert [f["line"] for f in s["findings"]] == ["Core layout warning: " + OVER]
    assert cl.FIX_REVIEW in s["findings"][0]["detail"]
    fem = s["table"]["fems"][0]
    assert fem["over"] and fem["total"] == "Total 10 / 8 cores" and len(fem["rows"]) == 10
    assert {r["core"] for r in fem["rows"]} == {cl.REASON_OWN_UNSET}
    assert s["risk_note"] is None
    on = cl.review_summary(_over_chip(core=True))
    assert on["findings"] == [] and on["table"]["shared_groups"] == 5 and on["risk_note"] == cl.RISK_NOTE
    assert {r["source"] for r in on["table"]["fems"][0]["rows"]} == {"core"}


def test_review_route_overage_says_cores_cannot_fix_it():
    c = Chip()
    c.mw_qubit("q1", core_xy="a", core_rr="a")
    c.digital("q1", "xy", "con1", 1, n=11)
    s = cl.review_summary(c.s)
    assert cl.PANEL_ROUTES in s["findings"][0]["detail"]


def test_no_ports_no_core_section():
    assert cl.review_summary({"qubits": {"q1": {"id": "q1"}}}) is None
    out: dict = {"ok": True}
    assert config_generator.annotate_core_layout(out, {"qubits": {}}, {}) is None
    assert "core_layout" not in out


def test_unresolved_count_is_said_on_the_total_line():
    c = Chip()
    c.mw_qubit("q1")
    c.el("q2", "xy", None, opx_output="#/wiring/qubits/q2/xy/opx_output")
    s = cl.review_summary(c.s)
    total = s["table"]["fems"][0]["total"]
    assert "may be too low: 1 port references not read" in total
    assert "unresolved_ports" in [f["kind"] for f in s["findings"]]


def test_count_failed_is_shown(monkeypatch):
    monkeypatch.setattr(cl, "_core_budget", lambda *a: (_ for _ in ()).throw(KeyError("x")))
    s = cl.review_summary(_over_chip())
    assert [f["kind"] for f in s["findings"]] == ["count_failed"]


# --- real builds (lab env) -------------------------------------------------------------
def _live_build(tmp_path, pin):
    from tests.test_core_layout_build import _env, _five_qubit_spec, _build
    python = _env()
    spec = _five_qubit_spec(twpa=False)
    spec["populate"]["options"] = {"pin_cores": pin}
    out = tmp_path / ("on" if pin else "off")
    outcome, _ = _build(python, spec, out)
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    wr = json.loads((out / "wiring.json").read_text(encoding="utf-8"))
    config_generator.annotate_core_layout(outcome, st, wr)
    return outcome


def test_real_build_checkbox_off_warns(tmp_path):
    o = _live_build(tmp_path, False)
    assert o["ok"]
    lines = [f["line"] for f in o["core_layout"]["findings"]]
    assert lines and lines[0].startswith("Core layout warning: 10 / 8 cores on con1 FEM 1 (MW-FEM); ")


def test_real_build_checkbox_on_shares_five_groups(tmp_path):
    o = _live_build(tmp_path, True)
    cl_ = o["core_layout"]
    assert cl_["findings"] == [] and cl_["table"]["shared_groups"] == 5
    assert cl_["risk_note"] == cl.RISK_NOTE


# --- selfchecks (node + jsdom) -------------------------------------------------------
@pytest.mark.parametrize("name,marker", [
    ("generate_core_layout_selfcheck.cjs", "generate_core_layout_selfcheck ok ("),
    ("load_check_selfcheck.cjs", "load_check_selfcheck ok"),
])
def test_selfchecks_pass(name, marker):
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(Path("tests") / name)], capture_output=True, text=True,
                       encoding="utf-8", timeout=300)
    if r.returncode == 2 or "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and marker in r.stdout, r.stdout + r.stderr
