"""FB-12 port test: script building, amplitude cap, credentials, run/stop routes."""
from __future__ import annotations

import ast
import json
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.core import port_test as pt
from quam_state_manager.core import port_test_runs as ptr


def _config():
    return {
        "controllers": {"con1": {"fems": {
            "1": {"type": "MW", "analog_outputs": {
                "1": {"band": 2, "full_scale_power_dbm": -2, "upconverter_frequency": 7.4e9, "sampling_rate": 1e9},
                "2": {"band": 2, "full_scale_power_dbm": 4, "upconverter_frequency": 5.1e9, "sampling_rate": 1e9}},
                  "analog_inputs": {"1": {"band": 2, "downconverter_frequency": 7.4e9}}},
            "5": {"type": "LF", "analog_outputs": {"1": {"offset": 0.0, "filter": {"exponential": [[-0.01, 100]]}}}},
        }}},
        "elements": {
            "q1.xy": {"MWInput": {"port": ["con1", 1, 2], "upconverter": 1}, "intermediate_frequency": -1e8,
                      "operations": {"x180": "q1.xy.x180.pulse", "big": "q1.xy.big.pulse"}},
            "q1.resonator": {"MWInput": {"port": ["con1", 1, 1], "upconverter": 1},
                             "MWOutput": {"port": ["con1", 1, 1]}, "intermediate_frequency": 5e7,
                             "time_of_flight": 300, "smearing": 0,
                             "operations": {"readout": "q1.ro.pulse"}},
            "q1.z": {"singleInput": {"port": ["con1", 5, 1]}, "operations": {"const": "q1.z.const.pulse"}},
        },
        "pulses": {
            "q1.xy.x180.pulse": {"operation": "control", "length": 40,
                                 "waveforms": {"I": "q1.xy.x180.I", "Q": "q1.xy.zero"}, "digital_marker": "ON"},
            "q1.xy.big.pulse": {"operation": "control", "length": 40,
                                "waveforms": {"I": "q1.xy.big.I", "Q": "q1.xy.zero"}},
            "q1.ro.pulse": {"operation": "measurement", "length": 1000, "digital_marker": "ON",
                            "waveforms": {"I": "q1.ro.I", "Q": "q1.xy.zero"}, "integration_weights": {"iw1": "q1.ro.iw1"}},
            "q1.z.const.pulse": {"operation": "control", "length": 100, "waveforms": {"single": "q1.z.c"}},
        },
        "waveforms": {"q1.xy.x180.I": {"type": "arbitrary", "samples": [0.0, 0.2, 0.4, 0.2, 0.0]},
                      "q1.xy.big.I": {"type": "constant", "sample": 0.9},
                      "q1.xy.zero": {"type": "constant", "sample": 0.0},
                      "q1.ro.I": {"type": "constant", "sample": 0.05},
                      "q1.z.c": {"type": "constant", "sample": 0.1}},
        "digital_waveforms": {"ON": {"samples": [[1, 0]]}},
        "integration_weights": {"q1.ro.iw1": {"cosine": [[1.0, 1000]], "sine": [[0.0, 1000]]}},
    }


class TestReadBack:
    def test_element_info_reads_the_port_and_the_power_from_the_config(self):
        i = pt.element_info(_config(), "q1.xy")
        assert i["port"] == ["con1", 1, 2] and i["kind"] == "mw"
        assert i["lo"] == 5.1e9 and i["rf"] == 5.1e9 - 1e8
        x = next(o for o in i["operations"] if o["name"] == "x180")
        assert x["peak"] == pytest.approx(0.4)
        assert x["dbm"] == pytest.approx(4 + 20 * __import__("math").log10(0.4))

    def test_elements_on_port(self):
        c = _config()
        assert pt.elements_on_port(c, "con1", 1, 2) == ["q1.xy"]
        assert pt.elements_on_port(c, "con1", "1", "1") == ["q1.resonator"]
        assert pt.elements_on_port(c, "con1", 1, 1, "input") == ["q1.resonator"]
        assert pt.elements_on_port(c, "con1", 5, 1) == ["q1.z"]


