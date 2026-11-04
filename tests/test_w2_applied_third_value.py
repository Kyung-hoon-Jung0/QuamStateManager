"""W2-6: a value SM applied is lost to an outside write only when the live
value went BACK to what SM's apply replaced (a stale copy saved over the
chip). A THIRD value is the outside writer's new measurement: newer than SM's,
never named as lost and never overwritten by the default merge. [derived]"""
from __future__ import annotations

import json
import re

from tests.test_sync_review_applied import _apply_t1, _live, _outside, _st, env  # noqa: F401
from tests.test_autosync_lost_applied import _outside_save_and_pull
from tests.test_autosync_merge import _arm


def _row(html, path):
    m = re.search(r'<tr[^>]*data-path="' + re.escape(path) + r'".*?</tr>', html, re.S)
    assert m, html[:1500]
    return m.group(0)


def test_stale_value_is_a_loss(env):  # noqa: F811
    _apply_t1(env)
    _outside(env, _st(t1=2.0e-5, f01=5.2e9))      # back to the pre-apply value
    html = env["client"].get("/state/review").get_data(as_text=True)
    assert "you applied" in _row(html, "qubits.qA1.T1")


def test_third_value_is_newer_not_lost(env):  # noqa: F811
    _apply_t1(env)
    _outside(env, _st(t1=4.4e-5, f01=5.2e9))      # a new measurement
    html = env["client"].get("/state/review").get_data(as_text=True)
    assert "you applied" not in _row(html, "qubits.qA1.T1"), _row(html, "qubits.qA1.T1")


def test_the_default_merge_keeps_the_third_value(env):  # noqa: F811
    _apply_t1(env)
    _outside(env, _st(t1=4.4e-5, f01=5.2e9))
    r = env["client"].post("/state/sync?mode=apply&keep_applied=1")
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    assert _live(env)["T1"] == 4.4e-5, "the merge wrote SM's older value over the new one"


def test_third_value_is_not_in_the_loss_toast(env):  # noqa: F811
    _arm(env, pull=True, push=False)
    _apply_t1(env)
    r = _outside_save_and_pull(env, _st(t1=4.4e-5, f01=5.2e9))
    assert r.status_code == 200
    assert json.loads(r.headers["HX-Trigger"])["autoSyncPulled"]["lost"] == []


def test_stale_value_is_in_the_loss_toast(env):  # noqa: F811
    _arm(env, pull=True, push=False)
    _apply_t1(env)
    r = _outside_save_and_pull(env, _st(t1=2.0e-5, f01=5.2e9))
    lost = json.loads(r.headers["HX-Trigger"])["autoSyncPulled"]["lost"]
    assert [x["path"] for x in lost] == ["qubits.qA1.T1"], lost
