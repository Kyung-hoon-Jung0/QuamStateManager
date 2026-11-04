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
