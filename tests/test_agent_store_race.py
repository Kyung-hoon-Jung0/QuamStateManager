"""Two people pressing the same card must not wipe every card on the chip.

Found by the two-windows stress lane (2026-09-11) and reproduced deliberately:
one in forty aligned bursts left a byte-identical corrupt tail. All four agent
stores wrote through a FIXED temp path per chip —

    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(...)
    os.replace(tmp, p)

— so two concurrent writers shared one temp file: their bytes interleaved,
`os.replace` moved the mixture into place, and `load()`'s
`except (OSError, ValueError): return []` turned it into an empty list. Every
plan card gone, no message, and still gone after a restart because the damage
was on disk. The losing request 500s.

This project had already solved it for this same customer: `safe_io._tmp_for`
(2026-09-09) exists because "the temp file used to be a fixed `<file>.tmp`, so
two writers of the SAME file shared one temp". These four stores never used it.

Atomicity stops a CORRUPT file; it does not stop a LOST UPDATE, so the plan
store's read-modify-write is also under one lock per file. Both browser
windows talk to one Flask process, which is the whole of the reported case.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import agent_plans, approvals, agent_session, limits

_ROOT = Path(__file__).resolve().parent.parent


class TestNoStoreSharesATempFile:
    MODULES = ["agent_plans", "approvals", "agent_session", "limits"]

    @pytest.mark.parametrize("mod", MODULES)
    def test_the_fixed_temp_path_is_gone(self, mod):
        src = (_ROOT / "quam_state_manager" / "core" / (mod + ".py")).read_text(encoding="utf-8")
        assert 'with_suffix(".json.tmp")' not in src, (
            "a fixed temp is shared by two writers; safe_io._tmp_for exists for this")

    @pytest.mark.parametrize("mod", MODULES)
    def test_it_goes_through_the_one_atomic_writer(self, mod):
        src = (_ROOT / "quam_state_manager" / "core" / (mod + ".py")).read_text(encoding="utf-8")
        assert "safe_io.atomic_write_json" in src


class TestTheRaceItself:
    """Driven, not asserted from the source: N threads appending at once."""

    def test_concurrent_adds_never_corrupt_the_file(self, tmp_path):
        inst, chip = tmp_path / "inst", "chipA"
        errors: list = []

        def add(i):
            try:
                agent_plans.add(inst, chip, title="p%d" % i, mode=None, created_by="human:t%d" % i,
                                steps=[{"node": "05_rabi", "targets": ["q1"]}])
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=add, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors[:3]
        # the file is still JSON — the corruption showed up as load() == []
        raw = agent_plans.path_for(inst, chip).read_text(encoding="utf-8")
        json.loads(raw)
        rows = agent_plans.load(inst, chip)
        assert len(rows) == 40, (
            "a lost update: %d of 40 plans survived" % len(rows))
        assert len({r["id"] for r in rows}) == 40

    def test_concurrent_updates_do_not_lose_each_other(self, tmp_path):
        inst, chip = tmp_path / "inst", "chipB"
        recs = [agent_plans.add(inst, chip, title="p%d" % i, mode=None, created_by="human",
                                steps=[{"node": "05_rabi", "targets": ["q1"]}]) for i in range(12)]
        errors: list = []

        def touch(rec, i):
            try:
                agent_plans.update(inst, chip, rec["id"], note="n%d" % i)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=touch, args=(r, i)) for i, r in enumerate(recs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors[:3]
        rows = {r["id"]: r for r in agent_plans.load(inst, chip)}
        assert len(rows) == 12
        for i, rec in enumerate(recs):
            assert rows[rec["id"]]["note"] == "n%d" % i, (
                "one window's write erased another's")

    def test_no_temp_file_is_left_behind(self, tmp_path):
        inst, chip = tmp_path / "inst", "chipC"
        for i in range(5):
            agent_plans.add(inst, chip, title="p%d" % i, mode=None, created_by="human",
                            steps=[{"node": "05_rabi", "targets": ["q1"]}])
        folder = agent_plans.path_for(inst, chip).parent
        assert not list(folder.glob("*.tmp*")), list(folder.glob("*.tmp*"))

    def test_the_siblings_survive_a_burst_too(self, tmp_path):
        """approvals / session / limits share the shape, so they share the
        check — a corrupt one of these loses an approval or a stop."""
        inst, chip = tmp_path / "inst", "chipD"
        errors: list = []

        def hammer(i):
            try:
                agent_session.save(inst, chip, backend="claude", owner="human:t%d" % i)
                limits.save(inst, chip, {"mode": "ask-writes"}, who="human")
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=hammer, args=(i,)) for i in range(24)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors[:3]
        assert isinstance(agent_session.load(inst, chip), dict)
        assert limits.load(inst, chip).get("mode") == "ask-writes"
        assert isinstance(approvals.load(inst, chip), list)
