"""v1.3.0 A9 -- the ledger's chip is the chip the user opened, named or not.

A NAMELESS chip (no ``extras.chip_name``) used to take its ledger identity
(meta ``chip_identity``) from the FIRST run ingested from its data folder
(docs/275 "P2 CHIP_UNCERTAIN on the chip's own history"). In a shared data
folder whose oldest run is ANOTHER chip's, that other chip became the seed:
the open chip's own runs were flagged CHIP_UNCERTAIN and the other chip's
runs looked like its own.

Pinned here, on generic synthetic run folders in ``tmp_path``:

* (a) shared folder, the oldest run another chip's (another controller, no
  qubit in common, the same labels on another controller, a name the open
  chip does not declare): the open chip's runs are not flagged, the other
  chip's are -- in SM and in the offline builder handed the same chip
  (``build(identity=)``, ``--chip``);
* (b) a lone nameless chip whose qubit set or controller address changed
  since keeps its own history (the docs/275 pins in test_hub_sync.py stay
  green; one more: a qubit removed since);
* the honest ambiguity: a run on another controller may be this chip before
  a move or another chip -- it is kept, flagged, shown and never named as the
  writer, never dropped and never silently assigned;
* (c) a ledger a v1.2.0 build seeded from another chip's run heals on the
  next open: the seed becomes the open chip, every run is judged again, no
  stale flag stays behind, and an in-process reader follows the new seed;
* one rule: judging a run again from the ledger gives the verdict ingestion
  gave it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.core import hub, hub_build, hub_lanes, hub_sync
from quam_state_manager.core.hub_store import CHIP_UNCERTAIN, HubStore
from tests.test_hub_sync import T0, WIRING, events, run, sm_write, sync

NET_A = {"network": {"host": "10.0.0.1", "cluster_name": "CA"}}
NET_B = {"network": {"host": "10.0.0.9", "cluster_name": "CB"}}
A_QUBITS = ("qA1", "qA2")


@pytest.fixture(autouse=True)
def _inline():
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    yield
    hub.set_inline(old)


def nameless(v: float, qubits=A_QUBITS, name: str | None = None) -> dict:
    state = {"qubits": {q: {"f": v if i == 0 else 2.0} for i, q in enumerate(qubits)}}
    if name:
        state["extras"] = {"chip_name": name}
    return state


#: the other chip of a shared folder, as each case lays it out: (state, wiring)
OTHER = {
    "another_controller": (nameless(9.0, ("qB1", "qB2", "qB3")), NET_B),
    "same_controller_no_common_qubit": (nameless(9.0, ("qB1", "qB2")), NET_A),
    "another_controller_same_labels": (nameless(9.0), NET_B),
    "declares_a_name": (nameless(9.0, ("qB1", "qB2"), name="beta"), NET_B),
    # a name the open chip does not declare, on its controller and its layout
    "declares_a_name_same_layout": (nameless(9.0, name="beta"), NET_A),
}


def shared_folder(data: Path, other: tuple[dict, dict]) -> None:
    """#1 and #4 are the other chip's (#1 the OLDEST run), #2 and #3 the open chip's."""
    state, wiring = other
    run(data, 1, state, wiring=wiring)
    run(data, 2, nameless(3.0), wiring=NET_A)
    run(data, 3, nameless(4.0), wiring=NET_A)
    later = json.loads(json.dumps(state))
    next(iter(later["qubits"].values()))["f"] = 9.5
    run(data, 4, later, wiring=wiring)


def write_live(live: Path, data: Path, state: dict | None = None, wiring: dict | None = None):
    live.mkdir(parents=True, exist_ok=True)
    state = json.loads(json.dumps(state if state is not None else nameless(5.0)))
    state.setdefault("extras", {})["data_folder"] = str(data)
    wiring = wiring if wiring is not None else NET_A
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return state, wiring


def make_app(tmp_path, *, sync=True):
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    app.config["HUB_SYNC_ON_OPEN"] = sync
    return app


