"""v1.5.1 fix round 1 review: MINOR 4 and 11.

MINOR 4. The agent's take_live reported ``taken: True`` (and journaled "took
the live files") when SM answered the pull with ``needs_confirm`` -- a staged
version arrived between the bridge's look and its pull, nothing was pulled.
And a re-apply stash with no dirty flag was dropped (reported, not refused).

MINOR 11. The archive refusal fired whenever the OPEN chip was an archive,
even for a run aimed at a live folder; ``qsm set --save`` wrote a dataset
run's frozen state (SA-F11); and opening an archive opened its state.json for
update to probe write permission (SA-F12).
"""
from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

from quam_state_manager import mcp
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _state(t1=1.0e-5):
    return {"qubits": {"q1": {"id": "q1", "T1": t1, "z": {"joint_offset": 0.08}}},
            "qubit_pairs": {}, "active_qubit_names": ["q1"]}


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _seed_run(root: Path, state: dict) -> Path:
    run = root / "2026-12-30" / "#31_08_spec_010000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "08_spec", "status": "successful",
                     "run_start": "2026-12-30T01:00:00", "run_end": "2026-12-30T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
        "id": 31, "parents": [], "created_at": "2026-12-30T01:00:00"}), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", state)
    return run / "quam_state"


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    root = tmp_path / "data"
    archive = _seed_run(root, _state(t1=2.0e-5))
    c.post("/workspace/add", data={"folder": str(root)})
    return {"app": app, "client": c, "live": live, "archive": archive, "tmp": tmp_path,
            "uid": f"{routes._folder_key(root)}:31"}


def _ctx(env):
    return env["app"].config["contexts"][env["app"].config["active_context"]]


def _edit(env, path, value):
    r = env["client"].post("/field/edit", data={"dot_path": path, "value": value})
    assert r.status_code == 200 and r.get_json()["ok"], r.data


# --------------------------------------------------------------------------
# MINOR 4: the agent's take_live
# --------------------------------------------------------------------------

class _ClientLink:
    def __init__(self, client):
        self.c, self.h = client, {"X-SM-Agent": "mcp"}

    def alive(self):
        return True

    @staticmethod
    def _out(r):
        return r.status_code, (r.get_json(silent=True) if r.is_json else r.get_data(as_text=True))

    def get(self, path, params=None, **_k):
        return self._out(self.c.get(path, query_string=params or {}, headers=self.h))

    def post_form(self, path, data):
        return self._out(self.c.post(path, data={k: v for k, v in data.items() if v is not None},
                                     headers=self.h))

    def post_json(self, path, data, **_k):
        return self._out(self.c.post(path, json=data, headers=self.h))


@pytest.fixture
def bridge(env, monkeypatch):
    monkeypatch.setattr(mcp, "_link", _ClientLink(env["client"]))
    monkeypatch.setattr(mcp, "_CHIP_PIN", None)
    for k, v in {"_seen": None, "_chip": None, "_seen_key": None, "_seen_token": None,
                 "_seen_sig": None, "_seen_paths": [], "_pin_key": None}.items():
        monkeypatch.setattr(mcp, k, v, raising=False)
    journal = []
    monkeypatch.setattr(mcp, "_journal", lambda *a, **k: journal.append((a, k)))
    return journal


def _working_t1(env):
    wf = Path(_ctx(env)["working_copy"].working_folder) / "state.json"
    return json.loads(wf.read_text(encoding="utf-8"))["qubits"]["q1"]["T1"]


