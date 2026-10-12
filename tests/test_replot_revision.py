"""Name-free pins for the replay revision ladder (current -> run-time -> saved)."""
import json
import os
import shutil
import subprocess

import pytest

from quam_state_manager.core.interactive_plots import replot_revision as ladder
from quam_state_manager.generator.iplot_sources import fingerprint


def _ok(src, root=""):
    return {"figures": [{"key": "fit", "axes": []}], "errors": [], "source_root": root,
            "module_files": [{"path": str(src)}], "module_trees": [],
            "code_fingerprint": fingerprint([src]), "archive_match": {"missing": []}}


def _failed(src, root=""):
    out = _ok(src, root)
    out.update(figures=[], errors=[{"stage": "plot:fit", "trace": "Traceback\nKeyError: 'amp'"}])
    return out


def _run(tmp_path, start="2026-02-01T00:00:00+00:00"):
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "node.json").write_text(json.dumps({"created_at": start}))
    (folder / "figures.fit.png").write_bytes(b"png")
    return folder


def _git(root, *args, date=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True, env=env)


def test_current_code_rung_is_labelled_and_watched(tmp_path):
    src = tmp_path / "plotting.py"
    src.write_text("x=1")
    calls = []
    out = ladder.reproduce(_run(tmp_path), tmp_path / "inst", None,
                           lambda root: calls.append(root) or _ok(src))
    assert calls == [None]
    assert out["revision"]["rung"] == "current" and out["revision"]["heuristic"] is False
    assert out["status_chip"] == "Reproduced with the current analysis code"
    assert out["figures"][0]["status_chip"] == out["status_chip"]
    assert out["cache_watch"]["files"] == [str(src)]


def test_non_git_tree_skips_the_git_rung_and_shows_the_saved_figure(tmp_path):
    src = tmp_path / "lab" / "plotting.py"
    src.parent.mkdir()
    src.write_text("x=1")
    calls = []
    out = ladder.reproduce(_run(tmp_path), tmp_path / "inst", None,
                           lambda root: calls.append(root) or _failed(src, str(src.parent)))
    assert len(calls) == 1
    rungs = [(a["rung"], a["status"]) for a in out["revision_ladder"]]
    assert rungs == [("current", "failed"), ("run_time", "skipped"), ("saved", "shown")]
    assert out["revision_ladder"][1]["reasons"] == ["Analysis source tree has no git history"]
    assert out["revision"]["rung"] == "saved" and out["figures"] == []
    assert [f["key"] for f in out["saved_figures"]] == ["fit"]
    assert out["status_chip"].startswith("Could not reproduce") and "KeyError: 'amp'" in out["status_chip"]
    assert out["cache_watch"]["files"] == [str(src)]


def test_environment_failure_does_not_try_an_older_revision(tmp_path):
    src = tmp_path / "plotting.py"
    src.write_text("x=1")
    timeout = {"figures": [], "errors": [{"stage": "timeout", "trace": "exceeded 180s"}]}
    calls = []
    out = ladder.reproduce(_run(tmp_path), tmp_path / "inst", str(tmp_path),
                           lambda root: calls.append(root) or timeout)
    assert len(calls) == 1
    assert out["revision_ladder"][1]["reasons"] == ["The lab environment did not run (timeout)"]


