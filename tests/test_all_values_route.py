"""Route-level tests for GET /bulk/all-values — the flat 'All values' completeness
payload + its gzip / ETag / 304 transport contract (the perf red-team's must-fixes).
"""

from __future__ import annotations

import gzip as _gzip
import json as _json
import re
import time
from pathlib import Path

import pytest

from quam_state_manager.core.leaf_classify import ALL_KINDS, READONLY_KINDS
from quam_state_manager.web.app import create_app


def _state() -> dict:
    return {
        "qubits": {"qA1": {
            "__class__": "quam.components.Transmon",
            "id": "qA1",
            "f_01": 6.25e9,
            "f_12": None,
            "xy": {"intermediate_frequency": "#./inferred_intermediate_frequency",
                   "operations": {"x180": "#./x180_DragCosine",
                                  "x180_DragCosine": {"amplitude": 0.11, "digital_marker": "ON"}}},
            "z": {"opx_output": "#/wiring/qubits/qA1/z/opx_output", "joint_offset": 0.05},
            "resonator": {"time_of_flight": 376,
                          "confusion_matrix": [[0.98, 0.02], [0.03, 0.97]]},
        }},
        "qubit_pairs": {},
        "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 2, "upconverter_frequency": 5.05e9}}}}},
        "twpas": {"twpaA": {"frequency": 6.0e9}},
        "active_qubit_names": ["qA1"],
        "active_twpa_names": ["twpaA"],
    }


def _wiring() -> dict:
    return {"wiring": {"qubits": {"qA1": {"z": {"opx_output": "#/ports/analog_outputs/con1/5"}}}},
            "network": {"host": "10.0.0.1"}}


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / "state.json").write_text(_json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(_json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    return c


def _decode(resp) -> dict:
    raw = resp.get_data()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = _gzip.decompress(raw)
    return _json.loads(raw)


class TestCompletenessPayload:
    def test_total_partitions_and_matches_rows(self, client):
        data = _decode(client.get("/bulk/all-values"))
        s = data["summary"]
        assert s["total"] == len(data["rows"])
        assert sum(s["by_kind"].values()) == s["total"]
        assert s["editable"] + s["readonly"] == s["total"]
        # the user's flagship leaf is an editable row
        tof = [r for r in data["rows"] if r[0] == "qubits.qA1.resonator.time_of_flight"]
        assert tof and tof[0][2] == "scalar"
        # chip-level twpa leaf (0% reachable in the curated grid) is editable here
        assert any(r[0].startswith("twpas.") and r[2] == "scalar" for r in data["rows"])

    def test_every_kind_valid_and_readonly_never_modified(self, client):
        data = _decode(client.get("/bulk/all-values"))
        for path, _disp, kind, mod in data["rows"]:
            assert kind in ALL_KINDS
            if kind in READONLY_KINDS:
                assert mod == 0, f"{path}: read-only row must not carry a modified flag"

    def test_membership_and_pointers_are_readonly_kinds(self, client):
        rows = {r[0]: r[2] for r in _decode(client.get("/bulk/all-values"))["rows"]}
        assert rows["active_qubit_names.0"] == "membership"
        assert rows["active_twpa_names.0"] == "membership"
        assert rows["qubits.qA1.z.opx_output"] == "xref"
        assert rows["qubits.qA1.xy.intermediate_frequency"] == "selfref"
        assert rows["qubits.qA1.resonator.confusion_matrix.0.0"] == "list"
        assert rows["qubits.qA1.__class__"] == "skip"


class TestEditToLiveChain:
    """The headline data-safety chain (audit P2: was uncovered): an edit → apply-to-live
    writes the LIVE state.json AND captures a History snapshot containing it, and the apply
    response uses the corrected (no-stateRestored) trigger."""

    @pytest.fixture
    def chip(self, tmp_path: Path):
        (tmp_path / "state.json").write_text(_json.dumps(_state()), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(_json.dumps(_wiring()), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path)})
        return c, tmp_path

    def test_edit_applies_to_live_and_snapshots(self, chip):
        import glob
        import os
        c, folder = chip
        c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.f_01", "value": "6.4e9"}]})
        r = c.post("/state/apply-to-live")
        assert r.status_code == 200
        live = _json.loads((folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 6.4e9     # the edit reached the live chip
        snaps = glob.glob(os.path.join(str(folder), "_i", "history", "**", "state.json"), recursive=True)
        assert any(_json.loads(open(p).read()).get("qubits", {}).get("qA1", {}).get("f_01") == 6.4e9
                   for p in snaps), "no History snapshot captured the applied edit"
        assert "stateRestored" not in r.headers.get("HX-Trigger", "")


class TestServerEditabilityGate:
    """Audit P0: the read-only safety policy (membership arrays / list-matrix elements /
    identity keys) is enforced SERVER-SIDE on /field/edit-batch — not just in the
    All-values client render — so a crafted/buggy POST can't mutate the dangerous leaves.
    Pointers stay client-policy (legit surfaces write them), and a plain scalar still applies."""

    def test_membership_array_edit_rejected_and_rolled_back(self, client):
        jb = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "active_qubit_names.0", "value": "qZZ9"}]}).get_json()
        assert jb["ok"] is False
        assert any("read-only" in (r.get("error") or "") or "membership" in (r.get("error") or "")
                   for r in jb["results"])
        # the membership array is unchanged
        peek = client.get("/field/peek?dot_path=active_qubit_names.0").get_json()
        assert peek["values"].get("active_qubit_names.0") in ("qA1", None)

    def test_list_matrix_element_edit_rejected(self, client):
        jb = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.resonator.confusion_matrix.0.0", "value": "0.5"}]}).get_json()
        assert jb["ok"] is False

    def test_identity_key_edit_rejected(self, client):
        jb = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.__class__", "value": "evil"}]}).get_json()
        assert jb["ok"] is False

    def test_plain_scalar_still_applies(self, client):
        jb = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.f_01", "value": "6.3e9"}]}).get_json()
        assert jb["ok"] is True and jb["results"][0]["applied"] is True

    def test_one_bad_target_rolls_back_the_good_one(self, client):
        # atomic: a read-only target in the batch rolls back the legit scalar too
        jb = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.f_01", "value": "5.5e9"},
            {"dot_path": "active_qubit_names.0", "value": "qZZ"}]}).get_json()
        assert jb["ok"] is False
        peek = client.get("/field/peek?dot_path=qubits.qA1.f_01").get_json()
        assert peek["values"]["qubits.qA1.f_01"] == 6.25e9   # unchanged (rolled back)


