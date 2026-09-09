"""One click charts EVERY entity, and the 2Q vocabulary gets its badges.

Customer, 2026-09-09: *"Trends should carry a badge for at least the numbers
the Overview panel has — 2Q fidelity is missing right now. And the biggest
problem: if I search 'interleaved' in the search box I have to click it again
for every single qubit before the trend appears. It should plot ALL pairs / ALL
qubits by default — the user can toggle series on and off in Plotly afterwards,
which is enough. …Whatever the entity is, qubit or pair, the default is: plot
them all."*

Measured on the customer's own chips before any of this was written: the
20-qubit chip's change-point index holds 3,735 leaves in 151 families, of which
**46 families are PAIR-scoped with 30 pairs each** — so the old behaviour on
that chip was 30 clicks per family, and each of the 30 charts drew ONE line
without saying it was one of thirty. Neither of the two chips carries a 2Q
fidelity leaf at all, which is why the honest empty slot below is a pin and not
a footnote.

Every test here drives the REAL route.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

# q10 after q2, so the natural-order rule (2026-09-09) is testable rather than
# accidentally satisfied by byte order.
QUBITS = ("q1", "q2", "q10")
PAIRS = ("q1-q2", "q2-q10", "q1-q10")


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    state = {"qubits": {}, "qubit_pairs": {}, "active_qubit_names": list(QUBITS)}
    for i, q in enumerate(QUBITS):
        state["qubits"][q] = {
            "id": q, "f_01": 6.0e9 + i * 1e8, "T1": 2.0e-5 + i * 1e-6,
            "xy": {"operations": {"x180": {"amplitude": 0.1 + i * 0.01}}},
        }
    for j, p in enumerate(PAIRS):
        state["qubit_pairs"][p] = {
            "id": p,
            "coupler": {"interaction_offset": 0.01 + j * 0.001},
            "mutual_flux_bias": [0.0 + j, 0.5 + j],
            "macros": {"cz": {"phase_shift_target": 0.2 + j * 0.01}},
        }
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "9.9.9.9"}, "wiring": {"qubits": {}}}), encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path):
    _chip(tmp_path / "quam_state")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path / "quam_state")})
    return c


def _versions(client, folder: Path, n=3):
    """n snapshots that actually DIFFER on both qubit AND pair leaves."""
    sp = folder / "state.json"
    for step in range(n):
        doc = json.loads(sp.read_text(encoding="utf-8"))
        for i, q in enumerate(doc["qubits"]):
            doc["qubits"][q]["f_01"] = 6.0e9 + i * 1e8 + step * 1e6
        for j, p in enumerate(doc["qubit_pairs"]):
            doc["qubit_pairs"][p]["coupler"]["interaction_offset"] = \
                0.01 + j * 0.001 + step * 1e-4
            doc["qubit_pairs"][p]["macros"]["cz"]["phase_shift_target"] = \
                0.2 + j * 0.01 + step * 1e-3
        sp.write_text(json.dumps(doc), encoding="utf-8")
        client.post("/state/archive", data={"tag": f"v{step}"})
        time.sleep(1.05)


def _charts(body: str) -> list[dict]:
    m = re.search(r'id="topo-trends-data">(.*?)</script>', body, re.S)
    return json.loads(m.group(1)) if m else []


class TestOneClickChartsEveryEntity:
    """Mechanism 1: `_trend_series_leaf` fans out over the entity segment for
    BOTH scopes."""

    def test_a_pair_family_charts_every_pair(self, client, tmp_path):
        """THE complaint. A pair leaf used to fall into the "nothing to fan out
        over" branch and draw exactly one line."""
        _versions(client, tmp_path / "quam_state")
        body = client.get("/topology/trends?metrics="
                          "&paths=qubit_pairs.*.coupler.interaction_offset"
                          ).get_data(as_text=True)
        charts = _charts(body)
        assert len(charts) == 1, "one family asked for, one chart"
        c = charts[0]
        assert c["kind"] == "pair"
        assert [s["entity"] for s in c["series"]] == ["q1-q2", "q1-q10", "q2-q10"], \
            "every pair is a line on the SAME chart, in natural order"
        assert c["n_entities"] == 3
        assert all(len(s["points"]) >= 2 for s in c["series"])

    def test_the_title_says_pairs_not_qubits(self, client, tmp_path):
        _versions(client, tmp_path / "quam_state")
        body = client.get("/topology/trends?metrics="
                          "&paths=qubit_pairs.*.coupler.interaction_offset"
                          ).get_data(as_text=True)
        assert "· 3 pairs" in body
        assert "· 3 qubits" not in body

    def test_a_CONCRETE_pair_path_fans_out_too(self, client, tmp_path):
        """What the old typeahead handed the user — one pair's own path — must
        chart every pair, exactly as one qubit's path always charted every
        qubit."""
        _versions(client, tmp_path / "quam_state")
        body = client.get("/topology/trends?metrics="
                          "&path=qubit_pairs.q1-q2.coupler.interaction_offset"
                          ).get_data(as_text=True)
        charts = _charts(body)
        assert len(charts) == 1
        assert {s["entity"] for s in charts[0]["series"]} == set(PAIRS)

    def test_a_qubit_family_still_charts_every_qubit(self, client, tmp_path):
        """The behaviour that already worked must not have been traded away."""
        _versions(client, tmp_path / "quam_state")
        body = client.get(
            "/topology/trends?metrics=&path=qubits.q1.f_01").get_data(as_text=True)
        charts = _charts(body)
        assert len(charts) == 1
        assert [s["entity"] for s in charts[0]["series"]] == ["q1", "q2", "q10"]
        assert charts[0]["kind"] == "qubit"

    def test_a_non_entity_path_is_still_one_line(self, client, tmp_path):
        """A port or a top-level key has nothing to fan out over — and inventing
        an entity axis for it would be a lie."""
        from quam_state_manager.web.routes import _trend_family_of
        assert _trend_family_of("network.host") is None
        assert _trend_family_of("qubits") is None
        assert _trend_family_of("qubits.q1") is None
        assert _trend_family_of("qubits.q1.f_01") == ("qubits", "f_01")
        assert _trend_family_of("qubit_pairs.*.coupler.x") == \
            ("qubit_pairs", "coupler.x")

    def test_the_fan_out_stays_ONE_connection(self):
        """Per-entity calls were measured at 458 ms for 20 qubits — connect and
        close, not query. The pair fan-out must not reintroduce that."""
        from quam_state_manager.web import routes as R
        src = Path(R.__file__).read_text(encoding="utf-8")
        i = src.index("def _trend_series_leaf")
        body = src[i:i + 2200]
        fanout = body[:body.index("# Not entity-scoped")]
        assert "leaf_field_series_many" in fanout
        assert "hm.leaf_field_series(" not in fanout


