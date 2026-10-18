"""v1.5.1 P2: times read in the app's display zone everywhere.

* T1-F3 -- the CZ gate plan's "Last set" printed the history's raw UTC stamp
  ("2026-10-15 00:47:40 UTC" for a run whose folder said 09:47:40) while every
  other surface shows the project zone with its offset. Pinned against the
  REAL /cz-plan markup and /cz-plan/history answer in
  ``tests/v151_display_zone_selfcheck.cjs``.
* U P2-17 -- the Versions pill's title named the live chip's version by its
  raw UTC-based id ("recorded version 20261018_113153_6780" at 20:31 local).
  It now says when that version was recorded, in the page's zone; the id
  stays machine-readable on ``data-version``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes as R
from tests.test_cz_plan_page import _client

ROOT = Path(__file__).resolve().parent.parent
ZONE = "Asia/Seoul"
OP = "qubit_pairs.q1-2.macros.cz_unipolar"
OTHER = "qubit_pairs.q3-2.macros.cz"


@pytest.fixture
def seoul(monkeypatch):
    monkeypatch.setattr(R, "_display_zone", lambda: {"zone": ZONE})


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_cz_plan_last_set_reads_in_the_display_zone(tmp_path, seoul):
    c = _client(tmp_path)
    page = c.get(f"/cz-plan?op={OP}", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert f'data-last="{OP}"' in page and f'data-last="{OTHER}"' in page
    hist = c.get("/cz-plan/history").get_json()
    assert hist["ok"] and OP in hist["ops"] and OTHER in hist["ops"]
    # the route's own shape, one operation's last change filled in (a ledger
    # point), the other unknown with the core's "first recorded <t>" note
    hist["ops"][OP].update(last_set={"t": "2026-10-15T00:47:40Z", "run_id": 1687,
                                     "provenance": "run_proven", "proven": True},
                           unknown=False, proven=True)
    hist["ops"][OTHER]["note"] = ("qubit_pairs.q3-2.macros.cz.flux_pulse_qubit.amplitude: "
                                  "first recorded 2026-10-15T00:47:40Z; setting time unknown")
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"zone": ZONE, "page": page, "history": hist, "op": OP,
                              "other_op": OTHER,
                              "expect_long": "2026-10-15 09:47:40 (UTC+9)"}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_display_zone_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"


def test_the_versions_pill_names_the_version_by_its_local_time(tmp_path, seoul):
    c = _client(tmp_path)
    assert c.post("/state/archive", data={"tag": "t"}).status_code < 500
    body = c.get("/state/version").get_data(as_text=True)
    m = re.search(r'data-version="(\d{8}_\d{6}[^"]*)"', body)
    assert m, body
    vid = m.group(1)
    title = re.search(r'class="state-version-chip[^"]*"[^>]*?title="([^"]*)"', body, re.S).group(1)
    assert vid not in title, "the raw UTC id is not the name a person reads"
    from quam_state_manager.core.timefmt import local_text, to_utc
    assert f"the version recorded {local_text(to_utc(vid), ZONE)}." in title, title
    assert "(UTC+9)" in title
