/* Converter & calculator — topbar badge (feedback #2).
 *
 * Pure client-side, chip-INDEPENDENT (works with no chip loaded). Covers the QM
 * researcher's real conversions:
 *   1. Power change Δ(dB) → amplitude factor 10^(Δ/20)  (the headline ask;
 *      20·log10 because POWER ∝ amplitude², NOT 10·log10 — the central trap).
 *   2. MW-FEM amplitude ↔ dBm via full_scale_power_dbm: dBm = FSP + 20·log10|a|.
 *   3. dBm ↔ Volt @ R (50Ω RF default): three DISTINCT rows V_rms / V_peak / V_pp.
 *   4. A free expression box evaluated by a SAFE recursive-descent parser
 *      (NEVER eval()/Function — a topbar-global text input must not run JS).
 *
 * Formulas are byte-identical to generate.js ampToDisplay/ampToBase so the
 * calculator can't drift from the rest of the app.
 */
(function () {
    'use strict';

    // ── safe expression evaluator (the security boundary) ───────────────────────
    var FUNCS = {
        sqrt: Math.sqrt, log10: Math.log10, log: Math.log, ln: Math.log,
        exp: Math.exp, abs: Math.abs, sin: Math.sin, cos: Math.cos, tan: Math.tan
    };
    var CONSTS = { pi: Math.PI, e: Math.E };

    function tokenize(src) {
        var toks = [], i = 0, n = src.length;
        while (i < n) {
            var c = src[i];
            if (c === ' ' || c === '\t') { i++; continue; }
            if (c === '*' && src[i + 1] === '*') { toks.push({ t: '^' }); i += 2; continue; }
            if ('+-*/^()'.indexOf(c) >= 0) { toks.push({ t: c }); i++; continue; }
            if (c >= '0' && c <= '9' || c === '.') {
                var m = /^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?/.exec(src.slice(i));
                if (!m) throw { e: 'bad number' };
                toks.push({ t: 'num', v: parseFloat(m[0]) }); i += m[0].length; continue;
            }
            if (/[a-zA-Z_]/.test(c)) {
                var mm = /^[a-zA-Z_][a-zA-Z0-9_]*/.exec(src.slice(i));
                toks.push({ t: 'id', v: mm[0] }); i += mm[0].length; continue;
            }
            throw { e: 'unexpected "' + c + '"' };
        }
        return toks;
    }

    // Grammar (low→high precedence): add → mul → unary → pow → atom.
    // pow is right-assoc with a unary exponent so 10^(-25/20) and 2^-3 and 2^3^2 work.
    function parse(toks) {
        var p = 0;
        function peek() { return toks[p]; }
        function eat(t) { var x = toks[p]; if (!x || x.t !== t) throw { e: 'expected ' + t }; p++; return x; }
        function add() {
            var v = mul();
            while (peek() && (peek().t === '+' || peek().t === '-')) {
                var op = toks[p++].t; var r = mul(); v = op === '+' ? v + r : v - r;
            }
            return v;
        }
        function mul() {
            var v = unary();
            while (peek() && (peek().t === '*' || peek().t === '/')) {
                var op = toks[p++].t; var r = unary();
                if (op === '/' && r === 0) throw { e: 'divide by zero' };
                v = op === '*' ? v * r : v / r;
            }
            return v;
        }
        function unary() {
            if (peek() && peek().t === '-') { p++; return -unary(); }
            if (peek() && peek().t === '+') { p++; return unary(); }
            return pow();
        }
        function pow() {
            var b = atom();
            if (peek() && peek().t === '^') { p++; var e = unary(); return Math.pow(b, e); }
            return b;
        }
        function atom() {
            var x = peek();
            if (!x) throw { e: 'unexpected end' };
            if (x.t === 'num') { p++; return x.v; }
            if (x.t === '(') { p++; var v = add(); eat(')'); return v; }
            if (x.t === 'id') {
                p++;
                if (peek() && peek().t === '(') {
                    p++; var arg = add(); eat(')');
                    // hasOwnProperty so a prototype name (constructor/toString/valueOf/…)
                    // is 'unknown' at the LOOKUP, not merely blocked by the downstream
                    // typeof-number guard — the parser is the security boundary (audit P2).
                    var fn = Object.prototype.hasOwnProperty.call(FUNCS, x.v) ? FUNCS[x.v] : null;
                    if (!fn) throw { e: 'unknown: ' + x.v };
                    return fn(arg);
                }
                if (Object.prototype.hasOwnProperty.call(CONSTS, x.v)) return CONSTS[x.v];
                throw { e: 'unknown: ' + x.v };
            }
            throw { e: 'unexpected ' + x.t };
        }
        var val = add();
        if (p !== toks.length) throw { e: 'trailing input' };
        return val;
    }

    // Returns {ok, value} | {ok:false, err}. Never throws, never leaks Inf/NaN, never
    // resolves a global identifier (window/fetch/constructor → "unknown: …").
    function calcEval(expr) {
        if (expr == null) return { ok: false, err: '' };
        expr = String(expr).trim();
        if (expr === '') return { ok: false, err: '' };
        if (expr.length > 200) return { ok: false, err: 'too long' };
        try {
            var v = parse(tokenize(expr));
            if (typeof v !== 'number' || !isFinite(v)) return { ok: false, err: '—' };
            return { ok: true, value: v };
        } catch (e) {
            return { ok: false, err: (e && e.e) ? e.e : 'error' };
        }
    }
    window.calcEval = calcEval;   // exposed for the node self-check

    // ── number formatting (display ~5 sig figs, copy full precision) ─────────────
    function fmt(v) {
        if (v === 0) return '0';
        var a = Math.abs(v);
        if (a >= 1e-4 && a < 1e7) {
            var s = parseFloat(v.toPrecision(6));
            return String(s);
        }
        return v.toExponential(5);
    }
    function num(id) {
        var el = document.getElementById(id);
        if (!el) return NaN;
        var v = String(el.value).trim().replace(/,/g, '');
        if (v === '') return NaN;
        var n = Number(v);
        return isFinite(n) ? n : NaN;
    }
    function setRes(id, val) {
        var el = document.getElementById(id);
        if (!el) return;
        if (val == null || !isFinite(val)) { el.textContent = '—'; el.dataset.raw = ''; }
        else { el.textContent = fmt(val); el.dataset.raw = String(val); }
    }
    function setInput(id, val, except) {
        if (except === id) return;
        var el = document.getElementById(id);
        if (el) el.value = (val == null || !isFinite(val)) ? '' : fmt(val);
    }

    // ── section recompute ───────────────────────────────────────────────────────
    function recompute1(fromAbs) {
        if (fromAbs) {
            var f = num('calc-s1-from'), t = num('calc-s1-to');
            if (isFinite(f) && isFinite(t)) {
                var dpEl = document.getElementById('calc-s1-dp');
                if (dpEl) dpEl.value = String(t - f);
            }
        }
        var dp = num('calc-s1-dp'), amp = num('calc-s1-amp');
        if (!isFinite(dp)) { setRes('calc-s1-k', null); setRes('calc-s1-anew', null); return; }
        var k = Math.pow(10, dp / 20);
        setRes('calc-s1-k', k);
        setRes('calc-s1-anew', isFinite(amp) ? amp * k : null);
    }
    function recompute2() {
        var fsp = num('calc-s2-fsp'), a = num('calc-s2-amp'), target = num('calc-s2-target');
        setRes('calc-s2-dbm', (isFinite(fsp) && isFinite(a) && a !== 0)
            ? fsp + 20 * Math.log10(Math.abs(a)) : null);
        setRes('calc-s2-anew', (isFinite(fsp) && isFinite(target))
            ? Math.pow(10, (target - fsp) / 20) : null);
    }
    function recompute3(role) {
        var R = num('calc-s3-r'); if (!isFinite(R) || R <= 0) R = 50;
        var Vrms;
        if (role === 'dbm' || role == null || role === 'r') {
            var dbm = num('calc-s3-dbm');
            if (!isFinite(dbm)) { setRes('calc-s3-pmw', null); setInput('calc-s3-vrms', null); setInput('calc-s3-vpk', null); setInput('calc-s3-vpp', null); return; }
            var Pmw = Math.pow(10, dbm / 10);
            Vrms = Math.sqrt((Pmw / 1000) * R);
            setRes('calc-s3-pmw', Pmw);
            setInput('calc-s3-vrms', Vrms); setInput('calc-s3-vpk', Math.SQRT2 * Vrms); setInput('calc-s3-vpp', 2 * Math.SQRT2 * Vrms);
        } else {
            var v = num('calc-s3-' + role);
            if (!isFinite(v) || v < 0) return;
            Vrms = role === 'vrms' ? v : role === 'vpk' ? v / Math.SQRT2 : v / (2 * Math.SQRT2);
            var Pw = Vrms * Vrms / R;
            var dbm2 = 10 * Math.log10(Pw * 1000);
            var dbmEl = document.getElementById('calc-s3-dbm');
            if (dbmEl) dbmEl.value = isFinite(dbm2) ? fmt(dbm2) : '';
            setRes('calc-s3-pmw', Pw * 1000);
            setInput('calc-s3-vrms', Vrms, 'calc-s3-' + role);
            setInput('calc-s3-vpk', Math.SQRT2 * Vrms, 'calc-s3-' + role);
            setInput('calc-s3-vpp', 2 * Math.SQRT2 * Vrms, 'calc-s3-' + role);
        }
    }
    function recomputeExpr() {
        var box = document.getElementById('calc-expr');
        var out = document.getElementById('calc-expr-res');
        if (!box || !out) return;
        var r = calcEval(box.value);
        if (r.ok) { out.textContent = fmt(r.value); out.dataset.raw = String(r.value); out.classList.remove('calc-err'); }
        else { out.textContent = box.value.trim() === '' ? '—' : ('⚠ ' + (r.err || '—')); out.dataset.raw = ''; out.classList.toggle('calc-err', !!r.err && r.err !== '—'); }
    }
    function recomputeAll() { recompute1(false); recompute2(); recompute3(null); recomputeExpr(); }

    // ── copy ────────────────────────────────────────────────────────────────────
    function copyFrom(target, btn) {
        var raw = target.dataset.raw || (target.value !== undefined ? target.value : '') || target.textContent;
        if (!raw || raw === '—') return;
        if (window.copyWithFeedback) window.copyWithFeedback(raw, btn);
        else if (navigator.clipboard) navigator.clipboard.writeText(raw);
    }

    // ── open / close / pin ──────────────────────────────────────────────────────
    var _calcWired = false, _calcInit = false;
    window.toggleCalc = function () {
        var pop = document.getElementById('calc-popover');
        var btn = document.getElementById('calc-btn');
        if (!pop || !btn) return;
        var willOpen = pop.classList.contains('calc-hidden');
        // singleton: never overlap the settings dropdown
        var sd = document.getElementById('settings-dropdown');
        if (sd) sd.classList.add('settings-hidden');
        pop.classList.toggle('calc-hidden', !willOpen);
        btn.classList.toggle('calc-open', willOpen);
        btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        if (willOpen) {
            if (!_calcInit) { recomputeAll(); _calcInit = true; }
            var first = document.getElementById('calc-s1-dp');
            if (first) setTimeout(function () { first.focus(); first.select && first.select(); }, 0);
            setTimeout(function () { document.addEventListener('click', _calcOutside); }, 0);
        } else {
            document.removeEventListener('click', _calcOutside);
            btn.focus();
        }
    };
    function _calcOutside(e) {
        var pop = document.getElementById('calc-popover');
        if (!pop || pop.classList.contains('calc-hidden')) return;
        if (pop.dataset.pinned === '1') return;          // pinned: ignore outside clicks
        if (pop.contains(e.target) || (e.target.closest && e.target.closest('.calc-btn'))) return;
        window.toggleCalc();
    }
    window.calcTogglePin = function () {
        var pop = document.getElementById('calc-popover');
        var pin = document.getElementById('calc-pin');
        if (!pop) return;
        var on = pop.dataset.pinned === '1';
        pop.dataset.pinned = on ? '0' : '1';
        if (pin) pin.setAttribute('aria-pressed', on ? 'false' : 'true');
    };

    // ── wiring ──────────────────────────────────────────────────────────────────
    function wire() {
        if (_calcWired) return;
        var pop = document.getElementById('calc-popover');
        if (!pop) return;
        _calcWired = true;
        pop.addEventListener('input', function (e) {
            var id = e.target.id || '';
            if (id === 'calc-s1-from' || id === 'calc-s1-to') recompute1(true);
            else if (id.indexOf('calc-s1-') === 0) recompute1(false);
            else if (id.indexOf('calc-s2-') === 0) recompute2();
            else if (id === 'calc-s3-vrms') recompute3('vrms');
            else if (id === 'calc-s3-vpk') recompute3('vpk');
            else if (id === 'calc-s3-vpp') recompute3('vpp');
            else if (id.indexOf('calc-s3-') === 0) recompute3('dbm');
            else if (id === 'calc-expr') recomputeExpr();
        });
        pop.addEventListener('click', function (e) {
            var cp = e.target.closest ? e.target.closest('.calc-copy') : null;
            if (cp && cp.dataset.copy) { e.preventDefault(); var t = document.getElementById(cp.dataset.copy); if (t) copyFrom(t, cp); return; }
            var use = e.target.closest ? e.target.closest('#calc-s1-use') : null;
            if (use) {
                e.preventDefault();
                var src = document.getElementById('calc-s1-anew');
                var dst = document.getElementById('calc-s2-amp');
                if (src && dst && src.dataset.raw) {
                    dst.value = src.dataset.raw; recompute2();
                    var sec2 = dst.closest('details'); if (sec2) sec2.open = true;
                }
            }
        });
        pop.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { e.preventDefault(); window.toggleCalc(); }
            else if (e.key === 'Enter' && e.target.id === 'calc-expr') {
                e.preventDefault();
                var out = document.getElementById('calc-expr-res');
                if (out) copyFrom(out, out);
            }
        });
        enableDrag();
    }

    // B3: float + drag the popover by its header. Anchored under the badge until the
    // first real drag (then position:fixed via .calc-floating); pin/close still click
    // (excluded from the drag), and toggleCalc / outside-click / Escape are unchanged.
    function enableDrag() {
        var pop = document.getElementById('calc-popover');
        var head = document.getElementById('calc-header');
        if (!pop || !head) return;
        var dragging = false, committed = false, sx = 0, sy = 0, ox = 0, oy = 0;
        function endDrag() {
            dragging = false; committed = false;
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', endDrag);
        }
        function commit() {
            // Float ONLY on a real drag — a plain header click stays anchored under the
            // badge (audit P2: mousedown used to snap it to position:fixed immediately).
            var r = pop.getBoundingClientRect();
            pop.classList.add('calc-floating');
            pop.style.left = r.left + 'px'; pop.style.top = r.top + 'px'; pop.style.width = r.width + 'px';
            ox = r.left; oy = r.top; committed = true;
        }
        function onMove(e) {
            if (!dragging) return;
            if (e.buttons === 0) { endDrag(); return; }   // missed mouseup (released over chrome) → self-heal
            if (!committed) {
                if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) < 4) return;  // click, not drag
                commit();
            }
            var w = pop.offsetWidth, h = pop.offsetHeight;
            var nx = ox + (e.clientX - sx), ny = oy + (e.clientY - sy);
            var maxX = window.innerWidth - w - 4, maxY = window.innerHeight - h - 4;
            nx = Math.max(4, Math.min(nx, Math.max(4, maxX)));
            ny = Math.max(4, Math.min(ny, Math.max(4, maxY)));
            pop.style.left = nx + 'px'; pop.style.top = ny + 'px';
        }
        head.addEventListener('mousedown', function (e) {
            if (e.button !== 0) return;
            if (e.target.closest && e.target.closest('.calc-header-tools')) return;  // pin/close stay clickable
            dragging = true; committed = false; sx = e.clientX; sy = e.clientY;
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', endDrag);
            e.preventDefault();
        });
        window.addEventListener('blur', function () { if (dragging) endDrag(); });
    }

    if (document.readyState === 'loading')
        document.addEventListener('DOMContentLoaded', wire);
    else wire();
})();
