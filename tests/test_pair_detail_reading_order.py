"""Rendered reading order, honest summaries, and visible gate warnings."""
from html.parser import HTMLParser

import pytest
from flask import render_template

from tests.test_gate_status_routes import rig  # noqa: F401


class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []
        self.parts = []

    @property
    def text(self):
        return ' '.join(''.join(self.parts).split())

    def has(self, cls):
        return cls in self.attrs.get('class', '').split()

    def all(self, predicate):
        return [n for c in self.children for n in [c, *c.all(lambda n: True)] if predicate(n)]


class Tree(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.root = self.current = Node()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.current)
        self.current.children.append(n)
        if tag not in ('input', 'br', 'hr', 'img', 'meta', 'link'):
            self.current = n

    def handle_endtag(self, tag):
        n = self.current
        while n.parent:
            if n.tag == tag:
                self.current = n.parent
                return
            n = n.parent

    def handle_data(self, data):
        n = self.current
        while n:
            n.parts.append(data)
            n = n.parent


def nodes(html, cls):
    return Tree(html).root.all(lambda n: n.has(cls))


def item(key, text, tone='neutral', pill=False, why='Evidence'):
    return dict(key=key, text=text, tone=tone, pill=pill, why=why)


def line(active=False, checked=False):
    facts = [item('check', 'checked' if checked else 'no check record', 'ok' if checked else 'neutral'),
             item('where', 'on the chip', 'ok', True),
             item('active', 'active' if active else 'not active', 'accent' if active else 'neutral', active),
             item('class', 'not a class SM made'), item('scaling', 'amplitude scaling: not known')]
    return dict(pair='q1-q2', gate='cz_example', items=facts, pills=[i for i in facts if i['pill']],
                details=facts, active=active, present=True, edit_paths=[], notices=[], recheck=False)


def render(rig, template, lines=(), sections=(), in_list=True):
    with rig._app.test_request_context('/pair/q1-q2'):
        return render_template(template, line=lines[0] if lines else None,
                               gate_lines=dict(pair='q1-q2', lines=lines, count=len(lines),
                                               no_active=bool(lines) and not any(x['active'] for x in lines)),
                               pair_name='q1-q2', pair={}, sections=sections, in_list=in_list)


@pytest.mark.parametrize('states, expected', [
    ([(True, True), (False, False), (False, True)], '1 active, 2 not active, 2 checked'),
    ([(True, False), (True, False)], '2 active, 0 not active, none checked'),
    ([], 'No CZ gate on this pair yet.'),
])
def test_conclusion(rig, states, expected):
    html = render(rig, '_gate_status_list.html', [line(*s) for s in states])
    summary = nodes(html, 'ui-panel-sum')[0]
    assert expected in summary.text
    assert nodes(html, 'ui-count')[0].text == str(len(states))
    verdicts = nodes(html, 'ui-verdict')
    assert len(verdicts) == bool(states)
    if states:
        assert verdicts[0].has('ui-verdict--strong')
    else:
        assert '0 active' not in html and 'ui-verdict--ok' not in html


def test_normal_facts_stay_inside_closed_fold(rig):
    html = render(rig, '_gate_status.html', [line()])
    assert len(nodes(html, 'ui-item')) == 1
    fold = nodes(html, 'gate-status-details')[0]
    assert 'open' not in fold.attrs
    assert not nodes(html, 'gate-status-items')[0].text
    assert not nodes(html, 'ui-fail-row')
    assert all(i['text'] in fold.text for i in line()['items'])
    assert len(fold.all(lambda n: n.has('ui-pill'))) == len(line()['items'])


def test_warning_and_refusal_stay_visible(rig):
    ln = line()
    ln['items'].append(item('class', 'class file missing', 'warn', False, 'Restore the file.'))
    ln.update(note='Cannot activate this gate.', note_tone='danger')
    html = render(rig, '_gate_status.html', [ln])
    fold = nodes(html, 'gate-status-details')[0]
    assert 'open' not in fold.attrs
    warnings = nodes(html, 'ui-fail-row')
    assert not any('Restore the file.' in n.text for n in warnings)
    assert any('Cannot activate this gate.' in n.text for n in warnings)
    for n in warnings:
        while n.parent:
            assert n is not fold
            n = n.parent
    pills = nodes(html, 'gate-status-items')[0]
    assert pills.text.count('class file missing') == 1
    assert 'class file missing' not in fold.text
    assert any(n.attrs.get('title') == 'Restore the file.' for n in pills.children)
    labels = fold.all(lambda n: n.tag == 'dt' and n.attrs.get('data-status-key') == 'class')
    assert labels[-1].text.strip() == 'Class'


