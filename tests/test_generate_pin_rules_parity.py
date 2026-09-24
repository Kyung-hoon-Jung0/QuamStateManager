"""Step-5 pin rules: the wizard and the server must flag the SAME lines.

QA review of regenerate-r2-13 / regenerate-r2-24: "a pin names a slot the
chassis does not hold a FEM of that kind in" and "two lines pinned to one FEM
output" are written twice -- ``generate.js`` (``stalePinGroups`` /
``pinCollisionGroups`` / ``femKindAt``, the step-5 flags and the one-press
fixes) and ``config_generator.validate_spec`` (the server's refusal) -- and
``run_build._fem_kind_at`` holds a third spelling of "which FEM is in
(con, slot)". A drift would flag a box the server accepts, or let through a
spec the server refuses. One fixture list runs through all of them (the
search_query parity precedent); each case is a spelling the two sides could
read differently.

Needs node + jsdom for the JS side (skips without them).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core.config_generator import validate_spec

_ROOT = Path(__file__).resolve().parent.parent
_JS = _ROOT / "tests" / "generate_pin_rules_parity.cjs"
_RUN_BUILD = _ROOT / "quam_state_manager" / "generator" / "run_build.py"


def _mw(con, slot, port, **kw):
    return dict({"kind": "mw_fem", "con": con, "slot": slot, "out_port": port}, **kw)


def _lf(con, port, **kw):
    return dict({"kind": "lf_fem", "con": con, "out_port": port}, **kw)


def _ln(element, line, channel, **kw):
    return dict({"element": element, "line": line, "channel": channel}, **kw)


_RACK = {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "lf"}]}],
         "opx_plus": [], "octaves": []}


def _spec(lines, instruments=None, **extra):
    spec = {
        "network": {"host": "10.0.0.1", "cluster_name": "C"},
        "instruments": copy.deepcopy(instruments or _RACK),
        "qubits": ["q1", "q2", "q3"],
        "qubit_pairs": [["q1", "q2"], ["q2", "q3"]],
        "twpas": [], "lines": lines,
        "populate": {"qubits": {}, "pairs": {}},
    }
    spec.update(extra)
    return spec


_PROBE = [[c, s] for c in (1, 2) for s in range(1, 9)]

CASES = [
    # a module moved away: two drives left on the empty slot
    ("stale_slot", _spec([_ln("q1", "drive", _mw(1, 3, 2)), _ln("q2", "drive", _mw(1, 3, 3))])),
    # the slot holds the other kind
    ("wrong_kind", _spec([_ln("q1", "flux", _lf(1, 1, out_slot=1)),
                          _ln("q1", "drive", _mw(1, 2, 1))])),
    # lf spellings: bare slot (read as out_slot), out_slot beating slot, an
    # input half falling back to slot, an input half with its own in_slot
    ("lf_spellings", _spec([
        _ln("q1", "flux", _lf(1, 1, slot=4)),
        _ln("q2", "flux", _lf(1, 2, slot=4, out_slot=2)),
        _ln("q3", "flux", _lf(1, 3, out_slot=2, slot=5, in_port=1)),
        _ln("q1-q2", "coupler", _lf(1, 4, out_slot=2, in_port=1, in_slot=6)),
        _ln("q2-q3", "coupler", _lf(1, 5, out_slot=2, in_port=1)),
    ])),
    # a feedline is ONE line (its first member's pin); a drive on its port collides
    ("feedline_is_one_line", _spec([
        _ln("q1", "resonator", _mw(1, 1, 1, in_port=1), group="feedline1"),
        _ln("q2", "resonator", _mw(1, 1, 1, in_port=1), group="feedline1"),
        _ln("q3", "resonator", _mw(1, 1, 1, in_port=1), group="feedline1"),
        _ln("q1", "drive", _mw(1, 1, 1)),
    ])),
    # ungrouped resonators are each their own feedline
    ("solo_resonators", _spec([
        _ln("q1", "resonator", _mw(1, 1, 8, in_port=2)),
        _ln("q2", "resonator", _mw(1, 1, 8, in_port=2)),
    ])),
    # CR / ZZ share the control's xy port by design
    ("cr_shares", _spec([
        _ln("q1", "drive", _mw(1, 1, 2)),
        _ln("q1-q2", "cross_resonance", _mw(1, 1, 2)),
        _ln("q1-q2", "zz_drive", _mw(1, 1, 2)),
    ], pair_gate="cr")),
    # a partial pin (no con) is an "any" constraint, never a collision
    ("partial_pin", _spec([
        _ln("q1", "drive", {"kind": "mw_fem", "out_port": 2}),
        _ln("q2", "drive", _mw(1, 1, 2)),
        _ln("q3", "resonator", {"kind": "mw_fem", "out_port": 8, "in_port": 2}, group="feedline1"),
    ])),
    # three on one port
    ("three_on_one", _spec([_ln(q, "drive", _mw(1, 1, 4)) for q in ("q1", "q2", "q3")])),
    # two on a missing module are named stale, not colliding
    ("stale_not_collision", _spec([_ln("q1", "drive", _mw(1, 3, 2)), _ln("q2", "drive", _mw(1, 3, 2))])),
    # same port number on different FEMs / kinds is no clash
    ("different_fems", _spec([_ln("q1", "drive", _mw(1, 1, 5)), _ln("q1", "flux", _lf(1, 5, out_slot=2))])),
    # a second chassis
    ("two_chassis", _spec([
        _ln("q1", "flux", _lf(2, 1, out_slot=1)),
        _ln("q2", "drive", _mw(2, 1, 1)),
        _ln("q3", "drive", _mw(2, 3, 1)),
    ], instruments={"controllers": [
        {"con": 1, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "lf"}]},
        {"con": 2, "fems": [{"slot": 1, "fem": "lf"}, {"slot": 3, "fem": "mw"}]}],
        "opx_plus": [], "octaves": []})),
    # no OPX1000 chassis declared: nothing is stale, collisions still count
    ("no_chassis", _spec([_ln("q1", "drive", _mw(1, 3, 2)), _ln("q2", "drive", _mw(1, 3, 2))],
                         instruments={"controllers": [], "opx_plus": [{"con": 1}], "octaves": []})),
    # a TWPA pump is a line like any other
    ("twpa_pump", _spec([_ln("t1", "twpa_pump", _mw(1, 1, 6)), _ln("q2", "drive", _mw(1, 1, 6))],
                        twpas=[{"id": "t1", "qubits": ["q1", "q2"]}])),
]

_STALE = re.compile(
    r"^\d+ pinned lines? \((?P<who>.*)\) names? con(?P<con>\d+) slot (?P<slot>\d+), which "
    r"(?:holds an (?:LF|MW)-FEM, not an (?P<want1>MW|LF)-FEM|has no (?P<want2>MW|LF)-FEM)")
_COLLIDE = re.compile(
    r"^(?P<who>.*) are (?:both|all) pinned to con(?P<con>\d+) slot (?P<slot>\d+) "
    r"output (?P<port>\d+) —")


def _server_view(spec):
    stale, collide = [], []
    for err in validate_spec(spec):
        m = _STALE.match(err)
        if m:
            assert "…" not in m["who"], "fixture groups must stay <= 6 lines (no ellipsis)"
            want = (m["want1"] or m["want2"]).lower()
            stale.append([int(m["con"]), int(m["slot"]), want, sorted(m["who"].split(", "))])
            continue
        m = _COLLIDE.match(err)
        if m:
            who = m["who"].split(" and ") if " and " in m["who"] else m["who"].split(", ")
            collide.append([int(m["con"]), int(m["slot"]), int(m["port"]), sorted(who)])
    return {"stale": sorted(stale), "collide": sorted(collide)}


def _run_build():
    spec = importlib.util.spec_from_file_location("run_build_pinparity", _RUN_BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def js_view(tmp_path_factory):
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    cases = tmp_path_factory.mktemp("pinparity") / "cases.json"
    cases.write_text(json.dumps([{"spec": s, "probe": _PROBE} for _n, s in CASES]), encoding="utf-8")
    r = subprocess.run(["node", str(_JS), str(cases)], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_every_case_exercises_a_rule():
    # a fixture list that flags nothing would pass vacuously
    flagged = [n for n, s in CASES if any(_server_view(s).values())]
    assert len(flagged) >= 10, flagged


@pytest.mark.parametrize("i", range(len(CASES)), ids=[n for n, _s in CASES])
def test_wizard_and_server_flag_the_same_lines(js_view, i):
    name, spec = CASES[i]
    js = js_view[i]
    got = {"stale": sorted(js["stale"]), "collide": sorted(js["collide"])}
    assert got == _server_view(spec), name


@pytest.mark.parametrize("i", range(len(CASES)), ids=[n for n, _s in CASES])
def test_fem_kind_is_read_the_same_three_ways(js_view, i):
    name, spec = CASES[i]
    rb = _run_build()
    for con, slot in _PROBE:
        py = {"mw-fem": "mw", "lf-fem": "lf", None: None}[rb._fem_kind_at(spec, con, slot)]
        assert js_view[i]["kinds"][f"{con}/{slot}"] == py, (name, con, slot)
