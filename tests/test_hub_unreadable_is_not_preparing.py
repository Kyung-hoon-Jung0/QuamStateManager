"""S8 review P2-1: a ledger that cannot be read is said so, once -- not "Preparing" forever.

Every non-ledger answer of the per-path read used to be turned into
``preparing``, so a persistently failing read (a corrupt blob) made Trends,
the grid and the metric meta say "Preparing the change history..." and ask
again every 800 ms indefinitely, while the S7 drawer for the same value said
the ledger could not be read.
"""

from __future__ import annotations

import re

import pytest

from quam_state_manager.core import value_history
from tests.test_hub_drawer import _inline, sm  # noqa: F401


@pytest.fixture
def broken(sm, monkeypatch):
    real = value_history.read

    def read(chip_dir, targets, **kw):
        if targets:                      # the mode check (no paths) still answers
            raise ValueError("corrupt ledger blob")
        return real(chip_dir, targets, **kw)
    monkeypatch.setattr(value_history, "read", read)
    return sm


@pytest.mark.parametrize("url", ["/topology/trends?metrics=T1", "/param-history?since=all&props=T1"])
def test_a_surface_says_unreadable_and_stops_asking(broken, url):
    body = broken["client"].get(url).data.decode()
    assert "could not be read" in body and "Preparing" not in body, body[:300]
    wait = re.search(r'<p class="vh-wait"[^>]*>', body)
    assert wait and 'data-vh-mode="fallback"' in wait.group(0), body[:300]
    assert "hx-trigger" not in wait.group(0), "an end state never re-asks by itself"
    assert "Try again" in body


def test_the_metric_meta_says_unreadable_and_is_not_updating(broken):
    d = broken["client"].get("/topology/metric-meta").get_json()
    assert d["mode"] == "fallback" and d["updating"] is False, d
    assert any("could not be read" in n for n in d.get("notes", []))


def test_the_drawer_and_the_surfaces_use_the_same_words(broken):
    drawer = broken["client"].get("/field/history", query_string={"path": "qubits.qA1.T1"}).data.decode()
    trends = broken["client"].get("/topology/trends?metrics=T1").data.decode()
    assert "could not be read" in drawer and "could not be read" in trends


# S8 review P3: the Changes group of a run offers its data only when that run's own patch
# set at least one of the group's values (a data link only on proof, docs/283 §1.2)

def _group_head(body):
    return body.split('<div class="ph-change-group">')[1].split('<table class="ph-change-table">')[0]


def test_an_unproven_run_group_has_no_data_link(sm):
    head = _group_head(sm["client"].get("/param-history/changes?at=3").data.decode())
    assert "run #3" in head and "/dataset/" not in head, head


def test_a_run_whose_own_patch_set_a_value_keeps_its_data_link(sm):
    head = _group_head(sm["client"].get("/param-history/changes?at=2").data.decode())
    assert "/dataset/" in head, head
