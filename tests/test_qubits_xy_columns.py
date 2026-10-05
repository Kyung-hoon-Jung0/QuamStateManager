"""The Qubits component table shows the XY drive (docs/286).

Before this the table carried frequencies, coherence and the readout pulse but
no drive amplitude at all. These pins hold the new columns to four promises:

* the value is the one the pulse IN FORCE carries -- the ``x180`` operation
  followed through its alias, never a sibling pulse that merely shares the
  name prefix, and never the raw pointer string;
* the dBm beside it is ``physical_units.amp_annotation``'s, the same call the
  Live State Edit grid makes, so the two pages cannot disagree;
* a qubit with no such operation shows an em dash that says why;
* a lab's own operation names work through the same alias rule.
"""

from __future__ import annotations

import json
import math
from html.parser import HTMLParser
from pathlib import Path

import pytest

from quam_state_manager.core import xy_drive
from quam_state_manager.core.param_specs import _BULK_COLUMNS_SPEC
from quam_state_manager.web.app import create_app

_QC = "quam.components.pulses."
_EM = chr(0x2014)   # em dash


def _drag(amp, length=48, alpha=-0.24):
    return {"__class__": _QC + "DragCosinePulse", "amplitude": amp,
            "length": length, "axis_angle": 0.0, "alpha": alpha,
            "anharmonicity": -2.0e8, "detuning": 0.0}


def _square(amp, length=40):
    return {"__class__": _QC + "SquarePulse", "amplitude": amp,
            "length": length, "axis_angle": 0.0}


def _qubit(qid, ops, rf=5.0e9):
    return {
        "id": qid, "f_01": rf,
        "xy": {"opx_output": f"#/wiring/qubits/{qid}/xy/opx_output",
               "RF_frequency": rf,
               "intermediate_frequency": "#./inferred_intermediate_frequency",
               "LO_frequency": "#./upconverter_frequency",
               "operations": ops},
        "resonator": {"opx_output": f"#/wiring/qubits/{qid}/rr/opx_output",
                      "operations": {"readout": {
                          "__class__": _QC + "SquareReadoutPulse",
                          "amplitude": 0.05, "length": 800}}},
    }


def _state():
    return {
        "qubits": {
            # 1. a DIRECT value: the pulse lives inline under the op name
            "qA1": _qubit("qA1", {"x180": _square(0.2), "x90": _square(0.1)}),
            # 2. the builder alias: x180 -> x180_DragCosine. A sibling
            #    x180_Square with a different amplitude is the decoy -- the
            #    alias decides, not the prefix. x90's length and alpha are
            #    themselves pointers into the x180 pulse.
            "qA2": _qubit("qA2", {
                "x180": "#./x180_DragCosine",
                "x90": "#./x90_DragCosine",
                "x180_DragCosine": _drag(0.3530),
                "x90_DragCosine": dict(_drag(0.1765),
                                       length="#../x180_DragCosine/length",
                                       alpha="#../x180_DragCosine/alpha"),
                "x180_Square": _square(0.1),
            }),
            # 3. no x90 at all; its DRAG alpha is a run-time alias (a pointer
            #    that ends in no stored number) -- shown blank, never as text
            "qA3": _qubit("qA3", {"x180": "#./x180_DragCosine",
                                  "x180_DragCosine": dict(
                                      _drag(0.25), alpha="#./inferred_alpha")}),
            # 4. a lab's own operation name, reached through the alias
            "qA4": _qubit("qA4", {"x180": "#./x180_LabShape",
                                  "x90": "#./x90_LabShape",
                                  "x180_LabShape": _square(0.4, length=60),
                                  "x90_LabShape": _square(0.2, length=60)}),
            # 5. a lab pulse with NO x180 operation: which one plays is not
            #    stated, so nothing is picked
            "qA5": _qubit("qA5", {"x180_LabShape": _square(0.4)}),
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1", "qA2", "qA3", "qA4", "qA5"],
        "ports": {"mw_outputs": {"con1": {"1": {
            str(i): {"band": 2, "full_scale_power_dbm": 0,
                     "upconverter_frequency": 5.1e9}
            for i in range(1, 7)}}}},
    }


def _wiring():
    w = {}
    for i, qid in enumerate(("qA1", "qA2", "qA3", "qA4", "qA5"), start=2):
        w[qid] = {"xy": {"opx_output": f"#/ports/mw_outputs/con1/1/{i}"},
                  "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1"}}
    return {"network": {"host": "127.0.0.1", "port": 1},
            "wiring": {"qubits": w}}


def _write_chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    return folder


@pytest.fixture
def store(tmp_path):
    from quam_state_manager.core.loader import QuamStore
    return QuamStore(str(_write_chip(tmp_path / "chip")))


