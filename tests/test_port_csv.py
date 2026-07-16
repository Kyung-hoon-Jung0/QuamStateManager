"""core.port_csv — the wizard's port-label-mapping CSV import (docs/54)."""

from __future__ import annotations

import sys
from pathlib import Path

from quam_state_manager.core.port_csv import parse_port_label_csv

sys.path.insert(0, str(Path(__file__).parent))
from cr_fixtures import PORT_LABEL_CSV_1X5  # noqa: E402


class TestParse:
    def test_customer_layout_parses(self):
        p = parse_port_label_csv(PORT_LABEL_CSV_1X5)
        assert p["ok"], p["errors"]
        assert p["qubits"] == ["q0", "q1", "q2", "q3", "q4"]
        # 4 grid edges × 2 directions (CR is directional)
        assert len(p["qubit_pairs"]) == 8
        assert ["q0", "q1"] in p["qubit_pairs"]
        assert ["q1", "q0"] in p["qubit_pairs"]
        # one MW FEM: con1 slot1
        assert p["instruments"]["controllers"] == [
            {"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}]
        # control pins on ports 2..6; shared readout out 1 / IN2
        assert p["pins"]["q0"]["drive"] == {
            "kind": "mw_fem", "con": 1, "slot": 1, "out_port": 2}
        assert p["pins"]["q4"]["drive"]["out_port"] == 6
        assert p["pins"]["q0"]["resonator"] == {
            "kind": "mw_fem", "con": 1, "slot": 1, "out_port": 1, "in_port": 2}
        # single row: all grid y == 0, x = column
        assert p["grid"]["q0"] == "0,0" and p["grid"]["q4"] == "4,0"
        assert p["feedlines"]["q0"] == "mux0_0"
        assert p["warnings"] == []

    def test_bom_tolerated(self):
        p = parse_port_label_csv("﻿" + PORT_LABEL_CSV_1X5)
        assert p["ok"], p["errors"]

    def test_duplicate_port_rejected(self):
        bad = PORT_LABEL_CSV_1X5.replace(",1,1,6", ",1,1,3")  # q4 onto q1's port
        p = parse_port_label_csv(bad)
        assert not p["ok"]
        assert any("already used" in e for e in p["errors"])

    def test_missing_columns_rejected(self):
        p = parse_port_label_csv("a,b,c\n1,2,3\n")
        assert not p["ok"]
        assert any("missing CSV columns" in e for e in p["errors"])

    def test_empty_rejected(self):
        p = parse_port_label_csv("")
        assert not p["ok"]

    def test_missing_readout_rows_fall_back_with_warning(self):
        # strip the two readout rows → (fem, 1, 1) fallback + a warning
        lines = [ln for ln in PORT_LABEL_CSV_1X5.splitlines()
                 if "readout" not in ln]
        p = parse_port_label_csv("\n".join(lines))
        assert p["ok"], p["errors"]
        assert p["pins"]["q0"]["resonator"]["out_port"] == 1
        assert p["pins"]["q0"]["resonator"]["in_port"] == 1
        assert any("fallback" in w for w in p["warnings"])

    def test_mux_overflow_rejected(self):
        # 9 qubits on one mux exceeds the feedline multiplex bound (8)
        rows = PORT_LABEL_CSV_1X5.splitlines()
        header, body = rows[0], rows[1:]
        extra = [f"{i},0,0,0,control Q{i} ,0,{i},0,{i},1,1,{i + 2}"
                 for i in range(5, 9)]
        p = parse_port_label_csv("\n".join([header] + body + extra))
        assert not p["ok"]
        assert any("multiplex bound" in e for e in p["errors"])
