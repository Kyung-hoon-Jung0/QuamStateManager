"""One-click repair for numbers stored as TEXT (docs/77).

SM already detected the anomaly and warned about it (r14, docs/56 amendment);
repairing it meant visiting every field and retyping the value. These pin the
button that replaces that: SM proposes (value + resulting type, per field),
the user confirms once, and the whole repair lands as ONE change group in the
working copy.

The interesting half is what the plan REFUSES to convert — a fix that guesses
wrong is worse than no fix, and one that silently skips is worse still.
"""
import json
import re

import pytest

from quam_state_manager.core import type_fix
from quam_state_manager.web.app import create_app

# q1 carries one of every shape the plan has to reason about.
_STATE = {
    "qubits": {
        "q1": {
            "id": "1",                                  # identity key — read-only
            "f_01": "4830000000.0",                     # convert → real
            "T1": "8834",                               # convert → int
            "grid_location": "4,8",                     # a pair, not a number
            "slot": "02",                               # label, not a number
            "flux_point": "joint",                      # never numeric
            "thermalization_factor": 5,                 # already a number
            "xy": {
                "RF_frequency": 6.25e9,
                "opx_output": "#/wiring/qubits/q1/xy/opx_output",   # pointer
                "operations": {"saturation": {"amplitude": "0.13"}},  # convert
            },
        }
    },
    "active_qubit_names": ["q1"],
}
_WIRING = {"wiring": {"qubits": {"q1": {"xy": {"opx_output": "MW-FEM/1/2"}}}},
           "network": {"host": "127.0.0.1"}}

_CONVERTIBLE = {
    "qubits.q1.f_01",
    "qubits.q1.T1",
    "qubits.q1.xy.operations.saturation.amplitude",
}


@pytest.fixture
def chip(tmp_path):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_STATE), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    return folder


@pytest.fixture
def app(tmp_path, chip):
    created = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    created.config["_chip_folder"] = str(chip)
    return created


@pytest.fixture
def client(app):
    c = app.test_client()
    c.post("/load", data={"folder": app.config["_chip_folder"]})
    return c


def _ctx(app):
    return app.config["contexts"][app.config["active_context"]]


def _plan(client):
    html = client.get("/type-fix/plan").get_data(as_text=True)
    paths = re.findall(r'class="tfx-pick"[^>]*data-path="([^"]+)"', html)
    if not paths:                       # attribute order is template-dependent
        paths = re.findall(r'data-path="([^"]+)"', html)
    sig = re.search(r'data-sig="([^"]+)"', html)
    return html, paths, (sig.group(1) if sig else "")


class TestWhatThePlanProposes:
    def test_only_unambiguous_numbers_are_offered(self, client):
        _, paths, _ = _plan(client)
        assert set(paths) == _CONVERTIBLE

    def test_int_stays_int_and_real_stays_real(self, app, client):
        plan = type_fix.build_plan(_ctx(app)["store"])
        by = {r["path"]: r for r in plan["rows"]}
        assert by["qubits.q1.T1"]["proposed_type"] == "int"
        assert by["qubits.q1.T1"]["proposed_value"] == 8834
        assert by["qubits.q1.f_01"]["proposed_type"] == "real"
        assert by["qubits.q1.f_01"]["proposed_value"] == 4830000000.0

    def test_the_number_itself_never_changes(self):
        # a conversion may only ever change the stored TYPE
        for text, want in (("0.13", 0.13), ("8834", 8834), ("1e9", 1e9),
                           ("-2.5", -2.5), ("  7 ", 7)):
            assert type_fix._parse_plain(text) == want

    def test_current_value_is_shown_as_text(self, app, client):
        plan = type_fix.build_plan(_ctx(app)["store"])
        by = {r["path"]: r for r in plan["rows"]}
        assert by["qubits.q1.f_01"]["current_display"] == '"4830000000.0"'


