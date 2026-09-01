r"""A test run writes nothing into the developer's own SM state (docs/155 F7).

``create_app()`` with no ``instance_path`` falls back to
``default_instance_path()``, which returns ``None`` in a repo checkout so that
Flask derives the familiar ``<repo>/instance``. That is right for the app and
wrong for a test: a couple of dozen call sites across the suite build an app
that way, and every working copy, session file and history sidecar they write
lands in the directory the developer's own SM is using.

Measured before the fix, on six of those files alone: **33 stray working
copies created, and three files REWRITTEN** — ``last_session.json``,
``workspace_roots.json`` (the developer's configured workspace roots, replaced
by a test's tmp paths) and docs/139's ``history/_fingerprints.json``. The
directory is gitignored, so nothing reached a commit and nothing failed
loudly; it just quietly replaced state a person was relying on.

``tests/conftest.py::_isolate_instance_dir`` redirects the default into
pytest's own tmp tree. These pins hold that, and hold its escape hatch open:
the handful of tests that assert on the instance-path POLICY itself must keep
seeing the real function.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from quam_state_manager.web import app as app_mod

_REPO = Path(__file__).resolve().parent.parent
_REPO_INSTANCE = _REPO / "instance"


def _snapshot(d: Path) -> dict[str, tuple[int, int]]:
    """Every file under *d* as (size, mtime_ns) — cheaper than hashing and
    enough to catch a rewrite, since every writer here goes through
    ``safe_io``'s atomic replace."""
    out: dict[str, tuple[int, int]] = {}
    for root, _dirs, files in os.walk(d):
        for f in files:
            p = Path(root) / f
            try:
                st = p.stat()
            except OSError:
                continue
            out[str(p.relative_to(d))] = (st.st_size, st.st_mtime_ns)
    return out


class TestNothingLandsInTheDevelopersInstanceDir:

    def test_an_app_built_without_an_instance_path_lands_in_pytest_tmp(
            self, tmp_path_factory):
        app = app_mod.create_app()
        inst = Path(app.instance_path).resolve()

        assert inst != _REPO_INSTANCE.resolve(), (
            "create_app() with no instance_path landed in the repo's own "
            f"instance/ ({inst}) — a test run is writing the developer's "
            f"working copies, session file and history sidecars"
        )
        assert inst.is_relative_to(
            Path(tmp_path_factory.getbasetemp()).resolve().parent), (
            f"the app's instance dir ({inst}) is outside pytest's tmp tree, "
            f"so nothing ever cleans it up"
        )

    def test_the_testing_shortcut_also_stays_inside_pytest_tmp(
            self, tmp_path_factory):
        """``create_app(testing=True)`` takes an earlier branch that never
        consults the default: it mkdtemps its own. That is isolated from the
        developer but from pytest's cleanup too — 327 of those directories had
        accumulated in %TEMP% on the machine this was written on."""
        app = app_mod.create_app(testing=True)
        inst = Path(app.instance_path).resolve()

        assert inst.is_relative_to(
            Path(tmp_path_factory.getbasetemp()).resolve().parent), (
            f"testing=True allocated {inst}, outside pytest's tmp tree — it "
            f"will still be there after the run, and after every run"
        )
        assert not inst.name.startswith("quam_test_instance_"), (
            "the raw tempfile.mkdtemp path is being used; nothing removes it"
        )

    def test_a_real_chip_load_leaves_the_repo_instance_dir_untouched(self,
                                                                     tmp_path):
        """The property itself, end to end: the thing that actually wrote
        those 36 files was an ordinary ``POST /load``."""
        if not _REPO_INSTANCE.is_dir():
            pytest.skip("no repo instance/ dir to protect on this checkout")
        live = tmp_path / "chip"
        live.mkdir()
        (live / "state.json").write_text(
            '{"qubits": {"q1": {"T1": 1e-05}}}', encoding="utf-8")
        (live / "wiring.json").write_text(
            '{"network": {"host": "1.2.3.4"}}', encoding="utf-8")

        before = _snapshot(_REPO_INSTANCE)
        app = app_mod.create_app()
        client = app.test_client()
        # A non-testing app enforces the same-origin CSRF check, so the POST
        # carries the header a browser would send.
        assert client.post("/load", data={"folder": str(live)},
                           headers={"Origin": "http://localhost"}
                           ).status_code < 400
        after = _snapshot(_REPO_INSTANCE)

        added = sorted(set(after) - set(before))
        changed = sorted(k for k in set(before) & set(after)
                         if before[k] != after[k])
        assert not added and not changed, (
            f"one chip load disturbed the developer's instance/: "
            f"{len(added)} added {added[:4]}, {len(changed)} rewritten "
            f"{changed[:4]}"
        )

    def test_mkdtemp_is_redirected_only_for_sms_own_prefix(self):
        """The redirect is keyed on SM's own prefix string, so it cannot
        quietly capture some other library's temp directory."""
        other = tempfile.mkdtemp(prefix="something_else_")
        try:
            assert not Path(other).name.startswith("sm_testing_instance")
        finally:
            os.rmdir(other)


