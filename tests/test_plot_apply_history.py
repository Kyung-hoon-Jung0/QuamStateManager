"""Later calibration warnings require both a source anchor and proven ledger writes."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.web.plot_apply_history import changed_since


def history():
    source = str(Path("/runs/source").absolute())
    later = str(Path("/runs/later").absolute())
    point = {"kind": "run", "folder": later, "t_us": 2_000_000,
             "t": "2026-10-13T10:02:00Z", "run_id": 514, "proven": True,
             "provenance": "run_proven", "flags": [], "value": .15}
    return source, {"mode": "ledger", "runs": [], "rows": {"value": {"points": [
        {**point, "folder": source, "t_us": 1_000_000, "run_id": 502}, point]}}}


def test_proven_later_write_names_the_run():
    source, answer = history()
    result = changed_since(answer, source)
    assert result["proven"] is True and result["run_id"] == 514
    assert "Changed since this run by #514 at" in result["text"]


@pytest.mark.parametrize("change", ["unproven", "saved", "earlier", "undone", "wrong_source", "unknown_time", "uncertain", "rewritten"])
def test_unproven_or_unordered_history_never_names_a_writer(change):
    source, answer = history()
    points = answer["rows"]["value"]["points"]
    if change == "unproven": points[1]["proven"] = False
    if change == "saved": points[1]["provenance"] = "run_saved"
    if change == "earlier": points[1]["t_us"] = 500_000
    if change == "undone": points[1]["undone"] = True
    if change == "wrong_source": source = "/runs/other"
    if change == "unknown_time": points[1]["flags"] = ["time_assumed"]
    if change == "uncertain": points[1]["flags"] = ["chip_uncertain"]
    if change == "rewritten": points[1]["flags"] = ["rewritten"]
    result = changed_since(answer, source)
    assert result["proven"] is False and "unknown" in result["text"]
    assert "#514" not in result["text"]


def test_field_peek_passes_run_and_target_to_the_shared_ledger(tmp_path, monkeypatch):
    from tests.state_model.repros._rig import Chip
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    source, answer = history()
    path = "qubits.q1.T1"
    calls = []
    monkeypatch.setattr(routes, "_resolve_run", lambda uid: (SimpleNamespace(runs={502: SimpleNamespace(folder_path=source)}), 502, ""))
    def read(ctx, paths, **kw):
        calls.append(paths)
        return {**answer, "rows": {path: answer["rows"]["value"]}}
    monkeypatch.setattr(routes, "_value_history", read)
    result = chip.c.get("/field/peek", query_string={"dot_path": path, "run_uid": "root:502"}).get_json()
    assert calls == [{path: path}]
    assert result["since_run"][path]["proven"] and result["since_run"][path]["run_id"] == 514