class TestBuild:
    def test_chip_basis_keeps_the_element_mapping_verbatim(self):
        c = _config()
        cfg, plan = pt.build(c, "q1.xy", "pulse", "chip", operation="x180")
        el = cfg["elements"]["q1.xy"]
        assert el["MWInput"] == c["elements"]["q1.xy"]["MWInput"]
        assert el["intermediate_frequency"] == -1e8
        assert cfg["controllers"]["con1"]["fems"]["1"]["analog_outputs"] == \
            {"2": c["controllers"]["con1"]["fems"]["1"]["analog_outputs"]["2"]}
        assert plan["expected_peak"] == pytest.approx(0.4) and plan["amp_scale"] == 1.0

    def test_raw_basis_holds_only_the_port(self):
        cfg, plan = pt.build(_config(), "q1.xy", "cw", "raw", amplitude=0.1)
        assert list(cfg["elements"]) == ["port_test"]
        assert cfg["controllers"]["con1"]["fems"]["1"]["analog_outputs"] == {
            "2": _config()["controllers"]["con1"]["fems"]["1"]["analog_outputs"]["2"]}
        assert "5" not in cfg["controllers"]["con1"]["fems"]

    def test_cw_above_the_cap_is_refused(self):
        with pytest.raises(pt.PortTestError):
            pt.build(_config(), "q1.xy", "cw", "chip", amplitude=0.6, amp_cap=0.5)

    def test_an_operation_above_the_cap_plays_scaled(self):
        _cfg, plan = pt.build(_config(), "q1.xy", "pulse", "chip", operation="big", amp_cap=0.3)
        assert plan["amp_scale"] == pytest.approx(0.3 / 0.9)
        assert plan["expected_peak"] == pytest.approx(0.3)

    def test_the_pulse_train_carries_a_marker_on_a_dig_output(self):
        cfg, plan = pt.build(_config(), "q1.xy", "pulse", "chip", operation="x180",
                             marker_port=["con1", 1, 3])
        assert cfg["controllers"]["con1"]["fems"]["1"]["digital_outputs"] == {"3": {}}
        assert cfg["elements"]["q1.xy"]["digitalInputs"]["port_test_trigger"]["port"] == ["con1", 1, 3]
        op_pulse = cfg["elements"]["q1.xy"]["operations"][plan["operation"]]
        assert cfg["pulses"][op_pulse]["digital_marker"] == pt.MARKER

    def test_loopback_keeps_the_digital_waveform_its_pulse_names(self):
        cfg, _plan = pt.build(_config(), "q1.resonator", "loopback", "chip")
        assert "ON" in cfg["digital_waveforms"]
        assert cfg["integration_weights"]

    def test_loopback_needs_a_measurement_element(self):
        with pytest.raises(pt.PortTestError):
            pt.build(_config(), "q1.xy", "loopback", "chip")

    @pytest.mark.parametrize("test,el", [("cw", "q1.xy"), ("pulse", "q1.xy"), ("loopback", "q1.resonator"),
                                         ("pulse", "q1.z")])
    @pytest.mark.parametrize("basis", ["chip", "raw"])
    def test_the_script_is_valid_python_with_the_plan_in_it(self, test, el, basis):
        cfg, plan = pt.build(_config(), el, test, basis)
        text = pt.render_script(cfg, plan, chip_label="demo")
        tree = ast.parse(text)
        names = {n.targets[0].id: n for n in tree.body if isinstance(n, ast.Assign)}
        assert ast.literal_eval(names["CONFIG"].value) == cfg
        assert ast.literal_eval(names["TEST"].value) == test
        assert "close_other_machines=False" in text and "busy_jobs" in text

    def test_the_sm_module_never_imports_qm(self):
        src = Path(pt.__file__).read_text(encoding="utf-8")
        head = src.split("_SCRIPT = ")[0]
        assert "import qm" not in head and "from qm" not in head


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, s, k, v):
        self.store[(s, k)] = v

    def get_password(self, s, k):
        return self.store.get((s, k))

    def delete_password(self, s, k):
        self.store.pop((s, k), None)


