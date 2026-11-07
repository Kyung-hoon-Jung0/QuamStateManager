"""Sync callbacks must not own retired application graphs."""
from __future__ import annotations

import gc
import json
import weakref

from quam_state_manager.web.app import create_app


def test_loaded_apps_release_retired_owners(tmp_path, monkeypatch):
    from quam_state_manager.web import routes

    # Isolate registry ownership from pending background work.
    monkeypatch.setattr(routes, "_prewarm_search_index", lambda ctx: None)

    def load(index):
        root = tmp_path / str(index)
        live = root / "live"
        live.mkdir(parents=True)
        (live / "state.json").write_text(json.dumps({
            "qubits": {"q1": {"id": "q1", "T1": 0.00002}},
            "qubit_pairs": {}, "active_qubit_names": ["q1"],
        }), encoding="utf-8")
        (live / "wiring.json").write_text("{}", encoding="utf-8")
        app = create_app(testing=True, instance_path=str(root / "instance"))
        with app.test_client() as client:
            with client.post("/load", data={"folder": str(live)}) as response:
                assert response.status_code in (200, 302)
            assert app.config["active_context"] is not None
        return weakref.ref(app)

    refs = [load(index) for index in range(8)]
    gc.collect()
    assert all(ref() is None for ref in refs[:-1])
