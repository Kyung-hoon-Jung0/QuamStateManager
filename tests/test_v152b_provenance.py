"""Current metric writers require exact-path write evidence."""
from tests.test_topology_pairs import _chip, _edge
import quam_state_manager.web.routes as routes


def test_load_id_and_manual_edits_never_prove_a_writer(tmp_path):
    client = _chip(tmp_path, {"InterleavedRB": .90, "InterleavedRB_load_id": 412})
    assert _edge(client)["cz_fidelity_run"] is None
    response = client.post("/field/edit", data={"dot_path": "qubit_pairs.q1-q2.macros.cz.fidelity.InterleavedRB", "value": "0.999"})
    assert response.status_code == 200
    assert _edge(client)["cz_fidelity"] == .999
    assert _edge(client)["cz_fidelity_run"] is None


def test_exact_selected_path_and_current_value_are_required(tmp_path, monkeypatch):
    client = _chip(tmp_path, {"InterleavedRB": .90, "InterleavedRB_load_id": 412, "IRB": .999})
    assert _edge(client)["cz_fidelity_run"] is None
    path = "qubit_pairs.q1-q2.macros.cz.fidelity.IRB"
    point = dict(provenance="run_proven", proven=True, value=.999, run_id=413)
    def history(ctx, paths, **kw):
        assert list(paths) == [path]
        return dict(mode="ledger", rows={path: {"points": [dict(point)]}})
    monkeypatch.setattr(routes, "_value_history", history)
    assert _edge(client)["cz_fidelity_run"] == 413
    for change in ({"value": .90}, {"provenance": "sm"}, {"proven": False}, {"removed": True}, {"undone": True}):
        before = dict(point); point.update(change)
        assert _edge(client)["cz_fidelity_run"] is None
        point.clear(); point.update(before)
    with client.application.app_context():
        ctx = routes._active_ctx()
    for key, value in (("working_dirty", True), ("pending_reapply", {path: .999})):
        original = ctx.get(key)
        ctx[key] = value
        assert _edge(client)["cz_fidelity_run"] is None
        ctx[key] = original
    # A staged same-value write is still manual, not new evidence for the run.
    response = client.post("/field/edit", data={"dot_path": path, "value": "0.999"})
    assert response.status_code == 200
    ctx["working_dirty"] = False
    ctx["pending_reapply"] = None
    assert _edge(client)["cz_fidelity_run"] is None