class TestTheTypeaheadOffersFamilies:
    """Mechanism 2: one row per (scope, tail), never 25 rows of the same
    parameter differing only by the entity id."""

    def test_one_row_per_family_with_the_entity_count(self, client, tmp_path):
        _versions(client, tmp_path / "quam_state")
        rows = client.get(
            "/topology/trends/paths?q=interaction_offset").get_json()
        assert len(rows) == 1, f"one family, not one row per pair: {rows}"
        r = rows[0]
        assert r["path"] == "qubit_pairs.*.coupler.interaction_offset"
        assert r["label"] == "coupler.interaction_offset"
        assert r["scope"] == "qubit_pairs"
        assert r["n"] == 3, "how many pairs this one click will chart"
        assert r["changes"] >= 3

    def test_a_qubit_family_folds_the_same_way(self, client, tmp_path):
        _versions(client, tmp_path / "quam_state")
        rows = client.get("/topology/trends/paths?q=f_01").get_json()
        fams = [r for r in rows if r["label"] == "f_01"]
        assert len(fams) == 1
        assert fams[0]["path"] == "qubits.*.f_01"
        assert fams[0]["scope"] == "qubits"
        assert fams[0]["n"] == 3

    def test_the_row_the_user_clicks_charts_them_all(self, client, tmp_path):
        """End to end: what the typeahead returns, handed straight back to the
        chart route, must chart every entity."""
        _versions(client, tmp_path / "quam_state")
        row = client.get(
            "/topology/trends/paths?q=interaction_offset").get_json()[0]
        body = client.get("/topology/trends?metrics=&paths="
                          + row["path"]).get_data(as_text=True)
        assert _charts(body)[0]["n_entities"] == row["n"]

    def test_families_rank_by_total_change_points_then_naturally(self):
        """The grouping is SQL now (`leaf_index.path_families`), so this drives
        a real index rather than a pure fold — same contract: one row per
        (scope, tail), ranked by total change points then naturally, a
        non-entity path staying a single row."""
        import sqlite3

        from quam_state_manager.core import leaf_index

        conn = sqlite3.connect(":memory:")
        leaf_index.ensure_schema(conn)
        for i, (p, n) in enumerate([("qubits.q10.a", 1), ("qubits.q2.a", 1),
                                    ("qubits.q1.b", 9),
                                    ("ports.con1.offset", 4)]):
            conn.execute("INSERT INTO leaf_paths (id, path) VALUES (?, ?)", (i, p))
            for s in range(n):
                conn.execute("INSERT INTO leaf_cp (path_id, snap_id, value)"
                             " VALUES (?, ?, ?)", (i, s, float(s)))
        rows = leaf_index.path_families(conn)
        assert [r["path"] for r in rows] == [
            "qubits.*.b", "ports.con1.offset", "qubits.*.a"]
        assert [r["n"] for r in rows] == [1, 1, 2]
        assert rows[2]["changes"] == 2, "a family's changes are its members' sum"
        assert rows[1]["scope"] == "", "a non-entity path stays a single row"
        assert rows[1]["n"] == 1
        conn.close()

    def test_an_empty_query_still_returns_nothing(self, client):
        assert client.get("/topology/trends/paths?q=").get_json() == []


class TestSeveralFamiliesAtOnce:
    """Mechanism 3: ?paths= is comma-separated, ?path= keeps working, and the
    cap SAYS when it trims."""

    def test_paths_charts_several_families(self, client, tmp_path):
        _versions(client, tmp_path / "quam_state")
        body = client.get(
            "/topology/trends?metrics=&paths="
            "qubit_pairs.*.coupler.interaction_offset,"
            "qubit_pairs.*.macros.cz.phase_shift_target").get_data(as_text=True)
        charts = _charts(body)
        assert [c["metric"] for c in charts] == [
            "coupler.interaction_offset", "macros.cz.phase_shift_target"]
        assert all(c["n_entities"] == 3 for c in charts)

    def test_a_badge_and_a_typed_family_coexist(self, client, tmp_path):
        """?paths= carries the badges, ?path= carries the box — one press can
        never evict the other."""
        _versions(client, tmp_path / "quam_state")
        body = client.get(
            "/topology/trends?metrics="
            "&paths=qubit_pairs.*.coupler.interaction_offset"
            "&path=qubits.q1.f_01").get_data(as_text=True)
        charts = _charts(body)
        assert {c["metric"] for c in charts} == {
            "coupler.interaction_offset", "f_01"}
        assert 'value="qubits.q1.f_01"' in body, "the box still holds what was typed"

    def test_path_alone_still_works(self, client, tmp_path):
        _versions(client, tmp_path / "quam_state")
        charts = _charts(client.get(
            "/topology/trends?metrics=&path=qubits.q1.f_01"
        ).get_data(as_text=True))
        assert [c["metric"] for c in charts] == ["f_01"]

    def test_the_families_cap_trims_AND_says_so(self, client):
        """A cap that quietly drops families is indistinguishable from a badge
        that does not work."""
        from quam_state_manager.web.routes import _TRENDS_MAX_FAMILIES
        # The bound itself is the armor, so it is pinned rather than merely
        # read back: a cap of 500 satisfies "it trims at the cap" and still
        # lets a 46-family pair chip render hundreds of series.
        assert 2 <= _TRENDS_MAX_FAMILIES <= 16, _TRENDS_MAX_FAMILIES
        asked = [f"qubits.*.made_up_{i}" for i in range(_TRENDS_MAX_FAMILIES + 3)]
        body = client.get("/topology/trends?metrics=&paths="
                          + ",".join(asked)).get_data(as_text=True)
        charts = _charts(body)
        assert len(charts) == _TRENDS_MAX_FAMILIES, \
            "the cap trims, so a wide chip cannot render hundreds of series"
        # It NAMES the first one dropped, not just a count — "3 more were left
        # off" leaves the user guessing which three.
        dropped = f"made_up_{_TRENDS_MAX_FAMILIES}"
        assert dropped in body, f"the note names what it dropped: {body[-2000:]}"
        assert "and 2 more were left off" in body, \
            "and how many others went with it"

    def test_no_trim_note_when_nothing_was_trimmed(self, client, tmp_path):
        _versions(client, tmp_path / "quam_state")
        body = client.get("/topology/trends?metrics="
                          "&paths=qubit_pairs.*.coupler.interaction_offset"
                          ).get_data(as_text=True)
        assert "left off" not in body and "not drawn" not in body


