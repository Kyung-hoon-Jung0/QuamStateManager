"""The one calibrations-folder resolver (agents ver180, decisions D-1..D-6, Q-2).

Order: override for the project -> the project's calibration_library.folder
(native, existing dir) -> the legacy scheduler setting ONLY with no project.
A scoped project with a broken value resolves to None, never to legacy.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from quam_state_manager.core import calibrations, qualibrate_config as qc


def _cfg(tmp_path, root_folder=None, own_folder=None, own_empty=False, project="p1"):
    cdir = tmp_path / "qcfg"
    (cdir / "projects" / project).mkdir(parents=True)
    root = "[qualibrate]\nversion = 5\nproject = \"%s\"\n" % project
    if root_folder is not None:
        root += "[qualibrate.calibration_library]\nfolder = \"%s\"\n" % Path(root_folder).as_posix()
    (cdir / "config.toml").write_text(root, encoding="utf-8")
    ov = ""
    if own_folder is not None:
        ov = "[qualibrate.calibration_library]\nfolder = \"%s\"\n" % Path(own_folder).as_posix()
    elif own_empty:
        ov = "[qualibrate.calibration_library]\nfolder = \"\"\n"
    (cdir / "projects" / project / "config.toml").write_text(ov, encoding="utf-8")
    return cdir


@pytest.fixture()
def inst(tmp_path):
    p = tmp_path / "inst"
    p.mkdir()
    return p


def _dir(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    return d


def test_own_value_wins_over_global(tmp_path, inst):
    a, b = _dir(tmp_path, "glob"), _dir(tmp_path, "own")
    cdir = _cfg(tmp_path, root_folder=a, own_folder=b)
    r = calibrations.resolve(inst, "p1", cfg_dir=cdir)
    assert r["source"] == "project" and Path(r["folder"]) == b
    assert r["entry"]["source"] == "own"
    assert r["entry"]["overridden"]            # D-7: the global value it replaced is named


def test_inherited_value(tmp_path, inst):
    a = _dir(tmp_path, "glob")
    cdir = _cfg(tmp_path, root_folder=a)
    r = calibrations.resolve(inst, "p1", cfg_dir=cdir)
    assert r["source"] == "project" and Path(r["folder"]) == a and r["entry"]["source"] == "inherited"


@pytest.mark.parametrize("kw,reason", [({}, "unset"), ({"own_empty": True}, "empty")])
def test_unset_or_empty_is_none_never_legacy(tmp_path, inst, kw, reason):
    legacy = _dir(tmp_path, "legacy")
    cdir = _cfg(tmp_path, **kw)
    r = calibrations.resolve(inst, "p1", legacy=str(legacy), cfg_dir=cdir)
    assert r["folder"] is None and r["reason"] == reason
    assert r["legacy_ok"] is True            # offered as a door, not used


def test_missing_folder(tmp_path, inst):
    cdir = _cfg(tmp_path, own_folder=tmp_path / "nope")
    r = calibrations.resolve(inst, "p1", legacy=str(_dir(tmp_path, "legacy")), cfg_dir=cdir)
    assert r["folder"] is None and r["reason"] == "missing"


def test_no_config_at_all(tmp_path, inst):
    r = calibrations.resolve(inst, "p1", legacy=str(_dir(tmp_path, "legacy")), cfg_dir=tmp_path / "absent")
    assert r["folder"] is None and r["reason"] == "no_config"


def test_unmapped_wsl_value_uses_native(tmp_path, inst):
    # R-1: a raw value is mapped to native before the existence check
    assert str(qc._to_native("/mnt/d/x/y", "nt")) == "D:\\x\\y"
    assert qc._to_native(r"D:\x\y", "posix").as_posix() == "/mnt/d/x/y"


def test_no_project_uses_legacy(tmp_path, inst):
    legacy = _dir(tmp_path, "legacy")
    r = calibrations.resolve(inst, None, legacy=str(legacy))
    assert r["source"] == "legacy" and r["folder"] == str(legacy)
    assert calibrations.resolve(inst, None, legacy="")["folder"] is None


def test_override_wins_and_is_per_project_and_config_dir(tmp_path, inst):
    own, ov = _dir(tmp_path, "own"), _dir(tmp_path, "ov")
    cdir = _cfg(tmp_path, own_folder=own)
    calibrations.set_override(inst, "p1", str(cdir), str(ov))
    r = calibrations.resolve(inst, "p1", cfg_dir=cdir)
    assert r["source"] == "override" and Path(r["folder"]) == ov
    # another config dir never matches (D-3)
    other = tmp_path / "other"
    (other / "projects" / "p1").mkdir(parents=True)
    (other / "config.toml").write_text("[qualibrate.calibration_library]\nfolder = \"%s\"\n" % own.as_posix(), encoding="utf-8")
    assert calibrations.resolve(inst, "p1", cfg_dir=other)["source"] == "project"
    # another project never carries it (D-8)
    assert calibrations.load_override(inst, "p2", str(cdir)) is None
    calibrations.set_override(inst, "p1", str(cdir), None)
    assert calibrations.resolve(inst, "p1", cfg_dir=cdir)["source"] == "project"


def test_project_switch_changes_the_value(tmp_path, inst):
    a, b = _dir(tmp_path, "a"), _dir(tmp_path, "b")
    cdir = _cfg(tmp_path, own_folder=a, project="p1")
    (cdir / "projects" / "p2").mkdir()
    (cdir / "projects" / "p2" / "config.toml").write_text(
        "[qualibrate.calibration_library]\nfolder = \"%s\"\n" % b.as_posix(), encoding="utf-8")
    assert Path(calibrations.resolve(inst, "p1", cfg_dir=cdir)["folder"]) == a
    assert Path(calibrations.resolve(inst, "p2", cfg_dir=cdir)["folder"]) == b


def test_a_config_edit_reaches_the_resolver(tmp_path, inst):
    a, b = _dir(tmp_path, "a"), _dir(tmp_path, "bb")
    cdir = _cfg(tmp_path, own_folder=a)
    assert Path(calibrations.resolve(inst, "p1", cfg_dir=cdir)["folder"]) == a
    (cdir / "projects" / "p1" / "config.toml").write_text(
        "[qualibrate.calibration_library]\nfolder = \"%s\"\n" % b.as_posix(), encoding="utf-8")
    assert Path(calibrations.resolve(inst, "p1", cfg_dir=cdir)["folder"]) == b


def test_resolver_never_writes_the_config(tmp_path, inst):
    a = _dir(tmp_path, "a")
    cdir = _cfg(tmp_path, own_folder=a)
    before = {p: p.stat().st_mtime_ns for p in cdir.rglob("*.toml")}
    calibrations.set_override(inst, "p1", str(cdir), str(a))
    calibrations.resolve(inst, "p1", cfg_dir=cdir)
    assert before == {p: p.stat().st_mtime_ns for p in cdir.rglob("*.toml")}


# ---- inventory pin: every request-path reader goes through the resolver (F-10)
_PKG = Path(__file__).resolve().parent.parent / "quam_state_manager"
_RAW = re.compile(r"""(?:settings|load_settings\([^)]*\))\.get\(\s*["']calibrations_folder["']""")
# the only places allowed to read the RAW setting: the resolver's own legacy read,
# the background scheduler (Q-2: its gate gets the stamped value; sweeps fall back)
_ALLOWED = {
    ("web/routes.py", "_calibrations"),
    ("core/scheduler.py", None),
    ("core/agent_runs.py", "check_gates"),   # reads the STAMPED settings (_run_settings)
    ("core/agent_runs.py", "_drive"),        # adapter.settings() (stamped)
    ("web/agent_api.py", "run_node"),         # adapter.settings() == _run_settings (stamped)
    ("web/agent_api.py", "_grant_covers"),    # settings passed from adapter (stamped)
}


def _enclosing(src: str, pos: int) -> str | None:
    m = None
    for m2 in re.finditer(r"^\s*def (\w+)", src[:pos], re.M):
        m = m2
    return m.group(1) if m else None


def test_every_request_reader_uses_the_resolver():
    hits = []
    for f in list(_PKG.rglob("*.py")):
        rel = f.relative_to(_PKG).as_posix()
        src = f.read_text(encoding="utf-8")
        for m in _RAW.finditer(src):
            fn = _enclosing(src, m.start())
            if (rel, fn) in _ALLOWED or (rel, None) in _ALLOWED:
                continue
            hits.append(f"{rel}:{fn}")
    assert not hits, f"raw calibrations_folder readers bypass the resolver: {hits}"


def test_run_settings_stamp_the_resolved_folder():
    src = (_PKG / "web" / "agent_api.py").read_text(encoding="utf-8")
    body = src[src.index("def _run_settings"):src.index("def _run_adapter")]
    assert "_calibrations_folder(ctx)" in body


def test_preflight_stamps_the_resolved_folder():
    src = (_PKG / "web" / "routes.py").read_text(encoding="utf-8")
    body = src[src.index("def _gather_preflight"):]
    body = body[:body.index("\ndef ")]
    assert "_calibrations_folder()" in body and '"calibrations_folder": cal' in body


# ---- the routes: Step 2 over a real app with a scoped project
@pytest.fixture()
def pc(tmp_path, monkeypatch):
    import json as _json
    import sys as _sys
    from quam_state_manager.web.app import create_app
    from quam_state_manager.web import chat_api
    from quam_state_manager.core import scheduler
    from tests.test_web import _make_state, _make_wiring
    from tests.test_agent_runs import NODE_SRC
    monkeypatch.setattr(chat_api.ab, "detect", lambda exe: {"found": False, "version": None})
    projcal = _dir(tmp_path, "projcal")
    (projcal / "05_power_rabi.py").write_text(NODE_SRC, encoding="utf-8")
    legacy = _dir(tmp_path, "legacy")
    cdir = _cfg(tmp_path, own_folder=projcal)
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cdir / "config.toml"))
    inst = tmp_path / "_i"
    app = create_app(testing=True, instance_path=str(inst))
    app.config["agent_setup_home"] = str(_dir(tmp_path, "home"))
    chip = _dir(tmp_path, "chip")
    (chip / "state.json").write_text(_json.dumps(_make_state()), encoding="utf-8")
    (chip / "wiring.json").write_text(_json.dumps(_make_wiring()), encoding="utf-8")
    client = app.test_client()
    client.post("/load", data={"folder": str(chip)})
    with app.app_context():
        from quam_state_manager.web import routes as r
        scheduler.save_settings(r._sched_inst(), {"env_python": _sys.executable, "calibrations_folder": str(legacy)})
        r._active_ctx()["qualibrate_project"] = "p1"
    client._app, client._projcal, client._legacy, client._cdir = app, projcal, legacy, cdir
    return client