def open_live(app, live: Path) -> Path:
    from quam_state_manager.web import routes
    assert app.test_client().post("/load", data={"folder": str(live)}).status_code in (200, 302)
    with app.test_request_context():
        return routes._hub_chip_dir(routes._active_ctx()["path"])


def flagged(chip: Path) -> dict[int, bool]:
    return {e["run_id"]: bool(e["flags"] & CHIP_UNCERTAIN) for e in events(chip) if e["kind"] == "run"}


def seed(chip: Path) -> dict | None:
    with HubStore(chip) as st:
        raw = st.meta("chip_identity")
    return json.loads(raw) if raw else None


def points(app) -> list[tuple]:
    body = app.test_client().get("/api/agent/field-history?path=qubits.qA1.f").get_json()["history"]
    return [(p["value"], p.get("run_id"), p["provenance"]) for p in body["points"]]


# ----------------------------------------------------------------------
# (a) a shared data folder whose oldest run is another chip's
# ----------------------------------------------------------------------

@pytest.mark.parametrize("case", sorted(OTHER))
def test_the_open_chip_is_the_seed_not_the_oldest_run(tmp_path, case):
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    shared_folder(data, OTHER[case])
    state, wiring = write_live(live, data)
    chip = open_live(make_app(tmp_path), live)
    assert flagged(chip) == {1: True, 2: False, 3: False, 4: True}, case
    assert seed(chip) == hub_build.identity_of(state, wiring), "the seed is the chip the user opened"
    # the offline builder handed the same chip judges every run the same way
    hub_build.build(data, tmp_path / "offline", identity=hub_build.identity_of(state, wiring))
    assert flagged(tmp_path / "offline") == flagged(chip)
    assert hub_build.main([str(data), "--out", str(tmp_path / "cli"), "--chip", str(live)]) == 0
    assert flagged(tmp_path / "cli") == flagged(chip) and seed(tmp_path / "cli") == seed(chip)


def test_a_nameless_chip_never_names_the_other_chips_run_as_its_writer(tmp_path):
    """The other chip's later run (#4, the same labels on another controller)
    is kept and shown, flagged, never named as the writer -- the open chip's
    own run (#3) is."""
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    shared_folder(data, OTHER["another_controller_same_labels"])
    write_live(live, data)
    app = make_app(tmp_path)
    open_live(app, live)
    got = points(app)
    assert (9.5, 4, "run_uncertain_chip") in got, got
    assert (4.0, 3, "run_uncertain_chip") not in got and any(p[:2] == (4.0, 3) for p in got), got


# ----------------------------------------------------------------------
# (b) a lone nameless chip that changed since: its own history is its own
# ----------------------------------------------------------------------

@pytest.mark.parametrize("change", ["qubit_removed_since", "address_changed_since"])
def test_a_lone_nameless_chip_keeps_its_own_history(tmp_path, change):
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    for i in (1, 2, 3):
        run(data, i, nameless(float(i), ("qA1", "qA2", "qA3")), wiring=NET_A)
    if change == "qubit_removed_since":
        write_live(live, data, nameless(5.0, A_QUBITS))
    else:     # the same controller (its cluster name) reached at another address
        write_live(live, data, nameless(5.0, ("qA1", "qA2", "qA3")),
                   {"network": {"host": "10.0.0.77", "cluster_name": "CA"}})
    chip = open_live(make_app(tmp_path), live)
    assert flagged(chip) == {1: False, 2: False, 3: False}


