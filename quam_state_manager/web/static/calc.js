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
 *
 * Two surfaces, one script (docs/156): the in-page popover (#calc-popover in
 * base.html — anchored, draggable, Alt+C) and the standalone /calc-window
 * document (#calc-popover.calc-standalone — the calculator as its OWN browser
 * window, opened by openCalcWindow). Every field is found by id, so both
 * render the same _calc_body.html partial.
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
    // ── RF = LO + IF (1.0-prep, docs/100) ───────────────────────────────────────
    // Pure 3-way solver so the node self-check can pin the math without a DOM.
    // Units are the daily convention: RF/LO in GHz, IF in MHz. `role` names the
    // field the user just edited; the OTHER known field decides which of the
    // remaining two is derived (edit RF with LO known → IF; with only IF known
    // → LO; etc.). Returns {rf, lo, if_} in those units (NaN = unknown).
    function calcSolveRfLoIf(role, rf, lo, if_) {
        var haveRf = isFinite(rf), haveLo = isFinite(lo), haveIf = isFinite(if_);
        if (role === 'rf' && haveRf) {
            if (haveLo) if_ = (rf - lo) * 1000;
            else if (haveIf) lo = rf - if_ / 1000;
        } else if (role === 'lo' && haveLo) {
            if (haveRf) if_ = (rf - lo) * 1000;
            else if (haveIf) rf = lo + if_ / 1000;
        } else if (role === 'if' && haveIf) {
            if (haveLo) rf = lo + if_ / 1000;
            else if (haveRf) lo = rf - if_ / 1000;
        }
        return { rf: rf, lo: lo, if_: if_ };
    }
    window.calcSolveRfLoIf = calcSolveRfLoIf;   // exposed for the node self-check

    function recompute4(role) {
        var s = calcSolveRfLoIf(role, num('calc-s4-rf'), num('calc-s4-lo'),
                                num('calc-s4-if'));
        var except = role ? 'calc-s4-' + role : null;
        setInput('calc-s4-rf', s.rf, except);
        setInput('calc-s4-lo', s.lo, except);
        setInput('calc-s4-if', s.if_, except);
        var note = document.getElementById('calc-s4-note');
        if (note) {
            if (!isFinite(s.if_)) { note.textContent = '—'; note.classList.remove('calc-err'); }
            else {
                var out = Math.abs(s.if_) > 400;
                note.textContent = fmt(s.if_) + ' MHz ' + (out
                    ? '⚠ outside the ±400 MHz window' : '· within ±400 MHz');
                note.classList.toggle('calc-err', out);
            }
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
    function recomputeAll() { recompute1(false); recompute2(); recompute3(null); recompute4(null); recomputeExpr(); }

    // ── copy ────────────────────────────────────────────────────────────────────
    function copyFrom(target, btn) {
        var raw = target.dataset.raw || (target.value !== undefined ? target.value : '') || target.textContent;
        if (!raw || raw === '—') return;
        if (window.copyWithFeedback) window.copyWithFeedback(raw, btn);
        else if (navigator.clipboard) {
            // the standalone window has no app.js toast: the button itself
            // says it happened (docs/156)
            navigator.clipboard.writeText(raw).then(function () {
                if (!btn || !btn.classList) return;
                btn.classList.add('calc-copied');
                setTimeout(function () { btn.classList.remove('calc-copied'); }, 800);
            }).catch(function () {});
        }
    }

    /* ── size (docs/141 4aj, user: "calculator는 크기 조절이 안되고 있어") ──
       The window is `resize: both` in CSS; this is the memory. Same contract
       as the Config Manual's (manual.js): only a size the USER set is stored,
       so opening on a smaller screen — where restore clamps to the viewport —
       never shrinks the remembered size. */
    var SIZE_KEY = 'quam_calc_size';
    function calcOpen() {
        var p = document.getElementById('calc-popover');
        return !!(p && !p.classList.contains('calc-hidden'));
    }
    function restoreSize(p) {
        p._calcApplied = null;
        try {
            var s = JSON.parse(window.localStorage.getItem(SIZE_KEY) || 'null');
            if (s && s.w > 200 && s.h > 150) {
                var vw = window.innerWidth || 0, vh = window.innerHeight || 0;
                var w = (vw > 240 ? Math.min(s.w, vw - 16) : s.w);
                var h = (vh > 190 ? Math.min(s.h, vh - 16) : s.h);
                p.style.width = w + 'px';
                p.style.height = h + 'px';
                p._calcApplied = { w: Math.round(w), h: Math.round(h) };
            }
        } catch (e) {}
    }
    function watchSize(p) {
        if (p._calcSized || !window.ResizeObserver) return;
        p._calcSized = true;
        var t = null;
        new ResizeObserver(function () {
            if (!calcOpen()) return;
            clearTimeout(t);
            t = setTimeout(function () {
                var w = p.offsetWidth, h = p.offsetHeight, a = p._calcApplied;
                if (a && Math.abs(a.w - w) < 2 && Math.abs(a.h - h) < 2) return;
                try { window.localStorage.setItem(SIZE_KEY, JSON.stringify({ w: w, h: h })); } catch (e) {}
            }, 250);
        }).observe(p);
    }
    window.CalcWindow = { restoreSize: restoreSize, watchSize: watchSize, SIZE_KEY: SIZE_KEY };

    /* ── a window of its own (docs/156) ──────────────────────────────────────
       User feedback: the popover floats, but only INSIDE the SM window. This
       opens the SAME calculator (/calc-window renders the same partial) as its
       own browser window — window.open with a size, which Chrome/Edge/Firefox
       answer with a popup WINDOW rather than a tab: movable across monitors,
       above other apps, and it outlives every navigation of the page that
       opened it. The page keeps ONE reference: while that window is alive the
       Calculator button and Alt+C FOCUS it instead of opening a second
       calculator in-page (two calculators with two sets of numbers is the
       confusing outcome). The window remembers its own size + screen position
       (quam_calc_win) and reopens there.

       Browser mode only. Under the desktop shell (pywebview) the WebView2
       backend answers window.open by navigating THIS window to the URL
       (edgechromium.py on_new_window_request: Handled=True, then load_url) —
       the whole app replaced by a calculator, with no way back. So there the
       button is hidden and openCalcWindow does nothing; the in-page floating
       popover stays the desktop answer. `window.pywebview` is injected by
       the shell AFTER navigation completes, so it is checked at click time
       (reliable) and the button is hidden on `pywebviewready` (cosmetic). */
    var WIN_URL_FALLBACK = '/calc-window';
    var WIN_NAME = 'quam-calc';
    var WIN_KEY = 'quam_calc_win';          // {w, h, x, y} the separate window last had
    var REQ_KEY = 'quam_calc_req';          // {w, h} the CONTENT size we last ASKED window.open for
    var WIN_DEFAULT = { w: 400, h: 680 };
    var _calcWin = null;

    // A calculator window this page did not open (it survived a full reload of
    // the SM page, so `_calcWin` is gone) still answers on the channel --
    // `_extAlive` is what the page knows about it (code-review round 2, F11).
    // Without it the ↗ ping focused the live window while every OTHER entry
    // point (Alt+C, the sidebar button) still believed no window existed and
    // opened a SECOND calculator beside it.
    var _extAlive = false, _extCh = null;
    function calcWinAlive() {
        try { return _extAlive || !!(_calcWin && !_calcWin.closed); } catch (e) { return _extAlive; }
    }
    function standalone() {
        var p = document.getElementById('calc-popover');
        return !!(p && p.classList.contains('calc-standalone'));
    }
    function winFeatures() {
        var s = null;
        try { s = JSON.parse(window.localStorage.getItem(WIN_KEY) || 'null'); } catch (e) {}
        var w = (s && s.w > 200) ? s.w : WIN_DEFAULT.w;
        var h = (s && s.h > 150) ? s.h : WIN_DEFAULT.h;
        // F-CALC-GROW: record the CONTENT size we are asking for, so the window
        // can measure this browser's frame overhead (inner - requested) once and
        // store back a size that reproduces the same content next time, instead
        // of feeding its realised (larger) inner size back as the next request.
        try { window.localStorage.setItem(REQ_KEY, JSON.stringify({ w: w, h: h })); } catch (e) {}
        var f = 'popup=yes,width=' + Math.round(w) + ',height=' + Math.round(h)
              + ',resizable=yes,scrollbars=yes';
        if (s && isFinite(s.x) && isFinite(s.y)) f += ',left=' + Math.round(s.x) + ',top=' + Math.round(s.y);
        return f;
    }
    function winUrl(trigger) {
        var el = (trigger && trigger.dataset && trigger.dataset.calcWindowUrl)
            ? trigger : document.querySelector('[data-calc-window-url]');
        var url = (el && el.dataset.calcWindowUrl) || WIN_URL_FALLBACK;
        // the OPENING page's theme, even when it was forced by ?theme= and
        // never persisted — the window should look like the page it came from
        var theme = document.documentElement.getAttribute('data-theme');
        if (theme) url += (url.indexOf('?') < 0 ? '?' : '&') + 'theme=' + encodeURIComponent(theme);
        return url;
    }
    // The window announces itself on a BroadcastChannel (docs/156 review):
    // after a full reload of the SM page `_calcWin` is gone, and a
    // window.open on the same NAME would NAVIGATE the still-open window --
    // wiping every value the user typed. So the page first asks "anyone
    // there?"; a live window answers and focuses itself, and only silence
    // opens a new one.
    var CH_NAME = 'quam-calc';
    function _channel() {
        try { return window.BroadcastChannel ? new BroadcastChannel(CH_NAME) : null; }
        catch (e) { return null; }
    }
    // ONE long-lived listener per page: the window announces itself (`calc-here`,
    // answered to a ping or a silent probe) and says goodbye when it closes
    // (`calc-bye`), so `_extAlive` stays true only while a window really is there.
    function _extListen() {
        if (_extCh || standalone()) return;
        _extCh = _channel();
        if (!_extCh) return;
        _extCh.onmessage = function (ev) {
            var d = ev && ev.data;
            if (!d) return;
            if (d.type === 'calc-here') _extAlive = true;
            else if (d.type === 'calc-bye') _extAlive = false;
        };
    }
    // asked once at page load: unlike `calc-ping` it must NOT pull the window
    // to the front -- a page reload is not a request to see the calculator.
    function _extProbe() {
        _extListen();
        if (!_extCh) return;
        try { _extCh.postMessage({ type: 'calc-probe' }); } catch (e) {}
    }
    function _openNew(trigger) {
        var w = null;
        try { w = window.open(winUrl(trigger), WIN_NAME, winFeatures()); } catch (e) { w = null; }
        if (!w) return null;                 // popup blocked: the in-page popover stays
        if (calcOpen()) window.toggleCalc();  // it moved out — close the in-page one first
        _calcWin = w;
        try { w.focus(); } catch (e) {}
        return w;
    }
    // Bring a window this page did NOT open to the front; if nothing answers,
    // it is gone -- clear the flag and let the caller act as if there were none.
    function _focusExternal(onGone) {
        var ch = _channel();
        if (!ch) { _extAlive = false; if (onGone) onGone(); return; }
        var answered = false;
        var timer = setTimeout(function () {
            try { ch.close(); } catch (e) {}
            if (answered) return;
            _extAlive = false;
            if (onGone) onGone();
        }, 200);
        ch.onmessage = function (ev) {
            if (ev && ev.data && ev.data.type === 'calc-here') answered = true;
        };
        try { ch.postMessage({ type: 'calc-ping' }); }
        catch (e) { clearTimeout(timer); _extAlive = false; if (onGone) onGone(); }
    }
    window.openCalcWindow = function (trigger) {
        // only a window THIS page opened can be focused directly; an external
        // one is reached through the ping below (F11)
        if (_calcWin && !_calcWin.closed) { try { _calcWin.focus(); } catch (e) {} return _calcWin; }
        if (window.pywebview) return null;   // desktop shell — see the note above
        _extListen();
        var ch = _channel();
        if (!ch) return _openNew(trigger);
        var answered = false;
        var timer = setTimeout(function () {
            if (answered) return;
            try { ch.close(); } catch (e) {}
            _openNew(trigger);
        }, 120);
        ch.onmessage = function (ev) {
            if (!ev || !ev.data || ev.data.type !== 'calc-here') return;
            answered = true;
            clearTimeout(timer);
            try { ch.close(); } catch (e) {}
            // the page now KNOWS a window is out there (F11) — Alt+C and the
            // sidebar button must bring that one forward, not open a second
            _extAlive = true;
            if (calcOpen()) window.toggleCalc();
        };
        try { ch.postMessage({ type: 'calc-ping' }); } catch (e) { clearTimeout(timer); return _openNew(trigger); }
        return null;                         // asynchronous: the answer decides
    };
    function hidePopout() {
        document.querySelectorAll('.calc-popout').forEach(function (b) { b.hidden = true; });
    }
    // (guarded: calc_selfcheck.cjs evaluates this file against a bare `{}` window)
    if (window.addEventListener) window.addEventListener('pywebviewready', hidePopout);

    // The standalone document: the window IS the frame, so no anchoring, no
    // drag, no outside-click closer; Escape closes the window; the size and
    // screen position are remembered for the next open.
    function wireStandalone() {
        if (!_calcInit) { recomputeAll(); _calcInit = true; }
        var first = document.getElementById('calc-s1-dp');
        if (first) setTimeout(function () { first.focus(); first.select && first.select(); }, 0);
        var t = null;
        // F-CALC-GROW: measure the frame overhead ONCE at load -- how much
        // bigger the realised inner size is than the content size we asked
        // window.open for. remember() then stores (inner - overhead), the size
        // that reproduces the current content, so a pure open/close cycle stores
        // the SAME size and only a genuine user resize moves it. Clamped to the
        // screen so a maximised window can never seed an off-screen next open.
        var _req = null;
        try { _req = JSON.parse(window.localStorage.getItem(REQ_KEY) || 'null'); } catch (e) {}
        var _frameDW = (_req && _req.w > 0) ? (window.innerWidth - _req.w) : 0;
        var _frameDH = (_req && _req.h > 0) ? (window.innerHeight - _req.h) : 0;
        function remember() {
            try {
                var sw = (window.screen && window.screen.availWidth) || 100000;
                var sh = (window.screen && window.screen.availHeight) || 100000;
                window.localStorage.setItem(WIN_KEY, JSON.stringify({
                    w: Math.max(200, Math.min(sw, window.innerWidth - _frameDW)),
                    h: Math.max(150, Math.min(sh, window.innerHeight - _frameDH)),
                    x: window.screenX, y: window.screenY }));
            } catch (e) {}
        }
        window.addEventListener('resize', function () { clearTimeout(t); t = setTimeout(remember, 250); });
        window.addEventListener('pagehide', remember);
        // answer the opener page's "anyone there?" (see openCalcWindow) and
        // come to the front; follow the page's theme toggle via storage
        var ch = _channel();
        if (ch) {
            ch.onmessage = function (ev) {
                var d = ev && ev.data;
                if (!d || (d.type !== 'calc-ping' && d.type !== 'calc-probe')) return;
                try { ch.postMessage({ type: 'calc-here' }); } catch (e) {}
                // a PROBE is the opener page asking on load whether a window
                // exists (F11) — answering must not steal the user's focus
                if (d.type === 'calc-ping') { try { window.focus(); } catch (e) {} }
            };
            // F-CALC-DUP (final review): announce ourselves ONCE on open, so any
            // SM tab that was ALREADY open when this window opened latches
            // _extAlive=true now (a page only ASKS once, at its own load) --
            // otherwise those tabs keep _extAlive=false and their Calculator
            // button / Alt+C open a SECOND in-page calculator beside the window.
            try { ch.postMessage({ type: 'calc-here' }); } catch (e) {}
            // ... and say goodbye, so the page stops believing in a window
            // the user closed
            window.addEventListener('pagehide', function () {
                try { ch.postMessage({ type: 'calc-bye' }); } catch (e) {}
            });
        }
        window.addEventListener('storage', function (ev) {
            if (!ev || ev.key !== 'quam_theme') return;
            var th = ev.newValue === 'light' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', th);
        });
    }

    // ── open / close / pin ──────────────────────────────────────────────────────
    var _calcWired = false, _calcInit = false;
    window.toggleCalc = function (trigger) {
        var pop = document.getElementById('calc-popover');
        // docs/89: two possible triggers (sidebar row / collapsed-sidebar topbar
        // fallback) — act on the one that is actually rendered.
        var btn = (window._toolTrigger
            ? window._toolTrigger('.calc-btn', trigger)
            : document.getElementById('calc-btn'));
        if (!pop || !btn) return;
        var willOpen = pop.classList.contains('calc-hidden');
        // docs/156: the calculator is OUT in its own window — bring that one
        // forward rather than open a second calculator here
        if (willOpen && calcWinAlive()) {
            // F-CALC-DEAD (final review): a CLOSED handle must fall through to
            // _focusExternal (which heals a stale _extAlive and reopens in-page)
            // -- focus() on a closed window is a silent no-op, so `if (_calcWin)`
            // alone left the Calculator button dead after the window crashed /
            // was discarded while _extAlive was still latched true.
            if (_calcWin && !_calcWin.closed) { try { _calcWin.focus(); } catch (e) {} return; }
            // an EXTERNAL window (F11), or a closed local handle: ping it
            // forward, and if it turns out to be gone, open here after all
            // rather than doing nothing
            _focusExternal(function () { window.toggleCalc(trigger); });
            return;
        }
        // docs/141 4u (user: "a bug"): the Calculator and Settings are two
        // windows, not a singleton -- opening one leaves the other alone
        pop.classList.toggle('calc-hidden', !willOpen);
        document.querySelectorAll('.calc-btn').forEach(function (b) {
            b.classList.toggle('calc-open', willOpen);
            b.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        });
        if (willOpen) {
            // the remembered size is applied BEFORE anchoring, so the anchor
            // math sees the real box (4aj)
            restoreSize(pop);
            watchSize(pop);
            // A popover the user DRAGGED keeps where they put it; otherwise
            // re-anchor to the trigger (which side it opens from can change
            // when the sidebar collapses).
            if (!pop.classList.contains('calc-floating') && window._anchorPopover) {
                window._anchorPopover(pop, btn);
            }
        }
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
    // docs/89: the calculator had no shortcut at all (Escape only closed it),
    // which is part of why it went unused. Alt+C avoids Ctrl+K (palette) and
    // every browser Ctrl+<letter>; it is ignored while typing in a field so it
    // can never eat an Alt-combination inside an input.
    document.addEventListener('keydown', function (e) {
        if (!e.altKey || e.ctrlKey || e.metaKey) return;
        if ((e.key || '').toLowerCase() !== 'c') return;
        var t = e.target;
        var tag = t && t.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
            || (t && t.isContentEditable)) return;
        e.preventDefault();
        window.toggleCalc();
    });
    function _calcOutside(e) {
        var pop = document.getElementById('calc-popover');
        if (!pop || pop.classList.contains('calc-hidden')) return;
        // A DRAGGED calculator is an implicit "keep it around" (docs/126 ⑥ —
        // the explicit pin button was removed as unrecognisable; dragging is
        // the gesture that actually communicated the intent).
        if (pop.classList.contains('calc-floating')) return;
        if (pop.contains(e.target) || (e.target.closest && e.target.closest('.calc-btn'))) return;
        // docs/141 4u: a click on ANOTHER TOOL WINDOW (its button or its body)
        // is not "outside" -- that click is what used to make the Calculator
        // vanish. 4ac: the list was the literal pair Settings+Calculator, so
        // the Config Manual -- the third window of the same section -- still
        // closed this one. FloatPanel.TOOLS_SEL is the one place they are named.
        var _tools = (window.FloatPanel && window.FloatPanel.TOOLS_SEL)
            || '.settings-btn, #settings-dropdown, .manual-btn, #manual-popover';
        if (e.target.closest && e.target.closest(_tools)) return;
        window.toggleCalc();
    }
    // (docs/126 ⑥: the pin button was removed on customer request — nobody
    // recognised it and dragging already keeps the calculator around.)

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
            else if (id === 'calc-s4-rf') recompute4('rf');
            else if (id === 'calc-s4-lo') recompute4('lo');
            else if (id === 'calc-s4-if') recompute4('if');
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
            if (e.key === 'Escape') {
                e.preventDefault();
                // docs/156: in the standalone window Escape closes the WINDOW
                if (standalone()) { try { window.close(); } catch (e2) {} }
                else window.toggleCalc();
            }
            else if (e.key === 'Tab' && e.target.matches &&
                     e.target.matches('input.calc-in, input.calc-expr')) {
                // Field-to-field hop: visible calc inputs only — skip the
                // summaries / hover-reveal copy buttons between them, and the
                // inputs inside closed <details> sections (the popover's only
                // hide mechanism). Wraps (Escape closes; Shift+Tab reverses).
                var ins = Array.prototype.slice.call(
                    pop.querySelectorAll('input.calc-in, input.calc-expr')
                ).filter(function (el) { return !el.closest('details:not([open])'); });
                var i = ins.indexOf(e.target);
                if (i < 0 || !ins.length) return;
                e.preventDefault();
                var nxt = ins[(i + (e.shiftKey ? -1 : 1) + ins.length) % ins.length];
                nxt.focus();
                if (nxt.select) nxt.select();
            }
            else if (e.key === 'Enter' && e.target.id === 'calc-expr') {
                e.preventDefault();
                var out = document.getElementById('calc-expr-res');
                if (out) copyFrom(out, out);
            }
        });
        if (standalone()) { wireStandalone(); return; }   // docs/156: the window is the frame
        if (window.pywebview) hidePopout();
        // F11: a calculator window can outlive this page's load — ask, quietly
        _extProbe();
        enableDrag();
    }

    // B3: float + drag the popover by its header. Anchored under the badge until the
    // first real drag (then position:fixed via .calc-floating); pin/close still click
    // (excluded from the drag), and toggleCalc / outside-click / Escape are unchanged.
    function enableDrag() {
        // docs/141 4u: the drag lives in float-panel.js (one core for the
        // Calculator, Settings and the Config Manual); calc-floating stays
        // the class the outside-click exemption and the CSS key on
        var pop = document.getElementById('calc-popover');
        var head = document.getElementById('calc-header');
        if (!pop || !head || !window.FloatPanel) return;
        window.FloatPanel.drag(pop, { handle: head, tools: '.calc-header-tools', floatClass: 'calc-floating' });
        // docs/165 (user): every edge, not just the bottom-right corner
        if (window.FloatPanel.resize) window.FloatPanel.resize(pop, { floatClass: 'calc-floating' });
    }

    if (document.readyState === 'loading')
        document.addEventListener('DOMContentLoaded', wire);
    else wire();
})();
