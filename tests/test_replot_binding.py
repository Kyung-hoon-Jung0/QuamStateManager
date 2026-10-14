"""Synthetic plotting modules pin saved-value identity and record cardinality."""
import ast
import json
import sys
from types import ModuleType, SimpleNamespace

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

def plot_saved(ds_check):
    return ds_check

def plot_processed(ds_off, ds_on):
    return ds_off, ds_on

def plot_cell(cell):
    return cell

def plot_summary(summary):
    return summary

def plot_limits(parameters=None):
    return parameters.limit

def plot_bare(fit_results: Dict):
    return fit_results

def plot_builtin(fit_results: dict):
    return fit_results

def plot_decision(summary=None):
    return summary.cell if summary.propose else summary.get('fallback', -1)

def plot_uncertain(fit: Fit):
    import matplotlib.pyplot as plt
    import numpy as np
    fig, ax = plt.subplots()
    ax.errorbar([0, 1, 2], [fit.peak, fit.peak + 1, fit.peak + 2],
                yerr=[0.2, np.nan, 0.4], fmt='o', capsize=3)
    return fig
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


def test_saved_dataset_alias_is_the_same_object(plots):
    saved = object()
    try:
        values, report = bind(plots.plot_saved, {}, """
def draw(node):
    ds_check = node.results['ds_measurement']
    plot_saved(ds_check)
""", saved_datasets={'ds_measurement': saved})
    except ValueError:
        values, report = {}, {}
    assert 'ds_check' in values
    assert values['ds_check'] is saved
    assert plots.plot_saved(**values) is saved
    assert report['ds_check']['source'] == 'ds_measurement.h5'


@pytest.mark.parametrize('function,source,names', [
    ('plot_processed', "plot_processed(node.namespace['ds_off'], node.namespace['ds_on'])", ['ds_off', 'ds_on']),
    ('plot_cell', 'cell = choose_cell()\nplot_cell(cell)', ['cell']),
    ('plot_summary', "plot_summary(node.namespace['summary'])", ['summary']),
])
def test_unsaved_analysis_values_explain_fallback(plots, function, source, names):
    fn = getattr(plots, function)
    report = {}
    with pytest.raises(ValueError) as error:
        runner._bind_args(fn, ds_raw=object(), ds_fit=object(), qubits=[], params={},
            results={'ds_raw_off': './ds_raw_off.h5', 'ds_raw_on': './ds_raw_on.h5'},
            node_tree=ast.parse(source), plot_imports={function: ('calibration_utils.synthetic', function)},
            bind_report=report, saved_datasets={'ds_raw_off': object(), 'ds_raw_on': object()})
    for name in names:
        assert report[name]['status'] == 'unbound'
        assert name in report[name]['reason']
        assert "computed by the node's analysis, not saved" in str(error.value)


def test_missing_explicit_saved_dataset_is_not_a_filename(plots):
    report = {}
    message = ''
    try:
        runner._bind_args(plots.plot_saved, ds_raw=None, ds_fit=None, qubits=[], params={},
            results={'ds_measurement': './ds_measurement.h5'}, saved_datasets={},
            node_tree=ast.parse("plot_saved(node.results['ds_measurement'])"),
            plot_imports={'plot_saved': ('calibration_utils.synthetic', 'plot_saved')}, bind_report=report)
    except ValueError as error:
        message = str(error)
    assert report['ds_check']['status'] == 'unbound'
    assert 'saved value node.results.ds_measurement is missing' in message


def test_additional_saved_dataset_invalidates_run_inputs(tmp_path):
    from quam_state_manager.core.interactive_plots import replot
    before = replot._fingerprint(tmp_path)
    extra = tmp_path / 'ds_measurement.h5'
    extra.write_bytes(b'first')
    after = replot._fingerprint(tmp_path)
    assert after != before
    extra.write_bytes(b'second')
    assert replot._fingerprint(tmp_path) != after


def test_whole_saved_parameters_supply_marker_thresholds(plots):
    report = {}
    values = runner._bind_args(plots.plot_limits, ds_raw=None, ds_fit=None, qubits=[], params={'limit': 0.8},
        node_tree=ast.parse('plot_limits(node.parameters)'),
        plot_imports={'plot_limits': ('calibration_utils.synthetic', 'plot_limits')}, bind_report=report)
    assert 'parameters' in values
    assert plots.plot_limits(**values) == 0.8
    assert report['parameters']['status'] == 'bound'


def test_missing_sibling_keeps_successful_lab_figure(plots, tmp_path):
    from quam_state_manager.core.interactive_plots import replot_revision as ladder
    (tmp_path / 'figures.fit.png').write_bytes(b'png')
    (tmp_path / 'figures.missing.png').write_bytes(b'png')
    values, _ = bind(plots.plot_record, {'element0': {'peak': 4}})
    result = {'figures': [{'key': 'fit', 'axes': [], 'peak': plots.plot_record(**values)},
                          {'key': 'missing', 'axes': [], 'marker_defaults': ['peak']}],
              'errors': [{'stage': 'plot:missing', 'trace': 'ValueError: computed by analysis, not saved'}]}
    out = ladder.reproduce(tmp_path, tmp_path / 'instance', None, lambda _: result)
    assert [f['key'] for f in out['figures']] == ['fit']
    assert out['figures'][0]['peak'] == 4
    assert out['figures'][0]['revision']['rung'] == 'current'
    assert [f['key'] for f in out['saved_figures']] == ['missing']
    assert out['revision']['rung'] == 'mixed'


