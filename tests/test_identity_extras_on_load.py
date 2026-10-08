"""docs/301 (F6, F26): loading an older version of a chip keeps its identity.

``extras.chip_name`` files the chip's history and ``extras.data_folder``
pairs it with its data. A version saved before either changed carried the old
value, and loading it wholesale re-pointed the chip: the sync panel offered to
write the old data folder back, and the project asked whether to scope
Datasets to it. Same-chip loads keep the current values and name what they
kept; Revert last apply and an explicit cross-chip load still take the files
as they are (QA F1 pins the latter)."""
from __future__ import annotations

import html
import json
from pathlib import Path

import pytest

from quam_state_manager.core.identity_extras import keep_identity, kept_note
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


# ── the pure rule ────────────────────────────────────────────────────────────
def test_the_current_identity_is_carried_and_nothing_else():
    incoming = {"qubits": {"q1": {"T1": 1}}, "extras": {"chip_name": "old", "note": "v1"}}
    current = {"extras": {"chip_name": "chipA", "data_folder": "/data/now", "note": "v2"}}
    out, kept = keep_identity(incoming, current)
    assert out["extras"] == {"chip_name": "chipA", "data_folder": "/data/now", "note": "v1"}
    assert out["qubits"] == incoming["qubits"] and incoming["extras"]["chip_name"] == "old"
    assert [k["key"] for k in kept] == ["chip_name", "data_folder"]
    note = kept_note(kept)
    assert "chip name (the version had old)" in note and "data folder (the version had none)" in note


def test_a_key_the_chip_does_not_hold_is_left_as_the_version_has_it():
    incoming = {"extras": {"data_folder": "/data/then"}}
    assert keep_identity(incoming, {"extras": {}}) == (incoming, [])
    assert keep_identity(incoming, {}) == (incoming, [])
    same = {"extras": {"chip_name": "chipA"}}
    assert keep_identity(same, same) == (same, [])
    assert kept_note([]) == ""


# ── the doors ────────────────────────────────────────────────────────────────
def _write_chip(folder: Path, state: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _state(data_folder: Path, t1: float) -> dict:
    return {"qubits": {"q1": {"id": "q1", "T1": t1}}, "qubit_pairs": {},
            "active_qubit_names": ["q1"],
            "extras": {"chip_name": "chipA", "data_folder": str(data_folder)}}


@pytest.fixture
def env(tmp_path):
    old_data, new_data = tmp_path / "data_then", tmp_path / "data_now"
    old_data.mkdir()
    new_data.mkdir()
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state(old_data, 1.0e-5))
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    assert c.post("/state-history/snapshot").status_code == 200
    with app.app_context():
        ts = routes_mod._history().list_snapshots(live)[0].timestamp
    # the data moved: the chip now declares the new folder and a new T1
    _write_chip(live, _state(new_data, 2.0e-5))
    assert c.post("/state/sync", data={"mode": "pull"}).status_code == 200
    return {"app": app, "client": c, "live": live, "ts": ts, "old": old_data, "new": new_data,
            "tmp": tmp_path}


def _ctx(env):
    return next(iter(env["app"].config["contexts"].values()))


def _working(env) -> dict:
    return _ctx(env)["store"].state


def test_loading_an_old_version_keeps_the_chips_data_folder_and_says_so(env):
    c = env["client"]
    assert _working(env)["extras"]["data_folder"] == str(env["new"])
    body = html.unescape(c.post(f"/state-history/{env['ts']}/stage").get_data(as_text=True))
    assert "loaded as the working state" in body, body[:400]
    w = _working(env)
    assert w["qubits"]["q1"]["T1"] == 1.0e-5, "the version's calibration values are loaded"
    assert w["extras"]["data_folder"] == str(env["new"]), "the chip keeps its data folder"
    assert "data folder (the version had" in body and str(env["old"]) in body, body[:600]
    review = c.get("/state/review").get_data(as_text=True)
    assert 'data-path="extras.data_folder"' not in review, "nothing offers to write the old folder back"


def test_restoring_an_old_version_to_live_keeps_the_identity(env):
    c = env["client"]
    r = c.post(f"/state-history/{env['ts']}/restore-live?force_pending=1&force_align=1")
    body = html.unescape(r.get_data(as_text=True))
    assert r.status_code == 200, body[:400]
    live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    assert live["qubits"]["q1"]["T1"] == 1.0e-5
    assert live["extras"]["data_folder"] == str(env["new"])
    assert "Kept this chip's own data folder" in body, body[:400]
    assert env["ts"] not in body, "the raw UTC stamp is not the message's time"


def test_revert_last_apply_still_restores_the_files_exactly(env):
    """from=tray is the exact restore: the pre-apply files come back as they were."""
    c = env["client"]
    body = c.post(f"/state-history/{env['ts']}/stage?from=tray").get_data(as_text=True)
    assert "loaded as the working state" in body, body[:400]
    assert _working(env)["extras"]["data_folder"] == str(env["old"])
    assert "Kept this chip" not in body


