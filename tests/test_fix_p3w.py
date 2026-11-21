"""Fix P3W: the report's not-checked gate wording, the not-computable check's own reason, the
no-chip fold count weight and journal-line authors in the Calibration log's author filter."""
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from quam_state_manager.core import story
from quam_state_manager.core.autofit import families, gates
from quam_state_manager.web import journal_routes
from quam_state_manager.web.app import create_app
from tests.test_journal_search import search_fixture
from tests.test_story import world  # noqa: F401 -- synthetic run folders


def _run_card(run_id, gate):
    return {"kind": "run", "run_id": run_id, "time": "12:00:00", "author": "human:user-a", "node": "ramsey",
            "targets": ["q1"], "outcome": "ok", "gate": gate, "writes": [], "params_diff": [], "journal": []}


def _report(tmp_path, cards):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    day = {"day": "2026-10-04", "history": {"state": "ready"}, "cards": cards, "loose": [],
           "counts": {"runs": len(cards), "writes": 0, "write_cards": 0}}
    with app.test_request_context():
        return app.jinja_env.get_template("_report_calibration_log.html").render(days=[day], building=None,
                                                                                 gates_pending=0)


def test_report_row_says_gate_not_checked_with_the_card_wording(tmp_path):
    unknown = {"verdict": "unknown", "reason": "no autofit family for this node"}
    suspect = {"verdict": "suspect", "reason": "r2=0.1 violates [0.3, None] (noisy)"}
    html = _report(tmp_path, [_run_card(1, unknown), _run_card(2, suspect)])
    assert "outcome: ok; gate not checked: no autofit family for this node</p>" in html
    assert "gate: unknown" not in html
    # a real suspect verdict keeps its wording
    assert "outcome: ok; gate: suspect - r2=0.1 violates [0.3, None] (noisy)</p>" in html
    # the run card says the same words for the same gate (one shared macro)
    app = create_app(testing=True, instance_path=str(tmp_path / "instance2"))
    with app.test_request_context():
        card = app.jinja_env.get_template("_journal_card.html").module.card(_run_card(1, unknown), "2026-10-04")
    assert '<p class="jr-gate-reason muted">gate not checked: no autofit family for this node</p>' in card
    assert 'title="gate not checked: no autofit family for this node"' in card


def test_not_checked_line_names_the_not_computable_check_not_an_ok_detail(world, monkeypatch):  # noqa: F811
    run = world["ds"].get_run(104)
    import h5py
    with h5py.File(Path(run["folder_path"]) / "ds_raw.h5", "w"):
        pass
    family = replace(families.family_for("ramsey_vs_flux"), plausibility=[], metric_gates=[], consistency_checks=[])
    missing = "var 'flux_bias' missing in ds_raw"
    monkeypatch.setattr(families, "family_for", lambda name: family)
    monkeypatch.setattr(gates, "node_targets", lambda run, fam: ["q4"])
    # the extremum check cannot be computed; the feature check that runs after it is ok
    monkeypatch.setattr(gates, "_extremum_inside_sweep", lambda *a, **k: ("not_evaluated", missing))
    monkeypatch.setattr(gates, "_feature_check", lambda *a, **k: ("ok", "feature span z=12.0 (ok detail)"))
    gate = story.gate_for_run(world["inst"], run, folder_key="p3w-family")
    verdict = gate["per_target"]["q4"]
    assert verdict["reasons"][0] == "feature span z=12.0 (ok detail)"   # the trap reasons[0] fell into
    assert gate["verdict"] == "unknown"
    assert gate["reason"] == missing


def test_suspect_reason_is_unchanged(world, monkeypatch):  # noqa: F811
    run = world["ds"].get_run(104)
    monkeypatch.setattr(families, "family_for", lambda name: SimpleNamespace(key="sample"))
    monkeypatch.setattr(gates, "node_targets", lambda run, fam: ["q4"])
    monkeypatch.setattr(gates, "evaluate_target", lambda *a, **k: gates.GateVerdict(
        target="q4", verdict="suspect", failure_mode="noisy", reasons=["first", "second"],
        checks={"G2_metrics": "suspect"}, failure_reason="second"))
    gate = story.gate_for_run(world["inst"], run, folder_key="p3w-suspect")
    assert gate["verdict"] == "suspect" and gate["reason"] == "first"


def test_no_chip_fold_count_carries_no_bold_override():
    html = search_fixture()["html"]
    fold = re.search(r'<details class="ui-disclosure jr-unassigned jr-line-group"[^>]*>.*?</summary>', html, re.S)
    assert fold
    summary = fold.group()
    assert '<span class="jr-line-count"' in summary
    assert not re.search(r"<(b|strong)[ >]", summary) and "font-weight" not in summary


def test_author_options_include_journal_line_authors_and_reach_their_lines():
    html = search_fixture()["html"]
    select = re.search(r'<select name="author" id="jr-author".*?</select>', html, re.S).group()
    assert re.findall(r'<option value="([^"]*)"', select) == ["", "agent", "hook", "human", "human:user-a"]
    assert 'data-jr-line-authors="agent human"' in html
    # paged days: the server's filter reaches a card through a line it holds
    f = {"author": "hook", "q": ""}
    card = {"kind": "run", "author": "human:user-a", "journal": [{"kind": "hook"}], "search_hay": ""}
    assert journal_routes._matches(card, f, grps=[])
    assert not journal_routes._matches(dict(card, journal=[{"kind": "agent"}]), f, grps=[])
    data = {"cards": [card], "loose": [{"kind": "human"}], "unassigned": [{"kind": "hook"}, {"kind": "hook"}]}
    assert journal_routes._day_authors(data) == ["hook", "human", "human:user-a"]


def test_shared_macro_is_the_only_not_checked_wording():
    root = Path(__file__).resolve().parent.parent / "quam_state_manager/web/templates"
    for name in ("_journal_card.html", "_report_calibration_log.html"):
        text = (root / name).read_text(encoding="utf-8")
        assert "gate_not_checked(" in text
        assert "gate not checked:" not in text and "gate: unknown" not in text