def test_runner_opens_the_explicit_saved_object_once(plots, tmp_path, monkeypatch):
    import matplotlib.pyplot as plt
    from quam_state_manager.generator import iplot_extract
    monkeypatch.setitem(sys.modules, 'iplot_extract', iplot_extract)
    loaded, closed = [], []
    dataset = SimpleNamespace(values=[2, 4], close=lambda: closed.append('closed'))
    xr = ModuleType('xarray')
    xr.open_dataset = lambda path, **kwargs: loaded.append(str(path)) or dataset
    monkeypatch.setitem(sys.modules, 'xarray', xr)
    config = ModuleType('quam_config')
    config.Quam = SimpleNamespace(load=lambda _: SimpleNamespace(qubits={}))
    monkeypatch.setitem(sys.modules, 'quam_config', config)
    module = ModuleType('calibration_utils.synthetic')
    module.Fit = plots.Fit
    module.plt = plt
    exec('def plot_saved(ds_check, ds_other):\n    assert ds_check is ds_other\n    fig, ax = plt.subplots()\n    ax.plot([0, 1], ds_check.values)\n    return fig\n', module.__dict__)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    tree = ast.parse("def plot_data(node):\n    plot_saved(node.results['ds_measurement'], node.results['ds_measurement'])")
    imports = {'plot_saved': (module.__name__, 'plot_saved')}
    monkeypatch.setattr(runner, '_resolve_node', lambda *args: (module.__name__, tree, imports))
    (tmp_path / 'node.json').write_text(json.dumps({'metadata': {'name': '01_synthetic'}}))
    (tmp_path / 'data.json').write_text(json.dumps({'ds_measurement': './alternate.h5'}))
    (tmp_path / 'alternate.h5').write_bytes(b'dataset')
    result = runner.replay(SimpleNamespace(run=str(tmp_path), source_root='', qubits='', util='', only=''))
    plt.close('all')
    assert not result['errors']
    assert loaded == [str((tmp_path / 'alternate.h5').resolve())]
    assert closed == ['closed']
    assert result['figures'][0]['axes'][0]['traces'][0]['y'] == [2, 4]
    assert result['bind_report']['saved']['ds_check']['source'] == 'alternate.h5'


@pytest.mark.parametrize('function', ['plot_bare', 'plot_builtin'])
def test_bare_mapping_annotations_keep_saved_records(plots, function):
    records = {'element0': {'peak': 4}, 'element1': {'peak': 7}}
    fn = getattr(plots, function)
    try:
        values, report = bind(fn, records)
    except IndexError:
        values, report = {}, {}
    assert 'fit_results' in values
    assert values['fit_results'] is records
    assert fn(**values) == records
    assert report['fit_results']['source'] == 'data.fit_results'


def test_saved_namespace_record_preserves_attribute_marker_inputs(plots):
    report = {}
    saved = {'cell': 3, 'propose': True, 'fallback': -1}
    values = runner._bind_args(plots.plot_decision, ds_raw=None, ds_fit=None, qubits=[], params={},
        results={'summary': saved}, node_tree=ast.parse("""
def draw(node):
    summary = node.namespace.get('summary')
    plot_decision(summary)
"""), plot_imports={'plot_decision': ('calibration_utils.synthetic', 'plot_decision')}, bind_report=report)
    assert getattr(values['summary'], 'propose', False) is True
    assert plots.plot_decision(**values) == 3
    assert values['summary'] == saved
    assert report['summary']['source'] == 'data.summary via node.namespace.summary'


def test_empty_uncertainty_segments_keep_points_and_finite_bars(plots):
    import matplotlib.pyplot as plt
    from quam_state_manager.generator.iplot_extract import extract_figure
    values, _ = bind(plots.plot_uncertain, {'element0': {'peak': 4}})
    figure = plots.plot_uncertain(**values)
    result = None
    try:
        result = extract_figure(figure, 'uncertainty')
    except IndexError:
        pass
    finally:
        plt.close(figure)
    assert result is not None
    traces = result['axes'][0]['traces']
    bars = [t for t in traces if t.get('mode') == 'lines' and t['x'] in ([0, 0], [2, 2])]
    assert len(bars) == 2
    assert bars[0]['y'] == [3.8, 4.2] and bars[1]['y'] == [5.6, 6.4]
    points = next(t for t in traces if t.get('mode') == 'markers' and t['x'] == [0, 1, 2])
    assert points['y'] == [4, 5, 6]
