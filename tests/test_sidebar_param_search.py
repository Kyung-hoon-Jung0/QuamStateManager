"""The sidebar can filter by the run's own node parameters.

Customer, 2026-09-10: "혹시 실험 노드의 parameter로도 검색가능하니? 예를들어
multiplexed 같은걸로?"

On the Datasets page the answer had been yes since the sort banner shipped --
``multiplexed=true`` is a param facet, and ``param:``/``p:`` a scope. In the
sidebar tree the very same token was literal text that matched nothing, because
``_entry_matches``'s haystack was name/date/status/qubits/pairs/run-id only.
One grammar, two capabilities, and no line on either page saying so.

What is pinned here is the whole chain, because every link of it was missing:
the scanner has to put the params on the entry at all, the listing cache has to
carry them across a restart, the parser has to route the token, and the matcher
has to answer it with the SAME rule the Datasets table uses (dataset-virtual.js
:284-303) -- key by substring, value exactly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.core import scanner
from quam_state_manager.core.scanner import (
    ExperimentEntry, extract_filter_params, node_parameters,
)

_STATIC = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static"


def _entry(**params) -> ExperimentEntry:
    """A minimal tree entry carrying *params* and nothing else searchable."""
    return ExperimentEntry(
        folder_path=Path("/x/#1_node_010101"),
        quam_state_path=Path("/x/#1_node_010101/quam_state"),
        run_id=1, experiment_name="node", timestamp="2026-09-10T01:01:01",
        status="finished", qubits=[], qubit_pairs=[], outcomes={},
        parent_ids=[], date_str="2026-09-10", is_standalone=False,
        filter_params=dict(params),
    )


def _match(entry, query: str) -> bool:
    from quam_state_manager.web import routes
    return routes._entry_matches(entry, routes._parse_tree_query(query))


# ── the entry carries the params at all ─────────────────────────────────────
class TestTheEntryKnowsItsParameters:
    def test_the_extractor_keeps_what_can_be_searched(self):
        got = extract_filter_params({
            "multiplexed": False, "reset_type": "thermal", "num_shots": 2000,
            "amp_factor": 1.5,
            # not searchable: sweep axes, absent values, nested config
            "qubits": ["q1", "q2"], "nothing": None, "cfg": {"a": 1},
            "long_note": "x" * 41, "empty": "",
            # orchestration plumbing, never a physics knob
            "simulate": True, "timeout": 120, "load_data_id": 7,
        })
        assert got == {"multiplexed": False, "reset_type": "thermal",
                       "num_shots": 2000, "amp_factor": 1.5}

    def test_a_node_json_fills_it(self, tmp_path):
        run = tmp_path / "2026-09-10" / "#1461_31c_flux_pulse_pair_scan_121727"
        (run / "quam_state").mkdir(parents=True)
        (run / "quam_state" / "state.json").write_text("{}", encoding="utf-8")
        (run / "node.json").write_text(json.dumps({
            "id": 1461, "created_at": "2026-09-10T12:17:27",
            "metadata": {"name": "31c_flux_pulse_pair_scan", "status": "finished"},
            "data": {"parameters": {"model": {
                "qubit_pairs": ["q1-2"], "multiplexed": True,
                "reset_type": "active", "num_shots": 400,
            }}},
        }), encoding="utf-8")
        e = scanner._parse_experiment_folder(run / "quam_state")
        assert e.filter_params == {"multiplexed": True, "reset_type": "active",
                                   "num_shots": 400}
        # the pair still reaches its own field -- params are an ADDITION
        assert e.qubit_pairs == ["q1-2"]

    def test_an_older_node_puts_them_one_level_up(self):
        """Some nodes have no ``model`` wrapper. Both readers resolve the same
        dict, or a token would mean different things on the two surfaces."""
        assert node_parameters({"parameters": {"model": {"a": 1}}}) == {"a": 1}
        assert node_parameters({"parameters": {"a": 1}}) == {"a": 1}
        # An EMPTY model falls back to the whole raw dict (the historic
        # dataset.py behaviour, kept verbatim); the stray "model" key is a dict
        # and so never reaches filter_params.
        assert node_parameters({"parameters": {"model": {}, "a": 1}}) == {"model": {}, "a": 1}
        assert extract_filter_params(
            node_parameters({"parameters": {"model": {}, "a": 1}})) == {"a": 1}
        assert node_parameters({}) == {}
        assert node_parameters({"parameters": "not a dict"}) == {}

    def test_the_datasets_table_uses_the_same_extractor(self):
        """One spelling. A second one is how the two search boxes drift."""
        from quam_state_manager.core.dataset import DatasetStore, RunInfo
        run = RunInfo(run_id=1, experiment_name="n", date="2026-09-10",
                      time="01:01:01", folder_path=Path("/x"))
        run.parameters = {"multiplexed": True, "qubits": ["q1"], "simulate": False}
        assert (DatasetStore._extract_filter_params(run)
                == extract_filter_params(run.parameters)
                == {"multiplexed": True})

    def test_the_listing_cache_carries_them(self):
        """docs/142's cache rebuilds entries from disk. A field it does not
        round-trip comes back empty, so a search after a restart would silently
        find nothing until the next rescan."""
        assert "filter_params" in scanner.Workspace._ENTRY_FIELDS
        # ...and a cache written before the field existed must read as a MISS,
        # not as an entry with no params.
        assert scanner.Workspace._LISTING_CACHE_V >= 2


# ── the token gets routed ───────────────────────────────────────────────────
class TestTheSidebarParsesTheParamToken:
    def test_bare_key_equals_value_is_a_param(self):
        from quam_state_manager.web import routes
        c, = routes._parse_tree_query("multiplexed=true")
        assert (c["field"], c["value"], c["negate"]) == ("param", "multiplexed=true", False)
        # …and the token is parsed ONCE here, not per entry per condition
        assert (c["key"], c["op"], c["want"]) == ("multiplexed", "=", "true")

    def test_the_scope_and_its_alias(self):
        from quam_state_manager.web import routes
        for q in ("param:multiplexed=true", "p:multiplexed=true"):
            c, = routes._parse_tree_query(q)
            assert (c["field"], c["value"], c["negate"]) \
                == ("param", "multiplexed=true", False), q
            assert (c["key"], c["op"], c["want"]) == ("multiplexed", "=", "true"), q

    def test_negation(self):
        from quam_state_manager.web import routes
        c, = routes._parse_tree_query("-multiplexed=true")
        assert (c["field"], c["value"], c["negate"]) == ("param", "multiplexed=true", True)

    def test_a_bare_word_is_still_free_text(self):
        """``multiplexed`` alone stays a name/date/status search on BOTH
        surfaces -- the Datasets page routes only ``key=value`` and ``param:``
        to the facets. Same grammar or the answer to 'why did this find
        nothing' differs by page."""
        from quam_state_manager.web import routes
        assert routes._parse_tree_query("multiplexed") == [
            {"field": None, "value": "multiplexed", "negate": False}]


