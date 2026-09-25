"""QA round (agents menu), on a 30-qubit chip: a plan card's "values that may
change" read "now: not set" for every row, and its count stopped at the
60-row cap.

1. Real chips carry ``operations.x180 = "#./x180_DragCosine"``; the family's
   update path ``...operations.x180.amplitude`` was read with ``get_value``,
   which does not walk through a pointer, so the value the chip holds was
   reported as absent. It is followed now with the autofit writer's own
   ``families.resolve_alias_path`` (one function for "where does this write
   land"), and the row names the path the node really writes.
2. The card printed ``len(rows)`` as the count; the rows are capped at 60, so a
   40-qubit ``/run 05_power_rabi`` (80 values) said "60 value(s) may change".
"""

from __future__ import annotations

import copy
import json
import sys

import pytest

from quam_state_manager.core import scheduler
from quam_state_manager.web.app import create_app
from tests.test_agent_panel import cal, fakes, inst  # noqa: F401  (fixtures)
from tests.test_agent_runs import HUMAN

N = 40


def _state_with_aliases(n: int) -> dict:
    from tests.test_web import _make_state
    st = _make_state()
    base = st["qubits"]["qA1"]
    ops = base["xy"]["operations"]
    ops["x180"] = "#./x180_DragCosine"                 # the real chips' alias
    ops.setdefault("x90_DragCosine", {"amplitude": 0.0575, "length": 40, "alpha": -1.75})
    ops["x90"] = "#./x90_DragCosine"
    qubits = {}
    for k in range(1, n + 1):
        q = json.loads(json.dumps(base).replace("#/wiring/qubits/qA1/", "#/wiring/qubits/qA1/"))
        q["id"] = f"qA{k}"
        q["xy"]["operations"]["x180_DragCosine"]["amplitude"] = round(0.1 + k / 1000, 6)
        qubits[f"qA{k}"] = q
    st["qubits"] = qubits
    st["active_qubit_names"] = list(qubits)
    return st


@pytest.fixture
def c(tmp_path, inst, fakes, cal):  # noqa: F811
    from tests.test_web import _make_wiring
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_state_with_aliases(N)), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(inst))
    client = app.test_client()
    client.post("/load", data={"folder": str(d)})
    assert client.get("/api/agent/chip").get_json()["loaded"]
    with app.app_context():
        from quam_state_manager.web import routes as r
        scheduler.save_settings(r._sched_inst(), {"env_python": sys.executable, "calibrations_folder": str(cal)})
    return client


def test_rows_follow_the_alias_to_the_value_the_chip_holds(c):
    p = c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi qA1 qA2"}, headers=HUMAN).get_json()["plan"]
    rows = {(m["target"], m["label"]): m for m in p["may_change"]}
    pi1 = next(m for m in p["may_change"] if m["target"] == "qA1" and m.get("via", "").endswith(".x180.amplitude"))
    assert pi1["path"] == "qubits.qA1.xy.operations.x180_DragCosine.amplitude", pi1
    assert pi1["now"] == pytest.approx(0.101), "the value the chip holds, not 'not set'"
    assert next(m for m in p["may_change"] if m["target"] == "qA2" and m["path"].endswith("x180_DragCosine.amplitude"))["now"] == pytest.approx(0.102)
    assert all(m["now"] is not None for m in p["may_change"] if "…" not in m["path"]), rows


def test_the_count_is_the_total_not_the_capped_rows(c):
    line = "/run 05_power_rabi " + " ".join(f"qA{k}" for k in range(1, N + 1))
    p = c.post("/api/agent/plans", json={"run_line": line}, headers=HUMAN).get_json()["plan"]
    per_target = len({m["label"] for m in p["may_change"] if m["target"] == "qA1"})
    assert len(p["may_change"]) == 60, "the list stays bounded"
    assert p["may_change_total"] == N * per_target > 60, (p["may_change_total"], per_target)
    feed = c.get("/api/agent/chat/cards?after=0").get_json()
    assert feed["live"]["plans"][0]["may_change_total"] == N * per_target, "the feed carries the same total"


def test_agent_plan_count_selfcheck():
    """The card's count under jsdom (tests/agent_plan_count_selfcheck.cjs)."""
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([node, str(root / "tests" / "agent_plan_count_selfcheck.cjs")], capture_output=True,
                       text=True, encoding="utf-8", timeout=120, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and " 0 failed" in r.stdout, r.stdout[-2000:] + r.stderr[-1000:]
