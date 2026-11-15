/* Quam load check                                                     */
/* ------------------------------------------------------------------ */

window.runLoadCheck = (function () {
    function allBtns() {
        return Array.prototype.slice.call(document.querySelectorAll('.js-load-check-btn'));
    }
    function allStatus() {
        return Array.prototype.slice.call(document.querySelectorAll('[data-loadcheck-status]'));
    }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (ch) {
            return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
        });
    }
    function setRunning(on) {
        allBtns().forEach(function (b) {
            if (!b) return;
            if (on) {
                if (!b.getAttribute('data-loadcheck-label')) {
                    b.setAttribute('data-loadcheck-label', b.textContent || '');
                }
                b.textContent = 'Checking…';
                b.disabled = true;
            } else {
                b.textContent = b.getAttribute('data-loadcheck-label') || '\u2713 Load check';
                b.disabled = false;
            }
        });
    }
    // UI kit verdict line: dot + key + why (no page-specific CSS).
    function setStatus(kind, key, whyHtml) {
        allStatus().forEach(function (s) {
            if (!s) return;
            if (!kind) { s.className = ''; s.innerHTML = ''; return; }
            s.className = 'ui-verdict ui-verdict--' + kind + '';
            s.innerHTML = '<span class="ui-dot"></span><span class="ui-verdict-key">' + esc(key)
                + '</span>' + (whyHtml ? '<span class="ui-verdict-why">' + whyHtml + '</span>' : '');
        });
    }
    function renderFail(data) {
        var typ = (data && data.exc_type) || 'Error';
        var detail = esc((data && data.detail) || 'Load check failed.');
        var msg = data && data.message;
        var tb = '';
        if (msg && msg !== data.detail) {
            // The exception's own words first; the traceback folds away.
            tb = '<details class="ui-disclosure"><summary class="ui-disclosure-summary">'
                + 'Traceback</summary><pre>' + detail + '</pre></details>';
            detail = esc(msg);
        }
        var links = (data && data.links) || [];
        var linkHtml = '';
        if (Array.isArray(links)) {
            for (var i = 0; i < links.length; i++) {
                var p = links[i] && links[i].path;
                if (!p) continue;
                linkHtml += ' <a href="#" class="ui-path load-check-link" data-loadcheck-path="' + esc(p)
                    + '" title="Show this path in Json tree">' + esc(p) + '</a>';
            }
        }
        setStatus('danger', typ, detail + envLabel(data && data.env) + linkHtml + (tb || ''));
    }
    // Static core budget warnings under "Loads OK": one warn callout per
    // finding, its detail lines folded (the load itself still passed).
    function coreWarnHtml(list) {
        if (!Array.isArray(list) || !list.length) return '';
        var html = '';
        for (var i = 0; i < list.length; i++) {
            var f = list[i] || {};
            if (!f.line) continue;
            var det = Array.isArray(f.detail) ? f.detail : [];
            var tone = f.severity === 'info' ? 'note' : 'warn';
            html += '<div class="ui-callout ui-callout--' + tone + ' js-loadcheck-core">'
                + '<p class="ui-callout-text"><span class="ui-callout-title">' + esc(f.line) + '</span></p>';
            if (det.length) {
                html += '<details class="ui-disclosure"><summary class="ui-disclosure-summary">Details</summary>';
                for (var j = 0; j < det.length; j++) html += '<p class="ui-callout-text">' + esc(det[j]) + '</p>';
                html += '</details>';
            }
            if (window.CoreRecommend) html += window.CoreRecommend.html(f);
            html += '</div>';
        }
        return html ? '<div data-loadcheck-core>' + html + '</div>' : '';
    }
    // After a staged recommendation, the warning lines are re-read from the working copy.
    function onCoreFindings(list) {
        document.querySelectorAll('[data-loadcheck-core]').forEach(function (slot) {
            var h = coreWarnHtml(list);
            if (!h) { slot.innerHTML = ''; return; }
            var tmp = document.createElement('div');
            tmp.innerHTML = h;
            slot.innerHTML = tmp.firstChild.innerHTML;
        });
    }
    setTimeout(function () {
        if (window.CoreRecommend) window.CoreRecommend.onApplied(onCoreFindings);
    }, 0);
    function envLabel(env) {
        if (!env || !env.name) return '';
        var src = { project: 'project', selected: 'selected machine-wide', chosen: 'chosen here' }[env.source] || '';
        return ' · env: ' + esc(env.name) + (src ? ' (' + esc(src) + ')' : '');
    }
    // No project env and no machine-wide env: offer the discovered envs right
    // in the status line; a pick runs the check in that env (loadchk D-1).
    function envRequired(data) {
        var d = data || {};
        var envs = Array.isArray(d.envs) ? d.envs : [];
        var html = esc(d.message || 'Choose a Python environment.');
        if (!envs.length) {
            html += ' No Python environment was found on this machine.';
        } else {
            for (var i = 0; i < envs.length; i++) {
                var e = envs[i] || {};
                if (!e.python) continue;
                html += ' <button type="button" class="btn-sm outline js-load-check-env" data-loadcheck-python="'
                    + esc(e.python) + '" title="' + esc(e.python) + '">' + esc(e.name || e.python) + '</button>';
            }
        }
        setStatus('warn', 'Choose an env', html);
    }
    var picked = null;
    function run(python) {
        if (typeof python === 'string' && python) picked = python;
        setRunning(true);
        setStatus('');
        var init = { method: 'POST', headers: { 'X-Requested-With': 'fetch' } };
        if (picked) {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify({ python: picked });
        }
        return fetch('/state/load-check', init)
            .then(function (r) {
                return r.json().catch(function () { return {}; }).then(function (j) {
                    return { ok: r.ok, status: r.status, body: j || {} };
                });
            })
            .then(function (resp) {
                var data = resp.body || {};
                if (resp.status === 409 && data.error === 'env_required') {
                    envRequired(data);
                    return;
                }
                if (resp.ok && data.ok) {
                    var qv = ((data.lib_versions || {}).quam || '?');
                    setStatus('ok', 'Loads OK', 'quam ' + esc(qv) + envLabel(data.env)
                        + coreWarnHtml(data.core_findings));
                    return;
                }
                renderFail(data);
            })
            .catch(function (e) {
                setStatus('danger', 'Load check failed', esc(e && e.message || e));
            })
            .finally(function () { setRunning(false); });
    }
    run.withEnv = function (python) { return run(python); };
    run._coreWarnHtml = coreWarnHtml;
    return run;
})();

