/* project-time.js -- the project's time zone and the run clock (docs/263).
 *
 * 1. The landing's zone picker (#landing-tz, above the env picker): a
 *    searchable list of IANA zones, each with its CURRENT UTC offset (DST is
 *    Intl's). It follows the project the env picker shows. On a pick SM
 *    compares at once:
 *      - the PC's zone (GET /project-time/clock, fetched after the page
 *        renders -- never on the render) vs the chosen one: when they differ
 *        a popup asks "is the PC's zone wrong, or view in <city> time?";
 *      - then the watch check: "now HH:MM in <zone> -- does it match your
 *        watch?", with this PC's time-sync status.
 *    The pick is saved per project (POST /project-time/zone); later launches
 *    only show it. A new project shows the last project's zone.
 * 2. ONE zone: SnapTime reads <html data-sm-zone> (app.js). A saved zone is
 *    applied to the page at once, and the old per-browser choice (quam_tz)
 *    is retired the first time a project zone is saved.
 * 3. The run-clock question, on any page: GET /project-time/status after
 *    load and when a run lands (sm:runs-changed). When the server says
 *    auto_ask, the question goes up ONCE (POST /project-time/skew-shown);
 *    after that it waits on Diagnostics ("Answer...").
 * 4. Run times (.ts-run): the instant in the viewer's zone; the run's own
 *    recorded clock in the tooltip; a correction -- only after the person
 *    said the experiment PC's clock is wrong -- labelled, original kept.
 */
