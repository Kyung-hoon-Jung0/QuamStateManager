"""Editing a field the entity does not carry YET (docs/88).

The report, verbatim: typing into ``lo_mode`` in Live State Edit answered

    Parent at 'qubits.qC4.resonator.opx_input.lo_mode' is str, not dict or list

for every value, so the cell was permanently uneditable.

The chain: ``qubits.<q>.resonator.opx_input`` is a POINTER to a port dict, and
that port does not carry ``lo_mode`` while its siblings do — which is precisely
why the grid derives the column at all. ``resolve_field_target`` dead-ended,
``resolve_edit_path`` fell back to the RAW path, and the modifier then tried to
walk into the pointer STRING. Both halves are fixed here: the resolver returns
``<resolved parent>.<leaf>``, and the edit choke points create a leaf whose
parent exists — the promise the grid makes is the promise the backend keeps.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.edit_policy import (
    leaf_is_absent,
    resolve_edit_path,
    resolve_missing_leaf_path,
)
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state():
    """Two qubits behind ONE pointer hop each; only qA1's port has lo_mode —
    the real shape (KRISS_CR: 4 of N mw_input ports carry it)."""
    return {
        "qubits": {
            "qA1": {"id": "qA1", "f_01": 5.0e9, "resonator": {
                "opx_input": "#/ports/mw_inputs/con1/1/1"}},
            "qA2": {"id": "qA2", "f_01": 5.1e9, "resonator": {
                "opx_input": "#/ports/mw_inputs/con1/2/1"}},
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1", "qA2"],
        "ports": {"mw_inputs": {"con1": {
            "1": {"1": {"band": 1, "downconverter_frequency": 6.0e9,
                        "lo_mode": "always_on"}},
            "2": {"1": {"band": 1, "downconverter_frequency": 6.1e9}},
        }}},
    }


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    ctx = next(iter(app.config["contexts"].values()))
    return {"app": app, "client": c, "ctx": ctx, "store": ctx["store"], "live": live}


def _port2(env) -> dict:
    return env["store"].merged["ports"]["mw_inputs"]["con1"]["2"]["1"]


MISSING = "qubits.qA2.resonator.opx_input.lo_mode"
PRESENT = "qubits.qA1.resonator.opx_input.lo_mode"


class TestResolver:
    def test_present_leaf_resolves_through_the_pointer(self, env):
        assert resolve_edit_path(env["store"], PRESENT) == \
            "ports.mw_inputs.con1.1.1.lo_mode"

    def test_missing_leaf_targets_the_resolved_parent_not_the_raw_path(self, env):
        """THE bug: this used to return the raw path, whose parent is a pointer
        string, which is what produced 'Parent at ... is str, not dict or list'."""
        got = resolve_edit_path(env["store"], MISSING)
        assert got == "ports.mw_inputs.con1.2.1.lo_mode"
        assert "opx_input" not in got, "must not keep the pointer segment"

    def test_helper_is_precise(self, env):
        st = env["store"]
        assert resolve_missing_leaf_path(st, MISSING) == \
            "ports.mw_inputs.con1.2.1.lo_mode"
        # already there → not a missing leaf
        assert resolve_missing_leaf_path(st, PRESENT) is None
        # parent itself absent → not this case (a whole new subtree)
        assert resolve_missing_leaf_path(
            st, "qubits.qA2.nope.lo_mode") is None
        assert leaf_is_absent(st, "ports.mw_inputs.con1.2.1.lo_mode") is True
        assert leaf_is_absent(st, "ports.mw_inputs.con1.1.1.lo_mode") is False


def _grid_commit(client, dot_path, value, create=True):
    """What the Live-Edit grids post: the RAW alias path, plus ``create`` when
    the server rendered that cell "not set" (``data-missing``)."""
    up = {"dot_path": dot_path, "value": value}
    if create:
        up["create"] = True
    return client.post("/field/edit-batch",
                       json={"updates": [up], "expect_chip": ""})


class TestEditingFillsItIn:
    def test_the_grid_commit_writes_the_string(self, env):
        r = _grid_commit(env["client"], MISSING, "always_on")
        body = r.get_json()
        assert r.status_code == 200 and body["ok"] is True, body
        assert body["results"][0]["applied"] is True
        assert body["results"][0]["resolved_path"] == \
            "ports.mw_inputs.con1.2.1.lo_mode"
        assert _port2(env)["lo_mode"] == "always_on"

    def test_a_typo_is_still_just_a_string(self, env):
        """The reporter tried both spellings — SM has no opinion about the
        VALUE here, only about the type, so both must land verbatim."""
        assert _grid_commit(env["client"], MISSING, "alway_on").status_code == 200
        stored = _port2(env)["lo_mode"]
        assert stored == "alway_on" and isinstance(stored, str)

    def test_it_is_one_undoable_change(self, env):
        c = env["client"]
        _grid_commit(c, MISSING, "always_on")
        assert _port2(env)["lo_mode"] == "always_on"
        assert c.post("/undo").status_code == 200
        assert "lo_mode" not in _port2(env), "undo must remove the created key"

    def test_a_missing_PARENT_still_refuses(self, env):
        """Filling in a leaf of a known object is one thing; inventing a whole
        subtree from a cell edit is another, and must stay an error."""
        r = _grid_commit(env["client"],
                         "qubits.qA2.resonator.nosuch.lo_mode", "x")
        assert r.status_code == 400

    def test_a_plain_missing_leaf_also_fills_in(self, env):
        """Same bug class without a pointer: the grid offers T1 because qA1 has
        it, so typing into qA2's cell must work too."""
        r = _grid_commit(env["client"], "qubits.qA2.T1", "1.5e-5")
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        assert env["store"].merged["qubits"]["qA2"]["T1"] == 1.5e-5

    def test_existing_values_are_untouched(self, env):
        _grid_commit(env["client"], MISSING, "always_on")
        p1 = env["store"].merged["ports"]["mw_inputs"]["con1"]["1"]["1"]
        assert p1["lo_mode"] == "always_on"      # the sibling that already had it
        assert _port2(env)["band"] == 1          # neighbours in the same port
        assert _port2(env)["downconverter_frequency"] == 6.1e9


