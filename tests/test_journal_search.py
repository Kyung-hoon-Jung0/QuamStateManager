"""Calibration log search, driven through shipped templates and JavaScript."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def search_fixture():
    """Render real read routes over a synthetic day with every search group."""
    from quam_state_manager.core import story
    from quam_state_manager.web.app import create_app

    day = "2026-10-04"

    def line(text, kind="agent"):
        return {"text": text, "kind": kind, "time": "12:00:00", "ts": 1, "because": ""}

    def build(*args, **kwargs):
        cards = []
        for i in range(1, 9):
            node = ["ramsey", "rabi", "echo"][min(i - 1, 2)]
            cards.append({
                "kind": "run", "run_id": i, "node": node, "family_label": node,
                "family_short": node, "targets": [f"qA{i}"], "time": "12:00:00",
                "ts": i, "author": "human:alice", "certainty": "claimed",
                "outcome": "ok", "gate": None, "writes": [], "params_diff": [],
                "because": "", "figure": None, "uid": None, "plan_id": None,
                "note": "", "duration_s": 1, "folder": "data",
                "journal": [line("ramsey attached"), line("unrelated attached", "human")]
                if i == 1 else [],
            })
        for i, node in enumerate(["ramsey", "rabi"], 1):
            cards.append({"kind": "write", "id": i, "time": "12:00:00", "ts": 10 + i,
                          "author": "human:alice", "src": "manual", "plan_id": None,
                          "entries": [{"path": f"qubits.qA{i}.{node}", "old": 1,
                                       "new": 2, "actor": "human:alice"}]})
        return {"day": day, "chip": "chipX", "cards": cards,
                "loose": [line("ramsey human", "human"), line("ramsey agent"),
                          line("looseonly agent"), line("rabi human", "human")],
                "unassigned": [line("orphan line", "hook") for _ in range(7)]
                + [line("tailonly line", "hook")],
                "counts": story._counts(cards), "timeline": story._timeline(cards)}

    with tempfile.TemporaryDirectory() as folder:
        app = create_app(testing=True, instance_path=folder)
        with patch.object(story, "build_day", side_effect=build):
            client = app.test_client()
            return {"html": client.get(f"/journal?day={day}", headers={"HX-Request": "true"}).get_data(as_text=True),
                    "filtered": client.get(f"/journal?day={day}&q=qA1", headers={"HX-Request": "true"}).get_data(as_text=True),
                    "swap": client.get(f"/journal/day?day={day}&q=qA2").get_data(as_text=True),
                    "grammar": {q: client.get("/journal/day", query_string={"day": day, "q": q}).get_data(as_text=True)
                                for q in ["ramsey qA1", "ramsey | rabi", "ramsey|rabi"]}}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_journal_search_selfcheck():
    env = {**os.environ, "JOURNAL_SEARCH_PYTHON": sys.executable}
    result = subprocess.run(["node", str(ROOT / "tests/journal_search_selfcheck.cjs")],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding="utf-8", timeout=120)
    if result.returncode == 2:
        pytest.skip("jsdom not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all checks passed (24 pins)" in result.stdout, result.stdout


if __name__ == "__main__":
    os.environ["SM_DISABLE_ENV_WARMUP"] = "1"
    os.environ["QUALIBRATE_CONFIG_FILE"] = str(ROOT / "tmp_cr_audit/log_search/missing-config")
    print(json.dumps(search_fixture()))