@pytest.fixture
def client(tmp_path):
    live = _write_chip(tmp_path / "chips" / "live")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return c


class _Table(HTMLParser):
    """Header labels + per-row cells (text, attrs) of ``#qubits-table``."""

    def __init__(self):
        super().__init__()
        self.heads: list[str] = []
        self.rows: dict[str, list[dict]] = {}
        self._in = False
        self._depth = 0
        self._row = None
        self._cell = None
        self._th = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and a.get("id") == "qubits-table":
            self._in = True
        if not self._in:
            return
        if tag == "th":
            self._th = []
        elif tag == "tr" and a.get("data-qubit-id"):
            self._row = self.rows.setdefault(a["data-qubit-id"], [])
        elif tag == "td" and self._row is not None:
            self._cell = {"text": "", "attrs": a, "titles": [a.get("title") or ""]}
        elif self._cell is not None and a.get("title"):
            self._cell["titles"].append(a["title"])

    def handle_endtag(self, tag):
        if not self._in:
            return
        if tag == "th" and self._th is not None:
            self.heads.append(" ".join("".join(self._th).split()))
            self._th = None
        elif tag == "td" and self._cell is not None:
            self._cell["text"] = self._cell["text"].strip()
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr":
            self._row = None
        elif tag == "table":
            self._in = False

    def handle_data(self, data):
        if self._th is not None:
            self._th.append(data)
        elif self._cell is not None:
            self._cell["text"] += data


def _table(client) -> _Table:
    html = client.get("/qubits?per_page=50").get_data(as_text=True)
    t = _Table()
    t.feed(html)
    return t


def _cell(t: _Table, qid: str, head_prefix: str) -> dict:
    idx = [i for i, h in enumerate(t.heads) if h.startswith(head_prefix)]
    assert len(idx) == 1, (head_prefix, t.heads)
    return t.rows[qid][idx[0]]


class TestColumnsExist:
    def test_every_row_has_every_column(self, client):
        t = _table(client)
        for h in ("XY IF", "x180 Amp", "P(x180)", "x90 Amp", "P(x90)",
                  "x180 Len", "DRAG"):
            assert any(x.startswith(h) for x in t.heads), (h, t.heads)
        assert set(t.rows) == {"qA1", "qA2", "qA3", "qA4", "qA5"}
        for qid, cells in t.rows.items():
            assert len(cells) == len(t.heads), (qid, len(cells), len(t.heads))

    def test_the_empty_table_spans_every_column(self):
        src = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
               / "templates" / "_qubits.html").read_text(encoding="utf-8")
        n_heads = src.count('<th class="sortable"')
        assert f'colspan="{n_heads}"' in src

    def test_the_paths_are_the_live_edit_grids_paths(self):
        """One vocabulary: the amplitude leaf this page reads IS the curated
        Live-Edit column's template."""
        tmpl = {c["key"]: c["tmpl"] for c in _BULK_COLUMNS_SPEC}
        assert xy_drive.OP_TEMPLATES["x180"] + ".amplitude" == tmpl["x180_amplitude"]
        assert xy_drive.OP_TEMPLATES["x90"] + ".amplitude" == tmpl["x90_amplitude"]


class TestDirectValue:
    def test_inline_pulse_amplitudes(self, client):
        t = _table(client)
        assert _cell(t, "qA1", "x180 Amp")["text"] == "0.2000"
        assert _cell(t, "qA1", "x90 Amp")["text"] == "0.1000"
        assert _cell(t, "qA1", "x180 Len")["text"] == "40"

    def test_inline_pulse_dbm(self, client):
        t = _table(client)
        p = _cell(t, "qA1", "P(x180)")
        # FSP 0 dBm, a Square pulse peaks at its amplitude: 20*log10(0.2)
        assert p["text"] == f"{20 * math.log10(0.2):.1f} dBm" == "-14.0 dBm"
        assert float(p["attrs"]["data-dbm"]) == pytest.approx(20 * math.log10(0.2))
        assert _cell(t, "qA1", "P(x90)")["text"] == "-20.0 dBm"

    def test_square_pulse_has_no_drag_alpha(self, client):
        c = _cell(_table(client), "qA1", "DRAG")
        assert c["text"] == _EM
        assert any("no such field" in x for x in c["titles"])