class TestCreationStaysDeclared:
    """The standing invariant this fix must NOT trade away: creation is asked
    for, never inferred, so a generic bulk/plot edit can't quietly bring a
    mistyped path into existence."""

    def test_without_the_flag_it_still_refuses(self, env):
        r = _grid_commit(env["client"], MISSING, "always_on", create=False)
        assert r.status_code == 400
        assert "lo_mode" not in _port2(env)

    def test_single_field_edit_never_creates(self, env):
        """/field/edit has no create flag and no surface that renders a missing
        leaf as editable — it must keep refusing rather than grow a silent
        creation path."""
        r = env["client"].post("/field/edit",
                               data={"dot_path": MISSING, "value": "always_on"})
        assert r.status_code == 400
        assert "lo_mode" not in _port2(env)

    def test_the_error_is_no_longer_gibberish(self, env):
        """Even when it refuses, it must not talk about walking into a pointer:
        the resolver now names the REAL target, so the message is about a key
        that is missing — not about a str that is 'not dict or list'."""
        body = env["client"].post(
            "/field/edit", data={"dot_path": MISSING, "value": "x"}
        ).get_data(as_text=True)
        assert "not dict or list" not in body
        assert "opx_input" not in body or "lo_mode" in body


class TestGridsDeclareIt:
    def test_missing_cells_are_marked_for_the_client(self, env):
        """The server marks the cell; the grids turn that mark into
        ``create: true``. Without the mark the fix cannot reach the user."""
        html = env["client"].get("/bulk", headers={"HX-Request": "true"}).get_data(
            as_text=True)
        assert 'data-missing="1"' in html

    def test_both_grids_send_create_for_a_marked_cell(self):
        root = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"
        for name in ("bulk-edit.js", "pair-edit.js"):
            src = (root / name).read_text(encoding="utf-8")
            assert "data-missing" in src, name
            assert "up.create = true" in src, name