@pytest.fixture
def kr(monkeypatch):
    k = FakeKeyring()
    monkeypatch.setattr(ptr, "_keyring", lambda: k)
    return k


@pytest.fixture
def inst(tmp_path, kr):
    return tmp_path / "inst"


class TestCredentials:
    def test_the_password_goes_to_the_keyring_only_and_is_shown_masked(self, inst, kr):
        ptr.save_settings(inst, {"saas": {"email": "a@b.c", "host": "h.example"}})
        assert ptr.save_password(inst, ptr.load_settings(inst), "s3cret-pw") == "keyring"
        assert kr.store == {(ptr.KEYRING_SERVICE, "saas:a@b.c@h.example"): "s3cret-pw"}
        for f in inst.rglob("*"):
            assert not f.is_file() or b"s3cret-pw" not in f.read_bytes(), f
        assert not (inst / "port_test" / "secrets.json").exists()
        view = ptr.public_view(inst)
        assert view["saas"]["password_saved"] is True and "s3cret-pw" not in json.dumps(view)
        assert view["saas"]["password_where"] == "OS keyring"
        assert ptr.load_password(inst, ptr.load_settings(inst))[0] == "s3cret-pw"
        ptr.remove_password(inst, ptr.load_settings(inst))
        assert ptr.public_view(inst)["saas"]["password_saved"] is False and not kr.store

    def test_without_a_keyring_a_password_is_refused_and_never_written(self, inst, monkeypatch):
        monkeypatch.setattr(ptr, "_keyring", lambda: None)
        ptr.save_settings(inst, {"saas": {"email": "a@b.c", "host": "h"}})
        with pytest.raises(ptr.KeyringUnavailable, match="keyring"):
            ptr.save_password(inst, ptr.load_settings(inst), "pw2")
        for f in inst.rglob("*"):
            assert not f.is_file() or b"pw2" not in f.read_bytes(), f
        assert ptr.public_view(inst)["keyring_available"] is False

    def test_a_refusing_keyring_is_not_replaced_by_a_file(self, inst, kr, monkeypatch):
        def boom(*a):
            raise RuntimeError("locked")
        monkeypatch.setattr(kr, "set_password", boom)
        ptr.save_settings(inst, {"saas": {"email": "a@b.c", "host": "h"}})
        with pytest.raises(ptr.KeyringUnavailable):
            ptr.save_password(inst, ptr.load_settings(inst), "pw3")
        assert not (inst / "port_test" / "secrets.json").exists()

    def test_an_old_plain_text_secrets_file_moves_into_the_keyring(self, inst, kr):
        ptr.save_settings(inst, {"saas": {"email": "a@b.c", "host": "h"}})
        old = inst / "port_test" / "secrets.json"
        old.write_text(json.dumps({"saas:a@b.c@h": "legacy-pw"}))
        assert ptr.load_password(inst, ptr.load_settings(inst)) == ("legacy-pw", "keyring")
        assert not old.exists()

    def test_an_old_secrets_file_is_deleted_even_without_a_keyring(self, inst, monkeypatch):
        monkeypatch.setattr(ptr, "_keyring", lambda: None)
        ptr.save_settings(inst, {"saas": {"email": "a@b.c", "host": "h"}})
        old = inst / "port_test" / "secrets.json"
        old.write_text(json.dumps({"saas:a@b.c@h": "legacy-pw"}))
        assert ptr.public_view(inst)["saas"]["password_saved"] is False
        assert not old.exists()

    def test_keyring_is_a_core_dependency(self):
        import tomllib
        deps = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
        assert any(d.split(">")[0].split("=")[0].strip().lower() == "keyring"
                   for d in deps["project"]["dependencies"])