(function () {
    'use strict';
    if (typeof window === 'undefined' || window.ProjectTime) return;
    var LEGACY_KEY = 'quam_tz';

    /* ------------------------------------------------ pure zone helpers */
    var _zones = null;
    function zones() {
        if (_zones) return _zones;
        var list = [];
        try {
            if (typeof Intl.supportedValuesOf === 'function') list = Intl.supportedValuesOf('timeZone').slice();
        } catch (e) { list = []; }
        if (!list.length) {
            list = ['Asia/Seoul', 'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Kolkata', 'Asia/Jerusalem',
                    'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'America/New_York',
                    'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Australia/Sydney'];
        }
        if (list.indexOf('UTC') < 0) list.unshift('UTC');
        _zones = list;
        return list;
    }
    var _offFmt = {};
    /* The zone's offset from UTC at *date*, in minutes (east positive), or
       null for a zone the browser rejects. */
    function offsetMinutes(zone, date) {
        date = date || new Date();
        if (!zone) return -date.getTimezoneOffset();
        if (zone === 'UTC') return 0;
        var f = _offFmt[zone];
        if (f === undefined) {
            try {
                f = new Intl.DateTimeFormat('en-US', { timeZone: zone, year: 'numeric', month: '2-digit',
                    day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
            } catch (e) { f = null; }
            _offFmt[zone] = f;
        }
        if (!f) return null;
        var p = {};
        f.formatToParts(date).forEach(function (x) { p[x.type] = x.value; });
        var hour = p.hour === '24' ? 0 : +p.hour;
        var asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, hour, +p.minute, +p.second);
        return Math.round((asUtc - Math.floor(date.getTime() / 1000) * 1000) / 60000);
    }
    function offsetText(mins) {
        if (mins == null) return '';
        if (!mins) return 'UTC';
        var a = Math.abs(mins), h = Math.floor(a / 60), m = a % 60;
        return 'UTC' + (mins < 0 ? '-' : '+') + h + (m ? ':' + (m < 10 ? '0' : '') + m : '');
    }
    /* "+09:00" -> 540 */
    function isoOffsetMinutes(off) {
        var m = /^([+-])(\d{2}):(\d{2})$/.exec(String(off || ''));
        if (!m) return null;
        var v = (+m[2]) * 60 + (+m[3]);
        return m[1] === '-' ? -v : v;
    }
    function spanText(mins) {
        var a = Math.abs(Math.round(mins)), h = Math.floor(a / 60), m = a % 60;
        if (h && m) return h + ' h ' + m + ' min';
        if (h) return h + ' h';
        return m + ' min';
    }
    function secondsText(s) {
        var a = Math.round(Math.abs(s)), h = Math.floor(a / 3600), m = Math.floor((a % 3600) / 60), sec = a % 60;
        if (h) return h + ' h ' + (m < 10 ? '0' : '') + m + ' min';
        if (m) return m + ' min' + (sec ? ' ' + (sec < 10 ? '0' : '') + sec + ' s' : '');
        return sec + ' s';
    }
    function cityOf(zone) {
        if (!zone) return 'this PC';
        var parts = String(zone).split('/');
        return parts[parts.length - 1].replace(/_/g, ' ');
    }
    /* "HH:MM" of *date* in *zone* (the zone's own wall clock). */
    function hhmm(zone, date) {
        var mins = offsetMinutes(zone, date);
        if (mins == null) return '';
        var d = new Date(date.getTime() + mins * 60000);
        var h = d.getUTCHours(), m = d.getUTCMinutes();
        return (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m;
    }
    /* A wall clock in a fixed offset: "2026-09-30 01:55:12" of an ISO
       instant read in offset *mins*. */
    function wallInOffset(isoUtc, mins) {
        var t = Date.parse(isoUtc);
        if (isNaN(t) || mins == null) return '';
        var d = new Date(t + mins * 60000);
        function p2(n) { return (n < 10 ? '0' : '') + n; }
        return d.getUTCFullYear() + '-' + p2(d.getUTCMonth() + 1) + '-' + p2(d.getUTCDate()) + ' '
            + p2(d.getUTCHours()) + ':' + p2(d.getUTCMinutes()) + ':' + p2(d.getUTCSeconds());
    }
    /* Zones matching *query*: words match the name (a "_" reads as a space),
       an offset ("+9", "UTC-7", "-3:30") matches the current offset. City
       prefix hits first. At most *limit* (12). */
    function search(query, date, limit) {
        date = date || new Date();
        limit = limit || 12;
        var q = String(query || '').trim().toLowerCase();
        var offQ = null;
        var m = /^(?:utc|gmt)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$/.exec(q);
        if (m) offQ = (m[1] === '-' ? -1 : 1) * ((+m[2]) * 60 + (+(m[3] || 0)));
        if (q === 'utc' || q === 'gmt') offQ = 0;
        var words = q.replace(/_/g, ' ').split(/\s+/).filter(Boolean);
        var out = [];
        zones().forEach(function (z) {
            var name = z.toLowerCase().replace(/_/g, ' ');
            var mins = offsetMinutes(z, date);
            if (mins == null) return;
            var hit, rank = 2;
            if (offQ != null) {
                hit = mins === offQ;
                rank = 1;
            } else if (!words.length) {
                hit = true;
            } else {
                hit = words.every(function (w) { return name.indexOf(w) >= 0; });
                if (hit && cityOf(z).toLowerCase().indexOf(words[0]) === 0) rank = 0;
                else if (hit && name.indexOf(words[0]) === 0) rank = 1;
            }
            if (hit) out.push({ zone: z, mins: mins, text: offsetText(mins), rank: rank });
        });
        out.sort(function (a, b) { return a.rank - b.rank || a.zone.localeCompare(b.zone); });
        return out.slice(0, limit);
    }

    /* ----------------------------------------------------- server calls */
    function form(data) {
        return Object.keys(data).filter(function (k) { return data[k] != null; })
            .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(String(data[k])); })
            .join('&');
    }
    function post(url, data) {
        return fetch(url, { method: 'POST', credentials: 'same-origin',
                            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                            body: form(data) })
            .then(function (r) {
                return r.json().then(function (j) { j = j || {}; j._status = r.status; return j; },
                                     function () { return { ok: false, _status: r.status }; });
            });
    }
    var _clock = null, _clockAt = 0, _clockP = null;
    function clockStatus(refresh) {
        if (!_clockP || refresh) {
            _clockP = fetch('/project-time/clock' + (refresh ? '?refresh=1' : ''),
                            { credentials: 'same-origin', cache: 'no-store' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) { if (d) { _clock = d; _clockAt = Date.now(); } return d; },
                      function () { return null; });
        }
        return _clockP;
    }
    /* This SERVER's clock now (the one SM stamps its records with). */
    function serverNow() {
        if (_clock && typeof _clock.now_ms === 'number') return new Date(_clock.now_ms + (Date.now() - _clockAt));
        return new Date();
    }

    /* ---------------------------------------------------------- dialogs */
    /* Pico draws a <dialog> as the full-screen overlay and centres its
       <article> as the box: content goes in the article (returned here). */
    function dialog(id, cls) {
        var d = document.getElementById(id);
        if (!d) {
            d = document.createElement('dialog');
            d.id = id;
            d.className = 'pt-dialog ' + (cls || '');
            d.setAttribute('aria-modal', 'true');
            d.appendChild(el('article', 'pt-card'));
            document.body.appendChild(d);
            d.addEventListener('cancel', function () { stopTick(); });
        }
        return d.querySelector('.pt-card');
    }
    function dlgOf(card) { return card && card.closest ? (card.closest('dialog') || card) : card; }
    function show(card) {
        var d = dlgOf(card);
        if (d.open) return;
        if (typeof d.showModal === 'function') {
            try { d.showModal(); return; } catch (e) { /* not connected / already open */ }
        }
        d.setAttribute('open', '');
    }
    function hide(card) {
        stopTick();
        var d = dlgOf(card);
        if (!d) return;
        if (typeof d.close === 'function') { try { d.close(); } catch (e) { /* closed */ } }
        d.removeAttribute('open');
    }
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    function button(label, cls, onClick, attrs) {
        var b = el('button', 'btn-sm ' + (cls || ''), label);
        b.type = 'button';
        Object.keys(attrs || {}).forEach(function (k) { b.setAttribute(k, attrs[k]); });
        b.addEventListener('click', onClick);
        return b;
    }
    var _tick = null;
    function stopTick() { if (_tick) { clearInterval(_tick); _tick = null; } }

    /* ------------------------------------------------- landing picker */
    var _views = {};
    var _project = null;
    function root() { return document.getElementById('landing-tz'); }
    function q(sel) { var r = root(); return r ? r.querySelector(sel) : null; }
    function legacyZone() {
        try { return window.localStorage.getItem(LEGACY_KEY) || ''; } catch (e) { return ''; }
    }
    function viewOf(project) { return _views[project] || { zone: null, state: 'none' }; }

    function renderCurrent() {
        var r = root();
        if (!r || !_project) return;
        var v = viewOf(_project);
        var nm = q('[data-tz-project]');
        if (nm) nm.textContent = _project;
        var cur = q('[data-tz-current]'), st = q('[data-tz-state]');
        var now = serverNow();
        if (v.zone) {
            var mins = offsetMinutes(v.zone, now);
            if (cur) cur.textContent = v.zone + ' · ' + offsetText(mins) + ' · now ' + hhmm(v.zone, now);
        } else if (cur) {
            var lz = legacyZone();
            var pcOff = isoOffsetMinutes(_clock && _clock.os_zone && _clock.os_zone.utc_offset);
            cur.textContent = lz ? lz + ' · ' + offsetText(offsetMinutes(lz, now)) + ' (this browser)'
                : (pcOff != null ? offsetText(pcOff) + ' (this PC)' : 'this PC’s zone');
        }
        if (st) {
            var label = { picked: 'saved', default: 'default from the last project',
                          suggested: 'default' + (v.from_project ? ' from ' + v.from_project : '') + ' — used when opened',
                          none: 'not set — pick one' }[v.state] || '';
            st.textContent = label;
            st.setAttribute('data-state', v.state || 'none');
        }
        var note = q('[data-tz-note]');
        if (note) { note.textContent = v.note || ''; note.hidden = !v.note; }
    }
    function renderClockLine() {
        var line = q('[data-tz-clock]');
        if (!line) return;
        if (!_clock) { line.textContent = 'This PC’s clock: unknown'; return; }
        var off = isoOffsetMinutes(_clock.os_zone && _clock.os_zone.utc_offset);
        line.textContent = 'This PC: ' + (off == null ? 'zone unknown' : offsetText(off))
            + (_clock.os_zone && _clock.os_zone.iana ? ' (' + _clock.os_zone.iana + ')' : '')
            + ' · ' + (_clock.ntp_text || 'Time sync: unknown');
        line.title = (_clock.ntp && _clock.ntp.detail) || '';
        line.setAttribute('data-ntp', _clock.ntp && _clock.ntp.synced === true ? 'on'
            : _clock.ntp && _clock.ntp.synced === false ? 'off' : 'unknown');
    }
    function setProject(p) {
        if (!p) return;
        _project = p;
        var s = q('[data-tz-search]');
        if (s) s.hidden = true;
        renderCurrent();
    }
    function readViews() {
        var holder = q('[data-tz-views]');
        try { _views = JSON.parse(holder ? holder.textContent : '{}') || {}; } catch (e) { _views = {}; }
    }
    function boot() {
        var r = root();
        if (!r || r.getAttribute('data-tz-booted')) return;
        r.setAttribute('data-tz-booted', '1');
        readViews();
        setProject(_project && _views[_project] ? _project : r.getAttribute('data-tz-default'));
        // the clock status AFTER the render: a slow w32tm never holds the page
        clockStatus().then(function () { renderClockLine(); renderCurrent(); });
        if (!r._tzTimer) {
            r._tzTimer = setInterval(function () {
                if (!document.body.contains(r)) { clearInterval(r._tzTimer); return; }
                renderCurrent();
            }, 20000);
        }
    }

    function openSearch() {
        var s = q('[data-tz-search]');
        if (!s) return;
        s.hidden = false;
        var input = q('[data-tz-input]');
        if (input) {
            input.value = '';
            try { input.focus(); } catch (e) { /* jsdom */ }
        }
        renderList('');
    }
    function renderList(query) {
        var list = q('[data-tz-list]');
        if (!list) return;
        list.innerHTML = '';
        var now = serverNow();
        var hits = search(query, now);
        var cur = viewOf(_project).zone;
        var lz = !query && !cur ? legacyZone() : '';
        if (lz && !hits.some(function (h) { return h.zone === lz; })) {
            var lm = offsetMinutes(lz, now);
            if (lm != null) hits.unshift({ zone: lz, mins: lm, text: offsetText(lm) });
        }
        if (!hits.length) {
            list.appendChild(el('p', 'muted landing-tz-empty', 'No zone matches “' + query + '”.'));
            return;
        }
        hits.forEach(function (h) {
            var row = el('button', 'landing-tz-row');
            row.type = 'button';
            row.setAttribute('role', 'option');
            row.setAttribute('data-zone', h.zone);
            if (h.zone === cur) { row.classList.add('selected'); row.setAttribute('aria-selected', 'true'); }
            row.appendChild(el('span', 'landing-tz-name', h.zone));
            row.appendChild(el('span', 'landing-tz-off', h.text));
            row.appendChild(el('span', 'landing-tz-now muted', hhmm(h.zone, now)));
            if (h.zone === lz) row.appendChild(el('span', 'landing-tz-tag muted', 'your earlier choice'));
            row.addEventListener('click', function () { pick(h.zone); });
            list.appendChild(row);
        });
    }

    /* The pick: compare with the PC's zone at once (asking when they
       differ), save, then the watch check. */
    function pick(zone) {
        var project = _project;
        if (!project || !zone) return Promise.resolve(null);
        return clockStatus().then(function (st) {
            renderClockLine();
            var now = serverNow();
            var chosen = offsetMinutes(zone, now);
            var pc = isoOffsetMinutes(st && st.os_zone && st.os_zone.utc_offset);
            if (pc != null && chosen != null && pc !== chosen) {
                return askPcZone(project, zone, chosen, pc, st);
            }
            return save(project, zone, null).then(function (ok) { if (ok) watchCheck(project, zone, null); return ok; });
        });
    }
    function askPcZone(project, zone, chosen, pc, st) {
        var d = dialog('pt-zone-dialog', 'pt-zone');
        d.innerHTML = '';
        d.appendChild(el('h3', 'pt-title', 'This PC is in another zone'));
        var iana = st && st.os_zone && st.os_zone.iana;
        d.appendChild(el('p', 'pt-text', 'This PC is ' + offsetText(pc) + (iana ? ' (' + iana + ')' : '')
            + '; you chose ' + zone + ', ' + offsetText(chosen) + ' — ' + spanText(chosen - pc) + ' apart.'));
        var browser = -new Date().getTimezoneOffset();
        if (browser !== pc && browser !== chosen) {
            d.appendChild(el('p', 'pt-text muted', 'This browser is ' + offsetText(browser) + '.'));
        }
        d.appendChild(el('p', 'pt-question', 'Is the PC’s zone wrong, or do you want to view times in '
            + cityOf(zone) + ' time?'));
        var row = el('div', 'pt-actions');
        var p = new Promise(function (resolve) {
            row.appendChild(button('View in ' + cityOf(zone) + ' time', 'primary', function () {
                hide(d);
                save(project, zone, { answer: 'view', os_offset: st.os_zone.utc_offset, os_iana: iana })
                    .then(function (ok) { if (ok) watchCheck(project, zone, null); resolve(ok); });
            }, { 'data-pt-answer': 'view' }));
            row.appendChild(button('The PC’s zone is wrong', 'outline', function () {
                hide(d);
                save(project, zone, { answer: 'pc_wrong', os_offset: st.os_zone.utc_offset, os_iana: iana })
                    .then(function (ok) {
                        if (ok) watchCheck(project, zone, 'Fix the PC’s zone: Settings › Time & language › '
                            + 'Date & time › Time zone. Runs already recorded keep their own offsets.');
                        resolve(ok);
                    });
            }, { 'data-pt-answer': 'pc_wrong' }));
            row.appendChild(button('Cancel', 'secondary outline', function () { hide(d); resolve(false); },
                                   { 'data-pt-answer': 'cancel' }));
        });
        d.appendChild(row);
        show(d);
        return p;
    }
    function save(project, zone, osCheck) {
        var data = { project: project, zone: zone };
        if (osCheck) { data.os_answer = osCheck.answer; data.os_offset = osCheck.os_offset; data.os_iana = osCheck.os_iana; }
        return post('/project-time/zone', data).then(function (j) {
            if (!j || !j.ok) {
                var cur = q('[data-tz-current]');
                if (cur) cur.textContent = 'could not save: ' + ((j && j.error) || 'error');
                return false;
            }
            _views[project] = Object.assign({}, _views[project] || {}, j.view);
            // projects that were only offered the last zone now offer this one
            Object.keys(_views).forEach(function (n) {
                if (n !== project && _views[n].state === 'suggested') {
                    _views[n] = Object.assign({}, _views[n], { zone: zone, from_project: project });
                }
            });
            try { window.localStorage.removeItem(LEGACY_KEY); } catch (e) { /* blocked storage */ }
            applyPageZone(j.display);
            updateCards();
            var s = q('[data-tz-search]');
            if (s) s.hidden = true;
            renderCurrent();
            return true;
        }, function () { return false; });
    }
    /* The zone this page renders in, at once (the next full render stamps it). */
    function applyPageZone(display) {
        var z = display && display.zone;
        if (!z) return;
        if (window.SnapTime && window.SnapTime.zone() === z) return;
        if (typeof window.setDisplayZone === 'function') window.setDisplayZone(z);
        else if (window.SnapTime) window.SnapTime.setZone(z);
        refreshRunTimes();
    }
    function updateCards() {
        var now = serverNow();
        var cards = document.querySelectorAll('[data-tz-card]');
        for (var i = 0; i < cards.length; i++) {
            var v = _views[cards[i].getAttribute('data-tz-card')];
            if (!v || !v.zone) continue;
            cards[i].textContent = v.zone === 'UTC' ? 'tz UTC'
                : 'tz ' + cityOf(v.zone) + ' ' + offsetText(offsetMinutes(v.zone, now));
            cards[i].title = 'Times shown in ' + v.zone;
        }
    }
    /* "now HH:MM in <zone> -- does it match your watch?" + time sync. */
    function watchCheck(project, zone, advice) {
        var d = dialog('pt-zone-dialog', 'pt-zone');
        d.innerHTML = '';
        d.appendChild(el('h3', 'pt-title', 'Check the clock'));
        if (advice) d.appendChild(el('p', 'pt-text pt-advice', advice));
        var line = el('p', 'pt-now');
        var shown = '';
        function tick() {
            var now = serverNow();
            shown = hhmm(zone, now);
            line.textContent = 'Now ' + shown + ' in ' + zone + ' (' + offsetText(offsetMinutes(zone, now)) + ').';
        }
        tick();
        stopTick();
        _tick = setInterval(tick, 1000);
        d.appendChild(line);
        d.appendChild(el('p', 'pt-question', 'Does it match your watch?'));
        var ntp = el('p', 'pt-ntp muted', (_clock && _clock.ntp_text) || 'Time sync: unknown');
        if (_clock && _clock.ntp && _clock.ntp.detail) ntp.title = _clock.ntp.detail;
        if (_clock && _clock.ntp) ntp.setAttribute('data-ntp', _clock.ntp.synced === true ? 'on' : _clock.ntp.synced === false ? 'off' : 'unknown');
        d.appendChild(ntp);
        var row = el('div', 'pt-actions');
        function answer(a) {
            stopTick();
            post('/project-time/watch', { project: project, answer: a, shown: shown, zone: zone,
                                          ntp_synced: _clock && _clock.ntp && typeof _clock.ntp.synced === 'boolean' ? String(_clock.ntp.synced) : null });
            if (a === 'matches') { hide(d); return; }
            d.innerHTML = '';
            d.appendChild(el('h3', 'pt-title', 'This PC’s clock is off'));
            d.appendChild(el('p', 'pt-text', 'Turn on “Set time automatically” and press “Sync now” '
                + '(Settings › Time & language › Date & time). SM keeps your answer; no time is moved.'));
            var r2 = el('div', 'pt-actions');
            r2.appendChild(button('Close', 'outline', function () { hide(d); }, { 'data-pt-answer': 'close' }));
            d.appendChild(r2);
        }
        row.appendChild(button('Yes, it matches', 'primary', function () { answer('matches'); }, { 'data-pt-answer': 'matches' }));
        row.appendChild(button('No', 'outline', function () { answer('differs'); }, { 'data-pt-answer': 'differs' }));
        d.appendChild(row);
        show(d);
    }

    /* ------------------------------------------- the run-clock question */
    var _asking = false;
    function checkStatus(manual) {
        if (!manual && !document.documentElement.getAttribute('data-sm-project')) return Promise.resolve(null);
        return fetch('/project-time/status', { credentials: 'same-origin', cache: 'no-store' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (st) {
                if (!st || !st.project || !st.clock) return st;
                updateClockLine(st);
                var ask = st.clock.ask;
                if (ask && (manual || st.clock.auto_ask) && !_asking) showAsk(st);
                return st;
            }, function () { return null; });
    }
    function updateClockLine(st) {
        var line = document.querySelector('[data-clock-line] .diag-clock-text');
        if (line && st.line && st.line.text) line.textContent = st.line.text;
        if (st.line && !st.line.ask) {
            var b = document.querySelector('[data-clock-line] [data-clock-ask-open]');
            if (b) b.remove();
        }
    }
    function showAsk(st) {
        var ask = st.clock.ask;
        _asking = true;
        post('/project-time/skew-shown', { project: st.project, skew_s: ask.skew_s });
        var d = dialog('pt-skew-dialog', 'pt-skew');
        d.innerHTML = '';
        d.setAttribute('data-skew', String(ask.skew_s));
        d.appendChild(el('h3', 'pt-title', 'Two clocks disagree by ' + ask.skew_text));
        var later = ask.ahead ? 'later' : 'earlier';
        var ev = ask.src === 'live'
            ? 'SM saw ' + ask.n + ' runs of ' + st.project + ' arrive. Each run’s own clock says it was saved '
              + ask.skew_text + ' ' + later + ' than this PC saw its folder appear.'
            : 'In ' + ask.n + ' runs of ' + st.project + ' written in place, each run’s own clock is '
              + ask.skew_text + ' ' + later + ' than the folder’s file time.';
        d.appendChild(el('p', 'pt-text', ev));
        if (ask.examples && ask.examples.length) {
            var ul = el('ul', 'pt-examples');
            ask.examples.forEach(function (x) {
                var name = String(x.key || '').split('::').pop().split('/').pop();
                var off = isoOffsetMinutes(x.run_off);
                var runWall = off != null ? wallInOffset(x.run_utc, off) + ' (' + offsetText(off) + ')'
                                          : (window.SnapTime ? window.SnapTime.display(x.run_utc) : x.run_utc);
                var seen = window.SnapTime ? window.SnapTime.display(x.seen_utc) : x.seen_utc;
                ul.appendChild(el('li', null, name + ': run clock ' + runWall + ' · '
                    + (ask.src === 'live' ? 'seen ' : 'folder time ') + seen));
            });
            d.appendChild(ul);
        }
        if (ask.whole_units) {
            d.appendChild(el('p', 'pt-text muted', 'A whole-hour step like this usually means a wrong time zone '
                + 'or a missed daylight-saving change on one of the PCs.'));
        }
        var ntp = el('p', 'pt-ntp muted', 'This PC: checking time sync…');
        d.appendChild(ntp);
        clockStatus().then(function (c) { ntp.textContent = 'This PC: ' + ((c && c.ntp_text) || 'Time sync: unknown'); if (c && c.ntp && c.ntp.detail) ntp.title = c.ntp.detail; });
        d.appendChild(el('p', 'pt-question', 'Which clock is wrong?'));
        var row = el('div', 'pt-actions');
        var msg = el('p', 'pt-text pt-result', '');
        function answer(choice) {
            post('/project-time/skew-answer', { project: st.project, choice: choice, skew_s: ask.skew_s })
                .then(function (j) {
                    if (j && j.ok) {
                        _asking = false;
                        hide(d);
                        try { document.dispatchEvent(new CustomEvent('sm:run-clock-answered', { detail: { choice: choice } })); } catch (e) { /* old engine */ }
                        checkStatus(true);
                    } else if (j && j._status === 409) {
                        _asking = false;
                        hide(d);
                        checkStatus(true);          // the skew moved: ask the new one
                    } else {
                        msg.textContent = 'Could not save the answer: ' + ((j && j.error) || 'error');
                    }
                });
        }
        row.appendChild(button('The experiment PC’s clock', 'primary', function () { answer('experiment_pc'); },
                               { 'data-pt-choice': 'experiment_pc' }));
        row.appendChild(button('This PC’s clock', 'outline', function () { answer('this_pc'); },
                               { 'data-pt-choice': 'this_pc' }));
        row.appendChild(button('Ignore', 'secondary outline', function () { answer('ignore'); },
                               { 'data-pt-choice': 'ignore' }));
        d.appendChild(row);
        d.appendChild(msg);
        // the regime starts at a RUN (its own clock is the skewed one, so a
        // time here would read an hour off the seen times above it)
        var from = ask.from_key ? 'run ' + String(ask.from_key).split('::').pop().split('/').pop()
            : (window.SnapTime && ask.from_utc ? window.SnapTime.display(ask.from_utc) : ask.from_utc);
        d.appendChild(el('p', 'pt-text muted pt-small', 'Only “The experiment PC’s clock” changes how run '
            + 'times are shown: corrected from ' + from + ' on, labelled “corrected”, the recorded time kept '
            + 'beside it. SM asks again only if the difference changes.'));
        var x = button('Later', 'secondary outline pt-later', function () { _asking = false; hide(d); },
                       { 'data-pt-choice': 'later', title: 'Close -- the question stays on Diagnostics' });
        row.appendChild(x);
        dlgOf(d).addEventListener('close', function () { _asking = false; }, { once: true });
        show(d);
    }

    /* ------------------------------------------------------- run times */
    function applyRunTimes(scope) {
        if (!window.SnapTime) return;
        var nodes = (scope || document).querySelectorAll('.ts-run[data-utc]:not([data-run-localized])');
        for (var i = 0; i < nodes.length; i++) {
            var n = nodes[i];
            var utc = n.getAttribute('data-utc');
            var corr = n.getAttribute('data-corrected-utc');
            var rec = n.getAttribute('data-recorded') || '';
            var recOff = n.getAttribute('data-recorded-off') || '';
            var recText = rec + (recOff ? ' (' + recOff + ')' : ' (folder clock; zone not recorded)');
            var shown = window.SnapTime.display(corr || utc);
            n.textContent = '';
            n.appendChild(document.createTextNode(shown));
            if (corr) {
                n.appendChild(document.createTextNode(' '));
                n.appendChild(el('span', 'ts-run-corrected', 'corrected'));
                n.appendChild(document.createTextNode(' '));
                n.appendChild(el('small', 'muted ts-run-orig', 'recorded ' + recText));
                n.title = 'corrected by ' + (n.getAttribute('data-corrected-by') || '')
                    + ' (you said the experiment PC’s clock is wrong) · recorded ' + recText
                    + ' · ' + utc + ' (UTC)';
            } else {
                n.title = 'recorded ' + recText + ' · ' + utc + ' (UTC)';
            }
            n.setAttribute('data-run-localized', '1');
        }
    }
    function refreshRunTimes() {
        var done = document.querySelectorAll('.ts-run[data-run-localized]');
        for (var i = 0; i < done.length; i++) done[i].removeAttribute('data-run-localized');
        applyRunTimes(document);
    }

    /* ---------------------------------------------------------- wiring */
    var _statusTimer = null;
    function scheduleStatus(ms) {
        if (_statusTimer) clearTimeout(_statusTimer);
        _statusTimer = setTimeout(function () { _statusTimer = null; checkStatus(false); }, ms);
    }
    function onReady() {
        boot();
        applyRunTimes(document);
        scheduleStatus(1500);     // after the page's own first requests
    }
    document.addEventListener('htmx:afterSettle', function (e) {
        boot();
        applyRunTimes((e && e.detail && e.detail.target) || document);
    });
    document.addEventListener('sm-landing-project', function (e) {
        var p = e && e.detail && e.detail.project;
        if (p) setProject(p);
    });
    document.addEventListener('sm:runs-changed', function () { scheduleStatus(4000); });
    document.addEventListener('sm:timezone-changed', function () { refreshRunTimes(); renderCurrent(); });
    document.addEventListener('click', function (e) {
        var t = e.target;
        if (!t || !t.closest) return;
        if (t.closest('[data-tz-change]')) { openSearch(); return; }
        if (t.closest('[data-clock-ask-open]')) { checkStatus(true); return; }
    });
    document.addEventListener('input', function (e) {
        var t = e.target;
        if (t && t.matches && t.matches('[data-tz-input]')) renderList(t.value);
    });
    document.addEventListener('keydown', function (e) {
        var t = e.target;
        if (!t || !t.matches || !t.matches('[data-tz-input]')) return;
        if (e.key === 'Enter') {
            e.preventDefault();
            var first = q('[data-tz-list] [data-zone]');
            if (first) pick(first.getAttribute('data-zone'));
        } else if (e.key === 'Escape') {
            var s = q('[data-tz-search]');
            if (s) s.hidden = true;
        }
    });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', onReady);
    else onReady();

    window.ProjectTime = {
        zones: zones, offsetMinutes: offsetMinutes, offsetText: offsetText, search: search,
        hhmm: hhmm, spanText: spanText, secondsText: secondsText, cityOf: cityOf,
        wallInOffset: wallInOffset, isoOffsetMinutes: isoOffsetMinutes,
        pick: pick, save: save, setProject: setProject, checkStatus: checkStatus,
        applyRunTimes: applyRunTimes, refreshRunTimes: refreshRunTimes, clockStatus: clockStatus,
        _boot: boot, _state: function () { return { project: _project, views: _views, clock: _clock }; }
    };
})();
