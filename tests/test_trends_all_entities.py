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
        from quam_state_manager.web.routes import _trend_group_families
        rows = _trend_group_families([
            {"path": "qubits.q10.a", "changes": 1},
            {"path": "qubits.q2.a", "changes": 1},
            {"path": "qubits.q1.b", "changes": 9},
            {"path": "ports.con1.offset", "changes": 4},
        ])
        assert [r["path"] for r in rows] == [
            "qubits.*.b", "ports.con1.offset", "qubits.*.a"]
        assert [r["n"] for r in rows] == [1, 1, 2]
        assert rows[2]["changes"] == 2, "a family's changes are its members' sum"
        assert rows[1]["scope"] == "", "a non-entity path stays a single row"

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
        assert "3 more were left off" in body, "and the section SAYS it trimmed"

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
        i = js.index("function _params()")
        body = js[i:i + 900]
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