def test_a_run_on_another_controller_is_kept_flagged_and_never_named(tmp_path):
    """The honest ambiguity: a nameless run on another controller is this chip
    before a move or another chip -- the files cannot say which. It stays in
    the ledger and in the folder's timeline, flagged, shown, and is never
    named as the writer; nothing is dropped or silently assigned."""
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    run(data, 1, nameless(1.0), wiring=NET_B)
    run(data, 2, nameless(2.0), wiring=NET_A)
    write_live(live, data)
    app = make_app(tmp_path)
    chip = open_live(app, live)
    assert flagged(chip) == {1: True, 2: False}
    got = points(app)
    assert (1.0, 1, "run_uncertain_chip") in got and any(p[:2] == (2.0, 2) for p in got), got
    body = app.test_client().get("/api/agent/field-history?path=qubits.qA1.f").get_json()["history"]
    assert not body.get("other_chip_hidden"), "a run that declares nothing is not hidden"
    drawer = app.test_client().get("/field/history?path=qubits.qA1.f").data.decode()
    seen = re.sub(r"<[^>]+>", "", drawer)
    assert "#1 (chip uncertain)" in seen and "not named as writer" in seen
    assert 'data-note="other_chip"' not in drawer


# ----------------------------------------------------------------------
# (c) a ledger a v1.2.0 build seeded from another chip heals on the next open
# ----------------------------------------------------------------------

@pytest.mark.parametrize("stored", ["v120_seed_format", "current_seed_format"])
def test_a_ledger_seeded_from_another_chip_heals_on_the_next_open(tmp_path, stored):
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    shared_folder(data, OTHER["declares_a_name"])
    state, wiring = write_live(live, data)
    app = make_app(tmp_path, sync=False)          # the chip dir, no catch-up yet
    chip = open_live(app, live)
    # what v1.2.0 left behind: both builders seeded the first run (the other chip's)
    hub_build.build(data, chip)
    if stored == "v120_seed_format":
        with HubStore(chip) as st:
            old = json.loads(st.meta("chip_identity"))
            old.pop("network", None)
            with st.conn:
                st.set_meta("chip_identity", json.dumps(old, sort_keys=True, separators=(",", ":")))
    assert seed(chip)["name"] == "beta"
    assert flagged(chip) == {1: False, 2: True, 3: True, 4: False}, "the v1.2.0 symptom"
    app.config["HUB_SYNC_ON_OPEN"] = True
    open_live(app, live)
    assert seed(chip) == hub_build.identity_of(state, wiring)
    assert flagged(chip) == {1: True, 2: False, 3: False, 4: True}, "no stale flag stays behind"
    assert hub_sync.sync_for(chip).status()["state"] == "ready"


def test_a_reader_in_the_same_process_follows_a_new_seed(tmp_path, monkeypatch):
    """The seed changes while SM runs (here: the v1.2.0 rule on the first open,
    the open chip on the second): an answer read before is not served after."""
    from quam_state_manager.web import routes
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    shared_folder(data, OTHER["another_controller_same_labels"])
    write_live(live, data)
    app = make_app(tmp_path)
    real = routes._hub_live_identity
    monkeypatch.setattr(routes, "_hub_live_identity", lambda ctx: None)
    open_live(app, live)
    before = points(app)
    assert (4.0, 3, "run_uncertain_chip") in before, before      # seeded from #1: the v1.2.0 symptom
    monkeypatch.setattr(routes, "_hub_live_identity", real)
    open_live(app, live)
    after = points(app)
    assert (4.0, 3, "run_uncertain_chip") not in after and (9.5, 4, "run_uncertain_chip") in after, after


def test_the_lane_rule_cache_follows_the_seed(tmp_path):
    """``hub_lanes.declares_another_chip`` keeps its verdicts per ledger
    event; a verdict made against one seed is never served for another."""
    data, chip = tmp_path / "data", tmp_path / "chip"
    run(data, 1, nameless(1.0, name="beta"), wiring=NET_B)
    hub_build.build(data, chip)
    with HubStore(chip) as st:
        f = dict(st.conn.execute("SELECT * FROM events").fetchone())
        f["flags"] |= CHIP_UNCERTAIN
        as_beta = {"name": "beta", "fingerprint": None, "qubits": list(A_QUBITS)}
        as_alpha = {"name": "alpha", "fingerprint": None, "qubits": list(A_QUBITS)}
        assert hub_lanes.declares_another_chip(st.conn, "x", f, as_beta) is False
        assert hub_lanes.declares_another_chip(st.conn, "x", f, as_alpha) is True


