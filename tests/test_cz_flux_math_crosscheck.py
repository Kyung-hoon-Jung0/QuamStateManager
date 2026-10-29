"""C-IMPL: compare the extracted math to independently maintained checks."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core import cz_flux_math as M

CHECK_ROOT = Path(os.environ.get("CZ_SPEC_ROOT", "D:/work_laptop/study/2026-10-29_qsm-v160-release/cz"))


class CheckOutput(io.StringIO):
    def reconfigure(self, **kwargs):
        pass


def load_check(relative):
    path = CHECK_ROOT / relative
    if not path.is_file():
        pytest.skip(f"Independent check script is absent: {path}")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(CheckOutput()):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checks():
    return (load_check("literature/checks/derived_checks.py"),
            load_check("checks/spec_checks.py"))


def test_two_hundred_seeded_independent_parameter_sets(checks):
    derived, spec = checks
    import sympy as sp
    # D4's coefficient is obtained from its independent symbolic series.
    coefficient = sp.lambdify((derived.F, derived.Ec, derived.d), derived.quad, "numpy")
    rng = np.random.default_rng(160)
    for _ in range(200):
        fmax, ec = rng.uniform(4e9, 8e9), rng.uniform(0.1e9, 0.4e9)
        d, phi = rng.uniform(0.05, 0.95), rng.uniform(-2, 2)
        model = {"f_max_hz": fmax, "ec_hz": ec, "d": d}
        actual = M.arc_frequency(model, phi)
        np.testing.assert_allclose(actual, derived.f_model(phi, fmax, ec, d), rtol=1e-6)
        np.testing.assert_allclose(actual, spec.F(phi, fmax, ec, d), rtol=1e-6)
        q = M.arc_quad_coefficient(fmax, ec, d)
        np.testing.assert_allclose(q, coefficient(fmax, ec, d), rtol=1e-6)
        amplitude = rng.uniform(-0.8, 0.8)
        shift = M.quad_shift(amplitude, abs(q), -1)
        np.testing.assert_allclose(derived.inv_quad(-shift, q), abs(amplitude), rtol=1e-6)

        fc, ft = rng.uniform(4e9, 7e9, 2)
        ac, at = rng.uniform(0.1e9, 0.4e9, 2)
        # D5's independently built Duffing Hamiltonian gives the bare energies.
        energy = derived.label_energies(derived.H((fc, ft, 9e9), (-ac, -at, -ec), {}))
        expected = (energy(2, 0, 0) - energy(1, 1, 0),
                    energy(1, 1, 0) - energy(0, 2, 0),
                    energy(1, 0, 0) - energy(0, 1, 0))
        np.testing.assert_allclose([r["distance_hz"] for r in M.resonances(fc, ft, -ac, at)],
                                   expected, rtol=1e-6, atol=1e-5)
