"""One-click "Apply to chip" on the dataset State tab (docs/108).

User feedback: reaching the live chip from a run's snapshot took several
clicks (Load State -> top bar -> Apply). The primary button is now "Apply to
chip": ONE press stages the snapshot AND pushes it through the SHARED apply
core (`_sync_pull_apply_to_live`) — covenant-compliant (the labeled press IS
the one explicit apply act), no confirm dialog, reversible via the pre-apply
snapshot that arms "Revert last apply".

Pins:
  - apply=1 lands the run's values on the LIVE chip in one call, arms
    ctx["last_apply"], and the tray comes back clean;
  - the plain (stage-only) call is byte-identical legacy: live untouched,
    same message/trigger shape;
  - both 409 gates carry apply=1 through their confirm URLs;
  - a staleness conflict (live moved since load) STAGES but never
    force-pushes — honest warning + the conflict tray, live keeps the
    out-of-band content;
  - the template offers Apply to chip (apply=1) + Stage only + read-only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(off_a=0.08):
    return {
        "qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9,
                           "z": {"joint_offset": off_a}}},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _write_chip(folder: Path, state: dict, wiring: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING),
                                        encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(live)})
    assert r.status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _ctx(env):
    return next(iter(env["app"].config["contexts"].values()))


def _live_off(env) -> float:
    st = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    return st["qubits"]["qA1"]["z"]["joint_offset"]


def _seed_run(root: Path, run_id: int, state: dict) -> None:
    run = root / "2026-12-30" / f"#{run_id}_08_spec_010000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "08_spec", "status": "successful",
                     "run_start": "2026-12-30T01:00:00",
                     "run_end": "2026-12-30T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}},
                 "outcomes": {}},
        "id": run_id, "parents": [],
        "created_at": "2026-12-30T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", state)


def _uid(env, root: Path, run_id: int) -> str:
    env["client"].post("/workspace/add", data={"folder": str(root)})
    return f"{routes_mod._folder_key(root)}:{run_id}"


class TestOneClickApply:
    def test_apply_lands_on_live_in_one_call(self, env):
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, 31, _state(off_a=0.079))
        uid = _uid(env, root, 31)
        r = c.post(f"/dataset/{uid}/load-state?apply=1")
        assert r.status_code == 200, r.data
        html = r.data.decode()
        assert "LIVE" in html and "Revert last apply" in html
        assert _live_off(env) == 0.079, "one press must reach the live chip"
        ctx = _ctx(env)
        assert ctx.get("last_apply", {}).get("pre_ts"), \
            "the pre-apply snapshot must arm Revert last apply"
        assert not ctx["working_dirty"]
        assert 'data-working-dirty="1"' not in html   # tray came back clean
        assert "stateRestored" in r.headers.get("HX-Trigger", "")

    def test_stage_only_is_unchanged_legacy(self, env):
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, 32, _state(off_a=0.077))
        uid = _uid(env, root, 32)
        r = c.post(f"/dataset/{uid}/load-state")
        assert r.status_code == 200
        html = r.data.decode()
        assert "WORKING state" in html
        assert 'data-working-dirty="1"' in html
        assert _live_off(env) == 0.08, "stage-only must not touch live"
        assert _ctx(env).get("last_apply") is None

    def test_gates_carry_apply_flag(self, env):
        c = env["client"]
        root = env["tmp"] / "data"
        # different-network run → identity gate fires
        _seed_run(root, 33, {**_state(0.05),
                             })
        run_dir = root / "2026-12-30" / "#33_08_spec_010000"
        _write_chip(run_dir / "quam_state", _state(0.05),
                    {"network": {"host": "9.9.9.9", "cluster_name": "OTHER"}})
        uid = _uid(env, root, 33)
        r = c.post(f"/dataset/{uid}/load-state?apply=1")
        assert r.status_code == 409
        html = r.data.decode()
        assert "apply=1" in html and "force_chip=1" in html
        assert "APPLIED to the live chip" in html   # the gate names the stakes
        assert _live_off(env) == 0.08

        # pending-edits gate: put an unsaved edit in, confirm URL keeps apply=1
        r2 = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA1.f_01", "value": "5.01e9"}],
            "expect_chip": ""})
        assert r2.status_code == 200 and r2.get_json()["ok"]
        r3 = c.post(f"/dataset/{uid}/load-state?apply=1&force_chip=1")
        assert r3.status_code == 409
        html3 = r3.data.decode()
        assert "force=1" in html3 and "apply=1" in html3

    def test_conflict_stages_but_never_force_pushes(self, env):
        """Live moved out-of-band since the chip was loaded: one-click must
        NOT clobber it — the snapshot stays staged, the honest conflict tray
        renders, and live keeps the out-of-band content."""
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, 34, _state(off_a=0.076))
        uid = _uid(env, root, 34)
        # out-of-band writer bumps the live chip AFTER load
        _write_chip(env["live"], _state(off_a=0.5))
        r = c.post(f"/dataset/{uid}/load-state?apply=1")
        assert r.status_code == 200
        html = r.data.decode()
        assert "is safe" in html
        assert "pending-tray-conflict" in html      # the honest tray, OOB
        assert _live_off(env) == 0.5, "a drifted live chip is never clobbered"
        assert _ctx(env)["working_dirty"]

    def test_conflict_answers_where_the_press_happened(self, env):
        """docs/116: the verdict used to be a one-liner pointing at the top
        bar, and the tray it pointed at asked a DIFFERENT flow's question
        (\"choose which side wins\", whose down-choice discards the run just
        chosen). The continuation now renders in place, names the run, and
        offers the choice the press actually meant."""
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, 44, _state(off_a=0.076))
        uid = _uid(env, root, 44)
        _write_chip(env["live"], _state(off_a=0.5))
        html = c.post(f"/dataset/{uid}/load-state?apply=1").data.decode()

        assert "ds-apply-conflict" in html            # rendered in place
        assert "Apply run #44 over live" in html      # the one continuation
        assert "/state/apply-to-live?force=1" in html
        assert "Leave live as it is" in html
        assert "Review changes" in html
        # the panel IS the confirmation - no native dialog stacked on top.
        # (Scoped to the PANEL: the OOB tray below keeps its own confirm,
        # which docs/86 requires of a force button that has no prose.)
        panel = html.split('<div id="pending-tray"')[0]
        assert 'hx-confirm=' not in panel
        # and it still refuses to write by itself
        assert _live_off(env) == 0.5

    def test_apply_that_changes_nothing_is_not_a_conflict(self, env):
        """docs/116 (the root cause): the staleness gate answered \"did live
        move away from our sync point?\" when the question it refuses on
        behalf of is \"would this write DESTROY something?\". Applying a run
        whose snapshot the live chip ALREADY holds - the ordinary case, since
        that run is usually what last wrote the chip - was a conflict about a
        value and itself."""
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, 45, _state(off_a=0.079))
        uid = _uid(env, root, 45)
        # live already holds exactly what the run would write, but its mtime
        # (and content) moved away from the working copy's sync point
        _write_chip(env["live"], _state(off_a=0.079))
        html = c.post(f"/dataset/{uid}/load-state?apply=1").data.decode()

        assert "pending-tray-conflict" not in html
        assert "ds-apply-conflict" not in html
        assert "now LIVE" in html
        assert _live_off(env) == 0.079
        assert not _ctx(env)["working_dirty"]

    def test_a_real_difference_still_conflicts(self, env):
        """The carve-out is identical-content ONLY: a live chip holding
        DIFFERENT values is still never clobbered."""
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, 46, _state(off_a=0.076))
        uid = _uid(env, root, 46)
        _write_chip(env["live"], _state(off_a=0.31))
        html = c.post(f"/dataset/{uid}/load-state?apply=1").data.decode()
        assert "ds-apply-conflict" in html
        assert _live_off(env) == 0.31

    def test_template_offers_both_buttons(self, env):
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, 35, _state(off_a=0.075))
        uid = _uid(env, root, 35)
        r = c.get(f"/dataset/{uid}")
        html = r.data.decode()
        assert "load-state?apply=1" in html
        assert "Apply to chip" in html
        assert "Stage only" in html
        assert "load-state?mode=archive" in html


def test_gate_panels_ask_once_not_twice():
    """docs/116: `_sh_confirm.html` is itself the confirmation - it names what
    is lost and its button names the act. It carried an hx-confirm as well, so
    one decision cost two answers (a native dialog on top of the panel the
    user had just read and clicked)."""
    from pathlib import Path
    tpl = (Path(__file__).resolve().parent.parent / "quam_state_manager"
           / "web" / "templates" / "_sh_confirm.html").read_text(encoding="utf-8")
    assert 'hx-confirm=' not in tpl        # the ATTRIBUTE, not the word
    assert "hx-post" in tpl and "action_label" in tpl      # still completable
