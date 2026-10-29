"""Run S2 fault injections, restoring exact source bytes after each attempt."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MATH = 'quam_state_manager/core/cz_flux_math.py'
PLAN = 'quam_state_manager/core/cz_gate_plan.py'
ROUTE = 'quam_state_manager/web/routes.py'
CLIENT = 'quam_state_manager/web/static/cz-plan.js'
TEST = 'tests/test_cz_solve.py::'
CASES = [
    ('coefficient overrides evidence', MATH,
     'sign = evidence_sign if evidence_sign is not None else (1 if coefficient > 0 else -1)',
     'sign = 1 if coefficient > 0 else -1', TEST + 'test_quad_inverse_and_direction',
     [('if evidence_sign is not None and coefficient * evidence_sign < 0:', 'if False:')]),
    ('drop direction check', MATH, 'if delta * sign < 0:', 'if False:', TEST + 'test_quad_inverse_and_direction', []),
    ('drop measured window', MATH,
     'allowed = [a for a in candidates if lo - tol <= a <= hi + tol]',
     'allowed = candidates', TEST + 'test_model_roundtrip_and_window', []),
    ('ignore off-spot model anchor', PLAN,
     'is False or off_spot:', 'is False:', TEST + 'test_off_spot_model_anchor_refuses_only_quad', []),
    ('swap anharmonicities', MATH,
     'ah = abs(alpha_c if high == "control" else alpha_t)',
     'ah = abs(alpha_t if high == "control" else alpha_c)', TEST + 'test_signed_target', []),
    ('ignore unit peak', PLAN, 'abs(amplitude * peak) > bound', 'abs(amplitude) > bound',
     TEST + 'test_dac_bound_uses_unit_peak', []),
    ('literal macro amplitude path', PLAN,
     'path = read(doc, ln["amplitude_path"]).get("holder_path")',
     'path = row["path"] + ".flux_pulse_qubit.amplitude"', TEST + 'test_path_is_played_row_leaf', []),
    ('change exact refusal text', PLAN,
     'raise ValueError("no flux-frequency model: freq_vs_flux_01_quad_term is missing")',
     'raise ValueError("flux model missing")', TEST + 'test_exact_scalar_errors', []),
    ('domain error HTTP 400', ROUTE,
     'return jsonify(**result, chip=_active_chip_token() or "")',
     'return jsonify(**result, chip=_active_chip_token() or ""), (200 if result["ok"] else 400)',
     TEST + 'test_route_readonly_domain_200_and_exposure', []),
    ('mutate state during GET', ROUTE,
     'rows, convs, _ = _cz_plan_ops(store)',
     'store.merged["inverse_mutation"] = True\n        rows, convs, _ = _cz_plan_ops(store)',
     TEST + 'test_route_memo_and_no_state_mutation', []),
    ('offset operating marker', CLIENT,
     'y: [shared ? shared.y : ghz(p.frequency_hz)]',
     'y: [shared ? shared.y + 0.01 : ghz(p.frequency_hz)]',
     TEST + 'test_client_table_marker_blob_and_stage', []),
    ('swap official mover rule', MATH,
     'official = high if gap > ah else low', 'official = low if gap > ah else high',
     'tests/test_cz_solve_node30_parity.py', []),
]


def run(test):
    result = subprocess.run([sys.executable, '-m', 'pytest', test, '-q', '-p', 'no:cacheprovider'],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                            env={**os.environ, 'PYTHONUTF8': '1'}, timeout=200)
    return result.returncode, result.stdout + result.stderr


def main():
    records = []
    for name, filename, old, new, test, extra in CASES:
        target = ROOT / filename
        original = target.read_bytes()
        source = original.decode('utf-8').replace('\r\n', '\n')
        assert old in source, name
        changed = source.replace(old, new)
        for a, b in extra:
            assert a in changed
            changed = changed.replace(a, b)
        try:
            target.write_text(changed, encoding='utf-8')
            red, details = run(test)
            assert red == 1 and 'FAILED' in details, (name, details)
        finally:
            target.write_bytes(original)
        green, details = run(test)
        assert green == 0, (name, details)
        record = name + ': RED (assertion failure), reverted, GREEN'
        records.append(record)
        print(record, flush=True)
    (ROOT / 'tests/cz_solve_mutation_results.txt').write_text('\n'.join(records) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
