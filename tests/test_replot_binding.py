"""Synthetic plotting modules pin saved-value identity and record cardinality."""
import ast
import sys
from types import ModuleType

import pytest

from quam_state_manager.generator import run_interactive_replot as runner


@pytest.fixture
def plots(monkeypatch):
    module = ModuleType("synthetic_plots")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec("""
from dataclasses import dataclass
from typing import Dict

@dataclass
class Fit:
    peak: float

def plot_record(fit: Fit):
    return fit.peak

def plot_plain(fit):
    return fit['peak']

def plot_mapping(fit: Dict[str, Fit]):
    return {key: value.peak for key, value in fit.items()}
""", module.__dict__)
    return module


def bind(fn, records, source="", **kwargs):
    report = {}
    tree = ast.parse(source) if source else None
    values = runner._bind_args(fn, ds_raw=None, ds_fit=None, qubits=[], params={},
        results={"fit_results": records}, node_tree=tree,
        plot_imports={fn.__name__: ("calibration_utils.synthetic", fn.__name__)},
        bind_report=report, **kwargs)
    return values, report


def test_single_dataclass_fit(plots):
    values, report = bind(plots.plot_record, {"element0": {"peak": 4}})
    assert isinstance(values['fit'], plots.Fit)
    assert plots.plot_record(**values) == 4
    assert report['fit']['records'][0]['selection'] == 'element0'


@pytest.mark.parametrize('selector', ['element.name', "'element1'"])
def test_subscripted_local_fit(plots, selector):
    records = {"element1": {"peak": 7}}
    if selector.startswith("'"):
        records = {'element0': {'peak': 4}, **records}
    values, report = bind(plots.plot_plain, records, f"""
def draw(node):
    fit = node.namespace['fit_results'][{selector}]
    plot_plain(fit)
""")
    assert values['fit'] == {'peak': 7}
    assert plots.plot_plain(**values) == 7
    assert report['fit']['records'][0]['selection'] == 'element1'


def test_ambiguous_single_fit_keeps_mapping_and_explains(plots):
    records = {'element0': {'peak': 4}, 'element1': {'peak': 7}}
    values, report = bind(plots.plot_record, records)
    assert values['fit'] is records
    assert report['fit'].get('records')
    assert 'multiple fit entries' in report['fit']['records'][0]['reason']


def test_mapping_hint_keeps_all_entries_even_with_subscript(plots):
    values, _ = bind(plots.plot_mapping, {'element0': {'peak': 4}},
                     "plot_mapping(node.namespace['fit_results'][element.name])")
    assert set(values['fit']) == {'element0'}
    assert plots.plot_mapping(**values) == {'element0': 4}


def test_whole_mapping_call_overrides_single_hint(plots):
    records = {'element0': {'peak': 4}}
    values, _ = bind(plots.plot_record, records, """
def draw(node):
    fit = node.results['fit_results']
    plot_record(fit)
""")
    assert values['fit'] is records


def test_call_aliases_resolve_within_own_function(plots):
    values, _ = bind(plots.plot_plain, {'element0': {'peak': 4}}, """
def analysis(node):
    fit = node.namespace['fit_results'][element.name]
def draw(node):
    plot_plain(fit)
""")
    assert values['fit'] == {'element0': {'peak': 4}}
