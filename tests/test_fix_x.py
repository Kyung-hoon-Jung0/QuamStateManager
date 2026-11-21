"""Fix X: every open that arms Auto-Sync says so and offers the quiet open (landing Resume, project card),
a suspect gate leads with its suspect reason, 2Q node ids take the one node-name table, and the Datasets
metric / empty-outcome cells (ds_fix_x_selfcheck.cjs)."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import story
from quam_state_manager.core.autofit import families, gates
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app
from tests.test_project_scope import scoped  # noqa: F401 -- tmp QUAlibrate config tree + chips
from tests.test_story import world  # noqa: F401 -- synthetic run folders

pytestmark = pytest.mark.usefixtures("any_project_env_chosen")

_NOTICE = "data-resume-arms-auto-sync"
_QUIET = 'name="auto_sync" value="0"'


@pytest.fixture
def arming(monkeypatch):
    monkeypatch.setenv("SM_AUTO_SYNC_ON_OPEN_DEFAULT", "1")


def _armed(app) -> bool:
    with app.app_context():
        return bool(routes_mod._auto_sync_state())


def _restart_landing(sc):
    """A restart with no chip open: the landing offers to resume chip_a."""
    with routes_mod._quam_cache_lock:
        routes_mod._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(sc["inst"]))
    sc["app"], sc["client"] = app, app.test_client()
    # after create_app: its startup hygiene drops a last path under the OS temp dir
    (sc["inst"] / "last_session.json").write_text(
        json.dumps({"last_quam_state_path": str(sc["chip_a"])}), encoding="utf-8")
    body = sc["client"].get("/").get_data(as_text=True)
    form = re.search(r'<form[^>]*class="landing-resume-form">.*?</form>', body, re.S)
    assert form, "the landing offers Resume"
    return body, form.group()


def _card_form(body, project):
    for m in re.finditer(r'<form[^>]*class="landing-card-open">.*?</form>', body, re.S):
        if f'name="project" value="{project}"' in m.group():
            return m.group()
    raise AssertionError(f"no card form for {project}")


def _files(folder):
    return {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}


# ---------------------------------------------------------------- X-1 landing Resume

def test_landing_resume_that_arms_says_so_and_offers_the_quiet_open(scoped, arming):  # noqa: F811
    _body, form = _restart_landing(scoped)
    assert _NOTICE in form
    assert re.search(r'<button type="submit" ' + re.escape(_QUIET) + r'[^>]*>Re-open without Auto-Sync<', form)
    before = _files(scoped["chip_a"])
    r = scoped["client"].post("/load", data={"folder": str(scoped["chip_a"]), "auto_sync": "0"})
    assert r.status_code in (200, 302)
    assert not _armed(scoped["app"])
    assert before == _files(scoped["chip_a"])


def test_landing_default_resume_still_arms(scoped, arming):  # noqa: F811
    _restart_landing(scoped)
    r = scoped["client"].post("/load", data={"folder": str(scoped["chip_a"])})
    assert r.status_code in (200, 302)
    assert _armed(scoped["app"])


def test_landing_resume_that_does_not_arm_offers_neither(scoped, arming):  # noqa: F811
    scoped["client"].post("/settings/auto-sync-on-open", json={"enabled": False})
    _body, form = _restart_landing(scoped)
    assert _NOTICE not in form and _QUIET not in form and "without Auto-Sync" not in form


# ---------------------------------------------------------------- X-1 project card (/qualibrate/open)

def test_project_card_that_arms_offers_the_quiet_open_and_it_is_honoured(scoped, arming):  # noqa: F811
    _restart_landing(scoped)
    card = _card_form(scoped["client"].get("/landing/projects").get_data(as_text=True), "alpha")
    assert _NOTICE in card and _QUIET in card and "Without Auto-Sync" in card
    before = _files(scoped["chip_a"])
    r = scoped["client"].post("/qualibrate/open", data={"project": "alpha", "from": "landing", "auto_sync": "0"})
    assert r.status_code in (200, 302)
    assert not _armed(scoped["app"])
    assert before == _files(scoped["chip_a"])
    r = scoped["client"].post("/qualibrate/open", data={"project": "alpha", "from": "landing"})
    assert r.status_code in (200, 302)
    assert _armed(scoped["app"])


def test_project_card_that_does_not_arm_offers_neither(scoped, arming):  # noqa: F811
    scoped["client"].post("/settings/auto-sync-on-open", json={"enabled": False})
    _restart_landing(scoped)
    card = _card_form(scoped["client"].get("/landing/projects").get_data(as_text=True), "alpha")
    assert _NOTICE not in card and _QUIET not in card


# ---------------------------------------------------------------- X-2 suspect reason first

def _gate(world, monkeypatch, verdict, key):  # noqa: F811
    run = world["ds"].get_run(104)
    monkeypatch.setattr(families, "family_for", lambda name: SimpleNamespace(key="sample"))
    monkeypatch.setattr(gates, "node_targets", lambda run, fam: ["q4"])
    monkeypatch.setattr(gates, "evaluate_target", lambda *a, **k: verdict)
    return story.gate_for_run(world["inst"], run, folder_key=key)


def test_suspect_leads_with_its_suspect_reason_not_a_passing_detail(world, monkeypatch):  # noqa: F811
    passing = "G3 signal present: contrast z=14.2 (ok)"
    suspect = "r2=0.1 violates [0.3, None] (noisy)"
    gate = _gate(world, monkeypatch, gates.GateVerdict(
        target="q4", verdict="suspect", failure_mode="noisy", reasons=[passing, suspect],
        checks={"G3_signal": "ok", "G2_metrics": "suspect"}, failure_reason=suspect), "x2-suspect")
    assert gate["verdict"] == "suspect" and gate["reason"] == suspect


def test_pass_verdict_text_is_unchanged(world, monkeypatch):  # noqa: F811
    gate = _gate(world, monkeypatch, gates.GateVerdict(
        target="q4", verdict="pass", failure_mode=None, reasons=["first", "second"],
        checks={"G3_signal": "ok"}), "x2-pass")
    assert gate["verdict"] == "pass" and gate["reason"] == "first"


# ---------------------------------------------------------------- X-7 2Q labels

@pytest.mark.parametrize("node, label", [
    ("31e_CR_correction_phase", "CR phase"),
    ("50a_CNOT_zaxis_fidelity", "CNOT fidelity"),
    ("51a_2Q_confusion_matrix", "2Q confusion"),
    ("51b_Bell_state_tomography", "Bell state"),
    ("32a_CR_drive_amp_erroramplification", "CR drive amp"),
])
def test_two_qubit_node_ids_get_their_label(node, label):
    assert story._family_label(node) == (None, label)


@pytest.mark.parametrize("node", ["99_mystery_node", "scan"])
def test_an_unknown_node_id_stays_raw(node):
    assert story._family_label(node) == (None, node)


# ---------------------------------------------------------------- X-3 / X-4 Datasets cells

def test_metric_title_and_empty_outcome_status():
    result = subprocess.run(["node", str(Path(__file__).with_name("ds_fix_x_selfcheck.cjs"))],
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "11 checks passed" in result.stdout