def prop(key, value, **extra):
    return dict(key=key, value=value, raw=value, editable=True, dot_path='qubit_pairs.q1-q2.macros.' + key,
                is_pointer=False, is_self_ref=False, dangling=False, unit_key=key, **extra)


def sections(amplitude=0.25, length=52):
    return [dict(name='CZ ' + kind.title(), props=[prop('cz_' + kind + '_amplitude', amplitude),
                                                 prop('cz_' + kind + '_length', length)])
            for kind in ('bipolar', 'flattop', 'unipolar')]


def test_gate_zone_precedes_all_property_tables(rig):
    html = render(rig, '_pair_detail.html', [line()], sections())
    article = nodes(html, 'pair-detail')[0]
    assert article.children[0].has('gate-status-list')
    ordered = article.all(lambda n: n.has('gate-status-list') or n.tag == 'table')
    assert ordered[0].has('gate-status-list')


def test_parameter_zones_folded_and_summary_matches_cells(rig):
    html = render(rig, '_pair_detail.html', [line()], sections())
    zones = nodes(html, 'ui-zone--fold')
    assert len(zones) == 3
    for zone in zones:
        assert zone.tag == 'details' and 'open' not in zone.attrs
        summary = zone.children[0]
        assert summary.has('ui-zone-head')
        assert 'Amplitude: 0.25; Length: 52 ns' in summary.text
        cells = zone.all(lambda n: n.tag == 'td' and n.has('col-val'))
        assert cells[0].all(lambda n: n.tag == 'input' and n.attrs.get('value') == '0.25')
        assert 'dimensionless' not in summary.text + cells[0].text
        assert '52 ns' in cells[1].text


@pytest.mark.parametrize('amplitude,length', [(None, 52), (0.25, None), (None, None)])
def test_missing_values_are_named(rig, amplitude, length):
    html = render(rig, '_pair_detail.html', [line()], sections(amplitude, length))
    for zone in nodes(html, 'ui-zone--fold'):
        summary = zone.children[0].text
        for label, value in [('Amplitude', amplitude), ('Length', length)]:
            if value is None:
                assert label + ': not available' in summary
        assert '0 dimensionless' not in summary and '0 ns' not in summary


@pytest.mark.parametrize('value', [[1, 2], '#./missing', True])
def test_non_numeric_values_are_not_numbers(rig, value):
    html = render(rig, '_pair_detail.html', [line()], sections(value, value))
    for zone in nodes(html, 'ui-zone--fold'):
        assert 'Amplitude: not available; Length: not available' in zone.children[0].text


def test_parameter_warning_opens_its_fold(rig):
    data = sections()
    data[0]['props'][0]['dangling'] = True
    html = render(rig, '_pair_detail.html', [line()], data)
    zones = nodes(html, 'ui-zone--fold')
    assert 'open' in zones[0].attrs
    assert all('open' not in n.attrs for n in zones[1:])


@pytest.mark.parametrize('flag', ['is_self_ref', 'dangling'])
def test_numeric_pointer_summary_guard(rig, flag):
    data = sections()
    data[0]['props'][0][flag] = True
    summary = nodes(render(rig, '_pair_detail.html', [line()], data), 'ui-zone--fold')[0].children[0]
    assert 'Amplitude: not available' in summary.text


def test_conflict_opens_its_fold(rig):
    data = sections()
    data[0]['props'][1]['fid_note'] = dict(conflict='Values disagree', title='Check values')
    zones = nodes(render(rig, '_pair_detail.html', [line()], data), 'ui-zone--fold')
    assert 'open' in zones[0].attrs
    assert 'data-detail-warning' in zones[0].attrs
    assert all('open' not in z.attrs for z in zones[1:])


