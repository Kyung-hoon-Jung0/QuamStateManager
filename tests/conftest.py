"""Shared test fixtures.

Isolates every test from two things the developer is actually using: their
real ``~/.qualibrate`` tree, and their real ``instance/`` directory.

Without the first, the 130+ ``client.post("/load", ...)`` calls across the suite would
each read the real config tree — the project lens (docs/63) reverse-matches
loaded folders against the qualibrate project listing on chip activation —
making results depend on whatever the dev machine has configured, and
paying real TOML/stat I/O per load.

Without the second, the ~22 ``create_app()`` call sites that name no instance
dir write their working copies, session file and history sidecars into the
directory the developer's own SM is using (docs/155 F7).

Tests that NEED a qualibrate tree (test_qualibrate_routes,
test_project_scope) set ``QUALIBRATE_CONFIG_FILE`` themselves via their own
fixtures or bodies; those layer AFTER this autouse fixture and win for the
duration of the test.
"""
from __future__ import annotations

import os
import tempfile

import pytest

# create_app() warms the conda env inventory in a background thread (docs/135).
# A dozen test modules build a REAL app, so without this the suite would spawn
# a 4 s `conda env list` per app and race the cache-reset fixture below.
os.environ.setdefault("SM_DISABLE_ENV_WARMUP", "1")

# Listing a chip's snapshots arms a background sweep that re-stats the sidecar
# (docs/155 10h). It is harmless but it is a THREAD touching tmp dirs the test
# is about to delete, and its timing is not the subject of any test. Off by
# default; the pins that are about it either call `verify_manifest` directly or
# clear this variable themselves.
os.environ.setdefault("SM_DISABLE_HISTORY_VERIFY", "1")


@pytest.fixture
def tmp_path(tmp_path):
    """Hand every test the CANONICAL on-disk spelling of its temp dir.

    Windows is case-insensitive but case-PRESERVING, and pytest builds its base
    temp dir from ``getpass.getuser()`` -- "measurement" on this machine, while
    the directory that actually exists is ``pytest-of-Measurement``.
    ``Path.resolve()`` returns what is on disk, so SM -- which canonicalizes
    every root and chip path it registers, deliberately, so two spellings of one
    folder can never become two entries -- stores its state under the capital-M
    spelling while the test looks it up under the lowercase one it was handed.

    The result is not a wrong answer from SM but a lookup miss in the test:
    ``KeyError: 'C:\...\Temp\pytest-of-measurement\...'``, or a staleness
    probe that finds no recorded spine for the root it was asked about and
    therefore answers "stale" forever. Sixteen failures across five files were
    this and nothing else (docs/155 10e), and for months they were read as an
    OS-behaviour class the product had to live with.

    Resolving here is a no-op wherever the two spellings already agree, which
    includes POSIX and any Windows box whose account name is lowercase.
    """
    return type(tmp_path)(os.path.realpath(tmp_path))


@pytest.fixture(autouse=True)
def _isolate_qualibrate_config(tmp_path_factory, monkeypatch):
    missing = tmp_path_factory.getbasetemp() / "_no_qualibrate_config"
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(missing))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("QUALIBRATE_STATE_PATH", raising=False)
    monkeypatch.delenv("QUAM_STATE_PATH", raising=False)


@pytest.fixture(autouse=True)
def _isolate_instance_dir(tmp_path_factory, monkeypatch, request):
    """No test run writes outside pytest's own tmp tree (docs/155 F7).

    ``create_app()`` with no ``instance_path`` falls back to
    ``default_instance_path()``, which is ``None`` in a repo checkout — so
    Flask derives the REPO's ``instance/`` and a couple of dozen test call
    sites were writing into the developer's own SM state. Measured on six
    test files: 33 stray working copies created, and ``last_session.json``,
    ``workspace_roots.json`` and docs/139's ``history/_fingerprints.json``
    REWRITTEN — the developer's configured workspace roots replaced by a
    test's tmp paths.

    The default is redirected lazily, so only a test that actually builds
    such an app pays for a directory. ``create_app(testing=True)`` takes an
    earlier branch (``tempfile.mkdtemp``) which never consults the default and
    left 327 ``quam_test_instance_*`` dirs in %TEMP% on this machine; that one
    call is redirected by its own prefix into the basetemp pytest garbage
    collects, without touching the production branch.

    Opt out with ``@pytest.mark.real_instance_path`` — the handful of tests
    that assert on the instance-path POLICY itself (``test_pip_install``'s
    ``TestDefaultInstancePath``, and the ``_user_instance_path`` pin) need the
    real function. Those tests fail loudly if this opt-out ever stops working,
    which is what keeps the marker honest.
    """
    if request.node.get_closest_marker("real_instance_path"):
        return
    from quam_state_manager.web import app as app_mod

    made: list[str] = []

    def _tmp_default() -> str:
        if not made:
            made.append(str(tmp_path_factory.mktemp("sm_instance")))
        return made[0]

    monkeypatch.setattr(app_mod, "default_instance_path", _tmp_default)

    real_mkdtemp = tempfile.mkdtemp

    def _mkdtemp(*a, **k):
        if k.get("prefix") == "quam_test_instance_":
            return str(tmp_path_factory.mktemp("sm_testing_instance"))
        return real_mkdtemp(*a, **k)

    monkeypatch.setattr(tempfile, "mkdtemp", _mkdtemp)


@pytest.fixture(autouse=True)
def _isolate_env_discovery_cache():
    """The conda env inventory is memoized per process (docs/135) — clear it
    around every test so one test's monkeypatched fake list can never be
    served to the next one from the memo."""
    from quam_state_manager.core import config_generator

    config_generator.reset_env_discovery_cache()
    yield
    config_generator.reset_env_discovery_cache()
