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
        assert "function _pulsesSyncUrl()" in _APP_JS
        assert "history.replaceState" in _APP_JS
        # pulseTabActive (badge click) syncs the URL.
        assert re.search(r"a\.classList\.add\(\"active\"\);\s*_pulsesSyncUrl\(\);", _APP_JS)
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