class TestWhatThePlanRefuses:
    """Every refusal is listed with a reason — never silently dropped."""

    def _skips(self, app):
        return {s["path"]: s["reason"]
                for s in type_fix.build_plan(_ctx(app)["store"])["skipped"]}

    def test_identity_key_is_left_alone(self, app, client):
        assert "read-only" in self._skips(app)["qubits.q1.id"]

    def test_leading_zero_looks_like_a_label(self, app, client):
        assert "leading zero" in self._skips(app)["qubits.q1.slot"]

    def test_a_pair_is_not_a_number(self, app, client):
        # "4,8" never even reaches the parser (float() rejects it), so it is
        # not in the anomaly set at all — the important thing is that no plan
        # row proposes 48 for it.
        _, paths, _ = _plan(client)
        assert "qubits.q1.grid_location" not in paths

    def test_separator_rule_holds_if_such_a_value_is_fed_in(self, app, client):
        plan = type_fix.build_plan(_ctx(app)["store"],
                                   paths=["qubits.q1.grid_location"])
        assert plan["rows"] == []
        assert "separator" in plan["skipped"][0]["reason"]

    def test_env_typed_text_field_is_left_alone(self, app, client):
        store = _ctx(app)["store"]

        class _Exp:
            enforced = True
            spec = {"base": "str"}

        class _Policy:
            def expected_for(self, merged, path, infer=True):
                return _Exp() if path == "qubits.q1.T1" else None

        plan = type_fix.build_plan(store, policy=_Policy())
        skips = {s["path"]: s["reason"] for s in plan["skipped"]}
        assert "schema types this field as text" in skips["qubits.q1.T1"]
        assert "qubits.q1.T1" not in {r["path"] for r in plan["rows"]}

    def test_env_typed_numeric_field_needs_no_assignment(self, app, client):
        store = _ctx(app)["store"]

        class _Exp:
            enforced = True
            spec = {"base": "float"}

        class _Policy:
            def expected_for(self, merged, path, infer=True):
                return _Exp() if path == "qubits.q1.f_01" else None

        plan = type_fix.build_plan(store, policy=_Policy())
        by = {r["path"]: r for r in plan["rows"]}
        assert by["qubits.q1.f_01"]["needs_assignment"] is False
        assert by["qubits.q1.T1"]["needs_assignment"] is True

    def test_pointers_and_plain_strings_are_never_candidates(self, client):
        _, paths, _ = _plan(client)
        assert "qubits.q1.xy.opx_output" not in paths
        assert "qubits.q1.flux_point" not in paths


class TestTheDialogIsHonest:
    def test_plan_lists_both_what_it_will_and_will_not_touch(self, client):
        html, _, _ = _plan(client)
        assert "stored as <strong>text</strong>" in html
        assert "qubits.q1.id" in html and "read-only" in html
        assert "leading zero" in html
        # and it says where the result goes
        assert "working copy" in html

    def test_it_shows_the_resulting_type_per_row(self, client):
        html, _, _ = _plan(client)
        assert "tfx-type-chip" in html
        assert ">int<" in html and ">real<" in html


class TestApply:
    def test_converts_selected_fields_to_real_numbers(self, app, client):
        _, paths, sig = _plan(client)
        r = client.post("/type-fix/apply", json={"paths": paths, "sig": sig})
        assert r.status_code == 200, r.get_data(as_text=True)[:400]
        body = r.get_json()
        assert body["ok"] and body["count"] == 3

        store = _ctx(app)["store"]
        assert store.get_value("qubits.q1.f_01") == 4830000000.0
        assert isinstance(store.get_value("qubits.q1.f_01"), float)
        assert store.get_value("qubits.q1.T1") == 8834
        assert isinstance(store.get_value("qubits.q1.T1"), int)
        assert store.get_value("qubits.q1.xy.operations.saturation.amplitude") == 0.13

    def test_the_whole_repair_is_one_undo(self, app, client):
        _, paths, sig = _plan(client)
        client.post("/type-fix/apply", json={"paths": paths, "sig": sig})
        store = _ctx(app)["store"]
        gids = {e.group_id for e in store.change_log}
        assert len(gids) == 1 and None not in gids

        client.post("/undo")
        assert store.get_value("qubits.q1.f_01") == "4830000000.0"
        assert store.get_value("qubits.q1.T1") == "8834"
        assert store.get_value("qubits.q1.xy.operations.saturation.amplitude") == "0.13"

    def test_the_type_sticks_for_later_edits(self, app, client):
        _, paths, sig = _plan(client)
        client.post("/type-fix/apply", json={"paths": paths, "sig": sig})
        # a later ordinary edit must NOT fall back to text (that is the whole
        # point of persisting the assignment)
        client.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "9000"})
        assert _ctx(app)["store"].get_value("qubits.q1.T1") == 9000

    def test_nothing_is_left_to_fix_afterwards(self, client):
        _, paths, sig = _plan(client)
        client.post("/type-fix/apply", json={"paths": paths, "sig": sig})
        _, paths_after, _ = _plan(client)
        assert paths_after == []

    def test_the_live_chip_is_untouched(self, app, client, chip):
        before = (chip / "state.json").read_text(encoding="utf-8")
        _, paths, sig = _plan(client)
        client.post("/type-fix/apply", json={"paths": paths, "sig": sig})
        assert (chip / "state.json").read_text(encoding="utf-8") == before

    def test_a_partial_selection_converts_only_that(self, app, client):
        _, paths, sig = _plan(client)
        one = ["qubits.q1.T1"]
        r = client.post("/type-fix/apply", json={"paths": one, "sig": sig})
        assert r.get_json()["count"] == 1
        store = _ctx(app)["store"]
        assert store.get_value("qubits.q1.T1") == 8834
        assert store.get_value("qubits.q1.f_01") == "4830000000.0"   # untouched


