"""Reversible faults against the A4 plan pins (tests/test_cz_a4_plan.py); restores every file."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MATH = 'quam_state_manager/core/cz_flux_math.py'
PLAN = 'quam_state_manager/core/cz_gate_plan.py'
T = 'tests/test_cz_a4_plan.py::'
# The distinct-eigenvector rule is implied by overlap >= 0.9 (two orthogonal eigenvectors cannot
# both hold > 0.5 of one bare state), so no input reaches it alone; it is kept as REVIEW r1 asks.
FAULTS = [
    ('drop overlap refusal', MATH, 'if overlap < ZZ_OVERLAP_MIN or not distinct:', 'if not distinct:',
     T + 'test_hybridised_bias_is_refused'),
    ('wrong ZZ combination', MATH, '"zz_hz": e11 - e10 - e01 + e00', '"zz_hz": e11 - e10 - e01',
     T + 'test_zz_matches_an_independent_diagonalisation'),
    ('flat time without the factor 2', MATH, 't = 1e9 / (2 * abs(zz_hz))', 't = 1e9 / abs(zz_hz)',
     T + 'test_flat_time_gives_a_pi_conditional_phase_under_time_evolution'),
    ('drop the ZZ floor', MATH, '    if t > ZZ_T_MAX_NS:', '    if False:',
     T + 'test_flat_time_rounding_and_floor'),
    ('collision made a refusal', MATH, 'out["warnings"].append(f"coupler within', 'out["refusal"] = (f"coupler within',
     T + 'test_plan_collision_is_a_warning_and_a_path_through_hybridisation_is_refused'),
    ('no path scan', MATH, '    for a in np.linspace(0.0, amplitude, n_scan):', '    for a in []:',
     T + 'test_plan_collision_is_a_warning_and_a_path_through_hybridisation_is_refused'),
    ('coupler-only branch off', PLAN, '            elif is_coupler_only(cls, macro):', '            elif False:',
     T + 'test_coupler_only_row_moves_no_qubit_and_carries_the_plan'),
    ('invent a model', PLAN, '    if not model:\r\n        out.update(refusal=A4_MODEL_MISSING',
     '    if False:\r\n        out.update(refusal=A4_MODEL_MISSING',
     T + 'test_coupler_only_row_without_a_model_says_so_and_invents_nothing'),
]


def run(test):
    return subprocess.run([sys.executable, '-m', 'pytest', test, '-q', '-p', 'no:cacheprovider'],
                          cwd=ROOT, capture_output=True, text=True, encoding='utf8')


def main():
    rows = ['GREEN baseline: ' + run('tests/test_cz_a4_plan.py').stdout.strip().splitlines()[-1]]
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
    rows.append('GREEN restored: ' + run('tests/test_cz_a4_plan.py').stdout.strip().splitlines()[-1])
    (ROOT / 'tests/cz_a4_plan_mutation_results.txt').write_text('\n'.join(rows) + '\n', encoding='utf8')
    print('\n'.join(rows))


if __name__ == '__main__':
    main()