# ── and answered by the Datasets page's own rule ────────────────────────────
class TestTheMatchingRuleIsTheDatasetsOne:
    def test_key_matches_by_substring(self):
        e = _entry(reset_type="thermal")
        assert _match(e, "reset=thermal")
        assert _match(e, "reset_type=thermal")

    def test_but_the_value_matches_exactly(self):
        """``active`` must not find ``active_gef`` -- the whole point of the
        exact side is that two reset schemes are two different answers."""
        assert _match(_entry(reset_type="active"), "reset=active")
        assert not _match(_entry(reset_type="active_gef"), "reset=active")
        assert _match(_entry(reset_type="active_gef"), "reset=active_gef")

    def test_a_bool_is_spelled_the_way_it_is_shown(self):
        assert _match(_entry(multiplexed=True), "multiplexed=true")
        assert not _match(_entry(multiplexed=True), "multiplexed=false")
        assert _match(_entry(multiplexed=False), "multiplexed=false")

    def test_without_an_equals_it_is_a_substring_over_both_sides(self):
        assert _match(_entry(reset_type="thermal"), "param:reset")
        assert _match(_entry(reset_type="thermal"), "param:therm")
        assert not _match(_entry(reset_type="thermal"), "param:active")

    def test_a_run_with_no_indexed_params_matches_nothing(self):
        """Silence, never a false hit: a stub entry whose node.json has not
        been parsed yet has no params, and must not be swept in."""
        assert not _match(_entry(), "multiplexed=true")
        assert not _match(_entry(), "param:multiplexed")

    def test_negation_excludes_only_the_hits(self):
        assert not _match(_entry(multiplexed=True), "-multiplexed=true")
        assert _match(_entry(multiplexed=False), "-multiplexed=true")

    def test_it_ands_and_ors_like_every_other_token(self):
        e = _entry(multiplexed=True, reset_type="thermal")
        assert _match(e, "multiplexed=true reset=thermal")
        assert not _match(e, "multiplexed=true reset=active")
        assert _match(e, "multiplexed=true | reset=active")
        assert not _match(_entry(multiplexed=False), "multiplexed=true | reset=active")

    def test_a_param_token_still_and_s_with_the_name(self):
        e = _entry(multiplexed=True)
        e.experiment_name = "09_rabi_chevron"
        assert _match(e, "rabi multiplexed=true")
        assert not _match(e, "ramsey multiplexed=true")


