"""Config Manual surface (2026-08-27): the sidebar item sits right below
Settings / Calculator, the window ships in the base shell with the house
search box, manual.js is loaded, and the per-key ? affordances exist on the
state surfaces. Behaviour is pinned by config_manual_selfcheck.cjs (jsdom)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"


def test_sidebar_item_sits_right_below_settings_and_calculator():
    base = (_TPL / "base.html").read_text(encoding="utf-8")
    tools = base.split('class="sidebar-tools"', 1)[1].split("sidebar-tools-divider", 1)[0]
    order = [m.group(1) for m in re.finditer(r'sidebar-tool-label">([^<]+)<', tools)]
    assert order[:4] == ["Help", "Settings", "Calculator", "Config Manual"], order
    assert 'id="manual-btn"' in tools and 'onclick="toggleConfigManual(this)"' in tools


def test_the_window_and_script_ship_in_the_shell(tmp_path):
    base = (_TPL / "base.html").read_text(encoding="utf-8")
    assert 'id="manual-popover"' in base and 'class="manual-header"' in base
    assert 'class="manual-search"' in base, "the house search box lives in the window"
    assert "manual.js" in base and base.index("search-query.js") < base.index("manual.js"), \
        "manual.js needs SearchQuery loaded first"
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    html = app.test_client().get("/").data.decode()
    assert 'id="manual-popover"' in html and 'id="manual-btn"' in html


def test_per_key_help_affordances_exist():
    bulk = (_TPL / "_bulkedit.html").read_text(encoding="utf-8")
    assert bulk.count('class="key-help-btn"') >= 2, "qubit + pair column headers"
    q = (_TPL / "_qubit_detail.html").read_text(encoding="utf-8")
    assert 'class="key-help-btn"' in q and 'data-help-path="{{ p.dot_path }}"' in q,         "a data attribute, never an inline onclick string (a key with a quote would end the script)"
    assert "openConfigManual({path: '" not in q and "openConfigManual({q: '" not in bulk
    js = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "tree-help" in js, "the Json tree rows carry the ? too"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_manual_reports_the_catalogue_state_and_the_window_is_resizable():
    from quam_state_manager.web.app import create_app
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(testing=True, instance_path=tmp)
        d = app.test_client().get("/api/manual").get_json()
        assert d["ok"] is True and d["catalog_state"] == "none"       # no chip, no env
    css = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    block = css[css.index(".manual-popover {"): css.index(".manual-hidden { display: none; }")]
    assert "resize: both" in block and "border-radius: 10px" in block and "--pico-primary" in block, \
        "the window is resizable, rounded and carries the SM-blue edge"
    assert "min-width: 380px" in block and "min-height: 260px" in block
    js = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static" / "manual.js").read_text(encoding="utf-8")
    assert "var SIZE_KEY = 'quam_manual_size';" in js and "new ResizeObserver(" in js


def test_config_manual_selfcheck():
    node = shutil.which("node")
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "config_manual_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ok - an undescribed key says so" in res.stdout


# ---------------------------------------------------------------------------
# docs/141 4l-review: the probe outcome is remembered, partial is never cached
# ---------------------------------------------------------------------------
import json as _json
import sys as _sys
import time as _time


def _fake_outcome(result):
    def run(cmd, work_dir, timeout, outcome, **kw):
        outcome["ok"] = True
        outcome["result"] = result
    return run


def test_a_partial_catalogue_is_served_but_never_cached(tmp_path, monkeypatch):
    from quam_state_manager.core import state_env_schema as ses
    cat = {"quam.components.pulses.SquarePulse": {"importable": True, "fields": {}, "category": "Pulses"}}
    monkeypatch.setattr(ses, "_run_script_outcome", _fake_outcome({
        "catalog": cat, "catalog_roots": {"quam.components": "ok", "quam_builder.common": "error: ImportError: boom"}, "versions": {}}))
    res = ses.probe_catalog(_sys.executable, ["a.B"], str(tmp_path))
    assert res["ok"] is True and res["partial"] is True and "partial" in res["error"] and "boom" in res["error"]
    assert res["catalog"] == cat
    assert ses.catalog_for_env(_sys.executable, str(tmp_path)) is None, "a partial catalogue is not the truth"
    # an ABSENT root (quam_builder not installed) is not a failure
    monkeypatch.setattr(ses, "_run_script_outcome", _fake_outcome({
        "catalog": cat, "catalog_roots": {"quam.components": "ok", "quam_builder.common": "absent"}, "versions": {}}))
    res = ses.probe_catalog(_sys.executable, ["a.B"], str(tmp_path))
    assert res["ok"] is True and not res["partial"] and res["error"] is None
    assert ses.catalog_for_env(_sys.executable, str(tmp_path)) == cat


def test_the_requested_class_set_is_unioned_and_a_new_class_re_probes(tmp_path, monkeypatch):
    from quam_state_manager.core import state_env_schema as ses
    seen = []

    def run(cmd, work_dir, timeout, outcome, **kw):
        spec = _json.loads((Path(work_dir) / "_classes.json").read_text(encoding="utf-8"))
        seen.append(sorted(spec["classes"]))
        outcome["ok"] = True
        outcome["result"] = {"catalog": {c: {"importable": True, "fields": {}} for c in spec["classes"]} | {"quam.X": {"importable": True, "fields": {}}},
                             "catalog_roots": {"quam.components": "ok"}, "versions": {}}
    monkeypatch.setattr(ses, "_run_script_outcome", run)
    ses.probe_catalog(_sys.executable, ["lab.A"], str(tmp_path))
    assert ses.catalog_for_env(_sys.executable, str(tmp_path), ["lab.A"]) is not None
    assert ses.catalog_for_env(_sys.executable, str(tmp_path), ["lab.B"]) is None, "a class this catalogue never saw makes it cold"
    ses.probe_catalog(_sys.executable, ["lab.B"], str(tmp_path))
    assert seen[-1] == ["lab.A", "lab.B"], "the second probe asks for the union"
    assert ses.catalog_requested(_sys.executable, str(tmp_path)) == ["lab.B", "lab.A"] or set(ses.catalog_requested(_sys.executable, str(tmp_path))) == {"lab.A", "lab.B"}
    assert ses.catalog_for_env(_sys.executable, str(tmp_path), ["lab.A", "lab.B"]) is not None


def test_a_failed_probe_is_remembered_not_relaunched(tmp_path, monkeypatch):
    from quam_state_manager.core import state_env_schema as ses
    from quam_state_manager.web import routes as R
    from quam_state_manager.core import config_generator as cg
    calls = []

    def probe(python_path, classes, inst, **kw):
        calls.append(1)
        return {"ok": False, "cached": False, "error": "no quam in this interpreter", "catalog": {}}
    monkeypatch.setattr(ses, "probe_catalog", probe)
    monkeypatch.setattr(cg, "get_selected_env", lambda inst: _sys.executable)
    R._catalog_outcome.pop(_sys.executable, None)
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(_json.dumps({"qubits": {"qA1": {"f_01": 5e9}}, "active_qubit_names": ["qA1"]}), encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    d = c.get("/api/manual").get_json()
    assert d["catalog_state"] in ("loading", "error")
    for _ in range(50):                                   # the probe thread lands
        if R._catalog_outcome.get(_sys.executable, {}).get("state") == "error":
            break
        _time.sleep(0.05)
    d = c.get("/api/manual").get_json()
    assert d["catalog_state"] == "error" and "no quam in this interpreter" in d["note"]
    c.get("/api/manual"); c.get("/api/manual/node?path=qubits.qA1")
    assert len(calls) == 1, f"one probe, remembered ({len(calls)} launched)"
    R._catalog_outcome.pop(_sys.executable, None)
