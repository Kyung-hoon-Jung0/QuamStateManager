"""The CZ gate dialog routes: plan, check, create, with a fake check runner.

The fake stands in for the lab's code only (cz_gate_routes.run_check); the
chip copies, the token, the rule and the write are the real ones."""
import copy
import json
import tempfile
from pathlib import Path

import pytest

from quam_state_manager.core import chip_copy, cz_gate_build as cgb
from quam_state_manager.web import cz_gate_routes as czr
from quam_state_manager.web.app import create_app
from tests.test_cz_gate_build import LAB_GATE, _chip, _lab_chip

SQUARE = "quam.components.pulses.SquarePulse"
PLAN, CHECK, CREATE = "/api/pulse/cz-gate/plan", "/api/pulse/cz-gate/check", "/api/pulse/cz-gate/create"


def _body(pairs=("q1-2",), name="cz_sq", cls=cgb.CZ_GATE, amp=0.2, **kw):
    b = {"pairs": list(pairs), "gate_name": name, "gate_class": cls,
         "pulses": {"flux_pulse_qubit": {"class": SQUARE,
                                         "fields": {"length": 48, "amplitude": amp}}}}
    if cls != cgb.CZ_GATE:
        b["pulses"] = {}
    b.update(kw)
    return b


class App:
    def __init__(self, tmp, state=None):
        live = Path(tmp) / "chips" / "live"
        live.mkdir(parents=True)
        (live / "state.json").write_text(json.dumps(state or _chip(4)), encoding="utf-8")
        (live / "wiring.json").write_text(json.dumps({"network": {"host": "1.1.1.1"}}),
                                          encoding="utf-8")
        self.app = create_app(testing=True, instance_path=str(Path(tmp) / "_inst"))
        self.c = self.app.test_client()
        r = self.c.post("/load", data={"folder": str(live)})
        assert r.status_code in (200, 302)

    @property
    def ctx(self):
        return next(v for v in self.app.config["contexts"].values() if v.get("type") == "quam")

    @property
    def store(self):
        return self.ctx["store"]

    def snap(self):
        s = self.store
        return json.dumps(s.state, sort_keys=False), json.dumps(s.wiring), len(s.change_log)