def test_take_live_answered_with_needs_confirm_pulled_nothing(env, bridge, monkeypatch):
    # a staged version (the run's state) arrives between the bridge's look and its pull
    r = env["client"].post(f"/dataset/{env['uid']}/load-state")
    assert r.status_code == 200, r.data[:300]
    ctx = _ctx(env)
    assert ctx.get("staged_base") and ctx.get("working_dirty")
    _write_chip(env["live"], _state(t1=3.0e-5))             # live moved too
    real = mcp._chip_facts
    calls = {"n": 0}

    def facts():
        f = real()
        calls["n"] += 1
        if calls["n"] == 1:          # the look saw nothing pending
            f = {**f, "working_dirty": False, "pending": 0, "reapply_stash": 0,
                 "live_diverged": True}
        return f

    monkeypatch.setattr(mcp, "_chip_facts", facts)
    out = mcp.t_take_live({})
    assert out.get("taken") is False, out
    assert out.get("status") == "needs_confirm", out
    assert _working_t1(env) == 2.0e-5, "the staged version is still the working copy"
    assert not any("took the live files" in str(a) for a, _k in bridge), bridge


def test_take_live_refuses_over_a_reapply_stash(env, bridge):
    _write_chip(env["live"], _state(t1=3.0e-5))             # live moved
    ctx = _ctx(env)
    ctx["live_diverged"] = True
    ctx["pending_reapply"] = {"qubits.q1.z.joint_offset": ("set", 0.095)}
    assert not ctx.get("working_dirty") and not ctx["store"].change_log
    out = mcp.t_take_live({})
    assert out.get("taken") is False, out
    assert out.get("would_drop") == "reapply_stash", out
    assert ctx.get("pending_reapply"), "the stash is still there"


# --------------------------------------------------------------------------
# MINOR 11: the archive refusal, scoped; SA-F11; SA-F12
# --------------------------------------------------------------------------

def test_an_open_archive_does_not_refuse_a_run_aimed_at_a_live_folder(env):
    r = env["client"].post(f"/dataset/{env['uid']}/load-state?mode=archive")
    assert r.status_code in (200, 302)
    assert _ctx(env).get("origin") == "dataset_archive"
    with env["app"].test_request_context("/"):
        assert routes._archive_run_reason(str(env["live"])) == ""
        assert "frozen" in routes._archive_run_reason(str(env["archive"]))
        assert "frozen" in routes._archive_run_reason(None)


def test_qsm_set_save_refuses_a_run_archive_unless_asked(env):
    from typer.testing import CliRunner
    from quam_state_manager import cli
    before = (env["archive"] / "state.json").read_bytes()
    runner = CliRunner()
    args = ["set", "qubits.q1.T1", "3.3e-05", "--folder", str(env["archive"]), "--save",
            "--instance", str(env["tmp"] / "_inst")]
    r = runner.invoke(cli.app, args)
    assert r.exit_code == 1, r.output
    assert "frozen" in r.output and "Nothing was written" in r.output
    assert (env["archive"] / "state.json").read_bytes() == before
    r = runner.invoke(cli.app, args + ["--write-archive"])
    assert r.exit_code == 0, r.output
    assert json.loads((env["archive"] / "state.json").read_text())["qubits"]["q1"]["T1"] == 3.3e-05


def test_opening_an_archive_never_opens_its_state_for_update(env, monkeypatch):
    real_open = builtins.open
    opened = []

    def spy(file, mode="r", *a, **kw):
        if "+" in str(mode) or "w" in str(mode) or "a" in str(mode):
            opened.append((str(file), mode))
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    r = env["client"].post("/load", data={"folder": str(env["archive"])})
    monkeypatch.undo()
    assert r.status_code in (200, 302)
    assert _ctx(env).get("origin") == "dataset_archive"
    arch = str(env["archive"].resolve()).lower()
    assert not [o for o in opened if o[0].lower().startswith(arch)], opened
    assert not _ctx(env).get("live_readonly_hint")


def test_the_live_probe_still_runs_for_a_live_chip(env, monkeypatch):
    real_open = builtins.open
    opened = []

    def spy(file, mode="r", *a, **kw):
        if mode == "r+":
            opened.append(str(file))
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    routes._quam_cache.clear()
    r = env["client"].post("/load", data={"folder": str(env["live"])})
    monkeypatch.undo()
    assert r.status_code in (200, 302)
    assert any(o.lower().endswith("state.json") for o in opened), opened
