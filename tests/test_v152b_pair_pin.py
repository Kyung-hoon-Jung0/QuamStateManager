"""The public pair-parameter pin must kill disabled pair selection."""
import quam_state_manager.web.routes as routes
from tests import test_topology_pairs as pairs


def test_pair_parameter_pin_rejects_a_disabled_selector(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_pair_cz_plan_ops", lambda store: {})
    rejected = False
    try:
        pairs.test_cz_plan_accepts_a_pair_id(tmp_path)
    except AssertionError:
        rejected = True
    assert rejected, "the pair pin must reject a disabled selector"
