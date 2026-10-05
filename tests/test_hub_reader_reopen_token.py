"""S8 review P1-1: a cached answer is never served across a reader reopen.

``PRAGMA data_version`` counts per connection, and the ledger reader of a chip
is reopened when eight other chips' ledgers are read in the same process (the
reader LRU) or when its file is replaced. The cache token used to carry only
that counter, so a run folder rewritten after a reopen left the Chip Status
meta saying "its own patch set it" about a run whose patch was gone.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from quam_state_manager.core import hub_index, hub_sync
from tests.test_hub_drawer import _inline, _small_ledger, chip_dir, sm  # noqa: F401

META = "/topology/metric-meta"


def _rewrite_without_patch_and_sync(sm):
    folder = next((sm["data"] / "2026-01-01").glob("#2_*"))
    body = json.loads((folder / "node.json").read_text(encoding="utf-8"))
    body.pop("patches", None)
    (folder / "node.json").write_text(json.dumps(body), encoding="utf-8")
    sync = hub_sync.sync_for(chip_dir(sm))
    sync.request(full=True)
    hub_sync._kick(sync)


def _ledger_now(sm):
    with sm["app"].test_request_context():
        from quam_state_manager.web import routes
        fresh = routes._value_history(routes._active_ctx(), {"t": "qubits.qA1.T1"})
        return fresh["rows"]["t"]["effective"][-1]


def test_eight_other_ledgers_evict_the_reader_and_the_meta_still_follows(sm, tmp_path):
    c = sm["client"]
    before = c.get(META).get_json()["q"]["T1"]["qA1"]
    assert before["provenance"] == "run_proven"
    for i in range(8):                              # the reader LRU holds eight chips
        with hub_index.snapshot(SimpleNamespace(directory=_small_ledger(tmp_path, f"o{i}"))):
            pass
    _rewrite_without_patch_and_sync(sm)
    after = c.get(META).get_json()["q"]["T1"]["qA1"]
    now = _ledger_now(sm)
    assert now["provenance"] != "run_proven", "the rewrite took the proof away"
    assert after["provenance"] == now["provenance"] and after["writer"] is None, after
    hub_index.close_readers()


def test_each_reader_opening_has_its_own_generation(sm):
    c = sm["client"]
    c.get(META)
    gens = {r.conn.gen for r in hub_index._READERS.values()}
    hub_index.close_readers()
    c.get(META)
    assert gens and not gens & {r.conn.gen for r in hub_index._READERS.values()}
    hub_index.close_readers()