@pytest.fixture
def chip(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        a = App(tmp)
        yield a
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def _fake(fail=None, status="ok", checked=True):
    """A check runner that passes every gate (or fails one check of *fail*)."""
    calls = []

    def run(store, python_path, gates, creates, previews):
        calls.append({"gates": list(gates), "creates": list(creates), "previews": previews})
        with store._lock:
            seq = store.mutation_seq
        out = {"status": status, "checked": checked, "seq": seq,
               "store_id": chip_copy.store_id(store), "gates": [], "leftover": None,
               "why_not_checked": None, "chip_error": None, "error": None}
        for pair, gate in gates:
            bad = fail == pair
            checks = {c: {"status": "pass", "message": f"{c} ok"} for c in
                      ("load_config", "ops", "length", "preview", "timeline", "range")}
            if bad:
                checks["range"] = {"status": "fail", "message": "The output range is exceeded."}
            out["gates"].append({
                "pair": pair, "gate": gate, "checks": checks, "unchecked": [], "ran": True,
                "timeline": {"rows": [{"element": "q1.z", "port": ["con1", 1, 1],
                                       "operation": f"{gate}_pulse_q1_q2", "start_ns": 0,
                                       "end_ns": 48, "level": 0.2, "scale": 1.0,
                                       "samples": None, "constant": 0.2, "total": 48,
                                       "truncated": False, "sampling_rate": 1e9,
                                       "length_ns": 48, "range_v": 0.5, "offset_v": 0.0,
                                       "port_kind": "lf"}],
                             "notes": ["Times are program time before filters."],
                             "cannot_draw": None}})
        return out
    run.calls = calls
    return run


@pytest.fixture
def fake(monkeypatch):
    f = _fake()
    monkeypatch.setattr(czr, "run_check", f)
    monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
    return f


def _check(c, body):
    r = c.post(CHECK, json=body)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


# ---------------------------------------------------------------- dialog GET

def test_dialog_preselects_the_pair_and_lists_classes(chip):
    r = chip.c.get("/pulse/cz-gate/dialog?pair=q1-2")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and 'id="czg-dialog"' in html and 'class="ui-dialog ui-dialog--xl czg"' in html
    assert '<option value="q1-2" selected>' in html and 'id="czg-status-slot"' in html
    data = json.loads(html.split('id="czg-data">', 1)[1].split("</script>", 1)[0])
    assert data["selected"] == "q1-2"
    p = next(x for x in data["pairs"] if x["id"] == "q1-2")
    assert (p["control"], p["target"], p["moving_qubit"]) == ("q1", "q2", "control")
    known = {c["class"]: c for c in data["classes"] if c["layout"] == "known"}
    assert set(known) == {cgb.CZ_GATE, cgb.COUPLER_CZ_GATE} and all(c["available"] for c in known.values())
    off = [c for c in data["classes"] if not c["available"]]
    assert off and all(c["why"] for c in off)
    slot = known[cgb.CZ_GATE]["slots"][0]
    assert slot["field"] == "flux_pulse_qubit" and any(pc["class"] == SQUARE for pc in slot["pulse_classes"])


def test_dialog_unknown_pair_says_so(chip):
    html = chip.c.get("/pulse/cz-gate/dialog?pair=nope").get_data(as_text=True)
    assert "Pair not found" in html and '<option value="nope"' not in html


def test_dialog_lab_class_with_a_model_is_offered(tmp_path):
    a = App(tmp_path, _lab_chip())
    html = a.c.get("/pulse/cz-gate/dialog").get_data(as_text=True)
    data = json.loads(html.split('id="czg-data">', 1)[1].split("</script>", 1)[0])
    lab = next(c for c in data["classes"] if c["class"] == LAB_GATE)
    assert lab["available"] and lab["layout"] == "copied" and ["q1-2", "cz_tf"] in lab["models"]


@pytest.mark.parametrize("url,needle", [
    ("/pairs", 'data-czg-open data-czg-pair="q1-2"'),
    ("/qubits", 'data-czg-pulse="q1"'),
    ("/cz-plan", 'data-czg-open="czplan"'),
    ("/pulse/new", 'data-create-kind="cz_gate" data-czg-open'),
])
def test_every_entry_point_carries_the_one_opener(chip, url, needle):
    html = chip.c.get(url).get_data(as_text=True)
    assert needle in html, url


# ---------------------------------------------------------------- plan

def test_plan_is_pure_and_signed(chip):
    before = chip.snap()
    j = chip.c.post(PLAN, json=_body()).get_json()
    assert j["ok"] and j["pairs"][0]["ok"] and j["pairs"][0]["layout"] == "known"
    assert j["pairs"][0]["fields"]["flux_pulse_qubit"]["amplitude"] == "given"
    assert j["plan_sig"] == chip.c.post(PLAN, json=_body()).get_json()["plan_sig"]
    assert j["plan_sig"] != chip.c.post(PLAN, json=_body(amp=0.3)).get_json()["plan_sig"]
    assert chip.snap() == before


def test_plan_refuses_a_bad_name(chip):
    p = chip.c.post(PLAN, json=_body(name="xx")).get_json()["pairs"][0]
    assert not p["ok"] and any("cz_" in r for r in p["refusals"])


def test_plan_other_pairs_get_the_first_pairs_values(chip):
    j = chip.c.post(PLAN, json=_body(pairs=["q1-2", "q2-3"], amp=0.25)).get_json()
    second = j["pairs"][1]
    assert second["ok"] and second["pulses"]["flux_pulse_qubit"]["amplitude"] == 0.25


# ---------------------------------------------------------------- check + create

def test_check_then_create_is_one_group_and_one_undo_restores_bytes(chip, fake):
    before = chip.snap()
    j = _check(chip.c, _body())
    p = j["pairs"][0]
    assert p["verdict"] == {"allowed": True, "state": "verified", "reason": "", "headline": ""}
    assert p["timeline"]["lanes"][0]["key"] == "q1.z"
    assert [t["key"] for t in p["tiles"]] == ["lines", "length", "peak"]
    assert p["intent"][0]["intended"]["amplitude"] == 0.2
    pv = fake.calls[0]["previews"][("q1-2", "cz_sq")]
    assert pv == {("q1.z", "cz_sq_pulse_q1_q2"): 0.2}, pv
    assert chip.snap() == before, "the check writes nothing"
    r = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    out = r.get_json()
    assert r.status_code == 200 and out["ok"] and out["count"] == 1, out
    assert r.headers["HX-Trigger"] == "pulses-changed, diagnostics-changed"
    log = chip.store.change_log[before[2]:]
    assert len({e.group_id for e in log}) == 1 and len(log) == len(out["created"][0]["paths"])
    assert "cz_sq" in chip.store.merged["qubit_pairs"]["q1-2"]["macros"]
    assert "cz" not in chip.store.merged["qubit_pairs"]["q1-2"]["macros"]
    r = chip.c.post("/undo")
    assert r.status_code == 200
    assert chip.snap()[:2] == before[:2], "one undo gives the state back byte for byte"


def test_the_token_is_single_use_and_unknown_tokens_are_refused(chip, fake):
    j = _check(chip.c, _body())
    ok = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert ok.status_code == 200
    again = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert again.status_code == 409 and again.get_json()["check_again"]
    r = chip.c.post(CREATE, json={"check_token": "made-up", "plan_sig": j["plan_sig"]})
    assert r.status_code == 409 and "not known" in r.get_json()["error"]


def test_a_foreign_token_is_refused(chip, fake):
    j = _check(chip.c, _body())
    czr._TOKENS[j["check_token"]]["store_id"] = -1
    before = chip.snap()
    r = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 409 and "another chip" in r.get_json()["error"]
    assert chip.snap() == before


def test_a_changed_plan_after_the_check_is_refused(chip, fake):
    j = _check(chip.c, _body())
    other = chip.c.post(PLAN, json=_body(amp=3.0)).get_json()["plan_sig"]
    before = chip.snap()
    r = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": other})
    assert r.status_code == 409 and "inputs changed" in r.get_json()["error"]
    assert chip.snap() == before


def test_a_stale_store_is_409_and_nothing_is_written(chip, fake):
    j = _check(chip.c, _body())
    chip.ctx["modifier"].set_value("qubits.q3.z.operations.const.amplitude", 0.11)
    before = chip.snap()
    r = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 409 and r.get_json()["stale"]
    assert chip.snap() == before


def test_several_pairs_one_refused_two_written_in_one_group(chip, fake):
    pre = _check(chip.c, _body(pairs=["q2-3"]))
    chip.c.post(CREATE, json={"check_token": pre["check_token"], "plan_sig": pre["plan_sig"]})
    n0 = len(chip.store.change_log)
    j = _check(chip.c, _body(pairs=["q1-2", "q2-3", "q3-4"]))
    by = {p["pair"]: p for p in j["pairs"]}
    assert not by["q2-3"]["verdict"]["allowed"] and by["q2-3"]["refusals"]
    assert by["q1-2"]["verdict"]["allowed"] and by["q3-4"]["verdict"]["allowed"]
    out = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]}).get_json()
    assert [c["pair"] for c in out["created"]] == ["q1-2", "q3-4"]
    assert [s["pair"] for s in out["skipped"]] == ["q2-3"] and out["skipped"][0]["reason"]
    assert len({e.group_id for e in chip.store.change_log[n0:]}) == 1


