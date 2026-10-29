"""Run each isolated fault, restore source bytes, require RED then GREEN."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MATH = 'quam_state_manager/core/cz_flux_math.py'
PLAN = 'quam_state_manager/core/cz_gate_plan.py'
JS = 'quam_state_manager/web/static/cz-plan.js'
MAP = 'quam_state_manager/web/static/cz-map.js'
HTML = 'quam_state_manager/web/templates/_cz_plan.html'
CAP = 'quam_state_manager/core/capabilities.py'
TEST = 'tests/test_cz_coupler.py::'
CASES = [
    ('invent default', PLAN, '"coupler_shift_hz": None, "coupler_method": COUPLER_UNKNOWN', '"coupler_shift_hz": 0.0, "coupler_method": COUPLER_UNKNOWN', TEST+'test_unknown_and_validation'),
    ('drop CR', MATH, 'return rwa + excited_cr - ground_cr', 'return rwa', TEST+'test_lamb_cr_switch'),
    ('idle Delta', PLAN, 'cz_flux_math.coupler_shift(bare, frequency, alpha', 'cz_flux_math.coupler_shift(5e9, frequency, alpha', TEST+'test_pulsed_frequencies'),
    ('twice f01', MATH, 'distances = [e20 - e11, e11 - e02, e10 - e01]', 'distances = [e20 - e11, fc - ft + abs(at) + 2 * coupler_shift(fc, wc, ac, ec, gc)["coupler_shift_hz"], e10 - e01]', TEST+'test_dressed_distance'),
    ('drop overlap', MATH, 'if min(s[1] for s in states) < 0.9 or len({s[2] for s in states}) != 2:', 'if False:', TEST+'test_hybridised_refusal'),
    ('second batch', JS, "return fetch('/field/edit-batch',", "fetch('/field/edit-batch', {body: JSON.stringify(payload)}); return fetch('/field/edit-batch',", TEST+'test_form_one_batch_group_and_identity'),
    ('skip chip gate', JS, "expect_chip: window.__chipToken || ''", "expect_chip: ''", TEST+'test_form_one_batch_group_and_identity'),
    ('recompute JS', MAP, 'if (point.shifted_frequency_hz != null) {', 'if (point.shifted_frequency_hz != null) { point.shifted_frequency_hz = point.frequency_hz;', TEST+'test_table_map_blob_toggle'),
    ('A1 empty rows', HTML, '<p>No flux CZ applies:', '<table><tr class="czp-row"><td></td></tr></table><p>No flux CZ applies:', TEST+'test_a1_exact_line_no_rows'),
    ('CR skip review', CAP, 'the cr gate macro will be skipped when unavailable', 'the cr gate macro', 'tests/test_capabilities_cz_all.py::test_cr_and_stark_unavailable_are_degrade_skips'),
]


def run(test):
    return subprocess.run([sys.executable, '-m', 'pytest', test, '-q', '-p', 'no:cacheprovider'],
        cwd=ROOT, env={**os.environ, 'PYTHONUTF8':'1'}, capture_output=True, text=True, encoding='utf-8')


def main():
    lines = []
    for name, relative, old, new, test in CASES:
        path = ROOT / relative
        original = path.read_bytes()
        source = original.decode('utf-8')
        assert old in source, name
        try:
            path.write_text(source.replace(old, new, 1), encoding='utf-8')
            red = run(test)
        finally:
            path.write_bytes(original)
        green = run(test)
        line = f'{name}: RED={red.returncode != 0}, GREEN={green.returncode == 0}; source restored'
        lines.append(line)
        print(line, flush=True)
        (ROOT/'tests/cz_coupler_mutation_results.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8')
        if red.returncode == 0 or green.returncode != 0:
            print(red.stdout + red.stderr + green.stdout + green.stderr)
            raise SystemExit(1)


if __name__ == '__main__':
    main()
