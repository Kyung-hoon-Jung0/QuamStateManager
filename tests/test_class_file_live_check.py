"""The class-file check of a live write (docs/313 "The live-write check").

A gate macro using SM-made classes records ``extras.sm_class_files``. A write
that adds such a gate, or makes it the pair's active gate, lands only when
every recorded file is in the environment's quam_config folder with the
recorded content hash. Pinned per door that reaches
``working_copy.apply_to_live``: refused for a missing file, one changed byte
and an unknown environment (live files byte-equal, the reason in the door's
response); passes for a gate without the record and for an unrelated edit.
One more pin proves the check lives only in ``apply_to_live``.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from quam_state_manager.core import class_file_check, hub, safe_io, working_copy
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

PKG = Path(__file__).resolve().parent.parent / "quam_state_manager"
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
_SOURCE = b"class SmFlux:\n    pass\n"
_PAIR = "qA1-qA2"
_FAKE_PY = "C:/fake_env/python.exe"


def _entry(sha=None):
    return {"qclass": "quam_config.SmFlux.SmFlux", "file": "SmFlux.py",
            "sha256": sha or hashlib.sha256(_SOURCE).hexdigest(), "proportional": True}


def _state(active="cz_plain", *, extra_gate=False):
    macros = {
        "cz": f"#./{active}",
        "cz_plain": {"__class__": "lab.gates.FluxCZ", "amplitude": 0.1},
        "cz_other": {"__class__": "lab.gates.FluxCZ", "amplitude": 0.2},
        "cz_sm": {"__class__": "lab.gates.FluxCZ", "amplitude": 0.3,
                  "extras": {"sm_class_files": [_entry()]}},
    }
    if extra_gate:
        macros["cz_new"] = {"__class__": "lab.gates.FluxCZ", "amplitude": 0.4,
                            "extras": {"sm_class_files": [_entry()]}}
    return {"qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9, "T1": 2.0e-5},
                       "qA2": {"id": "qA2", "f_01": 5.1e9, "T1": 3.0e-5}},
            "qubit_pairs": {_PAIR: {"id": _PAIR, "qubit_control": "#/qubits/qA1",
                                    "qubit_target": "#/qubits/qA2", "macros": macros}},
            "active_qubit_names": ["qA1", "qA2"]}


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _live_bytes(live: Path):
    return (live / "state.json").read_bytes(), (live / "wiring.json").read_bytes()


@pytest.fixture(autouse=True)
def _inline_projection():
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    yield
    hub.set_inline(old)


@pytest.fixture
def folder_a(tmp_path, monkeypatch):
    """A tmp quam_config folder the fake environment imports."""
    a = tmp_path / "env_a" / "quam_config"
    a.mkdir(parents=True)
    (a / "SmFlux.py").write_bytes(_SOURCE)
    monkeypatch.setattr(class_file_check, "env_folder",
                        lambda python: a.resolve() if python == _FAKE_PY else (_ for _ in ()).throw(
                            AssertionError(python)))
    return a


def _break(folder_a: Path, how: str, monkeypatch):
    if how == "missing":
        (folder_a / "SmFlux.py").unlink()
    elif how == "changed":
        data = bytearray(_SOURCE)
        data[-2] = ord("x")                     # one changed byte
        (folder_a / "SmFlux.py").write_bytes(bytes(data))
    elif how == "no_env":
        monkeypatch.setattr(class_file_check, "_env_source", routes_mod._class_file_env)
    else:
        raise AssertionError(how)


def _reason_words(how: str):
    if how == "missing":
        return ["cz_sm", "SmFlux.py", "missing"]
    if how == "changed":
        return ["cz_sm", "SmFlux.py", "changed"]
    return ["cz_sm", "no environment is selected"]


BREAKS = ["missing", "changed", "no_env"]


# ======================================================================
# the core function, on a tmp working copy
# ======================================================================

def _wc_chip(tmp_path, state):
    live = tmp_path / "live"
    _write_chip(live, state)
    return live, working_copy.create(tmp_path / "inst", live)


def _stage(wc, mutate):
    s, w = safe_io.read_state_wiring(wc.working_folder)
    mutate(s)
    safe_io.write_state_wiring(wc.working_folder, s, w)


def _apply(wc, tmp_path, **kw):
    return working_copy.apply_to_live(
        wc, record=hub.Pending(tmp_path / "hist", "sm_apply", "human", "apply", entries=[]), **kw)


class TestCore:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setattr(class_file_check, "_env_source", lambda folder: _FAKE_PY)

    def _activate(self, s):
        s["qubit_pairs"][_PAIR]["macros"]["cz"] = "#./cz_sm"

    def _add(self, s):
        s["qubit_pairs"][_PAIR]["macros"]["cz_new"] = copy.deepcopy(
            s["qubit_pairs"][_PAIR]["macros"]["cz_sm"])

    @pytest.mark.parametrize("how", BREAKS)
    @pytest.mark.parametrize("change", ["activate", "add"])
    def test_refused_with_the_gate_and_file_named(self, tmp_path, folder_a, monkeypatch, how, change):
        live, wc = _wc_chip(tmp_path, _state())
        _stage(wc, self._activate if change == "activate" else self._add)
        _break(folder_a, how, monkeypatch)
        before = _live_bytes(live)
        with pytest.raises(working_copy.ClassFileRefusedError) as exc:
            _apply(wc, tmp_path)
        gate = "cz_sm" if change == "activate" else "cz_new"
        for word in _reason_words(how):
            assert (gate if word == "cz_sm" else word) in str(exc.value)
        assert _live_bytes(live) == before
        assert not (tmp_path / "hist" / "events.jsonl").exists() or \
            (tmp_path / "hist" / "events.jsonl").read_text() == "", "a refused write records nothing"

    def test_a_forced_push_is_refused_too(self, tmp_path, folder_a, monkeypatch):
        live, wc = _wc_chip(tmp_path, _state())
        _stage(wc, self._activate)
        _break(folder_a, "changed", monkeypatch)
        before = _live_bytes(live)
        with pytest.raises(working_copy.ClassFileRefusedError):
            _apply(wc, tmp_path, force=True)
        assert _live_bytes(live) == before

    @pytest.mark.parametrize("change", ["activate", "add"])
    def test_files_in_place_pass(self, tmp_path, folder_a, change):
        live, wc = _wc_chip(tmp_path, _state())
        _stage(wc, self._activate if change == "activate" else self._add)
        _apply(wc, tmp_path)
        macros = json.loads((live / "state.json").read_text())["qubit_pairs"][_PAIR]["macros"]
        assert macros["cz"] == "#./cz_sm" if change == "activate" else "cz_new" in macros

    @pytest.mark.parametrize("how", BREAKS)
    def test_a_gate_without_the_record_and_an_unrelated_edit_pass(self, tmp_path, folder_a,
                                                                   monkeypatch, how):
        live, wc = _wc_chip(tmp_path, _state())
        _break(folder_a, how, monkeypatch)
        _stage(wc, lambda s: (s["qubit_pairs"][_PAIR]["macros"].__setitem__("cz", "#./cz_other"),
                              s["qubits"]["qA1"].__setitem__("T1", 9e-5)))
        _apply(wc, tmp_path)
        doc = json.loads((live / "state.json").read_text())
        assert doc["qubit_pairs"][_PAIR]["macros"]["cz"] == "#./cz_other"
        assert doc["qubits"]["qA1"]["T1"] == 9e-5

    def test_a_chip_without_any_record_never_reads_live_or_asks_the_env(self, tmp_path, monkeypatch):
        st = _state()
        del st["qubit_pairs"][_PAIR]["macros"]["cz_sm"]
        live, wc = _wc_chip(tmp_path, st)
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        monkeypatch.setattr(class_file_check, "_env_source",
                            lambda f: (_ for _ in ()).throw(AssertionError("asked the env")))
        monkeypatch.setattr(class_file_check, "check_payload",
                            lambda *a: (_ for _ in ()).throw(AssertionError("checked")))
        _apply(wc, tmp_path)

    def test_the_active_gate_already_on_the_chip_is_not_checked(self, tmp_path, folder_a, monkeypatch):
        live, wc = _wc_chip(tmp_path, _state(active="cz_sm"))
        _break(folder_a, "missing", monkeypatch)
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        _apply(wc, tmp_path)
        assert json.loads((live / "state.json").read_text())["qubits"]["qA1"]["T1"] == 9e-5

    def test_an_unreadable_env_folder_refuses(self, tmp_path, monkeypatch):
        live, wc = _wc_chip(tmp_path, _state())
        _stage(wc, self._activate)

        def boom(python):
            raise class_file_check.ClassFileRefusedError("the lab check could not run")

        monkeypatch.setattr(class_file_check, "env_folder", boom)
        before = _live_bytes(live)
        with pytest.raises(working_copy.ClassFileRefusedError, match="quam_config folder"):
            _apply(wc, tmp_path)
        assert _live_bytes(live) == before


class TestOnePlace:
    def test_only_apply_to_live_calls_the_check(self):
        """Every door reaches the check through apply_to_live; no door keeps
        its own copy of it."""
        callers = []
        for path in PKG.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call):
                        f = node.func
                        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                        if name in ("check_payload", "gates_to_check", "_check_class_files"):
                            callers.append((path.name, fn.name, name))
        callers = sorted(set(callers))
        assert callers == [("class_file_check.py", "check_payload", "gates_to_check"),
                           ("working_copy.py", "_check_class_files", "check_payload"),
                           ("working_copy.py", "apply_to_live", "_check_class_files")], callers


# ======================================================================
# the doors
# ======================================================================

@pytest.fixture
def env(tmp_path, folder_a, monkeypatch):
    monkeypatch.setattr(class_file_check, "_env_source", lambda folder: _FAKE_PY)

    def make(state):
        live = tmp_path / "chips" / "live"
        _write_chip(live, state)
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        return {"app": app, "client": c, "live": live, "tmp": tmp_path, "a": folder_a}
    return make


def _ctx(e):
    with e["app"].app_context():
        return routes_mod._active_ctx()


def _edit(e, path, val, headers=None):
    r = e["client"].post("/field/edit", data={"dot_path": path, "value": val}, headers=headers or {})
    assert r.status_code == 200, r.data[:300]


def _activate(e, gate, headers=None):
    _edit(e, f"qubit_pairs.{_PAIR}.macros.cz", f"#./{gate}", headers)


def _live_active(e):
    return json.loads((e["live"] / "state.json").read_text())["qubit_pairs"][_PAIR]["macros"]["cz"]


def _text(r) -> str:
    """The body plus the toast an undo / redo answers in (``HX-Trigger``)."""
    trig = r.headers.get("HX-Trigger") or "{}"
    return r.get_data(as_text=True) + "\n" + json.dumps(json.loads(trig), ensure_ascii=False)


def _assert_refused(e, r, before, how):
    assert _live_bytes(e["live"]) == before, "the live files did not change"
    body = _text(r)
    for word in _reason_words(how):
        assert word in body, (word, body[:600])


class TestDoors:
    @pytest.mark.parametrize("how", BREAKS)
    def test_apply_press(self, env, monkeypatch, how):
        e = env(_state())
        _activate(e, "cz_sm")
        _break(e["a"], how, monkeypatch)
        before = _live_bytes(e["live"])
        r = e["client"].post("/state/apply-to-live")
        assert r.status_code == 409
        _assert_refused(e, r, before, how)

    @pytest.mark.parametrize("how", BREAKS)
    def test_sync_apply(self, env, monkeypatch, how):
        e = env(_state())
        _activate(e, "cz_sm")
        _break(e["a"], how, monkeypatch)
        before = _live_bytes(e["live"])
        r = e["client"].post("/state/sync", data={"mode": "apply"})
        assert r.get_json()["status"] == "error"
        _assert_refused(e, r, before, how)

    @pytest.mark.parametrize("how", BREAKS)
    def test_undo_that_makes_the_gate_active_again(self, env, monkeypatch, how):
        e = env(_state(active="cz_sm"))
        _activate(e, "cz_plain")                    # Make active: another gate
        assert e["client"].post("/state/apply-to-live").status_code == 200
        assert _live_active(e) == "#./cz_plain"
        _break(e["a"], how, monkeypatch)
        before = _live_bytes(e["live"])
        r = e["client"].post("/undo")
        _assert_refused(e, r, before, how)
        assert _live_active(e) == "#./cz_plain"

    @pytest.mark.parametrize("how", BREAKS)
    def test_redo(self, env, monkeypatch, how):
        e = env(_state())
        _activate(e, "cz_sm")
        assert e["client"].post("/state/apply-to-live").status_code == 200
        assert e["client"].post("/undo").status_code == 200
        assert _live_active(e) == "#./cz_plain"
        _break(e["a"], how, monkeypatch)
        before = _live_bytes(e["live"])
        r = e["client"].post("/redo")
        _assert_refused(e, r, before, how)

    def _run(self, e, state):
        root = e["tmp"] / "data"
        run = root / "2026-12-30" / "#31_08_spec_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "08_spec", "status": "successful", "run_start": "2026-12-30T01:00:00",
                         "run_end": "2026-12-30T01:00:01"},
            "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
            "id": 31, "parents": [], "created_at": "2026-12-30T01:00:00"}), encoding="utf-8")
        (run / "data.json").write_text("{}", encoding="utf-8")
        _write_chip(run / "quam_state", state)
        e["client"].post("/workspace/add", data={"folder": str(root)})
        return f"{routes_mod._folder_key(root)}:31"

    @pytest.mark.parametrize("how", BREAKS)
    def test_dataset_chip_apply_that_adds_a_gate(self, env, monkeypatch, how):
        e = env(_state())
        uid = self._run(e, _state(extra_gate=True))
        _break(e["a"], how, monkeypatch)
        before = _live_bytes(e["live"])
        r = e["client"].post(f"/dataset/{uid}/load-state?apply=1")
        assert _live_bytes(e["live"]) == before
        body = _text(r)
        for word in _reason_words(how):
            assert (word if word != "cz_sm" else "cz_new") in body, (word, body[:600])

    @pytest.mark.parametrize("how", BREAKS)
    def test_version_live_restore(self, env, monkeypatch, how):
        e = env(_state(active="cz_sm"))
        c = e["client"]
        _activate(e, "cz_plain")
        assert c.post("/state/apply-to-live").status_code == 200
        with e["app"].app_context():
            pre = [s for s in routes_mod._history().list_snapshots(_ctx(e)["path"])
                   if s.kind == "backup"][0].timestamp
        _break(e["a"], how, monkeypatch)
        before = _live_bytes(e["live"])
        r = c.post(f"/state-history/{pre}/restore-live?force=1")
        assert r.status_code == 409
        _assert_refused(e, r, before, how)

    @pytest.mark.parametrize("how", BREAKS)
    def test_auto_sync_disarms_with_the_reason(self, env, monkeypatch, how):
        e = env(_state())
        assert e["client"].post("/auto-apply/arm").status_code == 200
        _activate(e, "cz_sm")
        _break(e["a"], how, monkeypatch)
        before = _live_bytes(e["live"])
        r = e["client"].post("/state/apply-to-live")
        _assert_refused(e, r, before, how)
        assert _ctx(e).get("auto_apply") is None, "the session is disarmed"
        reason = json.loads(r.headers["HX-Trigger"])["autoApplyDisarm"]["reason"]
        for word in _reason_words(how):
            assert word in reason

    @pytest.mark.parametrize("how", BREAKS)
    def test_agent_apply(self, env, monkeypatch, how):
        from quam_state_manager.core import limits
        e = env(_state())
        agent = {"X-SM-Agent": "claude"}
        _activate(e, "cz_sm", headers=agent)
        limits.save(e["app"].instance_path, working_copy.key_for(_ctx(e)["path"]), {"mode": "auto"})
        _break(e["a"], how, monkeypatch)
        before = _live_bytes(e["live"])
        r = e["client"].post("/state/apply-to-live",
                             headers={**agent, "X-SM-Plan": "plan-7", "Accept": "application/json"})
        assert r.status_code == 409
        _assert_refused(e, r, before, how)

    # -- passes -------------------------------------------------------------

    @pytest.mark.parametrize("how", BREAKS)
    def test_apply_of_a_gate_without_the_record(self, env, monkeypatch, how):
        e = env(_state())
        _break(e["a"], how, monkeypatch)
        _activate(e, "cz_other")
        assert e["client"].post("/state/apply-to-live").status_code == 200
        assert _live_active(e) == "#./cz_other"

    @pytest.mark.parametrize("how", BREAKS)
    def test_auto_sync_of_a_gate_without_the_record(self, env, monkeypatch, how):
        e = env(_state())
        assert e["client"].post("/auto-apply/arm").status_code == 200
        _break(e["a"], how, monkeypatch)
        _activate(e, "cz_other")
        assert e["client"].post("/state/apply-to-live").status_code == 200
        assert _live_active(e) == "#./cz_other"
        assert _ctx(e).get("auto_apply") is not None

    @pytest.mark.parametrize("how", BREAKS)
    def test_unrelated_edit(self, env, monkeypatch, how):
        e = env(_state(active="cz_sm"))
        _break(e["a"], how, monkeypatch)
        _edit(e, "qubits.qA1.T1", "3.5e-5")
        assert e["client"].post("/state/apply-to-live").status_code == 200
        assert json.loads((e["live"] / "state.json").read_text())["qubits"]["qA1"]["T1"] == 3.5e-5

    def test_files_in_place_apply_lands(self, env):
        e = env(_state())
        _activate(e, "cz_sm")
        assert e["client"].post("/state/apply-to-live").status_code == 200
        assert _live_active(e) == "#./cz_sm"


class TestEnvSource:
    def test_the_open_chip_maps_to_its_projects_env(self, tmp_path, monkeypatch):
        from quam_state_manager.core import project_env
        live = tmp_path / "chips" / "live"
        _write_chip(live, _state())
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        monkeypatch.setattr(project_env, "run_env", lambda inst, project: _FAKE_PY)
        assert routes_mod._class_file_env(live) is None, "outside a request nothing is known"
        with app.app_context():
            assert routes_mod._class_file_env(live) == _FAKE_PY
            assert routes_mod._class_file_env(tmp_path / "other") is None
