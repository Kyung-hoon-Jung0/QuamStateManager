"""SM says something before the disk does — and never deletes your snapshots.

Customer, 2026-09-11, after a machine-wide temp audit found 28.79 GB under one
scratch folder:

  "우리 SM이 엄청나게 캐시를 계속 쌓아두나봐. 이거 자동으로 정리하게 하거나,
   최소한 유저에게 일정 용량되면(20GB정도?) 알려줘서 삭제하든 옮기든 알려주자."

MEASURED first, because it changed what to build. That 28.79 GB was the
assistant's own browser-automation Chrome profiles, not SM: the live instance on
the same machine was **0.19 GB** (history 172 MB / 1,331 files, working_state
15 MB, workspace_cache 7 MB).

The concern was right even though the diagnosis was not. Nothing in SM ever
deletes a snapshot — ``DEFAULT_MAX_SNAPSHOTS`` is 100,000 so ``_prune`` never
fires — and docs/143's aging audit explicitly left snapshot disk copies alone
("disk, not speed"). Measured growth on the busiest chip here: 1,309 snapshot
files in 3 days, about **56 MB a day**.

So the guard MEASURES and SAYS. The one thing it must never do is decide for
you: a snapshot is your record of what the chip was, and a tool that quietly
threw one away would be worse than a full disk. That is what most of this file
pins.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core import instance_disk as idk
from quam_state_manager.web.app import create_app


def _fill(p: Path, mb: float) -> None:
    """A file of the given SIZE, without writing the bytes.

    The sizes here have to be realistic: `settings` floors `limit_gb` at 0.1 GB,
    so on a fixture of a few MB no limit can put the folder over 80% and every
    threshold pin below would pass without testing anything. `truncate` gives a
    real `st_size` -- which is all `_dir_size` reads -- without putting 300 MB
    through the disk on every run of the suite.
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    n = int(mb * 1024 * 1024)
    with open(p, "wb") as f:
        if n:
            f.truncate(n)


@pytest.fixture(autouse=True)
def _fresh_measurement():
    """The memo is a module global keyed by root, so a stale entry from another
    test could answer for this one's folder."""
    idk._cache.update(key=None, at=0.0, value=None)
    yield
    idk._cache.update(key=None, at=0.0, value=None)


@pytest.fixture
def inst(tmp_path):
    """An instance shaped like a real one: a big record, a big cache."""
    root = tmp_path / "inst"
    _fill(root / "history" / "chipA" / "2026-09-11" / "state.json", 60)
    _fill(root / "history" / "chipA" / "2026-09-10" / "state.json", 60)
    _fill(root / "working_state" / "chipA" / "state.json", 10)
    _fill(root / "workspace_cache" / "ws_abc.json", 40)
    _fill(root / "story_cache" / "s1.json", 10)
    (root / "last_session.json").write_text("{}", encoding="utf-8")
    return root


