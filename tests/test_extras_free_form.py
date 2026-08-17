"""``extras`` is the user's own corner of the state (docs/81).

Reported from the field on a real chip: a CZ branch label stored as
``qubit_pairs.<pair>.extras.cz_branch = "02"``.

  * SM warned that it was "stored as text" — but a label IS text, and ``"02"``
    is exactly how a label is written. On that chip those two labels were
    **100% of the type alarm**, so the whole feature was crying wolf.
  * Worse, the value could not be EDITED. Changing it to ``"03"`` came back as
    a 409 offering to convert the label into a number, and the only way
    through was an undocumented quoting escape hatch the Explorer editor does
    not even hint at (it shows a bare ``02``, no quotes).
  * And once past that, the legacy parse+coerce round trip stored ``"3"`` —
    the leading zero silently dropped from a label edit.

The root of all three is one thing: ``extras`` has no schema and never did.
QUAM does not model it; it is where a lab puts its own keys. SM must therefore
not form opinions about the TYPE of what lives there.

The detector and the edit-path offer share ONE definition
(:func:`edit_policy.is_free_form_path`) precisely so the warning and the
repair can never disagree about what counts as text.
"""
from __future__ import annotations

import json

import pytest

from quam_state_manager.core.diagnostics import numeric_string_leaves
from quam_state_manager.core.edit_policy import is_free_form_path
from quam_state_manager.web.app import create_app


def _state():
    return {
        "qubits": {
            "qA1": {"id": "qA1", "f_01": 5.0e9,
                    # a REAL anomaly, outside extras — must still be reported
                    "T1": "1.5e-5",
                    "extras": {"operator_note": "12"}},
        },
        "qubit_pairs": {
            "qA2-qA1": {"id": "qA2-qA1",
                        "extras": {"cz_branch": "02", "tuning_round": "007"}},
        },
        "active_qubit_names": ["qA1"],
        "extras": {"chip_name": "Alpha", "rack_slot": "03"},
    }


