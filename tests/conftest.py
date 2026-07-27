"""Shared test fixtures.

Isolates every test from the developer's real ``~/.qualibrate``. Without
this, the 130+ ``client.post("/load", ...)`` calls across the suite would
each read the real config tree — the project lens (docs/63) reverse-matches
loaded folders against the qualibrate project listing on chip activation —
making results depend on whatever the dev machine has configured, and
paying real TOML/stat I/O per load.

Tests that NEED a qualibrate tree (test_qualibrate_routes,
test_project_scope) set ``QUALIBRATE_CONFIG_FILE`` themselves via their own
fixtures or bodies; those layer AFTER this autouse fixture and win for the
duration of the test.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_qualibrate_config(tmp_path_factory, monkeypatch):
    missing = tmp_path_factory.getbasetemp() / "_no_qualibrate_config"
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(missing))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("QUALIBRATE_STATE_PATH", raising=False)
    monkeypatch.delenv("QUAM_STATE_PATH", raising=False)
