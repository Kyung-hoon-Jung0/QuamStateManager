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
            "slot0": "007",                             # leading zeros
            "grouped": "1_000",                         # a separator
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

    def test_the_response_names_every_converted_leaf(self, app, client):
        # QA F-E: an open Explorer tree kept the quoted text after the repair;
        # the response now carries the ONE patch shape (sync pull / undo) with
        # the value the modifier STORED, which the client patches in place.
        _, paths, sig = _plan(client)
        body = client.post("/type-fix/apply", json={"paths": paths, "sig": sig}).get_json()
        by = {c["dot_path"]: c for c in body["changes"]}
        assert set(by) == set(paths)
        assert by["qubits.q1.T1"]["value"] == 8834 and isinstance(by["qubits.q1.T1"]["value"], int)
        assert isinstance(by["qubits.q1.f_01"]["value"], float)
        for c in body["changes"]:
            assert c["old_kind"] == "num"
            assert {"old_value_str", "old_value_disp", "source_file"} <= set(c)

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

    def test_undoing_the_repair_leaves_the_tray_synced(self, app, client):
        """QA diagnostics-r2-14: the repair lives in the change log only, so
        Ctrl+Z must return the tray to "Synced" -- raising working_dirty made
        it read "Working state · not applied" with nothing differing from live."""
        ctx = _ctx(app)
        assert not ctx.get("working_dirty")                  # the premise
        _, paths, sig = _plan(client)
        client.post("/type-fix/apply", json={"paths": paths, "sig": sig})
        assert ctx["store"].change_log                       # pending, in the log
        assert not ctx.get("working_dirty")
        client.post("/undo")
        assert ctx["store"].change_log == []
        assert not ctx.get("working_dirty")
        tray = client.get("/state/tray").get_data(as_text=True)
        assert 'data-working-dirty="0"' in tray
        assert "not applied" not in tray
        assert "state-status-synced" in tray

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