def test_length_only_gate_and_coupler_and_non_gate(rig):
    data = [dict(name='Length only', props=[prop('cz_example_length', 76)]),
            dict(name='Coupler', props=[prop('cz_example_coupler_amplitude', 0.3)]),
            dict(name='General', props=[prop('detuning', 0.2)])]
    html = render(rig, '_pair_detail.html', [line()], data)
    zones = nodes(html, 'ui-zone--fold')
    assert len(zones) == 1
    assert 'Length: 76 ns' in zones[0].children[0].text
    assert zones[0].attrs['data-detail-zone'] == 'cz_example'
    other = [z for z in nodes(html, 'detail-section') if any(n.tag == 'table' for n in z.children) and not z.has('ui-zone--fold')]
    assert len(other) == 2 and all('open' in z.attrs for z in other)


@pytest.mark.parametrize('editable', [True, False])
def test_unitless_values_use_cell_wording(rig, editable):
    data = sections()
    for section in data:
        for p in section['props']:
            p.update(unit_key='unknown_quantity', editable=editable)
    zones = nodes(render(rig, '_pair_detail.html', [line()], data), 'ui-zone--fold')
    for zone in zones:
        assert 'Amplitude: 0.25; Length: 52' in zone.children[0].text
        assert 'dimensionless' not in zone.text


@pytest.mark.parametrize('editable', [True, False])
@pytest.mark.parametrize('value', [0.0005, 0, 0.0, -0.0005, 0.25, 2500000.0, 'text'])
def test_summary_and_cell_print_a_unitless_value_alike(rig, editable, value):
    data = sections(value, value)
    for section in data:
        for p in section['props']:
            p.update(unit_key='unknown_quantity', editable=editable)
    for zone in nodes(render(rig, '_pair_detail.html', [line()], data), 'ui-zone--fold'):
        cell = zone.all(lambda n: n.tag == 'td' and n.has('col-val'))[0]
        shown = cell.all(lambda n: n.tag == 'input' and n.attrs.get('name') == 'value')[0].attrs['value'] if editable else cell.text
        text = zone.children[0].text
        if isinstance(value, str):
            assert 'Amplitude: not available' in text
            continue
        assert 'Amplitude: ' + shown + '; Length: ' + shown in text


def test_summary_takes_a_unit_when_the_cell_has_one(rig):
    data = sections(0.0005, 5e-8)
    for zone in nodes(render(rig, '_pair_detail.html', [line()], data), 'ui-zone--fold'):
        cells = zone.all(lambda n: n.tag == 'td' and n.has('col-val'))
        previews = [c.all(lambda n: n.has('unit-preview')) for c in cells]
        assert previews[1] and previews[1][0].text in zone.children[0].text
        assert 'Length: ' + previews[1][0].text in zone.children[0].text


def test_checked_count_and_warning_tone_from_rows(rig):
    lines = [line(True, True), line(), line(), line(), line()]
    for ln, text in zip(lines[1:], ['values changed since the check', 'checked, cannot tell whether values changed', 'not checked', 'no check record']):
        ln['items'][0].update(text=text, tone='warn' if text != 'no check record' else 'neutral')
    html = render(rig, '_gate_status_list.html', lines)
    verdict = nodes(html, 'ui-verdict')[0]
    assert '1 active, 4 not active, 3 checked' in verdict.text
    assert verdict.has('ui-verdict--warn')


def test_no_active_pill(rig):
    html = render(rig, '_gate_status_list.html', [line(), line()])
    assert any(n.text == 'no active gate' and n.has('ui-pill--warn') for n in nodes(html, 'ui-pill'))
    verdict = nodes(html, 'ui-verdict')[0]
    assert '0 active, 2 not active' in verdict.text
    assert verdict.has('ui-verdict--warn') and not verdict.has('ui-verdict--neutral')


def test_warning_notice_once_and_row_hooks(rig):
    ln = line()
    fact = item('needs', 'Required file missing', 'warn', True)
    ln['items'].append(fact)
    ln['notices'] = [fact]
    ln.update(edit_paths=['macros.example.amplitude'], recheck=True)
    html = render(rig, '_gate_status_list.html', [ln])
    row = nodes(html, 'gate-status')[0]
    assert row.text.count(fact['text']) == 1
    assert row.attrs['data-gate-pair'] == ln['pair'] and row.attrs['data-gate'] == ln['gate']
    for cls in ['gate-status-edit', 'gate-status-recheck', 'gate-status-activate', 'gate-status-delete']:
        button = row.all(lambda n: n.has(cls))[0]
        assert button in row.all(lambda n: n.tag == 'button')
        assert 'hx-target' in button.attrs and button.attrs['hx-swap'] in ['innerHTML', 'outerHTML']
    assert row.all(lambda n: n.has('gate-status-slot'))[0] in row.children


