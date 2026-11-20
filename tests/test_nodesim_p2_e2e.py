"""P2 merge gate: missing parallel pieces must fail, never skip (P2 spec 5, E-1).

The killed-owner/spawn ledger scenario remains an integration handoff until W and K are merged.
This dependency pin prevents an isolated S run from claiming the complete gate is green.
"""
from pathlib import Path


def test_e1_merge_dependencies_are_present():
    """Fail explicitly on absent real worker, broker or fake SaaS (D-10, P2 spec 5 E-1)."""
    root = Path(__file__).resolve().parents[1]
    required = ("quam_state_manager/generator/nodesim_warm.py",
                "quam_state_manager/generator/nodesim_broker.py",
                "tests/fakes/fake_qm_saas/qm_saas/__init__.py")
    missing = [p for p in required if not (root / p).is_file()]
    assert not missing, "E-1 requires merged W and K; missing: " + ", ".join(missing)