@pytest.fixture
def env(tmp_path):
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(
        {"wiring": {}, "network": {"host": "127.0.0.1"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    return {"app": app, "client": c, "chip": chip}


class TestThePredicate:
    def test_extras_is_matched_at_any_depth(self):
        assert is_free_form_path("qubit_pairs.qA2-qA1.extras.cz_branch")
        assert is_free_form_path("extras.chip_name")
        assert is_free_form_path("a.b.extras.c.d")

    def test_ordinary_paths_are_not_free_form(self):
        assert not is_free_form_path("qubits.qA1.f_01")
        assert not is_free_form_path("")

    def test_a_similar_key_name_is_not_extras(self):
        """Segment equality, not substring — ``extras_backup`` is a real key."""
        assert not is_free_form_path("qubits.qA1.extrasomething")
        assert not is_free_form_path("qubits.qA1.extras_backup.x")


class TestTheWarning:
    def test_a_label_in_extras_is_not_an_anomaly(self, env):
        leaves = numeric_string_leaves(_state())
        assert "qubit_pairs.qA2-qA1.extras.cz_branch" not in leaves
        assert "qubits.qA1.extras.operator_note" not in leaves
        assert "extras.rack_slot" not in leaves

    def test_a_real_stored_as_text_number_is_STILL_reported(self, env):
        """The carve-out must not blunt the feature it lives next to."""
        assert "qubits.qA1.T1" in numeric_string_leaves(_state())

    def test_the_repair_plan_does_not_offer_extras(self, env):
        from quam_state_manager.core import type_fix
        ctx = env["app"].config["contexts"][env["app"].config["active_context"]]
        plan = type_fix.build_plan(ctx["store"])
        touched = [r["path"] for r in plan["rows"]] + \
                  [r["path"] for r in plan.get("skipped", [])]
        assert not any(is_free_form_path(p) for p in touched), (
            "extras should not even appear as a refused row — it is not a "
            "candidate at all")
        assert any(r["path"] == "qubits.qA1.T1" for r in plan["rows"])


class TestEditingALabel:
    def _edit(self, client, path, value, **extra):
        return client.post("/field/edit",
                           data={"dot_path": path, "value": value, **extra})

    def test_changing_a_label_is_not_intercepted(self, env):
        """THE reported blocker: every attempt used to come back 409."""
        r = self._edit(env["client"], "qubit_pairs.qA2-qA1.extras.cz_branch", "03")
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        body = r.get_json()
        assert body["ok"] is True
        assert body["stored_kind"] == "str"

    def test_a_leading_zero_survives_the_edit(self, env):
        """The legacy parse+coerce round trip stored "3" — data loss on an
        ordinary label edit, and leading zeros are how labels are written."""
        r = self._edit(env["client"], "qubit_pairs.qA2-qA1.extras.cz_branch", "03")
        assert r.get_json()["stored"] == "03"

    def test_a_bare_number_stays_text_in_extras(self, env):
        r = self._edit(env["client"], "qubit_pairs.qA2-qA1.extras.tuning_round", "8")
        body = r.get_json()
        assert r.status_code == 200 and body["stored_kind"] == "str"
        assert body["stored"] == "8"

    def test_non_numeric_text_still_works(self, env):
        r = self._edit(env["client"], "qubit_pairs.qA2-qA1.extras.cz_branch", "02b")
        assert r.status_code == 200 and r.get_json()["stored"] == "02b"

    def test_the_offer_STILL_fires_outside_extras(self, env):
        """A genuinely string-ified number keeps its repair offer."""
        r = self._edit(env["client"], "qubits.qA1.T1", "2.5e-5")
        assert r.status_code == 409
        assert "stored as TEXT" in r.get_json()["error"]

    def test_a_numeric_extras_value_still_edits_as_a_number(self, env):
        """The carve-out keys on the CURRENT value being text, so a user who
        genuinely stores a number under extras keeps a number."""
        c = env["client"]
        st = json.loads((env["chip"] / "state.json").read_text(encoding="utf-8"))
        st["extras"]["n_rounds"] = 4
        (env["chip"] / "state.json").write_text(json.dumps(st), encoding="utf-8")
        c.post("/state/sync", data={"mode": "discard", "force": "1"})
        r = self._edit(c, "extras.n_rounds", "5")
        assert r.status_code == 200
        assert r.get_json()["stored_kind"] in ("int", "float")


class TestVerbatimDoesNotSwallowTheTokens:
    """The red team caught this the same day it shipped.

    Widening the verbatim carve-out to every non-numeric string leaf returned
    `raw_value` unconditionally, which quietly deleted the three tokens every
    other write path honours:

      'null'   stored the four-character string 'null' — 617 leaves on the real
               chip could no longer be CLEARED at all
      '"high"' stored the quotes too, killing the documented escape hatch
      '  #/q ' stored the padding, so `is_pointer()` said no afterwards and the
               reference was dead — 200 OK, and on disk after apply

    "The characters are the value" was never meant to mean the vocabulary stops
    existing. Verbatim applies to ORDINARY TEXT, after the tokens.
    """

    PATH = "qubits.qA1.extras.operator_note"      # a text leaf holding "12"

    def _edit(self, env, value):
        return env["client"].post("/field/edit",
                                  data={"dot_path": self.PATH, "value": value})

    def _peek(self, env):
        st = (env["app"].config["contexts"]
              [env["app"].config["active_context"]])["store"]
        return st.state["qubits"]["qA1"]["extras"]["operator_note"]

    def test_null_still_clears_a_text_leaf(self, env):
        assert self._edit(env, "null").status_code == 200
        assert self._peek(env) is None

    def test_the_quoted_escape_hatch_still_works(self, env):
        assert self._edit(env, '"high"').status_code == 200
        assert self._peek(env) == "high"

    def test_a_pointer_is_stripped_wherever_it_is_typed(self, env):
        assert self._edit(env, "  #/qubits/qA1/f_01  ").status_code == 200
        assert self._peek(env) == "#/qubits/qA1/f_01", (
            "a padded pointer is not a pointer — the link dies silently")

    def test_ordinary_text_is_still_verbatim(self, env):
        for typed in ("1,03,2", "007", "amplified", "a b  c"):
            assert self._edit(env, typed).status_code == 200
            assert self._peek(env) == typed, typed


class TestATargetlessPointerIsRefused:
    """`#`, `#/`, `#./` satisfy is_pointer() and reference NOTHING, so the
    re-point escape waved them through and the link died anyway — 200 OK, on
    disk after apply. A reference has to reference something."""

    PTR = "qubit_pairs.qA2-qA1.extras.cz_branch"

    def test_a_real_repoint_still_works(self, env):
        c = env["client"]
        assert c.post("/field/edit", data={"dot_path": self.PTR,
                                           "value": "#/qubits/qA1"}).status_code == 200
        r = c.post("/field/edit", data={"dot_path": self.PTR,
                                        "value": "#/qubits/qA1/f_01"})
        assert r.status_code == 200

    def test_targetless_pointers_are_refused(self, env):
        c = env["client"]
        c.post("/field/edit", data={"dot_path": self.PTR, "value": "#/qubits/qA1"})
        for bad in ("#", "#/", "#./", "#../"):
            r = c.post("/field/edit", data={"dot_path": self.PTR, "value": bad})
            assert r.status_code == 400, f"{bad!r} was accepted"
            assert "not a reference to anything" in (r.get_json() or {}).get("error", "")
