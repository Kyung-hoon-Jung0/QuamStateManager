"""v1.5.1 R3 MINOR 1: the read-only pill's re-probe asks what an Apply needs.

The re-probe opened state.json for update (``r+``), which tests writing the
FILE, not creating a file in the FOLDER. With a deny on creating files only,
the pill dropped while every Apply still failed, and it flapped. The
re-probe (only while the pill is up, at most every 10 s) now also creates a
probe file in the folder and removes it -- and never leaves one behind, nor
touches state.json / wiring.json.
"""
from __future__ import annotations

import json
import os

import pytest

from quam_state_manager.web import routes
from tests.test_v151_refused_write_rows import _ctx, env  # noqa: F401


def _pill_up(env):
    ctx = _ctx(env)
    ctx["live_readonly_hint"] = True
    ctx["_ro_probe_at"] = 0.0
    return ctx


def _listing(folder):
    return sorted(p.name for p in folder.iterdir())


def test_a_folder_that_refuses_new_files_keeps_the_pill(env, monkeypatch):
    live = env["live"]
    ctx = _pill_up(env)
    real_open = os.open
    folder = os.path.normcase(str(live))

    def no_new_files(path, flags, *a, **k):
        p = os.path.normcase(str(path))
        if p == folder or (p.startswith(folder + os.sep) and flags & os.O_CREAT):
            raise PermissionError(13, "Access is denied", str(path))
        return real_open(path, flags, *a, **k)
    monkeypatch.setattr(os, "open", no_new_files)
    with env["app"].test_request_context():
        routes._reprobe_readonly(ctx)
    assert ctx["live_readonly_hint"] is True, (
        "state.json opens for update, but the folder refuses new files: Apply still fails")


def test_a_writable_folder_drops_the_pill_and_leaves_nothing_behind(env):
    live = env["live"]
    before = _listing(live)
    st = [(live / f).stat() for f in ("state.json", "wiring.json")]
    ctx = _pill_up(env)
    with env["app"].test_request_context():
        routes._reprobe_readonly(ctx)
    assert ctx["live_readonly_hint"] is False
    assert _listing(live) == before, "the probe left a file in the chip folder"
    after = [(live / f).stat() for f in ("state.json", "wiring.json")]
    assert [(s.st_mtime_ns, s.st_size) for s in st] == [(s.st_mtime_ns, s.st_size) for s in after]


def test_the_probe_is_throttled(env, monkeypatch):
    ctx = _pill_up(env)
    calls = []
    monkeypatch.setattr(routes, "_folder_accepts_new_file",
                        lambda f: calls.append(f) or False)
    with env["app"].test_request_context():
        routes._reprobe_readonly(ctx)
        routes._reprobe_readonly(ctx)
    assert len(calls) == 1


@pytest.mark.skipif(os.name != "nt", reason="delete-on-close is a Windows open flag")
def test_the_probe_file_is_delete_on_close(env, monkeypatch):
    seen = []
    real_open = os.open

    def spy(path, flags, *a, **k):
        if ".sm-write-probe-" in str(path):
            seen.append(flags)
        return real_open(path, flags, *a, **k)
    monkeypatch.setattr(os, "open", spy)
    assert routes._folder_accepts_new_file(env["live"]) is True
    assert seen and all(f & os.O_TEMPORARY for f in seen), seen
    assert not [p for p in env["live"].iterdir() if ".sm-write-probe-" in p.name]
    json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
