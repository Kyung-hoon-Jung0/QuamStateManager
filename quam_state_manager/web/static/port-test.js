/* FB-12 port test page: plan preview, script download, run / stop on a target.
 * One run per SM process; the page polls its status (that poll is also the
 * heartbeat: the server stops a run whose page stopped asking). Leaving the
 * page stops the run explicitly as well. */
(function () {
    'use strict';
    var root = document.getElementById('port-test');
    var form = document.getElementById('pt-form');
    if (!root || !form) return;
    var $ = function (sel) { return root.querySelector(sel); };
    var runId = null, pollTimer = null;

    function val(name) {
        var el = form.querySelector('[name="' + name + '"]:checked') || form.querySelector('[name="' + name + '"]');
        return el ? el.value : '';
    }
    function params() {
        var test = val('test');
        var p = {element: val('element'), test: test, basis: val('basis'), cap: val('cap')};
        if (test === 'cw') p.amp = val('amp');
        else p.op = val('op');
        if (test === 'pulse') { p.period = val('period'); p.marker = val('marker'); }
        if (test === 'loopback') p.period = val('period');
        return p;
    }
    function qs(p) {
        return Object.keys(p).map(function (k) {
            return encodeURIComponent(k) + '=' + encodeURIComponent(p[k]);
        }).join('&');
    }
    function fmt(v, d) { return (v === null || v === undefined) ? '—' : Number(v).toFixed(d); }

    function showFor(test) {
        root.querySelectorAll('.pt-cw').forEach(function (e) { e.hidden = test !== 'cw'; });
        root.querySelectorAll('.pt-op').forEach(function (e) { e.hidden = test === 'cw'; });
        root.querySelectorAll('.pt-period').forEach(function (e) { e.hidden = test === 'cw'; });
        root.querySelectorAll('.pt-marker').forEach(function (e) { e.hidden = test !== 'pulse'; });
        var sel = form.querySelector('[name="op"]');
        if (sel) Array.prototype.forEach.call(sel.options, function (o) {
            o.disabled = test === 'loopback' && o.getAttribute('data-measure') !== '1';
        });
        if (sel && sel.selectedOptions[0] && sel.selectedOptions[0].disabled) {
            var first = Array.prototype.find.call(sel.options, function (o) { return !o.disabled; });
            if (first) sel.value = first.value;
        }
    }

    var planSeq = 0;
    function refreshPlan() {
        var p = params();
        showFor(p.test);
        $('#pt-download').href = '/port-test/script.py?' + qs(p);
        var seq = ++planSeq;
        fetch('/port-test/plan?' + qs(p)).then(function (r) { return r.json(); }).then(function (j) {
            if (seq !== planSeq) return;
            var box = $('#pt-plan');
            if (!j.ok) { box.textContent = j.error; box.classList.add('pt-warn'); $('#pt-download').setAttribute('aria-disabled', 'true'); return; }
            box.classList.remove('pt-warn');
            $('#pt-download').removeAttribute('aria-disabled');
            var pl = j.plan;
            var t = 'Plays ' + pl.operation + ' on ' + pl.element + ' at port ' + pl.port.join('/') +
                ': peak ' + fmt(pl.expected_peak, 4) + ' of full scale';
            if (pl.expected_dbm !== null) t += ' = ' + fmt(pl.expected_dbm, 2) + ' dBm';
            if (pl.marker_port) t += '; scope trigger on DIG ' + pl.marker_port.join('/');
            t += '. ' + pl.notes.join(' ');
            box.textContent = t;
        });
    }
    form.addEventListener('change', refreshPlan);
    form.addEventListener('input', function (e) { if (e.target.type === 'number') refreshPlan(); });

    function target() { var t = root.querySelector('[name="pt-target"]:checked'); return t ? t.value : 'saas'; }
    function showTarget() {
        var t = target();
        root.querySelectorAll('.pt-t-saas').forEach(function (e) { e.hidden = t !== 'saas'; });
        root.querySelectorAll('.pt-t-opx').forEach(function (e) { e.hidden = t !== 'opx'; });
    }
    root.querySelectorAll('[name="pt-target"]').forEach(function (r) { r.addEventListener('change', showTarget); });

    function msg(t, warn) { var m = $('#pt-msg'); m.textContent = t; m.classList.toggle('pt-warn', !!warn); }
    function post(url, body) {
        return fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                           body: JSON.stringify(body || {})}).then(function (r) { return r.json(); });
    }
    function inputVal(n) { var e = root.querySelector('[name="' + n + '"]'); return e ? e.value.trim() : ''; }
    function saveTarget() {
        var body = {target: target(),
                    saas: {email: inputVal('saas_email'), host: inputVal('saas_host'), version: inputVal('saas_version')},
                    opx: {host: inputVal('opx_host'), cluster: inputVal('opx_cluster'), port: inputVal('opx_port')}};
        var pw = root.querySelector('[name="saas_password"]');
        if (pw && pw.value) body.password = pw.value;
        return post('/port-test/settings', body).then(function (j) {
            if (pw) pw.value = '';
            if (j.ok && pw) pw.placeholder = j.settings.saas.password_saved
                ? '•••••••• saved (' + j.settings.saas.password_where + ')' : 'not saved';
            return j;
        });
    }
    $('#pt-save').addEventListener('click', function () {
        saveTarget().then(function (j) { msg(j.ok ? 'Target saved.' : j.error, !j.ok); });
    });
    var rm = $('#pt-pw-remove');
    if (rm) rm.addEventListener('click', function () {
        post('/port-test/settings/password/remove').then(function (j) {
            var pw = root.querySelector('[name="saas_password"]');
            if (pw) pw.placeholder = 'not saved';
            rm.hidden = true;
            msg('Saved password removed.');
        });
    });

    $('#pt-run').addEventListener('click', function () {
        var t = target();
        if (t === 'opx' && !$('#pt-hw-ok').checked) { msg('Tick the fridge-line warning first.', true); return; }
        saveTarget().then(function (s) {
            if (!s.ok) { msg(s.error, true); return; }
            var body = params(); body.target = t; body.hardware_ok = t === 'opx' && $('#pt-hw-ok').checked;
            msg('Starting…');
            $('#pt-result').innerHTML = '';
            post('/port-test/run', body).then(function (j) {
                if (!j.ok) { msg(j.error, true); return; }
                runId = j.id;
                running(true);
                poll();
            });
        });
    });
    $('#pt-stop').addEventListener('click', function () {
        if (!runId) return;
        post('/port-test/stop', {id: runId}).then(function () { msg('Stopping…'); });
    });

    function running(on) { $('#pt-run').hidden = on; $('#pt-stop').hidden = !on; }
    function poll() {
        clearTimeout(pollTimer);
        if (!runId || !document.body.contains(root)) return;
        fetch('/port-test/status?id=' + encodeURIComponent(runId)).then(function (r) { return r.json(); }).then(function (st) {
            if (st.state === 'running') {
                msg((st.stopping ? 'Stopping (' + st.stop_why + ')… ' : 'Running: ') + (st.phase || 'starting') +
                    (st.detail ? ' · ' + st.detail : '') + ' · ' + st.elapsed_s + ' s');
                pollTimer = setTimeout(poll, 1000);
                return;
            }
            running(false);
            var res = st.result || {};
            runId = null;
            if (!res.ok) { msg('Failed: ' + (res.error || 'no answer'), true); return; }
            msg(st.stop_why ? 'Stopped (' + st.stop_why + ').' : 'Done.');
            renderResult(res, (st.meta || {}).plan || {});
        }).catch(function () { pollTimer = setTimeout(poll, 2000); });
    }

    function portKey(pl) {
        var p = pl.port || [];
        return p.length === 3 ? p[1] + '-' + p[2] : String(p[1]);
    }
    function sparkline(row, w, h) {
        var ys = row.re, n = ys.length;
        if (!n) return '';
        var lo = Math.min.apply(null, ys), hi = Math.max.apply(null, ys);
        if (row.im) { lo = Math.min(lo, Math.min.apply(null, row.im)); hi = Math.max(hi, Math.max.apply(null, row.im)); }
        var span = (hi - lo) || 1;
        function path(a) {
            var s = '', stride = Math.max(1, Math.floor(a.length / 2000));
            for (var i = 0; i < a.length; i += stride)
                s += (i ? 'L' : 'M') + (i / (n - 1 || 1) * w).toFixed(1) + ',' + (h - (a[i] - lo) / span * h).toFixed(1);
            return s;
        }
        return '<svg class="pt-trace" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" width="100%" height="' + h + '">' +
            '<path d="' + path(ys) + '" class="pt-re"/>' + (row.im ? '<path d="' + path(row.im) + '" class="pt-im"/>' : '') + '</svg>';
    }
    function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
    function renderResult(res, pl) {
        var out = $('#pt-result'), html = '';
        if (res.mode === 'saas') {
            var want = portKey(pl), keys = Object.keys(res.traces || {});
            html += '<p>Simulator' + (res.version ? ' (QOP ' + esc(res.version) + ')' : '') +
                ': expected peak <b>' + fmt(res.expected_peak, 4) + '</b> on port ' + esc((pl.port || []).join('/')) + '.</p>';
            keys.sort(function (a, b) { return (b.indexOf(':' + want) >= 0) - (a.indexOf(':' + want) >= 0); });
            keys.forEach(function (k) {
                var row = res.traces[k], ours = !row.digital && (k.split(':').pop() === want || k.split(':').pop().indexOf(want + '-') === 0);
                var ok = ours ? Math.abs(row.peak - res.expected_peak) <= Math.max(1e-3, 0.01 * res.expected_peak) : null;
                html += '<div class="pt-res-row"><div class="pt-res-head"><code>' + esc(k) + '</code> ' +
                    (row.digital ? 'digital marker' : 'peak ' + fmt(row.peak, 5)) +
                    (ok === true ? ' <span class="pt-ok">matches the config</span>' : '') +
                    (ok === false ? ' <span class="pt-warn">differs from the config (' + fmt(row.peak / (res.expected_peak || 1), 3) + '×)</span>' : '') +
                    ' · ' + row.n + ' samples' + (row.rate ? ' at ' + (row.rate / 1e9) + ' GSa/s' : '') + '</div>' +
                    sparkline(row, 600, 70) + '</div>';
            });
        } else {
            var adc = res.adc || {};
            if (!Object.keys(adc).length) html += '<p>Hardware run ended. Check the scope / spectrum analyser.</p>';
            Object.keys(adc).forEach(function (k) {
                html += '<div class="pt-res-row"><div class="pt-res-head"><code>' + esc(k) + '</code> raw ADC (averaged)</div>' +
                    sparkline({re: adc[k]}, 600, 90) + '</div>';
            });
        }
        out.innerHTML = html;
    }

    function stopOnLeave() {
        if (!runId) return;
        try { navigator.sendBeacon('/port-test/stop?left=1&id=' + encodeURIComponent(runId)); } catch (e) { /* the heartbeat stops it */ }
        runId = null;
    }
    window.addEventListener('pagehide', stopOnLeave);
    document.body.addEventListener('htmx:beforeSwap', function onSwap(e) {
        if (!document.body.contains(root)) { document.body.removeEventListener('htmx:beforeSwap', onSwap); return; }
        var t = e.detail && e.detail.target;
        if (t && (t === root || t.contains(root))) { stopOnLeave(); document.body.removeEventListener('htmx:beforeSwap', onSwap); }
    });

    showTarget();
    refreshPlan();
    fetch('/port-test/status').then(function (r) { return r.json(); }).then(function (st) {
        if (st.state === 'running') { runId = st.id; running(true); poll(); }
    });
})();
