/* The CZ gate dialog (one door for every entry point).
 *
 * window.CzGate.open({pair}) fetches GET /pulse/cz-gate/dialog, shows it as a
 * modal, builds the form from its JSON block and asks
 * POST /api/pulse/cz-gate/plan (debounced) on every change. "Check" posts the
 * same body to /check (the lab's code on chip copies) and draws the evidence;
 * "Create" sends only the server's check token and the plan signature to
 * /create. The server keeps every verdict; nothing here decides one.
 * Any input change after a check greys the evidence and returns to "Check".
 * A stale store (409 stale) re-runs the check itself, at most twice.
 * Entry buttons: [data-czg-open] (opens the dialog, data-czg-pair preselects;
 * "czplan" reads the selected CZ plan row) and [data-czg-pulse] (the pulse form
 * for that qubit in the inspector). Their clicks never reach the row under them.
 */
(function () {
    'use strict';
    var DEBOUNCE_MS = 250;
    var STALE_ROUNDS = 2;
    var S = null;   // the open dialog's state

    function $(id) { return document.getElementById(id); }
    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }
    function clear(n) { if (n) n.textContent = ''; return n; }
    function UIkit() { return window.UI || null; }
    function callout(tone, title, text) {
        var U = UIkit();
        if (U) return U.callout(tone, title, text);
        var d = el('div', 'ui-callout ui-callout--' + tone);
        d.setAttribute('role', 'note');
        d.appendChild(el('div', 'ui-callout-title', title));
        d.appendChild(el('p', 'ui-callout-text', text));
        return d;
    }
    function note(text) { return el('p', 'ui-note', text); }
    function fresh() {
        return { data: null, busy: false, phase: 'check', plan: null, planSig: null, check: null,
                 checkSig: null, stale: 0, entered: {}, raw: {}, cls: {}, timer: null, handle: null,
                 opener: null, seq: 0, created: null };
    }

    /* ---- the request body ------------------------------------------------ */
    function klass() {
        var v = $('czg-class') ? $('czg-class').value : '';
        return (S.data.classes || []).filter(function (c) { return c['class'] === v; })[0] || null;
    }
    function extraPairs() {
        var box = $('czg-more-list');
        if (!box) return [];
        return Array.prototype.slice.call(box.querySelectorAll('input[data-czg-more]:checked'))
            .map(function (i) { return i.value; });
    }
    function body() {
        var c = klass();
        var pulses = {};
        if (c && c.layout === 'known') {
            (c.slots || []).forEach(function (s) {
                var cls = S.cls[s.field];
                if (!cls) return;
                pulses[s.field] = { 'class': cls, fields: Object.assign({}, S.entered[s.field] || {}) };
            });
        } else {
            Object.keys(S.entered).forEach(function (f) {
                pulses[f] = { 'class': S.cls[f] || null, fields: Object.assign({}, S.entered[f]) };
            });
        }
        var pair = $('czg-pair').value;
        var model = $('czg-model') && !$('czg-model-field').hidden ? $('czg-model').value : '';
        return {
            pairs: pair ? [pair].concat(extraPairs().filter(function (p) { return p !== pair; })) : [],
            gate_name: $('czg-name').value.trim(),
            gate_class: c ? c['class'] : '',
            pulses: pulses,
            copy_from: $('czg-from').value || null,
            model_gate: model ? model.split('/') : null
        };
    }

    /* ---- buttons and status ---------------------------------------------- */
    function say(t) { var s = $('czg-status'); if (s) s.textContent = t || ''; }
    function alertBox(node) {
        var a = clear($('czg-alert'));
        if (a && node) a.appendChild(node);
    }
    function firstPlan() { return S.plan && S.plan.pairs && S.plan.pairs[0]; }
    function allowedPairs() {
        return ((S.check && S.check.pairs) || []).filter(function (p) { return p.verdict && p.verdict.allowed; });
    }
    function refresh() {
        var b = $('czg-primary');
        if (!b) return;
        if (S.phase === 'done') { b.textContent = 'Close'; b.disabled = false; return; }
        if (S.phase === 'create') {
            var ok = allowedPairs();
            var unchecked = ok.some(function (p) { return p.verdict.state !== 'verified'; });
            b.textContent = unchecked ? 'Create without the lab check'
                : (ok.length === 1 ? 'Create gate' : 'Create ' + ok.length + ' gates');
            b.disabled = S.busy || !ok.length;
            return;
        }
        b.textContent = 'Check';
        var fp = firstPlan();
        b.disabled = S.busy || !fp || !fp.ok;
    }
    function setBusy(on, text) {
        S.busy = on;
        var d = $('czg-dialog');
        if (d) d.setAttribute('aria-busy', on ? 'true' : 'false');
        say(text || '');
        refresh();
    }

    /* ---- the form -------------------------------------------------------- */
    function pairInfo(id) {
        return (S.data.pairs || []).filter(function (p) { return p.id === id; })[0] || null;
    }
    function fillPairLine() {
        var p = pairInfo($('czg-pair').value);
        var line = $('czg-pair-line');
        var gist = $('czg-gist');
        if (gist) gist.textContent = p ? 'Pair ' + p.id : 'Pick a pair';
        if (!line) return;
        if (!p) { line.textContent = ''; return; }
        var mover = p.moving_qubit === 'control' ? p.control : p.moving_qubit === 'target' ? p.target : null;
        line.textContent = (p.control || '?') + ' → ' + (p.target || '?') + ', moving qubit '
            + (mover || 'not recorded') + ' (a property of the pair)';
    }
    function fillClasses() {
        var sel = clear($('czg-class'));
        (S.data.classes || []).forEach(function (c, i) {
            var o = el('option', null, c.available ? c.label : c.label + ' (not available)');
            o.value = c['class'];
            if (!c.available) o.disabled = true;
            sel.appendChild(o);
        });
        var first = (S.data.classes || []).filter(function (c) { return c.available; })[0];
        if (first) sel.value = first['class'];
        whyClass();
    }
    function whyClass() {
        var w = clear($('czg-class-why'));
        var off = (S.data.classes || []).filter(function (c) { return !c.available; });
        off.forEach(function (c) { w.appendChild(el('span', 'czg-why-line', c.label + ': ' + c.why)); });
    }
    function fillModel() {
        var c = klass();
        var field = $('czg-model-field');
        var sel = clear($('czg-model'));
        var copied = c && c.layout === 'copied';
        field.hidden = !copied;
        if (!copied) return;
        var pair = $('czg-pair').value;
        var models = c.models || [];
        models.forEach(function (m) {
            var o = el('option', null, m[0] + ' / ' + m[1]);
            o.value = m[0] + '/' + m[1];
            sel.appendChild(o);
        });
        var own = models.filter(function (m) { return m[0] === pair; })[0] || models[0];
        if (own) sel.value = own[0] + '/' + own[1];
    }
    function fillFrom() {
        var sel = clear($('czg-from'));
        var o = el('option', null, 'Defaults of the pulse class');
        o.value = '';
        sel.appendChild(o);
        var p = pairInfo($('czg-pair').value);
        ((p && p.gates) || []).forEach(function (g) {
            var x = el('option', null, 'The gate ' + g + ' of this pair');
            x.value = g;
            sel.appendChild(x);
        });
    }
    function fillMore() {
        var box = clear($('czg-more-list'));
        var pair = $('czg-pair').value;
        (S.data.pairs || []).forEach(function (p) {
            if (p.id === pair) return;
            var row = el('div', 'czg-more-row');
            var l = el('label', 'ui-check');
            var i = document.createElement('input');
            i.type = 'checkbox';
            i.value = p.id;
            i.setAttribute('data-czg-more', '');
            l.appendChild(i);
            l.appendChild(document.createTextNode(p.id));
            row.appendChild(l);
            row.appendChild(el('span', 'ui-note czg-more-why'));
            box.appendChild(row);
        });
    }
    var SOURCE_WORDS = { given: 'entered', copied: 'copied', 'default': 'default' };
    function sourceOf(planned, field, name) {
        var f = planned && planned.fields && planned.fields[field];
        var s = f && f[name];
        if (!s) return '';
        return SOURCE_WORDS[s] || s;
    }
    function shown(v) {
        if (v === null || v === undefined) return '';
        if (typeof v === 'object') return JSON.stringify(v);
        return String(v);
    }
    function fieldInput(field, name, label, kind, value, src) {
        var l = el('label', 'ui-field czg-fieldbox');
        var head = el('span', 'ui-field-label', label);
        if (src) { head.appendChild(document.createTextNode(' ')); head.appendChild(el('span', src === 'entered' ? 'czg-src czg-src--entered' : src.indexOf('copied') === 0 ? 'czg-src czg-src--copied' : 'czg-src', src)); }
        l.appendChild(head);
        var i = document.createElement('input');
        i.className = 'czg-input czg-input--text';
        i.type = 'text';
        i.spellcheck = false;
        if (kind === 'float' || kind === 'int') i.setAttribute('inputmode', 'decimal');
        i.setAttribute('data-czg-field', field);
        i.setAttribute('data-czg-name', name);
        i.setAttribute('data-czg-kind', kind || '');
        var typed = (S.raw[field] || {})[name];
        i.value = typed !== undefined ? typed : shown(value);
        l.appendChild(i);
        return l;
    }
    function kindOf(v) {
        if (typeof v === 'boolean') return 'bool';
        if (typeof v === 'number') return Number.isInteger(v) ? 'int' : 'float';
        return 'str';
    }
    function slotBlock(title, sub) {
        var b = el('fieldset', 'czg-slot');
        var lg = el('legend', 'czg-slot-title', title);
        b.appendChild(lg);
        if (sub) b.appendChild(note(sub));
        return b;
    }
    function fillSlots() {
        var host = clear($('czg-slots'));
        var c = klass();
        var fp = firstPlan();
        if (!c) return;
        if (c.layout === 'known') {
            (c.slots || []).forEach(function (s) {
                var b = slotBlock(s.field, 'Plays on ' + s.line_words + '.');
                var l = el('label', 'ui-field');
                l.appendChild(el('span', 'ui-field-label', 'Pulse class'));
                var sel = el('select', 'czg-input');
                sel.setAttribute('data-czg-slot', s.field);
                if (!s.required) { var n = el('option', null, 'No pulse on this line'); n.value = ''; sel.appendChild(n); }
                (s.pulse_classes || []).forEach(function (pc) {
                    var o = el('option', null, pc.label);
                    o.value = pc['class'];
                    sel.appendChild(o);
                });
                if (S.cls[s.field] === undefined) S.cls[s.field] = s.required ? ((s.pulse_classes || [])[0] || {})['class'] || '' : '';
                sel.value = S.cls[s.field] || '';
                l.appendChild(sel);
                b.appendChild(l);
                var spec = (s.pulse_classes || []).filter(function (pc) { return pc['class'] === S.cls[s.field]; })[0];
                if (spec) {
                    var grid = el('div', 'ui-field-grid');
                    var planned = fp && fp.ok && fp.pulses ? fp.pulses[s.field] : null;
                    spec.fields.forEach(function (f) {
                        var v = planned && planned[f.name] !== undefined ? planned[f.name] : f['default'];
                        grid.appendChild(fieldInput(s.field, f.name, f.label + (f.unit ? ' (' + f.unit + ')' : ''),
                            f.kind, v, sourceOf(fp, s.field, f.name) || 'default'));
                    });
                    b.appendChild(grid);
                }
                host.appendChild(b);
            });
            return;
        }
        if (!fp || !fp.ok) {
            host.appendChild(note('The pulse fields come from the model gate once the plan is made.'));
            return;
        }
        Object.keys(fp.pulses || {}).forEach(function (f) {
            var pulse = fp.pulses[f] || {};
            var op = (fp.operations || {})[f] || {};
            S.cls[f] = pulse.__class__ || null;
            var b = slotBlock(f, 'Plays on ' + (op.channel || 'a line') + '. Class ' + (pulse.__class__ || '?') + ', from the model gate.');
            var grid = el('div', 'ui-field-grid');
            Object.keys(pulse).forEach(function (k) {
                var v = pulse[k];
                if (k === '__class__' || k === 'id' || (typeof v === 'string' && v.charAt(0) === '#')) return;
                grid.appendChild(fieldInput(f, k, k, kindOf(v), v, sourceOf(fp, f, k) || 'default'));
            });
            b.appendChild(grid);
            host.appendChild(b);
        });
    }
    function parse(input) {
        var kind = input.getAttribute('data-czg-kind');
        var raw = input.value.trim();
        if (kind === 'float' || kind === 'int') {
            if (raw === '' ) return { ok: false, why: 'Type a number.' };
            var n = Number(raw);
            if (!isFinite(n)) return { ok: false, why: 'Type a number.' };
            if (kind === 'int' && !Number.isInteger(n)) return { ok: false, why: 'Type a whole number.' };
            return { ok: true, v: n };
        }
        if (kind === 'bool') {
            if (/^(true|yes|1)$/i.test(raw)) return { ok: true, v: true };
            if (/^(false|no|0)$/i.test(raw)) return { ok: true, v: false };
            return { ok: false, why: 'Type true or false.' };
        }
        if (raw === '' || raw === 'null') return { ok: true, v: null };
        return { ok: true, v: raw };
    }

    /* ---- the plan -------------------------------------------------------- */
    function inputsChanged() {
        S.seq++;
        if (S.check) {
            var ev = $('czg-evidence');
            if (ev) ev.classList.add('czg-stale');
            say('Inputs changed, check again.');
        }
        S.phase = 'check';
        S.stale = 0;
        refresh();
        clearTimeout(S.timer);
        S.timer = setTimeout(plan, DEBOUNCE_MS);
    }
    function post(url, payload) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(payload) })
            .then(function (r) {
                return r.json().then(function (j) { return { r: r, j: j || {} }; },
                                     function () { return { r: r, j: {} }; });
            });
    }
    function plan() {
        var b = body();
        var mine = S.seq;
        if (!b.pairs.length) { S.plan = null; refresh(); return Promise.resolve(); }
        return post('/api/pulse/cz-gate/plan', b).then(function (x) {
            if (!S || mine !== S.seq) return;
            if (!x.r.ok || !x.j.ok) {
                S.plan = null;
                alertBox(callout('danger', 'The plan could not be made', (x.j.error || ('The app answered HTTP ' + x.r.status)) + ' Change the inputs or close the dialog.'));
                refresh();
                return;
            }
            S.plan = x.j;
            S.planSig = x.j.plan_sig;
            renderPlan();
        }).catch(function (e) {
            if (!S) return;
            alertBox(callout('danger', 'The plan could not be made', 'The app did not answer (' + e + '). Try again.'));
        });
    }
    function renderPlan() {
        var fp = firstPlan();
        alertBox(null);
        var why = clear($('czg-name-why'));
        if (fp && !fp.ok) {
            (fp.refusals || []).forEach(function (r) { why.appendChild(el('span', 'czg-why-line', r)); });
        }
        var focused = document.activeElement;
        var keep = focused && focused.getAttribute ? [focused.getAttribute('data-czg-field'), focused.getAttribute('data-czg-name')] : null;
        fillSlots();
        if (keep && keep[0]) {
            var again = $('czg-slots').querySelector('input[data-czg-field="' + keep[0] + '"][data-czg-name="' + keep[1] + '"]');
            if (again) { again.focus(); try { again.setSelectionRange(again.value.length, again.value.length); } catch (e) { /* not a text input */ } }
        }
        var rows = {};
        ((S.plan && S.plan.pairs) || []).forEach(function (p) { rows[p.pair] = p; });
        var n = 0;
        Array.prototype.slice.call(($('czg-more-list') || document.createElement('div')).querySelectorAll('.czg-more-row')).forEach(function (row) {
            var i = row.querySelector('input');
            var w = row.querySelector('.czg-more-why');
            var p = rows[i.value];
            if (i.checked) n++;
            w.textContent = !i.checked ? '' : !p ? '' : p.ok ? 'can be created' : (p.refusals || []).join(' ');
        });
        var cnt = $('czg-more-count');
        if (cnt) cnt.textContent = n ? String(n) : '';
        renderWrites();
        refresh();
    }
    function renderWrites() {
        var host = clear($('czg-writes'));
        var paths = [];
        ((S.plan && S.plan.pairs) || []).forEach(function (p) { if (p.ok) paths = paths.concat(p.paths || []); });
        if (!paths.length) { host.appendChild(note('Nothing yet: the plan has no gate to write.')); return; }
        host.appendChild(note(paths.length + (paths.length === 1 ? ' object' : ' objects')
            + ' in the working copy. Not on the chip until you press Apply.'));
        var U = UIkit();
        if (U) host.appendChild(U.disclosure('Paths', paths.length, paths.map(function (p) { return { part: 'path', text: p }; }), false));
    }

    /* ---- the check and its evidence --------------------------------------- */
    var TONE = { pass: 'ok', fail: 'danger', skipped: 'warn', note: 'neutral' };
    function num(v, unit) {
        if (v === null || v === undefined) return '';
        if (typeof v === 'number') return (Math.abs(v) >= 1000 || Number.isInteger(v) ? String(v) : v.toPrecision(4)) + (unit ? ' ' + unit : '');
        return String(v);
    }
    function renderChart(tl) {
        var host = clear($('czg-chart'));
        if (S.handle && S.handle.destroy) { try { S.handle.destroy(); } catch (e) { /* gone */ } }
        S.handle = null;
        if (!tl) { host.appendChild(note('No waveform: this pair was not checked.')); return; }
        if (!window.GateTimeline || typeof window.GateTimeline.render !== 'function') {
            host.appendChild(note('Chart not loaded. The checks and the table below still hold.'));
            return;
        }
        try { S.handle = window.GateTimeline.render(host, tl, {}); }
        catch (e) { clear(host).appendChild(note('Chart not loaded: ' + e)); }
    }
    function renderIntent(rows) {
        var host = clear($('czg-intent'));
        if (!rows || !rows.length) { host.appendChild(note('No pulse to compare.')); return; }
        var U = UIkit();
        if (!U) return;
        var marks = [];
        var data = rows.map(function (r) {
            var a = r.actual || {};
            var i = r.intended || {};
            marks.push([r.differs.length, r.differs.amplitude]);
            return [{ chip: r.operation },
                    i.length !== null ? num(i.length, 'ns') : i.length_source,
                    num(a.length_ns, 'ns'),
                    i.amplitude !== null ? num(i.amplitude, 'V') : i.amplitude_source,
                    num(a.max_v, 'V'), num(a.min_v, 'V')];
        });
        var t = U.table(['Pulse', 'Length entered', 'Length in config', 'Amplitude entered', 'Maximum', 'Minimum'],
                        data, false, [1, 2, 3, 4, 5], 'Intended and actual');
        Array.prototype.slice.call(t.querySelectorAll('tbody tr')).forEach(function (tr, k) {
            var tds = tr.children;
            if (marks[k][0]) { tds[1].classList.add('czg-diff'); tds[2].classList.add('czg-diff'); }
            if (marks[k][1]) { tds[3].classList.add('czg-diff'); tds[4].classList.add('czg-diff'); tds[5].classList.add('czg-diff'); }
        });
        host.appendChild(t);
        rows.forEach(function (r) { (r.why || []).forEach(function (w) { host.appendChild(note(r.operation + ': ' + w)); }); });
    }
    function renderChecks(p) {
        var host = clear($('czg-checks'));
        var U = UIkit();
        if (!p.ok) { (p.refusals || []).forEach(function (r) { host.appendChild(note(r)); }); return; }
        if (!U) return;
        host.appendChild(U.table(['Check', 'Result', 'Message'], (p.checks || []).map(function (c) {
            return [c.name, { dot: TONE[c.status] || 'neutral', text: c.status }, c.message];
        }), false, [], 'Checks'));
        (p.previews_skipped || []).forEach(function (s) { host.appendChild(note('No preview compared for ' + s)); });
    }
    function renderCheck() {
        var c = S.check;
        var ev = $('czg-evidence');
        if (ev) ev.classList.remove('czg-stale');
        var p = (c.pairs || [])[0];
        if (p) {
            renderChart(p.timeline);
            renderIntent(p.intent);
            renderChecks(p);
        }
        var ok = allowedPairs();
        var no = (c.pairs || []).filter(function (x) { return !(x.verdict && x.verdict.allowed); });
        var parts = [];
        if (!ok.length) {
            alertBox(callout('danger', 'Not created', (no.map(function (x) { return x.pair + ': ' + x.verdict.reason; }).join(' '))
                + ' Change the inputs and press Check again.'));
        } else if (ok.some(function (x) { return x.verdict.state !== 'verified'; })) {
            var why = ok.filter(function (x) { return x.verdict.state !== 'verified'; })[0].verdict.reason;
            alertBox(callout('warn', 'Not checked with the lab\'s code', why + ' You can still create it; the record will say it was not checked.'));
        } else if (no.length) {
            alertBox(callout('note', 'Some pairs are left out', no.map(function (x) { return x.pair + ': ' + x.verdict.reason; }).join(' ')));
        } else {
            alertBox(null);
        }
        if (no.length && ok.length) parts.push(no.length + ' left out');
        S.phase = ok.length ? 'create' : 'check';
        S.checkSig = c.plan_sig;
        setBusy(false, ok.length ? ('Checked. ' + parts.join(' ')).trim() : 'Checked: nothing can be created.');
    }
    function check() {
        if (!S || S.busy) return Promise.resolve();
        var b = body();
        var mine = S.seq;
        setBusy(true, 'Checking with the lab\'s code, about 10 s');
        return post('/api/pulse/cz-gate/check', b).then(function (x) {
            if (!S) return;
            if (mine !== S.seq) { setBusy(false, 'Inputs changed, check again.'); return; }
            if (!x.r.ok || !x.j.ok) {
                setBusy(false, '');
                alertBox(callout('danger', 'The check did not run', (x.j.error || ('The app answered HTTP ' + x.r.status)) + ' Press Check to try again.'));
                return;
            }
            S.check = x.j;
            renderCheck();
        }).catch(function (e) {
            if (!S) return;
            setBusy(false, '');
            alertBox(callout('danger', 'The check did not run', 'The app did not answer (' + e + '). Press Check to try again.'));
        });
    }

    /* ---- create ---------------------------------------------------------- */
    function create() {
        if (!S || S.busy || S.phase !== 'create' || !S.check) return Promise.resolve();
        setBusy(true, 'Creating…');
        return post('/api/pulse/cz-gate/create', { check_token: S.check.check_token, plan_sig: S.check.plan_sig })
            .then(function (x) {
                if (!S) return;
                var j = x.j;
                if (j.tray_html && window._swapPendingTray) {
                    try { window._swapPendingTray(j.tray_html); } catch (e) { /* the tray poll catches up */ }
                }
                if (x.r.status === 409 && j.stale) {
                    if (S.stale >= STALE_ROUNDS) {
                        S.phase = 'check';
                        setBusy(false, '');
                        alertBox(callout('danger', 'The chip keeps changing', 'Something else is changing this chip while you create the gate. Nothing was written. Wait until it is quiet, then press Check.'));
                        return;
                    }
                    S.stale++;
                    setBusy(false, '');
                    alertBox(callout('warn', 'The chip changed', 'Nothing was written. The check runs again; look at the new result, then press Create again.'));
                    return check().then(function () {
                        if (S && S.phase === 'create') alertBox(callout('warn', 'Checked again', 'The chip changed after the first check. This is the new result; press Create again to write it.'));
                    });
                }
                if (!x.r.ok || !j.ok) {
                    S.phase = j.check_again ? 'check' : S.phase;
                    setBusy(false, '');
                    alertBox(callout('danger', 'Not created', j.error || ('The app answered HTTP ' + x.r.status)));
                    return;
                }
                done(j);
            }).catch(function (e) {
                if (!S) return;
                setBusy(false, '');
                alertBox(callout('danger', 'Not created', 'The app did not answer (' + e + '). Nothing is known to be written; look at the pending changes, then try again.'));
            });
    }
    function done(j) {
        S.phase = 'done';
        S.created = j;
        S.busy = false;
        var made = (j.created || []).map(function (c) { return c.gate + ' on ' + c.pair + (c.state !== 'verified' ? ' (not checked)' : ''); });
        var skip = (j.skipped || []).map(function (s) { return s.pair + ': ' + s.reason; });
        alertBox(callout('note', 'Created ' + made.join(', '),
            'Not on the chip yet: press Apply to write it. The gate is not active; the pair\'s cz stays as it was.'
            + (skip.length ? ' Left out: ' + skip.join(' ') : '')));
        var f = $('czg-form');
        if (f) f.hidden = true;
        var ev = $('czg-evidence');
        if (ev) ev.classList.add('czg-stale');
        var open = $('czg-open-pair');
        if (open) open.hidden = false;
        var cancel = $('czg-cancel');
        if (cancel) cancel.hidden = true;
        say('');
        refresh();
        if (window.htmx && document.body) {
            window.htmx.trigger(document.body, 'pulses-changed');
            window.htmx.trigger(document.body, 'diagnostics-changed');
        }
    }

    /* ---- open / close ---------------------------------------------------- */
    function host() {
        var h = $('czg-host');
        if (!h) { h = el('div'); h.id = 'czg-host'; document.body.appendChild(h); }
        return h;
    }
    function close() {
        var d = $('czg-dialog');
        if (S) { clearTimeout(S.timer); if (S.handle && S.handle.destroy) { try { S.handle.destroy(); } catch (e) { /* gone */ } } }
        var opener = S && S.opener;
        S = null;
        if (d) {
            if (typeof d.close === 'function') { try { d.close(); } catch (e) { /* not open */ } }
            d.removeAttribute('open');
            if (d.parentNode) d.parentNode.removeChild(d);
        }
        if (opener && opener.focus) { try { opener.focus(); } catch (e) { /* gone */ } }
    }
    function init() {
        var src = $('czg-data');
        S.data = JSON.parse((src && src.textContent) || '{}');
        fillPairLine();
        fillClasses();
        fillModel();
        fillFrom();
        fillMore();
        fillSlots();
        renderWrites();
        refresh();
        return plan();
    }
    function open(opts) {
        opts = opts || {};
        if (S) close();
        S = fresh();
        S.opener = opts.opener || null;
        var url = '/pulse/cz-gate/dialog' + (opts.pair ? '?pair=' + encodeURIComponent(opts.pair) : '');
        return fetch(url).then(function (r) { return r.text().then(function (t) { return { r: r, t: t }; }); })
            .then(function (x) {
                if (!S) return;
                var h = clear(host());
                h.innerHTML = x.t;
                var d = $('czg-dialog');
                if (!d) {
                    S = null;
                    h.textContent = '';
                    if (window.showToast) window.showToast('The gate dialog could not open: HTTP ' + x.r.status, 'error');
                    return;
                }
                if (typeof d.showModal === 'function') { try { d.showModal(); } catch (e) { d.setAttribute('open', ''); } }
                else d.setAttribute('open', '');
                return init();
            });
    }

    /* ---- events (delegated; CSP: no inline handlers) ---------------------- */
    function inDialog(t) { var d = $('czg-dialog'); return d && d.contains(t) ? d : null; }
    document.addEventListener('click', function (ev) {
        var t = ev.target && ev.target.closest ? ev.target : null;
        if (!t) return;
        var opener = t.closest('[data-czg-open]');
        var pulse = t.closest('[data-czg-pulse]');
        if (opener || pulse) {
            // the row under the button must not open the inspector as well
            ev.stopPropagation();
            ev.preventDefault();
            if (pulse) {
                var q = pulse.getAttribute('data-czg-pulse');
                if (window.htmx) window.htmx.ajax('GET', '/pulse/new?qubit=' + encodeURIComponent(q) + '&channel=xy',
                                                  { target: '#inspector-pane', swap: 'innerHTML' });
                return;
            }
            var pair = opener.getAttribute('data-czg-pair') || '';
            if (opener.getAttribute('data-czg-open') === 'czplan') {
                var row = document.querySelector('.czp-row-selected[data-pair]');
                pair = row ? row.getAttribute('data-pair') : '';
            }
            open({ pair: pair, opener: opener });
            return;
        }
        if (!inDialog(t) || !S) return;
        if (t.closest('[data-czg-close]')) { close(); return; }
        if (t.closest('#czg-open-pair')) {
            var p = $('czg-pair') ? $('czg-pair').value : '';
            close();
            if (p && window.htmx) window.htmx.ajax('GET', '/pair/' + encodeURIComponent(p), { target: '#inspector-pane', swap: 'innerHTML' });
            return;
        }
        if (t.closest('#czg-primary')) {
            if (S.busy) return;
            if (S.phase === 'done') close();
            else if (S.phase === 'create') create();
            else check();
        }
    }, true);
    document.addEventListener('change', function (ev) {
        var t = ev.target;
        if (!t || !inDialog(t) || !S) return;
        if (t.id === 'czg-pair') { fillPairLine(); fillFrom(); fillModel(); fillMore(); }
        else if (t.id === 'czg-class') { S.entered = {}; S.raw = {}; S.cls = {}; fillModel(); }
        else if (t.id === 'czg-model') { S.entered = {}; S.raw = {}; }
        else if (t.hasAttribute('data-czg-slot')) {
            var f = t.getAttribute('data-czg-slot');
            S.cls[f] = t.value;
            S.entered[f] = {};
            S.raw[f] = {};
        } else if (!t.hasAttribute('data-czg-more') && t.id !== 'czg-from') { return; }
        if (t.id === 'czg-class' || t.id === 'czg-model' || t.hasAttribute('data-czg-slot')) fillSlots();
        inputsChanged();
    });
    document.addEventListener('input', function (ev) {
        var t = ev.target;
        if (!t || !inDialog(t) || !S) return;
        if (t.id === 'czg-name') { inputsChanged(); return; }
        if (!t.hasAttribute('data-czg-field')) return;
        var f = t.getAttribute('data-czg-field');
        var n = t.getAttribute('data-czg-name');
        var r = parse(t);
        S.entered[f] = S.entered[f] || {};
        S.raw[f] = S.raw[f] || {};
        S.raw[f][n] = t.value;
        if (r.ok) { S.entered[f][n] = r.v; t.removeAttribute('aria-invalid'); t.classList.remove('czg-bad'); }
        else { delete S.entered[f][n]; t.setAttribute('aria-invalid', 'true'); t.classList.add('czg-bad'); say(r.why); }
        inputsChanged();
    });
    document.addEventListener('keydown', function (ev) {
        if (ev.key !== 'Escape' || !S) return;
        var d = $('czg-dialog');
        if (!d || !d.hasAttribute('open')) return;
        ev.preventDefault();
        if (!S.busy) close();
    });

    window.CzGate = { open: open, close: close, state: function () { return S; },
                      _check: check, _create: create, _plan: plan };
})();
