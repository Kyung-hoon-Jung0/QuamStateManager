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
                 opener: null, seq: 0, created: null, pairIdx: 0 };
    }

    /* ---- the request body ------------------------------------------------ */
    function klass() {
        var v = S.klass || '';
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
        var model = !$('czg-model-field').hidden ? (S.model || '') : '';
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
    function banner(tone, key, why) {
        var U = UIkit();
        return U.verdict(tone, key, why, true);
    }
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
    /* The head carries the pair as chips: pair, control -> target, moving qubit. */
    function fillPairLine() {
        var p = pairInfo($('czg-pair').value);
        var host = clear($('czg-chips'));
        if (!host) return;
        var U = UIkit();
        if (!p) { host.appendChild(U.pill('Pick a pair', 'neutral')); return; }
        var mover = p.moving_qubit === 'control' ? p.control : p.moving_qubit === 'target' ? p.target : null;
        host.appendChild(U.pill(p.id, 'accent'));
        host.appendChild(U.pill('control ' + (p.control || '?') + ' \u2192 target ' + (p.target || '?'), 'neutral'));
        host.appendChild(U.pill('moving qubit ' + (mover || 'not recorded'), 'neutral'));
    }
    function menuInto(hostId, spec) {
        var h = clear($(hostId));
        var U = UIkit();
        if (h && U && U.menu) h.appendChild(U.menu(spec));
    }
    function fillClasses() {
        var first = (S.data.classes || []).filter(function (c) { return c.available; })[0];
        S.klass = first ? first['class'] : '';
        menuInto('czg-class-host', { id: 'czg-class', kind: 'pick', label: 'Gate class', block: true,
            value: S.klass, placeholder: 'Pick a gate class',
            items: (S.data.classes || []).map(function (c) {
                return { value: c['class'], title: c.label, disabled: !c.available, reason: c.why };
            }) });
    }
    function fillModel() {
        var c = klass();
        var field = $('czg-model-field');
        var copied = c && c.layout === 'copied';
        field.hidden = !copied;
        if (!copied) { S.model = ''; return; }
        var pair = $('czg-pair').value;
        var models = c.models || [];
        var own = models.filter(function (m) { return m[0] === pair; })[0] || models[0];
        S.model = own ? own[0] + '/' + own[1] : '';
        menuInto('czg-model-host', { id: 'czg-model', kind: 'pick', label: 'Model gate', block: true,
            value: S.model, placeholder: 'Pick a model gate',
            items: models.map(function (m) { return { value: m[0] + '/' + m[1], title: m[1], desc: 'on ' + m[0] }; }) });
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
            row.appendChild(el('span', 'ui-text-quiet czg-more-why'));
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
    var SOURCE_TONE = { entered: 'accent', copied: 'ok' };
    function fieldInput(field, name, label, unit, kind, value, src) {
        var U = UIkit();
        var num = kind === 'float' || kind === 'int';
        var id = 'czg-f-' + field + '-' + name;
        var typed = (S.raw[field] || {})[name];
        var f = U.field({ label: label, source: src ? { text: src, tone: SOURCE_TONE[src.split(' ')[0]] || 'neutral' } : null,
                          control: { part: 'input', id: id, num: num, unit: unit || null, block: true,
                                     value: typed !== undefined ? typed : shown(value) } });
        var i = f.querySelector('input');
        i.spellcheck = false;
        if (num) i.setAttribute('inputmode', 'decimal');
        i.setAttribute('data-czg-field', field);
        i.setAttribute('data-czg-name', name);
        i.setAttribute('data-czg-kind', kind || '');
        i.value = typed !== undefined ? typed : shown(value);
        return f;
    }
    function kindOf(v) {
        if (typeof v === 'boolean') return 'bool';
        if (typeof v === 'number') return Number.isInteger(v) ? 'int' : 'float';
        return 'str';
    }
    var SLOT_WORDS = { flux_pulse_qubit: 'Qubit flux pulse', coupler_flux_pulse: 'Coupler pulse',
                       flux_pulse_control: 'Control flux pulse', flux_pulse_target: 'Target flux pulse' };
    /* The channel THIS pair's slot plays on: the plan's own answer, else the pair's lines. */
    function slotChannel(field, line, fp) {
        var op = fp && fp.ok && fp.operations ? fp.operations[field] : null;
        var ch = op && op.channel;
        if (ch) return ch.indexOf('coupler ') === 0 ? ch.slice(8) : ch;
        var p = pairInfo($('czg-pair').value);
        if (!p) return '';
        if (line === 'moving') {
            var q = p.moving_qubit === 'control' ? p.control : p.moving_qubit === 'target' ? p.target : null;
            return q ? q + '.z' : '';
        }
        if (line === 'control' || line === 'target') return p[line] ? p[line] + '.z' : '';
        if (line === 'coupler') return 'the coupler of ' + p.id;
        return '';
    }
    /* The identity colour of a line: control z, target z, coupler (the same order
       and numbering as the chart lanes and the table dots, from the server). */
    var LINE_RANK = { control: 0, target: 1, coupler: 2 };
    function slotLine(channel) {
        var p = pairInfo($('czg-pair').value) || {};
        var r = channel === (p.control + '.z') ? 0 : channel === (p.target + '.z') ? 1 : 2;
        var used = (S.slotChannels || []).map(function (c) {
            return c === (p.control + '.z') ? 0 : c === (p.target + '.z') ? 1 : 2;
        }).filter(function (x, i, a) { return a.indexOf(x) === i; }).sort();
        var k = used.indexOf(r);
        return (k < 0 ? 0 : k) + 1;
    }
    function slotBlock(field, channel, sub) {
        var U = UIkit();
        var root = U.slot({ line: slotLine(channel), title: SLOT_WORDS[field] || field, chip: channel || '?' });
        var head = root.querySelector('.ui-slot-head');
        if (head) { head.appendChild(document.createTextNode(' ')); head.appendChild(el('span', 'ui-text-quiet', field)); }
        var b = root.querySelector('.ui-slot-body') || root.appendChild(el('div', 'ui-slot-body'));
        if (sub) b.appendChild(el('p', 'ui-text-quiet', sub));
        return b;
    }
    function fillSlots() {
        var host = clear($('czg-slots'));
        var c = klass();
        var fp = firstPlan();
        if (!c) return;
        S.slotChannels = c.layout === 'known'
            ? (c.slots || []).filter(function (s) { return S.cls[s.field] !== '' || s.required; })
                .map(function (s) { return slotChannel(s.field, s.line, fp); })
            : Object.keys((fp && fp.ok && fp.pulses) || {}).map(function (f) { return slotChannel(f, null, fp); });
        var sum = $('czg-pulses-sum');
        if (sum) { var nl = S.slotChannels.length; sum.textContent = nl === 1 ? '1 line' : nl + ' lines play together'; }
        if (c.layout === 'known') {
            (c.slots || []).forEach(function (s) {
                var b = slotBlock(s.field, slotChannel(s.field, s.line, fp), '');
                var l = el('div', 'ui-field');
                var lh = el('div', 'ui-field-head');
                lh.appendChild(el('span', 'ui-field-label', 'Pulse class'));
                l.appendChild(lh);
                var sel = el('select', 'ui-select ui-select--block');
                sel.setAttribute('aria-label', 'Pulse class of ' + (SLOT_WORDS[s.field] || s.field));
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
                    var grid = el('div', 'ui-grid2');
                    var planned = fp && fp.ok && fp.pulses ? fp.pulses[s.field] : null;
                    spec.fields.forEach(function (f) {
                        var v = planned && planned[f.name] !== undefined ? planned[f.name] : f['default'];
                        grid.appendChild(fieldInput(s.field, f.name, f.label, f.unit,
                            f.kind, v, sourceOf(fp, s.field, f.name) || 'default'));
                    });
                    b.appendChild(grid);
                }
                host.appendChild(b.parentNode);
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
            var b = slotBlock(f, slotChannel(f, null, fp), 'Class ' + (pulse.__class__ || '?') + ', from the model gate.');
            var grid = el('div', 'ui-grid2');
            Object.keys(pulse).forEach(function (k) {
                var v = pulse[k];
                if (k === '__class__' || k === 'id' || (typeof v === 'string' && v.charAt(0) === '#')) return;
                grid.appendChild(fieldInput(f, k, k, '', kindOf(v), v, sourceOf(fp, f, k) || 'default'));
            });
            b.appendChild(grid);
            host.appendChild(b.parentNode);
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
        var mine = S.seq, g = S;
        if (!b.pairs.length) { S.plan = null; refresh(); return Promise.resolve(); }
        return post('/api/pulse/cz-gate/plan', b).then(function (x) {
            if (S !== g || mine !== S.seq) return;
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
            if (S !== g) return;
            alertBox(callout('danger', 'The plan could not be made', 'The app did not answer (' + e + '). Try again.'));
        });
    }
    function renderPlan() {
        var fp = firstPlan();
        alertBox(null);
        var why = clear($('czg-name-why'));
        var bad = fp && !fp.ok ? (fp.refusals || []) : [];
        bad.forEach(function (r) { why.appendChild(el('span', 'czg-why-line', r)); });
        why.hidden = !bad.length;
        if (bad.length) $('czg-name').setAttribute('aria-invalid', 'true'); else $('czg-name').removeAttribute('aria-invalid');
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
        if (cnt) cnt.textContent = n ? '+' + n + (n === 1 ? ' pair' : ' pairs') : 'only ' + ($('czg-pair').value || 'this pair');
        renderWrites();
        refresh();
    }
    function renderWrites(only) {
        var host = clear($('czg-writes'));
        var sum = $('czg-writes-sum');
        var paths = [];
        if (only) paths = only.ok ? (only.paths || []) : [];
        else ((S.plan && S.plan.pairs) || []).forEach(function (p) { if (p.ok) paths = paths.concat(p.paths || []); });
        if (sum) sum.textContent = paths.length ? paths.length + (paths.length === 1 ? ' object' : ' objects') : 'nothing yet';
        if (!paths.length) { host.appendChild(el('p', 'ui-text-quiet', 'The plan has no gate to write yet.')); return; }
        host.appendChild(el('p', 'ui-text-quiet', 'In the working copy. Not on the chip until you press Apply.'));
        var U = UIkit();
        paths.forEach(function (x) { host.appendChild(U.path(x)); });
    }
    /* ---- the check and its evidence --------------------------------------- */
    var TONE = { pass: 'ok', fail: 'danger', skipped: 'warn', note: 'neutral' };
    /* The shortest exact form: 0.30000000000000004 -> 0.3, 0.1001 stays 0.1001
       (12 significant digits, never an exponent for a value in a person's range). */
    function fmt(v) {
        var x = parseFloat(Number(v).toPrecision(12));
        var s = String(x);
        if (s.indexOf('e') < 0) return s;
        return Math.abs(x) >= 1e-6 && Math.abs(x) < 1e21 ? x.toFixed(12).replace(/0+$/, '').replace(/\.$/, '') : s;
    }
    function num(v, unit) {
        if (v === null || v === undefined) return '';
        if (typeof v === 'number') return fmt(v) + (unit ? ' ' + unit : '');
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
        clear($('czg-chart-tools'));
        try { S.handle = window.GateTimeline.render(host, tl, { toolbarHost: $('czg-chart-tools') }); }
        catch (e) { clear(host).appendChild(note('Chart not loaded: ' + e)); }
    }
    function lineOfChannel(tl) {
        var m = {};
        ((tl && tl.lanes) || []).forEach(function (ln) { m[ln.key] = (ln.color_index || 0) + 1; });
        return m;
    }
    var CELL_TONE = { warn: 'ui-text-warn', danger: 'ui-text-danger' };
    function renderIntent(rows, tl) {
        var host = clear($('czg-intent'));
        var sum = $('czg-intent-sum');
        if (!rows || !rows.length) { if (sum) sum.textContent = ''; host.appendChild(el('p', 'ui-text-quiet', 'No pulse to compare.')); return; }
        var U = UIkit();
        var lines = lineOfChannel(tl);
        var oneLevel = rows.every(function (r) { var a = r.actual || {}; return a.max_v === a.min_v; });
        var data = rows.map(function (r) {
            var a = r.actual || {};
            var i = r.intended || {};
            var levels = oneLevel ? [num(a.max_v, 'V')] : [num(a.max_v, 'V'), num(a.min_v, 'V')];
            return [{ chip: r.operation },
                    i.length !== null ? num(i.length, 'ns') : i.length_source,
                    num(a.length_ns, 'ns'),
                    i.amplitude !== null ? num(i.amplitude, 'V') : i.amplitude_source]
                .concat(levels, [num(a.start_ns, 'ns')]);
        });
        var cols = ['Pulse', 'Length entered', 'In config', 'Amplitude entered']
            .concat(oneLevel ? ['Level'] : ['Maximum', 'Minimum'], ['Starts at']);
        var t = U.table(cols, data, false, cols.map(function (_c, k) { return k; }).slice(1), 'Entered and actual');
        var flagged = 0;
        Array.prototype.slice.call(t.querySelectorAll('tbody tr')).forEach(function (tr, k) {
            var r = rows[k], tds = tr.children, tones = r.tones || {};
            var dot = el('span', 'ui-line-dot ui-line-' + (lines[r.channel] || 1));
            dot.setAttribute('aria-hidden', 'true');
            tds[0].insertBefore(dot, tds[0].firstChild);
            if (tones.length) { [1, 2].forEach(function (c) { tds[c].classList.add(CELL_TONE[tones.length]); }); flagged++; }
            if (tones.amplitude) { tds[3].classList.add(CELL_TONE[tones.amplitude]); flagged++; }
            // only the cell that breaks the port's limit is red, with the limit beside it
            [['max', 4, 1], ['min', oneLevel ? 4 : 5, -1]].forEach(function (x) {
                if (tones[x[0]] !== 'danger') return;
                tds[x[1]].classList.add(CELL_TONE.danger);
                if ((r.actual || {}).limit_v) tds[x[1]].appendChild(el('span', 'ui-text-quiet', ' limit ' + num(x[2] * r.actual.limit_v, 'V')));
            });
        });
        host.appendChild(t);
        var over = rows.filter(function (r) { var t = r.tones || {}; return t.max === 'danger' || t.min === 'danger'; }).length;
        if (sum) sum.textContent = over ? over + ' over the limit' : flagged ? flagged + ' differ' : 'all as entered';
        rows.forEach(function (r) { (r.why || []).forEach(function (w) { host.appendChild(el('p', 'ui-text-quiet', r.operation + ': ' + w)); }); });
    }
    var CHECK_WORDS = { load_config: 'Chip loads and builds its config', ops: 'Every played operation exists',
                        length: 'Pulse lengths', preview: 'Preview equals the config', timeline: 'Timeline',
                        range: 'Output range' };
    function allSkippedReason(p) {
        var cs = p.checks || [];
        if (!cs.length || !cs.every(function (c) { return c.status === 'skipped' && c.message === cs[0].message; })) return null;
        return cs[0].message;
    }
    var CHECK_STATE = { pass: 'pass', note: 'pass', fail: 'fail', skipped: 'skip' };
    function renderChecks(p) {
        var host = clear($('czg-checks'));
        var sum = $('czg-checks-sum');
        var U = UIkit();
        if (!p.ok) { (p.refusals || []).forEach(function (r) { host.appendChild(el('p', 'ui-text-quiet', r)); }); return; }
        if (allSkippedReason(p)) { if (sum) sum.textContent = ''; return; }
        var cs = p.checks || [];
        var list = cs.map(function (c) { return { name: CHECK_WORDS[c.name] || c.name, state: CHECK_STATE[c.status] || 'skip', why: c.message }; });
        var w = U.checks(list, 'Checks');
        Array.prototype.slice.call(w.querySelectorAll('.ui-result')).forEach(function (li, k) { li.setAttribute('data-check', cs[k].name); });
        list.forEach(function (c) {
            if (c.state !== 'skip') return;
            var r = el('p', 'ui-fail-row');
            r.appendChild(el('strong', 'ui-fail-name', c.name + ' not checked.'));
            r.appendChild(document.createTextNode(' ' + (c.why || '')));
            w.appendChild(r);
        });
        host.appendChild(w);
        var nf = list.filter(function (c) { return c.state === 'fail'; }).length;
        var np = list.filter(function (c) { return c.state === 'pass'; }).length;
        var ns = list.length - nf - np;
        if (sum) sum.textContent = nf ? nf + ' failed, ' + np + ' passed' : np + ' of ' + list.length + ' passed' + (ns ? ', ' + ns + ' not checked' : '');
        (p.previews_skipped || []).forEach(function (x) { host.appendChild(el('p', 'ui-text-quiet', 'No preview compared for ' + x)); });
    }
    function renderTiles(p) {
        var host = clear($('czg-tiles'));
        var ts = (p && p.tiles) || [];
        if (!ts.length) return;
        host.appendChild(UIkit().stats(ts.map(function (t) {
            var m = /^(-?[\d.]+)\s*(.*)$/.exec(t.value || '');
            var unit = (m ? m[2] : '') + (t.key === 'peak' || t.key === 'timing' ? (m && m[2] ? (t.key === 'timing' ? ', ' : ' ') : '') + t.note : '');
            return { label: t.label, value: m ? m[1] : t.value, unit: unit.trim() || null,
                     tone: t.tone === 'warn' || t.tone === 'danger' ? t.tone : null };
        }), 'Key numbers'));
    }
    function showSections(p) {
        var has = function (id) { var n = $(id); return !!(n && n.childNodes.length); };
        var e = $('czg-empty');
        if (e) e.hidden = !!p;
        [['czg-sec-chart', 'czg-chart'], ['czg-sec-intent', 'czg-intent'], ['czg-sec-checks', 'czg-checks']].forEach(function (x) {
            var sec = $(x[0]);
            if (sec) sec.hidden = !p || !has(x[1]);
        });
        // nothing was run: the banner says why once; no empty panels under it
        if (p && allSkippedReason(p)) {
            ['czg-sec-chart', 'czg-sec-intent'].forEach(function (id) { if ($(id)) $(id).hidden = true; });
        }
    }
    var PAIR_MARK = { ok: ['ok', 'ok'], fail: ['danger', 'cannot'], unchecked: ['warn', 'not checked'] };
    function pairMark(x) {
        if (!(x.verdict && x.verdict.allowed)) return 'fail';
        return x.verdict.state === 'verified' ? 'ok' : 'unchecked';
    }
    /* More than one pair: a switch at the top of the evidence column, each
       option with its own verdict mark; everything below follows the choice. */
    function renderPairSwitch() {
        var host = clear($('czg-pairs'));
        var pairs = (S.check && S.check.pairs) || [];
        if (!host) return;
        host.hidden = pairs.length < 2;
        if (pairs.length < 2) return;
        var U = UIkit();
        if (pairs.length <= 4) {
            var seg = U.segmented(pairs.map(function (x, k) { return { label: x.pair, selected: k === S.pairIdx }; }), 'Pair shown');
            Array.prototype.slice.call(seg.children).forEach(function (b, k) {
                var m = PAIR_MARK[pairMark(pairs[k])];
                b.setAttribute('data-czg-pair-idx', String(k));
                b.appendChild(document.createTextNode(' '));
                b.appendChild(U.badge(m[1], m[0]));
            });
            host.appendChild(seg);
        } else {
            var sel = U.select({ id: 'czg-pair-shown', label: 'Pair shown', value: String(S.pairIdx),
                options: pairs.map(function (x, k) { return { value: String(k), label: x.pair + ' (' + PAIR_MARK[pairMark(x)][1] + ')' }; }) });
            host.appendChild(sel);
        }
    }
    function renderPair() {
        var pairs = (S.check && S.check.pairs) || [];
        var p = pairs[S.pairIdx] || pairs[0];
        if (p) {
            renderChart(p.ok ? p.timeline : null);
            renderIntent(p.intent, p.timeline);
            renderChecks(p);
            renderTiles(p);
            renderWrites(pairs.length > 1 ? p : null);
        }
        showSections(p);
        if (p && !p.ok && pairs.length > 1) {
            var c = $('czg-chart');
            if (c) { clear(c).appendChild(el('p', 'ui-text-quiet', p.pair + ' cannot be planned: ' + (p.refusals || []).join(' '))); }
            var sec = $('czg-sec-chart');
            if (sec) sec.hidden = false;
        }
        renderPairSwitch();
    }
    function renderCheck() {
        var c = S.check;
        var ev = $('czg-evidence');
        if (ev) ev.classList.remove('czg-stale');
        var pairs = c.pairs || [];
        var firstBad = -1;
        pairs.forEach(function (x, k) { if (firstBad < 0 && pairMark(x) === 'fail') firstBad = k; });
        S.pairIdx = firstBad >= 0 ? firstBad : 0;
        renderPair();
        var ok = allowedPairs();
        var no = pairs.filter(function (x) { return !(x.verdict && x.verdict.allowed); });
        var reason = function (x) { return x.verdict.headline || x.verdict.reason; };
        var many = pairs.length > 1;
        if (!ok.length) {
            alertBox(banner('danger', many ? 'None of the ' + pairs.length + ' pairs can be created yet' : 'Cannot be created yet',
                no.map(function (x) { return (many ? x.pair + ': ' : '') + reason(x); }).join(' ') + ' Change the inputs and check again.'));
        } else if (no.length) {
            alertBox(banner('danger', no.length + ' of ' + pairs.length + ' cannot be created: ' + no.map(function (x) { return x.pair; }).join(', '),
                no.map(function (x) { return x.pair + ': ' + reason(x); }).join(' ') + ' The other ' + ok.length
                + (ok.length === 1 ? ' pair' : ' pairs') + ' can still be created.'));
        } else if (ok.some(function (x) { return x.verdict.state !== 'verified'; })) {
            var why = ok.filter(function (x) { return x.verdict.state !== 'verified'; })[0].verdict.reason;
            alertBox(banner('warn', 'Not checked with the lab\'s code', why + ' You can still create it; the record says it was not checked.'));
        } else {
            alertBox(banner('ok', many ? 'Ready to create on ' + ok.length + ' pairs' : 'Ready to create',
                'The lab\'s code built ' + (many ? 'these gates' : 'this gate') + ' on a copy of the chip and every check passed.'));
        }
        S.phase = ok.length ? 'create' : 'check';
        S.checkSig = c.plan_sig;
        var verified = ok.length && ok.every(function (x) { return x.verdict.state === 'verified'; });
        setBusy(false, !ok.length ? 'Checked: nothing can be created. Change the inputs.'
            : (verified ? 'Checked with the lab code. ' : 'Not checked with the lab code. ')
            + 'Press Create to add ' + (ok.length === 1 ? 'it' : 'the ' + ok.length + ' gates') + ' to the working copy.');
    }
    function check() {
        if (!S || S.busy) return Promise.resolve();
        var b = body();
        var mine = S.seq, g = S;
        setBusy(true, 'Checking with the lab\'s code, about 10 s');
        return post('/api/pulse/cz-gate/check', b).then(function (x) {
            if (S !== g) return;
            if (mine !== S.seq) { setBusy(false, 'Inputs changed, check again.'); return; }
            if (!x.r.ok || !x.j.ok) {
                setBusy(false, '');
                alertBox(callout('danger', 'The check did not run', (x.j.error || ('The app answered HTTP ' + x.r.status)) + ' Press Check to try again.'));
                return;
            }
            S.check = x.j;
            renderCheck();
        }).catch(function (e) {
            if (S !== g) return;
            setBusy(false, '');
            alertBox(callout('danger', 'The check did not run', 'The app did not answer (' + e + '). Press Check to try again.'));
        });
    }

    /* ---- create ---------------------------------------------------------- */
    function create() {
        if (!S || S.busy || S.phase !== 'create' || !S.check) return Promise.resolve();
        var g = S;
        setBusy(true, 'Creating…');
        return post('/api/pulse/cz-gate/create', { check_token: S.check.check_token, plan_sig: S.check.plan_sig })
            .then(function (x) {
                if (S !== g) return;
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
                if (S !== g) return;
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
        alertBox(banner('ok', 'Created ' + made.join(', '),
            'Not on the chip yet: press Apply to write it. The gate is not active; the pair\'s cz stays as it was.'
            + (skip.length ? ' Left out: ' + skip.join(' ') : '')));
        // move the focus off the form before hiding it: a focused element that
        // disappears leaves the dialog without focus (Chrome then paints a stray mark)
        var pb = $('czg-primary');
        if (pb) { try { pb.focus(); } catch (e) { /* not focusable */ } }
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
        var g = S;
        S.opener = opts.opener || null;
        var url = '/pulse/cz-gate/dialog' + (opts.pair ? '?pair=' + encodeURIComponent(opts.pair) : '');
        return fetch(url).then(function (r) { return r.text().then(function (t) { return { r: r, t: t }; }); })
            .then(function (x) {
                if (S !== g) return;
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
                var name = $('czg-name');
                if (name) { try { name.focus(); } catch (e) { /* not focusable */ } }
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
        if (t.id === 'czg-dialog' && !S.busy) { close(); return; }       // the backdrop
        if (t.closest('[data-czg-close]')) { close(); return; }
        if (t.closest('#czg-open-pair')) {
            var p = $('czg-pair') ? $('czg-pair').value : '';
            close();
            if (p && window.htmx) window.htmx.ajax('GET', '/pair/' + encodeURIComponent(p), { target: '#inspector-pane', swap: 'innerHTML' });
            return;
        }
        var seg = t.closest('[data-czg-pair-idx]');
        if (seg) { S.pairIdx = +seg.getAttribute('data-czg-pair-idx'); renderPair(); return; }
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
        if (t.id === 'czg-pair-shown') { S.pairIdx = +t.value; renderPair(); return; }
        if (t.id === 'czg-pair') { fillPairLine(); fillFrom(); fillModel(); fillMore(); }
        else if (t.hasAttribute('data-czg-slot')) {
            var f = t.getAttribute('data-czg-slot');
            S.cls[f] = t.value;
            S.entered[f] = {};
            S.raw[f] = {};
        } else if (!t.hasAttribute('data-czg-more') && t.id !== 'czg-from') { return; }
        if (t.hasAttribute('data-czg-slot')) fillSlots();
        inputsChanged();
    });
    document.addEventListener('ui-menu-pick', function (ev) {
        var t = ev.target;
        if (!t || !inDialog(t) || !S) return;
        var v = ev.detail && ev.detail.value;
        if (t.querySelector('#czg-class')) {
            if (v === S.klass) return;
            S.klass = v; S.entered = {}; S.raw = {}; S.cls = {};
            fillModel();
        } else if (t.querySelector('#czg-model')) {
            if (v === S.model) return;
            S.model = v; S.entered = {}; S.raw = {};
        } else { return; }
        fillSlots();
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
        if (r.ok) { S.entered[f][n] = r.v; t.removeAttribute('aria-invalid'); }
        else { delete S.entered[f][n]; t.setAttribute('aria-invalid', 'true'); say(r.why); }
        inputsChanged();
    });
    document.addEventListener('cancel', function (ev) {
        if (!ev.target || ev.target.id !== 'czg-dialog') return;
        ev.preventDefault();
        if (S && !S.busy) close();
    }, true);
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
