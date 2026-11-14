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
    if (window.CzGate) return;      // loaded twice (page bundle + navigation): one set of listeners
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
                 opener: null, seq: 0, created: null, pairIdx: 0, invalid: {}, creating: false,
                 confirmClose: false, downOnBackdrop: false };
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
                if (f === '@settings') return;
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
            macro_fields: Object.assign({}, S.entered['@settings'] || {}),
            model_gate: model ? model.split('/') : null
        };
    }

    /* ---- buttons and status ---------------------------------------------- */
    function say(t) { var s = $('czg-status'); if (s) s.textContent = t || ''; }
    function sentence(t) {
        t = String(t || '').trim();
        return !t || /[.!?\u201d"]$/.test(t) ? t : t + '.';
    }
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
            var nun = ok.filter(function (p) { return p.verdict.state !== 'verified'; }).length;
            b.textContent = ok.length === 1 ? (nun ? 'Create without the lab check' : 'Create gate')
                : 'Create ' + ok.length + ' gates' + (nun ? ', ' + nun + ' without the lab check' : '');
            b.disabled = S.busy || !ok.length || hasInvalid();
            return;
        }
        b.textContent = 'Check';
        var fp = firstPlan();
        b.disabled = S.busy || !fp || !fp.ok || hasInvalid();
    }
    function fieldKey(field, name) { return field + '\u0000' + name; }
    function hasInvalid() { return Object.keys(S.invalid || {}).length > 0; }
    /* A2: never build a selector from a name: find by comparing dataset values */
    function findField(field, name) {
        var all = document.querySelectorAll('#czg-dialog input[data-czg-field]');
        for (var k = 0; k < all.length; k++) {
            if (all[k].getAttribute('data-czg-field') === field && all[k].getAttribute('data-czg-name') === name) return all[k];
        }
        return null;
    }
    /* The field's kit error line, tied to its input (aria-describedby). */
    function showFieldError(input, why) {
        var box = input.closest('.ui-field');
        if (!box) return;
        var eid = input.id + '-error';
        var line = null;
        Array.prototype.slice.call(box.querySelectorAll('.ui-field-error')).forEach(function (n) { if (n.id === eid) line = n; });
        if (why) {
            if (!line) { line = el('p', 'ui-field-error'); line.id = eid; line.setAttribute('role', 'alert'); box.appendChild(line); }
            line.textContent = why;
            input.setAttribute('aria-invalid', 'true');
            var d = (input.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
            if (d.indexOf(eid) < 0) d.push(eid);
            input.setAttribute('aria-describedby', d.join(' '));
        } else {
            if (line) line.parentNode.removeChild(line);
            input.removeAttribute('aria-invalid');
            var d2 = (input.getAttribute('aria-describedby') || '').split(/\s+/).filter(function (x) { return x && x !== eid; });
            if (d2.length) input.setAttribute('aria-describedby', d2.join(' ')); else input.removeAttribute('aria-describedby');
        }
    }
    function setBusy(on, text) {
        S.busy = on;
        var d = $('czg-dialog');
        if (d) d.setAttribute('aria-busy', on ? 'true' : 'false');
        var form = $('czg-form');
        if (form) { form.inert = !!on; if (on) form.setAttribute('aria-disabled', 'true'); else form.removeAttribute('aria-disabled'); }
        var x = d && d.querySelector('.ui-dialog-head [data-czg-close]');
        if (x) {
            x.disabled = !!(on && S.creating);
            x.title = x.disabled ? 'A gate is being created: wait for the answer.' : 'Close';
        }
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
        var c = klass();
        // a copied layout starts from the model gate's values, never from class defaults
        var o = el('option', null, c && c.layout === 'copied' ? 'The model gate\'s values' : 'Defaults of the pulse class');
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
            var l = UIkit().check(p.id);
            var i = l.querySelector('input');
            i.value = p.id;
            i.setAttribute('data-czg-more', '');
            row.appendChild(l);
            row.appendChild(el('span', 'ui-text-quiet czg-more-why'));
            box.appendChild(row);
        });
    }
    var SOURCE_WORDS = { given: 'entered', copied: 'copied', 'default': 'default',
                         'copied from the model gate': 'copied', 'copied from the model gate, renamed': 'copied',
                         'kept reference': 'kept' };
    function sourceLong(planned, field, name) {
        var f = planned && planned.fields && planned.fields[field];
        return (f && f[name]) || '';
    }
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
    function fieldInput(field, name, label, unit, kind, value, src, opts) {
        opts = opts || {};
        var U = UIkit();
        var num = kind === 'float' || kind === 'int';
        var id = ('czg-f-' + field + '-' + name).replace(/[^A-Za-z0-9_-]/g, '_');
        var typed = (S.raw[field] || {})[name];
        var f = U.field({ label: label, inline: !!opts.inline, source: src ? { text: src, tone: SOURCE_TONE[src] || 'neutral' } : null,
                          control: { part: 'input', id: id, num: num, unit: unit || null, block: true,
                                     value: typed !== undefined ? typed : shown(value) } });
        var i = f.querySelector('input');
        i.spellcheck = false;
        if (num) i.setAttribute('inputmode', 'decimal');
        i.setAttribute('data-czg-field', field);
        i.setAttribute('data-czg-name', name);
        i.setAttribute('data-czg-kind', kind || '');
        i.value = typed !== undefined ? typed : shown(value);
        var bad = (S.invalid || {})[fieldKey(field, name)];
        if (bad) setTimeout(function () { if (i.isConnected) showFieldError(i, bad); }, 0);
        if (opts.hint) f.appendChild(el('p', 'ui-field-hint', opts.hint));
        var bdg = f.querySelector('.ui-field-head .ui-badge');
        if (bdg && opts.title) { bdg.title = opts.title; bdg.setAttribute('aria-description', opts.title); }
        return f;
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
            var specs = (fp.field_specs || {})[f] || {};
            S.cls[f] = pulse.__class__ || null;
            var cls = String(pulse.__class__ || '?').split('.').pop();
            var b = slotBlock(f, slotChannel(f, null, fp), 'Class ' + cls + ', from the model gate.');
            var main = el('div', 'ui-field-stack');
            var opt = [];
            Object.keys(specs).forEach(function (k) {
                var sp = specs[k];
                var long = sourceLong(fp, f, k);
                var node = fieldInput(f, k, sp.label || k, sp.unit, sp.kind, pulse[k],
                                      SOURCE_WORDS[long] || long || 'default', { inline: true, title: long });
                if (sp.optional && !((S.entered[f] || {}).hasOwnProperty(k))) opt.push(node); else main.appendChild(node);
            });
            b.appendChild(main);
            if (opt.length) {
                var d = UIkit().disclosure('Optional fields', opt.length, [], false);
                var db = d.querySelector('.ui-disclosure-body');
                opt.forEach(function (n) { db.appendChild(n); });
                b.appendChild(d);
            }
            host.appendChild(b.parentNode);
        });
    }
    /* The gate's own settings of a copied layout (server list: key, value,
       kind, source); references are listed read-only. */
    function fillSettings() {
        var fp = firstPlan();
        var host = clear($('czg-settings'));
        var panel = $('czg-settings-panel');
        var list = (fp && fp.ok && fp.settings) || [];
        if (panel) panel.hidden = !list.length;
        var step = $('czg-more-step');
        if (step) step.textContent = list.length ? '4' : '3';
        if (!list.length || !host) return;
        var sum = $('czg-settings-sum');
        var given = list.filter(function (x) { return x.source === 'given'; }).length;
        if (sum) sum.textContent = given ? given + ' entered' : 'as the model gate';
        var opt = [];
        list.forEach(function (x) {
            if (x.optional && !((S.entered['@settings'] || {}).hasOwnProperty(x.key))) { opt.push(x); return; }
            if (x.reference) {
                var r = el('div', 'ui-field ui-field--inline');
                var h = el('div', 'ui-field-head');
                h.appendChild(el('span', 'ui-field-label', x.key));
                h.appendChild(UIkit().badge('kept reference', 'neutral'));
                r.appendChild(h);
                r.appendChild(el('code', 'ui-chip', String(x.value)));
                host.appendChild(r);
                return;
            }
            var n = fieldInput('@settings', x.key, x.key, '', x.kind, x.value,
                               SOURCE_WORDS[x.source] || x.source, { inline: true, title: x.source });
            var i = n.querySelector('input');
            i.setAttribute('data-czg-setting', x.key);
            host.appendChild(n);
        });
        if (opt.length) {
            var d = UIkit().disclosure('Optional settings', opt.length, [], false);
            var db = d.querySelector('.ui-disclosure-body');
            opt.forEach(function (x) {
                var n = fieldInput('@settings', x.key, x.key, '', x.kind, x.value,
                                   SOURCE_WORDS[x.source] || x.source, { inline: true, title: x.source });
                n.querySelector('input').setAttribute('data-czg-setting', x.key);
                db.appendChild(n);
            });
            host.appendChild(d);
        }
    }
    function fillModelFacts() {
        var fp = firstPlan();
        var panel = $('czg-model-panel');
        var host = clear($('czg-model-facts'));
        var show = !!(fp && fp.ok && fp.layout === 'copied' && fp.model_gate) && !S.check;
        if (panel) panel.hidden = !show;
        if (!show) return;
        var U = UIkit();
        host.appendChild(U.table(null, [
            ['Model gate', { chip: fp.model_gate[1] }],
            ['On the pair', { chip: fp.model_gate[0] }],
            ['Not inherited', (fp.reset || []).join(', ') || 'nothing']], true, [], 'From the model gate'));
        host.appendChild(el('p', 'ui-text-quiet', 'The new gate copies the model gate\'s structure; the lab\'s code must run it before it is written.'));
    }
    var NUMBER_RE = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;
    function parse(input) {
        var kind = input.getAttribute('data-czg-kind');
        var raw = input.value.trim();
        if (kind === 'float' || kind === 'int') {
            if (/^[+-]?\d+,\d+$/.test(raw)) return { ok: false, why: 'Use a dot for decimals.' };
            if (!NUMBER_RE.test(raw)) return { ok: false, why: 'Type a number.' };
            var n = Number(raw);
            if (!isFinite(n)) return { ok: false, why: 'Type a number.' };
            if (kind === 'int' && !Number.isInteger(n)) return { ok: false, why: 'Type a whole number.' };
            return { ok: true, v: n === 0 ? 0 : n };
        }
        if (kind === 'bool') {
            if (/^(true|yes|1)$/i.test(raw)) return { ok: true, v: true };
            if (/^(false|no|0)$/i.test(raw)) return { ok: true, v: false };
            return { ok: false, why: 'Type true or false.' };
        }
        if (raw === '' || raw === 'null') return { ok: true, v: null };
        if (kind === 'any' && NUMBER_RE.test(raw) && isFinite(Number(raw))) return { ok: true, v: Number(raw) === 0 ? 0 : Number(raw) };
        return { ok: true, v: raw };
    }

    /* ---- the plan -------------------------------------------------------- */
    function inputsChanged() {
        S.seq++;
        if (S.check) {
            var ev = $('czg-evidence');
            if (ev) ev.classList.add('czg-stale');
            alertBox(banner('note', 'Inputs changed: press Check again.', 'The result below is for the earlier inputs.'));
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
            if (S !== g || mine !== S.seq || S.phase === 'done' || S.busy) return;
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
        fillSettings();
        fillModelFacts();
        if (keep && keep[0]) {
            var again = findField(keep[0], keep[1]);
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
    function syncLine(sync) {
        if (sync && sync.auto_push) return 'In the working copy; Auto-Sync also writes the chip.';
        if (sync && sync.auto_pull) return 'In the working copy only (Auto-Sync pulls only): not on the chip until you press Apply.';
        return 'In the working copy. Not on the chip until you press Apply.';
    }
    /* What Create will write: every pair that will be written, grouped; a
       refused pair is named as not written with its reason. */
    function renderWrites() {
        var host = clear($('czg-writes'));
        var sum = $('czg-writes-sum');
        var U = UIkit();
        var rows = [];
        if (S.check && S.check.pairs) {
            S.check.pairs.forEach(function (p) {
                var v = p.verdict || {};
                rows.push({ pair: p.pair, ok: !!v.allowed, paths: p.paths || [], why: v.allowed ? '' : (v.reason || 'no verdict from the server') });
            });
        } else {
            ((S.plan && S.plan.pairs) || []).forEach(function (p) {
                rows.push({ pair: p.pair, ok: !!p.ok, paths: p.paths || [], why: p.ok ? '' : (p.refusals || []).join(' ') });
            });
        }
        var n = rows.reduce(function (a, r) { return a + (r.ok ? r.paths.length : 0); }, 0);
        if (sum) sum.textContent = n ? n + (n === 1 ? ' object' : ' objects') : 'nothing yet';
        if (!n) { host.appendChild(el('p', 'ui-text-quiet', 'Nothing will be written yet.')); }
        else host.appendChild(el('p', 'ui-text-quiet', syncLine((S.check && S.check.sync) || S.data.sync)));
        rows.forEach(function (r) {
            if (rows.length > 1) host.appendChild(el('p', 'ui-text-strong', r.pair));
            if (!r.ok) { host.appendChild(el('p', 'ui-text-quiet', 'not written: ' + r.why)); return; }
            r.paths.forEach(function (x) { host.appendChild(U.path(x)); });
        });
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
    var CELL_TONE = { warn: 'ui-cell--warn', danger: 'ui-cell--danger' };
    function renderIntent(rows, tl) {
        var host = clear($('czg-intent'));
        var sum = $('czg-intent-sum');
        if (!rows || !rows.length) { if (sum) sum.textContent = ''; host.appendChild(el('p', 'ui-text-quiet', 'No pulse to compare.')); return; }
        var U = UIkit();
        var lines = lineOfChannel(tl);
        if (rows.every(function (r) { return !r.actual; })) {
            if (sum) sum.textContent = 'not compared';
            host.appendChild(el('p', 'ui-text-quiet', (rows[0].why || [])[0] || 'Nothing was compared.'));
            return;
        }
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
                if ((r.actual || {}).limit_v) {
                    var off = +(r.actual.offset_v || 0);
                    tds[x[1]].appendChild(el('span', 'ui-cell-note', off
                        ? 'port range ' + num(r.actual.limit_v, 'V') + ' with a ' + num(off, 'V') + ' offset'
                        : 'limit ' + num(x[2] * r.actual.limit_v, 'V')));
                }
            });
        });
        host.appendChild(t);
        var over = rows.filter(function (r) { var t = r.tones || {}; return t.max === 'danger' || t.min === 'danger'; }).length;
        if (sum) sum.textContent = over ? over + ' over the limit' : flagged ? flagged + ' differ' : 'all as entered';
        rows.forEach(function (r) { (r.why || []).forEach(function (w) { host.appendChild(el('p', 'ui-text-quiet', r.operation + ': ' + w)); }); });
    }
    var CHECK_WORDS = { load_config: 'Chip loads and builds its config', ops: 'The gate runs with the lab\'s code',
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
        // skipped checks are not failures: one quiet line per reason, naming them
        var byWhy = {}, order = [];
        list.forEach(function (c) {
            if (c.state !== 'skip') return;
            var why = c.why || '';
            if (!byWhy[why]) { byWhy[why] = []; order.push(why); }
            byWhy[why].push(c.name);
        });
        order.forEach(function (why) {
            var k = byWhy[why].length;
            var head = k + (k === 1 ? ' check was' : ' checks were') + ' not run';
            var line = /does not run\.?$/.test(why) ? head + ' because the gate does not run.'
                : head + ' (' + byWhy[why].join(', ') + '): ' + why.replace(/^Not checked:\s*/, '');
            var q = el('p', 'ui-text-quiet', line);
            q.title = byWhy[why].join(', ');
            w.appendChild(q);
        });
        host.appendChild(w);
        var nf = list.filter(function (c) { return c.state === 'fail'; }).length;
        var np = list.filter(function (c) { return c.state === 'pass'; }).length;
        var ns = list.length - nf - np;
        if (sum) sum.textContent = nf ? nf + ' failed, ' + np + ' passed' : np + ' of ' + list.length + ' passed' + (ns ? ', ' + ns + ' not checked' : '');
        var ranNot = cs.some(function (c) { return c.name === 'ops' && c.status === 'fail'; });
        var pv = p.previews_skipped || [];
        if (pv.length && !ranNot) {
            var q = el('p', 'ui-text-quiet', 'No preview compared for ' + pv.length + (pv.length === 1 ? ' pulse' : ' pulses') + ': SM cannot draw its class itself.');
            q.title = pv.join('\n');
            host.appendChild(q);
        }
    }
    function renderTiles(p) {
        var host = clear($('czg-tiles'));
        var ts = (p && p.tiles) || [];
        if (!ts.length) return;
        // the server gives value, unit and one quiet line; nothing is split here
        var g = UIkit().stats(ts.map(function (t) {
            return { label: t.label, value: t.value, unit: t.unit || null,
                     tone: t.tone === 'warn' || t.tone === 'danger' ? t.tone : null };
        }), 'Key numbers');
        Array.prototype.slice.call(g.querySelectorAll('.ui-stat')).forEach(function (d, k) {
            d.setAttribute('data-tile', ts[k].key);
            if (ts[k].note) d.appendChild(el('p', 'ui-text-quiet', ts[k].note));
        });
        host.appendChild(g);
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
        if (!x.verdict) x.verdict = { allowed: false, state: 'not checked', reason: 'no verdict from the server' };
        if (!(x.verdict && x.verdict.allowed)) return 'fail';
        return x.verdict.state === 'verified' ? 'ok' : 'unchecked';
    }
    /* More than one pair: a switch at the top of the evidence column, each
       option with its own verdict mark; everything below follows the choice. */
    function renderPairSwitch() {
        var hostEl = $('czg-pairs');
        var hadFocus = !!(hostEl && hostEl.contains(document.activeElement));
        var host = clear(hostEl);
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
            if (hadFocus) { var on = seg.children[S.pairIdx]; if (on) try { on.focus(); } catch (e) { /* gone */ } }
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
            renderWrites();
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
    function checkCounts(list) {
        var passed = 0, skipped = 0;
        list.forEach(function (p) { (p.checks || []).forEach(function (c) {
            if (c.status === 'skipped') skipped++; else if (c.status === 'pass' || c.status === 'note') passed++; }); });
        return { passed: passed, skipped: skipped };
    }
    function renderCheck() {
        var c = S.check;
        var ev = $('czg-evidence');
        if (ev) ev.classList.remove('czg-stale');
        var pairs = c.pairs || [];
        pairs.forEach(function (x) { pairMark(x); });       // every pair carries a verdict from here on
        if ($('czg-model-panel')) $('czg-model-panel').hidden = true;
        var firstBad = -1;
        pairs.forEach(function (x, k) { if (firstBad < 0 && pairMark(x) === 'fail') firstBad = k; });
        S.pairIdx = firstBad >= 0 ? firstBad : 0;
        renderPair();
        var ok = allowedPairs();
        var no = pairs.filter(function (x) { return !(x.verdict && x.verdict.allowed); });
        var reason = function (x) { var v = x.verdict || {}; return sentence(v.headline || v.reason || 'no verdict from the server'); };
        var many = pairs.length > 1;
        if (!ok.length) {
            alertBox(banner('danger', many ? 'None of the ' + pairs.length + ' pairs can be created yet' : 'Cannot be created yet',
                no.map(function (x) { return (many ? x.pair + ': ' : '') + reason(x); }).join(' ') + ' Change the inputs and check again.'));
        } else if (no.length) {
            alertBox(banner('danger', no.length + ' of ' + pairs.length + ' cannot be created: ' + no.map(function (x) { return x.pair; }).join(', '),
                no.map(function (x) { return x.pair + ': ' + reason(x); }).join(' ') + ' The other ' + ok.length
                + (ok.length === 1 ? ' pair' : ' pairs') + ' can still be created.'));
        } else if (ok.some(function (x) { return x.verdict.state !== 'verified'; })) {
            var unv = ok.filter(function (x) { return x.verdict.state !== 'verified'; });
            var named = unv.slice(0, 3).map(function (x) { return (many ? x.pair + ': ' : '') + sentence(x.verdict.reason); }).join(' ')
                + (unv.length > 3 ? ' And ' + (unv.length - 3) + ' more.' : '');
            alertBox(banner('warn', many ? unv.length + ' of ' + ok.length + ' not checked with the lab\'s code' : 'Not checked with the lab\'s code',
                named + ' You can still create ' + (unv.length === 1 && !many ? 'it' : 'them') + '; the record says it was not checked.'));
        } else {
            var counts = checkCounts(ok);
            alertBox(banner('ok', many ? 'Ready to create on ' + ok.length + ' pairs' : 'Ready to create',
                'The lab\'s code built ' + (many ? 'these gates' : 'this gate') + ' on a copy of the chip and '
                + (counts.skipped ? counts.passed + ' checks passed, ' + counts.skipped + ' were not run.' : 'every check passed.')));
        }
        S.phase = ok.length ? 'create' : 'check';
        S.checkSig = c.plan_sig;
        var verified = ok.length && ok.every(function (x) { return x.verdict.state === 'verified'; });
        setBusy(false, !ok.length ? 'Checked: nothing can be created. Change the inputs.'
            : (verified ? 'Checked with the lab code. ' : 'Not checked with the lab code. ')
            + 'Press Create to add ' + (ok.length === 1 ? 'it' : 'the ' + ok.length + ' gates') + ' to the working copy.'
            + (ok.length && (c.sync || {}).auto_push ? ' ' + AUTO_NOTE : ''));
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
        S.creating = true;
        setBusy(true, 'Creating\u2026');
        return post('/api/pulse/cz-gate/create', { check_token: S.check.check_token, plan_sig: S.check.plan_sig })
            .then(function (x) {
                g.creating = false;
                var j = x.j;
                if (S !== g) {
                    // the page moved on: the app still reports what happened
                    if (j.tray_html && window._swapPendingTray) { try { window._swapPendingTray(j.tray_html); } catch (e) { /* poll catches up */ } }
                    if (window.showToast) window.showToast(j.ok ? 'Created ' + ((j.created || []).map(function (c) { return c.gate + ' on ' + c.pair; }).join(', '))
                        : 'The gate was not created: ' + (j.error || ('HTTP ' + x.r.status)), j.ok ? 'success' : 'error');
                    return;
                }
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
    /* After a create the dialog says what the app's own sync state says (the
       server's sync_now, the same source as the top bar's control). */
    var AUTO_NOTE = 'Auto-Sync is on: Create also writes the chip.';
    var PULL_NOTE = 'Auto-Sync is set to pull only, so it does not write this.';
    var SYNC_WORDS = {
        working: 'In the working copy only: press Apply to write it to the chip.',
        writing: 'Auto-Sync is on: writing it to the chip.',
        written: 'Written to the chip by Auto-Sync.',
        unknown: 'Auto-Sync is on: the top bar shows when it reaches the chip.'
    };
    /* The words for a sync state read AFTER the create's entries were counted
       (the create response) or later (the drift poll that starts from a
       'writing' state, i.e. after the entries were seen unapplied). "written"
       means synced with nothing unapplied in such a state. */
    function syncWord(sync) {
        if (!sync || sync.readable === false) return 'unknown';
        if (!sync.auto_push) return 'working';
        if ((sync.unapplied || 0) > 0) return 'writing';
        // only ever asked with a state read after the create's entries were counted:
        // the create response, or a poll that started from a 'writing' state
        if (sync.state === 'synced') return 'written';
        return 'unknown';
    }
    function createdWhy(word, skip) {
        return SYNC_WORDS[word] + ' The gate is not active; the pair\'s cz stays as it was.'
            + (skip.length ? ' Left out: ' + skip.join(' ') : '');
    }
    function showSync(word, skip) {
        S.syncWord = word;
        var w = $('czg-alert') && $('czg-alert').querySelector('.ui-verdict-why');
        if (w) w.textContent = createdWhy(word, skip);
        say(SYNC_WORDS[word]);
        // the status rows say on the chip or working copy only: read them again once written
        if (word === 'written' && S.created) loadStatusRows(S, S.created.created || []);
    }
    /* Follow the app's own sync state (the top bar's /state/drift) while
       Auto-Sync pushes: "writing" until it says synced with nothing unapplied. */
    function watchSync(g, skip, auto, seenPending, tries) {
        if (S !== g || tries <= 0) return;
        setTimeout(function () {
            if (S !== g) return;
            fetch('/state/drift').then(function (r) { return r.json(); }).then(function (d) {
                if (S !== g) return;
                var sv = d && d.sync;
                if (!sv) { showSync('unknown', skip); return; }
                var sync = { auto_push: auto.auto_push, state: sv.state, unapplied: sv.unapplied };
                var pending = seenPending || (sv.unapplied || 0) > 0;
                var word = syncWord(sync);
                showSync(word, skip);
                if (word === 'writing') watchSync(g, skip, auto, pending, tries - 1);
            }).catch(function () { if (S === g) showSync('unknown', skip); });
        }, 1500);
    }
    /* After Create: the status slice's own row for every created gate, the same
       partial the pair view shows (GET /pulse/cz-gate/status); no rendering here. */
    function loadStatusRows(g, created) {
        var slot = $('czg-status-slot');
        if (!slot) return;
        slot.textContent = '';
        created.forEach(function (c) {
            var holder = el('div');
            holder.setAttribute('data-czg-status-for', c.pair + '/' + c.gate);
            slot.appendChild(holder);
            fetch('/pulse/cz-gate/status?pair=' + encodeURIComponent(c.pair) + '&gate=' + encodeURIComponent(c.gate))
                .then(function (r) { return r.text(); })
                .then(function (html) {
                    if (S !== g) return;
                    holder.innerHTML = html;
                    if (window.htmx && window.htmx.process) { try { window.htmx.process(holder); } catch (e) { /* static row */ } }
                }).catch(function () {
                    if (S !== g) return;
                    holder.appendChild(el('p', 'ui-text-quiet', 'The status of ' + c.gate + ' on ' + c.pair + ' could not be read; open the pair to see it.'));
                });
        });
    }
    function done(j) {
        clearTimeout(S.timer);
        S.seq++;
        S.phase = 'done';
        S.created = j;
        S.busy = false;
        var made = (j.created || []).map(function (c) { return c.gate + ' on ' + c.pair + (c.state !== 'verified' ? ' (not checked)' : ''); });
        var skip = (j.skipped || []).map(function (s) { return s.pair + ': ' + s.reason; });
        var sync = j.sync;
        var word = syncWord(sync);
        S.syncWord = word;
        alertBox(banner('ok', 'Created ' + made.join(', '), createdWhy(word, skip)));
        if (sync && sync.readable !== false && !sync.auto_push && sync.auto_pull) {
            $('czg-alert').appendChild(el('p', 'ui-text-quiet', PULL_NOTE));
        }
        // move the focus off the form before hiding it: a focused element that
        // disappears leaves the dialog without focus (Chrome then paints a stray mark)
        var pb = $('czg-primary');
        if (pb) { pb.disabled = false; try { pb.focus(); } catch (e) { /* not focusable */ } }
        var ae = document.activeElement;
        if (ae && $('czg-form') && $('czg-form').contains(ae) && ae.blur) ae.blur();
        var f = $('czg-form');
        if (f) f.hidden = true;
        var ev = $('czg-evidence');
        if (ev) ev.classList.add('czg-stale');
        var open = $('czg-open-pair');
        if (open) open.hidden = false;
        var cancel = $('czg-cancel');
        if (cancel) cancel.hidden = true;
        say(SYNC_WORDS[word]);
        refresh();
        if (word === 'writing') watchSync(S, skip, sync, true, 40);
        loadStatusRows(S, j.created || []);
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
    function close(force) {
        if (S && S.creating && !force) return false;       // a create in flight: its answer must land
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
    /* Escape or backdrop on a dialog with edits or a check asks once (kit callout
       with two kit buttons inside the dialog; a page-level confirm cannot sit
       above a modal dialog). */
    function worked() {
        return !!(S && (S.check || Object.keys(S.entered || {}).some(function (k) { return Object.keys(S.entered[k] || {}).length; })
                        || ($('czg-name') && $('czg-name').value.trim() !== 'cz_')));
    }
    function softClose() {
        if (!S || S.phase === 'done' || !worked()) { close(); return; }
        if (S.confirmClose) { close(); return; }
        S.confirmClose = true;
        var top = clear($('czg-top'));
        var U = UIkit();
        top.appendChild(U.callout('warn', 'Discard this gate?', 'Press Escape again or Discard to close; Keep editing to stay.'));
        var acts = U.actions([{ label: 'Keep editing', cls: 'btn-sync outline' }, { label: 'Discard', cls: 'btn-sync danger' }], 'Discard this gate?');
        acts.children[0].setAttribute('data-czg-keep', '');
        acts.children[1].setAttribute('data-czg-discard', '');
        top.appendChild(acts);
        try { acts.children[0].focus(); } catch (e) { /* not focusable */ }
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
            }).catch(function (e) {
                if (S !== g) return;
                S = null;
                clear(host());
                if (window.showToast) window.showToast('The gate dialog could not open: ' + e, 'error');
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
        if (t.id === 'czg-dialog') {                                   // the backdrop: down AND up on it
            var both = S.downOnBackdrop;
            S.downOnBackdrop = false;
            if (both && !S.busy) softClose();
            return;
        }
        if (t.closest('[data-czg-discard]')) { close(); return; }
        if (t.closest('[data-czg-keep]')) { S.confirmClose = false; clear($('czg-top')); return; }
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
            fillFrom();
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
        var key = fieldKey(f, n);
        if (r.ok) {
            S.entered[f][n] = r.v;
            if (S.invalid[key]) { delete S.invalid[key]; say(''); }
            showFieldError(t, null);
        } else {
            delete S.entered[f][n];
            S.invalid[key] = r.why;
            showFieldError(t, r.why);
            say(r.why);
        }
        inputsChanged();
    });
    document.addEventListener('cancel', function (ev) {
        if (!ev.target || ev.target.id !== 'czg-dialog') return;
        ev.preventDefault();
        if (S && !S.busy && !ev.defaultPrevented) softClose();
    }, true);
    document.addEventListener('mousedown', function (ev) {
        if (!S) return;
        S.downOnBackdrop = !!(ev.target && ev.target.id === 'czg-dialog');
    }, true);
    document.addEventListener('keydown', function (ev) {
        if (ev.key !== 'Escape' || !S || ev.defaultPrevented) return;      // a kit menu took this Escape
        var d = $('czg-dialog');
        if (!d || !d.hasAttribute('open')) return;
        ev.preventDefault();
        if (!S.busy) softClose();
    });

    window.CzGate = { open: open, close: close, state: function () { return S; },
                      _check: check, _create: create, _plan: plan };
})();
