"""Chain tabs (docs/93 F1).

The bug: `chains` was computed AFTER the chain filter, so selecting Chain A
left only A's button rendered (the other chains became unreachable without
re-navigating). The fix mirrors the /table route's long-standing order —
compute from the UNFILTERED list. And per item 5, /flux drops its chain tabs
entirely (chain slicing has no flux workflow); Qubits/Resonators keep theirs,
and /pulses' channel badges (which merely reuse the .chain-tabs CSS class)
are untouched.
"""
import json

from quam_state_manager.web.app import create_app


def _client(tmp_path):
    state = {
        "qubits": {
            "qA1": {"id": "qA1", "grid_location": "0,0", "T1": 2.4e-5,
                    "resonator": {"operations": {"readout": {"amplitude": 0.04}}},
                    "z": {"flux_point": "independent"}},
            "qA2": {"id": "qA2", "grid_location": "1,0", "T1": 1.8e-5,
                    "resonator": {"operations": {"readout": {"amplitude": 0.05}}},
                    "z": {"flux_point": "independent"}},
            "qB1": {"id": "qB1", "grid_location": "0,1", "T1": 2.0e-5,
                    "resonator": {"operations": {"readout": {"amplitude": 0.06}}},
                    "z": {"flux_point": "independent"}},
        },
        "qubit_pairs": {},
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(
        json.dumps({"wiring": {"qubits": {}}, "network": {"host": "10.0.0.1"}}),
        encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    return client


def test_qubits_chain_filter_keeps_every_chain_button(tmp_path):
    client = _client(tmp_path)
    body = client.get("/qubits?chain=A").get_data(as_text=True)
    assert "Chain A" in body and "Chain B" in body, (
        "filtering to A must not make the other chains' buttons disappear")
    # the filter itself still works: only chain-A rows in the table
    assert 'data-qubit-id="qA1"' in body and 'data-qubit-id="qB1"' not in body


def test_resonators_chain_filter_keeps_every_chain_button(tmp_path):
    client = _client(tmp_path)
    body = client.get("/resonators?chain=B").get_data(as_text=True)
    assert "Chain A" in body and "Chain B" in body
    assert 'data-qubit-id="qB1"' in body and 'data-qubit-id="qA1"' not in body


def test_flux_has_no_chain_tabs_but_qubits_and_resonators_keep_theirs(tmp_path):
    client = _client(tmp_path)
    flux = client.get("/flux").get_data(as_text=True)
    assert "chain-tabs" not in flux, "flux dropped its chain tabs (docs/93 item 5)"
    # the flux table itself is intact
    assert 'data-qubit-id="qA1"' in flux
    assert "chain-tabs" in client.get("/qubits").get_data(as_text=True)
    assert "chain-tabs" in client.get("/resonators").get_data(as_text=True)


def test_flux_chain_param_stays_harmless(tmp_path):
    # deep links keep working: the param filters rows, renders no tabs
    client = _client(tmp_path)
    body = client.get("/flux?chain=A").get_data(as_text=True)
    assert "chain-tabs" not in body
    assert 'data-qubit-id="qA1"' in body and 'data-qubit-id="qB1"' not in body


def test_component_map_mount_carries_the_active_chain(tmp_path):
    """docs/93 F3: the map mount declares the page's active chain so the
    drawing can light that chain's qubits (empty when unfiltered)."""
    client = _client(tmp_path)
    filtered = client.get("/qubits?chain=A").get_data(as_text=True)
    assert 'data-chain="A"' in filtered
    unfiltered = client.get("/qubits").get_data(as_text=True)
    assert 'data-chain=""' in unfiltered


def test_pulses_channel_badges_untouched(tmp_path):
    # _pulses.html's .chain-tabs is the pulse CHANNEL badge strip (CSS reuse,
    # not qubit chains) — pinned present so the F1 removal can't overreach.
    client = _client(tmp_path)
    body = client.get("/pulses").get_data(as_text=True)
    assert "pulse-channel-tabs" in body
