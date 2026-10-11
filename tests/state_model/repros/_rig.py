"""A small synthetic chip opened through the real app, for the repros."""
from __future__ import annotations

import json
import os
from pathlib import Path

from tests import _ram_chip


class Chip:
    def __init__(self, tmp: Path, *, n_qubits: int = 3, seed: int = 1):
        from quam_state_manager.core import hub
        from quam_state_manager.web import routes
        from quam_state_manager.web.app import create_app
        hub.set_inline(True)
        routes._quam_cache.clear()
        self.tmp = Path(tmp)
        self.live = self.tmp / "chips" / "live"
        self.live.mkdir(parents=True)
        st, wi = _ram_chip.build(n_qubits, seed)
        self.write(st, wi)
        self.inst = self.tmp / "_inst"
        self.app = create_app(testing=True, instance_path=str(self.inst))
        self.c = self.app.test_client()
        r = self.c.post("/load", data={"folder": str(self.live)}, follow_redirects=True)
        assert r.status_code in (200, 302), r.get_data(as_text=True)[:300]
        self.load_html = r.get_data(as_text=True)

    # -- files ---------------------------------------------------------
    def write(self, state: dict, wiring: dict | None = None):
        for name, doc in (("state.json", state), ("wiring.json", wiring)):
            if doc is None:
                continue
            tmp = self.live / (name + ".tmp")
            tmp.write_text(json.dumps(doc), encoding="utf-8")
            os.replace(tmp, self.live / name)

    def live_state(self) -> dict:
        return json.loads((self.live / "state.json").read_text(encoding="utf-8"))

    def set_live(self, path: str, value):
        doc = self.live_state()
        cur = doc
        parts = path.split(".")
        for s in parts[:-1]:
            cur = cur[s]
        cur[parts[-1]] = value
        self.write(doc)

    # -- SM ------------------------------------------------------------
    @property
    def ctx(self) -> dict:
        return next(v for v in self.app.config["contexts"].values() if v.get("type") == "quam")

    def edit(self, path: str, value: str, **extra):
        r = self.c.post("/field/edit", data={"dot_path": path, "value": value, **extra})
        assert r.status_code == 200 and (r.get_json() or {}).get("ok"), r.get_data(as_text=True)[:300]
        return r

    def log(self) -> list:
        return [(e.dot_path, e.new_value) for e in self.ctx["store"].change_log]

    def status(self) -> str:
        body = {}
        for _ in range(3):
            body = self.c.get("/state/drift").get_json() or {}
        return (body.get("sync") or {}).get("state")

    def journal(self):
        from quam_state_manager.core import undo_journal
        return undo_journal.load_state(undo_journal.sidecar_path(self.app.instance_path, self.ctx["path"]))

    def trigger(self, r) -> dict:
        raw = r.headers.get("HX-Trigger")
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    def history_dirs(self) -> list[str]:
        return sorted(p.parent.name for p in (self.inst / "history").glob("*/events.jsonl"))