def test_marker_defaults_trigger_the_ladder(tmp_path):
    src = tmp_path / "plotting.py"
    src.write_text("x=1")
    current = _ok(src)
    current["figures"][0]["marker_defaults"] = ["center"]
    out = ladder.reproduce(_run(tmp_path), tmp_path / "inst", None, lambda root: current)
    assert out["revision_ladder"][0]["reasons"] == ["Defaulted fields feed markers in fit: center"]
    assert out["revision"]["rung"] == "saved"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_run_time_revision_is_chosen_by_the_runs_time(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    src = lab / "plotting.py"
    _git(lab, "init", "-q")
    src.write_text("version = 'old'\n")
    _git(lab, "add", ".")
    _git(lab, "commit", "-q", "-m", "old", date="2026-01-01T00:00:00+00:00")
    old_sha = subprocess.run(["git", "-C", str(lab), "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
    src.write_text("version = 'new'\n")
    _git(lab, "commit", "-q", "-am", "new", date="2026-03-01T00:00:00+00:00")
    calls = []

    def invoke(root):
        calls.append(root)
        if root is None:
            return _failed(src, str(lab))
        pinned = os.path.join(root, "plotting.py")
        assert open(pinned).read() == "version = 'old'\n"
        return _ok(pinned, root)

    out = ladder.reproduce(_run(tmp_path), tmp_path / "inst", None, invoke)
    assert len(calls) == 2 and calls[1].startswith(str(tmp_path / "inst"))
    assert out["revision"] == {"rung": "run_time", "sha": old_sha, "heuristic": True,
                               "code_fingerprint": out["code_fingerprint"]}
    assert out["status_chip"] == (f"Reproduced with analysis code {old_sha[:7]} "
                                  "chosen by the run's time (heuristic)")
    # the cache watches the CURRENT code, not the pinned copy
    assert out["cache_watch"]["files"] == [str(src)]
    # a run newer than every commit: the run-time revision is the current code
    later = tmp_path / "later"
    later.mkdir()
    folder = _run(later, start="2026-06-01T00:00:00+00:00")
    out = ladder.reproduce(folder, tmp_path / "inst", None, lambda root: _failed(src, str(lab)))
    assert out["revision_ladder"][1]["reasons"] == ["The run-time revision is the current code"]


def test_replot_run_labels_and_caches_the_ladder_outcome(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from quam_state_manager.core.interactive_plots import replot
    src = tmp_path / "plotting.py"
    src.write_text("x=1")
    folder = _run(tmp_path)
    calls = []
    monkeypatch.setattr("quam_state_manager.core.config_generator.get_selected_env", lambda _: "env")
    monkeypatch.setattr(replot, "_invoke", lambda *a: calls.append(a) or _failed(src, str(tmp_path)))
    replot._CACHE.clear()
    run = SimpleNamespace(folder_path=str(folder))
    out = replot.replot_run(run, tmp_path / "inst")
    assert out["revision"]["rung"] == "saved" and out["saved_figures"]
    replot._CACHE.clear()
    assert replot.replot_run(run, tmp_path / "inst")["cache_key"] == out["cache_key"]
    assert len(calls) == 1
    assert replot.replot_outcome(out)["status_chip"] == out["status_chip"]


def test_warm_plot_worker_is_reused_only_for_the_same_code(tmp_path, monkeypatch):
    from quam_state_manager.core import lab_worker
    from quam_state_manager.core.interactive_plots import replot
    env = tmp_path / "python.exe"
    env.write_text("")
    tree = tmp_path / "pkg"
    tree.mkdir()
    (tree / "a.py").write_text("")
    spawned, killed = [], []

    class Fake:
        def __init__(self, python_path, script, marker, context=""):
            self.python_path, self.context = python_path, context
            spawned.append(self)

        def fresh(self):
            return True

        def ask(self, spec, timeout):
            return {"figures": [], "module_files": [{"path": str(tree / "a.py")}],
                    "module_trees": [str(tree)]}

        def kill(self):
            killed.append(self)

    monkeypatch.setattr(lab_worker, "Worker", Fake)
    monkeypatch.setattr(lab_worker, "retire_except", lambda *a: None)
    monkeypatch.setattr(replot, "_PLOT_WORKER", None)
    replot._warm_request(str(env), tmp_path, None, 10)
    replot._warm_request(str(env), tmp_path, None, 10)
    assert len(spawned) == 1 and killed == []
    replot._warm_request(str(env), tmp_path, str(tmp_path), 10)
    assert len(spawned) == 2 and killed == spawned[:1]
    (tree / "added.py").write_text("")
    replot._warm_request(str(env), tmp_path, str(tmp_path), 10)
    assert len(spawned) == 3 and killed == spawned[:2]