class TestTheTwoSearchBoxesAgree:
    """Source-level parity with the JS twin. Limited by construction: it reads
    the shipped file rather than running it, so it catches a scope that exists
    on one side only -- not a divergence in the matching body, which the
    behavioural tests above cover against the transcribed rule."""

    def test_both_know_the_param_scope_and_its_alias(self):
        from quam_state_manager.web import routes
        js = (_STATIC / "dataset-virtual.js").read_text(encoding="utf-8")
        scopes = re.search(r"KNOWN_SCOPES\s*=\s*new Set\(\[(.*?)\]\)", js, re.S)
        assert scopes and "'param'" in scopes.group(1)
        aliases = re.search(r"SCOPE_ALIASES\s*=\s*\{(.*?)\}", js, re.S)
        assert aliases and re.search(r"\bp\s*:\s*'param'", aliases.group(1))
        assert "param" in routes._SIDEBAR_KNOWN_SCOPES
        assert routes._SIDEBAR_SCOPE_ALIASES.get("p") == "param"

    def test_both_route_a_bare_key_equals_value(self):
        """One token means one thing on both boxes — including the operator.

        `>=` MUST precede `>` in the alternation on both sides, or the single
        character matches first and the value becomes "=1000"."""
        from quam_state_manager.web import routes
        js = (_STATIC / "dataset-virtual.js").read_text(encoding="utf-8")
        pat = r"^([A-Za-z][\w.\-]*)(>=|<=|>|<|=)(.+)$"
        assert "body.match(/" + pat + "/)" in js
        assert routes._SIDEBAR_PARAM_OP.pattern == pat


class TestTheSidebarSaysTheScopeExists:
    """A capability nobody is told about is one a user has to guess at, and the
    report that started this was a user guessing."""

    def test_the_help_panel_and_the_tooltip_name_it(self):
        base = (Path(__file__).resolve().parents[1] / "quam_state_manager"
                / "web" / "templates" / "base.html").read_text(encoding="utf-8")
        panel = base.split('id="sidebar-search-help"', 1)[1].split("</table>", 1)[0]
        assert "multiplexed=true" in panel
        assert "<code>param:</code>" in panel and "<code>p:</code>" in panel
        tip = base.split('id="sidebar-filter-input"', 1)[0]
        tip = tip[tip.rfind("search_title("):]
        assert "'param:'" in tip


# ── on the customer's own archive ───────────────────────────────────────────
_REAL = Path("D:/work/Customer_Codes/dataset/KH_202608_CZ")


@pytest.mark.skipif(not _REAL.is_dir(), reason="customer archive not on this machine")
def test_it_filters_the_real_tree():
    """The report's own example, measured: `multiplexed` is a real key on this
    chip and splits the archive rather than matching everything or nothing."""
    entries = []
    for day in sorted(_REAL.glob("2026-0*")):
        if not day.is_dir():
            continue
        for run in sorted(day.iterdir()):
            if (run / "node.json").is_file():
                entries.append(scanner._parse_experiment_folder(run / "quam_state"))
    assert len(entries) > 500, "expected the full archive"
    assert all(e.filter_params for e in entries), "every run carries parameters"

    def n(q):
        return sum(1 for e in entries if _match(e, q))

    yes, no = n("multiplexed=true"), n("multiplexed=false")
    assert 0 < yes < len(entries) and 0 < no < len(entries)
    assert yes + no <= len(entries)
    assert n("param:multiplexed=true") == n("p:multiplexed=true") == yes
    assert n("-multiplexed=true") == len(entries) - yes
    assert n("multiplexed") == 0, "a bare word stays free text"
