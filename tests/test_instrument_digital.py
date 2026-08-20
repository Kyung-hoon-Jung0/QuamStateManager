"""Digital trigger ports on the Instrument Wiring diagram (docs/126 follow-up).

The customer's QDAC-biased chip wires 11 trigger lines through OPX digital
outputs (``wiring.qubits.<q>.qt.digital_output`` → ``#/ports/digital_outputs/…``,
referenced from the state channel ``z.opx_trigger_out.digital_outputs.trigger``)
— and the rack diagram used to collect NONE of it: no digital ref was read, so
the ports were absent from the drawing and from the refs_seen honesty counter.

Pins here:
  * both carrier shapes place (state channel scan with two-hop pointer follow;
    wiring-level ``qt`` fallback), and together they place ONCE (dedup);
  * digital port numbers never merge into the analog buckets (both count 1..8);
  * a shared trigger port carries every qubit on it (real chips share);
  * honesty: an unfollowable ref counts seen-not-placed; a digital-only FEM
    is typed ``fem`` (its LF/MW flavor is unknowable from digital alone);
  * the payload shape is additive — a chip with no digital wiring gets empty
    ``digital_ports`` maps and ``max_digital_port == 0``, nothing else moves.

The jsdom half (DIG sub-column rendering, popup fields, byte-identical
no-digital layout) lives in tests/instrument_digital_selfcheck.cjs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.query import QueryEngine


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _ports(digital: dict | None = None) -> dict:
    base = {
        "analog_outputs": {"con1": {"4": {"1": {}, "2": {}}}},
        "mw_outputs": {"con1": {"1": {"1": {"band": 1}}}},
    }
    if digital:
        base["digital_outputs"] = digital
    return base


def _engine(state: dict, wiring: dict) -> QueryEngine:
    return QueryEngine(QuamStore.from_dicts(state, wiring))


def _digital_ports(out: dict, ctrl: str = "con1", fem: str = "4") -> dict:
    return out["controllers"][ctrl]["fems"][fem]["digital_ports"]


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


class TestDigitalCollection:
    def test_qt_wiring_only_placed(self):
        """The docs/119 wiring line alone (state channel degraded away)."""
        state = {"qubits": {"q1": {}}}
        wiring = {
            "wiring": {"qubits": {"q1": {
                "qt": {"digital_output": "#/ports/digital_outputs/con1/4/1"},
            }}},
            "ports": _ports({"con1": {"4": {"1": {}}}}),
        }
        out = _engine(state, wiring).get_instrument_wiring()
        dp = _digital_ports(out)
        assert list(dp.keys()) == ["1"]
        (a,) = dp["1"]
        assert a["role"] == "digital"
        assert a["label"] == "q1.trigger"
        assert a["source"] == "qt"

    def test_channel_scan_follows_two_hop_pointer(self):
        """The real-chip shape: state channel → #/wiring/... → #/ports/...,
        with the DigitalOutputChannel metadata carried into the assignment."""
        state = {"qubits": {"q1": {"z": {"opx_trigger_out": {
            "digital_outputs": {"trigger": {
                "opx_output": "#/wiring/qubits/q1/qt/digital_output",
                "delay": 0, "buffer": 12, "shareable": True, "inverted": None,
            }},
        }}}}}
        wiring = {
            "wiring": {"qubits": {"q1": {
                "qt": {"digital_output": "#/ports/digital_outputs/con1/4/3"},
            }}},
            "ports": _ports({"con1": {"4": {"3": {}}}}),
        }
        out = _engine(state, wiring).get_instrument_wiring()
        dp = _digital_ports(out)
        (a,) = dp["3"]
        assert a["label"] == "q1.trigger"
        assert a["source"] == "z.opx_trigger_out"
        assert a["marker"] == "trigger"
        assert a["delay"] == 0 and a["buffer"] == 12 and a["shareable"] is True

    def test_channel_and_qt_describe_one_connection(self):
        """Both shapes present (every real QDAC chip) → placed ONCE, and the
        metadata-rich channel reading wins."""
        state = {"qubits": {"q1": {"z": {"opx_trigger_out": {
            "digital_outputs": {"trigger": {
                "opx_output": "#/wiring/qubits/q1/qt/digital_output",
                "delay": 0, "buffer": 0, "shareable": True,
            }},
        }}}}}
        wiring = {
            "wiring": {"qubits": {"q1": {
                "qt": {"digital_output": "#/ports/digital_outputs/con1/4/1"},
            }}},
            "ports": _ports({"con1": {"4": {"1": {}}}}),
        }
        out = _engine(state, wiring).get_instrument_wiring()
        dp = _digital_ports(out)
        assert len(dp["1"]) == 1
        assert dp["1"][0]["source"] == "z.opx_trigger_out"

    def test_shared_port_carries_every_qubit(self):
        """shareable=true triggers really share a port (q1/q9/q17 on the
        customer's older snapshot) — one port row, N assignments."""
        qs = {}
        wq = {}
        for q in ("q1", "q9", "q17"):
            qs[q] = {"z": {"opx_trigger_out": {"digital_outputs": {"trigger": {
                "opx_output": f"#/wiring/qubits/{q}/qt/digital_output",
                "shareable": True,
            }}}}}
            wq[q] = {"qt": {"digital_output": "#/ports/digital_outputs/con1/4/1"}}
        out = _engine({"qubits": qs}, {
            "wiring": {"qubits": wq},
            "ports": _ports({"con1": {"4": {"1": {}}}}),
        }).get_instrument_wiring()
        dp = _digital_ports(out)
        assert sorted(a["element"] for a in dp["1"]) == ["q1", "q17", "q9"]

    def test_digital_never_merges_into_analog(self):
        """Digital port 1 and analog output 1 on the SAME FEM are different
        physical connectors — a shared bucket would fold the trigger into the
        flux line."""
        state = {"qubits": {"q1": {"z": {}}}}
        wiring = {
            "wiring": {"qubits": {"q1": {
                "z": {"opx_output": "#/ports/analog_outputs/con1/4/1"},
                "qt": {"digital_output": "#/ports/digital_outputs/con1/4/1"},
            }}},
            "ports": _ports({"con1": {"4": {"1": {}}}}),
        }
        out = _engine(state, wiring).get_instrument_wiring()
        fem = out["controllers"]["con1"]["fems"]["4"]
        assert [a["role"] for a in fem["output_ports"]["1"]] == ["z"]
        assert [a["role"] for a in fem["digital_ports"]["1"]] == ["digital"]

    def test_resonator_marker_channel(self):
        """A readout marker on the resonator channel (non-QDAC lab shape),
        direct one-hop #/ports ref."""
        state = {"qubits": {"q2": {"resonator": {
            "digital_outputs": {"marker": {
                "opx_output": "#/ports/digital_outputs/con1/1/2",
                "delay": 57,
            }},
        }}}}
        wiring = {"wiring": {"qubits": {}},
                  "ports": _ports({"con1": {"1": {"2": {}}}})}
        out = _engine(state, wiring).get_instrument_wiring()
        dp = _digital_ports(out, fem="1")
        (a,) = dp["2"]
        assert a["label"] == "q2.marker"
        assert a["source"] == "resonator"
        assert a["delay"] == 57

    def test_digital_only_fem_is_typed_fem(self):
        """A slot seen only through digital refs can't claim LF or MW."""
        state = {"qubits": {"q1": {}}}
        wiring = {
            "wiring": {"qubits": {"q1": {
                "qt": {"digital_output": "#/ports/digital_outputs/con1/7/1"},
            }}},
            "ports": _ports({"con1": {"7": {"1": {}}}}),
        }
        out = _engine(state, wiring).get_instrument_wiring()
        assert out["controllers"]["con1"]["fems"]["7"]["type"] == "fem"

    def test_analog_fems_keep_their_type(self):
        state = {"qubits": {"q1": {"z": {}}}}
        wiring = {
            "wiring": {"qubits": {"q1": {
                "z": {"opx_output": "#/ports/analog_outputs/con1/4/1"},
                "xy": {"opx_output": "#/ports/mw_outputs/con1/1/1"},
                "qt": {"digital_output": "#/ports/digital_outputs/con1/4/2"},
            }}},
            "ports": _ports({"con1": {"4": {"2": {}}}}),
        }
        out = _engine(state, wiring).get_instrument_wiring()
        fems = out["controllers"]["con1"]["fems"]
        assert fems["4"]["type"] == "lf-fem"
        assert fems["1"]["type"] == "mw-fem"

    def test_refs_stats_count_digital(self):
        """The honesty counter must see digital connections — an unplaceable
        rack message sized off refs_seen would otherwise undercount."""
        state = {"qubits": {"q1": {}}}
        wiring_no_dig = {"wiring": {"qubits": {"q1": {
            "xy": {"opx_output": "#/ports/mw_outputs/con1/1/1"},
        }}}, "ports": _ports()}
        base = _engine(state, wiring_no_dig).get_instrument_wiring()["stats"]
        wiring_dig = {"wiring": {"qubits": {"q1": {
            "xy": {"opx_output": "#/ports/mw_outputs/con1/1/1"},
            "qt": {"digital_output": "#/ports/digital_outputs/con1/4/1"},
        }}}, "ports": _ports({"con1": {"4": {"1": {}}}})}
        got = _engine(state, wiring_dig).get_instrument_wiring()["stats"]
        assert got["refs_seen"] == base["refs_seen"] + 1
        assert got["refs_placed"] == base["refs_placed"] + 1

    def test_unfollowable_ref_is_seen_not_placed(self):
        """A relative or dead-end digital ref never crashes and never lies —
        it counts as a seen connection that could not be placed."""
        state = {"qubits": {"q1": {"z": {"opx_trigger_out": {
            "digital_outputs": {"trigger": {"opx_output": "#../nowhere"}},
        }}}}}
        wiring = {"wiring": {"qubits": {}}, "ports": _ports()}
        out = _engine(state, wiring).get_instrument_wiring()
        assert out["stats"]["refs_seen"] >= 1
        assert out["stats"]["refs_placed"] == out["stats"]["refs_seen"] - 1
        for cd in out["controllers"].values():
            for fd in cd["fems"].values():
                assert fd["digital_ports"] == {}

    def test_no_digital_payload_shape_is_additive(self):
        """A chip with no digital wiring: empty digital_ports on every FEM,
        max_digital_port 0 — the renderer's hasDigital gate reads exactly
        this to keep the layout byte-identical."""
        state = {"qubits": {"q1": {}}}
        wiring = {"wiring": {"qubits": {"q1": {
            "xy": {"opx_output": "#/ports/mw_outputs/con1/1/1"},
        }}}, "ports": _ports()}
        out = _engine(state, wiring).get_instrument_wiring()
        ctrl = out["controllers"]["con1"]
        assert ctrl["max_digital_port"] == 0
        assert all(fd["digital_ports"] == {} for fd in ctrl["fems"].values())


# ---------------------------------------------------------------------------
# Real chip (skip-gated)
# ---------------------------------------------------------------------------

_REAL = Path(r"D:\work\Customer_Codes\CQT\CS_installations\qualibration_graphs"
             r"\superconducting\quam_state")


@pytest.mark.skipif(not (_REAL / "state.json").exists(),
                    reason="CQT customer quam_state not present")
class TestRealChipDigital:
    def test_qdac_triggers_all_placed(self):
        state = json.loads((_REAL / "state.json").read_text(encoding="utf-8"))
        wiring = json.loads((_REAL / "wiring.json").read_text(encoding="utf-8"))
        out = _engine(state, wiring).get_instrument_wiring()
        ctrl = out["controllers"]["con1"]
        labels = sorted(
            a["label"]
            for fd in ctrl["fems"].values()
            for asg in fd["digital_ports"].values()
            for a in asg
        )
        # 11 QDAC-biased qubits, one trigger each, all found + placed
        assert len(labels) == 11
        assert "q1.trigger" in labels
        assert all(lb.endswith(".trigger") for lb in labels)
        assert ctrl["max_digital_port"] >= 1
        assert out["stats"]["refs_seen"] == out["stats"]["refs_placed"]
        # dedup: the channel AND the qt wiring both name each line — once each
        for fd in ctrl["fems"].values():
            for asg in fd["digital_ports"].values():
                els = [a["element"] for a in asg]
                assert len(els) == len(set(els))


# ---------------------------------------------------------------------------
# Route payload
# ---------------------------------------------------------------------------


@pytest.fixture
def dig_folder(tmp_path: Path) -> Path:
    state = {"qubits": {"q1": {
        "xy": {"opx_output": "#/wiring/qubits/q1/xy/opx_output", "operations": {}},
        "z": {"opx_trigger_out": {"digital_outputs": {"trigger": {
            "opx_output": "#/wiring/qubits/q1/qt/digital_output",
            "delay": 0, "shareable": True,
        }}}},
    }}, "active_qubit_names": ["q1"]}
    wiring = {
        "wiring": {"qubits": {"q1": {
            "xy": {"opx_output": "#/ports/mw_outputs/con1/1/1"},
            "qt": {"digital_output": "#/ports/digital_outputs/con1/4/1"},
        }}},
        "ports": _ports({"con1": {"4": {"1": {}}}}),
        "network": {"host": "10.0.0.1"},
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return tmp_path


class TestInstrumentRoutes:
    def test_float_panel_payload_carries_digital(self, tmp_path, dig_folder):
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True,
                         instance_path=str(tmp_path / "_app_instance"))
        client = app.test_client()
        client.post("/load", data={"folder": str(dig_folder)})
        r = client.get("/api/instrument/data")
        assert r.status_code == 200
        payload = r.get_json()
        fems = payload["instrument"]["controllers"]["con1"]["fems"]
        assert fems["4"]["digital_ports"]["1"][0]["label"] == "q1.trigger"

    def test_instrument_page_embeds_digital(self, tmp_path, dig_folder):
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True,
                         instance_path=str(tmp_path / "_app_instance"))
        client = app.test_client()
        client.post("/load", data={"folder": str(dig_folder)})
        r = client.get("/instrument")
        assert r.status_code == 200
        assert b"digital_ports" in r.data