def test_pair_detail_client_selfcheck(rig, tmp_path):
    import json
    import os
    import subprocess
    from pathlib import Path
    data = sections()
    changed = sections(0.37, 68)
    warning = sections(0.37, 68)
    warning[0]['props'][0]['dangling'] = True
    fixtures = dict(initial=render(rig, '_pair_detail.html', [line()], data),
                    changed=render(rig, '_pair_detail.html', [line()], changed),
                    warning=render(rig, '_pair_detail.html', [line()], warning))
    fixture = tmp_path / 'panes.json'
    fixture.write_text(json.dumps(fixtures), encoding='utf-8')
    result = subprocess.run(['node', str(Path(__file__).with_name('pair_detail_reading_order_selfcheck.cjs')), str(fixture)],
                            capture_output=True, text=True, timeout=60, env=os.environ.copy())
    assert result.returncode == 0, result.stdout + result.stderr


# The literal texts the page printed before the reading-order change: (value, input text, read-only cell text).
# Each was read off a render of the main-branch template and of this branch's template, and the two agreed.
NUMBER_FORMS = [
    (0.0005, '5.000000e-04', '5.000000e-04'), (5e-4, '5.000000e-04', '5.000000e-04'),
    (0, '0', '0'), (0.0, '0.0', '0.0'), (-0.0005, '-5.000000e-04', '-5.000000e-04'),
    (0.25, '0.25', '0.25'), (1e-9, '1.000000e-09', '1.000000e-09'),
    (1.23456789e-7, '1.234568e-07', '1.234568e-07'), (999999.0, '999999.0', '999999.0'),
    (1e6, '1.000000e+06', '1.000000e+06'), (2.5e6, '2.500000e+06', '2.500000e+06'),
    (1e21, '1.000000e+21', '1.000000e+21'), (7, '7', '7'), (True, 'True', 'True'),
    ('abc', 'abc', 'abc'), (None, '', 'not set'),
    # integers never take the exponent form, whatever their size
    (2000000, '2000000', '2000000'), (-2000000, '-2000000', '-2000000'), (1000000, '1000000', '1000000'),
    (999999, '999999', '999999'), (10 ** 12, '1000000000000', '1000000000000'), (-7, '-7', '-7'), (1, '1', '1'),
    # the small-number boundary: 0.001 and above stay plain, below it takes the exponent form
    (0.001, '0.001', '0.001'), (0.00099, '9.900000e-04', '9.900000e-04'), (0.0010000001, '0.0010000001', '0.0010000001'),
    (0.005, '0.005', '0.005'), (0.0099, '0.0099', '0.0099'), (0.01, '0.01', '0.01'),
    (-0.001, '-0.001', '-0.001'), (-0.00099, '-9.900000e-04', '-9.900000e-04'), (-0.005, '-0.005', '-0.005'),
    (-0.01, '-0.01', '-0.01'),
    # the large-number boundary, both signs
    (999999.9999, '999999.9999', '999999.9999'), (-999999.0, '-999999.0', '-999999.0'),
    (-1e6, '-1.000000e+06', '-1.000000e+06'), (-2.5e6, '-2.500000e+06', '-2.500000e+06'),
    # negative zero, a boolean, a numeric string and a list are not turned into exponent numbers
    (-0.0, '-0.0', '-0.0'), (False, 'False', 'False'), ('0.005', '0.005', '0.005'),
    ([0.0005, 2500000.0, 0.25], '[0.0005, 2500000.0, 0.25]', '[3 items]'),
    # non-finite floats: this is what main prints today and what this branch keeps; it is the present
    # behaviour, not a wish (a state file read with json.loads can hold them)
    (float('nan'), 'nan', 'nan'), (float('inf'), 'inf', 'inf'), (float('-inf'), '-inf', '-inf'),
]