def test_a_failed_check_is_not_created(chip, monkeypatch):
    monkeypatch.setattr(czr, "run_check", _fake(fail="q1-2"))
    monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
    j = _check(chip.c, _body(amp=3.0))
    assert not j["pairs"][0]["verdict"]["allowed"]
    before = chip.snap()
    r = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 400 and chip.snap() == before


def test_read_only_chip_is_refused(chip, fake):
    j = _check(chip.c, _body())
    chip.ctx["origin"] = "dataset_archive"
    before = chip.snap()
    r = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 409 and "read-only" in r.get_json()["error"]
    assert chip.snap() == before


def test_no_env_known_class_is_created_as_not_checked(chip, monkeypatch):
    monkeypatch.setattr(czr, "_env_python", lambda: None)
    j = _check(chip.c, _body())
    assert j["status"] == "no-env"
    v = j["pairs"][0]["verdict"]
    assert v["allowed"] and v["state"] == "not checked" and "environment" in v["reason"]
    out = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]}).get_json()
    assert out["ok"] and out["created"][0]["state"] == "not checked"


def test_no_env_copied_layout_is_refused(tmp_path, monkeypatch):
    a = App(tmp_path, _lab_chip())
    monkeypatch.setattr(czr, "_env_python", lambda: None)
    j = _check(a.c, _body(pairs=["q2-3"], name="cz_new", cls=LAB_GATE))
    p = j["pairs"][0]
    assert p["ok"] and not p["verdict"]["allowed"] and "copies the layout" in p["verdict"]["reason"]
    before = a.snap()
    r = a.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 400 and a.snap() == before
    from quam_state_manager.core.hub_index import close_readers
    close_readers()


