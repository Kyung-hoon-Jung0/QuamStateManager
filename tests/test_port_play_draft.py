"""The /port-play page offers the Generate wizard's unsaved draft as a rack source."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_port_play_page import chip, client  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[1]


def test_draft_source_selfcheck_jsdom(client, chip, tmp_path):  # noqa: F811
    nochip = client.get("/port-play", headers={"HX-Request": "true"}).get_data(as_text=True)
    client.post("/load", data={"folder": str(chip)})
    withchip = client.get("/port-play", headers={"HX-Request": "true"}).get_data(as_text=True)
    a, b = tmp_path / "nochip.html", tmp_path / "chip.html"
    a.write_text(nochip, encoding="utf-8")
    b.write_text(withchip, encoding="utf-8")
    env = dict(os.environ, NODE_PATH=os.environ.get("NODE_PATH", "D:/work_laptop/statemanager-cfb/node_modules"))
    r = subprocess.run(["node", str(ROOT / "tests" / "port_play_draft_selfcheck.cjs"), str(a), str(b)],
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "port play draft selfcheck ok" in r.stdout


def test_the_draft_key_and_version_match_the_wizard():
    gen = (ROOT / "quam_state_manager/web/static/generate.js").read_text(encoding="utf-8")
    pp = (ROOT / "quam_state_manager/web/static/port-play.js").read_text(encoding="utf-8")
    gv = re.search(r"var DRAFT_VERSION = (\d+);", gen).group(1)
    assert re.search(r"var DRAFT_KEY = 'quam_generate_draft', DRAFT_VERSION = (\d+);", pp).group(1) == gv
    assert 'var DRAFT_KEY = "quam_generate_draft";' in gen
