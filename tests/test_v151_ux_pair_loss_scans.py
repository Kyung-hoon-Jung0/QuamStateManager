"""P1-12 (U12): a pair leads to its population-loss scans.

Finding a pair's loss data took 12 actions: nothing on the pair, then a
Datasets search for the mover's per-qubit runs. The pair inspector now carries
a lazily filled "loss scans" line: the moving qubit's newest population-loss
run (found by the CZ plan's own discovery over the chip's data folders) and
the CZ gate plan, which shows the loss per operation. A pair whose mover has
no such run gets nothing -- the line is offered only when there is data.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("h5py")

from tests.test_cz_loss import _client, _q1_scan  # noqa: E402


def _slot_url(html: str) -> str:
    m = re.search(r'class="pair-loss-slot" hx-get="([^"]+)"', html)
    assert m, "the pair inspector has no loss-scans slot"
    return m.group(1)


def test_the_mover_s_newest_scan_is_linked(tmp_path):
    storage = tmp_path / "storage"
    _q1_scan(storage, rid=100)
    _q1_scan(storage, rid=104, day="2026-01-02")
    _app, c, _ctx = _client(tmp_path, roots=[(storage, "project_storage")])
    html = c.get("/pair/q1-2", headers={"HX-Request": "true"}).get_data(as_text=True)
    url = _slot_url(html)
    assert url == "/pair/q1-2/loss-scans"
    frag = c.get(url, headers={"HX-Request": "true"}).get_data(as_text=True)
    runs = re.findall(r'class="pair-loss-run" href="/dataset/([0-9a-f]+:(\d+))"', frag)
    assert runs and runs[0][1] == "104", frag          # the NEWEST scan of the mover (q1)
    assert "Newest population-loss scan of q1" in frag
    assert 'class="pair-loss-plan" href="/cz-plan?op=qubit_pairs.q1-2.macros.cz"' in frag


def test_no_loss_run_offers_nothing(tmp_path):
    empty = tmp_path / "empty"
    (empty / "2026-01-01").mkdir(parents=True)
    _app, c, _ctx = _client(tmp_path, roots=[(empty, "declared")])
    assert c.get("/pair/q1-2/loss-scans").get_data(as_text=True).strip() == ""


def test_a_scan_of_the_other_qubit_is_not_the_movers(tmp_path):
    """q1-2 moves its CONTROL (q1); a scan of q2 alone is not offered for it."""
    import numpy as np
    from tests.test_cz_loss import MHZ, pl_run
    storage = tmp_path / "storage"
    f = 3.9e9 + 5 * MHZ * np.arange(21)
    pl_run(storage, 200, {"q2": {"f": list(f), "loss": [0.02] * 21, "det": list(f - 4.0e9)}})
    _app, c, _ctx = _client(tmp_path, roots=[(storage, "project_storage")])
    assert c.get("/pair/q1-2/loss-scans").get_data(as_text=True).strip() == ""
