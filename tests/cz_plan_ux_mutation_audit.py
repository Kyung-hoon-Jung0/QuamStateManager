"""Run and revert one production mutation for each CZ7 UX pin."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
JS = 'quam_state_manager/web/static/cz-plan.js'
TEMPLATE = 'quam_state_manager/web/templates/_cz_plan.html'
MUTATIONS = [
    (1, JS, ".has('pair')) revealFigures(root)", ".has('disabled')) revealFigures(root)"),
    (2, JS, "(lines[k].point.validity || 'validity unknown')", "'extrapolated outside validity range'"),
    (3, JS, "match.textContent = result.summary || result.reason || '';", "match.textContent = 'amplitude unavailable';"),
    (4, JS, "x: 1, xanchor: 'left', xshift: 8, yref: 'y'", "x: 1, xanchor: 'right', xshift: 0, yref: 'y'"),
    (5, JS, "domain: [0, 1], anchor: 'x'", "domain: [0, 0.34], anchor: 'x'"),
    (6, TEMPLATE, '>no quad term</span>', '>{{ r.reasons | join("; ") }}</span>'),
    (7, JS, "(r.duration_note ? help(r.duration_note) : '')", "(r.duration_note ? esc(r.duration_note) : '')"),
    (8, 'quam_state_manager/web/static/cz-map.js', 'var PARTNER_FONT = 11;', 'var PARTNER_FONT = 9;'),
]


def main():
    results = []
    env = dict(os.environ, PYTHONUTF8='1')
    for item, file, old, new in MUTATIONS:
        path = ROOT / file
        original = path.read_bytes()
        source = original.decode('utf8')
        assert old in source, (item, old)
        command = [sys.executable, '-m', 'pytest', f'tests/test_cz_plan_ux.py::test_cz_plan_ux[UX-{item}]',
                   '-q', '-p', 'no:cacheprovider']
        try:
            path.write_text(source.replace(old, new), encoding='utf8')
            red = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf8', timeout=60)
            assert red.returncode == 1 and 'AssertionError' in red.stdout, red.stdout + red.stderr
        finally:
            path.write_bytes(original)
        green = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf8', timeout=60)
        assert green.returncode == 0, green.stdout + green.stderr
        result = f'UX-{item}: RED (mutation detected), reverted, GREEN; {file}: {old} -> {new}'
        print(result, flush=True)
        results.append(result)
    (ROOT / 'tests/cz_plan_ux_mutation_results.txt').write_text('\n'.join(results) + '\n', encoding='utf8')


if __name__ == '__main__':
    main()