class TestItMeasuresWhatIsThere:
    def test_the_total_and_the_breakdown(self, inst):
        m = idk.measure(inst, force=True)
        names = {d["name"]: d for d in m["dirs"]}
        assert set(names) == {"history", "working_state", "workspace_cache", "story_cache"}
        assert names["history"]["bytes"] == 120 * 1024 * 1024
        assert names["history"]["files"] == 2
        # the loose file at the root counts toward the total, or the number the
        # banner shows is smaller than the folder really is
        assert m["bytes"] > sum(d["bytes"] for d in m["dirs"])
        assert m["dirs"][0]["name"] == "history", "biggest first"

    def test_it_is_memoized_but_a_reclaim_invalidates_it(self, inst):
        a = idk.measure(inst, force=True)["bytes"]
        _fill(inst / "workspace_cache" / "more.json", 30)
        assert idk.measure(inst)["bytes"] == a, "served from the memo"
        b = idk.measure(inst, force=True)["bytes"]
        assert b == a + 30 * 1024 * 1024
        idk.reclaim(inst, ["story_cache"])                     # 10 MB of it
        assert idk.measure(inst)["bytes"] == b - 10 * 1024 * 1024, (
            "a reclaim must drop the memo, or the page still shows the old size")

    def test_a_folder_that_is_not_there_measures_zero(self, tmp_path):
        root = tmp_path / "i2"
        _fill(root / "a" / "f.bin", 1)
        assert idk.measure(root, force=True)["bytes"] == 1024 * 1024
        # a path that vanishes between two page loads must not raise
        assert idk.measure(tmp_path / "does-not-exist", force=True)["bytes"] == 0

    def test_an_unreadable_entry_is_skipped_and_the_rest_still_counted(
            self, inst, monkeypatch):
        """A file locked by a running experiment, or one deleted between the
        scandir and the stat, must not take the whole measurement down with it.

        The number is then short by that one file and honest about everything
        else — which is the point: a total that omits a locked file is far
        closer to the truth than no total at all. The hostile entry goes FIRST,
        because appended last it would be reached only after every real file was
        already counted and the guard could be deleted without moving a number.
        """
        real = os.scandir
        hostile = {str(inst), str(inst / "history")}

        class Locked:
            name = "locked-by-the-experiment.json"
            path = str(inst / "history" / "locked-by-the-experiment.json")

            def is_dir(self, follow_symlinks=True):
                raise OSError(13, "being written")

            def is_file(self, follow_symlinks=True):
                raise OSError(13, "being written")

            def stat(self, follow_symlinks=True):
                raise OSError(13, "being written")

        class Scan:
            def __init__(self, it):
                self._items = [Locked()] + list(it)

            def __iter__(self):
                return iter(self._items)

            def __enter__(self):
                return iter(self._items)

            def __exit__(self, *exc):
                return False

        def fake_scandir(path="."):
            return Scan(real(path)) if str(path) in hostile else real(path)

        monkeypatch.setattr(idk.os, "scandir", fake_scandir)
        m = idk.measure(inst, force=True)
        names = {d["name"]: d for d in m["dirs"]}
        assert names["history"]["bytes"] == 120 * 1024 * 1024, (
            "one unreadable entry cost the whole folder")
        assert names["history"]["files"] == 2
        assert set(names) == {"history", "working_state",
                              "workspace_cache", "story_cache"}


class TestItNeverDeletesTheRecord:
    def test_reclaim_refuses_by_name(self, inst):
        before = idk.measure(inst, force=True)["bytes"]
        out = idk.reclaim(inst, ["history"])
        assert out["ok"] is False
        assert out["refused"] == ["history"]
        assert out["removed"] == []
        assert idk.measure(inst, force=True)["bytes"] == before
        assert (inst / "history" / "chipA" / "2026-09-11" / "state.json").is_file()

    def test_it_refuses_rather_than_filtering_silently(self, inst):
        """Answering 'done' to a caller who asked to delete `history` would be a
        lie about the thing this module exists to protect."""
        out = idk.reclaim(inst, ["workspace_cache", "history", "working_state"])
        assert out["ok"] is False
        assert set(out["refused"]) == {"history", "working_state"}
        # …and the legitimate half is still done, so a mixed call is not a no-op
        assert out["removed"] == ["workspace_cache"]

    def test_a_path_outside_the_instance_is_refused(self, inst, tmp_path):
        victim = tmp_path / "elsewhere"
        victim.mkdir()
        (victim / "keep.txt").write_text("x", encoding="utf-8")
        for name in ("../elsewhere", "..", "/etc", "C:\\Windows"):
            out = idk.reclaim(inst, [name])
            assert out["ok"] is False and out["removed"] == [], name
        assert (victim / "keep.txt").is_file()

    def test_every_precious_name_is_outside_rebuildable(self):
        assert not (set(idk.PRECIOUS) & set(idk.REBUILDABLE))
        for name in ("history", "working_state", "journal"):
            assert name in idk.PRECIOUS
            assert name not in idk.REBUILDABLE

    def test_reclaim_frees_and_reports_what_it_freed(self, inst):
        out = idk.reclaim(inst, ["workspace_cache", "story_cache"])
        assert out["ok"] is True
        assert set(out["removed"]) == {"workspace_cache", "story_cache"}
        assert out["freed_bytes"] == 50 * 1024 * 1024
        assert not (inst / "workspace_cache").exists()
        assert (inst / "history").is_dir()


