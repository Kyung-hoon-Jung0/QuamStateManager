/* v1.5.1 (SB-F2/F5): every write request a page makes names the chip FOLDER
 * the page was rendered on.
 *
 * Two tabs share one server, and the server has one chip open. A tab left on
 * chip X after another tab opened chip Y used to press Ctrl+Z, Apply, Take
 * live or a grid commit onto Y -- the fingerprint token it sent could not
 * tell two copies of one chip design apart. base.html bakes the per-folder
 * key (window.__chipKey) into every full page; this sends it as the
 * X-SM-Chip-Key header on every non-GET request -- htmx's and fetch's -- and
 * the server's write doors refuse a key that is not the open chip's, naming
 * both chips.
 *
 * A chip switch is always a full page load, so the key a page holds is the
 * chip its screen shows. Loaded before app.js; no other code needs to know.
 */
(function () {
    'use strict';
    var HEADER = 'X-SM-Chip-Key';

    function key() {
        try { return String(window.__chipKey || ''); } catch (e) { return ''; }
    }
    function writes(method) {
        var m = String(method || 'GET').toUpperCase();
        return m !== 'GET' && m !== 'HEAD' && m !== 'OPTIONS';
    }
    function sameOrigin(url) {
        try { return new URL(String(url || ''), window.location.href).origin === window.location.origin; }
        catch (e) { return false; }
    }

    // htmx (every hx-post, htmx.ajax)
    document.addEventListener('htmx:configRequest', function (evt) {
        var d = evt && evt.detail;
        var k = key();
        if (!d || !k || !writes(d.verb)) return;
        d.headers = d.headers || {};
        if (!d.headers[HEADER]) d.headers[HEADER] = k;
    });

    // fetch (the JSON doors: doStateSync, the grids, the inspectors)
    var real = window.fetch;
    if (typeof real === 'function' && !real.__smTabChip) {
        var wrapped = function (input, init) {
            try {
                var k = key();
                var isReq = (typeof Request !== 'undefined') && (input instanceof Request);
                var method = (init && init.method) || (isReq ? input.method : 'GET');
                var url = isReq ? input.url : (input && input.href) || input;
                if (k && writes(method) && sameOrigin(url)) {
                    var h = new Headers((init && init.headers) || (isReq ? input.headers : undefined));
                    if (!h.has(HEADER)) h.set(HEADER, k);
                    init = Object.assign({}, init || {}, { headers: h });
                }
            } catch (e) { /* never break a request over a header */ }
            return real.call(window, input, init);
        };
        wrapped.__smTabChip = true;
        window.fetch = wrapped;
    }

    window.TabChip = { header: HEADER, key: key };
})();
