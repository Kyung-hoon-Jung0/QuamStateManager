"""Four small screen fixes of the gate zone and its neighbours.

* the gate row delete question focus (jsdom: tests/gate_delete_focus_selfcheck.cjs)
* a closed sidebar leaves the tab order (jsdom: tests/sidebar_inert_selfcheck.cjs)
* Save to quam_config shows one busy line (tests/pulse_class_edit_selfcheck.cjs, run by its own test)
* hand-built HTTP JSON answers never carry a bare NaN / Infinity token
"""
from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app, finite_json_dumps

_ROOT = Path(__file__).resolve().parent.parent


def _run_cjs(name: str) -> None:
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(_ROOT / "tests" / name)], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all checks passed" in r.stdout, r.stdout + r.stderr


def test_gate_delete_question_focus():
    _run_cjs("gate_delete_focus_selfcheck.cjs")


def test_closed_sidebar_is_inert():
    _run_cjs("sidebar_inert_selfcheck.cjs")


def test_search_help_button_keeps_the_kit_focus_ring():
    css = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    start = css.index(".ds-search-help-btn:hover")
    rule = css[start:css.index("}", start)]
    assert "outline: none" not in rule and "outline:none" not in rule
    assert ":focus-visible" in rule


def test_finite_json_dumps_replaces_non_finite_by_null():
    text = finite_json_dumps({"a": float("nan"), "b": [float("inf"), 1.5], "c": "NaN"}, separators=(",", ":"))
    assert json.loads(text, parse_constant=lambda c: pytest.fail(c)) == {"a": None, "b": [None, 1.5], "c": "NaN"}
    assert finite_json_dumps({"a": 1.5}) == json.dumps({"a": 1.5})


@pytest.fixture
def client(tmp_path: Path):
    state = {"qubits": {"q1": {"__class__": "quam.components.Transmon", "id": "q1",
                               "f_01": float("nan"), "anharmonicity": float("inf"),
                               "xy": {"intermediate_frequency": 1.0e8}}},
             "qubit_pairs": {}, "active_qubit_names": ["q1"]}
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {"host": "h"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    return c


def _strict(raw: bytes):
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw, parse_constant=lambda c: pytest.fail("bare " + c))


def test_explorer_model_answer_has_no_bare_nan(client):
    data = _strict(client.get("/explorer/model").get_data())
    assert data["ok"] is True
    leaf = data["state"]["qubits"]["q1"]
    assert leaf["f_01"] is None and leaf["anharmonicity"] is None   # no measurement, never 0
    assert leaf["xy"]["intermediate_frequency"] == 1.0e8


def test_all_values_answer_has_no_bare_nan(client):
    resp = client.get("/bulk/all-values")
    assert resp.status_code == 200
    _strict(resp.get_data())


def test_event_header_json_never_carries_a_bare_nan():
    from flask import Response
    from quam_state_manager.web.app import _finite_trigger_headers
    resp = Response("")
    resp.headers["HX-Trigger"] = json.dumps(
        {"stateRestored": {"changes": [{"path": "a.b", "value": float("nan")},
                                       {"path": "a.c", "value": float("-inf")},
                                       {"path": "a.d", "value": 2.5}]}})
    resp.headers["HX-Trigger-After-Settle"] = "NaN-named-event, other"   # a plain list stays as written
    _finite_trigger_headers(resp)
    events = json.loads(resp.headers["HX-Trigger"], parse_constant=lambda c: pytest.fail("bare " + c))
    values = [c["value"] for c in events["stateRestored"]["changes"]]
    assert values == [None, None, 2.5]
    assert resp.headers["HX-Trigger-After-Settle"] == "NaN-named-event, other"


def test_the_app_runs_the_event_header_door(client):
    funcs = client.application.after_request_funcs.get(None, [])
    assert any(f.__name__ == "_finite_trigger_headers" for f in funcs)


@pytest.mark.parametrize("page", ["pair_detail", "qubit_detail"])
def test_detail_pages_load_the_scripts_of_their_list_page(page):
    base = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "templates"
            / "base.html").read_text(encoding="utf-8")
    assert f"'{page}': ['components', 'czgate']" in base
