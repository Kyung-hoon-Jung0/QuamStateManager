/* Gaussian CZ "to several pairs" (Pulses page).
 *
 * The dialog skeleton is server-rendered inside the Gaussian CZ form
 * (_pulse_gaussian_cz.html); this file asks POST /api/pulse/gaussian-cz/plan
 * for every eligible pair's verdict, draws the Ready / Can't groups, and sends
 * the ticked Ready pairs to POST /api/pulse/gaussian-cz/apply-several with the
 * plan's own signature. No Replace anywhere: a taken name is a Can't.
 * Listeners are delegated on document, so the form may be swapped in and out
 * of the inspector without re-binding. */
(function () {
    'use strict';
    var state = { plan: null, opener: null, params: null, busy: false };

    function $(id) { return document.getElementById(id); }
    function el(tag, attrs, text) {
        var n = document.createElement(tag);
        if (attrs) Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
        if (text != null) n.textContent = text;
        return n;
    }
    function dlg() { return $('gcz-several'); }
    function ticks() {
        var d = dlg();
        return d ? Array.prototype.slice.call(d.querySelectorAll('input[data-gcz-pair]')) : [];
    }
    function params() {
        var f = $('gcz-form');
        var out = {};
        if (!f) return out;
        ['padding_length', 'qubit_filter_mhz', 'coupler_filter_mhz'].forEach(function (k) {
            var i = f.elements[k];
            if (i && String(i.value).trim() !== '') out[k] = i.value;
        });
        return out;
    }

    /* the button states follow the ticks */
    function refresh() {
        var d = dlg();
        if (!d) return;
        var boxes = ticks();
        var on = boxes.filter(function (b) { return b.checked; }).length;
        var add = $('gcz-sev-add');
        if (add) {
            add.textContent = 'Add to ' + on + (on === 1 ? ' pair' : ' pairs');
            add.disabled = on === 0 || state.busy;
        }
        var all = d.querySelector('[data-gcz-select="ready"]');
        var none = d.querySelector('[data-gcz-select="none"]');
        // The lit option = the ticks are exactly that preset (same exception to D-50 as gen-rules.js).
        [[all, boxes.length > 0 && on === boxes.length], [none, boxes.length > 0 && on === 0]].forEach(function (x) {
            if (!x[0]) return;
            x[0].setAttribute('aria-pressed', String(x[1]));
            x[0].classList.toggle('ui-segmented-on', x[1]);
        });
        var head = d.querySelector('input[data-gcz-group]');
        if (head) {
            head.checked = boxes.length > 0 && on === boxes.length;
            head.indeterminate = on > 0 && on < boxes.length;
        }
    }

    function render(plan) {
        var body = $('gcz-sev-body');
        body.textContent = '';
        var ready = plan.ready || [];
        var cant = plan.cant || [];
        if (ready.length) {
            var wrap = el('div', { 'class': 'gcz-sev-group' });
            var gbox = el('input', { type: 'checkbox', 'data-gcz-group': '1', checked: 'checked',
                                     'aria-label': 'Tick every ready pair' });
            gbox.checked = true;
            var det = el('details', { open: '', 'class': 'ui-disclosure' });
            var sum = el('summary', { 'class': 'ui-disclosure-summary' });
            sum.appendChild(el('span', null, 'Ready — ' + ready.length + ' of ' + plan.total + ' pairs'));
            if (plan.reason_suffix) sum.appendChild(el('span', { 'class': 'ui-count' }, ' ' + plan.reason_suffix));
            det.appendChild(sum);
            // The same group table as the rules' Apply dialog (D-56, D-62).
            var tbl = el('table', { 'class': 'ui-table gcz-sev-tbl' });
            var hr = el('tr');
            ['', 'Pair', 'Status', 'Moving qubit', 'Pulse names'].forEach(function (c, i) {
                var th = el('th', { 'class': 'ui-table-head', scope: 'col' }, c);
                if (!i) th.setAttribute('aria-label', 'Select');
                hr.appendChild(th);
            });
            tbl.appendChild(el('thead')).appendChild(hr);
            var tb = el('tbody');
            ready.forEach(function (r) {
                var tr = el('tr', { 'class': 'gcz-sev-row' });
                var cb = el('input', { type: 'checkbox', 'data-gcz-pair': r.pair, value: r.pair, 'aria-label': r.pair });
                cb.checked = true;
                var c0 = el('td', { 'class': 'ui-table-cell' }); c0.appendChild(cb); tr.appendChild(c0);
                var c1 = el('td', { 'class': 'ui-table-cell' }); c1.appendChild(el('code', { 'class': 'ui-chip' }, r.pair)); tr.appendChild(c1);
                var c2 = el('td', { 'class': 'ui-table-cell' });
                var v = el('div', { 'class': 'ui-verdict ui-verdict--ok' });
                v.appendChild(el('span', { 'class': 'ui-dot', 'aria-hidden': 'true' }));
                v.appendChild(el('strong', { 'class': 'ui-verdict-key' }, 'Ready'));
                c2.appendChild(v); tr.appendChild(c2);
                tr.appendChild(el('td', { 'class': 'ui-table-cell' }, r.moving));
                var names = (r.pulses || []).join(', ');
                tr.appendChild(el('td', { 'class': 'ui-table-cell ui-table-muted gcz-sev-names', title: names }, names));
                tb.appendChild(tr);
            });
            tbl.appendChild(tb);
            det.appendChild(tbl);
            wrap.appendChild(gbox);
            wrap.appendChild(det);
            body.appendChild(wrap);
        } else {
            body.appendChild(el('p', { 'class': 'ui-note' }, 'No pair is ready.'));
        }
        if (cant.length) {
            var n = cant.reduce(function (a, c) { return a + c.pairs.length; }, 0);
            var cdet = el('details', { open: '', 'class': 'gcz-sev-cant ui-disclosure' });
            cdet.appendChild(el('summary', { 'class': 'ui-disclosure-summary' }, 'Can’t — ' + n));
            cant.forEach(function (c) {
                var line = el('div', { 'class': 'gcz-sev-reason' });
                line.appendChild(el('span', null, c.pairs.length + ' — ' + c.reason));
                line.appendChild(el('span', { 'class': 'gcz-sev-reason-pairs' }, c.pairs.join(', ')));
                cdet.appendChild(line);
            });
            body.appendChild(cdet);
        }
        (plan.notes || []).forEach(function (t) { body.appendChild(el('p', { 'class': 'ui-note' }, t)); });
        refresh();
    }

    function say(text) { var s = $('gcz-sev-status'); if (s) s.textContent = text || ''; }

    function openDialog(opener) {
        var d = dlg();
        if (!d) return;
        state.opener = opener || null;
        state.plan = null;
        state.params = params();
        $('gcz-sev-body').textContent = 'Checking this chip…';
        say('');
        refresh();
        if (typeof d.showModal === 'function') { try { d.showModal(); } catch (e) { d.setAttribute('open', ''); } }
        else d.setAttribute('open', '');
        fetch('/api/pulse/gaussian-cz/plan', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ params: state.params })
        }).then(function (r) {
            return r.json().then(function (j) { return { r: r, j: j || {} }; });
        }).then(function (x) {
            if (!x.r.ok || !x.j.ok) {
                $('gcz-sev-body').textContent = x.j.error || ('The app answered HTTP ' + x.r.status);
                return;
            }
            state.plan = x.j;
            render(x.j);
        }).catch(function (e) {
            $('gcz-sev-body').textContent = 'Could not check this chip: ' + e;
        });
    }

    function closeDialog() {
        var d = dlg();
        if (!d) return;
        if (typeof d.close === 'function') { try { d.close(); } catch (e) { d.removeAttribute('open'); } }
        else d.removeAttribute('open');
        d.removeAttribute('open');
        if (state.opener && state.opener.focus) state.opener.focus();
    }

    function apply() {
        var picked = ticks().filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
        if (!picked.length || !state.plan || state.busy) return;
        state.busy = true;
        refresh();
        say('Adding…');
        fetch('/api/pulse/gaussian-cz/apply-several', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ params: state.params, pairs: picked, plan_sig: state.plan.plan_sig })
        }).then(function (r) {
            return r.json().then(function (j) { return { r: r, j: j || {} }; },
                                 function () { return { r: r, j: {} }; });
        }).then(function (x) {
            state.busy = false;
            var j = x.j;
            if (j.tray_html && window._swapPendingTray) {
                try { window._swapPendingTray(j.tray_html); } catch (e) { /* the tray poll catches up */ }
            }
            if (!x.r.ok || !j.ok) {
                say('Not added: ' + (j.error || ('HTTP ' + x.r.status)));
                refresh();
                return;
            }
            say('');
            $('gcz-sev-body').textContent = 'Added to ' + j.count + (j.count === 1 ? ' pair' : ' pairs') +
                ' in the working state — one Ctrl+Z undoes all; review, then Apply to live.';
            ticks().forEach(function (b) { b.checked = false; });
            var add = $('gcz-sev-add');
            if (add) { add.disabled = true; add.textContent = 'Add to 0 pairs'; }
            if (window.htmx && document.body) {
                window.htmx.trigger(document.body, 'pulses-changed');
                window.htmx.trigger(document.body, 'diagnostics-changed');
            }
        }).catch(function (e) {
            state.busy = false;
            say('Not added: ' + e);
            refresh();
        });
    }

    document.addEventListener('click', function (ev) {
        var t = ev.target && ev.target.closest ? ev.target : null;
        if (!t) return;
        var open = t.closest('#gcz-several-open');
        if (open) { openDialog(open); return; }
        var d = dlg();
        if (!d || !d.contains(t)) return;
        if (t.closest('[data-gcz-close]')) { closeDialog(); return; }
        var sel = t.closest('[data-gcz-select]');
        if (sel) {
            var want = sel.getAttribute('data-gcz-select') === 'ready';
            ticks().forEach(function (b) { b.checked = want; });
            refresh();
            return;
        }
        if (t.closest('#gcz-sev-add')) { apply(); }
    });
    document.addEventListener('change', function (ev) {
        var t = ev.target;
        if (!t || !t.matches) return;
        if (t.matches('input[data-gcz-group]')) {
            ticks().forEach(function (b) { b.checked = t.checked; });
            refresh();
        } else if (t.matches('input[data-gcz-pair]')) {
            refresh();
        }
    });
    /* Esc closes and gives the focus back to the button that opened it */
    document.addEventListener('keydown', function (ev) {
        if (ev.key !== 'Escape') return;
        var d = dlg();
        if (!d || !d.hasAttribute('open')) return;
        ev.preventDefault();
        closeDialog();
    });
    document.addEventListener('close', function (ev) {
        if (ev.target && ev.target.id === 'gcz-several' && state.opener && state.opener.focus) {
            state.opener.focus();
        }
    }, true);
})();
