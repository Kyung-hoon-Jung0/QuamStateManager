"""S3L-bound: finite grid against an independent product-basis Hamiltonian."""
import itertools

import numpy as np
import pytest

from quam_state_manager.core import cz_flux_math as M


BOUND = 0.0016  # 0.16 percent; scope and derivation in checks/S3L_bound.md.


def independent_shift(fq, fc, aq, ac, g):
    # Assemble by basis transitions, independently of production tensor code.
    basis = list(itertools.product(range(3), repeat=2))
    h = np.zeros((9, 9))
    for i, (q, c) in enumerate(basis):
        h[i, i] = fq*q + aq*q*(q-1)/2 + fc*c + ac*c*(c-1)/2
        for dq, dc in itertools.product((-1, 1), repeat=2):
            other = (q+dq, c+dc)
            if other in basis:
                h[i, basis.index(other)] = g*np.sqrt(max(q, q+dq)*max(c, c+dc))
    e, v = np.linalg.eigh(h)
    return e[np.argmax(v[3]**2)] - e[np.argmax(v[0]**2)] - fq


def grid():
    return itertools.product(
        np.arange(4, 7.01, .5)*1e9,
        np.array([4, 4.5, 5, 5.5, 5.6, 6, 6.2, 6.5, 7, 7.5, 8])*1e9,
        -np.arange(150, 351, 50)*1e6,
        -np.arange(50, 301, 50)*1e6,
        np.arange(1, 11)/100,
    )


def test_spec_s3l_case():
    # Exactly spec_checks.py S3-L, including alpha_q=-200 MHz.
    fq, g, ratio = 5e9, 60e6, .1
    fc, aq, ac = fq + g/ratio, -200e6, -100e6
    exact = independent_shift(fq, fc, aq, ac, g)
    old = g*g/(fq-fc) - g*g/(fq+fc)
    assert abs(old-exact)/abs(exact) > .007
    assert M.lamb_shift(fq, fc, g, aq, ac) == pytest.approx(exact, rel=BOUND)


def test_s3l_same_grid_bound():
    maximum, count, signs = 0.0, 0, set()
    for fq, fc, aq, ac, ratio in grid():
        if fq == fc:
            continue
        g = ratio*abs(fq-fc)
        exact = independent_shift(fq, fc, aq, ac, g)
        estimated = M.lamb_shift(fq, fc, g, aq, ac)
        maximum = max(maximum, abs(estimated-exact)/abs(exact))
        signs.add(np.sign(fq-fc))
        count += 1
    assert count == 21000 and signs == {-1, 1}
    assert maximum <= BOUND, maximum