@pytest.mark.real_instance_path
class TestTheOptOutStillWorks:
    """The escape hatch has to stay open, or the tests that pin the
    instance-path POLICY would be pinning the fixture instead."""

    def test_a_marked_test_sees_the_real_function(self):
        assert app_mod.default_instance_path() is None, (
            "a test marked real_instance_path did not get the real "
            "default_instance_path — the opt-out has stopped working, and "
            "every policy pin in test_pip_install is now measuring the "
            "isolation fixture"
        )

    def test_a_marked_test_gets_the_unpatched_tempfile_too(self):
        """The other half of the opt-out. Deliberately NOT asserted by
        building an app: under this marker that app would point at the
        developer's real instance/ and run the startup migrations and the
        leftover purge against it, which is the very thing this file exists
        to stop."""
        d = tempfile.mkdtemp(prefix="quam_test_instance_")
        try:
            assert Path(d).name.startswith("quam_test_instance_"), (
                "the mkdtemp redirect is still installed inside an opted-out "
                "test — the marker only half releases the isolation"
            )
        finally:
            os.rmdir(d)


class TestThePurgeClearsWhatTheUnisolatedYearsLeft:
    """``_purge_test_leftovers`` already dropped leaked history dirs and tmp
    paths out of ``workspace_roots.json`` / ``last_session.json``. It left the
    two directories that had actually filled up: on the machine docs/155 F7
    was written on, ALL 96 working copies and ALL 105 cached listings in the
    developer's instance/ were test leftovers, from pytest runs spanning
    months.

    The interesting half is what it must NOT delete."""

    def _inst(self, tmp_path):
        inst = tmp_path / "inst"
        (inst / "working_state").mkdir(parents=True)
        (inst / "workspace_cache").mkdir(parents=True)
        return inst

    def _copy(self, inst, name, live):
        (inst / "working_state" / name).mkdir()
        (inst / "working_state" / f"{name}/state.json").write_text(
            "{}", encoding="utf-8")
        (inst / "working_state" / f"{name}.meta.json").write_text(
            json.dumps({"key": name, "live_folder": str(live)}),
            encoding="utf-8")

    def _cache(self, inst, name, root):
        (inst / "workspace_cache" / name).write_text(
            json.dumps({"v": 1, "root": str(root), "entries": []}),
            encoding="utf-8")

    def test_a_working_copy_of_a_vanished_tmp_chip_goes(self, tmp_path):
        inst = self._inst(tmp_path)
        gone = Path(tempfile.gettempdir()) / "pytest-of-nobody" / "pytest-1" / "chip"
        self._copy(inst, "data-dead", gone)

        app_mod._purge_test_leftovers(str(inst))

        assert not (inst / "working_state" / "data-dead").exists()
        assert not (inst / "working_state" / "data-dead.meta.json").exists()

    def test_a_tmp_copy_whose_chip_is_still_there_is_kept(self, tmp_path,
                                                          tmp_path_factory):
        """Two signals are required. A tmp dir that still exists may belong to
        a run happening right now."""
        inst = self._inst(tmp_path)
        alive = tmp_path_factory.mktemp("still_here")
        self._copy(inst, "data-live", alive)

        app_mod._purge_test_leftovers(str(inst))

        assert (inst / "working_state" / "data-live.meta.json").exists(), (
            "a working copy whose chip folder is still on disk was deleted — "
            "one signal is not enough"
        )

    def test_a_real_chip_that_is_merely_MISSING_is_never_touched(self,
                                                                 tmp_path):
        """The failure this guards: a working copy can hold edits that were
        never applied to live, and a real chip folder can be absent for a
        hundred innocent reasons — renamed, moved, on a drive that is not
        mounted this morning. Absence alone must never be enough. Being under
        the system tempdir is what separates litter from a chip.

        The path here is a PLAIN absent path, deliberately: an unreachable
        UNC is covered by the test below, and it survives for a different
        reason, which is exactly how the first version of this pin came to
        prove nothing at all.
        """
        inst = self._inst(tmp_path)
        self._copy(inst, "data-real", Path(r"C:/lab/results/chip_A"))

        app_mod._purge_test_leftovers(str(inst))

        assert (inst / "working_state" / "data-real.meta.json").exists(), (
            "a working copy of a REAL chip folder was deleted as test litter "
            "just because the folder is not there today — unapplied edits, "
            "gone, with nothing under $TEMP anywhere in sight"
        )
        assert (inst / "working_state" / "data-real" / "state.json").exists()

    def test_a_tmp_path_that_cannot_be_ANSWERED_is_kept(self, tmp_path,
                                                        tmp_path_factory,
                                                        monkeypatch):
        """"I could not find out" is not "it is not there".

        This is the narrow case the OSError branch actually covers, and
        getting to it took three drafts, each corrected by a mutation rather
        than by re-reading the assertion:

        1. A real UNC share aimed at the $TEMP rule. It passed with that rule
           deleted — the copy never reached the rule, because asking about a
           dead UNC host RAISES on Windows and the OSError branch caught it
           first.
        2. A ``.invalid`` host, which answers False politely. That stopped
           testing this guard at all, and the guard's own mutation went green.
        3. This one. The $TEMP test comes FIRST, so a share path never reaches
           the existence check at all — an unreachable SHARE is kept by the
           $TEMP rule, which the pin above covers. What reaches here is a path
           under $TEMP that the filesystem will not answer for, and keeping it
           is the conservative choice: a working copy can hold edits nobody
           has applied.

        The raise is simulated, not aimed at a real host: a pin whose
        mechanism depends on the local network proves something different in
        every lab.
        """
        inst = self._inst(tmp_path)
        unanswerable_dir = tmp_path_factory.mktemp("cannot_ask")
        self._copy(inst, "data-unknown", unanswerable_dir)
        real_exists = Path.exists

        def unanswerable(self, *a, **k):
            if "cannot_ask" in str(self):
                raise OSError(53, "The network path was not found")
            return real_exists(self, *a, **k)

        monkeypatch.setattr(Path, "exists", unanswerable)

        app_mod._purge_test_leftovers(str(inst))

        assert real_exists(inst / "working_state" / "data-unknown.meta.json"), (
            "a working copy was deleted because the filesystem REFUSED to say "
            "whether its chip is there — unapplied edits, thrown away on an "
            "error message"
        )
        assert real_exists(
            inst / "working_state" / "data-unknown" / "state.json")

    def test_a_cached_listing_of_a_tmp_root_goes(self, tmp_path):
        inst = self._inst(tmp_path)
        self._cache(inst, "ws_dead.json",
                    Path(tempfile.gettempdir()) / "pytest-of-nobody" / "d")

        app_mod._purge_test_leftovers(str(inst))

        assert not (inst / "workspace_cache" / "ws_dead.json").exists()

    def test_a_cached_listing_of_a_real_root_stays(self, tmp_path):
        # NOT ``tmp_path`` for the root: pytest's own tmp dir lives UNDER
        # $TEMP, so it cannot stand in for a real path in a rule whose whole
        # test IS $TEMP membership. The first draft of this pin used it and
        # failed for exactly the reason the code is right.
        inst = self._inst(tmp_path)
        self._cache(inst, "ws_real.json", Path(r"C:/lab/results/chip_A"))

        app_mod._purge_test_leftovers(str(inst))

        assert (inst / "workspace_cache" / "ws_real.json").exists(), (
            "a real workspace's cached listing was deleted — the next open "
            "pays the full walk docs/142 built this cache to avoid"
        )

    def test_garbage_sidecars_are_left_alone_and_raise_nothing(self, tmp_path):
        inst = self._inst(tmp_path)
        (inst / "working_state" / "data-junk.meta.json").write_text(
            "{not json", encoding="utf-8")
        (inst / "workspace_cache" / "ws_junk.json").write_text(
            "", encoding="utf-8")

        app_mod._purge_test_leftovers(str(inst))          # must not raise

        assert (inst / "working_state" / "data-junk.meta.json").exists()
        assert (inst / "workspace_cache" / "ws_junk.json").exists()