class TestTransport:
    def test_gzip_roundtrip_identical_to_raw(self, client):
        gz = client.get("/bulk/all-values", headers={"Accept-Encoding": "gzip"})
        raw = client.get("/bulk/all-values", headers={"Accept-Encoding": "identity"})
        # gzip branch stamps the encoding + a Content-Length equal to the compressed bytes
        assert gz.headers.get("Content-Encoding") == "gzip"
        assert int(gz.headers["Content-Length"]) == len(gz.get_data())
        assert gz.headers.get("Vary") == "Accept-Encoding"
        # raw branch must NOT claim gzip
        assert raw.headers.get("Content-Encoding") is None
        # both decode to byte-identical JSON
        assert _decode(gz) == _decode(raw)

    def test_etag_folds_changelog_and_changes_on_edit(self, client):
        e1 = client.get("/bulk/all-values").headers["ETag"]
        # 3 components: chip-<mutation_seq>-<len(change_log)>
        assert e1.strip('"').count("-") == 2
        client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.resonator.time_of_flight", "value": "284"}]})
        e2 = client.get("/bulk/all-values").headers["ETag"]
        assert e2 != e1, "an edit (mutation_seq + change_log both move) must change the ETag"

    def test_304_on_unchanged_chip(self, client):
        e1 = client.get("/bulk/all-values").headers["ETag"]
        r = client.get("/bulk/all-values", headers={"If-None-Match": e1})
        assert r.status_code == 304
        assert r.headers["ETag"] == e1
        assert r.get_data() == b""

    def test_modified_flag_reflects_an_unsaved_edit(self, client):
        client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.f_01", "value": "6.3e9"}]})
        rows = {r[0]: r for r in _decode(client.get("/bulk/all-values"))["rows"]}
        assert rows["qubits.qA1.f_01"][3] == 1            # edited-but-unsaved → amber
        assert rows["qubits.qA1.resonator.time_of_flight"][3] == 0