def _legacy_setting(c):
    from quam_state_manager.core import scheduler
    with c._app.app_context():
        from quam_state_manager.web import routes as r
        return scheduler.load_settings(r._sched_inst()).get("calibrations_folder")


def test_status_reads_the_project_and_checks(pc):
    s = pc.get("/api/agent/setup").get_json()
    P = s["paths"]
    assert P["project"] == "p1" and Path(P["calibrations"]["folder"]) == pc._projcal
    assert P["calibrations"]["entry"]["source"] == "own"
    assert Path(s["calibrations_folder"]) == pc._projcal
    keys = {c["key"]: c for c in P["checks"]}
    assert keys["nodes"]["ok"] is True and set(keys) == {"state", "nodes", "data", "config"}
    assert keys["config"]["ok"] is True


def test_override_round_trip_never_writes_the_shared_setting(pc, tmp_path):
    mine = _dir(tmp_path, "mine")
    before = _legacy_setting(pc)
    r = pc.post("/api/agent/setup/calibrations", json={"folder": str(mine)})
    assert r.status_code == 200 and r.get_json()["calibrations"]["source"] == "override"
    assert Path(pc.get("/api/agent/setup").get_json()["paths"]["calibrations"]["folder"]) == mine
    r = pc.post("/api/agent/setup/calibrations", json={"clear": True})
    assert r.get_json()["calibrations"]["source"] == "project"
    r = pc.post("/api/agent/setup/calibrations", json={"use_legacy": True})
    assert Path(r.get_json()["calibrations"]["folder"]) == pc._legacy
    assert pc.post("/api/agent/setup/calibrations", json={"folder": str(tmp_path / "nope")}).status_code == 400
    assert _legacy_setting(pc) == before


def test_unset_project_value_is_amber_and_run_gate_refuses(pc):
    (pc._cdir / "projects" / "p1" / "config.toml").write_text("", encoding="utf-8")
    P = pc.get("/api/agent/setup").get_json()["paths"]
    assert P["calibrations"]["folder"] is None and P["calibrations"]["reason"] == "unset"
    assert P["calibrations"]["legacy_ok"] is True
    with pc._app.test_request_context():
        from quam_state_manager.web import routes as r
        r._active_ctx()  # same process-global contexts
        assert r._calibrations_folder() == ""
    # the context write keeps its 409 (F-7)
    assert pc.post("/api/agent/setup/context", json={"answers": {}}).status_code == 409
