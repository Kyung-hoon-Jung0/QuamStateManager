"""From a qubit (or a pair) to the experiment runs that measured it.

The Datasets side already had everything — the grammar, the pickers, the AND
filter. What was missing was the link, and the two ways a link like this lies:

* by MEANING MORE than it says — `qubit:q1` is a substring scope, so on a 20Q
  chip it drags q10…q19 along with q1. The link ships the BARE name, which the
  client matches exactly against the run's own qubit list.
* by MEANING LESS than it says — the Datasets picker/facet/experiment ticks
  persist across swaps by design, so arriving with a preset while a stale tick
  is live ANDs them and shows zero rows.

Both halves are pinned here; the client half is pinned as source, since the
behaviour lives in dataset-virtual.js's init.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static"
TEMPLATES = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "templates"


def _text(rel: str) -> str:
    base = STATIC if rel.endswith((".js", ".css")) else TEMPLATES
    return (base / rel).read_text(encoding="utf-8")


def _client_with_runs(tmp_path):
    """A test client over a small real archive, so /datasets actually renders
    its search box (with no data folder the partial takes the no-workspace
    branch and there is nothing to assert)."""
    import json as _json
    from quam_state_manager.web.app import create_app

    root = tmp_path / "data"
    for i in range(2):
        run = root / f"2026-01-0{i + 1}" / f"#{i + 1}_04_power_rabi_120000"
        run.mkdir(parents=True, exist_ok=True)
        (run / "node.json").write_text(
            _json.dumps({"id": i + 1, "name": "power_rabi"}), encoding="utf-8")

    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    r = client.post("/workspace/add", data={"folder": str(root)})
    assert r.status_code in (200, 204, 302), r.status_code
    return client


def _actions_block(head: str) -> str:
    """The `.inspector-actions` div — where the link lives."""
    i = head.index('<div class="inspector-actions">')
    return head[i:i + 1600]


def _search_input(html: str) -> str:
    m = re.search(r"<input type=\"search\" id=\"dataset-search\".*?>", html, re.S)
    assert m, "the datasets search input"
    return m.group(0)


# ---------------------------------------------------------------------------
# The link itself
# ---------------------------------------------------------------------------

class TestTheLink:
    def test_both_inspectors_get_it_from_one_template(self):
        """Scoped to the actions block on purpose.

        The same `inspector_type in ('qubit', 'pair')` test appears elsewhere in
        this template for an unrelated reason, so a whole-file `in` assert stays
        green when the LINK's own branch is narrowed to qubits — measured, by
        mutating it and watching this pass.
        """
        actions = _actions_block(_text("_inspector_header.html"))
        assert "inspector_type in ('qubit', 'pair')" in actions
        assert 'href="/datasets?q={{ inspector_label }}"' in actions
        assert 'hx-get="/datasets?q={{ inspector_label }}"' in actions

    def test_it_ships_the_bare_name_not_the_qubit_scope(self):
        """The whole design decision, in one assert.

        `qubit:`/`pair:` are substring scopes in dataset-virtual.js; a bare
        token is an exact membership test. A link that sent the scope would
        quietly show 11 qubits' runs when the user asked for one.
        """
        head = _text("_inspector_header.html")
        assert "/datasets?q=qubit:" not in head
        assert "/datasets?q=pair:" not in head

    def test_the_href_and_the_htmx_get_agree(self):
        """A middle-click must land where a left-click lands."""
        head = _text("_inspector_header.html")
        hrefs = re.findall(r'href="(/datasets\?q=[^"]+)"', head)
        gets = re.findall(r'hx-get="(/datasets\?q=[^"]+)"', head)
        assert hrefs and hrefs == gets

    def test_the_grammar_still_treats_a_bare_token_as_exact(self):
        """A pin on the BEHAVIOUR the link depends on, not on our own markup.

        If dataset-virtual.js ever made a free token a substring match, the
        link would start over-reporting with nothing here to say so.
        """
        js = _text("dataset-virtual.js")
        assert "_rowHasQubit" in js and "_rowHasPair" in js
        assert "knownQubits.has(" in js


# ---------------------------------------------------------------------------
# The server hands the token over without interpreting it
# ---------------------------------------------------------------------------

class TestServerPreset:
    def test_the_box_is_prefilled_from_q(self):
        tag = _search_input(_text("_datasets.html"))
        assert 'value="{{ search | default(\'\') }}"' in tag
        assert 'data-preset="{{ search | default(\'\') }}"' in tag

    def test_a_real_render_round_trips_the_token(self, tmp_path):
        """Rendered against a real archive, not just templated.

        Both halves matter: with no `q` the attributes are EMPTY (a default
        that echoed, say, `date` would silently filter every plain visit), and
        with a `q` the token arrives in the box character for character.
        """
        client = _client_with_runs(tmp_path)

        plain = _search_input(client.get("/datasets").get_data(as_text=True))
        assert 'value=""' in plain and 'data-preset=""' in plain

        picked = _search_input(
            client.get("/datasets?q=q7").get_data(as_text=True))
        assert 'value="q7"' in picked and 'data-preset="q7"' in picked

    def test_the_token_is_never_reinterpreted_on_the_way_through(self, tmp_path):
        """SM hands the string over; it does not parse it. A scope typed by
        hand must survive, and so must whitespace-trimming and nothing else."""
        client = _client_with_runs(tmp_path)
        tag = _search_input(
            client.get("/datasets?q=%20tag%3Aflagged%20").get_data(as_text=True))
        assert 'value="tag:flagged"' in tag

    def test_the_date_tabs_carry_the_search(self):
        """Without this a deep-linked filter evaporates on the first date click
        with nothing said about it."""
        html = _text("_datasets.html")
        assert '/datasets?date={{ d }}{% if search %}&q={{ search | urlencode }}{% endif %}' in html
        assert '/datasets{% if search %}?q={{ search | urlencode }}{% endif %}' in html

    def test_the_route_reads_q_and_never_interprets_it(self):
        routes = (Path(__file__).resolve().parents[1] / "quam_state_manager"
                  / "web" / "routes.py").read_text(encoding="utf-8")
        assert 'search = (request.args.get("q") or "").strip()' in routes
        # handed to BOTH render sites — the no-workspace branch renders the
        # partial too, and a missing variable there is a silent empty box
        assert routes.count("search=search") == 2


# ---------------------------------------------------------------------------
# A preset arrival states the WHOLE intent
# ---------------------------------------------------------------------------

class TestPresetClearsStaleFilters:
    def test_a_preset_arrival_clears_every_persisted_filter(self):
        js = _text("dataset-virtual.js")
        block = js[js.index("data-preset"):]
        block = block[:block.index("Drop any selected qubits")]
        for name in ("qubitFilter.clear()", "pairFilter.clear()",
                     "paramFilter.clear()", "paramRangeFilter.clear()",
                     "_selectedExps"):
            assert name in block, f"{name} must be cleared on a preset arrival"

    def test_it_clears_on_every_arrival_not_only_a_changed_one(self):
        """Clicking the same link twice must clear twice.

        Keying on a CHANGED preset would let a tick made between two presses of
        the same link survive into the second arrival — the same silent lie,
        one press later.
        """
        js = _text("dataset-virtual.js")
        assert "_lastPreset" not in js

    def test_a_swap_with_no_preset_leaves_the_users_filters_alone(self):
        """The guard is inside `if (preset)`, so a date tab / Rescan / nav-back
        is byte-identical to before this change."""
        js = _text("dataset-virtual.js")
        i = js.index("var _presetEl = document.getElementById('dataset-search');")
        guard = js[i:i + 400]
        assert "if (_presetEl && (_presetEl.getAttribute('data-preset') || ''))" in guard

    def test_the_clear_runs_before_the_known_entity_prune(self):
        """Order matters: pruning first would leave the prune loops fighting a
        filter the clear is about to empty."""
        js = _text("dataset-virtual.js")
        assert js.index("data-preset") < js.index("Drop any selected qubits")