# ----------------------------------------------------------------------
# one rule: a run judged again from the ledger is judged as ingestion judged it
# ----------------------------------------------------------------------

def test_judging_again_from_the_ledger_gives_the_ingestion_verdict(tmp_path):
    data = tmp_path / "data"
    other_labels = nameless(6.0, ("qX1", "qX2"))
    other_labels["extras"] = {"qubit_renames": [], "tags": list(range(20))}   # a list and a long array
    states = [
        (nameless(1.0), NET_A), (nameless(1.0), NET_A),                 # a zero-change run
        (nameless(2.0, ("qB1", "qB2")), NET_B), (nameless(3.0, name="beta"), NET_A),
        (nameless(4.0, ("qA1", "qA2", "qA3")), NET_A),
        ({"v": 1}, {"network": {}}),                                     # no identity at all
        (nameless(5.0), {"network": {"host": "10.0.0.1"}}), (other_labels, NET_A),
    ]
    for i, (state, wiring) in enumerate(states, 1):
        run(data, i, state, wiring=wiring)
    seeds = [hub_build.identity_of(nameless(0.0), NET_A),
             hub_build.identity_of(nameless(0.0, ("qB1", "qB2")), NET_B),
             hub_build.identity_of(nameless(0.0, name="beta"), NET_A)]
    verdicts = []
    for k, chip_seed in enumerate(seeds):
        out = tmp_path / f"ledger{k}"
        hub_build.build(data, out, identity=chip_seed)
        ingested = flagged(out)
        verdicts.append(ingested)
        with HubStore(out) as st, st.conn:
            hub_build.reseed(st, chip_seed)
        assert flagged(out) == ingested, (k, ingested)
    # the fixture exercises both answers under every seed
    assert all(any(v.values()) and not all(v.values()) for v in verdicts), verdicts
    assert verdicts[0] == {1: False, 2: False, 3: True, 4: True, 5: False, 6: True, 7: False, 8: True}


def test_judging_again_replays_from_an_sm_anchor_as_state_at_does(tmp_path, monkeypatch):
    """A late run of another chip lands between two SM writes: the second is
    anchored and its stored rows (what SM wrote) no longer chain from the run
    before it. The run after it saved the open chip's own identity -- judged
    again from the ledger it still is (its leaves come from the anchor, as
    ``state_at`` reads them, never from rows folded across the anchor)."""
    data, chip = tmp_path / "data", tmp_path / "chip"
    own_seed = hub_build.identity_of(nameless(0.0), WIRING)
    run(data, 1, nameless(1.0), t_us=T0 + 10_000_000, wiring=WIRING)
    sm_write(chip, monkeypatch, T0 + 30_000_000, nameless(1.0), nameless(5.0), base="h0", post="h1")
    e2 = sm_write(chip, monkeypatch, T0 + 50_000_000, nameless(5.0), nameless(6.0), base="h1", post="h2")
    run(data, 3, nameless(6.0, ("qA1", "qA2", "qA3")), t_us=T0 + 60_000_000, wiring=WIRING)
    sync(chip, data, identity=own_seed)
    run(data, 2, nameless(9.0, ("qB1", "qB2"), name="beta"), t_us=T0 + 40_000_000, wiring=NET_B)
    sync(chip, data, identity=own_seed)
    with HubStore(chip) as st:
        assert st.conn.execute("SELECT 1 FROM sm_anchors WHERE eid=?", (e2,)).fetchone(), "the fixture anchors"
    ingested = flagged(chip)
    assert ingested == {1: False, 2: True, 3: False}
    with HubStore(chip) as st, st.conn:
        hub_build.reseed(st, own_seed)
    assert flagged(chip) == ingested


