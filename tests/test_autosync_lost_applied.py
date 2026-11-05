"""F2 (final walk): an outside writer (a node saving its in-memory copy) took
away a value SM had just applied. Armed pulls restore it automatically;
pull history never reports a restored value as an unresolved loss."""
from __future__ import annotations

import json

import quam_state_manager.web.routes as routes_mod
from tests.test_autosync_merge import _arm, _state, _write_chip, env  # noqa: F401


def _apply_name(env):
    c = env["client"]
    r = c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "3.3e-05"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    r = c.post("/state/apply-to-live")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    assert doc["qubits"]["qA1"]["T1"] == 3.3e-05


def _outside_save_and_pull(env, state):
    _write_chip(env["live"], state)
    with env["app"].test_request_context():
        ctx = routes_mod._active_ctx()
        ctx["_live_hash_checked_at"] = None
        routes_mod._refresh_live_diverged(ctx)
    return env["client"].post("/auto-sync/pull", data={"dom_dirty": "0"})


def test_the_pull_restores_an_applied_value_the_outside_save_removed(env):  # noqa: F811
    _arm(env, pull=True, push=False)
    _apply_name(env)
    r = _outside_save_and_pull(env, _state(f01=5.2e9))      # T1 back to 2e-5, f_01 moved
    assert r.status_code == 200
    triggers = json.loads(r.headers["HX-Trigger"])
    assert triggers["autoSyncRestored"]["count"] == 1
    assert triggers["autoSyncPulled"]["lost"] == []
    doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    assert doc["qubits"]["qA1"]["T1"] == 3.3e-05


def test_an_outside_save_that_kept_the_value_names_nothing(env):  # noqa: F811
    _arm(env, pull=True, push=False)
    _apply_name(env)
    r = _outside_save_and_pull(env, _state(f01=5.2e9, t1=3.3e-05))
    assert r.status_code == 200
    assert json.loads(r.headers["HX-Trigger"])["autoSyncPulled"]["lost"] == []


def test_the_client_says_it():
    from pathlib import Path
    js = (Path(routes_mod.__file__).parent / "static" / "auto-apply.js").read_text(encoding="utf-8")
    i = js.index("function sayLost(list)")
    body = js[i:i + 900]
    assert "removed" in body and "toast(" in body
    assert "d.replaced" not in body, "the loss must be said whether or not edits were replaced"


def test_a_chip_name_the_outside_save_dropped_is_named(env):  # noqa: F811
    """The walk's case: Set name wrote extras.chip_name, the node's save
    rewrote state.json without any extras."""
    c = env["client"]
    _arm(env, pull=True, push=True)
    r = c.post("/chip-name/set", data={"name": "chip-a"})
    assert r.status_code in (200, 204), r.status_code
    c.post("/state/apply-to-live")
    doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    assert (doc.get("extras") or {}).get("chip_name") == "chip-a", doc
    r = _outside_save_and_pull(env, _state(f01=5.2e9))
    assert r.status_code == 200, r.status_code
    assert json.loads(r.headers["HX-Trigger"])["autoSyncRestored"]["count"] == 1
    doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    assert doc["extras"]["chip_name"] == "chip-a"


def test_every_window_learns_of_the_pull(env):  # noqa: F811
    """F23: the drift poll every window runs carries the pull step and what it
    took away; an empty pull (a second window's, after the first) is no step."""
    c = env["client"]
    _arm(env, pull=True, push=False)
    d0 = c.get("/state/drift").get_json()
    assert d0["pull_seq"].endswith("#0") and d0["pull_lost"] == []
    _apply_name(env)
    r = _outside_save_and_pull(env, _state(f01=5.2e9))
    seq = json.loads(r.headers["HX-Trigger"])["autoSyncPulled"]["pull_seq"]
    d1 = c.get("/state/drift").get_json()
    assert d1["pull_seq"] == seq and seq != d0["pull_seq"]
    assert d1["pull_lost"] == []
    r2 = _outside_save_and_pull(env, _state(f01=5.2e9, t1=3.3e-05))  # nothing differs
    if r2.status_code == 200:
        assert "pull_seq" not in json.loads(r2.headers["HX-Trigger"])["autoSyncPulled"]
    assert c.get("/state/drift").get_json()["pull_seq"] == seq


def test_a_window_repaints_once_per_foreign_pull():
    """Runs the shipped onPullSeq (app.js) against stubs."""
    import re
    import subprocess
    from pathlib import Path
    src = (Path(routes_mod.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
    fn = (re.search(r"    function _pullSeqParts\(s\) \{.*?\n    \}\n", src, re.S).group(0)
          + re.search(r"    function onPullSeq\(d\) \{.*?\n    \}\n", src, re.S).group(0))
    js = ("var window={_followValuesOnScreen:function(){window.f++},_smSayLost:function(l){window.s.push(l)},f:0,s:[]};\n"
          + fn + "\nvar out=[];"
          "out.push(onPullSeq({pull_seq:'c#0',pull_lost:[]}));"
          "out.push(onPullSeq({pull_seq:'c#0',pull_lost:[]}));"
          "out.push(onPullSeq({pull_seq:'c#1',pull_lost:[{path:'q.T1'}]}));"
          "out.push(onPullSeq({pull_seq:'c#1',pull_lost:[{path:'q.T1'}]}));"
          "console.log(JSON.stringify([out,window.f,window.s.length]));")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "[[false,false,true,false],1,1]", r.stdout + r.stderr


def test_two_pulls_between_two_polls_are_both_named(env):  # noqa: F811
    """A second window that polls after TWO pulls still learns of the first
    pull's loss: the drift answer carries each step's lost list."""
    c = env["client"]
    _arm(env, pull=True, push=False)
    _apply_name(env)
    r1 = _outside_save_and_pull(env, _state(f01=5.2e9))         # drops T1
    assert r1.status_code == 200
    r2 = _outside_save_and_pull(env, _state(f01=5.3e9))         # moves f_01 only
    assert r2.status_code == 200
    d = c.get("/state/drift").get_json()
    assert d["pull_seq"].endswith("#2"), d["pull_seq"]
    assert d["pull_lost"] == [], "the latest step alone names nothing"
    hist = d["pull_lost_hist"]
    assert [h[0] for h in hist] == [1, 2], hist
    assert hist[0][1] == [], "restored values must not be announced as lost"


def test_the_pull_history_is_bounded(env):  # noqa: F811
    _arm(env, pull=True, push=False)
    for i in range(9):
        r = _outside_save_and_pull(env, _state(f01=5.1e9 + i * 1e6))
        assert r.status_code == 200
    d = env["client"].get("/state/drift").get_json()
    assert d["pull_seq"].endswith("#9"), d["pull_seq"]
    assert len(d["pull_lost_hist"]) == routes_mod._PULL_LOST_HIST == 8
    assert [h[0] for h in d["pull_lost_hist"]] == list(range(2, 10))


def test_a_pull_that_changed_nothing_is_not_a_step(env):  # noqa: F811
    _arm(env, pull=True, push=False)
    r = _outside_save_and_pull(env, _state(f01=5.2e9))
    assert r.status_code == 200
    d1 = env["client"].get("/state/drift").get_json()
    r = _outside_save_and_pull(env, _state(f01=5.2e9))           # the same content
    d2 = env["client"].get("/state/drift").get_json()
    assert d2["pull_seq"] == d1["pull_seq"]
    assert d2["pull_lost_hist"] == d1["pull_lost_hist"]