def test_type_fix_tree_refresh_selfcheck():
    """QA F-E: the open Explorer tree shows the converted number and loses the
    stale warning mark without a reload (tests/type_fix_tree_refresh_selfcheck.cjs)."""
    import shutil
    import subprocess
    from pathlib import Path
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(["node", str(root / "tests" / "type_fix_tree_refresh_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(root), timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL OK" in r.stdout, r.stdout + r.stderr


class TestTheQuotedSignalFromTheTreeEditor:
    """The escape hatch the 409 message advertises, reaching the one surface
    that shows the literal (customer, 2026-09-05).

    ``_type_fix_offer`` reads a LEADING QUOTE in the typed value as "I want
    text" and stands down. The Json Tree editor (docs/145) shows a string as
    its JSON literal and UNWRAPS a valid one before posting -- which
    `tree_edit_literal_selfcheck.cjs` pins -- so those quotes never arrived and
    the hatch could not be taken from there. The client reports the fact
    instead of the character.
    """

    def test_an_unquoted_number_still_gets_the_offer(self, client):
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.T1", "value": "8834"})
        assert r.status_code == 409, r.get_data(as_text=True)
        assert r.get_json()["type_fix"]["proposed"] == "int"

    def test_the_quoted_marker_stands_the_offer_down(self, client):
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.T1", "value": "8835", "value_quoted": "1"})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["ok"] is True
        # and it is still TEXT, which is what the quotes asked for
        assert body["stored"] == "8835"
        assert body["stored_kind"] == "str"

    def test_a_literal_leading_quote_still_works_for_every_other_caller(self, client):
        """The character route is untouched -- only the tree editor needs the
        flag, because only the tree editor removes the character."""
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.T1", "value": '"8836"'})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["stored"] == "8836"

    def test_the_marker_cannot_convert_by_itself(self, client):
        """`value_quoted` says "do not ask me", never "change the type". A
        conversion still needs the user's own type_fix=convert."""
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.T1", "value": "8837", "value_quoted": "1"})
        assert r.get_json()["stored_kind"] == "str"
        r2 = client.post("/field/edit", data={
            "dot_path": "qubits.q1.T1", "value": "8838", "type_fix": "convert"})
        assert r2.status_code == 200, r2.get_data(as_text=True)
        assert r2.get_json()["stored_kind"] in ("int", "real")

    def test_the_client_sends_it_only_when_it_unwrapped(self):
        from pathlib import Path
        app_js = (Path(__file__).resolve().parent.parent / "quam_state_manager"
                  / "web" / "static" / "app.js").read_text(encoding="utf-8")
        assert 'body.append("value_quoted", "1")' in app_js
        assert "if (unwrapped) body.append" in app_js


class TestKeepActuallyKeepsTheText:
    """Answering "keep text" to the offer must not drop what makes it text.

    Measured before the fix, on a chip carrying real label-shaped values:
    007 -> "7", 02 -> "3", 1_000 -> "1000". The user was asked "convert to a
    number, or keep text", answered KEEP, and the zeros went anyway -- because
    `keep` only skipped the 409 and the value still went through
    `parse_value`. The quoted literal was the only spelling that survived, and
    docs/145's tree editor is the one surface that deletes the quotes.
    """

    def test_keep_preserves_leading_zeros(self, client):
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.slot0", "value": "008", "type_fix": "keep"})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["stored"] == "008", body
        assert body["stored_kind"] == "str"

    def test_keep_preserves_a_separator(self, client):
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.grouped", "value": "2_000", "type_fix": "keep"})
        assert r.get_json()["stored"] == "2_000"

    def test_the_quoted_marker_preserves_them_too(self, client):
        """`value_quoted` says the user typed quotes, so it must reach the
        PARSE, not only the offer -- standing the question down while still
        losing the zeros would be the worse half of both answers."""
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.slot0", "value": "009", "value_quoted": "1"})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["stored"] == "009"

    def test_an_explicit_literal_is_unchanged(self, client):
        """The path that always worked still works, byte for byte."""
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.slot0", "value": '"010"'})
        assert r.get_json()["stored"] == "010"

    def test_the_marker_does_not_double_wrap_a_literal(self, client):
        """The tree only sets the marker AFTER removing the quotes, so the two
        never arrive together from there -- but another caller could send both,
        and wrapping twice would store the quote characters themselves."""
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.slot0", "value": '"012"', "value_quoted": "1"})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["stored"] == "012"

    def test_convert_still_converts(self, client):
        """The other answer is untouched: keep is not a way to block convert."""
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.T1", "value": "8834", "type_fix": "convert"})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["stored_kind"] in ("int", "real")

    def test_keep_does_not_wrap_a_value_that_is_already_a_literal(self, client):
        """Double-wrapping would store the quotes themselves."""
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.slot0", "value": '"011"', "type_fix": "keep"})
        assert r.get_json()["stored"] == "011"


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


# ---------------------------------------------------------------------------
# docs/78 — the alert that raises ITSELF when new content lands
# ---------------------------------------------------------------------------


class TestTheAlertPayload:
    """The unified payload behind the banner, the popup and the card."""

    def test_it_carries_both_anomaly_classes_and_a_plan(self, app, client):
        from quam_state_manager.web import routes
        with app.test_request_context():
            payload = routes._type_alarm_payload(_ctx(app))
        assert payload is not None
        assert payload["strnum"]["fixable"] == len(_CONVERTIBLE)
        assert payload["strnum"]["skipped"] >= 1      # id / grid_location / slot
        assert payload["editable"] is True
        assert "env" in payload and "entries" in payload["env"]
        # QA F-F: an env finding that only restates the text values is not a
        # second problem (was: strnum.count + env.count, which double-counted)
        assert payload["total"] == (payload["strnum"]["count"] + payload["env"]["count"]
                                    - payload["env"]["restated_text"])

    def test_the_dismiss_signature_formula_is_unchanged(self, app, client):
        """r14 dismissals already on disk must keep working — the signature is
        sha1 over the sorted PATHS only, never the stored values."""
        import hashlib
        from quam_state_manager.web import routes
        with app.test_request_context():
            payload = routes._type_alarm_payload(_ctx(app))
            memo = routes._type_alarm_memo(_ctx(app))
        legacy = hashlib.sha1(
            "\n".join(sorted(memo["paths"])).encode("utf-8")).hexdigest()[:16]
        assert payload["sig"] == legacy

    # --- QA F-F: one mistyped value is one value -------------------------

    @staticmethod
    def _rec(paths, count=None, code="type_mismatch", field="T1"):
        return {"kind": "type_mismatch", "severity": "error",
                "class": "quam_builder.FluxTunableTransmon", "field": field,
                "code": code, "count": len(paths) if count is None else count,
                "example_paths": list(paths), "detail": "expected float, got str"}

    def test_a_text_value_the_env_also_flags_counts_once(self):
        p = ["qubits.q4.T1"]
        s = type_fix.alert_summary(None, [self._rec(p)], p)
        assert s["total"] == 1
        assert s["env"]["restated_text"] == 1
        assert s["env"]["count"] == 1              # the report itself is kept

    def test_two_text_values_on_one_field_are_two(self):
        p = ["qubits.q3.T1", "qubits.q4.T1"]
        assert type_fix.alert_summary(None, [self._rec(p)], p)["total"] == 2

    def test_four_fields_four_values(self):
        p = ["qubits.q4.T1", "qubits.q4.T2", "qubits.q4.f_01", "qubits.q4.anharmonicity"]
        recs = [self._rec([x], field=x.rsplit(".", 1)[-1]) for x in p]
        assert type_fix.alert_summary(None, recs, p)["total"] == 4

    def test_a_real_mismatch_is_still_counted(self):
        text = ["qubits.q4.T1"]
        other = self._rec(["qubits.q4.id"], field="id")          # not a text value
        s = type_fix.alert_summary(None, [self._rec(text), other], text)
        assert s["total"] == 2 and s["env"]["restated_text"] == 1
        enum = self._rec(text, code="enum_miss")                  # another code
        assert type_fix.alert_summary(None, [enum], text)["total"] == 2

    def test_a_finding_with_unlisted_places_is_counted_once(self):
        p = [f"qubits.q{i}.T1" for i in range(1, 8)]              # 7 places
        rec = self._rec(p[:5], count=7)                          # examples cap 5
        assert type_fix.alert_summary(None, [rec], p)["total"] == 7 + 1

    def test_the_popup_does_not_call_a_restatement_a_second_problem(self, app):
        """QA F-F: the env finding about the same text value is not "SM will
        not change these -- the library may have changed"; storing the number
        fixes it. A real env mismatch keeps that sentence."""
        from flask import render_template
        p = ["qubits.q4.T1"]
        plan = {"rows": [{"path": p[0], "current_display": '"2e-05"',
                          "proposed_display": "2e-05", "proposed_type": "real"}],
                "skipped": [], "total": 1, "sig": "s"}
        with app.test_request_context():
            alert = type_fix.alert_summary(plan, [self._rec(p)], p)
            alert.update(sig="s", env_sig="e", token="t", first=p[0])
            html = re.sub(r"\s+", " ", render_template(
                "_type_fix_plan.html", plan=plan, alert=alert))
            assert "<strong>1 value</strong> on this chip has a type problem" in html
            assert "expects a number there too" in html
            assert "SM will not change these" not in html
            other = self._rec(["qubits.q4.id"], field="id")
            alert = type_fix.alert_summary(plan, [self._rec(p), other], p)
            alert.update(sig="s", env_sig="e", token="t", first=p[0])
            html = re.sub(r"\s+", " ", render_template(
                "_type_fix_plan.html", plan=plan, alert=alert))
            assert "<strong>2 values</strong> on this chip have a type problem" in html
            assert "SM will not change these" in html

    def test_an_env_only_alert_is_not_called_a_type_problem(self, app):
        """QA F-N: an unknown field (or an unimportable class) is a disagreement
        with the environment's schema, not "1 value ... has a type problem"."""
        from flask import render_template
        rec = {"kind": "unknown_field", "severity": "error",
               "class": "quam_builder.FluxTunableTransmon", "field": "lab_calib_note",
               "code": "unknown_field", "count": 1,
               "example_paths": ["qubits.q1.lab_calib_note"],
               "detail": "the environment's class does not declare this field"}
        with app.test_request_context():
            alert = type_fix.alert_summary(None, [rec], [])
            assert alert["type_problems"] == 0 and alert["total"] == 1
            alert.update(sig="", env_sig="e", token="t", first="",
                         reason_label="this chip was opened")
            html = re.sub(r"\s+", " ", render_template(
                "_type_fix_plan.html", plan={"rows": [], "skipped": [], "sig": "s"},
                alert=alert))
            assert "type problem" not in html
            assert "1 value" not in html
            assert "does not match the selected environment" in html
            assert "disagrees with the selected environment" in html
            assert "found when this chip was opened" in html
            assert "lab_calib_note" in html             # the env line still names it
            # a text value alongside it is still "1 value", the env line explains the rest
            alert = type_fix.alert_summary(None, [rec], ["qubits.q2.T1"])
            alert.update(sig="s", env_sig="e", token="t", first="qubits.q2.T1")
            html = re.sub(r"\s+", " ", render_template(
                "_type_fix_plan.html", plan={"rows": [], "skipped": [], "sig": "s"},
                alert=alert))
            assert "Values arrived with a type problem" in html
            assert "<strong>1 value</strong> on this chip has a type problem" in html

    def test_the_env_signature_ignores_instance_counts(self):
        """One more qubit with the SAME defect is not a new thing to say."""
        one = [{"kind": "type_mismatch", "class": "Transmon", "field": "T1",
                "code": "type_mismatch", "count": 3}]
        many = [dict(one[0], count=21)]
        assert type_fix.env_signature(one) == type_fix.env_signature(many)
        other = [dict(one[0], field="T2")]
        assert type_fix.env_signature(one) != type_fix.env_signature(other)

    def test_an_archive_is_told_but_not_offered_a_repair(self, app, client):
        from quam_state_manager.web import routes
        ctx = _ctx(app)
        ctx["origin"] = "dataset_archive"
        with app.test_request_context():
            payload = routes._type_alarm_payload(ctx)
        assert payload["editable"] is False
        assert payload["strnum"]["fixable"] == 0     # no plan is even built


class TestTheContentEntryTrigger:
    """The popup may only fire when NEW CONTENT entered the working copy."""

    def test_opening_a_chip_arms_it(self, app, client):
        assert _ctx(app).get("type_alarm_armed")

    def test_an_ordinary_edit_does_not_arm_it(self, app, client):
        client.get("/type-alert")                     # consume the open-arm
        assert not _ctx(app).get("type_alarm_armed")
        client.post("/field/edit", data={"dot_path": "qubits.q1.T1",
                                         "value": "9000", "type_fix": "convert"})
        assert not _ctx(app).get("type_alarm_armed"), \
            "editing is the user's own doing — it must never prompt"

    def test_a_plain_render_does_not_arm_it(self, app, client):
        client.get("/type-alert")
        client.get("/qubits")
        client.get("/diagnostics")
        assert not _ctx(app).get("type_alarm_armed")

    def test_an_archive_is_never_armed(self, app, client):
        from quam_state_manager.web import routes
        ctx = _ctx(app)
        ctx["origin"] = "dataset_archive"
        ctx.pop("type_alarm_armed", None)
        routes._arm_type_alarm(ctx, "chip-open")
        assert not ctx.get("type_alarm_armed")


class TestTheAlertEndpoint:
    def test_it_answers_once_per_content_entry(self, client):
        first = client.get("/type-alert", headers={"HX-Request": "true"})
        assert first.status_code == 200
        body = first.get_data(as_text=True)
        # the PROPOSAL is in the popup — auto-correct is one click, but never blind
        assert 'class="tfx-pick"' in body
        assert "type problem" in body
        assert client.get("/type-alert").status_code == 204

    def test_it_names_where_the_content_came_from(self, client):
        body = client.get("/type-alert").get_data(as_text=True)
        assert "this chip was opened" in body

    def test_a_dismissed_set_never_raises_again(self, app, client):
        from quam_state_manager.web import routes
        with app.test_request_context():
            payload = routes._type_alarm_payload(_ctx(app))
        client.post("/type-alarm/dismiss",
                    data={"sig": payload["sig"], "env_sig": payload["env_sig"],
                          "token": payload["token"]})
        routes._arm_type_alarm(_ctx(app), "live-pull")
        _ctx(app).pop("_type_alarm_shown", None)
        assert client.get("/type-alert").status_code == 204

    def test_re_arming_with_the_same_set_does_not_re_nag(self, app, client):
        from quam_state_manager.web import routes
        assert client.get("/type-alert").status_code == 200
        routes._arm_type_alarm(_ctx(app), "live-pull")
        assert client.get("/type-alert").status_code == 204

    def test_a_new_anomaly_raises_it_again(self, app, client):
        from quam_state_manager.web import routes
        assert client.get("/type-alert").status_code == 200
        store = _ctx(app)["store"]
        with store._lock:
            store.state["qubits"]["q1"]["T2"] = "3.3e-6"   # a NEW text number
            store.mutation_seq += 1
        routes._arm_type_alarm(_ctx(app), "live-pull")
        assert client.get("/type-alert").status_code == 200

    def test_an_archive_is_never_prompted(self, app, client):
        ctx = _ctx(app)
        ctx["origin"] = "dataset_archive"
        ctx["type_alarm_armed"] = {"reason": "chip-open", "at": "now"}
        assert client.get("/type-alert").status_code == 204


class TestArchivesAreNotOfferedRepair:
    def test_plan_and_apply_refuse_on_an_archive(self, app, client):
        ctx = _ctx(app)
        ctx["origin"] = "dataset_archive"
        assert client.get("/type-fix/plan").status_code == 409
        r = client.post("/type-fix/apply", json={"paths": ["qubits.q1.T1"]})
        assert r.status_code == 409
        assert r.get_json()["error_kind"] == "archive_read_only"
        assert ctx["store"].get_value("qubits.q1.T1") == "8834"


class TestTheBannerRefreshes:
    def test_the_slot_re_renders_itself(self, client):
        html = client.get("/qubits").get_data(as_text=True)
        assert 'hx-get="/type-alarm/banner"' in html
        assert "diagnostics-changed from:body" in html

    def test_the_count_drops_after_a_repair(self, client):
        """The stale count was the bug: the slot had no refresh trigger, so a
        repaired chip kept advertising the old number until a full page load."""
        def _count(html):
            m = re.search(r"<strong>(\d+) values? stored as TEXT</strong>", html)
            return int(m.group(1)) if m else 0

        before = _count(client.get("/type-alarm/banner").get_data(as_text=True))
        assert before >= len(_CONVERTIBLE)
        _, paths, sig = _plan(client)
        assert client.post("/type-fix/apply",
                           json={"paths": paths, "sig": sig}).status_code == 200
        after = _count(client.get("/type-alarm/banner").get_data(as_text=True))
        assert after == before - len(_CONVERTIBLE)


class TestTheDiagnosticsCard:
    def test_the_card_is_the_single_entry_point(self, client):
        html = client.get("/diagnostics").get_data(as_text=True)
        assert "diag-types-card" in html
        assert "Auto-correct" in html
        # the per-row repeat is gone: one whole-chip action, offered once
        assert "Fix types" not in html

    def test_it_reports_both_classes_and_self_refreshes(self, client):
        html = client.get("/diagnostics/types-card").get_data(as_text=True)
        assert "Numbers stored as text" in html
        assert 'hx-get="/diagnostics/types-card"' in html
        assert "diagnostics-changed from:body" in html

    def test_nothing_is_left_to_auto_correct_after_a_repair(self, client):
        """What remains is only what SM refused to guess at (an identity key,
        a leading-zero label) — the card must stop offering a repair for them
        rather than pretending there is more to do."""
        _, paths, sig = _plan(client)
        assert client.post("/type-fix/apply",
                           json={"paths": paths, "sig": sig}).status_code == 200
        html = client.get("/diagnostics/types-card").get_data(as_text=True)
        # QA F-G: this pin used to assert the bug ("Auto-correct 0 values" as a
        # PRIMARY button) against its own docstring -- no offer for nothing
        assert "Auto-correct" not in html
        assert "type the number in the Json Tree View" in html
        assert "See why" in html

    # --- QA F-G: the offer counts what SM WILL convert ---------------------

    def test_the_banner_offers_only_the_convertible_count(self, client):
        html = client.get("/type-alarm/banner").get_data(as_text=True)
        # 7 text values, 3 convertible: "Fix 3", never "Fix 7"
        assert f"Fix {len(_CONVERTIBLE)} values" in html
        assert "Fix 7 value" not in html

    def test_the_banner_has_no_primary_fix_once_only_refusals_remain(self, client):
        _, paths, sig = _plan(client)
        assert client.post("/type-fix/apply",
                           json={"paths": paths, "sig": sig}).status_code == 200
        html = client.get("/type-alarm/banner").get_data(as_text=True)
        assert "stored as TEXT" in html                  # still reported...
        assert not re.search(r"Fix \d+ value", html)    # ...but not offered
        assert "type the number in the Json Tree View" in html
        assert "Why not" in html

    def test_a_plan_with_nothing_to_convert_opens_its_reasons(self, client):
        _, paths, sig = _plan(client)
        client.post("/type-fix/apply", json={"paths": paths, "sig": sig})
        html = client.get("/type-fix/plan").get_data(as_text=True)
        assert "none of them can be converted safely" in re.sub(r"\s+", " ", html)
        assert re.search(r'<details class="tfx-skipped"\s+open', html)
        assert "Staged into the working copy" not in html
        assert "type the number" in html
        # a per-row way there, only for the ones typing can fix
        go = re.findall(r'data-goto="([^"]+)"', html)
        assert set(go) == {"qubits.q1.slot", "qubits.q1.slot0", "qubits.q1.grouped"}

    def test_a_plan_with_rows_keeps_its_staging_note_and_closed_list(self, client):
        html, _, _ = _plan(client)
        assert "Staged into the working copy" in html
        assert not re.search(r'<details class="tfx-skipped"\s+open', html)

    def test_a_chip_with_no_anomalies_says_so(self, tmp_path):
        clean = tmp_path / "clean"
        clean.mkdir()
        (clean / "state.json").write_text(
            json.dumps({"qubits": {"q1": {"id": 1, "T1": 8834}}}), encoding="utf-8")
        (clean / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
        created = create_app(testing=True, instance_path=str(tmp_path / "_i3"))
        c = created.test_client()
        c.post("/load", data={"folder": str(clean)})
        html = c.get("/diagnostics/types-card").get_data(as_text=True)
        assert "consistent" in html
        assert "Auto-correct" not in html
        assert c.get("/type-alert").status_code == 204   # never prompts a clean chip

    def test_an_archive_sees_counts_but_no_repair_button(self, app, client):
        _ctx(app)["origin"] = "dataset_archive"
        html = client.get("/diagnostics/types-card").get_data(as_text=True)
        assert "Numbers stored as text" in html
        assert "Auto-correct" not in html
        assert "read-only archive" in html
