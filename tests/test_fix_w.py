"""Fix W: folded journal evidence and honest run author/gate displays."""
import re
from dataclasses import replace
from types import SimpleNamespace

import pytest

from quam_state_manager.core import story
from quam_state_manager.core.autofit import families, gates
from quam_state_manager.web.app import create_app
from tests.test_journal_search import search_fixture
from tests.test_story import world  # noqa: F401 -- synthetic run folders


def test_no_chip_lines_are_closed_after_timeline_and_cards():
    html = search_fixture()["html"]
    fold = re.search(r'<details class="ui-disclosure jr-unassigned jr-line-group"[^>]*>.*?</details>', html, re.S)
    assert fold and ' open' not in fold.group().split('>', 1)[0]
    assert html.index('class="jr-cards"') < fold.start()
    assert html.index('class="jr-section jr-digest') < fold.start()
    summary = fold.group().split('</summary>')[0]
    assert "8 journal lines from a session that named no chip." in summary
    assert "Move them under chipX" in summary
    assert re.search(r'data-jr-search="[^"]*tailonly line', fold.group())
    assert 'data-jr-author="hook"' in fold.group()


def test_zero_no_chip_lines_renders_no_fold(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    html = app.test_client().get('/journal/day?day=2026-10-04').get_data(as_text=True)
    assert 'jr-unassigned jr-line-group' not in html


@pytest.mark.parametrize("author", ["unknown", None, "agent"])
def test_run_author_badge_is_blank_when_unrecorded(tmp_path, author):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    card = dict(kind="run", run_id=1, author=author, targets=[], certainty="none")
    with app.test_request_context():
        html = app.jinja_env.get_template('_journal_card.html').module.card(card, '2026-10-04', True)
    badges = re.findall(r'<span class="jr-author [^"]+"[^>]*>(.*?)</span>', html, re.S)
    assert badges == (["agent"] if author == "agent" else [])


@pytest.mark.parametrize("case", ["missing", "exception", "suspect", "mixed"])
def test_gate_reader_distinguishes_an_uncomputed_check(world, monkeypatch, case):  # noqa: F811
    run = world["ds"].get_run(104)
    family = SimpleNamespace(key="sample")
    original = gates.evaluate_target
    if case == 'missing':
        import h5py
        from pathlib import Path
        with h5py.File(Path(run['folder_path']) / 'ds_raw.h5', 'w'):
            pass
        family = replace(families.family_for('05_power_rabi'), plausibility=[], metric_gates=[],
                         feature_check=families.FeatureCheck(var='IQ_abs', axis_var='sample'))
    monkeypatch.setattr(families, 'family_for', lambda name: family)
    monkeypatch.setattr(gates, 'node_targets', lambda run, fam: ['q4'])
    reason = "var 'IQ_abs' or coord 'qubit' missing in ds_raw" if case == 'missing' else 'sample reason'

    def evaluate(*args, **kwargs):
        if case == 'missing':
            return original(*args, **kwargs)
        if case == 'exception':
            raise ValueError(reason)
        return gates.GateVerdict(target='q4', verdict='suspect',
                                 failure_mode='noisy' if case == 'suspect' else 'unverifiable', reasons=[reason],
                                 checks={'G2_metrics': 'suspect'} if case == 'mixed' else {})

    monkeypatch.setattr(gates, 'evaluate_target', evaluate)
    gate = story.gate_for_run(world['inst'], run, folder_key='sample')
    assert gate['verdict'] == ('suspect' if case in ('suspect', 'mixed') else 'unknown')
    assert reason in gate['reason']
    app = create_app(testing=True, instance_path=str(world['inst'] / 'app'))
    with app.test_request_context():
        card = dict(kind='run', run_id=104, targets=[], gate=gate)
        html = app.jinja_env.get_template('_journal_card.html').module.card(card, '2026-10-04')
        assert ('gate suspect' if case in ('suspect', 'mixed') else 'gate not checked') in html
        if case not in ('suspect', 'mixed'):
            assert 'jr-gate-unknown' in html and 'jr-gate-suspect' not in html


def test_gate_reader_handles_a_checker_exception(tmp_path):
    def broken(run):
        raise ValueError('sample exception')
    gate = story.gate_for_run(tmp_path, {'folder_path': str(tmp_path)}, folder_key='sample', compute=broken)
    assert gate['verdict'] == 'unknown' and 'sample exception' in gate['reason']
