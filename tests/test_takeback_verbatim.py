"""docs/255 P3, the write-failure path: when the live write itself fails after the
save, ``agent_api._take_back_saved`` writes each leaf's old value back. A coercing
write cast the old int to the field's NEW (widened) type, so 5000000000 came back
as 5000000000.0 -- a drift a later push then carried to the live chip."""

from __future__ import annotations

import json
from pathlib import Path

from quam_state_manager.web.app import create_app


def _chip(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    st = {"qubits": {"q1": {"id": "q1", "f_01": 5000000000}}, "qubit_pairs": {},
          "active_qubit_names": ["q1"]}
    (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.1", "port": 1},
                                                    "wiring": {"qubits": {}}}), encoding="utf-8")


def test_the_take_back_writes_the_old_value_verbatim(tmp_path):
    folder = tmp_path / "quam_state"
    _chip(folder)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    from quam_state_manager.web import agent_api as aa
    from quam_state_manager.web import routes as r
    with app.test_request_context("/"):
        ctx = r._active_ctx()
        store, mod, saver = ctx["store"], ctx["modifier"], ctx["saver"]
        before = {"log": list(store.change_log), "dirty": ctx.get("working_dirty"),
                  "reapply": ctx.get("pending_reapply"), "reapply_orig": ctx.get("pending_reapply_orig"),
                  "units": None}
        e = mod.set_value("qubits.q1.f_01", 5000000001.5, group_id="g-take")
        assert isinstance(store.get_value("qubits.q1.f_01"), float), "setup: the int field widened"
        saver.save()
        assert aa._take_back_saved(r, ctx, [e], before) is None
        v = store.get_value("qubits.q1.f_01")
        assert v == 5000000000 and type(v) is int, (v, type(v))
    on_disk = json.loads((Path(ctx["working_copy"].working_folder) / "state.json").read_text(encoding="utf-8"))
    assert on_disk["qubits"]["q1"]["f_01"] == 5000000000 and isinstance(on_disk["qubits"]["q1"]["f_01"], int)