def test_a_run_of_this_chip_keeps_the_identity_on_load_state(env):
    c = env["client"]
    root = env["tmp"] / "runs"
    run = root / "2026-12-30" / "#7_08_spec_010000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "08_spec", "status": "successful",
                     "run_start": "2026-12-30T01:00:00", "run_end": "2026-12-30T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
        "id": 7, "parents": [], "created_at": "2026-12-30T01:00:00"}), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", _state(env["old"], 3.0e-5))
    c.post("/workspace/add", data={"folder": str(root)})
    uid = f"{routes_mod._folder_key(root)}:7"
    r = c.post(f"/dataset/{uid}/load-state")
    body = html.unescape(r.get_data(as_text=True))
    assert r.status_code == 200, body[:400]
    w = _working(env)
    assert w["qubits"]["q1"]["T1"] == 3.0e-5 and w["extras"]["data_folder"] == str(env["new"])
    assert "Kept this chip's own data folder (the run had" in body, body[:400]


def _seed_run(env, run_id: int, state: dict, wiring: dict | None = None) -> str:
    root = env["tmp"] / "runs"
    run = root / "2026-12-30" / f"#{run_id}_08_spec_010000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "08_spec", "status": "successful",
                     "run_start": "2026-12-30T01:00:00", "run_end": "2026-12-30T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": "2026-12-30T01:00:00"}), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", state)
    if wiring is not None:
        (run / "quam_state" / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    env["client"].post("/workspace/add", data={"folder": str(root)})
    return f"{routes_mod._folder_key(root)}:{run_id}"


def test_an_explicit_cross_chip_load_takes_the_runs_identity(env):
    """QA F1: "anyway" onto a NAMED chip takes the other chip's state as it is
    (the result names the identity change) -- nothing is carried over it."""
    other = {"qubits": {"qZ7": {"id": "qZ7", "T1": 9.0e-6}}, "qubit_pairs": {},
             "active_qubit_names": ["qZ7"],
             "extras": {"chip_name": "otherChip", "data_folder": str(env["old"])}}
    uid = _seed_run(env, 51, other, {"network": {"host": "9.9.9.9", "cluster_name": "Z"}})
    body = html.unescape(env["client"].post(f"/dataset/{uid}/load-state?force_chip=1").get_data(as_text=True))
    assert "WORKING state" in body, body[:400]
    w = _working(env)
    assert w["extras"]["chip_name"] == "otherChip" and w["extras"]["data_folder"] == str(env["old"])
    assert "Kept this chip" not in body


# ── the project's data-folder question withdraws itself ─────────────────────
def test_an_extras_root_the_chip_no_longer_declares_is_not_asked(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    with app.test_request_context("/"):
        routes_mod._save_pending_roots({"proj": {"new": [a, b], "extras": [b]}})
        ctx = {"qualibrate_project": "proj", "extras_data_roots": []}
        orig = routes_mod._load_project_roots
        routes_mod._load_project_roots = lambda: {"proj": [str(tmp_path / "c")]}
        try:
            ask = routes_mod._dataset_roots_ask(ctx)
            assert ask and ask["new"] == [a], ask
            ctx["extras_data_roots"] = [b]
            assert routes_mod._dataset_roots_ask(ctx)["new"] == [a, b]
            routes_mod._save_pending_roots({"proj": {"new": [b], "extras": [b]}})
            ctx["extras_data_roots"] = []
            assert routes_mod._dataset_roots_ask(ctx) is None
        finally:
            routes_mod._load_project_roots = orig


def test_a_stale_answer_never_scopes_the_project_to_a_withdrawn_root(tmp_path, monkeypatch):
    """Review: the banner was rendered, then the chip stopped declaring the
    proposed folder; an "only the new path" answer from that old banner
    replaced the project's roots with the withdrawn folder."""
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    b, c = str(tmp_path / "b"), str(tmp_path / "c")
    monkeypatch.setattr(routes_mod, "_active_ctx",
                        lambda: {"qualibrate_project": "proj", "extras_data_roots": []})
    monkeypatch.setattr(routes_mod, "_load_project_roots", lambda: {"proj": [c]})
    with app.test_request_context("/project-roots/confirm", method="POST",
                                  data={"project": "proj", "choice": "new_only"}):
        routes_mod._save_pending_roots({"proj": {"new": [b], "extras": [b]}})
        routes_mod.project_roots_confirm()
        roots_file = routes_mod._project_roots_file()
        assert not roots_file.exists() or "b" not in [
            Path(r).name for r in json.loads(roots_file.read_text(encoding="utf-8")).get("proj", [])],             "the project's roots were rewritten from a withdrawn proposal"
        assert "proj" not in routes_mod._load_pending_roots(), "the stale question is dropped"


def test_the_stop_asking_button_is_a_small_text_button():
    import re
    css = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static"
           / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"\.cnb-form \.cnb-dismiss\s*\{([^}]*)\}", css)
    assert rule, "the x had no rule, so Pico drew it as a full-width primary button"
    body = rule.group(1)
    assert "width: auto" in body and "background: transparent" in body, body
