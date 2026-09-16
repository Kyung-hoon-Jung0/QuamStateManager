"""Pulses filter (search keyword + channel badge) — respected & persisted.

Two reported issues, both fixed:

1. "qA2-qA1 검색하면 qA1도 나오고 다른것도 섞이네": the search IGNORED the active
   channel badge after a client-side switch, because the search input's hx-get channel
   was baked at template-render time. The htmx:configRequest patch now applies to the
   search input + the channel badges (not only the #pulses-rows-wrap mutation refresh),
   so a "Pair flux" badge + "qA2-qA1" is correctly scoped to the pair's pulses instead
   of falling back to "All" and mixing in the gate's CR/CZ pulses on qubit channels.

2. "apply 누르면 검색어 + 배지 초기화": the filter lived ONLY in the DOM, so any full
   re-fetch of /pulses (an apply that pulls, conflict/discard/reapply, a reload) reset
   it. It's now mirrored into the URL via replaceState, and the route renders the input
   value + active badge from ?q= / ?channel=, so it survives every re-fetch.

JS behaviour pytest can't execute → source-contract tripwires + a server render check."""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
_APP_JS = (_ROOT / "static" / "app.js").read_text(encoding="utf-8")


class TestPulsesFilterRespectedAndPersisted:
    def test_configrequest_covers_input_and_badges(self):
        # The filter patch must run for the search input + channel badges, not only the
        # rows wrapper — otherwise a client-side badge switch leaves the search's channel
        # stale (issue #1).
        assert 'el.id === "pulses-rows-wrap" ||' in _APP_JS
        assert "#pulse-channel-tabs a')" in _APP_JS
        assert '.table-filter input[name="q"]' in _APP_JS

    def test_url_sync_function_exists_and_is_called(self):
        assert "function _pulsesSyncUrl(push)" in _APP_JS
        assert "history.replaceState" in _APP_JS
        # docs/190 F37: a channel tab PUSHES now -- it is a destination, and
        # two presses used to leave one history entry with nothing for Back to
        # step into. Everything else still replaces.
        assert "history.pushState" in _APP_JS
        # pulseTabActive (badge click) syncs the URL, and since docs/190 F37
        # it asks for a PUSH. The wiring itself is executed rather than
        # grepped by escape_ladder_selfcheck.cjs, which presses the tab and
        # reads history.length -- a distance grep over these two statements
        # would expire the next time a comment lands between them
        # (docs/141 4l).
        assert 'a.classList.add("active");' in _APP_JS
        assert "_pulsesSyncUrl(true);" in _APP_JS
        # configRequest keeps the URL in sync too.
        assert "if (window._pulsesSyncUrl) window._pulsesSyncUrl();" in _APP_JS

    def test_server_renders_filter_from_url_params(self):
        # A full re-fetch / reload of /pulses?channel=xy&q=cr must restore BOTH the
        # search input value and the active badge (so the URL-persisted state survives).
        from quam_state_manager.web.app import create_app
        app = create_app()
        with app.test_request_context("/pulses?channel=xy&q=cr"):
            from flask import render_template
            html = render_template(
                "_pulses.html", total=0, per_page=50, active_channel="xy",
                active_query="cr", rows=[], current_page=1, total_pages=1)
        assert 'value="cr"' in html                       # search keyword restored
        m = re.search(r'channel=xy[^<]*?class="([^"]*)"\s*>XY</a>', html, re.S)
        assert m and "active" in m.group(1)               # XY badge restored as active
        assert 'class="active">All' not in html           # All is NOT active


class TestThePageNumberIsPartOfTheView:
    """docs/190 section 8: the URL dropped the page number, so page 4 of a
    156-pulse chip reloaded (or shared, or Back'd) as page 1 with no way to
    tell. The server always honoured ?page=; the client never wrote it."""

    def test_the_rows_partial_marks_the_current_page(self):
        # the marker the client reads lives in the shipped pagination partial
        import pathlib
        html = pathlib.Path("quam_state_manager/web/templates/_pagination.html").read_text(encoding="utf-8")
        assert 'data-current-page="{{ current_page }}"' in html

    def test_the_client_writes_page_and_per_page_into_the_url(self):
        import pathlib
        js = pathlib.Path("quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
        i = js.index("function _pulsesSyncUrl")
        body = js[i:i + 1800]
        assert 'parts.push("page=" + cur)' in body
        assert 'parts.push("per_page=" + pp.value)' in body
        assert "data-current-page" in body
        # and the sync runs after the swap that CHANGES the page
        assert 'if (t && (t.id === "pulses-rows-wrap" || t.id === "table-pane")) _pulsesSyncUrl();' in js
