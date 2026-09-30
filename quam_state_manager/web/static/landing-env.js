/* landing-env.js -- the Projects landing's per-project environment picker
 * (w9/labwarm).
 *
 * Each project card carries an env row (_landing_project_env.html): the env
 * the project was synced with, or the suggested one with a Confirm button.
 * "Change..." / "Choose..." retargets ONE inline picker above the cards -- the
 * same discovery Generate Config uses (GET /generate/envs: conda envs +
 * uv/venv found through the qualibrate projects; GET /generate/probe per env;
 * a typed interpreter or venv folder). Nothing here is fetched until the user
 * asks: a remembered env is never re-discovered or re-probed.
 *
 * The picker is not modal: the card's Open button stays pressable while the
 * envs are discovered and probed (a probe imports the QM stack, seconds per
 * env). A pick posts /qualibrate/project-env through htmx, so the card's row
 * and the sidebar env badge (out of band) are swapped by the answer.
 */
(function () {
    'use strict';
    if (typeof window === 'undefined' || window.LandingEnv) return;

    var _seq = 0;            // a newer open / rescan supersedes older answers
    var _project = null;
    var _pendingOpen = null; // customer 2026-09-30: an Open refused for want of
                             // an env; re-submitted once the pick is saved

    function picker() { return document.getElementById('landing-env-picker'); }
    function q(root, sel) { return root ? root.querySelector(sel) : null; }

    function rowFor(project) {
        var rows = document.querySelectorAll('.landing-card-env[data-project]');
        for (var i = 0; i < rows.length; i++) {
            if (rows[i].getAttribute('data-project') === project) return rows[i];
        }
        return null;
    }

    /* The picker stays on screen (customer 2026-09-30); "close" only drops
       a pending required-open and its note. */
    function close() {
        _pendingOpen = null;
        var note = q(picker(), '[data-env-required]');
        if (note) { note.hidden = true; note.textContent = ''; }
    }

    function openFormFor(project) {
        var forms = document.querySelectorAll('form[hx-post="/qualibrate/open"]');
        for (var i = 0; i < forms.length; i++) {
            var inp = forms[i].querySelector('input[name="project"]');
            var from = forms[i].querySelector('input[name="from"]');
            if (inp && inp.value === project && from && from.value === 'landing') return forms[i];
        }
        return null;
    }

    function pick(python) {
        var project = _project;
        var target = project && rowFor(project);
        if (!project || !target || !python) return;
        var status = q(picker(), '[data-env-custom-status]');
        if (status) status.textContent = 'saving…';
        var done = function () {
            var pk = picker();
            if (pk) pk.setAttribute('data-env-current', python);
            if (status) status.textContent = '';
            var again = _pendingOpen === project ? openFormFor(project) : null;
            close();
            if (again) {
                if (again.requestSubmit) again.requestSubmit(); else again.submit();
            } else {
                open(project, python, null, { noScroll: true });   // re-mark the row in use
            }
        };
        if (window.htmx && htmx.ajax) {
            htmx.ajax('POST', '/qualibrate/project-env', {
                target: target, swap: 'innerHTML',
                values: { project: project, python: python, how: 'changed' }
            }).then(done, function () {
                if (status) status.textContent = 'could not save the environment';
            });
        }
    }

    function probe(python, statusEl, btn) {
        var mine = _seq;
        return fetch('/generate/probe?python=' + encodeURIComponent(python))
            .then(function (r) { return r.json(); })
            .then(function (info) {
                if (mine !== _seq) return info;
                if (info && info.usable) {
                    var v = info.versions || {};
                    statusEl.setAttribute('data-state', 'ok');
                    statusEl.textContent = '✓ quam ' + (v.quam || '?') +
                        ' · quam_builder ' + (v.quam_builder || '?');
                    if (btn) btn.disabled = false;
                } else {
                    statusEl.setAttribute('data-state', 'bad');
                    var miss = (info && info.missing || []).join(', ');
                    statusEl.textContent = info && info.error ? '✗ ' + String(info.error).slice(0, 120)
                        : '✗ missing: ' + (miss || 'the QM stack');
                    if (btn) { btn.disabled = true; btn.title = statusEl.textContent; }
                }
                return info;
            }, function () {
                if (mine !== _seq) return null;
                statusEl.setAttribute('data-state', 'bad');
                statusEl.textContent = '✗ probe failed';
                return null;
            });
    }

    function render(envs, current) {
        var pk = picker();
        var list = q(pk, '[data-env-list]');
        var loading = q(pk, '[data-env-loading]');
        if (!list) return;
        list.innerHTML = '';
        if (loading) {
            loading.hidden = !!envs.length;
            loading.textContent = envs.length ? '' : 'No conda or uv environments found — type an interpreter or venv folder below.';
            if (!envs.length) loading.hidden = false;
        }
        envs.forEach(function (env) {
            var row = document.createElement('div');
            row.className = 'landing-env-row';
            row.setAttribute('role', 'option');
            row.setAttribute('data-python', env.python);
            var isCur = current && env.python && env.python.toLowerCase() === current.toLowerCase();
            // a merely SUGGESTED env is not chosen yet: its button must still
            // say "Use this" and save it (customer 2026-09-30)
            var chosen = isCur && (pk.getAttribute('data-env-state') === 'remembered');
            if (isCur) { row.classList.add('selected'); row.setAttribute('aria-selected', 'true'); }
            var name = document.createElement('span'); name.className = 'landing-env-name'; name.textContent = env.name;
            row.appendChild(name);
            if (env.kind && env.kind !== 'conda') {
                var kind = document.createElement('span'); kind.className = 'landing-env-kind';
                kind.textContent = env.kind === 'uv-venv' ? 'uv venv' : env.kind; row.appendChild(kind);
            }
            var path = document.createElement('span'); path.className = 'landing-env-path'; path.textContent = env.python;
            row.appendChild(path);
            var st = document.createElement('span'); st.className = 'landing-env-status';
            st.setAttribute('data-state', 'checking'); st.textContent = 'checking…';
            row.appendChild(st);
            var btn = document.createElement('button');
            btn.type = 'button'; btn.className = 'btn-sm' + (chosen ? ' outline' : '');
            btn.textContent = chosen ? 'In use' : (isCur ? 'Use this (suggested)' : 'Use this');
            btn.disabled = true;           // until its probe says the QM stack is there
            btn.addEventListener('click', function () { pick(env.python); });
            row.appendChild(btn);
            list.appendChild(row);
            probe(env.python, st, btn);
        });
    }

    function load(refresh) {
        var pk = picker();
        var mine = ++_seq;
        var loading = q(pk, '[data-env-loading]');
        var list = q(pk, '[data-env-list]');
        if (loading) { loading.hidden = false; loading.textContent = 'Discovering environments…'; }
        if (list) list.innerHTML = '';
        var current = pk ? pk.getAttribute('data-env-current') : '';
        return fetch('/generate/envs' + (refresh ? '?refresh=1' : ''))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (mine !== _seq) return;
                render((data && data.envs) || [], current);
            }, function () {
                if (mine !== _seq) return;
                if (loading) { loading.hidden = false; loading.textContent = 'Could not scan the environments — type an interpreter or venv folder below.'; }
            });
    }

    function open(project, current, anchor, opts) {
        var pk = picker();
        if (!pk) return;
        opts = opts || {};
        if (!opts.keepPending) close();
        _project = project;
        pk.setAttribute('data-env-current', current || '');
        var chg = rowFor(project);
        chg = chg && chg.querySelector('[data-env-change]');
        var st0 = (chg && chg.getAttribute('data-env-state')) || 'none';
        if (opts.state) st0 = opts.state;
        pk.setAttribute('data-env-state', st0);
        var nm = q(pk, '[data-env-project]');
        if (nm) nm.textContent = project;
        var st = q(pk, '[data-env-custom-status]');
        if (st) st.textContent = '';
        pk.hidden = false;
        if (!opts.noScroll) {
            try { pk.scrollIntoView({ block: 'nearest' }); } catch (e) { /* old engine */ }
        }
        load(false);
    }

    /* Opening *project* was refused for want of an env: show the picker for
       it with the reason; the pick re-submits the open. */
    function openRequired(project, message) {
        var chg = rowFor(project);
        chg = chg && chg.querySelector('[data-env-change]');
        open(project, chg ? chg.getAttribute('data-env-current') : '', null, { keepPending: true, noScroll: true });
        _pendingOpen = project;
        var note = q(picker(), '[data-env-required]');
        if (note) {
            note.textContent = (message || ('Choose the Python environment for ' + project + ' first.')) +
                ' The project opens as soon as you pick one.';
            note.hidden = false;
        }
        try { picker().scrollIntoView({ block: 'start' }); } catch (e) { /* old engine */ }
    }

    /* The picker is on screen from the start, for the default project (or
       the one a refused open elsewhere sent here via ?choose_env=). */
    function boot() {
        var pk = picker();
        if (!pk || pk.getAttribute('data-env-booted')) return;
        pk.setAttribute('data-env-booted', '1');
        var want = null;
        try { want = new URLSearchParams(location.search).get('choose_env'); } catch (e) { want = null; }
        if (want && rowFor(want)) { openRequired(want); return; }
        var def = pk.getAttribute('data-env-default');
        if (!def || !rowFor(def)) return;
        var chg = rowFor(def).querySelector('[data-env-change]');
        open(def, chg ? chg.getAttribute('data-env-current') : '', null, { noScroll: true });
    }
    document.addEventListener('htmx:afterSettle', boot);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
    document.addEventListener('sm-env-required', function (e) {
        var d = (e && e.detail) || {};
        if (d.project) openRequired(d.project, d.message);
    });

    function useCustom() {
        var pk = picker();
        var input = q(pk, '[data-env-custom]');
        var status = q(pk, '[data-env-custom-status]');
        var raw = input ? String(input.value || '').trim().replace(/^["']|["']$/g, '') : '';
        if (!raw) { if (status) status.textContent = 'Enter an interpreter or venv-folder path.'; return; }
        if (status) status.textContent = 'checking…';
        var mine = _seq;
        fetch('/generate/probe?python=' + encodeURIComponent(raw))
            .then(function (r) { return r.json(); })
            .then(function (info) {
                if (mine !== _seq) return;
                if (info && info.usable) {
                    pick(info.resolved || raw);
                } else if (status) {
                    var miss = (info && info.missing || []).join(', ');
                    status.textContent = info && info.error ? 'probe failed: ' + info.error
                        : 'missing: ' + (miss || 'qualang_tools / quam_builder / quam');
                }
            }, function () { if (status && mine === _seq) status.textContent = 'probe failed.'; });
    }

    document.addEventListener('click', function (e) {
        var t = e.target;
        if (!t || !t.closest) return;
        var ch = t.closest('[data-env-change]');
        if (ch) { open(ch.getAttribute('data-env-change'), ch.getAttribute('data-env-current'), ch); return; }
        if (t.closest('[data-env-close]')) { close(); return; }
        if (t.closest('[data-env-rescan]')) { load(true); return; }
        if (t.closest('[data-env-use]')) { useCustom(); return; }
    });
    document.addEventListener('keydown', function (e) {
        var pk = picker();
        if (!pk || pk.hidden) return;
        if (e.key === 'Escape' && _pendingOpen) { close(); return; }
        if (e.key === 'Enter' && e.target && e.target.matches && e.target.matches('[data-env-custom]')) {
            e.preventDefault(); useCustom();
        }
    });

    /* The sidebar's env badge names the ACTIVE env. An env selected outside
       the landing (Generate Config's picker, its build, the Runner -- all
       POST /generate/select-env from their own scripts) would leave it
       naming the previous one until a full reload: after any such POST
       answers ok, the badge slot is re-fetched and replaced. The caller's
       promise is returned untouched. */
    function refreshBadge() {
        var slot = document.getElementById('sidebar-folder-badges-slot');
        if (!slot || typeof _fetch !== 'function') return;
        _fetch.call(window, '/sidebar/folder-badges', { cache: 'no-store' })
            .then(function (r) { return r.ok ? r.text() : null; })
            .then(function (html) {
                var cur = document.getElementById('sidebar-folder-badges-slot');
                if (html == null || !cur) return;
                var tmp = document.createElement('div');
                tmp.innerHTML = html;
                var fresh = tmp.querySelector('#sidebar-folder-badges-slot');
                if (fresh) cur.replaceWith(fresh);
            }, function () { /* the next full render says it */ });
    }
    var _fetch = window.fetch;
    if (typeof _fetch === 'function') {
        window.fetch = function (input, init) {
            var p = _fetch.apply(this, arguments);
            try {
                var url = typeof input === 'string' ? input : (input && input.url) || '';
                var m = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
                if (m === 'POST' && /^\/generate\/select-env(\?|$)/.test(url.replace(/^https?:\/\/[^/]+/, ''))) {
                    p.then(function (r) { if (r && r.ok) refreshBadge(); }, function () {});
                }
            } catch (e) { /* the badge is a courtesy */ }
            return p;
        };
    }

    window.LandingEnv = { open: open, close: close, pick: pick, _load: load,
                          openRequired: openRequired, _boot: boot,
                          _refreshBadge: refreshBadge };
})();
