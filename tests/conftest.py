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

import os

import pytest

# create_app() warms the conda env inventory in a background thread (docs/135).
# A dozen test modules build a REAL app, so without this the suite would spawn
# a 4 s `conda env list` per app and race the cache-reset fixture below.
os.environ.setdefault("SM_DISABLE_ENV_WARMUP", "1")


@pytest.fixture(autouse=True)
def _isolate_qualibrate_config(tmp_path_factory, monkeypatch):
    missing = tmp_path_factory.getbasetemp() / "_no_qualibrate_config"
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(missing))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("QUALIBRATE_STATE_PATH", raising=False)
    monkeypatch.delenv("QUAM_STATE_PATH", raising=False)


@pytest.fixture(autouse=True)
def _isolate_env_discovery_cache():
    """The conda env inventory is memoized per process (docs/135) — clear it
    around every test so one test's monkeypatched fake list can never be
    served to the next one from the memo."""
    from quam_state_manager.core import config_generator

    config_generator.reset_env_discovery_cache()
    yield
    config_generator.reset_env_discovery_cache()