@pytest.mark.parametrize('value, typed, shown', NUMBER_FORMS)
def test_cell_number_forms_are_the_literal_texts(rig, value, typed, shown):
    for editable, expected in [(True, typed), (False, shown)]:
        data = sections(value, value)
        for section in data:
            for p in section['props']:
                p.update(unit_key='unknown_quantity', editable=editable)
        zone = nodes(render(rig, '_pair_detail.html', [line()], data), 'ui-zone--fold')[0]
        cell = zone.all(lambda n: n.tag == 'td' and n.has('col-val'))[0]
        if editable:
            box = cell.all(lambda n: n.tag == 'input' and n.attrs.get('name') == 'value')[0]
            assert box.attrs['value'] == expected
        else:
            assert cell.text == expected
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        assert 'Amplitude: ' + (expected if numeric else 'not available') + ';' in zone.children[0].text


def test_list_hides_the_routine_checked_pill_but_not_a_warning(rig):
    ln = line(checked=True)
    ln['items'][0].update(pill=True)
    pills = nodes(render(rig, '_gate_status.html', [ln]), 'gate-status-items')[0]
    assert 'checked' not in pills.text and 'on the chip' not in pills.text
    ln['items'][0].update(tone='warn')
    pills = nodes(render(rig, '_gate_status.html', [ln]), 'gate-status-items')[0]
    assert 'checked' in pills.text
    assert any(n.has('ui-pill--warn') and n.text == 'checked' for n in pills.children)


def test_empty_list_wording(rig):
    html = render(rig, '_gate_status_list.html', [])
    assert nodes(html, 'ui-zone-empty')[0].text == 'Press + Gate to create one.'
    assert not nodes(render(rig, '_gate_status_list.html', [line()]), 'ui-zone-empty')


def test_gate_zone_body_wraps_the_table_and_details_carry_evidence(rig):
    zones = nodes(render(rig, '_pair_detail.html', [line()], sections()), 'ui-zone--fold')
    for zone in zones:
        assert [c.tag for c in zone.children if c.has('ui-zone-body')] == ['div']
        body = [c for c in zone.children if c.has('ui-zone-body')][0]
        assert body.all(lambda n: n.tag == 'table')
    ln = line()
    for i, it in enumerate(ln['details']):
        it['why'] = 'Because reason %d.' % i
    html = render(rig, '_gate_status.html', [ln])
    dds = [n.text for n in nodes(html, 'gate-status-details-list')[0].all(lambda n: n.tag == 'dd')]
    assert dds == ['Because reason %d.' % i for i in range(len(ln['details']))]
    other = [dict(name='General', props=[prop('detuning', 0.2)])]
    plain = nodes(render(rig, '_pair_detail.html', [line()], other), 'detail-section')[0]
    assert not plain.all(lambda n: n.has('ui-zone-body'))


# A read-only cell with a known unit: only a float outside the plain range takes the exponent form AND a unit preview.
READ_ONLY_WITH_UNIT = [
    (0.0005, '5.000000e-04 0.0 ns'), (0.00099, '9.900000e-04 0.0 ns'), (0.001, '0.001'), (0.005, '0.005'),
    (999999.0, '999999.0'), (1000000.0, '1.000000e+06 1000000 ns'), (2500000.0, '2.500000e+06 2500000 ns'),
    (-0.00099, '-9.900000e-04 -0.0 ns'), (-0.001, '-0.001'), (-1000000.0, '-1.000000e+06 -1000000 ns'),
    (-999999.0, '-999999.0'), (2000000, '2000000'), (1000000, '1000000'), (52, '52'), (0.3, '0.3'), (123456.0, '123456.0'),
]


@pytest.mark.parametrize('value, shown', READ_ONLY_WITH_UNIT)
def test_read_only_cell_with_a_unit_keeps_its_preview_rule(rig, value, shown):
    data = sections(value, value)
    for section in data:
        for p in section['props']:
            p['editable'] = False
    zone = nodes(render(rig, '_pair_detail.html', [line()], data), 'ui-zone--fold')[0]
    assert zone.all(lambda n: n.tag == 'td' and n.has('col-val'))[1].text == shown


