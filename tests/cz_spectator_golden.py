"""The plan / create answers and the written state of gates made WITHOUT
spectators, through the dialog routes with a fake check runner. The golden
file was taken before spectators existed; a request without spectators must
keep giving exactly these bytes."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from quam_state_manager.core import cz_gate_build as cgb

GOLDEN = Path(__file__).parent / "golden" / "cz_gate_no_spectators.json"
SQUARE = "quam.components.pulses.SquarePulse"

#: answer keys that change from run to run (ids, html, sync state, records)
VOLATILE = ("group", "tray_html", "sync", "record", "record_error")


def _bodies():
    sq = {"flux_pulse_qubit": {"class": SQUARE, "fields": {"length": 48, "amplitude": 0.2}}}
    both = dict(sq, coupler_flux_pulse={"class": SQUARE,
                                        "fields": {"length": 48, "amplitude": 0.05}})
    return [
        {"pairs": ["q1-2"], "gate_name": "cz_sq", "gate_class": cgb.CZ_GATE, "pulses": sq},
        {"pairs": ["q1-2", "q3-4"], "gate_name": "cz_two", "gate_class": cgb.CZ_GATE,
         "pulses": both},
        {"pairs": ["q2-3"], "gate_name": "cz_cp", "gate_class": cgb.COUPLER_CZ_GATE,
         "pulses": {"coupler_flux_pulse": {"class": SQUARE,
                                           "fields": {"length": 48, "amplitude": 0.05}}}},
    ]


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def collect(monkeypatch) -> dict:
    """``{case: {"plan", "create", "state"}}`` for every body above."""
    from tests import test_cz_gate_routes as T
    from quam_state_manager.web import cz_gate_routes as czr
    from quam_state_manager.core.hub_index import close_readers
    out = {}
    for i, body in enumerate(_bodies()):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            a = T.App(tmp)
            monkeypatch.setattr(czr, "run_check", T._fake())
            monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
            plan = a.c.post(T.PLAN, json=body).get_json()
            chk = a.c.post(T.CHECK, json=body).get_json()
            made = a.c.post(T.CREATE, json={"check_token": chk["check_token"],
                                            "plan_sig": chk["plan_sig"]}).get_json()
            out[str(i)] = {"plan": _strip(plan), "create": _strip(made),
                           "state": json.dumps(a.store.state)}
            close_readers()
    return out


def dump(data: dict) -> str:
    return json.dumps(data, indent=1, sort_keys=True)