class TestTheTwoQBadges:
    """Mechanism 4: the Overview's 2Q vocabulary, built from what the chip has,
    with the gate-fidelity chip offered even when the chip has none."""

    def test_the_chip_offers_its_own_pair_families_as_badges(self, client, tmp_path):
        _versions(client, tmp_path / "quam_state")
        body = client.get("/topology/trends").get_data(as_text=True)
        assert 'data-trend-path="qubit_pairs.*.coupler.interaction_offset"' in body
        assert 'data-trend-path="qubit_pairs.*.macros.cz.phase_shift_target"' in body
        assert "2Q / pairs" in body

    def test_a_2Q_gate_fidelity_badge_exists_even_with_no_fidelity_recorded(
            self, client, tmp_path):
        """Neither customer chip records one. The vocabulary is honest rather
        than data-dependent: the badge is there, and pressing it renders the
        same 'Nothing recorded' slot a curated metric with no data renders."""
        _versions(client, tmp_path / "quam_state")
        body = client.get("/topology/trends").get_data(as_text=True)
        assert 'data-trend-path="qubit_pairs.*.gate_fidelity"' in body
        assert ">2Q gate fidelity<" in body

    def test_pressing_it_renders_the_honest_empty_slot(self, client, tmp_path):
        _versions(client, tmp_path / "quam_state")
        body = client.get("/topology/trends?metrics="
                          "&paths=qubit_pairs.*.gate_fidelity").get_data(as_text=True)
        charts = _charts(body)
        assert len(charts) == 1 and charts[0]["series"] == []
        assert "Nothing recorded for" in body
        assert "qubit_pairs.*.gate_fidelity" in body, \
            "the slot names what was asked for"

    def test_a_recorded_fidelity_family_lights_the_badge_by_itself(self, tmp_path):
        """On a chip that DOES record a 2Q fidelity, the same badge points at
        the chip's own family instead of the empty template."""
        folder = _chip(tmp_path / "quam_state")
        doc = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        for p in doc["qubit_pairs"]:
            doc["qubit_pairs"][p]["macros"]["cz"]["fidelity"] = \
                {"InterleavedRB": {"average_gate_fidelity": 0.99}}
        (folder / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(folder)})
        for step in range(2):
            d = json.loads((folder / "state.json").read_text(encoding="utf-8"))
            for p in d["qubit_pairs"]:
                d["qubit_pairs"][p]["macros"]["cz"]["fidelity"][
                    "InterleavedRB"]["average_gate_fidelity"] = 0.99 + step * 1e-3
            (folder / "state.json").write_text(json.dumps(d), encoding="utf-8")
            c.post("/state/archive", data={"tag": f"f{step}"})
            time.sleep(1.05)
        body = c.get("/topology/trends").get_data(as_text=True)
        assert 'data-trend-path="qubit_pairs.*.macros.cz.fidelity.' \
               'InterleavedRB.average_gate_fidelity"' in body
        assert 'data-trend-path="qubit_pairs.*.gate_fidelity"' not in body, \
            "the always-offered chip points at the REAL family once one exists"

    def test_a_measured_2Q_number_outranks_a_knob(self, tmp_path):
        """Ranked by change points alone, a chip with several CZ variants filled
        the whole badge row with phase-shift knobs and pushed its own RB/XEB
        numbers off the end — the opposite of "a badge for at least the numbers
        the Overview panel has". Driven through the REAL route."""
        folder = _chip(tmp_path / "quam_state")
        doc = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        for p in doc["qubit_pairs"]:
            cz = doc["qubit_pairs"][p]["macros"]["cz"]
            cz["fidelity"] = {"Bell_State": {"Fidelity": 0.97}}
            cz["xeb"] = {"value": 0.95}          # measured, and it barely moves
            cz["phase_shift_control"] = 0.1      # a knob, and it moves every run
        (folder / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(folder)})
        for step in range(3):
            d = json.loads((folder / "state.json").read_text(encoding="utf-8"))
            for p in d["qubit_pairs"]:
                cz = d["qubit_pairs"][p]["macros"]["cz"]
                cz["phase_shift_control"] = 0.1 + step * 1e-3
                cz["phase_shift_target"] = 0.2 + step * 1e-3
            (folder / "state.json").write_text(json.dumps(d), encoding="utf-8")
            c.post("/state/archive", data={"tag": f"k{step}"})
            time.sleep(1.05)
        body = c.get("/topology/trends").get_data(as_text=True)
        order = re.findall(r'data-trend-path="qubit_pairs\.\*\.([^"]+)"', body)
        assert any("xeb" in t for t in order), order
        assert any("phase_shift" in t for t in order), order
        xeb = next(k for k, t in enumerate(order) if "xeb" in t)
        knob = next(k for k, t in enumerate(order) if "phase_shift" in t)
        assert xeb < knob,             f"a measured 2Q number comes before a knob that moved more: {order}"
        assert order[0].endswith("Bell_State.Fidelity"),             f"and the 2Q fidelity chip leads: {order}"

    def test_the_2Q_vocabulary_is_segment_matched_not_substring_matched(self):
        """"rb" as a substring would drag in half the chip."""
        from quam_state_manager.web.routes import _is_2q_vocabulary
        for tail in ("fidelity.value", "gate_fidelity.averaged",
                     "macros.cz.fidelity.StandardRB.average_gate_fidelity",
                     "macros.cz.fidelity.Bell_State.Fidelity",
                     "macros.cz.xeb.fidelity", "macros.cz.phase_shift_target",
                     "coupler.interaction_offset", "mutual_flux_bias.0",
                     "macros.cz.fidelity.InterleavedRB_alpha"):
            assert _is_2q_vocabulary(tail), tail
        for tail in ("detuning", "coupler.opx_output.delay",
                     "macros.cz.flux_pulse_qubit.amplitude", "arbitrary.value"):
            assert not _is_2q_vocabulary(tail), tail

    def test_the_labels_are_the_overview_s_own(self):
        """A customer already reported one Trends/Overview label mismatch, so
        these strings must BE the Overview tiles' strings, not near-misses."""
        from quam_state_manager.core import chip_health
        from quam_state_manager.web.routes import _trend_pair_label
        js = Path("quam_state_manager/web/static/chip-status.js").read_text(
            encoding="utf-8")
        # the RB spellings: the Overview tile titles, verbatim
        for tail, label in (
                ("macros.cz.fidelity.StandardRB.average_gate_fidelity",
                 "2Q Clifford fid. (SRB)"),
                ("macros.cz.fidelity.InterleavedRB.average_gate_fidelity",
                 "2Q gate fid. (IRB)")):
            assert _trend_pair_label(tail) == label
            assert label in js, f"{label!r} must be the Overview's own text"
        assert "2Q gate fidelity" in js
        # everything else: chip_health.METRIC_META, the same map the hero map
        # and the threshold editor read
        assert _trend_pair_label("detuning") == \
            chip_health.METRIC_META["detuning"]["label"]
        # a LIST leaf is one family per element and they are different numbers,
        # so the index survives — two identically labelled badges side by side
        # would be worse than a slightly longer label
        base = chip_health.METRIC_META["mutual_flux_bias"]["label"]
        assert _trend_pair_label("mutual_flux_bias.0") == base + " [0]"
        assert _trend_pair_label("mutual_flux_bias.1") == base + " [1]"
        # a family nobody has named keeps its tail, minus the container word —
        # `macros.` is where a CZ gate lives, not what the number is, and it
        # was seven characters repeated across every badge in the row
        assert _trend_pair_label("macros.cz_bipolar.phase_shift_target") ==             "cz_bipolar.phase_shift_target"
        assert _trend_pair_label("coupler.interaction_offset") ==             "coupler.interaction_offset"

    def test_a_chip_with_no_pairs_offers_no_2Q_row(self, tmp_path):
        """No pairs, no 2Q anything — an empty badge row would be noise, not
        honesty."""
        folder = tmp_path / "solo"
        folder.mkdir()
        (folder / "state.json").write_text(json.dumps(
            {"qubits": {"q1": {"id": "q1", "f_01": 6e9}}, "qubit_pairs": {}}),
            encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(
            {"network": {"host": "9.9.9.9"}, "wiring": {}}), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i3"))
        c = app.test_client()
        c.post("/load", data={"folder": str(folder)})
        body = c.get("/topology/trends").get_data(as_text=True)
        assert "2Q / pairs" not in body


# ── review round 1 (2026-09-09) — five confirmed defects, five pins ────────
#
# Each of these drives the REAL route, and each was RED before its fix. The
# fixture had to grow first in three of the five: the shipped fixture has 3
# pairs and a handful of families, so it could not reach the >25-entity
# truncation, the >10-family badge cap, or the two-knob crowding at all.


def _badges(body: str) -> list[tuple[str, str]]:
    """(path, label) for every 2Q badge, in the order they render."""
    return [(p, lbl.strip()) for p, lbl in
            re.findall(r'data-trend-path="([^"]+)"[^>]*>([^<]*)', body)]


def _titles(body: str) -> list[str]:
    return [t.strip() for t in
            re.findall(r'class="topo-trend-title">\s*([^<\n]+)', body)]


def _chip_with(tmp_path, name: str, pairs, leaves, steps=3, qubits=QUBITS):
    """A chip whose pairs carry `leaves` (dot-tail -> starting value), archived
    `steps` times with every leaf moved. Returns (client, folder)."""
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    state = {"qubits": {q: {"id": q, "f_01": 6.0e9} for q in qubits},
             "qubit_pairs": {}, "active_qubit_names": list(qubits)}

    def _put(d, tail, val):
        cur = d
        segs = tail.split(".")
        for s in segs[:-1]:
            cur = cur.setdefault(s, {})
        cur[segs[-1]] = val

    for j, p in enumerate(pairs):
        node = {"id": p}
        for tail, v0 in leaves.items():
            _put(node, tail, v0 + j * 1e-3)
        state["qubit_pairs"][p] = node
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "9.9.9.9"}, "wiring": {"qubits": {}}}),
        encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / ("_i_" + name)))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    for step in range(steps):
        doc = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        for j, p in enumerate(doc["qubit_pairs"]):
            for tail, v0 in leaves.items():
                _put(doc["qubit_pairs"][p], tail, v0 + j * 1e-3 + (step + 1) * 1e-5)
        (folder / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        c.post("/state/archive", data={"tag": f"s{step}"})
        time.sleep(1.05)
    return c, folder


class TestReviewRound1:
    """The five confirmed defects of the first review round."""

    # ① The always-offered chip named a quantity the chip never measured.

    def test_an_SRB_badge_says_SRB_not_gate_fidelity(self, tmp_path):
        """A StandardRB average is a fidelity per CLIFFORD — over five 2Q gates
        on a real chip (docs/138). The badge used to hardcode "2Q gate
        fidelity" for whatever family it resolved to, so the badge and the
        chart it opens disagreed: the exact Trends/Overview label mismatch a
        customer already reported once."""
        c, _ = _chip_with(
            tmp_path, "srb", PAIRS,
            {"macros.cz.fidelity.StandardRB.average_gate_fidelity": 0.97})
        body = c.get("/topology/trends").get_data(as_text=True)
        srb = [(p, lbl) for p, lbl in _badges(body) if "StandardRB" in p]
        assert len(srb) == 1, _badges(body)
        assert srb[0][1] == "2Q Clifford fid. (SRB)", \
            f"the badge names what the number IS: {srb[0]}"
        # ...and the chart it opens agrees, character for character.
        chart_body = c.get("/topology/trends?metrics=&paths="
                           + srb[0][0]).get_data(as_text=True)
        assert srb[0][1] in _titles(chart_body), _titles(chart_body)

    def test_the_gate_fidelity_QUESTION_survives_an_SRB_chip(self, tmp_path):
        """A Clifford number is not a gate number and neither is derived from
        the other (`query._RB_LEVEL`'s doctrine), so a chip that recorded only
        SRB has still not measured a 2Q gate fidelity — and the row says so
        with the same honest empty template a chip with nothing gets."""
        c, _ = _chip_with(
            tmp_path, "srbq", PAIRS,
            {"macros.cz.fidelity.StandardRB.average_gate_fidelity": 0.97})
        body = c.get("/topology/trends").get_data(as_text=True)
        assert ('qubit_pairs.*.gate_fidelity', '2Q gate fidelity') in _badges(body)

    def test_an_IRB_chip_needs_no_empty_template(self, tmp_path):
        """InterleavedRB IS the gate fidelity, so offering an empty "2Q gate
        fidelity" beside it would ask a question the chip already answered."""
        c, _ = _chip_with(
            tmp_path, "irb", PAIRS,
            {"macros.cz.fidelity.InterleavedRB.average_gate_fidelity": 0.99})
        paths = [p for p, _ in _badges(
            c.get("/topology/trends").get_data(as_text=True))]
        assert "qubit_pairs.*.gate_fidelity" not in paths, paths

    # ② The badge row's cap was silent, and it dropped a named family.

    def test_the_badge_row_SAYS_what_it_could_not_fit(self, tmp_path):
        """13 vocabulary families against a row that cannot hold them.
        docs/94's rule — "a tripped cap now renders a visible note" — was
        applied to the chart grid in the same commit and not to this row."""
        from quam_state_manager.web.routes import _TREND_2Q_MAX_CHIPS
        leaves = {}
        for v in ("bipolar", "flattop", "flattop_erf", "snz", "unipolar"):
            leaves[f"macros.cz_{v}.phase_shift_control"] = 0.1
            leaves[f"macros.cz_{v}.phase_shift_target"] = 0.2
        leaves["mutual_flux_bias.0"] = 0.3
        leaves["mutual_flux_bias.1"] = 0.4
        leaves["coupler.interaction_offset"] = 0.5
        c, _ = _chip_with(tmp_path, "wide", PAIRS, leaves)
        body = c.get("/topology/trends").get_data(as_text=True)
        badges = _badges(body)
        assert len(badges) == _TREND_2Q_MAX_CHIPS, badges
        # 13 families + the always-offered fidelity template = 14 chips.
        assert f"+{14 - _TREND_2Q_MAX_CHIPS} more pair parameters" in body, \
            f"the row says what it left off: {body[body.find('2Q / pairs'):][:1200]}"

    def test_the_cap_is_shared_out_one_KNOB_at_a_time(self, tmp_path):
        """Five CZ variants carry ten near-identical `phase_shift_*` families
        with IDENTICAL change counts, so a flat cap spent every slot on two
        knobs and `mutual_flux_bias.*` — a family the ask named — never
        appeared. The paths are never merged (five variants are five different
        numbers); only the cap is shared out."""
        leaves = {}
        for v in ("bipolar", "flattop", "flattop_erf", "snz", "unipolar"):
            leaves[f"macros.cz_{v}.phase_shift_control"] = 0.1
            leaves[f"macros.cz_{v}.phase_shift_target"] = 0.2
        leaves["mutual_flux_bias.0"] = 0.3
        leaves["mutual_flux_bias.1"] = 0.4
        c, _ = _chip_with(tmp_path, "knobs", PAIRS, leaves)
        paths = [p for p, _ in _badges(
            c.get("/topology/trends").get_data(as_text=True))]
        assert "qubit_pairs.*.mutual_flux_bias.0" in paths, paths
        assert "qubit_pairs.*.mutual_flux_bias.1" in paths, paths
        # and the ten variants did not all get in, which is the crowding
        assert sum(1 for p in paths if "phase_shift" in p) < 10, paths

    # ③ The widened path scan — mechanism 2's headline claim — was unpinned.

    def test_a_family_wider_than_the_old_cap_is_counted_WHOLE(self, tmp_path):
        """The typeahead used to pull 25 CONCRETE leaves. Grouping them into
        one family row means the pull has to reach every member, or a 30-pair
        family reports 25 and the badge under-counts what one press charts.
        The shipped fixture has 3 pairs, so nothing could reach this."""
        qubits = tuple(f"q{i}" for i in range(1, 32))
        pairs = tuple(f"q{i}-q{i + 1}" for i in range(1, 31))
        assert len(pairs) == 30
        c, _ = _chip_with(tmp_path, "wide30", pairs,
                          {"macros.cz.phase_shift_target": 0.2,
                           "coupler.interaction_offset": 0.5},
                          steps=2, qubits=qubits)
        rows = c.get("/topology/trends/paths?q=phase_shift_target").get_json()
        fam = [r for r in rows if r["label"] == "macros.cz.phase_shift_target"]
        assert len(fam) == 1, rows
        assert fam[0]["n"] == 30, f"every pair counted, not the first 25: {fam}"
        # the badge row reads the same scan, so it under-counts identically
        badges = _badges(c.get("/topology/trends").get_data(as_text=True))
        assert any("phase_shift_target" in p for p, _ in badges), badges
        body = c.get("/topology/trends").get_data(as_text=True)
        assert " · 30<" in body, "the badge says how many pairs one press charts"
        # and one press really does chart all thirty
        charts = _charts(c.get("/topology/trends?metrics=&paths="
                               + fam[0]["path"]).get_data(as_text=True))
        assert charts[0]["n_entities"] == 30

    # ④ The families cap discarded the TYPED family in preference to badges.

    def test_the_box_wins_the_cap_over_the_badges(self, client, tmp_path):
        """`?paths=` (badges) was read before `?path=` (the box), so a full row
        of badges silently discarded the family the user had just typed and
        pressed Enter on — while the box still displayed it and the note told
        them to deselect something. The typed query is the most recent
        deliberate act on the page."""
        from quam_state_manager.web.routes import _TRENDS_MAX_FAMILIES
        _versions(client, tmp_path / "quam_state")
        badges = [f"qubit_pairs.*.made_up_{i}"
                  for i in range(_TRENDS_MAX_FAMILIES)]
        typed = "qubit_pairs.*.coupler.interaction_offset"
        body = client.get("/topology/trends?metrics=&paths=" + ",".join(badges)
                          + "&path=" + typed).get_data(as_text=True)
        charts = _charts(body)
        typed_chart = [c for c in charts
                       if c["metric"] == "coupler.interaction_offset"]
        assert typed_chart, [c["metric"] for c in charts]
        assert typed_chart[0]["n_entities"] == 3, "and it charted every pair"
        # a BADGE is what went, and the note names it
        assert f"made_up_{_TRENDS_MAX_FAMILIES - 1}" in body, body[-1500:]


def _hot_cold_chip(tmp_path, name: str, hot: dict, cold: dict, steps=4,
                   pairs=PAIRS):
    """A chip whose `hot` pair leaves move on EVERY archive and whose `cold`
    ones move only on the first — so the families rank by change points in a
    known order.

    `_chip_with` moves every leaf every step, which ties every family's change
    count and lets natural_key decide; three of round 2's findings only appear
    when a NON-gate fidelity family outranks the gate one, so the tie has to be
    breakable.
    """
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)

    def _put(d, tail, val):
        cur = d
        segs = tail.split(".")
        for s in segs[:-1]:
            cur = cur.setdefault(s, {})
        cur[segs[-1]] = val

    state = {"qubits": {q: {"id": q, "f_01": 6.0e9} for q in QUBITS},
             "qubit_pairs": {}, "active_qubit_names": list(QUBITS)}
    for j, p in enumerate(pairs):
        node = {"id": p}
        for tail, v0 in list(hot.items()) + list(cold.items()):
            _put(node, tail, v0 + j * 1e-3)
        state["qubit_pairs"][p] = node
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "9.9.9.9"}, "wiring": {"qubits": {}}}),
        encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / ("_i_" + name)))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    for step in range(steps):
        doc = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        for j, p in enumerate(doc["qubit_pairs"]):
            for tail, v0 in hot.items():
                _put(doc["qubit_pairs"][p], tail, v0 + j * 1e-3 + (step + 1) * 1e-5)
            if step == 0:
                for tail, v0 in cold.items():
                    _put(doc["qubit_pairs"][p], tail, v0 + j * 1e-3 + 1e-5)
        (folder / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        c.post("/state/archive", data={"tag": f"s{step}"})
        time.sleep(1.05)
    return c


SRB_TAIL = "macros.cz.fidelity.StandardRB.average_gate_fidelity"
IRB_TAIL = "macros.cz.fidelity.InterleavedRB.average_gate_fidelity"


class TestReviewRound2:
    """The three confirmed defects of the second review round — all reproduced
    through the real route before a line was changed."""

    # ① "Nothing recorded" was claimed beside the recording.

    def test_a_chip_that_measured_a_2Q_gate_fidelity_offers_no_empty_template(
            self, tmp_path):
        """A lab that measures a 2Q gate fidelity at all normally runs BOTH
        StandardRB and InterleavedRB. Ranked by change points, SRB won `best`,
        SRB is Clifford-level, so the empty "2Q gate fidelity" template was
        appended — while the chip's REAL IRB gate fidelity rendered two badges
        later in the same row. Pressing the empty one said "Nothing recorded for
        qubit_pairs.*.gate_fidelity yet" about a quantity this chip measured.
        """
        c = _hot_cold_chip(tmp_path, "mixedrb", {SRB_TAIL: 0.97}, {IRB_TAIL: 0.99})
        badges = _badges(c.get("/topology/trends").get_data(as_text=True))
        paths = [p for p, _ in badges]
        assert any("InterleavedRB" in p for p in paths), badges
        assert any("StandardRB" in p for p in paths), badges
        assert "qubit_pairs.*.gate_fidelity" not in paths, \
            f"the question is answered — do not ask it again: {badges}"

    def test_the_gate_level_family_LEADS_the_row_even_when_SRB_moved_more(
            self, tmp_path):
        """`best` is the badge the row opens with. What the Overview reports as
        "2Q fidelity" is the GATE number, so a chip that has one leads with it —
        change counts rank the rest, they do not decide which question the row
        answers first."""
        c = _hot_cold_chip(tmp_path, "leadrb", {SRB_TAIL: 0.97}, {IRB_TAIL: 0.99})
        badges = _badges(c.get("/topology/trends").get_data(as_text=True))
        assert "InterleavedRB" in badges[0][0], badges
        assert badges[0][1] == "2Q gate fid. (IRB)", badges

    def test_an_SRB_ONLY_chip_still_gets_the_question(self, tmp_path):
        """The fix must not delete the empty template — a Clifford number is not
        a gate number and neither is derived from the other (`_RB_LEVEL`)."""
        c, _ = _chip_with(tmp_path, "srbonly", PAIRS, {SRB_TAIL: 0.97})
        badges = _badges(c.get("/topology/trends").get_data(as_text=True))
        assert ("qubit_pairs.*.gate_fidelity", "2Q gate fidelity") in badges, badges

    # ② Two badges, one path, and the second one was a dead control.

    def test_no_two_badges_share_a_data_trend_path(self, tmp_path):
        """`_TREND_2Q_FIDELITY_TEMPLATE` IS `qubit_pairs.*.gate_fidelity`, so a
        chip carrying a bare `gate_fidelity` pair leaf plus a higher-change
        non-gate fidelity family emitted the template AND that family's own
        identical path. `togglePath` resolves its button with
        `[data-trend-path="…"]`, so pressing the second flipped the FIRST one's
        state and the pressed badge visibly did nothing."""
        c = _hot_cold_chip(tmp_path, "dupe", {SRB_TAIL: 0.97},
                           {"gate_fidelity": 0.985})
        badges = _badges(c.get("/topology/trends").get_data(as_text=True))
        paths = [p for p, _ in badges]
        assert "qubit_pairs.*.gate_fidelity" in paths, badges
        assert len(paths) == len(set(paths)), f"one path, one badge: {badges}"

    # ③ The last badges in the row could never be turned on.

    def test_every_badge_the_row_offers_can_actually_be_charted(self, tmp_path):
        """`_TREND_2Q_MAX_CHIPS` was 10 against a chart cap of 8, and the route
        hands `_trend_pair_chips` the ALREADY-TRIMMED selection — so pressing
        every badge brought the last two back `aria-pressed="false"`, pressing
        again reproduced the same trim forever, and the note told the user to
        deselect something while naming a badge that already rendered as
        deselected."""
        from quam_state_manager.web.routes import _TRENDS_MAX_FAMILIES
        leaves = {}
        for v in ("bipolar", "flattop", "flattop_erf", "snz", "unipolar"):
            leaves[f"macros.cz_{v}.phase_shift_control"] = 0.1
            leaves[f"macros.cz_{v}.phase_shift_target"] = 0.2
        leaves["mutual_flux_bias.0"] = 0.3
        leaves["mutual_flux_bias.1"] = 0.4
        leaves["coupler.interaction_offset"] = 0.5
        c, _ = _chip_with(tmp_path, "allpress", PAIRS, leaves)
        offered = [p for p, _ in
                   _badges(c.get("/topology/trends").get_data(as_text=True))]
        # one slot is left for the search box, which wins the cap by design
        assert 0 < len(offered) < _TRENDS_MAX_FAMILIES, offered
        body = c.get("/topology/trends?metrics=&paths="
                     + ",".join(offered)).get_data(as_text=True)
        pressed = re.findall(
            r'data-trend-path="([^"]+)"[^>]*?aria-pressed="(\w+)"', body)
        assert pressed and len(pressed) == len(offered), pressed
        off = [p for p, a in pressed if a != "true"]
        assert not off, f"pressed and came back un-pressed: {off}"
        assert "Charting the first" not in body, \
            "a full row of badges must not trip the families cap on its own"


# ── review round 3 (2026-09-09) — seven confirmed findings ────────────────
#
# Measured through the real route before a line moved:
#   _is_2q_measurement("macros.cz_bipolar.fidelity.StandardRB_load_id") -> True
#   _is_2q_measurement("macros.cz.fidelity.StandardRB.alpha")           -> True
#   _is_2q_measurement("…StandardRB.error_per_clifford")                -> True
# A *_load_id is a RUN IDENTIFIER (real values 129 / 457 / 529 / 666), an alpha
# is the RB exponential's decay base, an error_per_* is an error. None is a
# fidelity, and the badge row labelled all three "2Q gate fidelity".
#
# `core/query.py` had already fought and won exactly this fight:
# `_extract_pair_gate_fidelities` skips `*_load_id` ("they rendered as e.g.
# 529.0000") and `_rb_level` maps `*_alpha` to `decay` so an alpha is never a
# percentage under a fidelity heading (docs/138). The Trends row had a SECOND
# spelling of the same vocabulary, written with string prefixes.

SRB_ROOT = "macros.cz.fidelity.StandardRB"


def _rb_leaves(gate="cz"):
    """One CZ variant's whole StandardRB block — the fidelity, the decay base,
    two errors and the run id: the shape a real 2Q RB chip has."""
    root = f"macros.{gate}.fidelity.StandardRB"
    return {
        f"{root}.average_gate_fidelity": 0.97,
        f"{root}.alpha": 0.938,
        f"{root}.error_per_clifford": 0.0234,
        f"{root}.error_per_2q_layer": 0.0121,
        f"macros.{gate}.fidelity.StandardRB_load_id": 529.0,
    }


def _rb_chip(tmp_path, name, extra=None, steps=3, gates=("cz",)):
    leaves = {}
    for g in gates:
        leaves.update(_rb_leaves(g))
    leaves.update(extra or {})
    return _chip_with(tmp_path, name, PAIRS, leaves, steps=steps)


class TestReviewRound3:
    """Seven confirmed findings. Each drives the REAL route or the REAL JS."""

    # ① A run identifier is not a fidelity — and not a measurement at all.

    def test_a_load_id_is_not_a_2Q_measurement(self):
        from quam_state_manager.web.routes import (_is_2q_measurement,
                                                   _is_2q_vocabulary,
                                                   _is_fidelity_tail)
        for tail in ("macros.cz_bipolar.fidelity.StandardRB_load_id",
                     "macros.cz.fidelity.InterleavedRB_load_id",
                     "fidelity.Bell_State_load_id",
                     # ...and NESTED under a name the vocabulary knows, which
                     # is the spelling only the explicit load_id gate rejects:
                     # every other rule sees `StandardRB` / `xeb` and says yes.
                     "macros.cz.fidelity.StandardRB.rb_load_id",
                     "macros.cz.xeb.xeb_load_id"):
            assert not _is_fidelity_tail(tail), tail
            assert not _is_2q_measurement(tail), tail
            assert not _is_2q_vocabulary(tail), tail

    def test_an_alpha_and_an_error_are_measured_but_are_NOT_fidelities(self):
        """Both are real RB outputs — they belong on the row. Neither is a
        fidelity, so neither may wear a fidelity label or answer the row's
        gate-fidelity question."""
        from quam_state_manager.web.routes import (_is_2q_measurement,
                                                   _is_fidelity_tail,
                                                   _trend_is_gate_fidelity)
        for tail in (f"{SRB_ROOT}.alpha", f"{SRB_ROOT}.error_per_clifford",
                     f"{SRB_ROOT}.error_per_2q_layer",
                     "macros.cz.fidelity.InterleavedRB_alpha"):
            assert _is_2q_measurement(tail), tail
            assert not _is_fidelity_tail(tail), tail
            assert not _trend_is_gate_fidelity(tail), tail

    def test_the_vocabulary_comes_from_query_s_own_maps(self):
        """ONE vocabulary in the codebase. `query.fidelity_field_kind` is what
        `_extract_pair_gate_fidelities` skips run ids with, and it is what the
        badge row asks."""
        from quam_state_manager.core.query import fidelity_field_kind
        assert fidelity_field_kind("StandardRB_load_id") == "load_id"
        assert fidelity_field_kind("alpha") == "decay"
        assert fidelity_field_kind("InterleavedRB_alpha") == "decay"
        assert fidelity_field_kind("error_per_clifford") == "error"
        assert fidelity_field_kind("average_gate_fidelity") == "fidelity"
        assert fidelity_field_kind("Fidelity") == "fidelity"
        assert fidelity_field_kind("value") == "fidelity"
        assert fidelity_field_kind("not_a_field") is None
        # and query.py itself routes its own skip through it
        src = Path("quam_state_manager/core/query.py").read_text(encoding="utf-8")
        assert 'fidelity_field_kind(metric_name) == "load_id"' in src

    def test_no_badge_charts_a_run_id_on_a_real_RB_chip(self, tmp_path):
        """THE defect, through the route. The row is built from the chip's own
        families, so on the chip this feature was built for a run id got a
        badge — under the heading '2Q gate fidelity'."""
        c, _ = _rb_chip(tmp_path, "loadid")
        badges = _badges(c.get("/topology/trends").get_data(as_text=True))
        bad = [b for b in badges if "load_id" in b[0]]
        assert not bad, f"a run identifier was offered as a 2Q number: {bad}"

    # ② Whole SEGMENTS, not substrings — and the pin can now fail.

    def test_the_2Q_vocabulary_really_is_segment_matched(self):
        """The old rules were `"standard" in s` / `"bell" in s` / `"clifford"
        in s` / `"interleaved" in s`, while the docstring and the pin's own NAME
        claimed segments. The pin passed anyway: every whole-segment example it
        fed also satisfies a substring test, so it never tested its own claim.
        These negatives are the ones only a segment test rejects."""
        from quam_state_manager.web.routes import _is_2q_vocabulary
        for tail in ("standard_deviation", "coupler.bell_curve_width",
                     "macros.cz.interleaved_debug_counter",
                     "readout.clifford_gate_calibration_offset",
                     "macros.cz.alpha", "rb_settle_time"):
            assert not _is_2q_vocabulary(tail), \
                f"{tail!r} matched a SUBSTRING of the 2Q vocabulary"
        # bare `alpha` is the RB decay base only under an RB parent
        assert _is_2q_vocabulary(f"{SRB_ROOT}.alpha")

    # ③ Six of seven badges carried the same label on a real 21-qubit chip.

    def test_no_two_badges_share_a_LABEL(self, tmp_path):
        """`_trend_pair_label` stopped at the RB-level segment and ignored every
        segment below it, so StandardRB's fidelity, alpha, error_per_clifford
        and error_per_2q_layer all rendered as '2Q Clifford fid. (SRB)'. A row
        of controls you cannot tell apart is broken whatever each one charts."""
        c, _ = _rb_chip(tmp_path, "labels")
        badges = _badges(c.get("/topology/trends").get_data(as_text=True))
        labels = [lbl for _, lbl in badges]
        assert len(labels) == len(set(labels)), f"duplicate labels: {badges}"
        assert len(labels) >= 4, badges

    def test_two_CZ_VARIANTS_of_the_same_number_still_read_apart(self, tmp_path):
        """The cause is fixed, but a real chip carries the SAME RB block under
        five CZ variants — tails differing only in the variant, so their labels
        legitimately collide. The guarantee has to hold there, and it has to
        break the tie with the thing that differs."""
        c, _ = _rb_chip(tmp_path, "twocz", gates=("cz_bipolar", "cz_unipolar"))
        badges = _badges(c.get("/topology/trends").get_data(as_text=True))
        labels = [lbl for _, lbl in badges]
        assert len(labels) == len(set(labels)), f"duplicate labels: {badges}"
        srb = [lbl for p, lbl in badges
               if p.endswith("StandardRB.average_gate_fidelity")]
        assert len(srb) == 2, badges
        assert any("cz_bipolar" in lbl for lbl in srb), srb
        assert any("cz_unipolar" in lbl for lbl in srb), srb

    def test_a_decay_and_an_error_say_what_they_are(self, tmp_path):
        from quam_state_manager.web.routes import _trend_pair_label
        assert _trend_pair_label(f"{SRB_ROOT}.average_gate_fidelity") == \
            "2Q Clifford fid. (SRB)"
        assert _trend_pair_label(f"{SRB_ROOT}.alpha") == "RB decay α (SRB)"
        assert _trend_pair_label(f"{SRB_ROOT}.error_per_clifford") == \
            "RB error (SRB) · error_per_clifford"
        assert _trend_pair_label(f"{SRB_ROOT}.error_per_2q_layer") == \
            "RB error (SRB) · error_per_2q_layer"
        assert _trend_pair_label("macros.cz.fidelity.InterleavedRB_alpha") == \
            "RB decay α (IRB)"
        # and through the route, the chart title agrees with the badge
        c, _ = _rb_chip(tmp_path, "labelroute")
        badges = _badges(c.get("/topology/trends").get_data(as_text=True))
        alpha = next(p for p, _ in badges if p.endswith(".alpha"))
        body = c.get("/topology/trends?metrics=&paths=" + alpha).get_data(
            as_text=True)
        assert "RB decay α (SRB)" in _titles(body), _titles(body)

    # ④ The "· N pairs" count was folded from a TRUNCATED row list.

    def test_the_entity_count_is_exact_past_any_scan_cap(self, tmp_path):
        """A real chip's leaf index holds 9,793 paths of which 5,224 are
        pair-scoped, against a 4,000-row pull — so the badge's count, the only
        claim it makes about how many entities one press charts, was wrong on
        the chip this feature was built for. This fixture crosses that bound:
        50 pairs x 92 leaves = 4,600 pair paths."""
        pairs = tuple(f"p{i}" for i in range(1, 51))
        leaves = {f"blob.m{i:02d}": 0.1 + i for i in range(89)}
        leaves["mutual_flux_bias.0"] = 0.3
        leaves["macros.cz.phase_shift_target"] = 0.2
        leaves["macros.cz.fidelity.InterleavedRB.average_gate_fidelity"] = 0.99
        assert len(pairs) * len(leaves) > 4000, len(pairs) * len(leaves)
        c, _ = _chip_with(tmp_path, "big", pairs, leaves, steps=2,
                          qubits=("q1", "q2"))
        rows = c.get("/topology/trends/paths?q=mutual_flux_bias").get_json()
        fam = [r for r in rows if r["label"] == "mutual_flux_bias.0"]
        assert len(fam) == 1, rows
        assert fam[0]["n"] == 50, f"every pair counted, not a capped slice: {fam}"
        body = c.get("/topology/trends").get_data(as_text=True)
        badges = _badges(body)
        # the badge row reads the same grouping, and says the same number
        for tail in ("mutual_flux_bias.0", "macros.cz.phase_shift_target",
                     "macros.cz.fidelity.InterleavedRB.average_gate_fidelity"):
            hit = [b for b in badges if b[0] == "qubit_pairs.*." + tail]
            assert hit, (tail, badges)
        assert body.count("· 50<") >= 3, \
            "each badge says how many pairs one press charts"
        # ...and one press really does chart all fifty
        charts = _charts(c.get(
            "/topology/trends?metrics=&paths=qubit_pairs.*.mutual_flux_bias.0"
        ).get_data(as_text=True))
        assert charts[0]["n_entities"] == 50, charts[0]["n_entities"]

    # ⑤ The knobs the ask named never reached a chip that has RB data.

    def test_the_knobs_get_reserved_slots(self, tmp_path):
        """`measured` was emitted first and unconditionally, and on an RB chip
        there were always enough of them to fill every slot — so
        `coupler.*_offset`, `mutual_flux_bias.*` and `phase_shift_*`, the three
        the ask named, reached the row on no such chip."""
        from quam_state_manager.web.routes import (_TREND_2Q_KNOB_SLOTS,
                                                   _TREND_2Q_MAX_CHIPS,
                                                   _is_2q_measurement)
        c, _ = _rb_chip(tmp_path, "reserve", extra={
            "macros.cz.fidelity.Bell_State.Fidelity": 0.96,
            "mutual_flux_bias.0": 0.3, "mutual_flux_bias.1": 0.4,
            "coupler.interaction_offset": 0.5,
            "macros.cz.phase_shift_control": 0.1,
            "macros.cz.phase_shift_target": 0.2,
        })
        badges = _badges(c.get("/topology/trends").get_data(as_text=True))
        paths = [p for p, _ in badges]
        assert len(paths) == _TREND_2Q_MAX_CHIPS, badges
        knobs = [p for p in paths
                 if not _is_2q_measurement(p.split(".*.", 1)[1])
                 and not p.endswith(".gate_fidelity")]
        assert len(knobs) >= _TREND_2Q_KNOB_SLOTS, \
            f"the knobs got no slots: {badges}"
        # and the measurements did not lose the row either
        meas = [p for p in paths if "StandardRB" in p or "Bell_State" in p]
        assert meas, badges

    def test_an_unused_knob_slot_goes_back_to_the_measurements(self, tmp_path):
        """A chip with many real 2Q numbers and ONE knob must still fill the
        row — a reservation that leaves empty slots would be a second defect."""
        from quam_state_manager.web.routes import _TREND_2Q_MAX_CHIPS
        c, _ = _rb_chip(tmp_path, "backfill", extra={
            "macros.cz.fidelity.Bell_State.Fidelity": 0.96,
            "macros.cz.xeb.value": 0.95,
            "macros.cz.fidelity.InterleavedRB.average_gate_fidelity": 0.99,
            "mutual_flux_bias.0": 0.3,
        })
        paths = [p for p, _ in
                 _badges(c.get("/topology/trends").get_data(as_text=True))]
        assert len(paths) == _TREND_2Q_MAX_CHIPS, paths
        assert "qubit_pairs.*.mutual_flux_bias.0" in paths, paths

    # ⑦ One point per entity is not a trend.

    def test_an_all_single_point_family_renders_the_honest_slot(self, tmp_path):
        """Measured through this route on a real 5-qubit chip:
        `qubit_pairs.*.mutual_flux_bias.0` returns 4 series of exactly 1 point,
        all four at the same snapshot and all at 0.0 — and Plotly auto-ranges x
        to a ~2 ms window. An axis that implies a measurement over time is worse
        than no axis."""
        c = _hot_cold_chip(tmp_path, "onepoint",
                           {"macros.cz.phase_shift_target": 0.2},
                           {"mutual_flux_bias.0": 0.3})
        p = "qubit_pairs.*.mutual_flux_bias.0"
        body = c.get("/topology/trends?metrics=&paths=" + p).get_data(as_text=True)
        charts = _charts(body)
        assert len(charts) == 1, charts
        assert charts[0]["series"] == [], \
            "no series ships, so no axis is drawn"
        assert charts[0]["n_entities"] == len(PAIRS), charts[0]
        assert "no trend yet" in body, body[body.find("topo-trend-box"):][:900]
        assert "One value recorded for" in body
        assert "Nothing recorded for" not in body, \
            "the value EXISTS — this is not 'nothing recorded'"
        # the badge is not hidden: the family is real
        assert p in [b for b, _ in _badges(body)], _badges(body)

    def test_a_family_that_MOVED_still_draws_its_chart(self, tmp_path):
        """The honest slot must fire only when every series is a single point."""
        c = _hot_cold_chip(tmp_path, "moved",
                           {"macros.cz.phase_shift_target": 0.2},
                           {"mutual_flux_bias.0": 0.3})
        body = c.get("/topology/trends?metrics=&paths="
                     "qubit_pairs.*.macros.cz.phase_shift_target").get_data(
                         as_text=True)
        charts = _charts(body)
        assert charts[0]["series"], charts[0]
        assert "no trend yet" not in body

    def test_ONE_moving_pair_is_enough_to_draw_the_chart(self, tmp_path):
        """The MIXED case, which is where `any` and `all` part company: three
        pairs recorded, one of them moved. There IS a trend to draw, so the
        chart is drawn — suppressing it would hide the only pair that moved."""
        folder = tmp_path / "mixed"
        folder.mkdir(parents=True, exist_ok=True)
        state = {"qubits": {q: {"id": q, "f_01": 6.0e9} for q in QUBITS},
                 "qubit_pairs": {p: {"id": p, "detuning": 0.1 + j * 1e-3}
                                 for j, p in enumerate(PAIRS)},
                 "active_qubit_names": list(QUBITS)}
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(
            {"network": {"host": "9.9.9.9"}, "wiring": {"qubits": {}}}),
            encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i_mixed"))
        c = app.test_client()
        c.post("/load", data={"folder": str(folder)})
        for step in range(3):
            d = json.loads((folder / "state.json").read_text(encoding="utf-8"))
            # ONLY the first pair moves
            d["qubit_pairs"][PAIRS[0]]["detuning"] = 0.1 + (step + 1) * 1e-4
            (folder / "state.json").write_text(json.dumps(d), encoding="utf-8")
            c.post("/state/archive", data={"tag": f"m{step}"})
            time.sleep(1.05)
        body = c.get("/topology/trends?metrics=&paths="
                     "qubit_pairs.*.detuning").get_data(as_text=True)
        charts = _charts(body)
        pts = sorted(len(s["points"]) for s in charts[0]["series"])
        assert pts[0] == 1 and pts[-1] > 1, pts
        assert charts[0]["series"], "one moving pair is still a trend"
        assert "no trend yet" not in body


class TestNothingOldBroke:
    def test_the_curated_chips_keep_their_data_values(self, client):
        """localStorage/selection continuity: `data-trend-metric` values must
        not change."""
        from quam_state_manager.core.history import DEFAULT_TRACKED_PROPERTIES
        body = client.get("/topology/trends").get_data(as_text=True)
        for m in DEFAULT_TRACKED_PROPERTIES:
            assert f'data-trend-metric="{m}"' in body

    def test_a_curated_metric_with_no_data_still_says_so(self, client, tmp_path):
        _versions(client, tmp_path / "quam_state")
        body = client.get("/topology/trends?metrics=T2echo").get_data(as_text=True)
        assert "Nothing recorded for this metric yet" in body

    def test_no_chip_open_never_500s(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i4"))
        r = app.test_client().get("/topology/trends")
        assert r.status_code == 200
        assert "No chip is open" in r.get_data(as_text=True)

    def test_the_client_sends_the_three_selections_separately(self):
        js = Path("quam_state_manager/web/static/chip-status.js").read_text(
            encoding="utf-8")
        # Slice the WHOLE function, never a character budget: a 900-char window
        # expired the moment `_params` grew a press-order sort, and a distance
        # grep that stops matching reads as a regression it is not (docs/155
        # §10a's lesson, applied before it could bite twice).
        i = js.index("function _params()")
        body = js[i:js.index("\n    function ", i + 1)]
        assert "return q;" in body, "the slice really is the whole function"
        assert "'&paths=' + encodeURIComponent(paths.join(','))" in body
        assert "'&path=' + encodeURIComponent(pathEl.value.trim())" in body
        assert "data-trend-path" in body
        assert "togglePath: togglePath" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_trends_badges_selfcheck():
    """The section's CLIENT half against the real chip-status.js under jsdom."""
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True,
                       capture_output=True, timeout=30)
    except Exception:  # noqa: BLE001
        pytest.skip("jsdom not installed")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([node, str(root / "tests" / "trends_badges_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=180, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 20, r.stdout