document.addEventListener('click', function (evt) {
    var b = evt.target && evt.target.closest ? evt.target.closest('.js-load-check-env[data-loadcheck-python]') : null;
    if (!b) return;
    evt.preventDefault();
    window.runLoadCheck.withEnv(b.getAttribute('data-loadcheck-python'));
});

document.addEventListener('click', function (evt) {
    var a = evt.target && evt.target.closest ? evt.target.closest('.load-check-link[data-loadcheck-path]') : null;
    if (!a) return;
    evt.preventDefault();
    var p = a.getAttribute('data-loadcheck-path');
    if (p && window._navigateToExplorerPath) window._navigateToExplorerPath(p);
});

/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* Recommend core layout (Load check and Chip Status)                  */
/* Press -> preview -> confirm step, Auto-Sync note read now -> staged */
/* undo group through window.applyDiagFixAll -> re-read the findings.  */
/* ------------------------------------------------------------------ */
window.CoreRecommend = (function () {
    var NOTE_FAILED = 'Could not read the Auto-Sync state; try again.';
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (ch) {
            return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
        });
    }
    function num(x) { return (typeof x === 'number' && isFinite(x)) ? String(x) : ''; }
    function val(x) { return x == null ? 'null' : String(x); }
    function where(p) { return esc(p.controller) + ' FEM ' + esc(p.fem); }
    function previewHtml(p) {
        var est = p.cores && p.cores.estimated ? ' (estimated)' : '';
        var h = '<p class="ui-callout-text" data-core-rec-counts>Cores on ' + where(p) + ': '
            + num(p.cores.before) + ' / ' + num(p.cores.limit) + ' now, '
            + num(p.cores.after) + ' / ' + num(p.cores.limit) + est + ' after.</p>';
        var g = p.groups_after || [];
        if (g.length) {
            h += '<table class="ui-table" aria-label="Shared cores after"><thead><tr><th>core</th>'
                + '<th>elements</th><th class="ui-table-num">cost</th></tr></thead><tbody>';
            g.forEach(function (x) {
                h += '<tr><td>' + esc(x.core) + '</td><td>' + esc((x.elements || []).join(', '))
                    + '</td><td class="ui-table-num">' + num(x.cost) + '</td></tr>';
            });
            h += '</tbody></table>';
        }
        var c = p.changes || [];
        if (c.length) {
            h += '<table class="ui-table" aria-label="Changes" data-core-rec-changes><thead><tr>'
                + '<th>element</th><th>key</th><th>now</th><th>after</th></tr></thead><tbody>';
            c.forEach(function (x) {
                h += '<tr><td>' + esc(x.element) + '</td><td>' + esc(x.key) + '</td><td>'
                    + esc(val(x.before)) + '</td><td>' + esc(val(x.after)) + '</td></tr>';
            });
            h += '</tbody></table>';
        }
        (p.kept || []).forEach(function (k) {
            h += '<p class="ui-callout-text" data-core-rec-kept>' + esc(k.element) + ' ' + esc(k.note) + '</p>';
        });
        (p.unresolved || []).forEach(function (u) {
            h += '<p class="ui-callout-text" data-core-rec-unresolved>' + esc(u.element) + '.'
                + esc(u.field) + ': port not read (' + esc(u.reason) + '); not placed.</p>';
        });
        return h;
    }
    /* One finding's recommendation block ('' when it carries none). */
    function html(f) {
        var p = f && f.preview;
        if (!p) return '';
        if (!p.button) {
            return p.panel_line ? '<p class="ui-callout-text" data-core-rec-panel>' + esc(p.panel_line) + '</p>' : '';
        }
        var items = JSON.stringify(f.fixes || []);
        return '<div data-core-rec>'
            + '<div class="ui-actions ui-actions--start"><button type="button" class="btn-sync" data-core-rec-open>'
            + 'Recommend core layout</button></div>'
            + '<div data-core-rec-preview hidden>' + previewHtml(p)
            + '<p class="ui-callout-text" data-core-rec-note hidden></p>'
            + '<div class="ui-actions ui-actions--start">'
            + '<button type="button" class="btn-sync" data-core-rec-apply disabled data-items="' + esc(items) + '">'
            + 'Stage this layout</button>'
            + '<button type="button" class="btn-sync" data-core-rec-cancel>Cancel</button></div></div></div>';
    }
    function openPreview(box) {
        var pv = box.querySelector('[data-core-rec-preview]');
        var note = box.querySelector('[data-core-rec-note]');
        var apply = box.querySelector('[data-core-rec-apply]');
        pv.hidden = false; apply.disabled = true; note.hidden = true; note.textContent = '';
        return fetch('/diagnostics/auto-push-note', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { if (!r.ok) throw new Error('status ' + r.status); return r.json(); })
            .then(function (d) {
                if (!d || d.ok !== true) throw new Error('bad body');
                if (d.note) { note.textContent = d.note; note.hidden = false; }
                apply.disabled = false;
            })
            .catch(function () { note.textContent = NOTE_FAILED; note.hidden = false; apply.disabled = true; });
    }
    var refreshers = [];
    function onApplied(fn) { if (typeof fn === 'function') refreshers.push(fn); }
    /* Re-read the core findings from the working copy and hand them to every surface. */
    function refresh() {
        return fetch('/diagnostics/core-findings', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                var list = (d && Array.isArray(d.findings)) ? d.findings : [];
                refreshers.forEach(function (fn) { try { fn(list); } catch (e) { /* one surface only */ } });
                return list;
            })
            .catch(function () { return null; });
    }
    function press(apply) {
        if (!window.applyDiagFixAll) return null;
        var p = window.applyDiagFixAll(apply);
        return p && p.then ? p.then(function () { return refresh(); }) : null;
    }
    document.addEventListener('click', function (evt) {
        var t = evt.target && evt.target.closest ? evt.target : null;
        if (!t) return;
        var box = t.closest('[data-core-rec]');
        if (!box) return;
        if (t.closest('[data-core-rec-open]')) { evt.preventDefault(); openPreview(box); return; }
        if (t.closest('[data-core-rec-cancel]')) {
            evt.preventDefault();
            box.querySelector('[data-core-rec-preview]').hidden = true;
            box.querySelector('[data-core-rec-apply]').disabled = true;
            return;
        }
        var ap = t.closest('[data-core-rec-apply]');
        if (ap && !ap.disabled) { evt.preventDefault(); press(ap); }
    });
    /* Chip Status: one warning block per core layout finding that carries a
       recommendation, in a slot right after the given element; re-rendered in
       place when the findings are re-read. */
    function blocks(list) {
        var h = '';
        (list || []).forEach(function (f) {
            if (!f || !f.preview || (f.category && f.category !== 'core_layout')) return;
            h += '<div class="ui-callout ui-callout--warn"><p class="ui-callout-text"><span class="ui-callout-title">'
                + esc('Core layout warning: ' + (f.message || '')) + '</span></p>' + html(f) + '</div>';
        });
        return h;
    }
    function mountAfter(anchor, list) {
        if (!anchor || !anchor.parentNode) return null;
        var slot = anchor.parentNode.querySelector('[data-core-rec-slot]');
        if (!slot) {
            slot = document.createElement('div');
            slot.setAttribute('data-core-rec-slot', '');
            anchor.parentNode.insertBefore(slot, anchor.nextSibling);
        }
        slot.innerHTML = blocks(list);
        return slot;
    }
    onApplied(function (list) {
        document.querySelectorAll('[data-core-rec-slot]').forEach(function (slot) { slot.innerHTML = blocks(list); });
    });
    return { html: html, previewHtml: previewHtml, refresh: refresh, onApplied: onApplied,
             mountAfter: mountAfter, blocks: blocks,
             NOTE_FAILED: NOTE_FAILED };
})();
