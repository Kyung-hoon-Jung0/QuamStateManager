"""Edit as JSON (D-22..D-28): server diff helper and save semantics.

The save parses the editor text, diffs it against the working copy with the
tree's leaf grammar (``json_diff.edit_ops``) and writes ONE /field/edit-batch
(one undo group). Any refusal writes nothing and names every refused path.
"""
from __future__ import annotations

import json

import pytest

from quam_state_manager.core import json_diff
from quam_state_manager.web.app import create_app

_WIRING = {
    "network": {"host": "127.0.0.1", "cluster_name": "c"},
    "wiring": {"qubits": {"q1": {"rr": {"opx_output": "#/ports/mw_outputs/con1/1/1"}}}},
}


def _state():
    return {
        "qubits": {
            "q1": {
                "id": "q1",
                "f_01": 5.0e9,
                "T1": 2.0e-5,
                "label": "a",
                "filt": [1.0, 2.0],
                "alias": "#./T1",
                "alias2": "#./f_01",
                "resonator": {
                    "opx_output": "#/wiring/qubits/q1/rr/opx_output",
                    "operations": {"readout": {"amplitude": 0.2, "length": 1000}},
                },
                "extra": {"k": 1},
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["q1"],
        "ports": {"mw_outputs": {"con1": {"1": {"1": {
            "band": 1, "full_scale_power_dbm": 0, "upconverter_frequency": 5.0e9}}}}},
    }


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    ctx = next(iter(app.config["contexts"].values()))
    return {"client": c, "ctx": ctx, "live": live}


def _open(c, path="qubits.q1", tab="state"):
    d = c.get(f"/tree/json-edit/source?path={path}&tab={tab}").get_json()
    assert d["ok"], d
    return d


def _save(c, doc, opened, path="qubits.q1", tab="state", text=None):
    return c.post("/tree/json-edit/save", json={
        "tab": tab, "path": path, "base_seq": opened["mutation_seq"],
        "text": text if text is not None else json.dumps(doc)})




def _peek(c, p):
    d = c.get(f"/field/peek?dot_path={p}").get_json()
    return {"value": d["values"].get(p)}


class TestEditOps:
    def test_grammar(self):
        old = {"a": 1, "b": {"c": [1, 2], "d": 2}, "e": [1, 2]}
        new = {"a": 1.0, "b": {"c": [1, 3], "x": None}, "e": [1, 2, 3]}
        ops = json_diff.edit_ops(old, new, "r")
        assert {"op": "set", "path": "r.a", "value": 1.0} in ops       # 1 -> 1.0 is a change
        assert {"op": "set", "path": "r.b.c.1", "value": 3} in ops     # list index
        assert {"op": "delete", "path": "r.b.d"} in ops
        assert {"op": "create", "path": "r.b.x", "value": None} in ops
        assert {"op": "set", "path": "r.e", "value": [1, 2, 3]} in ops  # length change = whole list
        assert len(ops) == 5

    def test_order_is_not_a_change(self):
        assert json_diff.edit_ops({"a": 1, "b": 2}, {"b": 2, "a": 1}) == []


class TestSave:
    def test_open_has_pointers_as_text_and_seq(self, env):
        d = _open(env["client"])
        assert '"#./T1"' in d["text"] and isinstance(d["mutation_seq"], int)

    def test_values_add_delete_list_one_undo(self, env):
        c = env["client"]
        o = _open(c)
        doc = json.loads(o["text"])
        doc["T1"] = 3.0e-5
        doc["filt"] = [1.0, 2.0, 4.0]
        doc["new_key"] = 7
        del doc["extra"]
        r = _save(c, doc, o)
        assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
        assert _peek(c, "qubits.q1.T1")["value"] == 3.0e-5
        assert _peek(c, "qubits.q1.new_key")["value"] == 7
        assert _peek(c, "qubits.q1.filt")["value"] == [1.0, 2.0, 4.0]
        store = env["ctx"]["store"]
        assert "extra" not in store.merged["qubits"]["q1"]
        gids = {e.group_id for e in store.change_log}
        assert len(gids) == 1                                  # ONE group
        assert c.post("/undo").status_code == 200              # ONE Ctrl+Z
        assert store.merged["qubits"]["q1"] == _state()["qubits"]["q1"]

    def test_type_refusal_is_all_or_nothing(self, env):
        c = env["client"]
        o = _open(c)
        doc = json.loads(o["text"])
        doc["T1"] = 4.0e-5                    # fine on its own
        doc["f_01"] = {"not": "a number"}     # refused
        doc["id"] = "q9"                      # identity key: refused
        r = _save(c, doc, o)
        d = r.get_json()
        assert r.status_code == 409 and not d["ok"]
        paths = {x["path"] for x in d["refused"]}
        assert {"qubits.q1.f_01", "qubits.q1.id"} <= paths     # every refused path named
        assert all(x["error"] for x in d["refused"])
        assert _peek(c, "qubits.q1.T1")["value"] == 2.0e-5     # nothing landed
        assert not env["ctx"]["store"].change_log

    def test_pointer_relink_stays_text(self, env):
        c = env["client"]
        o = _open(c)
        doc = json.loads(o["text"])
        doc["alias"] = "#./f_01"
        r = _save(c, doc, o)
        assert r.status_code == 200, r.get_json()
        store = env["ctx"]["store"]
        assert store.merged["qubits"]["q1"]["alias"] == "#./f_01"   # the leaf itself
        assert store.merged["qubits"]["q1"]["T1"] == 2.0e-5          # target untouched

    def test_pointer_to_literal_is_refused(self, env):
        c = env["client"]
        o = _open(c)
        doc = json.loads(o["text"])
        doc["alias"] = 1.0e-5
        d = _save(c, doc, o).get_json()
        assert [x["path"] for x in d["refused"]] == ["qubits.q1.alias"]
        assert env["ctx"]["store"].merged["qubits"]["q1"]["T1"] == 2.0e-5

    def test_moved_seq_refuses(self, env):
        c = env["client"]
        o = _open(c)
        assert c.post("/field/edit", data={"dot_path": "qubits.q1.label",
                                           "value": "b"}).status_code == 200
        doc = json.loads(o["text"])
        doc["T1"] = 9.0e-5
        r = _save(c, doc, o)
        assert r.status_code == 409 and r.get_json()["moved"]
        assert _peek(c, "qubits.q1.T1")["value"] == 2.0e-5
        o2 = _open(c)                                       # re-diff on the new base
        doc2 = json.loads(o2["text"])
        doc2["T1"] = 9.0e-5
        assert _save(c, doc2, o2).status_code == 200

    def test_parse_error_names_line(self, env):
        c = env["client"]
        o = _open(c)
        r = _save(c, None, o, text='{\n "a": 1,\n}')
        d = r.get_json()
        assert r.status_code == 400 and d["line"] == 3
        assert not env["ctx"]["store"].change_log

    def test_fsp_commits_and_offers(self, env):
        c = env["client"]
        o = _open(c, "ports.mw_outputs.con1.1.1")
        doc = json.loads(o["text"])
        doc["full_scale_power_dbm"] = -6
        r = _save(c, doc, o, path="ports.mw_outputs.con1.1.1")
        assert r.status_code == 200, r.get_json()
        hdr = json.loads(r.headers["X-SM-Fsp-Changed"])
        assert hdr["ports"][0]["dot_path"] == "ports.mw_outputs.con1.1.1.full_scale_power_dbm"
        assert hdr["gid"] == r.get_json()["group_id"]
        assert _peek(c, "ports.mw_outputs.con1.1.1.full_scale_power_dbm")["value"] == -6
        # the amplitude did NOT change on its own
        assert _peek(c, "qubits.q1.resonator.operations.readout.amplitude")["value"] == 0.2

    def test_whole_wiring_file_and_apply_to_live(self, env):
        c = env["client"]
        o = _open(c, "", "wiring")
        doc = json.loads(o["text"])
        doc["network"]["host"] = "127.0.0.2"
        r = _save(c, doc, o, path="", tab="wiring")
        assert r.status_code == 200, r.get_json()
        assert c.post("/state/apply-to-live").status_code == 200
        on_disk = json.loads((env["live"] / "wiring.json").read_text(encoding="utf-8"))
        assert on_disk["network"]["host"] == "127.0.0.2"
        assert _peek(c, "network.host")["value"] == "127.0.0.2"

    def test_nothing_changed_writes_nothing(self, env):
        c = env["client"]
        o = _open(c)
        d = _save(c, json.loads(o["text"]), o).get_json()
        assert d["ok"] and d["changed"] == 0
        assert not env["ctx"]["store"].change_log


class TestRebase:
    def test_rediff_applies_only_own_changes(self, env):
        c = env["client"]
        o = _open(c)
        assert c.post("/field/edit", data={"dot_path": "qubits.q1.label",
                                           "value": "b"}).status_code == 200
        doc = json.loads(o["text"])           # still says label = "a"
        doc["T1"] = 9.0e-5
        r = c.post("/tree/json-edit/save", json={
            "path": "qubits.q1", "text": json.dumps(doc), "rebase": True,
            "base_text": o["text"], "base_seq": o["mutation_seq"],
            "open_token": o["open_token"]})
        assert r.status_code == 200, r.get_json()
        assert _peek(c, "qubits.q1.T1")["value"] == 9.0e-5
        assert _peek(c, "qubits.q1.label")["value"] == "b"     # the other edit kept

    def test_rediff_refuses_a_leaf_both_changed(self, env):
        c = env["client"]
        o = _open(c)
        c.post("/field/edit", data={"dot_path": "qubits.q1.label", "value": "b"})
        doc = json.loads(o["text"])
        doc["label"] = "c"
        doc["T1"] = 9.0e-5
        r = c.post("/tree/json-edit/save", json={
            "path": "qubits.q1", "text": json.dumps(doc), "rebase": True,
            "base_text": o["text"], "open_token": o["open_token"]})
        d = r.get_json()
        assert r.status_code == 409 and [x["path"] for x in d["refused"]] == ["qubits.q1.label"]
        assert _peek(c, "qubits.q1.T1")["value"] == 2.0e-5


class TestReviewFixes:
    """Review round over Stage B: each pin was seen RED with its fix reverted."""

    def test_new_dotted_key_is_refused_with_a_reason(self, env):
        c = env["client"]
        o = _open(c)
        doc = json.loads(o["text"])
        doc["a.b"] = 1
        doc["T1"] = 3.0e-5
        r = _save(c, doc, o)
        d = r.get_json()
        assert r.status_code == 409, d
        row = [x for x in d["refused"] if x["path"] == "qubits.q1.a.b"]
        assert row and "'.'" in row[0]["error"] and "use the tree" in row[0]["error"]
        assert not env["ctx"]["store"].change_log

    def test_existing_dotted_key_edit_is_refused_plainly(self, env):
        c = env["client"]
        store = env["ctx"]["store"]
        store.merged["qubits"]["q1"]["x.y"] = 1
        store.state["qubits"]["q1"]["x.y"] = 1
        o = _open(c)
        doc = json.loads(o["text"])
        doc["x.y"] = 2
        d = _save(c, doc, o).get_json()
        assert [x["path"] for x in d["refused"]] == ["qubits.q1.x.y"]
        assert "'.'" in d["refused"][0]["error"]
        assert "not found" not in d["refused"][0]["error"]

    def test_new_key_inside_a_list_element_is_refused(self, env):
        c = env["client"]
        store = env["ctx"]["store"]
        store.merged["qubits"]["q1"]["lst"] = [{"a": 1}]
        store.state["qubits"]["q1"]["lst"] = [{"a": 1}]
        o = _open(c)
        doc = json.loads(o["text"])
        doc["lst"][0]["b"] = 2
        r = _save(c, doc, o)
        d = r.get_json()
        assert r.status_code == 409, d
        assert [x["path"] for x in d["refused"]] == ["qubits.q1.lst.0.b"]
        assert "list element" in d["refused"][0]["error"]

    @pytest.mark.parametrize("tok", ["NaN", "Infinity", "-Infinity"])
    def test_non_json_constants_are_a_parse_error(self, env, tok):
        c = env["client"]
        o = _open(c)
        r = _save(c, None, o, text='{\n "label": ' + tok + '\n}')
        d = r.get_json()
        assert r.status_code == 400 and d["parse_error"], d
        assert d["line"] == 2 and d["col"] == 11
        assert _peek(c, "qubits.q1.label")["value"] == "a"
        assert not env["ctx"]["store"].change_log

    def test_batch_relink_on_a_literal_leaf_is_refused(self, env):
        c = env["client"]
        r = c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.q1.T1", "value": "#./f_01", "relink": True}]})
        assert r.status_code == 400, r.get_json()
        assert env["ctx"]["store"].merged["qubits"]["q1"]["T1"] == 2.0e-5
        assert not env["ctx"]["store"].change_log

    def test_rebase_refuses_a_base_text_that_was_not_served(self, env):
        c = env["client"]
        o = _open(c)
        c.post("/field/edit", data={"dot_path": "qubits.q1.label", "value": "b"})
        forged = json.loads(o["text"])
        forged["T1"] = 9.0e-5                 # claims the base already had it
        doc = json.loads(o["text"])
        doc["T1"] = 9.0e-5
        doc["f_01"] = 6.0e9
        r = c.post("/tree/json-edit/save", json={
            "path": "qubits.q1", "text": json.dumps(doc), "rebase": True,
            "base_text": json.dumps(forged), "open_token": o["open_token"]})
        assert r.status_code == 409, r.get_json()
        assert _peek(c, "qubits.q1.f_01")["value"] == 5.0e9
        r = c.post("/tree/json-edit/save", json={
            "path": "qubits.q1", "text": json.dumps(doc), "rebase": True,
            "base_text": o["text"], "open_token": "not-a-token"})
        assert r.status_code == 409
        assert _peek(c, "qubits.q1.f_01")["value"] == 5.0e9

    def test_relink_to_nothing_saves_with_a_warning(self, env):
        c = env["client"]
        o = _open(c)
        doc = json.loads(o["text"])
        doc["alias"] = "#/qubits/q1/nowhere"
        r = _save(c, doc, o)
        d = r.get_json()
        assert r.status_code == 200 and d["ok"], d
        assert env["ctx"]["store"].merged["qubits"]["q1"]["alias"] == "#/qubits/q1/nowhere"
        assert [w["path"] for w in d["warnings"]] == ["qubits.q1.alias"]
        assert "points to nothing" in d["warnings"][0]["warning"]

    def test_relink_that_resolves_has_no_warning(self, env):
        c = env["client"]
        o = _open(c)
        doc = json.loads(o["text"])
        doc["alias"] = "#./f_01"
        d = _save(c, doc, o).get_json()
        assert d["ok"] and not d.get("warnings")

    def test_pulse_structure_change_is_refused_with_the_pulses_page(self, env):
        c = env["client"]
        o = _open(c, "qubits.q1.resonator.operations")
        doc = json.loads(o["text"])
        doc["new_pulse"] = {"__class__": "quam.components.pulses.SquarePulse",
                            "amplitude": 0.1, "length": 100}
        r = _save(c, doc, o, path="qubits.q1.resonator.operations")
        d = r.get_json()
        assert r.status_code == 409, d
        row = [x for x in d["refused"] if x["path"] == "qubits.q1.resonator.operations.new_pulse"]
        assert row and row[0].get("pulses_page"), d
        assert not env["ctx"]["store"].change_log


# -- the panel, driven against the shipped JS under jsdom --------------------
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _run_panel(mutation=None):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    e = dict(os.environ)
    if mutation:
        e["JSON_EDIT_MUTATION"] = mutation
    r = subprocess.run(["node", "tests/tree_json_edit_selfcheck.cjs"], cwd=_ROOT, env=e,
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    return r


def test_panel_selfcheck():
    r = _run_panel()
    assert r.returncode == 0 and "ALL OK" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("mutation", ["parse", "no_save_on_error", "goto", "ctrls", "esc_dirty",
                                      "rebase", "token", "warn", "large", "one_warn", "dark", "seq", "all_or_nothing_msg"])
def test_panel_mutations(mutation):
    r = _run_panel(mutation)
    assert "mutation target exists" not in r.stderr, r.stderr
    assert r.returncode != 0 and "AssertionError" in r.stderr, r.stdout + r.stderr


def test_container_rows_open_the_json_panel_on_the_tree_page():
    """D-12 (replaced by D-21): the ✎ / Enter on a container in the Json Tree
    page opens this panel, never the old one-container textarea."""
    app = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    body = app.split("function _makeContainerEditable(node, dotPath, value) {", 1)[1][:900]
    assert re.search(r'closest\("#explorer-tree-state, #explorer-tree-wiring"\)', body)
    # the guard together with what it feeds (a string-only pin survives `if (false)`)
    guard = "if (jtHost && window.TreeJsonEdit && window.SmCodeMirror) {"
    assert guard in body and body.index(guard) < body.index("window.TreeJsonEdit.open(")
    assert "window.TreeJsonEdit.open(" in body and "return;" in body.split("TreeJsonEdit.open(", 1)[1][:400]
    tpl = (_ROOT / "quam_state_manager" / "web" / "templates" / "_explorer.html").read_text(encoding="utf-8")
    assert 'id="explorer-json-whole"' in tpl and "TreeJsonEdit.openWhole()" in tpl