class TestAliasInForce:
    def test_alias_value_not_the_prefix_sibling(self, client):
        t = _table(client)
        c = _cell(t, "qA2", "x180 Amp")
        assert c["text"] == "0.3530"            # x180_DragCosine, not x180_Square's 0.1
        assert any("x180_DragCosine" in x for x in c["titles"])
        assert _cell(t, "qA2", "x90 Amp")["text"] == "0.1765"
        assert _cell(t, "qA2", "x180 Len")["text"] == "48"
        assert _cell(t, "qA2", "DRAG")["text"] == "-0.24"

    def test_no_pointer_string_is_ever_a_value(self, client):
        t = _table(client)
        for qid, cells in t.rows.items():
            for c in cells:
                assert "#/" not in c["text"] and "#./" not in c["text"] \
                    and "#../" not in c["text"], (qid, c)

    def test_pointer_leaves_resolve(self, store):
        """x90's length and alpha are pointers into the x180 pulse; the row
        shows the numbers they reach."""
        d = xy_drive.summary(store, "qA2")
        assert d["x90"]["length"]["value"] == 48
        assert d["x90"]["alpha"]["value"] == -0.24
        assert d["x180"]["pulse"] == "x180_DragCosine"

    def test_drive_if_is_rf_minus_lo(self, client):
        c = _cell(_table(client), "qA2", "XY IF")
        # RF 5.0 GHz on a port whose LO is 5.1 GHz: quam's RF - LO
        assert float(c["text"].replace(",", "")) == pytest.approx(-100.0)


class TestSameNumberAsTheGrid:
    def test_amplitude_and_dbm_match_the_bulk_cell(self, store):
        from quam_state_manager.web.routes import _build_bulk_cell
        for qid in ("qA1", "qA2", "qA4"):
            for op in ("x180", "x90"):
                path = f"qubits.{qid}.xy.operations.{op}.amplitude"
                cell = _build_bulk_cell(store.merged, path, {}, {}, qid)
                rec = xy_drive.summary(store, qid)[op]
                assert rec["amplitude"]["value"] == cell["display"] or \
                    float(cell["display"]) == rec["amplitude"]["value"], (qid, op)
                assert rec["phys"]["text"] == cell["phys"]["text"], (qid, op)
                assert rec["phys"]["dbm"] == pytest.approx(
                    cell["phys"]["dbm"], abs=1e-12), (qid, op)


class TestMissingPulse:
    def test_no_x90_is_an_em_dash_that_says_why(self, client):
        t = _table(client)
        amp, p = _cell(t, "qA3", "x90 Amp"), _cell(t, "qA3", "P(x90)")
        assert amp["text"] == _EM and p["text"] == _EM
        assert any("No 'x90' operation" in x for x in amp["titles"])
        assert amp["attrs"].get("data-sort") == "-"     # sorts below every number
        # the x180 of the same qubit is unaffected
        assert _cell(t, "qA3", "x180 Amp")["text"] == "0.2500"
        # a run-time alias is a blank with its reason, not the pointer
        alpha = _cell(t, "qA3", "DRAG")
        assert alpha["text"] == _EM
        assert any("pointer" in x for x in alpha["titles"])


class TestLabOperationNames:
    def test_lab_named_pulse_through_the_alias(self, client):
        t = _table(client)
        c = _cell(t, "qA4", "x180 Amp")
        assert c["text"] == "0.4000"
        assert any("x180_LabShape" in x for x in c["titles"])
        assert _cell(t, "qA4", "x90 Amp")["text"] == "0.2000"
        assert _cell(t, "qA4", "x180 Len")["text"] == "60"

    def test_no_alias_names_the_candidates_and_picks_none(self, client):
        t = _table(client)
        c = _cell(t, "qA5", "x180 Amp")
        assert c["text"] == _EM
        assert any("x180_LabShape" in x and "not stated" in x for x in c["titles"])
        assert _cell(t, "qA5", "P(x180)")["text"] == _EM


class TestBlankReasons:
    def test_text_amplitude_is_quoted_not_converted(self, tmp_path):
        from quam_state_manager.core.loader import QuamStore
        st = _state()
        st["qubits"]["qA1"]["xy"]["operations"]["x180"]["amplitude"] = "0.2"
        folder = tmp_path / "txt"
        folder.mkdir()
        (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
        d = xy_drive.summary(QuamStore(str(folder)), "qA1")["x180"]
        assert d["amplitude"]["state"] == "text"
        assert d["phys"] is None and d["phys_why"]

    def test_runtime_pointer_is_blank_not_shown(self, tmp_path):
        from quam_state_manager.core.loader import QuamStore
        st = _state()
        st["qubits"]["qA1"]["xy"]["operations"]["x180"]["length"] = "#./inferred_length"
        folder = tmp_path / "rt"
        folder.mkdir()
        (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
        d = xy_drive.summary(QuamStore(str(folder)), "qA1")["x180"]["length"]
        assert d["state"] == "blank" and d["value"] is None
        assert "pointer" in d["why"]
