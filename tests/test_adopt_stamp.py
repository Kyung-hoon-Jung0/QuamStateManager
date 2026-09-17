"""The adopt-before-ingest timestamp (docs/196 open item, reproduced in docs/200).

The customer's own scenario: run 50–70 experiments with SM **closed**, then open
it. The chip holds the last run's state, history has never seen that content, so
the adopt snapshot cannot dedup — it lands stamped with the moment SM opened,
``kind="exp"``. The run's own ingest arrives afterwards, finds the content
already stored, and ENRICHES it (docs/132) — adding ``run_id`` and the
experiment name, but never the timestamp.

The result is a data point on Trends sitting at the time someone opened SM
rather than the time the experiment ran, which is the one thing the customer
said must not happen:

    "비록 sync버튼을 뒤늦게 눌렀더라도, trends의 그래프에 찍히는 value들의 날짜는
     반드시 실험을 수행한 그 날짜/시간 이어야한다"

Every OTHER run in such a batch is fine — they are ingested from their own
folders and carry their own stamps (``_entry_timestamp``). This is specifically
the one whose content the chip was already holding when SM opened.

The test below asserts the DESIRED behaviour and is expected to fail today. It
is written this way on purpose: it turns green the day the ordering (or the
re-stamp) lands, instead of quietly encoding the wrong answer as correct.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from quam_state_manager.core.history import HistoryManager
from quam_state_manager.core.scanner import ExperimentEntry


def _chip(folder: Path, t1: float) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "T1": t1}}, "qubit_pairs": {},
         "active_qubit_names": ["q1"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {}, "wiring": {"qubits": {}}}), encoding="utf-8")


def _run(root: Path, run_id: int, day: str, hhmmss: str, t1: float) -> ExperimentEntry:
    d = root / day / f"#{run_id}_some_node_{hhmmss}"
    _chip(d / "quam_state", t1)
    (d / "node.json").write_text(json.dumps(
        {"created_at": f"{day}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}",
         "metadata": {"name": "some_node"}}), encoding="utf-8")
    return ExperimentEntry(
        folder_path=d, quam_state_path=d / "quam_state", run_id=run_id,
        experiment_name="some_node",
        timestamp=f"{day}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}",
        status="finished", qubits=["q1"], qubit_pairs=[], outcomes={},
        parent_ids=[], date_str=day, is_standalone=False)


def _stamps(inst: Path) -> list[str]:
    root = inst / "history"
    return sorted(d.name for d in root.rglob("2*") if d.is_dir() and d.name[:4].isdigit())


class TestALateSyncKeepsTheRunsOwnTime:
    def test_ingest_first_is_already_correct(self, tmp_path):
        """The control: when the run is ingested BEFORE the adopt, everything
        is right — which is why the scheduler hook was reordered (docs/132)."""
        inst, chip, data = tmp_path / "_i", tmp_path / "chip", tmp_path / "data"
        _chip(chip, 1.0e-5)
        hm = HistoryManager(str(inst))
        entry = _run(data, 77, "2026-09-10", "141516", 4.2e-5)

        hm.ingest_run(str(chip), entry)                 # the run lands first
        _chip(chip, 4.2e-5)                             # then SM adopts it
        hm.check_and_snapshot(str(chip), "auto", kind="exp")

        assert any(s.startswith("20260910") for s in _stamps(inst)), \
            "the run's own date is in history"

    @pytest.mark.xfail(
        reason="docs/200: the adopt snapshot is stamped 'now' and the run's "
               "later ingest enriches it without correcting the timestamp, so "
               "the point lands on the day SM was opened. Fix options are in "
               "docs/200 §3; this turns green when one of them lands.",
        strict=False)
    def test_adopt_first_still_keeps_the_runs_own_time(self, tmp_path):
        """The customer's scenario: SM was CLOSED while the run wrote the chip."""
        inst, chip, data = tmp_path / "_i", tmp_path / "chip", tmp_path / "data"
        _chip(chip, 1.0e-5)
        hm = HistoryManager(str(inst))
        entry = _run(data, 77, "2026-09-10", "141516", 4.2e-5)

        _chip(chip, 4.2e-5)                             # the run wrote it, days ago
        hm.check_and_snapshot(str(chip), "auto", kind="exp")   # SM opens and adopts
        hm.ingest_run(str(chip), entry)                 # the ingest follows

        stamps = _stamps(inst)
        assert any(s.startswith("20260910") for s in stamps), (
            "the run ran on 2026-09-10, so its value belongs at that date; "
            f"history holds {stamps}")

    def test_the_enrich_does_attach_the_run_even_today(self, tmp_path):
        """What the current behaviour DOES get right, pinned so a fix keeps it:
        the linkage is never lost, only the time is wrong."""
        inst, chip, data = tmp_path / "_i", tmp_path / "chip", tmp_path / "data"
        _chip(chip, 1.0e-5)
        hm = HistoryManager(str(inst))
        entry = _run(data, 77, "2026-09-10", "141516", 4.2e-5)

        _chip(chip, 4.2e-5)
        hm.check_and_snapshot(str(chip), "auto", kind="exp")
        res = hm.ingest_run(str(chip), entry)

        assert res.get("skipped_duplicate") == 1
        assert res.get("enriched") == 1
        metas = [json.loads(p.read_text(encoding="utf-8"))
                 for p in (inst / "history").rglob("meta.json")]
        assert any(m.get("run_id") == 77 for m in metas), \
            "the run linkage survives — it is the timestamp that does not"