@pytest.mark.parametrize('count', [0, 1, 3])
def test_gate_count_is_the_number_of_rows(rig, count):
    lines = [line(i == 0) for i in range(count)]
    html = render(rig, '_gate_status_list.html', lines)
    assert nodes(html, 'ui-count')[0].text == str(count)
    assert len(nodes(html, 'gate-status')) == count
    assert bool(nodes(html, 'ui-zone--quiet')) == (count == 0)
    if count:
        assert nodes(html, 'ui-verdict')[0].text.startswith('%d active, %d not active' % (1, count - 1))


def test_verdict_is_neutral_without_a_warning_and_warn_for_a_danger_item(rig):
    html = render(rig, '_gate_status_list.html', [line(True, True)])
    assert nodes(html, 'ui-verdict')[0].has('ui-verdict--neutral')
    ln = line(True, True)
    ln['items'].append(item('class', 'class file missing', 'danger', True))
    html = render(rig, '_gate_status_list.html', [ln])
    assert nodes(html, 'ui-verdict')[0].has('ui-verdict--warn')


@pytest.mark.parametrize('tone', ['warn', 'danger'])
def test_one_notice_raised_by_an_item_and_a_notice_prints_once(rig, tone):
    ln = line()
    fact = item('needs', 'Required file missing', tone, True)
    other = item('needs', 'Another file is needed', 'neutral', False)
    ln['items'].append(fact)
    ln['notices'] = [fact, other]
    row = nodes(render(rig, '_gate_status_list.html', [ln]), 'gate-status')[0]
    assert row.text.count('Required file missing') == 1
    assert row.text.count('Another file is needed') == 1
    quiet = [n.text for n in row.all(lambda n: n.attrs.get('data-status-key') == 'needs' and n.has('ui-text-quiet'))]
    assert quiet == ['Another file is needed']
    ln['notices'] = [other, dict(other, text='A third file is needed')]
    row = nodes(render(rig, '_gate_status_list.html', [ln]), 'gate-status')[0]
    assert row.text.count('Another file is needed') == 1 and row.text.count('A third file is needed') == 1


def test_routine_pills_show_outside_a_list(rig):
    ln = line(checked=True)
    ln['items'][0].update(pill=True)
    pills = nodes(render(rig, '_gate_status.html', [ln], in_list=False), 'gate-status-items')[0]
    assert 'checked' in pills.text and 'on the chip' in pills.text
    pills = nodes(render(rig, '_gate_status.html', [ln], in_list=True), 'gate-status-items')[0]
    assert 'checked' not in pills.text and 'on the chip' not in pills.text
    ln['items'][0].update(text='checked, cannot tell whether values changed')
    pills = nodes(render(rig, '_gate_status.html', [ln], in_list=True), 'gate-status-items')[0]
    assert 'cannot tell' in pills.text


def test_row_actions_follow_the_line(rig):
    ln = line()
    ln.update(present=False, edit_paths=['a'], recheck=True)
    row = nodes(render(rig, '_gate_status.html', [ln]), 'gate-status')[0]
    assert not row.all(lambda n: n.tag == 'button')
    ln.update(present=True, edit_paths=[], recheck=False, active=True)
    row = nodes(render(rig, '_gate_status.html', [ln]), 'gate-status')[0]
    assert [b.text for b in row.all(lambda n: n.tag == 'button')] == ['Delete']


@pytest.mark.parametrize('tone', ['danger', 'warn'])
def test_a_press_note_is_marked_and_shown(rig, tone):
    ln = line()
    ln.update(note='The press said this.', note_tone=tone)
    row = nodes(render(rig, '_gate_status.html', [ln]), 'gate-status')[0]
    marked = row.all(lambda n: 'data-press-answer' in n.attrs)
    assert len(marked) == 1 and 'The press said this.' in marked[0].text
    assert bool(row.all(lambda n: n.has('ui-fail-row'))) == (tone == 'danger')


def test_details_fall_back_to_the_items(rig):
    ln = line()
    ln['details'] = []
    lst = nodes(render(rig, '_gate_status.html', [ln]), 'gate-status-details-list')[0]
    assert [n.attrs['data-status-key'] for n in lst.all(lambda n: n.tag == 'dt')] == [i['key'] for i in ln['items']]


