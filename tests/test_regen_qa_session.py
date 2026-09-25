"""Server-side pins for the Re-generate session findings from the 2026-09
real-browser QA pass (package gen-session).

F9                 /regenerate/reconstruct names the folder it actually READ
                   when "Load different…" posts one; the loaded chip's name is
                   kept only for the default (working-copy) path.
regenerate-r2-18   an unticked scripts export writes NO build-script bundle
                   (a None scripts_dir used to mean "the legacy folder"), and
                   the outcome names the real folder + whether it lies inside
                   the output folder, so the report stops claiming "written to
                   the output folder" for a folder somewhere else.
F8 (generate)      one build per output folder at a time: /generate/build and
                   /regenerate/build refuse (409, busy) a second build into a
                   folder a build is still writing, never queue it, and
                   release the folder when the build ends (a crash included).
F20                a re-generate's report is kept in the hash-keyed .regen
                   sidecar, and the overwrite question names the user's own,
                   unchanged build and hands its report back.
regenerate-r2-35   the build names the displayed Populate values that changed
                   in the source since the wizard read it and asks first
                   (ack builds; no stamp = an older client, unchanged).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import regenerate
from quam_state_manager.web.app import create_app
from tests.test_web import _gen_valid_spec, _make_state, _make_wiring


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    return app.test_client()


# ── F9 ────────────────────────────────────────────────────────────────────
class TestReconstructSourceName:
    def test_an_explicit_folder_is_named_after_itself(self, client, tmp_path):
        loaded = _chip(tmp_path / "loaded_chip")
        other = _chip(tmp_path / "other_chip")
        client.post("/load", data={"folder": str(loaded)})
        resp = client.post("/regenerate/reconstruct", json={"folder": str(other)})
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body["source_folder"] == str(other)
        assert body["source_name"] == "other_chip", (
            "Load different… showed the LOADED chip's name over another "
            "chip's counts: " + repr(body["source_name"]))

    def test_the_default_path_keeps_the_loaded_chip_name(self, client, tmp_path):
        loaded = _chip(tmp_path / "loaded_chip")
        client.post("/load", data={"folder": str(loaded)})
        resp = client.post("/regenerate/reconstruct", json={})
        body = resp.get_json()
        assert resp.status_code == 200, body
        # the folder read is the working copy — its key must never be the label
        assert body["source_name"] == "loaded_chip"
        assert Path(body["source_folder"]).name != "loaded_chip"

    def test_a_generic_container_folder_names_the_chip(self, client, tmp_path):
        loaded = _chip(tmp_path / "loaded_chip")
        other = _chip(tmp_path / "LabB" / "quam_state")
        client.post("/load", data={"folder": str(loaded)})
        body = client.post("/regenerate/reconstruct",
                           json={"folder": str(other)}).get_json()
        assert body["source_name"] == "LabB"


# ── regenerate-r2-18 (core) ───────────────────────────────────────────────
def _fake_build_into(monkeypatch):
    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps({"qubits": {"q1": {}}}))
        (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
        return {"ok": True, "status": "ok", "error": None, "result": {}}
    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)


def _old_chip(tmp_path: Path) -> Path:
    old = tmp_path / "old"
    old.mkdir()
    (old / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {}}, "active_qubit_names": []}))
    (old / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
    return old


class TestScriptsExport:
    def test_unticked_export_writes_no_bundle(self, tmp_path, monkeypatch):
        _fake_build_into(monkeypatch)
        old = _old_chip(tmp_path)
        new = tmp_path / "new"
        out = regenerate.run_regenerate("py", old, {"qubits": ["q1"]}, new,
                                        scripts_enabled=False)
        assert out["ok"] is True
        assert out["merge"] is not None           # the rebuild itself happened
        assert not (new / "build_scripts").exists(), \
            "an unticked export still wrote <out>/build_scripts"
        assert not (new / "state_gen_scripts").exists()
        assert out["script"] is None
        assert out["script_in_output"] is None

    def test_the_default_folder_is_named_and_inside(self, tmp_path, monkeypatch):
        _fake_build_into(monkeypatch)
        new = tmp_path / "new"
        out = regenerate.run_regenerate("py", _old_chip(tmp_path),
                                        {"qubits": ["q1"]}, new)
        assert out.get("script_error") is None, out.get("script_error")
        assert out["script"] == str(new / "build_scripts")
        assert (new / "build_scripts" / "02_build_machine.py").exists()
        assert out["script_in_output"] is True

    def test_a_folder_elsewhere_is_reported_as_elsewhere(self, tmp_path, monkeypatch):
        _fake_build_into(monkeypatch)
        new = tmp_path / "new"
        shared = tmp_path / "shared" / "state_gen_scripts"
        out = regenerate.run_regenerate("py", _old_chip(tmp_path),
                                        {"qubits": ["q1"]}, new, scripts_dir=shared)
        assert out.get("script_error") is None, out.get("script_error")
        assert out["script"] == str(shared)
        assert (shared / "02_build_machine.py").exists()
        assert out["script_in_output"] is False

    def test_a_sibling_with_the_output_as_prefix_is_not_inside(
            self, tmp_path, monkeypatch):
        # "new_scripts" starts with "new" as a STRING but is not under it.
        _fake_build_into(monkeypatch)
        out = regenerate.run_regenerate(
            "py", _old_chip(tmp_path), {"qubits": ["q1"]}, tmp_path / "new",
            scripts_dir=tmp_path / "new_scripts")
        assert out["script_in_output"] is False


# ── regenerate-r2-18 (route) ──────────────────────────────────────────────
class TestBuildRouteCarriesTheCheckbox:
    @pytest.fixture(autouse=True)
    def _all_capabilities(self, monkeypatch):
        from quam_state_manager.core import config_generator
        from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
        manifest = {
            "ok": True, "cached": False, "error": None, "versions": {},
            "capabilities": {c: {"available": True, "detail": ""} for c in CATALOG_IDS},
        }
        monkeypatch.setattr(config_generator, "probe_capabilities",
                            lambda *a, **k: manifest)

    def test_scripts_enabled_reaches_run_regenerate(self, client, tmp_path, monkeypatch):
        got = {}

        def fake_run(py, src, spec, out, timeout=300, **kw):
            got.clear()
            got.update(kw)
            return {"ok": True, "status": "ok", "error": None, "merge": None}

        monkeypatch.setattr(regenerate, "run_regenerate", fake_run)
        client.post("/generate/select-env", json={"python": sys.executable})
        src = tmp_path / "src"
        src.mkdir()
        base = {"spec": _gen_valid_spec(), "output_path": str(tmp_path / "out"),
                "source_folder": str(src)}
        resp = client.post("/regenerate/build", json={**base, "scripts_enabled": False})
        assert resp.status_code == 200, resp.get_json()
        assert got["scripts_enabled"] is False
        resp = client.post("/regenerate/build", json=base)     # an older client
        assert resp.status_code == 200, resp.get_json()
        assert got["scripts_enabled"] is True
        resp = client.post("/regenerate/build", json={**base, "scripts_enabled": True})
        assert got["scripts_enabled"] is True


# ── F8 (generate): one build per output folder at a time ──────────────────
class TestOneBuildPerFolder:
    """Two builds into one folder both passed the empty-folder guard (check-
    then-act) and both wrote it: a double press, or two SM windows. The
    second must be refused while the first runs — never queued behind it."""

    @pytest.fixture(autouse=True)
    def _all_capabilities(self, monkeypatch):
        from quam_state_manager.core import config_generator
        from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
        manifest = {
            "ok": True, "cached": False, "error": None, "versions": {},
            "capabilities": {c: {"available": True, "detail": ""} for c in CATALOG_IDS},
        }
        monkeypatch.setattr(config_generator, "probe_capabilities",
                            lambda *a, **k: manifest)

    @staticmethod
    def _held_build(monkeypatch, target, attr):
        """Replace target.attr with a build whose FIRST call blocks until
        released (later calls return at once)."""
        import threading
        entered, release = threading.Event(), threading.Event()
        calls = []

        def fake(*a, **kw):
            calls.append(a)
            if len(calls) == 1:
                entered.set()
                assert release.wait(20), "the held build was never released"
            return {"ok": True, "status": "ok", "error": None,
                    "result": {"qubits": [], "qubit_pairs": []}, "merge": None}
        monkeypatch.setattr(target, attr, fake)
        return entered, release, calls

    def _race(self, client, url, first, second, entered, release):
        import threading
        box = {}

        def run():
            box["first"] = client.post(url, json=first)
        t = threading.Thread(target=run)
        t.start()
        try:
            assert entered.wait(20), "the first build never started"
            box["second"] = client.post(url, json=second)
        finally:
            release.set()
            t.join(20)
        return box["first"], box["second"]

    def test_generate_build_refuses_a_second_build_into_the_same_folder(
            self, client, tmp_path, monkeypatch):
        from quam_state_manager.core import config_generator
        entered, release, calls = self._held_build(
            monkeypatch, config_generator, "run_generator")
        client.post("/generate/select-env", json={"python": sys.executable})
        out = str(tmp_path / "out")
        body = {"spec": _gen_valid_spec(), "output_path": out}
        # the second spelling differs only by a trailing separator
        r1, r2 = self._race(client, "/generate/build", body,
                            {**body, "output_path": out + "\\" if sys.platform == "win32"
                             else out + "/"}, entered, release)
        assert r1.status_code == 200, r1.get_json()
        assert r2.status_code == 409, r2.get_json()
        assert r2.get_json().get("busy") is True
        assert len(calls) == 1, "the second build ran the generator too"
        # released after the first finished: the folder can be built again
        again = client.post("/generate/build", json=body)
        assert again.status_code == 200, again.get_json()
        assert len(calls) == 2

    def test_another_folder_is_not_held_up(self, client, tmp_path, monkeypatch):
        from quam_state_manager.core import config_generator
        entered, release, calls = self._held_build(
            monkeypatch, config_generator, "run_generator")
        client.post("/generate/select-env", json={"python": sys.executable})
        spec = _gen_valid_spec()
        r1, r2 = self._race(client, "/generate/build",
                            {"spec": spec, "output_path": str(tmp_path / "a")},
                            {"spec": spec, "output_path": str(tmp_path / "b")},
                            entered, release)
        assert r1.status_code == 200 and r2.status_code == 200, r2.get_json()
        assert len(calls) == 2

    def test_a_crashed_build_releases_the_folder(self, client, tmp_path, monkeypatch):
        from quam_state_manager.core import config_generator

        def boom(*a, **kw):
            raise RuntimeError("generator crashed")
        monkeypatch.setattr(config_generator, "run_generator", boom)
        client.application.config["PROPAGATE_EXCEPTIONS"] = False
        client.post("/generate/select-env", json={"python": sys.executable})
        body = {"spec": _gen_valid_spec(), "output_path": str(tmp_path / "out")}
        assert client.post("/generate/build", json=body).status_code == 500
        ok_build = {"ok": True, "status": "ok", "error": None, "result": {}}
        monkeypatch.setattr(config_generator, "run_generator", lambda *a, **k: ok_build)
        assert client.post("/generate/build", json=body).status_code == 200, \
            "a crashed build left its folder claimed"

    def test_regenerate_build_refuses_a_second_build_into_the_same_folder(
            self, client, tmp_path, monkeypatch):
        entered, release, calls = self._held_build(
            monkeypatch, regenerate, "run_regenerate")
        client.post("/generate/select-env", json={"python": sys.executable})
        src = tmp_path / "src"
        src.mkdir()
        body = {"spec": _gen_valid_spec(), "output_path": str(tmp_path / "out"),
                "source_folder": str(src)}
        r1, r2 = self._race(client, "/regenerate/build", body, body, entered, release)
        assert r1.status_code == 200, r1.get_json()
        assert r2.status_code == 409, r2.get_json()
        assert len(calls) == 1


# ── F20: a re-generate's report is kept beside the chip it built ─────────
class TestOwnBuildReport:
    """A reload mid-build lost the report (it lived only in the page), and the
    next Generate called the user's own fresh build "a chip that would be
    OVERWRITTEN". The report now rides the hash-keyed .regen sidecar, and the
    overwrite question names the chip as SM's own, unchanged since."""

    @staticmethod
    def _built(folder: Path, report=None) -> Path:
        from quam_state_manager.core import regen_spec
        chip = _chip(folder)
        state = json.loads((chip / "state.json").read_text(encoding="utf-8"))
        wiring = json.loads((chip / "wiring.json").read_text(encoding="utf-8"))
        regen_spec.write_spec_sidecar(chip, {"qubits": ["qA1"]}, state, wiring)
        if report is not None:
            regen_spec.attach_build_report(chip, report, "D:/src/chip")
        return chip

    def test_report_round_trips_while_the_chip_is_unchanged(self, tmp_path):
        chip = self._built(tmp_path / "out", {"ok": True, "merge": {"carried": 3}})
        own = regenerate.own_build(chip)
        assert own is not None and own["report"] == {"ok": True, "merge": {"carried": 3}}
        assert own["source_folder"] == "D:/src/chip" and own["built_at"]
        # the exact-spec sidecar still serves re-generate
        from quam_state_manager.core import regen_spec
        state = json.loads((chip / "state.json").read_text(encoding="utf-8"))
        wiring = json.loads((chip / "wiring.json").read_text(encoding="utf-8"))
        assert regen_spec.load_spec_sidecar(chip, state, wiring) == {"qubits": ["qA1"]}
        state["qubits"]["qA1"]["T1"] = 1.5e-5            # edited since the build
        (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
        assert regenerate.own_build(chip) is None

    def test_guard_names_the_users_own_build(self, tmp_path):
        from quam_state_manager.web.routes import _build_output_guard
        chip = self._built(tmp_path / "out", {"ok": True, "merge": {"carried": 3}})
        g = _build_output_guard(str(chip))
        assert g["needs_confirm"] is True and g["existing_chip"] is True
        assert g["own_build"]["report"]["merge"] == {"carried": 3}
        assert "re-generated here" in g["error"] and "D:/src/chip" in g["error"]
        assert "already contains a chip" not in g["error"]

    def test_guard_stays_generic_for_a_foreign_or_edited_chip(self, tmp_path):
        from quam_state_manager.web.routes import _build_output_guard
        foreign = _chip(tmp_path / "foreign")
        g = _build_output_guard(str(foreign))
        assert "own_build" not in g and "already contains a chip" in g["error"]
        edited = self._built(tmp_path / "edited", {"ok": True})
        state = json.loads((edited / "state.json").read_text(encoding="utf-8"))
        state["qubits"]["qA1"]["T1"] = 2e-5
        (edited / "state.json").write_text(json.dumps(state), encoding="utf-8")
        g2 = _build_output_guard(str(edited))
        assert "own_build" not in g2 and "already contains a chip" in g2["error"]

    @staticmethod
    def _fake_build(client, monkeypatch):
        """A capability-complete env and a run_regenerate that writes a chip +
        its exact-spec sidecar, like the real one."""
        from quam_state_manager.core import config_generator, regen_spec
        from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
        manifest = {"ok": True, "cached": False, "error": None, "versions": {},
                    "capabilities": {c: {"available": True, "detail": ""}
                                     for c in CATALOG_IDS}}
        monkeypatch.setattr(config_generator, "probe_capabilities",
                            lambda *a, **k: manifest)

        def fake_run(python, src, spec, out, **kw):
            chip = _chip(Path(out))
            st = json.loads((chip / "state.json").read_text(encoding="utf-8"))
            wi = json.loads((chip / "wiring.json").read_text(encoding="utf-8"))
            regen_spec.write_spec_sidecar(chip, spec, st, wi)
            return {"ok": True, "status": "ok", "error": None,
                    "result": {"qubits": ["qA1"], "qubit_pairs": [],
                               "allocation": {"big": "x" * 100}, "warnings": []},
                    "merge": {"carried": 12, "residual_lost": []},
                    "script": None, "script_in_output": None}
        monkeypatch.setattr(regenerate, "run_regenerate", fake_run)
        client.post("/generate/select-env", json={"python": sys.executable})

    def test_the_report_names_the_loaded_chip_not_its_working_copy(
            self, client, tmp_path, monkeypatch):
        self._fake_build(client, monkeypatch)
        loaded = _chip(tmp_path / "loaded_chip")
        client.post("/load", data={"folder": str(loaded)})
        wc = client.post("/regenerate/reconstruct", json={}).get_json()["source_folder"]
        assert Path(wc).name != "loaded_chip", "the default source is the working copy"
        r = client.post("/regenerate/build", json={
            "spec": _gen_valid_spec(), "output_path": str(tmp_path / "out"),
            "source_folder": wc})
        assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
        own = regenerate.own_build(tmp_path / "out")
        assert own["source_folder"] == str(loaded), own["source_folder"]

    def test_regenerate_build_records_the_report_and_offers_it_back(
            self, client, tmp_path, monkeypatch):
        self._fake_build(client, monkeypatch)
        src = _chip(tmp_path / "src")
        body = {"spec": _gen_valid_spec(), "output_path": str(tmp_path / "out"),
                "source_folder": str(src)}
        r1 = client.post("/regenerate/build", json=body)
        assert r1.status_code == 200 and r1.get_json()["ok"], r1.get_json()
        side = json.loads((tmp_path / "out" / ".regen" / "generate_spec.json")
                          .read_text(encoding="utf-8"))
        assert side["report"]["merge"]["carried"] == 12
        assert "allocation" not in side["report"]["result"], "the report is trimmed"
        # the page was reloaded; the user presses Generate again
        r2 = client.post("/regenerate/build", json=body)
        g = r2.get_json()
        assert g["needs_confirm"] is True
        assert g["own_build"]["report"]["merge"]["carried"] == 12
        assert g["own_build"]["source_folder"] == str(src)


# ── regenerate-r2-35: a source that changed under the wizard asks first ──
class TestSourceDrift:
    """The merge reads the source at BUILD time (docs/72), so a save made in
    another tab after the wizard loaded was built while the wizard still
    showed the old value, with no notice. The build now names the displayed
    values that changed and asks; what gets built does not change."""

    @staticmethod
    def _set(chip: Path, **qa1) -> None:
        state = json.loads((chip / "state.json").read_text(encoding="utf-8"))
        state["qubits"]["qA1"].update(qa1)
        (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def test_reconstruct_stamps_what_it_read(self, tmp_path):
        from quam_state_manager.core import regen_spec
        chip = _chip(tmp_path / "src")
        rec = regenerate.reconstruct_from_folder(chip)
        state = json.loads((chip / "state.json").read_text(encoding="utf-8"))
        wiring = json.loads((chip / "wiring.json").read_text(encoding="utf-8"))
        assert rec.source_hash == regen_spec.content_hash(state, wiring)

    def test_drift_names_displayed_values_only(self, tmp_path):
        from quam_state_manager.core import regen_populate
        chip = _chip(tmp_path / "src")
        rec = regenerate.reconstruct_from_folder(chip)
        base = regen_populate.populate_view(rec.spec)
        assert regenerate.source_drift(chip, rec.source_hash, base) == []
        self._set(chip, T1=3.3e-5)                 # a value the wizard does not show
        assert regenerate.source_drift(chip, rec.source_hash, base) == []
        self._set(chip, anharmonicity=-190e6)      # a displayed one
        drift = regenerate.source_drift(chip, rec.source_hash, base)
        assert drift == [{"group": "qubit", "id": "qA1", "field": "anharmonicity",
                          "shown": -220e6, "now": -190e6, "yours": False}]
        edited = {"populate": json.loads(json.dumps(rec.spec["populate"]))}
        edited["populate"]["qubit"]["qA1"]["anharmonicity"] = -205e6
        drift2 = regenerate.source_drift(chip, rec.source_hash, base, spec=edited)
        assert drift2[0]["yours"] is True, "the wizard's own edit wins -- and says so"

    def test_drift_reads_the_same_unsaved_source_as_the_build(self, tmp_path):
        """Integration of regenerate-r2-21 x r2-35: with unsaved in-memory
        edits the reconstruct stamps THAT content, so the drift check judges
        the same source -- the user's own unsaved edit is never reported as a
        change made elsewhere, and a later change over it still is."""
        from quam_state_manager.core import regen_populate
        chip = _chip(tmp_path / "src")
        st = json.loads((chip / "state.json").read_text(encoding="utf-8"))
        wr = json.loads((chip / "wiring.json").read_text(encoding="utf-8"))
        st["qubits"]["qA1"]["anharmonicity"] = -205e6      # unsaved: memory only
        rec = regenerate.reconstruct_from_folder(chip, source=(st, wr))
        base = regen_populate.populate_view(rec.spec)
        assert regenerate.source_drift(chip, rec.source_hash, base,
                                       source=(st, wr)) == []
        # the files on disk still hold -220e6: read alone they DO differ
        assert regenerate.source_drift(chip, rec.source_hash, base) != []
        st2 = json.loads(json.dumps(st))
        st2["qubits"]["qA1"]["anharmonicity"] = -190e6
        drift = regenerate.source_drift(chip, rec.source_hash, base,
                                        source=(st2, wr))
        assert [(d["shown"], d["now"]) for d in drift] == [(-205e6, -190e6)]

    def _post(self, client, tmp_path, monkeypatch, **extra):
        from quam_state_manager.core import config_generator
        from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
        manifest = {"ok": True, "cached": False, "error": None, "versions": {},
                    "capabilities": {c: {"available": True, "detail": ""}
                                     for c in CATALOG_IDS}}
        monkeypatch.setattr(config_generator, "probe_capabilities",
                            lambda *a, **k: manifest)
        calls = []

        def fake_run(*a, **kw):
            calls.append(a)
            return {"ok": True, "status": "ok", "error": None,
                    "result": {"qubits": [], "qubit_pairs": []}, "merge": None}
        monkeypatch.setattr(regenerate, "run_regenerate", fake_run)
        client.post("/generate/select-env", json={"python": sys.executable})
        src = _chip(tmp_path / "src")
        rec = client.post("/regenerate/reconstruct", json={"folder": str(src)}).get_json()
        assert rec["ok"] and rec["source_hash"], rec
        self._set(src, anharmonicity=-190e6)       # "another tab" saves
        body = {"spec": _gen_valid_spec(), "output_path": str(tmp_path / "out"),
                "source_folder": str(src), "populate_baseline": rec["spec"]["populate"],
                "source_hash": rec["source_hash"], **extra}
        return client.post("/regenerate/build", json=body), calls

    def test_build_asks_when_a_displayed_value_changed(self, client, tmp_path, monkeypatch):
        resp, calls = self._post(client, tmp_path, monkeypatch)
        body = resp.get_json()
        assert body["needs_confirm"] is True and body["confirm_kind"] == "source_changed"
        assert body["source_drift"][0]["field"] == "anharmonicity"
        assert body["source_drift"][0]["now"] == -190e6
        assert calls == [], "nothing is built before the user answers"

    def test_the_acknowledgement_builds(self, client, tmp_path, monkeypatch):
        resp, calls = self._post(client, tmp_path, monkeypatch, ack_source_changed=True)
        assert resp.status_code == 200 and resp.get_json()["ok"], resp.get_json()
        assert len(calls) == 1

    def test_an_older_client_without_a_stamp_builds_as_before(
            self, client, tmp_path, monkeypatch):
        resp, calls = self._post(client, tmp_path, monkeypatch, source_hash=None)
        assert resp.status_code == 200 and resp.get_json()["ok"], resp.get_json()
        assert len(calls) == 1
