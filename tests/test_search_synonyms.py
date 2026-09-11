"""docs/181 — one chip object, several names; the Live-Edit search follows.

Customer, on-site (2026-09-11):

    "제발 동일한 컨택스트의 키워드는 live edit에서 같이 검색되게 해주세요!!
     예를 들어, ro, readout 만 입력해도 resonator 도 당연히 함께 나와야하는데,
     지금은 resonator를 입력하면 readout이 검색 안되고 그 vice versa 임."

The chip is the evidence. On their 5Q chip a qubit's readout object is literally
named ``resonator``, its pulses are ``readout`` / ``readout_GEF``, and SM's own
labels read "Readout frequency" over the blurb "Resonator frequency used to read
the qubit out". One object, three spellings — and ``resonator`` and ``readout``
share no substring, so no substring search can cross between them.

Two halves, each fixing a different part:

* **the template**, which is the chip's OWN word for the column. ``Readout freq``
  addresses ``qubits.{name}.resonator.f_01``; the only place that word appeared
  was a field the haystack did not read. This half needs no dictionary at all.
* **the synonym groups**, for names that share no substring with each other.

Measured on the customer's real chip through SM's own column builders:
``readout`` 16 → 37 columns, ``resonator`` 21 → 37, and the two sets are now
*identical* — which is the report, stated as a property. ``ro`` 28 → 51,
``drive`` 3 → 65 (every ``xy`` column), ``flux`` 111 → 124 (the ``z`` columns).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import search_synonyms
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app


class TestTheDictionary:
    def test_the_reported_group_closes_both_ways(self):
        assert "resonator" in search_synonyms.extra_terms("readout")
        assert "readout" in search_synonyms.extra_terms("resonator")
        assert "ro" in search_synonyms.extra_terms("resonator")
        assert "resonator" in search_synonyms.extra_terms("ro")

    def test_membership_is_by_whole_word_never_substring(self):
        """`ro` occurs inside `control`, `crosstalk`, `cross_resonance` and
        `phase_shift_control` on a real chip. A substring test would pull every
        control column into the readout group — the search would get VAGUER,
        which is the opposite of what was asked for."""
        for noise in ("phase_shift_control", "controller_id", "crosstalk",
                      "qubit_control", "post_zero_padding_length"):
            assert search_synonyms.extra_terms(noise) == [], noise

    def test_cross_resonance_is_not_a_resonator(self):
        """The word is there, but `cross_resonance` is a two-qubit drive, not
        the readout object. It is `resonance`, not `resonator` — tokenising
        keeps them apart, where a prefix test would not."""
        assert search_synonyms.extra_terms("cross_resonance") == []

    def test_the_other_three_groups(self):
        assert "drive" in search_synonyms.extra_terms("xy")
        assert "xy" in search_synonyms.extra_terms("drive")
        assert "flux" in search_synonyms.extra_terms("z")
        assert "z" in search_synonyms.extra_terms("flux")
        assert "coupler" in search_synonyms.extra_terms("cz")

    def test_a_word_never_returns_itself(self):
        """The text already contains it; repeating it only grows the haystack."""
        assert "readout" not in search_synonyms.extra_terms("readout")

    def test_prefix_pairs_are_deliberately_absent(self):
        """`amp`/`amplitude` and `freq`/`frequency` are NOT groups: one is a
        prefix of the other, so ordinary substring matching already finds them
        and a dictionary entry would be pure noise."""
        assert search_synonyms.extra_terms("amplitude") == []
        assert search_synonyms.extra_terms("frequency") == []

    def test_unknown_text_costs_nothing(self):
        assert search_synonyms.extra_terms("") == []
        assert search_synonyms.extra_terms("T1 T2echo anharmonicity") == []
        assert search_synonyms.augment("nothing here") == ""

    def test_several_groups_at_once_keep_their_order(self):
        got = search_synonyms.extra_terms("xy", "z")
        assert got.index("drive") < got.index("flux")

    def test_every_group_member_is_a_single_lowercase_word(self):
        """The index is matched against tokens, so a multi-word or
        mixed-case entry could never be found — a silently dead dictionary
        entry."""
        for group in search_synonyms.GROUPS:
            assert len(group) >= 2
            for w in group:
                assert w == w.lower() and w.isalnum(), w


_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _chip(folder: Path):
    """A qubit shaped like the customer's: the readout object is named
    `resonator`, its pulse is named `readout`, the drive is `xy`, flux is `z`."""
    state = {
        "qubits": {
            "q1": {
                "id": "q1", "f_01": 5.0e9, "T1": 2e-5,
                "resonator": {"RF_frequency": 6.1e9, "depletion_time": 1000,
                              "operations": {"readout": {"amplitude": 0.01}}},
                "xy": {"RF_frequency": 5.0e9, "operations": {"x180": {"amplitude": 0.2}}},
                "z": {"offset": 0.0},
            }
        },
        "qubit_pairs": {},
        "active_qubit_names": ["q1"],
    }
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def cols(tmp_path):
    live = tmp_path / "chip"
    _chip(live)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    html = c.get("/bulk").data.decode()
    assert "bulk-table" in html
    with app.app_context():
        store = routes_mod._active_ctx()["store"]
        grid = routes_mod._qubit_bulk_grid(store, set(), {})
    return grid["columns"]


def _hay(c: dict) -> str:
    """Exactly how `bulk-edit.js` `_colHay` composes it."""
    return " ".join(str(c.get(k) or "") for k in
                    ("label", "key", "section", "search")).lower()


def _hits(columns, token):
    return {c["key"] for c in columns if token in _hay(c)}


class TestTheGridsHaystack:
    def test_readout_and_resonator_find_the_same_columns(self, cols):
        """The report, stated as a property rather than a count."""
        ro = _hits(cols, "readout")
        res = _hits(cols, "resonator")
        assert ro, "no readout columns at all — the fixture is not exercising this"
        assert ro == res, {"readout only": sorted(ro - res),
                           "resonator only": sorted(res - ro)}

    def test_ro_finds_them_too(self, cols):
        assert _hits(cols, "ro") >= _hits(cols, "resonator")

    def test_drive_finds_the_xy_columns(self, cols):
        """The client payload carries no template — the haystack is the only
        place the chip's own words survive to, which is the point."""
        xy = _hits(cols, "xy")
        assert xy, "the fixture has no xy columns"
        assert xy <= _hits(cols, "drive")

    def test_flux_finds_the_z_columns(self, cols):
        z = {c["key"] for c in cols if c["key"].startswith("z_")}
        assert z, "the fixture has no z columns"
        assert z <= _hits(cols, "flux")

    def test_the_chips_own_word_is_searchable_without_any_dictionary(self, cols):
        """Half the fix is not a dictionary at all: the TEMPLATE is what the
        chip calls this column, and the haystack simply never read it.

        `Depletion time` is the case with no synonym in its label, key or
        section at all — the ONLY place `resonator` appears for it is the path
        it addresses."""
        dep = [c for c in cols if c["key"] == "depletion_time"]
        assert dep, "the fixture has no depletion_time column"
        assert "resonator" in _hay(dep[0])
        for field in ("label", "key", "section"):
            assert "resonator" not in str(dep[0][field]).lower(), field

    def test_an_unrelated_column_joins_nothing(self, cols):
        """T1 is not readout, drive, flux or a coupler — its haystack must not
        have grown."""
        t1 = [c for c in cols if c["key"] == "T1"]
        assert t1
        hay = _hay(t1[0])
        for w in ("readout", "resonator", "drive", "coupler"):
            assert w not in hay, w

    def test_the_row_placeholder_never_enters_the_haystack(self, cols):
        """`{name}` is the row placeholder. Searching `name` must not match
        every column on the chip."""
        for c in cols:
            assert "{name}" not in _hay(c)
        assert len(_hits(cols, "{name}")) == 0