@pytest.mark.parametrize('key', ['cz_example_flat_length', 'cz_example_smoothing_length', 'cz_example_padding_length',
                                 'cz_example_coupler_amplitude'])
def test_auxiliary_keys_alone_do_not_make_a_gate_zone(rig, key):
    data = [dict(name='Auxiliary', props=[prop(key, 12)])]
    html = render(rig, '_pair_detail.html', [line()], data)
    assert not nodes(html, 'ui-zone--fold')
    assert 'open' in nodes(html, 'detail-section')[0].attrs


def test_gate_name_comes_from_the_amplitude_key_first(rig):
    data = [dict(name='Mixed', props=[prop('cz_one_amplitude', 0.2), prop('cz_two_length', 40)]),
            dict(name='Mixed again', props=[prop('cz_three_length', 40), prop('cz_four_amplitude', 0.2)])]
    zones = nodes(render(rig, '_pair_detail.html', [line()], data), 'ui-zone--fold')
    assert [z.attrs['data-detail-zone'] for z in zones] == ['cz_one', 'cz_four']
    assert 'Length: not available' in zones[0].children[0].text
    assert 'Amplitude: 0.2' in zones[1].children[0].text


def test_a_note_without_a_conflict_does_not_open_the_fold(rig):
    data = sections()
    data[0]['props'][1]['fid_note'] = dict(conflict='', title='Fine')
    zones = nodes(render(rig, '_pair_detail.html', [line()], data), 'ui-zone--fold')
    assert all('open' not in z.attrs and 'data-detail-warning' not in z.attrs for z in zones)


def test_non_gate_sections_keep_the_plain_header_and_markup_balances(rig):
    other = [dict(name='General', props=[prop('detuning', 0.2)])]
    html = render(rig, '_pair_detail.html', [line()], other + sections())
    plain = nodes(html, 'detail-section')[0]
    assert plain.children[0].has('section-header') and not plain.children[0].has('ui-zone-head')
    assert html.count('<div') == html.count('</div>')
    assert html.count('<details') == html.count('</details>')


def test_active_gate_detail_is_a_badge_and_the_rest_are_pills(rig):
    ln = line(active=True)
    ln['items'].append(item('check', 'checked with an accent', 'accent'))
    dts = {(n.attrs['data-status-key'], n.text): n for n in
           nodes(render(rig, '_gate_status.html', [ln]), 'gate-status-details-list')[0].all(lambda n: n.tag == 'dt')}
    badge = dts[('active', 'active')]
    assert badge.all(lambda n: n.has('ui-badge') and n.has('ui-badge--accent'))
    assert not badge.all(lambda n: n.has('ui-pill'))
    for key in [('where', 'on the chip'), ('check', 'checked with an accent')]:
        assert dts[key].all(lambda n: n.has('ui-pill')) and not dts[key].all(lambda n: n.has('ui-badge'))
    ln = line(active=False)
    ln['items'][2].update(tone='accent')
    dt = [n for n in nodes(render(rig, '_gate_status.html', [ln]), 'gate-status-details-list')[0].all(lambda n: n.tag == 'dt')
          if n.attrs['data-status-key'] == 'active'][0]
    assert dt.all(lambda n: n.has('ui-badge')), 'key and accent tone are what make the badge'
    ln = line(active=False)
    dt = [n for n in nodes(render(rig, '_gate_status.html', [ln]), 'gate-status-details-list')[0].all(lambda n: n.tag == 'dt')
          if n.attrs['data-status-key'] == 'active'][0]
    assert dt.all(lambda n: n.has('ui-pill')) and not dt.all(lambda n: n.has('ui-badge'))


@pytest.mark.parametrize('tone', ['danger', 'warn'])
def test_a_warning_item_is_a_row_pill_whether_or_not_it_is_a_pill_item(rig, tone):
    ln = line()
    ln['items'].append(item('class', 'class file missing', tone, False, 'Restore the file.'))
    pills = nodes(render(rig, '_gate_status.html', [ln]), 'gate-status-items')[0]
    shown = [n for n in pills.children if n.text == 'class file missing']
    assert len(shown) == 1 and shown[0].has('ui-pill--' + tone)
    ln = line()
    ln['items'].append(item('class', 'class file is fine', 'ok', False))
    assert 'class file is fine' not in nodes(render(rig, '_gate_status.html', [ln]), 'gate-status-items')[0].text


