"""A landing can clear only a notice issued for that chip."""
import json
import subprocess
from pathlib import Path
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app
from tests.test_v151_r6_apply_failed_notice import _write_chip


def test_server_stamps_chip_on_trays_and_failure_answers(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    tokens = []
    for name in ("first", "second"):
        live = tmp_path / name
        _write_chip(live)
        client.post("/load", data={"folder": str(live)})
        with app.test_request_context(headers={"Accept": "application/json"}):
            ctx = routes._active_ctx()
            token = routes._ctx_chip_key(ctx)
            tokens.append(token)
            tray = routes._tray_html()
            assert f'data-live-landed-chip="{token}"' in tray
            response = routes._live_write_failure_response(ctx, "Apply failed", "live_write_failed")
            assert response.get_json().get("live_landed_chip") == token
            assert response.headers.get("X-SM-Live-Landed-Chip") == token
        with app.test_request_context():
            response = routes._live_write_failure_response(routes._active_ctx(), "Apply failed", "live_write_failed")
            assert f'data-live-write-failed-chip="{token}"' in response.get_data(as_text=True)
            response = routes._restore_failure("Apply failed", "live_write_failed")
            assert f'data-live-write-failed-chip="{token}"' in response.get_data(as_text=True)
            assert response.headers.get("X-SM-Live-Landed-Chip") == token
        assert f'data-live-landed-chip="{token}"' in client.get("/qubits").get_data(as_text=True)
    assert tokens[0] != tokens[1], "copies of the same design still have separate landings"


def test_notice_sweep_compares_chip_and_stamp():
    js = Path(__file__).resolve().parents[1] / "quam_state_manager/web/static/app.js"
    probe = r"""
const assert = require('assert'), fs = require('fs'), {JSDOM} = require('jsdom');
const w = new JSDOM('<body><div id="pending-tray" data-live-landed="10" data-live-landed-chip="first"></div></body>', {runScripts:'outside-only'}).window;
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('window.LiveWriteNotice =');
w.eval(src.slice(start, src.indexOf('/* v1.3.0: a tray notice', start)));
const d = w.document, tray = d.getElementById('pending-tray');
function note(at, chip) {
    const el = d.createElement('div'); el.className = 'toast'; d.body.appendChild(el);
    w.LiveWriteNotice.mark(el, at, chip); return el;
}
const first = note(10, 'first'), fallback = note();
assert.strictEqual(first.getAttribute('data-live-write-failed-chip'), 'first');
assert.strictEqual(fallback.getAttribute('data-live-write-failed-chip'), 'first');
tray.setAttribute('data-live-landed-chip', 'second'); tray.setAttribute('data-live-landed', '20');
w.LiveWriteNotice.sweep();
assert(first.isConnected && fallback.isConnected, 'another chip must not clear either notice');
const second = note(19, 'second'); w.LiveWriteNotice.sweep();
assert(!second.isConnected && first.isConnected);
tray.setAttribute('data-live-landed-chip', 'first'); tray.setAttribute('data-live-landed', '10');
w.LiveWriteNotice.sweep(); assert(first.isConnected, 'same stamp is not a later landing');
tray.setAttribute('data-live-landed', '11'); w.LiveWriteNotice.sweep();
assert(!first.isConnected && !fallback.isConnected, 'later landing on the same chip clears both');
w.close();
"""
    result = subprocess.run(["node", "-e", probe, str(js)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