class TestTheVerdict:
    def test_warn_before_the_limit_not_at_it(self, inst):
        """Told at exactly 20.0 GB, a person has already been surprised."""
        total = idk.measure(inst, force=True)["bytes"]
        gb = total / (1024 ** 3)
        idk.write_settings(inst, limit_gb=gb / 0.5)      # 50% -> ok
        assert idk.status(inst, force=True)["level"] == "ok"
        idk.write_settings(inst, limit_gb=gb / 0.85)     # 85% -> warn
        assert idk.status(inst, force=True)["level"] == "warn"
        idk.write_settings(inst, limit_gb=gb / 1.2)      # past it -> over
        assert idk.status(inst, force=True)["level"] == "over"

    def test_the_default_limit_is_the_one_that_was_asked_for(self, inst):
        assert idk.DEFAULT_LIMIT_GB == 20.0
        assert idk.settings(inst)["limit_gb"] == 20.0

    def test_a_corrupt_settings_file_falls_back(self, inst):
        idk.config_path(inst).write_text("{not json", encoding="utf-8")
        assert idk.settings(inst)["limit_gb"] == idk.DEFAULT_LIMIT_GB
        idk.config_path(inst).write_text('{"limit_gb": "banana"}', encoding="utf-8")
        assert idk.settings(inst)["limit_gb"] == idk.DEFAULT_LIMIT_GB

    def test_muting_comes_back_when_it_grows(self, inst):
        """A dismissal must not be able to hide a problem that is still getting
        worse — that is the difference between 'not now' and 'never'."""
        total = idk.measure(inst, force=True)["bytes"]
        gb = total / (1024 ** 3)
        idk.write_settings(inst, limit_gb=gb / 1.2)
        assert idk.status(inst, force=True)["level"] == "over"
        idk.write_settings(inst, muted_until_gb=gb * 1.25)
        assert idk.status(inst, force=True)["level"] == "ok", "muted"
        _fill(inst / "history" / "chipA" / "grew.json", 120)
        assert idk.status(inst, force=True)["level"] == "over", (
            "it grew past the muted size and must speak again")


class TestTheRoutes:
    @pytest.fixture
    def client(self, tmp_path):
        state = {"qubits": {"q1": {"id": "q1"}}, "qubit_pairs": {}}
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(
            json.dumps({"wiring": {}, "network": {}}), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path)})
        c._inst = Path(app.instance_path)
        return c

    def test_status_and_a_quiet_banner(self, client):
        d = client.get("/disk/status").get_json()
        assert {"gb", "level", "limit_gb", "dirs", "reclaimable_bytes"} <= set(d)
        assert d["level"] == "ok"
        body = client.get("/disk/banner").get_data(as_text=True).strip()
        assert body == "", "a healthy instance renders nothing"

    def test_the_banner_appears_once_it_matters(self, client):
        _fill(client._inst / "workspace_cache" / "big.json", 120)
        client.post("/disk/settings", json={"limit_gb": 0.1})
        html = client.get("/disk/banner").get_data(as_text=True)
        assert "disk-guard-banner" in html
        assert "Clear" in html and "of cache" in html

    def test_reclaim_over_http_refuses_history(self, client):
        _fill(client._inst / "history" / "c" / "s.json", 2)
        r = client.post("/disk/reclaim", json={"names": ["history"]})
        assert r.status_code == 400
        assert r.get_json()["refused"] == ["history"]
        assert (client._inst / "history" / "c" / "s.json").is_file()

    def test_the_page_renders_both_kinds(self, client):
        _fill(client._inst / "workspace_cache" / "big.json", 60)
        html = client.get("/disk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "disk-row-cache" in html and "disk-row-keep" in html
        assert "rebuildable" in html
        assert "never deleted automatically" in html, (
            "the page must say what it will NOT do for you")


class TestOneSpellingOfASize:
    def test_the_client_and_the_templates_agree(self):
        root = Path(__file__).resolve().parent.parent
        js = (root / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
        assert "window.diskGuardSize" in js
        i = js.index("window.diskGuardSize")
        block = js[i:i + 400]
        assert "1073741824" in block and "1048576" in block
        # the confirm() and the toast both go through it -- they said "0.09 GB"
        # and "88 MB" for one number before
        after = js[i:]
        assert "diskGuardSize(d.reclaimable_bytes)" in after
        assert "diskGuardSize(out.freed_bytes)" in after
        assert "toFixed(2) + ' GB'" in block
        for tpl in ("_disk_guard_banner.html", "_disk_page.html"):
            t = (root / "quam_state_manager/web/templates" / tpl).read_text(encoding="utf-8")
            assert "macro sz(b)" in t, tpl
            assert "1073741824" in t and "1048576" in t, tpl
