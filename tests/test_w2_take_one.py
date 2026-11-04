"""W2-2: a per-row take of a live value is not an unapplied edit -- the field
then agrees with the live chip, and nothing says SM's old value is "live"."""
from __future__ import annotations

import re

from tests.test_sync_review_applied import _outside, _st, env  # noqa: F401


def test_a_taken_row_is_not_an_unapplied_edit(env):  # noqa: F811
    c = env["client"]
    _outside(env, _st(t2=1.5e-5, f01=5.2e9))
    r = c.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.qA1.T2ramsey", "value": "1.5e-05", "create": False}], "source": "live"})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_data(as_text=True)[:300]
    d = c.get("/state/drift").get_json()["sync"]
    assert d["unapplied"] == 0, d
    html = c.get("/state/review").get_data(as_text=True)
    m = re.search(r'<tr[^>]*data-path="qubits.qA1.T2ramsey".*?</tr>', html, re.S)
    assert not m or "1e-05" not in m.group(0), m.group(0)


def test_a_lost_applied_value_stays_in_the_control_until_dismissed(env):  # noqa: F811
    from tests.test_autosync_lost_applied import _outside_save_and_pull
    from tests.test_autosync_merge import _arm
    from tests.test_sync_review_applied import _apply_t1
    _arm(env, pull=True, push=False)
    _apply_t1(env)
    assert _outside_save_and_pull(env, _st(f01=5.2e9)).status_code == 200
    c = env["client"]
    for _ in range(2):          # survives later renders, other windows too
        tray = env["app"].test_client().get("/state/tray").get_data(as_text=True)
        assert 'data-sync-lost="1"' in tray and "qubits.qA1.T1" in tray
    assert c.post("/sync/lost-ack").status_code == 200
    assert "data-sync-lost" not in c.get("/state/tray").get_data(as_text=True)
