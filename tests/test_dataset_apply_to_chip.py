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
  - docs/126 ⑤ (user-directed 2026-08-19): the IDENTITY gate is the one
    question the apply path still asks (and it carries apply=1); pending
    edits and live drift are applied over WITHOUT asking, each NAMED in the
    result line, with ↺ Revert last apply armed — the review path (plain
    stage) keeps its 409s;
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

    def test_identity_gate_still_asks_and_carries_apply(self, env):
        """docs/126 ⑤: chip identity is the ONE question the apply path still
        asks — a different-chip run never lands on one press."""
        c = env["client"]
        root = env["tmp"] / "data"
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

    def test_pending_edits_apply_without_asking_but_reported(self, env):
        """docs/126 ⑤ (user-directed 2026-08-19): unsaved edits no longer 409
        the APPLY path — the press already means "the run's state wins" — but
        what was replaced is NAMED in the result (docs/86: reported, never
        silent). The review path (plain stage) keeps its 409."""
        c = env["client"]
        root = env["tmp"] / "data"
        # both runs seeded BEFORE the workspace scan — a later _seed_run is
        # invisible until a rescan and resolves as "No quam_state in this run"
        _seed_run(root, 43, _state(off_a=0.076))
        _seed_run(root, 47, _state(off_a=0.074))
        uid = _uid(env, root, 43)
        uid47 = _uid(env, root, 47)
        r2 = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA1.f_01", "value": "5.01e9"}],
            "expect_chip": ""})
        assert r2.status_code == 200 and r2.get_json()["ok"]
        r3 = c.post(f"/dataset/{uid}/load-state?apply=1")
        assert r3.status_code == 200
        html3 = r3.data.decode()
        assert "now LIVE" in html3
        assert "Replaced 1 unsaved edit" in html3
        assert _live_off(env) == 0.076, "one press reaches live despite edits"
        # the review path (plain stage) still asks
        r_e = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA1.f_01", "value": "5.02e9"}],
            "expect_chip": ""})
        assert r_e.status_code == 200 and r_e.get_json()["ok"], r_e.get_json()
        r4 = c.post(f"/dataset/{uid47}/load-state")
        assert r4.status_code == 409
        assert "force=1" in r4.data.decode()

    def test_drifted_live_is_overwritten_and_named(self, env):
        """docs/126 ⑤ (user-directed 2026-08-19, superseding the docs/116
        conflict panel here): live moved out-of-band since the chip was
        loaded — the press already decided the run's state wins, so the drift
        is pushed over, the overwrite is NAMED in the result, and ↺ Revert
        last apply is armed (the reversibility that licenses this)."""
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, 34, _state(off_a=0.076))
        uid = _uid(env, root, 34)
        # out-of-band writer bumps the live chip AFTER load
        _write_chip(env["live"], _state(off_a=0.5))
        r = c.post(f"/dataset/{uid}/load-state?apply=1")
        assert r.status_code == 200
        html = r.data.decode()
        assert "now LIVE" in html
        assert "HAD changed" in html and "overwritten" in html
        assert "ds-apply-conflict" not in html
        assert _live_off(env) == 0.076, "the run's state wins over the drift"
        assert not _ctx(env)["working_dirty"]
        assert _ctx(env).get("last_apply", {}).get("pre_ts"), \
            "the overwrite must stay reversible (Revert last apply armed)"

    def test_drift_answer_lands_where_the_press_happened(self, env):
        """docs/116 established that the verdict must answer IN PLACE (not a
        one-liner pointing at the top bar); docs/126 ⑤ changed the verdict
        itself from a conflict panel to a completed, disclosed overwrite —
        still rendered in #ds-load-state-result, still naming the run."""
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, 44, _state(off_a=0.076))
        uid = _uid(env, root, 44)
        _write_chip(env["live"], _state(off_a=0.5))
        html = c.post(f"/dataset/{uid}/load-state?apply=1").data.decode()
        assert "Run #44" in html and "now LIVE" in html
        assert "overwritten" in html
        assert "Revert last apply" in html   # the road back is named in place
        assert _live_off(env) == 0.076

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

    def test_a_real_difference_is_overwritten_with_disclosure(self, env):
        """docs/126 ⑤: a live chip holding DIFFERENT values is overwritten by
        the press — while the identical-content carve-out (docs/116) stays the
        QUIET path, so the overwrite note appears only when something real was
        replaced (see test_apply_that_changes_nothing_is_not_a_conflict)."""
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, 46, _state(off_a=0.076))
        uid = _uid(env, root, 46)
        _write_chip(env["live"], _state(off_a=0.31))
        html = c.post(f"/dataset/{uid}/load-state?apply=1").data.decode()
        assert "now LIVE" in html and "overwritten" in html
        assert _live_off(env) == 0.076

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

