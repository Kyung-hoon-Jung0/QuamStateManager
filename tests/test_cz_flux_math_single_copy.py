"""Source pins and the inspector's signed-coefficient compatibility contract.

Mutation audit (2026-10-29), each reverted before its green rerun:
* Duplicate arc in cz_gate_plan: source pin 1 failed, then 1 passed.
* Inspector sign from flux_point: sign pin 3 failed, then 6 passed.
* Q25 character changed: quote pin 1 failed, then 1 passed.
* Arc multiplied by (1 + 1e-9): equivalence pin 1 failed, then 1 passed.
"""
import ast
from pathlib import Path
import re

import numpy as np
import pytest

from quam_state_manager.core import gate_inspector as G

ROOT = Path(__file__).resolve().parents[1]
# Unrelated waveform cosine windows are allowed only in this exact file.
COSINE_WINDOWS = {"quam_state_manager/core/waveform_synth.py"}


def code_text(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # Exclude documentation and string literals from formula searches.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = ""
    return ast.unparse(tree)


def test_formulas_have_one_implementation():
    violations = []
    for path in (ROOT / "quam_state_manager").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "quam_state_manager/core/cz_flux_math.py":
            continue
        source = code_text(path)
        arc = (re.search(r"(?:np|math)\.cos\([^\n]*(?:np|math)\.pi", source)
               and re.search(r"\*\*\s*0\.25", source))
        quad = re.search(r"\bquad\s*\*\s*\(", source)
        inverse = re.search(r"sqrt\(\s*-\s*det", source)
        if (arc and rel not in COSINE_WINDOWS) or quad or inverse:
            violations.append(rel)
    assert not violations, violations


@pytest.mark.parametrize("coefficient", [-1e9, 1e9])
@pytest.mark.parametrize("mode", ["joint", "min", "independent"])
def test_inspector_sign_is_the_coefficient_sign(coefficient, mode):
    # Deliberately vary the evidence mode independently of the coefficient.
    control = {"name": "q1", "f_01": 5.2e9, "anharmonicity": 250e6,
               "quad_term": coefficient, "flux_point": mode,
               "idle_voltage": 0.05, "idle_source": "z.joint_offset"}
    target = dict(control, name="q2", f_01=4.8e9, anharmonicity=230e6)
    for role in ("control", "target"):
        result = G.compute_detuning_sweep(control, target, role, n_points=51)
        moving = control if role == "control" else target
        x = np.asarray(result["x"])
        expected = moving["f_01"] + coefficient * (x - moving["idle_voltage"])**2
        np.testing.assert_array_equal(result["frequencies"], expected)
        assert min(x) < moving["idle_voltage"] < max(x)
        for zero in result["zeros"]:
            np.testing.assert_allclose(moving["f_01"] + coefficient * (zero["voltage"] - 0.05)**2,
                                       zero["frequency"], rtol=0, atol=1e-6)