class TestApplyRefusals:
    def test_a_stale_plan_is_refused_not_applied(self, app, client):
        _, paths, _ = _plan(client)
        r = client.post("/type-fix/apply",
                        json={"paths": paths, "sig": "deadbeefdeadbeef"})
        assert r.status_code == 409
        assert r.get_json()["error_kind"] == "stale_plan"
        assert _ctx(app)["store"].get_value("qubits.q1.T1") == "8834"

    def test_a_value_changed_since_the_preview_invalidates_the_plan(self, app, client):
        _, paths, sig = _plan(client)
        # someone edits one of the offending fields in another tab
        client.post("/field/edit", data={"dot_path": "qubits.q1.f_01",
                                         "value": '"4830000001.0"'})
        r = client.post("/type-fix/apply", json={"paths": paths, "sig": sig})
        assert r.status_code == 409, r.get_data(as_text=True)[:300]

    def test_a_path_the_plan_refused_cannot_be_forced(self, app, client):
        _, _, sig = _plan(client)
        r = client.post("/type-fix/apply",
                        json={"paths": ["qubits.q1.id"], "sig": sig})
        assert r.status_code == 409
        assert _ctx(app)["store"].get_value("qubits.q1.id") == "1"

    def test_empty_selection_is_a_no_op(self, client):
        r = client.post("/type-fix/apply", json={"paths": [], "sig": ""})
        assert r.status_code == 400

    def test_no_chip_loaded_is_handled(self, app):
        c = app.test_client()
        assert c.get("/type-fix/plan").status_code == 200      # a status partial
        assert c.post("/type-fix/apply", json={"paths": ["x"]}).status_code == 400


class TestEntryPoints:
    def test_the_alarm_banner_offers_the_fix(self, client):
        html = client.get("/qubits").get_data(as_text=True)
        assert "type-alarm-banner" in html
        assert "openTypeFixPlan" in html

    def test_diagnostics_offers_the_fix_on_the_strnum_finding(self, client):
        html = client.get("/diagnostics").get_data(as_text=True)
        assert "openTypeFixPlan" in html

    def test_the_client_helpers_exist(self):
        from pathlib import Path
        app_js = (Path(__file__).resolve().parent.parent / "quam_state_manager"
                  / "web" / "static" / "app.js").read_text(encoding="utf-8")
        for fn in ("openTypeFixPlan", "typeFixApply", "typeFixToggleAll",
                   "typeFixCount", "closeTypeFixPlan"):
            assert f"window.{fn}" in app_js, fn
        # the apply path must go through the shared tray swap + refresh events
        assert "_swapPendingTray(d.tray_html)" in app_js


class TestAStringifiedChipStaysNavigable:
    """Found while browser-testing the fix: a chip whose values were
    string-ified crashed the very pages the user needs to reach the repair.

    ``"%.4f"|format("0.13")`` raises TypeError, and ``"0.99" >= 0.99`` raises
    too, so one text value 500'd the whole Qubits list (same defect class as
    the r16 /pairs fix). The lists must degrade to an honest quoted value.
    """

    @pytest.fixture
    def strchip(self, tmp_path):
        state = json.loads(json.dumps(_STATE))
        q = state["qubits"]["q1"]
        q["resonator"] = {
            "operations": {"readout": {"amplitude": "0.042",
                                       "threshold": "-0.00014"}},
        }
        q["gate_fidelity"] = {"averaged": "0.991"}
        folder = tmp_path / "strchip"
        folder.mkdir()
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
        return folder

    @pytest.fixture
    def strclient(self, tmp_path, strchip):
        created = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
        c = created.test_client()
        c.post("/load", data={"folder": str(strchip)})
        return c

    @pytest.mark.parametrize("page", ["/qubits", "/resonators", "/pairs",
                                      "/flux", "/couplers", "/diagnostics"])
    def test_entity_pages_survive_text_values(self, strclient, page):
        r = strclient.get(page)
        assert r.status_code == 200, f"{page} -> {r.status_code}"

    def test_the_text_value_is_shown_honestly_not_swallowed(self, strclient):
        html = strclient.get("/qubits").get_data(as_text=True)
        # quoted + labelled, the same way bulk cells and All-values show a
        # stored-as-text number — never blanked, never silently formatted
        assert "0.042" in html
        assert "Stored as text" in html

    def test_the_banner_is_reachable_from_the_broken_looking_page(self, strclient):
        html = strclient.get("/qubits").get_data(as_text=True)
        assert "type-alarm-banner" in html
        assert "openTypeFixPlan" in html
