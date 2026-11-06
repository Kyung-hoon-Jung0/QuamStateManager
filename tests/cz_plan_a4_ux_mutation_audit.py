"""Reversible faults against the A4-UX pins and the stored-length phase; restores every file."""
from evidence_output import evidence_path
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
JS = 'quam_state_manager/web/static/cz-plan.js'
MATH = 'quam_state_manager/core/cz_flux_math.py'
UX = 'tests/test_cz_plan_a4_ux.py::test_cz_plan_a4_ux[A4-UX-{}]'
PLAN = 'tests/test_cz_a4_plan.py::'
FAULTS = [
    ('A4-UX-1 mismatch not in warn colour', JS, "(mismatch ? ' czp-warn-text' : '')", "''", UX.format(1)),
    ('A4-UX-1 phase dropped from the line', JS, "esc(phase) + '</span>'", "'</span>'", UX.format(1)),
    ('A4-UX-2 card duration from the macro', JS, "(r.a4 ? '<dt>Duration</dt>", "(false ? '<dt>Duration</dt>", UX.format(2)),
    ('A4-UX-3 flux rows on fixed qubits', JS, "            if (r.a4) {\r\n                h.push('<div class=\"czp-card-q czp-card-fixed\"",
     "            if (false) {\r\n                h.push('<div class=\"czp-card-q czp-card-fixed\"", UX.format(3)),
    ('A4-UX-4 no idle ZZ line', JS, '            idleShapes.push(', '            [].push(', UX.format(4)),
    ('phase without the factor 2', MATH, 'return (-2.0 * zz_hz * t_ns * 1e-9) % 2.0', 'return (-zz_hz * t_ns * 1e-9) % 2.0',
     PLAN + 'test_stored_length_phase_formula_matches_time_evolution'),
    ('idle ZZ taken at the pulse', MATH, 'idle = zz_three_mode(fc, ft, coupler_frequency(model, 0.0),',
     'idle = zz_three_mode(fc, ft, coupler_frequency(model, amplitude),', PLAN + 'test_plan_reports_the_stored_length_phase_and_the_idle_zz'),
]
ALL = ['tests/test_cz_plan_a4_ux.py', 'tests/test_cz_a4_plan.py']


def run(*tests):
    return subprocess.run([sys.executable, '-m', 'pytest', *tests, '-q', '-p', 'no:cacheprovider'],
                          cwd=ROOT, capture_output=True, text=True, encoding='utf8')


def main():
    output = evidence_path('cz_plan_a4_ux_mutation_results.txt')
    rows = ['GREEN baseline: ' + run(*ALL).stdout.strip().splitlines()[-1]]
    for name, file, old, new, test in FAULTS:
        path = ROOT / file
        original = path.read_bytes()
        source = original.decode('utf8')
        if source.count(old) == 0:
            old, new = old.replace('\r\n', '\n'), new.replace('\r\n', '\n')
        assert source.count(old) == 1, (name, source.count(old))
        try:
            path.write_bytes(source.replace(old, new).encode('utf8'))
            r = run(test)
            rows.append(('RED caught: ' if r.returncode == 1 and 'failed' in r.stdout else 'NOT CAUGHT: ') + name)
        finally:
            path.write_bytes(original)
    rows.append('GREEN restored: ' + run(*ALL).stdout.strip().splitlines()[-1])
    output.write_text('\n'.join(rows) + '\n', encoding='utf8')
    print('\n'.join(rows))


if __name__ == '__main__':
    main()
