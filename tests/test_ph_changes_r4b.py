"""R4b hist (UD-1, UD-2): the Changes typeahead offers only what the prefix
filter will then match, and the ledger's starting state is one summary line."""
from __future__ import annotations

import random
import re

from quam_state_manager.core.leaf_index import PathRank
from tests.test_param_history_changes import _get, _groups, _snap, _state, env  # noqa: F401


class TestPrefixTypeahead:
    def test_rank_prefix_equals_the_page_filter_rule(self):
        rng = random.Random(7)
        words = ["qubits", "qA1", "q1", "q10", "T1", "xy", "Qubits", "ports", "a"]
        paths = sorted({".".join(rng.choice(words) for _ in range(rng.randint(1, 4)))
                        for _ in range(300)})
        rank = PathRank([(p, rng.randint(0, 9)) for p in paths])
        for q in ["q", "qubits", "qubits.q1", "QUBITS.QA1", "T1", "a", "xy.T1", "zz", ""]:
            got = [r["path"] for r in rank.search_prefix(q, 1000)]
            want = {p for p in paths if q and p.lower().startswith(q.lower())}
            assert set(got) == want and len(got) == len(want), q
            # rank order kept: the order search() ranks the same rows in
            ranked = [r["path"] for r in rank.search(q, 1000)] if q else []
            assert got == [p for p in ranked if p in want], q
        assert len(rank.search_prefix("q", 3)) == 3

    def test_a_middle_of_path_word_is_not_offered(self, env):  # noqa: F811
        _snap(env, _state(t1=1e-5))
        _snap(env, _state(t1=2e-5))
        j = env["client"].get("/param-history/param-search?q=T1").get_json()
        assert j["results"] == []
        j = env["client"].get("/param-history/param-search?q=qubits.qA1.T").get_json()
        assert [h["path"] for h in j["results"]] == ["qubits.qA1.T1"]

    def test_the_box_says_it_matches_the_start(self, env):  # noqa: F811
        _snap(env, _state())
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        inp = re.search(r'<input[^>]*name="prefix"[^>]*>', html).group(0)
        assert "path starts with" in inp


class TestGenesisSummary:
    def test_the_first_event_is_one_folded_line(self, env):  # noqa: F811
        _snap(env, _state())
        _snap(env, _state(t1=2e-5))
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        groups = _groups(html)
        assert len(groups) == 2
        tag = '<details class="ph-change-group ui-disclosure" data-genesis>'
        assert html.count(tag) == 1          # folded: no `open`
        gen = html.split(tag)[1]
        assert re.search(r"First record: \d+ parameters", gen)
        # the real change is a plain group, not folded
        assert '<div class="ph-change-group">' in html

    def test_a_later_add_is_not_the_genesis(self, env):  # noqa: F811
        _snap(env, _state())
        _snap(env, _state(extra={"T2ramsey": 3e-6}))
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        assert html.count("data-genesis") == 1
        newest = _groups(html)[0]
        assert "T2ramsey" in newest and "First record" not in newest

    def test_the_count_is_real_changes_only(self, env):  # noqa: F811
        _snap(env, _state())
        _snap(env, _state(t1=2e-5))
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        stat = re.sub(r"\s+", " ", html.split('ph-changes-stat">')[1].split("</span>")[0])
        n_first = int(re.search(r"First record: (\d+)", html).group(1))
        assert stat.strip().startswith("1 recorded change over 2 events"), stat
        assert f"first record of {n_first} parameters" in stat, stat
