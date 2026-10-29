"""Run reversible faults against coupler-only CZ contracts; restore every file."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TEST = 'tests/test_cz_coupler_package.py'
LIVE = 'test_live_build_roundtrip_and_apply'
CAP = 'test_capability_and_validation'
FAULTS = [
    ('require extension', 'quam_state_manager/core/capabilities.py',
     'req.update({"pair.coupler_cz", "pulse.cr_flattop"})', 'req.update({"pulse.cr_flattop"})', CAP),
    ('refuse qubit z', 'quam_state_manager/core/config_generator.py',
     'if any(ln.get("line") == "flux" for ln in lines):', 'if False:', CAP),
    ('require coupler per pair', 'quam_state_manager/core/config_generator.py',
     'if not any(ln.get("line") == "coupler" and ln.get("element") in aliases',
     'if False and not any(ln.get("line") == "coupler" and ln.get("element") in aliases', CAP),
    ('gate operation id', 'quam_state_manager/generator/run_build.py',
     'flat_length=max(0, length - 20), id=key)', 'flat_length=max(0, length - 20), id=None)', LIVE),
    ('reconstruct family', 'quam_state_manager/core/regen_spec.py',
     'families.append("cz_coupler")', 'families.append("cz_tunable")', LIVE),
    ('phase override', 'extensions/qsm_quam_ext/qsm_quam_ext/__init__.py',
     'pair.qubit_control.xy.frame_rotation_2pi(phase_shift_control)',
     'pair.qubit_control.xy.frame_rotation_2pi(self.phase_shift_control)', LIVE),
    ('coupler synchronization', 'extensions/qsm_quam_ext/qsm_quam_ext/__init__.py',
     'names.add(self.coupler.name)', 'pass', LIVE),
    ('duration forwarding', 'extensions/qsm_quam_ext/qsm_quam_ext/__init__.py',
     'duration=self.duration if duration is None else duration,', 'duration=None,', LIVE),
    ('wizard disabled', 'quam_state_manager/web/static/generate.js',
     'opt.disabled = !lf || !_couplerCzAvailable;', 'opt.disabled = !lf;', 'test_wizard_coupler_selfcheck'),
    ('wizard coupler wiring', 'quam_state_manager/web/static/generate.js',
     'var wantCoupler   = lf && (pairGate === "cz_tunable" || pairGate === "cz_coupler");',
     'var wantCoupler   = lf && pairGate === "cz_tunable";', 'test_wizard_coupler_selfcheck'),
    ('build route bypass', 'quam_state_manager/core/capabilities.py',
     '"severity": BLOCKER},\n    "pair.cz_gate":',
     '"severity": DEGRADE},\n    "pair.cz_gate":', 'test_build_route_blocks_missing_extension'),
    ('standalone bypass', 'quam_state_manager/generator/run_build.py',
     'import qsm_quam_ext  # noqa: F401', 'pass', 'test_official_environment_refuses_before_writing'),
]

def check(test=None):
    command = [sys.executable, '-m', 'pytest', TEST + ('::' + test if test else ''), '-q', '-p', 'no:cacheprovider']
    return subprocess.run(command, cwd=ROOT, env={**os.environ, 'PYTHONUTF8': '1'},
                          capture_output=True, text=True, encoding='utf8', timeout=150)


def main():
    rows = []
    green = check()
    if green.returncode:
        print(green.stdout + green.stderr)
        return 1
    rows.append('GREEN baseline: ' + green.stdout.strip().splitlines()[-1])
    for name, file, old, new, test in FAULTS:
        path = ROOT / file
        original = path.read_bytes()
        source = original.decode('utf8')
        if source.count(old) == 0 and '\r\n' in source:
            old, new = old.replace('\n', '\r\n'), new.replace('\n', '\r\n')
        assert source.count(old) == 1, (name, source.count(old))
        try:
            path.write_bytes(source.replace(old, new).encode('utf8'))
            result = check(test)
            assert result.returncode == 1 and 'failed' in result.stdout, (name, result.stdout, result.stderr)
            rows.append('RED caught: ' + name)
        finally:
            path.write_bytes(original)
        print(rows[-1], flush=True)
    green = check()
    assert green.returncode == 0, green.stdout + green.stderr
    rows.append('GREEN restored: ' + green.stdout.strip().splitlines()[-1])
    (ROOT / 'tests/cz_a4_mutation_results.txt').write_text('\n'.join(rows) + '\n', encoding='utf8')
    print(rows[-1])
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
