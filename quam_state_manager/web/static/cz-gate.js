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
                 confirmClose: false, downOnBackdrop: false, spectOn: {}, spectNote: '' };
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
                if (f === '@settings' || isSpectField(f)) return;
                pulses[f] = { 'class': S.cls[f] || null, fields: Object.assign({}, S.entered[f]) };
            });
        }
        var model = !$('czg-model-field').hidden ? (S.model || '') : '';
        var pairs = selectedPairs();
        var out = {
            pairs: pairs,
            gate_name: $('czg-name').value.trim(),
            gate_class: c ? c['class'] : '',
            pulses: pulses,
            copy_from: $('czg-from').value || null,
            macro_fields: Object.assign({}, S.entered['@settings'] || {}),
            model_gate: model ? model.split('/') : null
        };
        var sp = spectBody(pairs);
        if (sp) out.spectators = sp;
        return out;
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
    /* kind names what the banner says; a plan answer clears only its own error */
    function alertBox(node, kind) {
        var a = clear($('czg-alert'));
        if (S) S.alertKind = node ? (kind || 'other') : null;
        if (a && node) a.appendChild(node);
    }
    function firstPlan() { return S.plan && S.plan.pairs && S.plan.pairs[0]; }
    function allowedPairs() {
        return ((S.check && S.check.pairs) || []).filter(function (p) { return p.verdict && p.verdict.allowed; });
    }
    function refresh() {
        simButton();
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
    /* A field keeps the group (main or folded) it was first shown in, so it
       never moves while the person types in it; a new class or model resets it. */
    function placeOf(field, name, folded) {
        var k = fieldKey(field, name);
        S.placed = S.placed || {};
        if (!S.placed[k]) S.placed[k] = folded ? 'fold' : 'main';
        return S.placed[k];
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
        if (d) d.removeAttribute('aria-busy');      // Pico draws a spinner for aria-busy: the status line says it
        var form = $('czg-form');
        if (form) { form.inert = !!on; if (on) form.setAttribute('aria-disabled', 'true'); else form.removeAttribute('aria-disabled'); }
        applyBusyLook();
        var x = d && d.querySelector('.ui-dialog-head [data-czg-close]');
        if (x) {
            x.disabled = !!(on && S.creating);
            x.title = x.disabled ? 'A gate is being created: wait for the answer.' : 'Close';
        }
        say(text || '');
        refresh();
    }

    /* While busy every form control wears the kit's disabled state (again after a re-render). */
    function applyBusyLook() {
        var form = $('czg-form');
        if (!form || !S) return;
        Array.prototype.slice.call(form.querySelectorAll('input, select, textarea, button')).forEach(function (c) {
            if (S.busy) { if (!c.disabled) { c.disabled = true; c.setAttribute('data-czg-busy-off', ''); } }
            else if (c.hasAttribute('data-czg-busy-off')) { c.disabled = false; c.removeAttribute('data-czg-busy-off'); }
        });
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
    /* Why a class cannot run on the picked pair: the env's probe first, then
       the server's per-pair answer (data.fit); '' when it can run. */
    function classWhy(c, pair) {
        if (!c.available) return c.why || 'This class is not available.';
        var f = (S.data.fit || {})[pair];
        return (f && f[c['class']]) || '';
    }
    /* The selected class is the first one that can run on the pair; a class
       picked by hand stays while it can run there. Returns true when the
       selection changed. */
    function fillClasses() {
        var pair = $('czg-pair') ? $('czg-pair').value : '';
        var all = S.data.classes || [];
        var runs = all.filter(function (c) { return !classWhy(c, pair); });
        var before = S.klass || '';
        var keep = runs.filter(function (c) { return c['class'] === before; })[0];
        S.klass = keep ? before : runs[0] ? runs[0]['class'] : '';
        S.classNote = '';
        if (before && S.klass !== before && S.klassByHand) {
            var old = all.filter(function (c) { return c['class'] === before; })[0];
            S.classNote = 'The class changed to ' + (runs[0] ? runs[0].label : 'none') + ': '
                + (old ? old.label : before) + ' cannot run on ' + pair + '. ' + (old ? classWhy(old, pair) : '');
        }
        if (S.klass !== before) S.klassByHand = false;
        S.noClass = runs.length ? '' : (all.length ? 'No gate class can run on ' + (pair || 'this pair') + '. ' + classWhy(all[0], pair) : '');
        menuInto('czg-class-host', { id: 'czg-class', kind: 'pick', label: 'Gate class', block: true,
            value: S.klass, placeholder: 'Pick a gate class',
            items: all.map(function (c) {
                var why = classWhy(c, pair);
                return { value: c['class'], title: c.label, disabled: !!why, reason: why };
            }) });
        renderClassWhy([]);
        return S.klass !== before;
    }
    /* Reasons about the class go under Class; the server names them
       (class_refusals), the client never sorts by text. */
    function renderClassWhy(fromPlan) {
        var w = $('czg-class-why');
        if (!w) return;
        clear(w);
        var lines = S.noClass ? [S.noClass] : [S.classNote].concat(fromPlan || []);
        lines = lines.filter(function (x) { return x; });
        lines.forEach(function (r) { w.appendChild(el('span', 'czg-why-line', r)); });
        w.hidden = !lines.length;
        var t = $('czg-class');
        var bad = !!S.noClass || !!(fromPlan && fromPlan.length);
        if (t) { if (bad) t.setAttribute('aria-invalid', 'true'); else t.removeAttribute('aria-invalid'); }
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
            row.setAttribute('data-czg-more-row', '');
            var mw = el('span', 'ui-text-quiet');
            mw.setAttribute('data-czg-more-why', '');
            row.appendChild(mw);
            box.appendChild(row);
        });
    }
    /* ---- spectators: other qubits' flux lines that play while the gate plays.
       The default is none; a ticked line needs its own length and level (SM
       computes no level). The choices live per pair: S.spectOn[pair][qubit]. */
    var SPECT_PREFIX = '@sp:';
    var LEVEL_NEEDED = 'Type a level in V: SM computes no level for a spectator.';
    var LENGTH_NEEDED = 'Type a length in ns.';
    function spectField(pair, q) { return SPECT_PREFIX + pair + ':' + q; }
    function isSpectField(f) { return String(f || '').indexOf(SPECT_PREFIX) === 0; }
    /* What the class says about spectators; null when the data names nothing. */
    function spectOffer() {
        var c = klass();
        var s = c && c.spectators;
        return s && typeof s === 'object' ? s : null;
    }
    function selectedPairs() {
        var pair = $('czg-pair') ? $('czg-pair').value : '';
        return pair ? [pair].concat(extraPairs().filter(function (p) { return p !== pair; })) : [];
    }
    function candidates(pair) { return ((pairInfo(pair) || {}).spectator_candidates) || []; }
    function ticked(pair) {
        var on = (S.spectOn || {})[pair] || {};
        return candidates(pair).filter(function (c) { return on[c.qubit]; });
    }
    /* The request's spectators field, or null when none is ticked (the body
       then equals a request without it). */
    function spectBody(pairs) {
        var offer = spectOffer();
        if (!offer || !offer.offered) return null;
        var out = {}, any = false;
        pairs.forEach(function (p) {
            var list = ticked(p).map(function (c) {
                var e = S.entered[spectField(p, c.qubit)] || {};
                var row = { qubit: c.qubit };
                if (e.length !== undefined) row.length = e.length;
                if (e.level !== undefined) row.level = e.level;
                return row;
            });
            if (list.length) { out[p] = list; any = true; }
        });
        return any ? out : null;
    }
    /* The gate's own length: the longest planned pulse of the first pair. */
    function gateLength() {
        var fp = firstPlan();
        var best = null;
        Object.keys((fp && fp.ok && fp.pulses) || {}).forEach(function (f) {
            var v = (fp.pulses[f] || {}).length;
            if (typeof v === 'number' && isFinite(v) && (best === null || v > best)) best = v;
        });
        if (best === null) {
            Object.keys(S.entered || {}).forEach(function (f) {
                var v = isSpectField(f) ? null : (S.entered[f] || {}).length;
                if (typeof v === 'number' && (best === null || v > best)) best = v;
            });
        }
        return best;
    }
    function dropSpect(pairOnly) {
        [S.entered, S.raw, S.invalid, S.placed || {}].forEach(function (m) {
            Object.keys(m).forEach(function (k) {
                if (!isSpectField(k)) return;
                if (!pairOnly || k.indexOf(SPECT_PREFIX + pairOnly + ':') === 0) delete m[k];
            });
        });
    }
    function anyTicked() {
        return Object.keys(S.spectOn || {}).some(function (p) {
            return Object.keys(S.spectOn[p] || {}).some(function (q) { return S.spectOn[p][q]; });
        });
    }
    function tickSpect(pair, q, on) {
        S.spectOn = S.spectOn || {};
        S.spectOn[pair] = S.spectOn[pair] || {};
        S.spectNote = '';
        var f = spectField(pair, q);
        delete S.entered[f];
        delete S.raw[f];
        delete S.invalid[fieldKey(f, 'length')];
        delete S.invalid[fieldKey(f, 'level')];
        if (!on) { delete S.spectOn[pair][q]; return; }
        S.spectOn[pair][q] = true;
        var len = gateLength();
        S.raw[f] = { length: len === null ? '' : String(len), level: '' };
        S.entered[f] = len === null ? {} : { length: len };
        if (len === null) S.invalid[fieldKey(f, 'length')] = LENGTH_NEEDED;
        S.invalid[fieldKey(f, 'level')] = LEVEL_NEEDED;
    }
    function planRow(pair) {
        return ((S.plan && S.plan.pairs) || []).filter(function (p) { return p.pair === pair; })[0] || null;
    }
    function checkRow(pair) {
        return ((S.check && S.check.pairs) || []).filter(function (p) { return p.pair === pair; })[0] || null;
    }
    /* A spectator's identity colour: its lane's after a check (the chart and
       the table use the same), else the next line colours after the gate's. */
    function spectLine(pair, line, k) {
        var cr = checkRow(pair);
        var lane = cr && cr.timeline && (cr.timeline.lanes || []).filter(function (ln) { return ln.key === line; })[0];
        if (lane && typeof lane.color_index === 'number') return lane.color_index % 6 + 1;
        var gate = (S.slotChannels || []).filter(function (x, i, a) { return x && a.indexOf(x) === i; }).length || 1;
        return (gate + k) % 6 + 1;
    }
    function portWords(c) {
        var port = c.port ? String(c.port).replace(/^#\/ports\/[^/]+\//, '') : null;
        var range = typeof c.range === 'number' ? 'range ±' + fmt(c.range) + ' V' : 'range not known';
        return (port ? 'port ' + port : 'port not known') + ', ' + range;
    }
    /* A reason a new gate name fixes goes under Name, even when it names a
       spectator operation. */
    function spectRefusals(pair) {
        var pr = planRow(pair);
        var named = (pr && pr.name_refusals) || [];
        return ((pr && !pr.ok && pr.refusals) || []).filter(function (r) { return /spectator/i.test(r) && named.indexOf(r) < 0; });
    }
    function fillSpect() {
        var panel = $('czg-spect');
        var host = clear($('czg-spect-body'));
        var sum = $('czg-spect-sum');
        if (!panel || !host) return;
        var offer = spectOffer();
        panel.hidden = !offer;
        if (!offer) return;
        if (!offer.offered) {
            if (sum) sum.textContent = 'not offered';
            if (S.spectNote) host.appendChild(note(S.spectNote));
            host.appendChild(el('p', 'ui-text-quiet', offer.why || 'This gate class takes no spectators.'));
            return;
        }
        var U = UIkit();
        var pairs = selectedPairs();
        var lines = 0;
        if (S.spectNote) host.appendChild(note(S.spectNote));
        host.appendChild(el('p', 'ui-text-quiet', 'A spectator line plays a constant level while the gate plays. None plays unless you tick it.'));
        pairs.forEach(function (pair) {
            var grp = el('div', 'ui-field-stack');
            grp.setAttribute('data-czg-spect-pair', pair);
            if (pairs.length > 1) grp.appendChild(el('div', 'ui-field-label', 'Spectators of ' + pair));
            var cands = candidates(pair);
            if (!cands.length) grp.appendChild(el('p', 'ui-text-quiet', 'No other qubit of this chip has a pulsed flux line.'));
            var on = (S.spectOn || {})[pair] || {};
            var refused = spectRefusals(pair);
            var k = 0;
            var pr = planRow(pair);
            cands.forEach(function (c) {
                var f = spectField(pair, c.qubit);
                var row = el('div', 'ui-field-stack');
                row.setAttribute('data-czg-spect-row', c.qubit);
                var head = el('div', 'czg-more-row');
                var l = U.check('', !!on[c.qubit]);
                var i = l.querySelector('input');
                i.setAttribute('data-czg-spect-pair', pair);
                i.setAttribute('data-czg-spect-qubit', c.qubit);
                i.setAttribute('aria-label', 'Play a spectator pulse on ' + c.line);
                l.appendChild(el('span', on[c.qubit] ? 'ui-slot-chip ui-line-' + spectLine(pair, c.line, k) : 'ui-chip', c.line));
                head.appendChild(l);
                head.appendChild(el('span', 'ui-text-quiet', portWords(c)));
                row.appendChild(head);
                if (on[c.qubit]) {
                    k++;
                    lines++;
                    var grid = el('div', 'ui-grid2');
                    var raw = S.raw[f] || {};
                    grid.appendChild(fieldInput(f, 'length', 'Length', 'ns', 'int', raw.length, null));
                    grid.appendChild(fieldInput(f, 'level', 'Level', 'V', 'float', raw.level, null));
                    row.appendChild(grid);
                    var planned = ((pr && pr.ok && pr.spectators) || []).filter(function (x) { return x.qubit === c.qubit; })[0];
                    if (planned && planned.range_not_compared) {
                        var rn = el('p', 'ui-text-quiet', planned.range_not_compared);
                        rn.setAttribute('data-czg-spect-range-note', c.qubit);
                        row.appendChild(rn);
                    }
                    var mine = refused.filter(function (r) { return r.indexOf('For the spectator ' + c.qubit + ':') === 0; });
                    var own = S.invalid[fieldKey(f, 'length')] || S.invalid[fieldKey(f, 'level')];
                    if (mine.length && !own) {
                        var w = el('p', 'ui-field-error', mine.join(' '));
                        w.setAttribute('role', 'alert');
                        row.appendChild(w);
                    }
                    refused = refused.filter(function (r) { return mine.indexOf(r) < 0; });
                }
                grp.appendChild(row);
            });
            var ops = ticked(pair).map(function (c) {
                var m = pr && pr.spectator_ops ? pr.spectator_ops[c.qubit] : undefined;
                return typeof m === 'number' ? c.line + ' will hold ' + m + (m === 1 ? ' spectator operation' : ' spectator operations') : null;
            }).filter(Boolean);
            if (ops.length) {
                var q = el('p', 'ui-text-quiet', 'After this create: ' + ops.join(', ') + '.');
                q.setAttribute('data-czg-spect-ops', pair);
                grp.appendChild(q);
            }
            refused.forEach(function (r) {
                var w = el('p', 'ui-field-error', r);
                w.setAttribute('role', 'alert');
                grp.appendChild(w);
            });
            host.appendChild(grp);
        });
        // after a check: the phase shift is not calibrated, said once, quietly
        if (S.check && lines) {
            var said = '';
            pairs.forEach(function (pair) {
                var pr2 = planRow(pair);
                ((pr2 && pr2.spectators) || []).forEach(function (x) { if (!said && x.not_calibrated) said = x.not_calibrated; });
            });
            if (said) { var nc = el('p', 'ui-text-quiet', said); nc.setAttribute('data-czg-spect-uncal', ''); host.appendChild(nc); }
        }
        if (sum) sum.textContent = !lines ? 'none' : lines + (lines === 1 ? ' line ticked' : ' lines ticked');
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
    /* After a check: each form slot takes its lane's kit line colour from the server. */
    function recolourSlots() {
        Array.prototype.slice.call(document.querySelectorAll('#czg-slots .ui-slot')).forEach(function (s) {
            var chip = s.querySelector('.ui-slot-chip');
            if (!chip) return;
            var n = slotLine(chip.textContent);
            Array.prototype.slice.call(s.classList).forEach(function (c) { if (/^ui-line-\d+$/.test(c)) s.classList.remove(c); });
            s.classList.add('ui-line-' + n);
        });
    }
    function slotLine(channel) {
        // after a check the server's lane colour wins: form chip, lane chip and table dot match
        var shownPair = S.check && (S.check.pairs || []).filter(function (x) { return x.pair === $('czg-pair').value; })[0];
        var lane = shownPair && shownPair.timeline && (shownPair.timeline.lanes || []).filter(function (ln) { return ln.key === channel || ln.key === 'coupler ' + channel; })[0];
        if (lane && typeof lane.color_index === 'number') return lane.color_index + 1;
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
        if (sum) { var nl = S.slotChannels.length; sum.textContent = nl === 1 ? '1 line' : nl + ' lines'; }
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
                if (placeOf(f, k, sp.optional && !((S.entered[f] || {}).hasOwnProperty(k))) === 'fold') opt.push(node); else main.appendChild(node);
            });
            b.appendChild(main);
            if (opt.length) {
                var d = UIkit().disclosure('Optional fields', opt.length, [], opt.some(function (nd) {
                    var ii = nd.querySelector('input[data-czg-field]');
                    return ii && S.focusKey === fieldKey(f, ii.getAttribute('data-czg-name'));
                }));
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
        if (!list.length || !host) return;
        var sum = $('czg-settings-sum');
        var given = list.filter(function (x) { return x.source === 'given'; }).length;
        if (sum) sum.textContent = given ? given + ' entered' : 'as the model gate';
        var opt = [];
        list.forEach(function (x) {
            if (placeOf('@settings', x.key, x.optional && !((S.entered['@settings'] || {}).hasOwnProperty(x.key))) === 'fold') { opt.push(x); return; }
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
            var d = UIkit().disclosure('Optional settings', opt.length, [], opt.some(function (x) { return S.focusKey === fieldKey('@settings', x.key); }));
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
        resultQuiet();
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
        simReset();
        if (S.check) {
            var ev = $('czg-evidence');
            if (ev) ev.classList.add('czg-stale');
            if (S.alertKind !== 'changed') alertBox(callout('note', 'Inputs changed: press Check again.', 'The result below is for the earlier inputs.'), 'changed');
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
                return r.json().then(function (j) { return { r: r, j: plainAnswer(j || {}) }; },
                                     function () { return { r: r, j: {} }; });
            });
    }
    /* The server tags a reason a new gate name fixes with "name: ". The tag
       routes, it is never shown: every plan, check, create and simulate answer
       passes here once, so each refusal, verdict reason, skipped reason and
       error reads plainly, and a row's tagged reasons are kept in name_refusals.
       Twin of cz_gate_build.plain_reasons (core/cz_gate_build.py), the same
       rule: the tag is dropped only at the start of a reason, at the start of
       the next reason of a space join, or right after a "<pair>: " prefix of a
       pair the answer names; tests/fixtures/name_tag_vectors.json pins both. */
    var NAME_TAG = 'name: ';
    var REASON_KEYS = { refusals: 1, class_refusals: 1, reason: 1, error: 1, headline: 1 };
    function reEsc(t) { return String(t).replace(/[.*+?^${}()|[\]\\\-]/g, '\\$&'); }
    function tagPattern(pairs) {
        var ids = (pairs || []).filter(function (p, i, a) { return p && a.indexOf(p) === i; })
            .sort(function (x, y) { return y.length - x.length; }).map(reEsc).join('|');
        var pair = ids ? '|(?:^|(?<=\\s))(?:' + ids + '): ' : '';
        return new RegExp('(^|(?<=\\. )' + pair + ')' + reEsc(NAME_TAG) + '(?=[A-Z])', 'g');
    }
    function pairsOf(x, acc) {
        if (Array.isArray(x)) x.forEach(function (v) { pairsOf(v, acc); });
        else if (x && typeof x === 'object') {
            Object.keys(x).forEach(function (k) {
                if (k === 'pair' && typeof x[k] === 'string') acc.push(x[k]); else pairsOf(x[k], acc);
            });
        }
        return acc;
    }
    function untag(re, r) { return r.replace(re, '$1'); }
    function plainReason(r, pairs) { return typeof r === 'string' ? untag(tagPattern(pairs), r) : r; }
    function plainAnswer(j) {
        var re = tagPattern(pairsOf(j, []));
        function walk(x, under) {
            if (Array.isArray(x)) return x.map(function (v) { return walk(v, under); });
            if (x && typeof x === 'object') {
                var out = {};
                Object.keys(x).forEach(function (k) { out[k] = walk(x[k], under || REASON_KEYS[k] === 1); });
                if (Array.isArray(x.refusals)) {
                    out.name_refusals = x.refusals.filter(function (r) { return typeof r === 'string' && r.indexOf(NAME_TAG) === 0; })
                        .map(function (r) { return untag(re, r); });
                }
                return out;
            }
            return under && typeof x === 'string' ? untag(re, x) : x;
        }
        return walk(j, false);
    }
    function plan() {
        var b = body();
        var mine = S.seq, g = S;
        if (S.phase === 'done') return Promise.resolve();      // nothing is planned after Create
        if (!b.pairs.length) { S.plan = null; refresh(); return Promise.resolve(); }
        return post('/api/pulse/cz-gate/plan', b).then(function (x) {
            if (S !== g || mine !== S.seq || S.phase === 'done' || S.busy) return;
            if (!x.r.ok || !x.j.ok) {
                S.plan = null;
                alertBox(callout('danger', 'The plan could not be made', (x.j.error || ('The app answered HTTP ' + x.r.status)) + ' Change the inputs or close the dialog.'), 'plan-error');
                refresh();
                return;
            }
            S.plan = x.j;
            S.planSig = x.j.plan_sig;
            renderPlan();
        }).catch(function (e) {
            if (S !== g) return;
            alertBox(callout('danger', 'The plan could not be made', 'The app did not answer (' + e + '). Try again.'), 'plan-error');
        });
    }
    function renderPlan() {
        var fp = firstPlan();
        if (S.alertKind === 'plan-error') alertBox(null);
        var why = clear($('czg-name-why'));
        var aboutClass = fp && !fp.ok ? (fp.class_refusals || []) : [];
        var aboutSpect = fp ? spectRefusals(fp.pair) : [];
        var split = splitRefusals(fp && !fp.ok ? (fp.refusals || []).filter(function (r) { return aboutClass.indexOf(r) < 0 && aboutSpect.indexOf(r) < 0; }) : [],
                                  fp && !fp.ok ? (fp.name_refusals || []) : []);
        var bad = split.names;
        var general = clear($('czg-refusals'));
        split.rest.forEach(function (r) {
            general.appendChild(el('p', 'ui-note', r));
        });
        general.hidden = !general.childNodes.length;
        renderClassWhy(S.noClass ? [] : aboutClass);
        bad.forEach(function (r) { why.appendChild(el('span', 'czg-why-line', r)); });
        why.hidden = !bad.length;
        if (bad.length) $('czg-name').setAttribute('aria-invalid', 'true'); else $('czg-name').removeAttribute('aria-invalid');
        var focused = document.activeElement;
        var keep = focused && focused.getAttribute ? [focused.getAttribute('data-czg-field'), focused.getAttribute('data-czg-name')] : null;
        var caret = null;
        if (keep && keep[0]) { try { caret = [focused.selectionStart, focused.selectionEnd]; } catch (e) { caret = null; } }
        S.focusKey = keep && keep[0] ? fieldKey(keep[0], keep[1]) : null;
        fillSlots();
        fillSettings();
        fillModelFacts();
        fillSpect();
        S.focusKey = null;
        if (keep && keep[0]) restoreFocus(keep, caret);
        split.fields.forEach(function (x) {
            var i = findField(x.field, x.name);
            if (i && !S.invalid[fieldKey(x.field, x.name)]) showFieldError(i, x.why);
        });
        var rows = {};
        ((S.plan && S.plan.pairs) || []).forEach(function (p) { rows[p.pair] = p; });
        var n = 0;
        Array.prototype.slice.call(($('czg-more-list') || document.createElement('div')).querySelectorAll('[data-czg-more-row]')).forEach(function (row) {
            var i = row.querySelector('input');
            var w = row.querySelector('[data-czg-more-why]');
            var p = rows[i.value];
            if (i.checked) n++;
            w.textContent = !i.checked ? '' : !p ? '' : p.ok ? 'can be created' : (p.refusals || []).join(' ');
        });
        var cnt = $('czg-more-count');
        if (cnt) cnt.textContent = n ? '+' + n + (n === 1 ? ' pair' : ' pairs') : 'only ' + ($('czg-pair').value || 'this pair');
        renderWrites();
        applyBusyLook();
        refresh();
    }
    /* The same field and caret get the focus back after a re-render. */
    function restoreFocus(keep, caret) {
        var again = findField(keep[0], keep[1]);
        if (!again || document.activeElement === again) return;
        try { again.focus(); } catch (e) { return; }
        var a = caret && typeof caret[0] === 'number' ? caret[0] : again.value.length;
        var b = caret && typeof caret[1] === 'number' ? caret[1] : a;
        try { again.setSelectionRange(Math.min(a, again.value.length), Math.min(b, again.value.length)); } catch (e) { /* not a text input */ }
    }
    /* The server names a refused value by "<slot>.<field>: <sentence>" or
       "gate setting <key>: <sentence>": those go on the field they name.
       A reason the server tagged "name: " (plainAnswer keeps it in
       name_refusals) belongs under Name, independent of its wording. */
    function splitRefusals(list, named) {
        var fields = [], rest = [], names = [];
        list.forEach(function (r) {
            if ((named || []).indexOf(r) >= 0) { names.push(r); return; }
            var m = /^gate setting (.+?): (Type .+)$/.exec(r);
            if (m && findField('@settings', m[1])) { fields.push({ field: '@settings', name: m[1], why: m[2] }); return; }
            m = /^([^.:\s]+)\.([^:]+): (.+)$/.exec(r);
            if (m && findField(m[1], m[2])) { fields.push({ field: m[1], name: m[2], why: m[3] }); return; }
            rest.push(r);
        });
        return { fields: fields, rest: rest, names: names };
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
        var made = S.phase === 'done' && S.created;
        var title = $('czg-writes-title');
        if (title) title.textContent = made ? 'What was written' : 'What will be written';
        if (made) {
            // the objects the create wrote, from its answer: never a plan after Create
            (S.created.created || []).forEach(function (c) { rows.push({ pair: c.pair, ok: true, paths: c.paths || [], why: '' }); });
        } else if (S.check && S.check.pairs) {
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
        if (!n) { host.appendChild(el('p', 'ui-text-quiet', made ? 'Nothing was written.' : 'Nothing will be written yet.')); }
        else if (!made) host.appendChild(el('p', 'ui-text-quiet', syncLine((S.check && S.check.sync) || S.data.sync)));
        rows.forEach(function (r) {
            var grp = el('div', 'ui-field-stack');
            grp.setAttribute('data-czg-writes-pair', r.pair);
            var h = el('div', 'ui-field-label', r.pair + (r.ok ? ': ' + r.paths.length + (r.paths.length === 1 ? ' object' : ' objects') : ''));
            grp.appendChild(h);
            if (!r.ok) grp.appendChild(el('div', 'ui-text-quiet', 'not written: ' + sentence(r.why)));
            else r.paths.forEach(function (x) { grp.appendChild(U.path(x)); });
            host.appendChild(grp);
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
        // a spectator lane carries only color_index: it takes that colour (the one its
        // form chip and table dot wear), and the ticked spectators are shown unfolded
        var simLanes = (S.sim && S.sim.lanes) || {};
        var data = Object.assign({}, tl, { lanes: (tl.lanes || []).map(function (ln) {
            if (ln && simLanes[ln.key]) ln = Object.assign({}, ln, { sim: simLanes[ln.key] });
            return ln && ln.group === 'spectator' && typeof ln.color !== 'number' && typeof ln.color_index === 'number'
                ? Object.assign({}, ln, { color: ln.color_index }) : ln;
        }) });
        try { S.handle = window.GateTimeline.render(host, data, { toolbarHost: $('czg-chart-tools'), foldSpectators: false }); }
        catch (e) { clear(host).appendChild(note('Chart not loaded: ' + e)); }
    }
    function lineOfChannel(tl) {
        var m = {};
        ((tl && tl.lanes) || []).forEach(function (ln) { m[ln.key] = (ln.color_index || 0) + 1; });
        return m;
    }
    var CELL_TONE = { warn: 'ui-cell--warn', danger: 'ui-cell--danger' };
    /* A spectator row is named by its line: every spectator of a gate shares one operation name. */
    function rowName(r) { return r.group === 'spectator' ? 'spectator on ' + (r.lane_key || '?') : r.operation; }
    /* The lane key of an intent row comes from the server (row.lane_key). */
    function laneKeyOf(r) { return r.lane_key || ''; }
    function renderIntent(rows, tl, pair) {
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
            return [{ chip: rowName(r) },
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
            // the plan names a coupler line "coupler <element>"; the lane key is the element
            var ck = laneKeyOf(r);
            var dot = el('span', 'ui-line-dot ui-line-' + (lines[ck] || 1));
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
        rows.forEach(function (r) { (r.why || []).forEach(function (w) { host.appendChild(el('p', 'ui-text-quiet', rowName(r) + ': ' + w)); }); });
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
        var oc = p.outcome;
        if (sum && oc && oc.kind !== 'none') sum.textContent = oc.failed.length ? oc.failed.length + ' failed, ' + oc.passed + ' passed'
            : oc.passed + ' of ' + oc.total + ' passed' + (oc.not_run.length ? ', ' + oc.not_run.length + ' could not run' : '');
        else if (sum) sum.textContent = nf ? nf + ' failed, ' + np + ' passed' : np + ' of ' + list.length + ' passed' + (ns ? ', ' + ns + ' not checked' : '');
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
        resultQuiet();
    }
    /* The Result zone is quiet (its head and one centred line) until it has
       something to show: a check answer or the model gate's facts. */
    function resultQuiet() {
        var z = $('czg-zone-result');
        if (!z) return;
        var mp = $('czg-model-panel');
        var e = $('czg-empty');
        z.classList.toggle('ui-zone--quiet', !!(e && !e.hidden) && !(mp && !mp.hidden));
    }
    function openLabel() {
        var b = $('czg-open-pair');
        if (!b) return;
        var pairs = (S.check && S.check.pairs) || [];
        var p = pairs.length > 1 ? (pairs[S.pairIdx] || pairs[0]) : null;
        b.textContent = p ? 'Open ' + p.pair : 'Open the pair';
        if (p) b.setAttribute('data-czg-open-target', p.pair); else b.removeAttribute('data-czg-open-target');
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
        if (S.sim && (!p || S.sim.pair !== p.pair)) simReset();
        if (p) {
            renderChart(p.ok ? p.timeline : null);
            renderIntent(p.intent, p.timeline, p.pair);
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
        openLabel();
        simButton();
    }
    /* What the checks that ran came to, in the server's words (one judgement
       for the banner, the summary and the record): "every check passed." or
       "5 checks passed, 1 could not run: preview comparison (why)." */
    function outcomeLine(ok, many) {
        var part = ok.filter(function (x) { return x.outcome && x.outcome.kind === 'partly'; });
        if (!part.length) return 'every check passed.';
        return part.map(function (x) { return (many ? x.pair + ': ' : '') + x.outcome.summary; }).join(' ');
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
        recolourSlots();
        fillSpect();
        var ok = allowedPairs();
        var no = pairs.filter(function (x) { return !(x.verdict && x.verdict.allowed); });
        var reason = function (x) { var v = x.verdict || {}; return sentence(v.headline || v.reason || 'no verdict from the server'); };
        var many = pairs.length > 1;
        var unv = ok.filter(function (x) { return x.verdict.state !== 'verified'; });
        // every unverified pair with its own reason: up to three, then "And N more."
        var named = unv.slice(0, 3).map(function (x) { return (many ? x.pair + ': ' : '') + reason(x); }).join(' ')
            + (unv.length > 3 ? ' And ' + (unv.length - 3) + ' more.' : '');
        if (!ok.length) {
            alertBox(banner('danger', many ? 'None of the ' + pairs.length + ' pairs can be created yet' : 'Cannot be created yet',
                no.map(function (x) { return (many ? x.pair + ': ' : '') + reason(x); }).join(' ') + ' Change the inputs and check again.'), 'verdict');
        } else if (no.length) {
            alertBox(banner('danger', no.length + ' of ' + pairs.length + ' cannot be created: ' + no.map(function (x) { return x.pair; }).join(', '),
                no.map(function (x) { return x.pair + ': ' + reason(x); }).join(' ') + ' The other ' + ok.length
                + (ok.length === 1 ? ' pair' : ' pairs') + ' can still be created.'
                + (unv.length ? ' Not checked with the lab\'s code: ' + named : '')), 'verdict');
        } else if (ok.some(function (x) { return x.verdict.state !== 'verified'; })) {
            alertBox(banner('warn', many ? unv.length + ' of ' + ok.length + ' not checked with the lab\'s code' : 'Not checked with the lab\'s code',
                named + ' You can still create ' + (unv.length === 1 && !many ? 'it' : 'them') + '; the record says '
                + (unv.length === 1 ? 'it was' : 'they were') + ' not checked.'), 'verdict');
        } else {
            var line = outcomeLine(ok, many);
            alertBox(banner('ok', many ? 'Ready to create on ' + ok.length + ' pairs' : 'Ready to create',
                line === 'every check passed.'
                    ? 'The lab\'s code built ' + (many ? 'these gates' : 'this gate') + ' on a copy of the chip and every check passed.'
                    : 'The lab\'s code built ' + (many ? 'these gates' : 'this gate') + ' on a copy of the chip. '
                      + line.charAt(0).toUpperCase() + line.slice(1)), 'verdict');
        }
        pairs.forEach(function (p) {
            (p.tiles || []).filter(function (t) { return t.key === 'timing' && t.tone === 'warn'; }).forEach(function (t) {
                $('czg-alert').appendChild(el('p', 'ui-note', (many ? p.pair + ': ' : '')
                    + 'The gate lines have a ' + t.value + ' ns gap with no overlap. Check the port delays.'));
            });
        });
        // a stacked dialog has the banner above the form: bring it into view
        var al = $('czg-alert');
        if (al && al.scrollIntoView) { try { al.scrollIntoView({ block: 'nearest' }); } catch (e) { /* old engine */ } }
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
        simReset();
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
            S.checkBody = b;
            S.checkSeq = mine;
            renderCheck();
        }).catch(function (e) {
            if (S !== g) return;
            setBusy(false, '');
            alertBox(callout('danger', 'The check did not run', 'The app did not answer (' + e + '). Press Check to try again.'));
        });
    }

    /* ---- simulate -------------------------------------------------------- */
    /* The server words every state (its `line` is shown verbatim); the only
       client-worded line is the one for an app that did not answer. A run is
       tied to the plan it was started for: any form edit, pair change or new
       check drops its traces and its status. */
    var SIM_POLL_MS = 1000;
    var SIM_NO_ANSWER = 'Not simulated: the app did not answer.';
    var SIM_LIVE = { starting: 1, running: 1 };
    function simCall(url, payload) {
        var init = payload === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                  body: JSON.stringify(payload) };
        return fetch(url, init).then(function (r) {
            return r.json().then(function (j) { return { r: r, j: (j && typeof j === 'object') ? plainAnswer(j) : null }; },
                                 function () { return { r: r, j: null }; });
        }, function () { return { r: null, j: null }; });
    }
    function shownPair() {
        var pairs = (S && S.check && S.check.pairs) || [];
        return pairs[S.pairIdx] || pairs[0] || null;
    }
    function simCanStart() {
        var p = shownPair();
        return !!(S && S.check && S.checkBody && S.checkSeq === S.seq && S.phase !== 'done'
                  && p && p.ok && p.timeline);
    }
    function simButton() {
        var b = $('czg-sim');
        if (!b || !S) return;
        var stop = !!(S.sim && (SIM_LIVE[S.sim.state] || S.sim.state === 'busy'));
        b.textContent = stop ? 'Stop' : 'Simulate';
        // Stop needs the run id: while the start answer is out there is nothing to stop yet
        b.disabled = stop ? simNoId() : (S.busy || !simCanStart());
    }
    function simNoId() {
        return !!(S && S.sim && S.sim.state === 'starting' && !S.sim.id);
    }
    function simShow(line, elapsed) {
        var l = $('czg-sim-line');
        if (l) l.textContent = line || '';
        var e = $('czg-sim-elapsed');
        if (e) e.textContent = typeof elapsed === 'number' && isFinite(elapsed) ? Math.round(elapsed) + ' s' : '';
    }
    function simReset() {
        if (!S) return;
        var had = S.sim;
        S.simGen = (S.simGen || 0) + 1;
        if (had && had.timer) clearTimeout(had.timer);
        if (had && SIM_LIVE[had.state] && had.id) simCall('/api/pulse/cz-gate/simulate/stop', { id: had.id });
        S.sim = null;
        simShow('', null);
        clear($('czg-sim-notes'));
        if (had && had.lanes) {            // traces were drawn: draw the chart again without them
            var p = shownPair();
            if (p && p.ok) renderChart(p.timeline);
        }
        simButton();
    }
    function portKeys(pt) {
        var parts = [pt.controller, pt.fem, pt.port];
        var keys = [parts.join(' / '), parts.join('/')];
        if (pt.fem === null || pt.fem === undefined) keys.push([pt.controller, pt.port].join(' / '));
        if (pt.port_text) keys.push(pt.port_text);
        return keys.map(String);
    }
    /* Each port goes onto the lane with the same controller / fem / port and an
       element the port names; a port whose time step is unknown is never drawn. */
    function simMatch(tl, ans) {
        var lanes = (tl && tl.lanes) || [];
        var got = {}, lines = [], unknown = {};
        (ans.ports || []).forEach(function (pt) {
            if (!pt) return;
            var els = pt.elements || [];
            if (pt.time_step_unknown) { els.forEach(function (e) { unknown[e] = true; }); return; }
            var keys = portKeys(pt);
            var hit = lanes.filter(function (ln) {
                return ln && keys.indexOf(String(ln.port)) >= 0 && els.indexOf(ln.key) >= 0;
            });
            if (!hit.length) {
                lines.push('The simulated output of ' + (pt.port_text || keys[0]) + ' (' + (els.join(', ') || 'no element named')
                           + ') is not drawn: no timeline line plays on that port.');
                return;
            }
            var v = (pt.volts || []).slice();
            var t = v.map(function (_x, i) { return pt.t0_ns + i * pt.dt_ns; });
            hit.forEach(function (ln) { got[ln.key] = { t_ns: t, v: v }; });
        });
        lanes.forEach(function (ln) {
            if (ln && !got[ln.key] && !unknown[ln.key]) lines.push(ln.key + ' has no simulated output: the simulator gave no trace for its port.');
        });
        return { lanes: got, lines: lines };
    }
    function simNotes(ans, extra) {
        var host = clear($('czg-sim-notes'));
        if (!host) return;
        [ans.time_step_note, ans.cut_note, ans.note, ans.cannot_draw].concat(extra || []).forEach(function (t) {
            if (typeof t === 'string' && t) host.appendChild(el('p', 'ui-text-quiet', t));
        });
    }
    /* Every request takes a sequence number; an answer older than the latest
       action (start / stop) or the latest applied answer is ignored, so the
       line shown is always the latest action's answer. */
    function simSeq(g, action) {
        g.simReq = (g.simReq || 0) + 1;
        if (action) g.simAction = g.simReq;
        return g.simReq;
    }
    function simAnswer(g, gen, x, seq, isStart) {
        if (S !== g || S.simGen !== gen) {                     // closed, or the plan changed
            // a run the server started for a plan that is gone is stopped by its id;
            // the stop answer is not shown (it belongs to no plan on screen)
            if (isStart && x && x.j && SIM_LIVE[x.j.state] && typeof x.j.id === 'string' && x.j.id)
                simCall('/api/pulse/cz-gate/simulate/stop', { id: x.j.id });
            return;
        }
        if (seq < (g.simAction || 0) || seq < (g.simApplied || 0)) return;   // a newer action owns the line
        g.simApplied = seq;
        var sim = S.sim || {};
        if (sim.timer) clearTimeout(sim.timer);
        sim.timer = null;
        S.sim = sim;
        if (!x.j || typeof x.j.line !== 'string') {
            sim.state = 'error';
            simShow(SIM_NO_ANSWER, null);
            simButton();
            return;
        }
        var j = x.j;
        sim.state = j.state;
        if (j.id) sim.id = j.id;
        var live = !!SIM_LIVE[j.state];
        var elapsed = typeof j.elapsed_s === 'number' ? j.elapsed_s : (sim.t0 ? (Date.now() - sim.t0) / 1000 : 0);
        simShow(j.line, live ? elapsed : null);
        if (j.state === 'done') {
            var p = shownPair();
            var m = simMatch(p && p.timeline, j);
            sim.lanes = m.lanes;
            if (p && p.ok) renderChart(p.timeline);
            simNotes(j, m.lines);
        } else if (live && sim.id) {
            sim.timer = setTimeout(function () { simPoll(g, gen); }, SIM_POLL_MS);
        }
        simButton();
    }
    function simPoll(g, gen) {
        if (S !== g || S.simGen !== gen || !S.sim || !S.sim.id) return;
        S.sim.timer = null;
        var seq = simSeq(g, false);
        return simCall('/api/pulse/cz-gate/simulate/status?id=' + encodeURIComponent(S.sim.id))
            .then(function (x) { simAnswer(g, gen, x, seq, false); });
    }
    function simulate() {
        if (!S) return Promise.resolve();
        var g = S;
        if (S.sim && (SIM_LIVE[S.sim.state] || S.sim.state === 'busy')) {      // the action is Stop
            if (simNoId()) return Promise.resolve();          // no id yet: nothing to stop, nothing sent
            var gen0 = S.simGen;
            if (S.sim.timer) { clearTimeout(S.sim.timer); S.sim.timer = null; }
            var sseq = simSeq(g, true);
            // without an id only after a 409 busy answer: the server stops this window's run
            return simCall('/api/pulse/cz-gate/simulate/stop', S.sim.id ? { id: S.sim.id } : {})
                .then(function (x) { simAnswer(g, gen0, x, sseq, false); });
        }
        if (!simCanStart()) return Promise.resolve();
        var p = shownPair();
        simReset();
        var gen = S.simGen;
        S.sim = { state: 'starting', pair: p.pair, t0: Date.now() };
        simShow('Waiting for the simulator to answer.');
        simButton();
        var seq = simSeq(g, true);
        return simCall('/api/pulse/cz-gate/simulate/start', Object.assign({}, S.checkBody, { pair: p.pair }))
            .then(function (x) { simAnswer(g, gen, x, seq, true); });
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
                    if (window.showToast) window.showToast(j.partial ? (j.error || 'The create failed part way.') : j.ok ? 'Created ' + ((j.created || []).map(function (c) { return c.gate + ' on ' + c.pair; }).join(', '))
                        : 'The gate was not created: ' + (j.error || ('HTTP ' + x.r.status)), j.ok && !j.partial ? 'success' : 'error');
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
                if (j.partial) {
                    // written part way and not undone: the server's sentence, never "Created"
                    S.phase = 'check';
                    setBusy(false, '');
                    alertBox(banner('danger', 'Partly written', j.error || 'The create failed part way and could not be undone. Press Ctrl+Z.'), 'partial');
                    return;
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
        var many = created.length > 1;
        created.forEach(function (c) {
            var holder = el('div');
            holder.setAttribute('data-czg-status-for', c.pair + '/' + c.gate);
            slot.appendChild(holder);
            fetch('/pulse/cz-gate/status?pair=' + encodeURIComponent(c.pair) + '&gate=' + encodeURIComponent(c.gate))
                .then(function (r) { return r.text(); })
                .then(function (html) {
                    if (S !== g) return;
                    holder.innerHTML = html;
                    var row = many && holder.querySelector('.gate-status-row');
                    if (row) {
                        var tag = el('span', 'ui-chip', c.pair);
                        tag.setAttribute('data-czg-status-pair', c.pair);
                        row.insertBefore(tag, row.firstChild);
                    }
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
        alertBox(banner('ok', 'Created ' + made.join(', '), createdWhy(word, skip)), 'created');
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
        // the result stays at full strength: it is what was just written, not an earlier answer
        var open = $('czg-open-pair');
        if (open) { openLabel(); open.hidden = false; }
        var cancel = $('czg-cancel');
        if (cancel) cancel.hidden = true;
        say(SYNC_WORDS[word]);
        renderWrites();
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
        if (S) { simReset(); clearTimeout(S.timer); if (S.handle && S.handle.destroy) { try { S.handle.destroy(); } catch (e) { /* gone */ } } }
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
    /* Stacked when the form and the evidence column do not fit side by side
       (their kit widths plus the gap): the kit then puts the banner above the form. */
    function layoutStack() {
        var d = $('czg-dialog'), cols = d && d.querySelector('.czg-cols');
        if (!cols || !window.getComputedStyle) return;
        var cs = window.getComputedStyle(cols);
        var need = (parseFloat(cs.getPropertyValue('--ui-czg-form-w')) || 360)
            + (parseFloat(cs.getPropertyValue('--ui-czg-evidence-min')) || 640) + (parseFloat(cs.columnGap) || 0);
        var w = cols.clientWidth;
        if (w > 0 && w < need) d.setAttribute('data-czg-stacked', ''); else d.removeAttribute('data-czg-stacked');
    }
    window.addEventListener('resize', function () { if (S) layoutStack(); });
    function init() {
        var src = $('czg-data');
        S.data = JSON.parse((src && src.textContent) || '{}');
        fillPairLine();
        fillClasses();
        fillModel();
        fillFrom();
        fillMore();
        fillSlots();
        fillSpect();
        renderWrites();
        refresh();
        layoutStack();
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
            var ob = $('czg-open-pair');
            var p = (ob && ob.getAttribute('data-czg-open-target')) || ($('czg-pair') ? $('czg-pair').value : '');
            close();
            if (p && window.htmx) window.htmx.ajax('GET', '/pair/' + encodeURIComponent(p), { target: '#inspector-pane', swap: 'innerHTML' });
            return;
        }
        var seg = t.closest('[data-czg-pair-idx]');
        if (seg) {
            var k = +seg.getAttribute('data-czg-pair-idx');
            S.pairIdx = k;
            renderPair();
            // the switch is drawn again: the pressed option keeps the focus
            Array.prototype.slice.call(($('czg-pairs') || document.createElement('div')).querySelectorAll('[data-czg-pair-idx]'))
                .forEach(function (b) { if (+b.getAttribute('data-czg-pair-idx') === k) { try { b.focus(); } catch (e) { /* not focusable */ } } });
            return;
        }
        if (t.closest('[data-czg-sim]')) { simulate(); return; }
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
        if (t.hasAttribute('data-czg-spect-qubit')) {
            tickSpect(t.getAttribute('data-czg-spect-pair'), t.getAttribute('data-czg-spect-qubit'), t.checked);
            fillSpect();
            applyBusyLook();
            inputsChanged();
            return;
        }
        if (t.id === 'czg-pair') {
            var had = anyTicked();
            S.spectOn = {};
            dropSpect(null);
            S.spectNote = had ? 'The spectator choices were cleared: ' + (t.value || 'the new pair') + ' has other neighbouring lines. Tick them again.' : '';
            if (fillClasses()) { S.entered = {}; S.raw = {}; S.cls = {}; S.placed = {}; S.invalid = {}; fillSlots(); }
            fillPairLine(); fillFrom(); fillModel(); fillMore(); fillSpect();
        }
        else if (t.hasAttribute('data-czg-slot')) {
            var f = t.getAttribute('data-czg-slot');
            S.cls[f] = t.value;
            S.entered[f] = {};
            S.raw[f] = {};
            [S.invalid, S.placed || {}].forEach(function (m) {
                Object.keys(m).forEach(function (k) { if (k.indexOf(f + '\u0000') === 0) delete m[k]; });
            });
        } else if (t.hasAttribute('data-czg-more')) {
            if (!t.checked && S.spectOn) { delete S.spectOn[t.value]; dropSpect(t.value); }
            fillSpect();
        } else if (t.id !== 'czg-from') { return; }
        if (t.hasAttribute('data-czg-slot')) fillSlots();
        inputsChanged();
    });
    document.addEventListener('ui-menu-pick', function (ev) {
        var t = ev.target;
        if (!t || !inDialog(t) || !S) return;
        var v = ev.detail && ev.detail.value;
        if (t.querySelector('#czg-class')) {
            if (v === S.klass) return;
            S.klass = v; S.klassByHand = true; S.classNote = ''; renderClassWhy([]);
            var hadTicks = anyTicked();
            S.entered = {}; S.raw = {}; S.cls = {}; S.placed = {}; S.invalid = {}; S.spectOn = {};
            S.spectNote = hadTicks ? 'The spectator choices were cleared: the new gate class takes other choices. Tick them again where it offers spectators.' : '';
            fillModel();
            fillFrom();
        } else if (t.querySelector('#czg-model')) {
            if (v === S.model) return;
            S.model = v; S.entered = {}; S.raw = {}; S.placed = {}; S.invalid = {};
        } else { return; }
        fillSlots();
        fillSpect();
        inputsChanged();
    });
    document.addEventListener('input', function (ev) {
        var t = ev.target;
        if (!t || !inDialog(t) || !S) return;
        if (t.id === 'czg-name') { inputsChanged(); return; }
        if (!t.hasAttribute('data-czg-field')) return;
        var f = t.getAttribute('data-czg-field');
        var n = t.getAttribute('data-czg-name');
        var r = isSpectField(f) && !t.value.trim() ? { ok: false, why: n === 'level' ? LEVEL_NEEDED : LENGTH_NEEDED } : parse(t);
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
    // on window: it runs after every document listener, so an open kit menu takes Escape first
    window.addEventListener('keydown', function (ev) {
        if (ev.key !== 'Escape' || !S || ev.defaultPrevented) return;      // a kit menu took this Escape
        var d = $('czg-dialog');
        if (!d || !d.hasAttribute('open')) return;
        ev.preventDefault();
        if (!S.busy) softClose();
    });

    window.CzGate = { open: open, close: close, state: function () { return S; },
                      _check: check, _create: create, _plan: plan, _simulate: simulate,
                      _plainAnswer: plainAnswer, _plainReason: plainReason };
})();