class TestEdgeCases:
    def test_empty_context_returns_zeroes_not_500(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        r = app.test_client().get("/bulk/all-values")
        assert r.status_code == 200
        data = _json.loads(r.get_data())
        assert data["rows"] == [] and data["summary"]["total"] == 0


_STATIC = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"
_TPL = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "templates"


class TestStaticCoupling:
    """Guard the JS↔CSS invariants the perf red-team flagged — the datasets
    zero-height/overlap bug-class is purely a drift between these constants."""

    def test_row_height_js_matches_css(self):
        js = (_STATIC / "all-values.js").read_text(encoding="utf-8")
        css = (_STATIC / "style.css").read_text(encoding="utf-8")
        assert "var ROW_HEIGHT = 28;" in js, "all-values.js ROW_HEIGHT must be 28"
        # the CSS rule the JS math depends on (whitespace-tolerant)
        assert re.search(r"\.av-table-virtual tbody tr\s*\{\s*height:\s*28px", css), \
            "CSS .av-table-virtual tbody tr must be height:28px to match ROW_HEIGHT"

    def test_scroll_uses_contain_content_not_strict(self):
        css = (_STATIC / "style.css").read_text(encoding="utf-8")
        block = css[css.index(".av-scroll {"):]
        block = block[:block.index("}")]
        # strip comments so the warning text ("strict bundles contain: size") in the
        # block can't trip the declaration check
        block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
        assert "contain: content" in block
        assert "contain: strict" not in block and "contain: size" not in block

    def test_input_focus_swaps_color_not_width(self):
        # focus must add 0px (border-COLOR only) so a focused row stays exactly 28px
        css = (_STATIC / "style.css").read_text(encoding="utf-8")
        focus = css[css.index(".av-input:focus {"):]
        focus = focus[:focus.index("}")]
        assert "border-color:" in focus
        # the path + value cells must never wrap (a 2nd line desyncs the window)
        assert "white-space: nowrap" in css[css.index(".av-cell-path, .av-cell-val"):][:200]

    def test_base_includes_all_values_js(self):
        base = (_TPL / "base.html").read_text(encoding="utf-8")
        assert "all-values.js" in base

    def test_r2_ux_a1_a4_a5(self):
        css = (_STATIC / "style.css").read_text(encoding="utf-8")
        js = (_STATIC / "all-values.js").read_text(encoding="utf-8")
        tpl = (_TPL / "_bulkedit.html").read_text(encoding="utf-8")
        # A1: the input is tightened to the read-only span's 18px band + font scales by --av-fs
        avin = css[css.index(".av-input { width:"):][:300]
        assert "height: 18px" in avin and "line-height: 18px" in avin and "--av-fs" in avin
        assert "--av-fs: 1" in css  # the scroller declares the var
        # A2: soft-blue leaf hover (paint only)
        assert ".av-table-virtual tbody tr.av-leaf:hover td" in css
        # A4: split toolbar so coverage can't push the action buttons to a 2nd line
        assert ".av-toolbar-left" in css and ".av-toolbar-right" in css
        assert "av-toolbar-left" in tpl and "av-toolbar-right" in tpl
        # A5: Enter in a scalar input applies that one field
        assert "onTbodyKeydown" in js and "function applyOne" in js
        assert "'keydown', onTbodyKeydown" in js


def _big_state(n_qubits: int = 150) -> dict:
    """A chip with enough editable scalar leaves to stress a large Apply-all."""
    qubits = {}
    for i in range(n_qubits):
        qid = f"q{i:03d}"
        qubits[qid] = {
            "id": qid, "f_01": 5.0e9 + i, "f_12": 4.8e9 + i, "chi": -0.5e6,
            "anharmonicity": -2.2e8, "T1": 2.4e-5, "T2ramsey": 2.2e-5, "T2echo": 2.0e-5,
            "resonator": {"time_of_flight": 376, "depletion_time": 3000,
                          "operations": {"readout": {"amplitude": 0.04, "length": 1000}}},
            "z": {"joint_offset": 0.05, "min_offset": 0.0, "settle_time": 64},
        }
    return {"qubits": qubits, "qubit_pairs": {}, "active_qubit_names": list(qubits)}


class TestLargeApplyThroughput:
    """Pre-ship gate for the lock-hold concern: the server must apply a multi-
    thousand-edit atomic batch (what an Apply-all of the flat list can POST) in
    sane wall-time. The frontend additionally chunks, but the server must not choke."""

    def test_two_thousand_edit_batch_applies(self, tmp_path):
        (tmp_path / "state.json").write_text(_json.dumps(_big_state()), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(_json.dumps({"network": {"host": "1.1.1.1"}}), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path)})
        rows = _decode(c.get("/bulk/all-values"))["rows"]
        scal = [r[0] for r in rows if r[2] == "scalar"][:2000]
        assert len(scal) >= 1500, f"need many scalars to stress the batch, got {len(scal)}"
        updates = [{"dot_path": p, "value": "1.23"} for p in scal]
        t0 = time.perf_counter()
        jb = c.post("/field/edit-batch", json={"updates": updates}).get_json()
        dt = time.perf_counter() - t0
        assert jb["ok"] is True and all(r["applied"] for r in jb["results"])
        assert dt < 5.0, f"a {len(updates)}-edit atomic batch took {dt:.2f}s (>5s ceiling)"