def _result_message(html: str) -> str:
    """Just the result line -- the response also carries an OOB tray whose own
    tooltip mentions Revert last apply, which made whole-body assertions read
    the tray instead of the thing under test."""
    return html.split('id="pending-tray"')[0]


class TestTheResultLineSaysWhatTheButtonDoes:
    """docs/198 -- found by pressing this path on the real customer chip.

    "Apply to chip" answers with a result line naming what it overwrote, and it
    ends by pointing at the way back. That pointer used to read "↺ Revert last
    apply (top bar) restores the pre-apply state", which promises the chip moves
    when you press it. It does not: the button STAGES the pre-apply state into
    the working copy, and the chip moves on the following Apply -- the docs/107
    covenant, one explicit act per live write.

    The tray's own tooltip already said this correctly. This line did not, and
    the consequence is not hypothetical: driving it for real, the chip did not
    move on the press and the button read as dead. docs/120 -- a press means
    what the presser could see.
    """

    def _apply(self, env, rid, off):
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, rid, _state(off_a=off))
        uid = _uid(env, root, rid)
        return _result_message(c.post(f"/dataset/{uid}/load-state?apply=1").data.decode())

    def test_the_result_line_does_not_promise_a_restore(self, env):
        msg = self._apply(env, 61, 0.061)
        assert "Revert last apply" in msg, "it still points at the way back"
        assert "restores the pre-apply state" not in msg, \
            "the button stages; only the following Apply restores"

    def test_it_names_the_staging(self, env):
        assert "stages the pre-apply state" in self._apply(env, 62, 0.062)

    def test_it_names_the_apply_that_follows(self, env):
        assert "back on the chip" in self._apply(env, 63, 0.063)

    def test_the_button_tooltip_agrees_with_it(self, env):
        tpl = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
               / "templates" / "_dataset_detail.html").read_text(encoding="utf-8")
        assert "stages the pre-apply state" in tpl
        assert "Revert last apply restores the pre-apply state" not in tpl
        # datasets-r2-19 review: the tooltip promised "Reversible" without
        # qualification while the result line below says a replaced unsaved
        # edit is NOT recoverable. The two texts agree now: reversible FOR THE
        # CHIP, and the replaced edits are named, not brought back.
        btn = tpl.split(">Apply to chip</button>", 1)[0].rsplit('title="', 1)[1]
        assert "Reversible for the chip" in btn
        assert "Unsaved working edits are replaced and named" in btn and "not recoverable" in btn
        assert "chip. Reversible —" not in btn, "the unqualified promise is gone"