def test_the_client_cannot_supply_a_verdict(chip, monkeypatch):
    monkeypatch.setattr(czr, "run_check", _fake(fail="q1-2"))
    monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
    j = _check(chip.c, _body())
    before = chip.snap()
    r = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"],
                                  "verdicts": {"q1-2": {"allowed": True}}, "allowed": True})
    assert r.status_code == 400 and chip.snap() == before


# ---------------------------------------------------------------- jsdom driver

def test_dialog_selfcheck_on_the_real_rendered_dialog(chip, tmp_path):
    import os
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env.setdefault("NODE_PATH", str(root / "node_modules"))
    if not any((Path(p) / "jsdom").exists() for p in env["NODE_PATH"].split(os.pathsep)):
        pytest.skip("jsdom not installed")
    html = chip.c.get("/pulse/cz-gate/dialog?pair=q1-2").get_data(as_text=True)
    f = tmp_path / "dialog.html"
    f.write_text(html, encoding="utf-8")
    env["CZG_DIALOG"] = str(f)
    r = subprocess.run([node, str(root / "tests" / "cz_gate_dialog_selfcheck.cjs")], env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "(132 assertions)" in r.stdout


def test_headline_names_line_value_and_limit():
    entry = {"checks": {"range": {"status": "fail", "message": "long"}}}
    tl = {"lanes": [{"key": "q1.z", "limit_v": [-2.5, 2.5], "segments": [
        {"peak_v": 3.0, "min_v": 0.0, "flags": ["over_range"]}]}]}
    assert czr.headline(entry, tl, "long") == "q1.z would play 3 V. This port's limit is 2.5 V."
    assert czr.headline({"checks": {"ops": {"status": "fail"}}}, tl, "why") == "why"


def test_field_kinds_come_from_the_value_type_never_from_its_digits():
    assert czr.value_kind(0.0) == "float" and czr.value_kind(92.0) == "float"
    assert czr.value_kind(2) == "int" and czr.value_kind(True) == "bool" and czr.value_kind("x") == "str"
    assert czr.value_kind(None, "float") == "float" and czr.value_kind(None) == "any"
    specs = czr.field_specs({"__class__": SQUARE, "length": 48, "amplitude": 0.0, "axis_angle": None,
                             "id": "x", "linked": "#./a"})
    assert specs["amplitude"] == {"kind": "float", "label": "Amplitude", "unit": "V", "optional": False}
    assert specs["length"]["kind"] == "int" and specs["axis_angle"]["optional"] is True
    assert "id" not in specs and "linked" not in specs
    raw = czr.field_specs({"__class__": "lab_pkg.Unknown", "neg_offset_v": 0.0})
    assert raw["neg_offset_v"] == {"kind": "float", "label": "neg_offset_v", "unit": "", "optional": False}


def test_plan_reports_the_model_settings_and_takes_macro_fields(tmp_path):
    a = App(tmp_path, _lab_chip())
    j = a.c.post(PLAN, json=_body(pairs=["q2-3"], name="cz_new", cls=LAB_GATE)).get_json()
    p = j["pairs"][0]
    assert p["model_gate"] == ["q1-2", "cz_tf"] and "phase_shift_control" in p["reset"]
    mode = next(s for s in p["settings"] if s["key"] == "mode")
    assert mode["value"] == "fast" and mode["kind"] == "str" and mode["source"] == cgb.FROM_MODEL
    j = a.c.post(PLAN, json=_body(pairs=["q2-3"], name="cz_new", cls=LAB_GATE, macro_fields={"mode": "slow"})).get_json()
    mode = next(s for s in j["pairs"][0]["settings"] if s["key"] == "mode")
    assert mode["value"] == "slow" and mode["source"] == cgb.GIVEN
    from quam_state_manager.core.hub_index import close_readers
    close_readers()


def test_untouched_copied_values_are_created_bit_for_bit_and_null_settings_listed(tmp_path, fake):
    s = _lab_chip()
    m = s["qubit_pairs"]["q1-2"]["macros"]["cz_tf"]
    m["flux_pulse_control"]["amplitude"] = 0.19033062162534012
    m["note"] = None
    a = App(tmp_path, s)
    body = _body(pairs=["q2-3"], name="cz_new", cls=LAB_GATE)
    p = a.c.post(PLAN, json=body).get_json()["pairs"][0]
    note = next(x for x in p["settings"] if x["key"] == "note")
    assert note["optional"] and note["value"] is None and note["kind"] == "any"
    assert not any(x["key"] in ("phase_shift_control", "flux_pulse_control", "__class__") for x in p["settings"])
    j = _check(a.c, body)
    r = a.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 200, r.get_json()
    made = a.store.merged["qubit_pairs"]["q2-3"]["macros"]["cz_new"]["flux_pulse_control"]
    assert made["amplitude"] == 0.19033062162534012
    from quam_state_manager.core.hub_index import close_readers
    close_readers()


def test_check_and_create_carry_the_top_bars_sync_state(chip, fake):
    j = _check(chip.c, _body())
    assert j["sync"]["auto_push"] is False and "state" in j["sync"]
    out = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]}).get_json()
    assert out["sync"]["auto_push"] is False and out["sync"]["auto_pull"] is False and out["sync"]["readable"] is True
    assert out["sync"]["state"] in ("mine", "staged") and out["sync"]["unapplied"] >= 1
    chip.ctx["auto_apply"] = {"push": True, "pull": False}
    assert _check(chip.c, _body(name="cz_sq2"))["sync"]["auto_push"] is True


