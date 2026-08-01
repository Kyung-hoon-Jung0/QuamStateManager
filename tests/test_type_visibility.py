"""r14 type-visibility batch (docs/56 amendment).

The user report: an external state regeneration turned numbers into STRINGS
("0.13") and SM (a) detected it only passively (an unclickable ⚠ inside the
Explorer the user had to find), (b) rendered the string byte-identically to
the number in Live Edit / All values, and (c) could not FIX it — the legacy
old-type-preserving coercer re-stored text on every edit. Plus: floats were
labelled "number" (too broad — "real" now) and the hover icons were tiny.

Pins here: the stored-as-text scan + diagnostics category + findings feed,
the ACTIVE alarm banner (delta-gated, dismiss-per-signature), the
/field/edit[-batch] type_fix offer (409 → convert persists a real/int
assignment and stores the number; keep stays text; quotes bypass), the
committed-value echo, the bulk-grid honesty markers, and the display rename.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import all_values, diagnostics, type_policy as tp
from quam_state_manager.web.app import create_app

_STATE = {
    "qubits": {
        "q1": {"id": "q1", "f_01": "4830000000.0", "T1": 2.5e-05,
               "xy": {"operations": {"x180": {"amplitude": "0.13", "length": 40}}}},
        "q2": {"id": "q2", "f_01": 4.9e9, "T1": 3e-05,
               "xy": {"operations": {"x180": {"amplitude": 0.2, "length": 40}}}},
    },
    "active_qubit_names": ["q1", "q2"],
    "extras": {"note": "12345"},          # extras: user free-form, never flagged
}
_WIRING = {"network": {"host": "1.2.3.4", "cluster_name": "C"}, "wiring": {}}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip"
    live.mkdir()
    (live / "state.json").write_text(json.dumps(_STATE), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(live)})
    assert r.status_code in (200, 302)
    ctx = next(iter(app.config["contexts"].values()))
    return {"app": app, "client": c, "ctx": ctx}


class TestScan:
    def test_numeric_string_leaves(self):
        got = diagnostics.numeric_string_leaves(_STATE)
        assert got == ["qubits.q1.f_01",
                       "qubits.q1.xy.operations.x180.amplitude"]

    def test_skips_pointers_wiring_and_extras(self):
        root = {"qubits": {"q": {"a": "#/qubits/q2/f_01", "b": "1e9"}},
                "wiring": {"x": "5"}, "extras": {"y": "7"},
                "__package_versions__": {"quam": "0.6.0"}}
        assert diagnostics.numeric_string_leaves(root) == ["qubits.q.b"]

    def test_list_elements_get_index_paths(self):
        root = {"qubits": {"q": {"weights": [0.1, "0.2", 0.3]}}}
        assert diagnostics.numeric_string_leaves(root) == ["qubits.q.weights.1"]

    def test_strnum_findings_cap(self):
        root = {"qubits": {f"q{i}": {"v": "1.5"} for i in range(150)}}
        f = diagnostics._strnum_findings(root)
        strn = [x for x in f if x.category == "value_type_strnum"]
        assert len(strn) == diagnostics._STRNUM_CAP + 1     # cap + summary row
        assert "more numeric-looking TEXT" in strn[-1].message

    def test_findings_feed_carries_type_marks(self, env):
        # r14 ⑨: value_type* (incl. the new strnum) reach the Explorer marks
        # feed — they were excluded by the old startswith("value_spec") filter.
        d = env["client"].get("/diagnostics/findings.json").get_json()
        cats = {f["category"] for f in d["value_spec"]}
        assert "value_type_strnum" in cats
        jumps = {f["jump_path"] for f in d["value_spec"]
                 if f["category"] == "value_type_strnum"}
        assert "qubits.q1.f_01" in jumps


class TestActiveAlarm:
    def test_banner_renders_and_names_first_path(self, env):
        html = env["client"].get("/qubits").data.decode()
        assert "type-alarm-banner" in html
        assert "stored as TEXT" in html
        assert "qubits.q1.f_01" in html
        assert "/type-alarm/dismiss" in html

    def test_dismiss_silences_this_set_and_new_anomaly_reraises(self, env):
        import re
        c, ctx = env["client"], env["ctx"]
        html = c.get("/qubits").data.decode()
        sig = re.search(r'"sig": "([0-9a-f]+)"', html).group(1)
        token = re.search(r'"token": "([^"]+)"', html).group(1)
        r = c.post("/type-alarm/dismiss", data={"sig": sig, "token": token})
        assert r.status_code == 200
        assert "type-alarm-banner" not in c.get("/qubits").data.decode()
        # a NEW text-typed value changes the signature → the banner re-raises
        ctx["modifier"].set_value("qubits.q2.T1", "0.5", coerce=False)
        assert "type-alarm-banner" in c.get("/qubits").data.decode()


class TestTypeFixOffer:
    def test_unquoted_number_on_text_field_gates_409(self, env):
        c, ctx = env["client"], env["ctx"]
        r = c.post("/field/edit", data={"dot_path": "qubits.q1.f_01",
                                        "value": "4.85e9"})
        assert r.status_code == 409
        tf = r.get_json()["type_fix"]
        assert tf["proposed"] == "real"
        assert tf["current_display"] == '"4830000000.0"'
        # NOTHING committed before the user chose
        assert ctx["store"].state["qubits"]["q1"]["f_01"] == "4830000000.0"

    def test_convert_stores_number_and_persists_the_type(self, env):
        c, ctx = env["client"], env["ctx"]
        r = c.post("/field/edit", data={"dot_path": "qubits.q1.f_01",
                                        "value": "4.85e9",
                                        "type_fix": "convert"})
        body = r.get_json()
        assert r.status_code == 200 and body["ok"]
        assert body["stored"] == 4.85e9 and body["stored_kind"] == "real"
        assert ctx["store"].state["qubits"]["q1"]["f_01"] == 4.85e9
        # the real assignment persisted → the NEXT edit needs no gate
        r2 = c.post("/field/edit", data={"dot_path": "qubits.q1.f_01",
                                         "value": "4.86e9"})
        assert r2.status_code == 200
        assert ctx["store"].state["qubits"]["q1"]["f_01"] == 4.86e9
        policy = getattr(ctx["store"], "type_policy", None)
        assert policy and "qubits.q1.f_01" in policy.assignments

    def test_keep_stays_text(self, env):
        c, ctx = env["client"], env["ctx"]
        r = c.post("/field/edit", data={
            "dot_path": "qubits.q1.xy.operations.x180.amplitude",
            "value": "0.14", "type_fix": "keep"})
        body = r.get_json()
        assert r.status_code == 200 and body["stored"] == "0.14"
        assert body["stored_kind"] == "str"
        assert ctx["store"].state["qubits"]["q1"]["xy"]["operations"][
            "x180"]["amplitude"] == "0.14"

    def test_quoted_input_means_text_no_gate(self, env):
        c, ctx = env["client"], env["ctx"]
        r = c.post("/field/edit", data={
            "dot_path": "qubits.q1.xy.operations.x180.amplitude",
            "value": '"0.15"'})
        assert r.status_code == 200
        assert ctx["store"].state["qubits"]["q1"]["xy"]["operations"][
            "x180"]["amplitude"] == "0.15"

    def test_number_typed_field_never_gates(self, env):
        c = env["client"]
        r = c.post("/field/edit", data={"dot_path": "qubits.q2.f_01",
                                        "value": "4.91e9"})
        assert r.status_code == 200 and r.get_json()["stored_kind"] == "real"

    def test_batch_gate_counts_and_converts_all(self, env):
        c, ctx = env["client"], env["ctx"]
        ups = [{"dot_path": "qubits.q1.f_01", "value": "4.85e9"},
               {"dot_path": "qubits.q1.xy.operations.x180.amplitude",
                "value": "0.14"}]
        r = c.post("/field/edit-batch",
                   json={"updates": ups, "expect_chip": ""})
        assert r.status_code == 409
        assert r.get_json()["type_fix"]["more_in_batch"] == 1
        r2 = c.post("/field/edit-batch",
                    json={"updates": ups, "expect_chip": "",
                          "type_fix": "convert"})
        assert r2.status_code == 200 and r2.get_json()["ok"]
        st = ctx["store"].state["qubits"]["q1"]
        assert st["f_01"] == 4.85e9
        assert st["xy"]["operations"]["x180"]["amplitude"] == 0.14


class TestHonestDisplays:
    def test_bulk_cells_wear_quotes_and_warning(self, env):
        html = env["client"].get("/bulk").data.decode()
        assert html.count("bulk-strq") >= 2          # open + close quote spans
        assert "bulk-cell-str" in html
        assert "stored as TEXT" in html

    def test_all_values_display_quotes_numeric_strings(self):
        assert all_values._display("0.13") == '"0.13"'
        assert all_values._display("abc") == "abc"
        assert all_values._display("#/qubits/q1/f_01") == "#/qubits/q1/f_01"
        assert all_values._display(0.13) == "0.13"

    def test_float_display_name_is_real(self):
        assert tp.format_type({"base": "float"}) == "real"
        assert tp.parse_type("real")["base"] == "float"
        assert tp.parse_type("number")["base"] == "float"   # legacy alias


class TestClientWiringPins:
    _STATIC = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"

    def test_js_surfaces_offer_the_conversion(self):
        app_js = (self._STATIC / "app.js").read_text(encoding="utf-8")
        assert "_confirmTypeFix" in app_js
        assert "data.type_fix" in app_js                 # explorer inline editor
        for f in ("bulk-edit.js", "pair-edit.js", "all-values.js"):
            js = (self._STATIC / f).read_text(encoding="utf-8")
            assert "type_fix" in js, f"{f} must offer the conversion"

    def test_explorer_marks_are_clickable_and_load_applied(self):
        app_js = (self._STATIC / "app.js").read_text(encoding="utf-8")
        i = app_js.index("function markTreePath")
        seg = app_js[i:i + 2600]
        assert "_navigateToExplorerPath(dotPath)" in seg   # ⚠ click-through
        j = app_js.index("function _diagInitOnLoad")
        assert "_applyExplorerSpecMarks" in app_js[j:j + 700]

    def test_hover_icons_enlarged(self):
        css = (self._STATIC / "style.css").read_text(encoding="utf-8")
        i = css.index(".tree-act-btn {")
        assert "font-size: 1.05em" in css[i:i + 400]
        j = css.index(".tree-json-edit-btn {")
        assert "font-size: 1.05em" in css[j:j + 400]