def test_the_no_active_pill_says_why_in_its_tooltip(rig):
    html = render(rig, '_gate_status_list.html', [line()])
    wrap = [n for n in nodes(html, 'ui-text-warn') if n.all(lambda c: c.has('ui-pill'))]
    assert len(wrap) == 1 and wrap[0].attrs.get('title') == "The pair's cz alias is not set"
    assert not nodes(render(rig, '_gate_status_list.html', [line(True)]), 'ui-text-warn')


def test_a_pill_has_a_tooltip_only_when_it_has_evidence(rig):
    ln = line()
    ln['items'].append(item('class', 'class file missing', 'warn', True, ''))
    ln['items'].append(item('needs', 'file needed', 'warn', True, 'Because.'))
    pills = {n.text: n for n in nodes(render(rig, '_gate_status.html', [ln]), 'gate-status-items')[0].children}
    assert 'title' not in pills['class file missing'].attrs
    assert pills['file needed'].attrs['title'] == 'Because.'


def test_empty_list_sentence_is_quiet_text_and_the_row_head_is_a_kit_head(rig):
    html = render(rig, '_gate_status_list.html', [])
    assert [n.text for n in nodes(html, 'ui-text-quiet')] == ['No CZ gate on this pair yet.']
    head = nodes(render(rig, '_gate_status.html', [line()]), 'ui-item')[0].children[0]
    assert head.has('ui-item-head') and head.has('gate-status-row')


def test_a_missing_notice_list_renders_and_a_press_note_has_a_title(rig):
    ln = line()
    ln['notices'] = None
    ln.update(note='The press said this.', note_tone='ok')
    row = nodes(render(rig, '_gate_status.html', [ln]), 'gate-status')[0]
    call = row.all(lambda n: n.has('ui-callout'))[0]
    assert call.has('ui-callout--ok') and call.children[0].text == 'Note'
    assert 'The press said this.' in call.text


@pytest.mark.parametrize('in_list', [True, False])
def test_row_buttons_target_their_own_scope(rig, in_list):
    ln = line()
    ln.update(edit_paths=['macros.example.amplitude'], recheck=True)
    row = nodes(render(rig, '_gate_status.html', [ln], in_list=in_list), 'gate-status')[0]
    want = 'closest .gate-status-list' if in_list else 'closest .gate-status'
    for cls in ['gate-status-recheck', 'gate-status-activate']:
        button = row.all(lambda n: n.has(cls))[0]
        assert button.attrs['hx-target'] == want
        assert ('"scope": "list"' in button.attrs['hx-vals']) == in_list
    assert ('scope=list' in row.all(lambda n: n.has('gate-status-delete'))[0].attrs['hx-get']) == in_list


def test_summary_falls_back_to_the_key_when_no_unit_key_is_given(rig):
    data = sections()
    for section in data:
        for p in section['props']:
            p['unit_key'] = None
    zone = nodes(render(rig, '_pair_detail.html', [line()], data), 'ui-zone--fold')[0]
    assert 'Length: 52 ns' in zone.children[0].text


def test_zone_head_has_a_title_node_the_search_can_read(rig):
    zone = nodes(render(rig, '_pair_detail.html', [line()], sections()), 'ui-zone--fold')[0]
    titles = zone.children[0].all(lambda n: n.has('ui-zone-title'))
    assert [t.tag for t in titles] == ['h3'] and titles[0].text == 'CZ Bipolar'
    assert zone.children[0].all(lambda n: n.has('ui-panel-sum'))


def test_notices_note_and_slot_sit_outside_the_row_item(rig):
    ln = line()
    ln['notices'] = [item('needs', 'Another file is needed')]
    ln.update(note='The press said this.', note_tone='ok')
    html = render(rig, '_gate_status.html', [ln])
    row = nodes(html, 'gate-status')[0]
    inside = nodes(html, 'ui-item')[0]
    assert 'Another file is needed' not in inside.text and 'The press said this.' not in inside.text
    assert not inside.all(lambda n: n.has('gate-status-slot'))
    assert row.all(lambda n: n.has('gate-status-slot'))[0] in row.children
    assert html.count('<div') == html.count('</div>')