class _FakeProc:
    def __init__(self, argv, **kw):
        self.argv = argv
        self.work = Path(argv[-1])
        self.rc = None
        self.stdin = self
        self.got = ""

    def write(self, s):
        self.got += s

    def close(self):
        self.closed = True

    def flush(self):
        pass

    def poll(self):
        if self.rc is None and (self.work / "stop").exists():
            (self.work / "_result.json").write_text(json.dumps({"ok": True, "mode": "opx", "adc": {}}))
            self.rc = 0
        return self.rc

    def kill(self):
        self.rc = -9

    @property
    def returncode(self):
        return self.rc


class TestRuns:
    def test_one_run_at_a_time_and_stop_writes_the_flag(self, inst):
        r = ptr.Runs(inst)
        procs = []

        def popen(argv, **kw):
            procs.append(_FakeProc(argv, **kw))
            return procs[-1]
        rid = r.start("py", "print(1)", {"mode": "opx", "target": {"host": "x"}}, {}, popen=popen)["id"]
        assert json.loads(procs[0].got)["mode"] == "opx"
        assert not any("x" == a for a in procs[0].argv), "the request never rides argv"
        with pytest.raises(RuntimeError):
            r.start("py", "print(1)", {}, {}, popen=popen)
        assert r.status(rid)["state"] == "running"
        assert r.stop(rid)
        assert (procs[0].work / "stop").exists()
        assert r.status(rid)["state"] == "done"

    def test_a_page_that_stops_asking_stops_the_run(self, inst, monkeypatch):
        monkeypatch.setattr(ptr, "HEARTBEAT_S", 0.5)
        r = ptr.Runs(inst)
        holder = []
        r.start("py", "x", {}, {}, popen=lambda a, **k: holder.append(_FakeProc(a)) or holder[-1])
        deadline = time.time() + 5
        while time.time() < deadline and not (holder[0].work / "stop").exists():
            time.sleep(0.1)
        assert (holder[0].work / "stop").exists()
        assert r.current["stop_why"] == "the page was left"

    def test_unknown_opening_qm_state_is_reported_and_can_be_cleared(self, inst, monkeypatch):
        r = ptr.Runs(inst)
        holder = []
        rid = r.start("py", "x", {"mode": "opx", "target": {"host": "h"}}, {"target": "opx"},
                      popen=lambda a, **k: holder.append(_FakeProc(a)) or holder[-1])["id"]
        (holder[0].work / "_status.json").write_text(json.dumps({
            "phase": "opening a QM",
            "qm_config_fingerprint": "abc123",
            "qm_config_name": "port-test:cw:chip:q1.xy",
        }), encoding="utf-8")
        holder[0].rc = 1
        st = r.status(rid)
        assert st["unknown_qm_state"] is True
        assert st["phase"] == "unknown state: the QM may be open"

        def fake_run(argv, **kwargs):
            assert "--close-matching" in argv
            (holder[0].work / "_close_matching.json").write_text(json.dumps({
                "ok": True,
                "open_qms": [
                    {"qm_id": "a", "fingerprint": "abc123", "matches": True},
                    {"qm_id": "b", "fingerprint": "zzz", "matches": False},
                ],
                "closed_qms": ["a"],
            }), encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        out = r.close_matching_open_qms(rid)
        assert out["closed_qms"] == ["a"] and r.current is None
        cleared = json.loads((holder[0].work / "_status.json").read_text(encoding="utf-8"))
        assert cleared["cleaned"] is True


@pytest.fixture
def client(tmp_path, monkeypatch, kr):
    from quam_state_manager.web.app import create_app
    from quam_state_manager.web import routes
    live = tmp_path / "chip"
    live.mkdir()
    (live / "state.json").write_text(json.dumps({"qubits": {"q1": {"id": "q1"}}, "qubit_pairs": {}}))
    (live / "wiring.json").write_text(json.dumps({"network": {"host": "1.1.1.1"}}))
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    with app.test_request_context():
        routes._store().generated_config = _config()
        routes._store().generated_config_meta = {"basis_hash": routes._config_state_hash(routes._store())}
    monkeypatch.setattr("quam_state_manager.core.config_generator.get_selected_env", lambda inst: "py")
    started = []
    orig = ptr.Runs.start

    def fake_start(self, python, script, request, meta, popen=None):
        started.append(request)
        return orig(self, python, script, request, meta,
                              popen=lambda a, **k: _FakeProc(a))
    monkeypatch.setattr(ptr.Runs, "start", fake_start)
    c.started = started
    yield c
    from quam_state_manager.core.hub_index import close_readers
    close_readers()


H = {"Origin": "http://localhost"}


class TestRoutes:
    def test_the_page_lists_the_elements_on_a_port_and_reads_back(self, client):
        html = client.get("/port-test?con=con1&slot=1&port=2&io=output").get_data(as_text=True)
        assert 'id="pt-port"' in html and "con1/1/2" in html
        html = client.get("/port-test?element=q1.resonator").get_data(as_text=True)
        assert "time of flight 300" in html

    def test_download_is_a_python_attachment(self, client):
        r = client.get("/port-test/script.py?element=q1.xy&test=pulse&basis=raw&op=x180")
        assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
        ast.parse(r.get_data(as_text=True))

    def test_hardware_needs_the_warning_and_saas_needs_a_password(self, client):
        client.post("/port-test/settings", json={"opx": {"host": "10.0.0.1"}}, headers=H)
        r = client.post("/port-test/run", json={"element": "q1.xy", "test": "cw", "basis": "chip",
                                               "target": "opx"}, headers=H)
        assert r.status_code == 400 and "fridge" in r.get_json()["error"]
        r = client.post("/port-test/run", json={"element": "q1.xy", "test": "cw", "target": "saas"}, headers=H)
        assert r.status_code == 400 and not client.started

    def test_a_saas_run_carries_the_password_only_on_stdin(self, client, tmp_path):
        client.post("/port-test/settings", json={"saas": {"email": "a@b.c", "host": "h"},
                                                 "password": "pw-xyz"}, headers=H)
        r = client.post("/port-test/run", json={"element": "q1.xy", "test": "cw", "target": "saas",
                                               "amp": "0.2"}, headers=H)
        assert r.status_code == 200, r.get_json()
        assert client.started[-1]["target"]["password"] == "pw-xyz"
        st = client.get("/port-test/status").get_data(as_text=True)
        page = client.get("/port-test?element=q1.xy").get_data(as_text=True)
        assert "pw-xyz" not in st and "pw-xyz" not in page
        for p in (tmp_path / "_inst").rglob("*"):
            if p.is_file():
                assert b"pw-xyz" not in p.read_bytes(), p
        assert client.post("/port-test/stop", json={"id": r.get_json()["id"]}, headers=H).get_json()["stopping"]


class _Stubborn(_FakeProc):
    def poll(self):
        return self.rc


def test_a_simulator_run_is_not_killed_at_the_hardware_grace(tmp_path, monkeypatch, kr):
    monkeypatch.setattr(ptr, "HEARTBEAT_S", 0.2)
    monkeypatch.setattr(ptr, "STOP_GRACE_S", 0.3)
    r = ptr.Runs(tmp_path)
    procs = []
    r.start("py", "x", {}, {"target": "saas"}, popen=lambda a, **k: procs.append(_Stubborn(a)) or procs[-1])
    r2 = ptr.Runs(tmp_path / "b")
    r2.start("py", "x", {}, {"target": "opx"}, popen=lambda a, **k: procs.append(_Stubborn(a)) or procs[-1])
    time.sleep(3.0)
    assert procs[0].rc is None, "a simulator run must end at its own check, not be killed"
    assert procs[1].rc == -9


def test_the_instrument_hover_card_offers_the_port_test(client):
    html = client.get("/instrument").get_data(as_text=True)
    assert 'id="popup-port-test"' in html and "/port-test?" in html