def test_create_records_the_check_and_a_recreated_gate_is_never_called_checked(chip, fake, monkeypatch):
    j = _check(chip.c, _body())
    out = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]}).get_json()
    assert out["created"][0]["record"] == "checked"
    row = chip.c.get("/pulse/cz-gate/status?pair=q1-2&gate=cz_sq").get_data(as_text=True)
    assert "Checked with the lab" in row
    assert chip.c.post("/undo").status_code == 200
    assert "cz_sq" not in chip.store.merged["qubit_pairs"]["q1-2"]["macros"]
    monkeypatch.setattr(czr, "_env_python", lambda: None)
    from quam_state_manager.core import gate_check as _gc
    monkeypatch.setattr(czr, "run_check", lambda store, py, g, c, pv: _gc.check_gates(store, py, g, c, previews=pv))
    j = _check(chip.c, _body(amp=0.33))
    out = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]}).get_json()
    assert out["ok"] and out["created"][0]["record"] == "not checked"
    row = chip.c.get("/pulse/cz-gate/status?pair=q1-2&gate=cz_sq").get_data(as_text=True)
    assert "not checked" in row and "Checked with the lab" not in row


def test_a_record_left_by_an_undone_create_never_matches_a_new_gate(chip, fake):
    j = _check(chip.c, _body())
    chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    chip.c.post("/undo")
    j2 = _check(chip.c, _body(amp=0.33))
    tok = czr._TOKENS[j2["check_token"]]
    tok["checks"] = {}
    chip.c.post(CREATE, json={"check_token": j2["check_token"], "plan_sig": j2["plan_sig"]})
    row = chip.c.get("/pulse/cz-gate/status?pair=q1-2&gate=cz_sq").get_data(as_text=True)
    assert "Checked with the lab" not in row



# ---------------------------------------------------------------- review fixes (server)

def _live_bytes(chip):
    folder = Path(chip.ctx["path"])
    return (folder / "state.json").read_bytes(), (folder / "wiring.json").read_bytes()


def test_a_write_that_logs_then_raises_is_undone_by_group(chip, fake, monkeypatch):
    before = chip.snap()
    j = _check(chip.c, _body())
    mod = chip.ctx["modifier"]
    real = mod.create_subtree
    calls = []

    def flaky(path, value, **kw):
        calls.append(path)
        entry = real(path, value, **kw)          # the entry is logged ...
        if len(calls) == 2:
            raise RuntimeError("index update failed")   # ... then the write raises
        return entry
    monkeypatch.setattr(mod, "create_subtree", flaky)
    r = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code >= 400 and "rolled back" in r.get_json()["error"]
    assert chip.snap() == before, "the working copy and the change log are as before"