class TestReplacedEditsAreNamedHonestly:
    """datasets-r2-19: "Apply to chip" over an unsaved Live-State edit said only
    "(Replaced 1 unsaved edit.)" and then "Reversible -- Revert last apply".
    The edit was on neither the chip nor any snapshot, so Revert can never
    bring it back: the line now NAMES each replaced field + value and says so
    (docs/187's rule: never point at a way back that does not hold the value).
    """

    def _press_over_an_edit(self, env, rid=71):
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, rid, _state(off_a=0.071))
        uid = _uid(env, root, rid)
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA1.f_01", "value": "5.01e9"}],
            "expect_chip": ""})
        assert r.status_code == 200 and r.get_json()["ok"]
        r = c.post(f"/dataset/{uid}/load-state?apply=1")
        assert r.status_code == 200
        return _result_message(r.data.decode())

    def test_the_line_names_the_field_and_the_value(self, env):
        msg = self._press_over_an_edit(env)
        assert "Replaced 1 unsaved edit" in msg          # the pinned prefix stays
        assert "qubits.qA1.f_01 = 5010000000" in msg
        assert _live_off(env) == 0.071

    def test_it_does_not_offer_revert_as_the_way_back_for_the_edit(self, env):
        msg = self._press_over_an_edit(env)
        assert "cannot bring it back" in msg
        # the chip's own pre-apply state IS reversible -- and says it is the chip
        assert "The chip&#39;s previous state is reversible" in msg \
            or "The chip's previous state is reversible" in msg
        assert "stages the pre-apply state" in msg
        assert ". Reversible —" not in msg and ".) Reversible" not in msg

    def test_a_clean_press_keeps_the_plain_wording(self, env):
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, 72, _state(off_a=0.072))
        uid = _uid(env, root, 72)
        msg = _result_message(c.post(f"/dataset/{uid}/load-state?apply=1").data.decode())
        assert "Replaced" not in msg and "cannot bring" not in msg
        assert " Reversible — ↺ Revert last apply" in msg


def test_replaced_edits_note_shapes():
    """The pure clause builder: last write per path wins, deletes/subtrees are
    words not dumps, long values and long lists are capped, and saved-but-
    unapplied changes are named alongside -- a count never pretends to be the
    whole loss (docs/187 R7)."""
    from quam_state_manager.core.loader import ChangeEntry
    f = routes_mod._replaced_edits_note
    E = lambda p, v, **kw: ChangeEntry(dot_path=p, old_value=None, new_value=v,
                                       source_file="state", **kw)
    assert f([], False) == ""
    assert f([], True) == " (Replaced saved-but-unapplied working changes.)"
    two = f([E("q.a", 1), E("q.a", 2)], False)
    assert two.startswith(" (Replaced 2 unsaved edits: q.a = 2.")
    assert "q.a = 1" not in two and "cannot bring them back" in two
    assert "(deleted)" in f([E("q.gone", None, deleted=True)], False)
    assert "q.new = (new subtree)" in f([E("q.new", {"x": 1}, created=True)], False)
    assert "…" in f([E("q.s", "x" * 80)], False)
    many = f([E(f"q.p{i}", i) for i in range(8)], False)
    assert "q.p4 = 4" in many and "q.p5" not in many and "+3 more" in many
    both = f([E("q.a", 1)], True)
    assert "Replaced 1 unsaved edit" in both
    assert "Saved-but-unapplied working changes were replaced as well." in both


class TestNoBackupNoForcedApply:
    """QA correctness-r2-01, the twin door: "Apply to chip" forces over a
    drifted live and promises "Reversible". With the live pair unreadable no
    backup exists, so the forced retry must refuse instead of writing."""

    def test_unreadable_live_is_not_overwritten(self, env):
        c = env["client"]
        root = env["tmp"] / "data"
        _seed_run(root, 41, _state(off_a=0.079))
        uid = _uid(env, root, 41)
        # an outside save written with a BOM: drift the first attempt sees,
        # and content no snapshot can read
        (env["live"] / "state.json").write_text(json.dumps(_state(off_a=0.5)),
                                                encoding="utf-8-sig")
        before = (env["live"] / "state.json").read_bytes()
        # the identity gate cannot fingerprint an unreadable live -> "anyway"
        r = c.post(f"/dataset/{uid}/load-state?apply=1&force_chip=1")
        assert (env["live"] / "state.json").read_bytes() == before
        assert "back up" in r.get_data(as_text=True)
        assert "is now LIVE" not in r.get_data(as_text=True)