def test_judging_again_inherits_like_ingestion_after_an_observed_state(tmp_path):
    """A run byte-identical to a state SM observed of the open folder takes
    that state's verdict at ingestion (the files were this chip's folder) --
    judged again it keeps it, even though its saved controller differs from
    the chip's today."""
    data, chip = tmp_path / "data", tmp_path / "chip"
    own_seed = hub_build.identity_of(nameless(0.0), NET_A)
    saw = tmp_path / "snap" / "20260101_120005_000000"
    saw.mkdir(parents=True)
    (saw / "state.json").write_text(json.dumps(nameless(1.0)), encoding="utf-8")
    (saw / "wiring.json").write_text(json.dumps(NET_B), encoding="utf-8")
    with HubStore(chip) as st:
        assert hub_sync.attach_observed(st, {"ts": saw.name, "t_us": T0 + 5_000_000, "trigger": "auto",
                                             "dir": str(saw)}) == "added"
    run(data, 1, nameless(1.0), t_us=T0 + 10_000_000, wiring=NET_B)
    sync(chip, data, identity=own_seed)
    assert [e["kind"] for e in events(chip)] == ["observed", "run"]
    ingested = flagged(chip)
    assert ingested == {1: False}
    with HubStore(chip) as st, st.conn:
        hub_build.reseed(st, own_seed)
    assert flagged(chip) == ingested


def test_a_seed_another_window_changed_is_checked_again_on_the_next_open(tmp_path):
    """Two SM windows can hold one ledger: a seed another window wrote is
    looked at again when THIS window opens the chip again (once per open,
    never on every slice)."""
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    shared_folder(data, OTHER["another_controller"])
    state, wiring = write_live(live, data)
    app = make_app(tmp_path)
    chip = open_live(app, live)
    with HubStore(chip) as st, st.conn:
        hub_build.reseed(st, hub_build.identity_of(*OTHER["another_controller"]))   # the other window
    assert flagged(chip) == {1: False, 2: True, 3: True, 4: False}
    open_live(app, live)
    assert seed(chip) == hub_build.identity_of(state, wiring)
    assert flagged(chip) == {1: True, 2: False, 3: False, 4: True}


# ----------------------------------------------------------------------
# the rule itself, case by case (hub_build.identity_disagrees)
# ----------------------------------------------------------------------

def ident(name=None, qubits=A_QUBITS, network=NET_A["network"], fingerprint="f-a"):
    return {"name": name, "fingerprint": fingerprint, "qubits": sorted(qubits), "network": network}


OTHER_HOST = {"host": "10.0.0.77", "cluster_name": "CA"}
RULE = [
    ("both_named_differ", ident("alpha"), ident("beta"), True),
    ("one_name_no_common_qubit", ident("alpha"), ident("alpha", ("qZ1",)), True),
    ("one_name_shared_qubit", ident("alpha"), ident("alpha", ("qA1", "qA9")), False),
    ("run_declares_a_name_the_chip_does_not", ident(), ident("beta"), True),
    ("nameless_run_of_a_chip_named_since", ident("alpha"), ident(None, ("qA1",)), False),
    ("another_cluster", ident(), ident(network=NET_B["network"]), True),
    ("same_cluster_other_host", ident(), ident(network=OTHER_HOST), False),
    ("same_controller_no_common_qubit", ident(), ident(qubits=("qB1", "qB2")), True),
    ("same_controller_qubit_added", ident(qubits=("qA1", "qA2", "qA3")), ident(), False),
    ("host_decides_without_cluster_names", ident(network={"host": "h1"}),
     ident(qubits=("qA1",), network={"host": "h1"}), False),
    ("no_controller_same_fingerprint", ident(network=None), ident(network=None), False),
    ("no_controller_other_fingerprint", ident(network=None), ident(qubits=("qA1",), network=None,
                                                                   fingerprint="f-b"), True),
    ("seed_stored_before_v130", {"name": None, "fingerprint": "f-a", "qubits": list(A_QUBITS)},
     ident(qubits=("qA1",), fingerprint="f-b"), True),
]


@pytest.mark.parametrize("chip, theirs, disagrees", [r[1:] for r in RULE], ids=[r[0] for r in RULE])
def test_the_identity_rule_case_by_case(chip, theirs, disagrees):
    assert hub_build.identity_disagrees(chip, theirs) is disagrees