def test_an_undo_that_fails_is_never_called_rolled_back(chip, fake, monkeypatch):
    j = _check(chip.c, _body())
    mod = chip.ctx["modifier"]
    real = mod.create_subtree
    calls = []

    def flaky(path, value, **kw):
        calls.append(path)
        entry = real(path, value, **kw)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return entry
    monkeypatch.setattr(mod, "create_subtree", flaky)
    monkeypatch.setattr(mod, "undo", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("undo broke")))
    r = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    body = r.get_json()
    assert r.status_code == 500 and body["partial"] and "rolled back" not in body["error"]
    assert "press Ctrl+Z" in body["error"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "0.2", [1], {"a": 1}])
def test_the_server_refuses_a_value_of_the_wrong_kind(chip, bad):
    b = _body()
    b["pulses"]["flux_pulse_qubit"]["fields"]["amplitude"] = bad
    p = chip.c.post(PLAN, json=b).get_json()["pairs"][0]
    assert not p["ok"] and any("amplitude" in r for r in p["refusals"])


def test_an_int_field_refuses_a_fraction_on_the_server(chip):
    b = _body()
    b["pulses"]["flux_pulse_qubit"]["fields"]["length"] = 16.5
    p = chip.c.post(PLAN, json=b).get_json()["pairs"][0]
    assert not p["ok"] and any("length" in r and "whole number" in r for r in p["refusals"])


def test_a_float_field_holding_an_integer_takes_a_fraction():
    assert czr.value_kind(1, "float") == "float"
    assert czr.value_problem(0.5, "float") is None
    assert czr.kind_mismatch(True, "int")


def test_the_range_sentence_names_the_offset_and_the_range():
    lane = {"key": "q1.z", "limit_v": [-0.7, 0.3], "offset_v": 0.2}
    assert czr.range_sentence(lane, 0.4) == (
        "q1.z would play 0.4 V on top of its 0.2 V offset: 0.6 V, above the port's 0.5 V range.")
    plain = {"key": "q1.z", "limit_v": [-0.5, 0.5], "offset_v": 0.0}
    assert czr.range_sentence(plain, 3.0) == "q1.z would play 3 V. This port's limit is 0.5 V."


def test_the_agent_edit_lock_refuses_the_create(chip, fake):
    j = _check(chip.c, _body())
    before = chip.snap()
    chip.app.config["agent_edit_lock"] = {"path": chip.ctx["path"], "node": "plan"}
    try:
        # call the door itself (no before-request guard), so the door's own refusal is what is pinned
        with chip.app.test_request_context(CREATE, method="POST",
                                           json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]}):
            from flask import make_response
            r = make_response(czr.api_cz_gate_create())
    finally:
        chip.app.config.pop("agent_edit_lock", None)
    assert r.status_code >= 400 and chip.snap() == before


def test_the_live_files_are_unchanged_after_a_create(chip, fake):
    live = _live_bytes(chip)
    j = _check(chip.c, _body())
    r = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 200 and _live_bytes(chip) == live



# ---------------------------------------------------------------- tokens

def test_a_token_expires_after_its_time(chip, fake, monkeypatch):
    j = _check(chip.c, _body())
    now = czr.time.time()
    monkeypatch.setattr(czr.time, "time", lambda: now + czr.TOKEN_TTL_S + 1)
    r = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 409 and "not known" in r.get_json()["error"]


def test_only_the_newest_tokens_are_kept(chip, fake):
    first = _check(chip.c, _body())["check_token"]
    for _ in range(czr._TOKENS_MAX):
        czr._remember({"store_id": 0, "seq": 0, "plan_sig": "x", "body": {}, "verdicts": {}, "sigs": {}})
    assert first not in czr._TOKENS and len(czr._TOKENS) <= czr._TOKENS_MAX


def test_two_creates_racing_on_one_token_create_once(chip, fake):
    import threading
    j = _check(chip.c, _body())
    codes = []

    def go():
        with chip.app.test_client() as c:
            codes.append(c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]}).status_code)
    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert sorted(codes) == [200, 409]
    assert list(chip.store.merged["qubit_pairs"]["q1-2"]["macros"]).count("cz_sq") == 1
