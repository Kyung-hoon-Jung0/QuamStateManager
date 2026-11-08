/* Quam load check                                                     */
/* ------------------------------------------------------------------ */

window.runLoadCheck = (function () {
    function allBtns() {
        return Array.prototype.slice.call(document.querySelectorAll('.js-load-check-btn'));
    }
    function allStatus() {
        return Array.prototype.slice.call(document.querySelectorAll('.js-load-check-status'));
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
    function setStatus(kind, html) {
        allStatus().forEach(function (s) {
            if (!s) return;
            s.className = 'load-check-status js-load-check-status ' + (kind ? ('is-' + kind) : '');
            s.innerHTML = html || '';
        });
    }
    function renderFail(data) {
        var typ = esc((data && data.exc_type) || 'Error');
        var detail = esc((data && data.detail) || 'Load check failed.');
        var links = (data && data.links) || [];
        var linkHtml = '';
        if (Array.isArray(links) && links.length) {
            linkHtml = ' ';
            for (var i = 0; i < links.length; i++) {
                var p = links[i] && links[i].path;
                if (!p) continue;
                linkHtml += '<a href="#" class="load-check-link" data-loadcheck-path="' + esc(p)
                    + '" title="Show this path in Json tree">' + esc(p) + '</a>';
                if (i + 1 < links.length) linkHtml += ' ';
            }
        }
        setStatus('fail', typ + ': ' + detail + linkHtml);
    }
    function envRequired(data) {
        var d = data || {};
        try {
            document.dispatchEvent(new CustomEvent('sm-env-required', {
                detail: { project: d.project || '', message: d.message || '' }
            }));
        } catch (e) {}
        if (!document.getElementById('landing-env-picker') && d.open_url) window.location.href = d.open_url;

    }
    return function () {
        setRunning(true);
        setStatus('', '');
        return fetch('/state/load-check', {
            method: 'POST',
            headers: { 'X-Requested-With': 'fetch' }
        })
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
                    setStatus('ok', 'Loads OK (quam ' + esc(qv) + ')');
                    return;
                }
                renderFail(data);
            })
            .catch(function (e) {
                setStatus('fail', 'Load check failed: ' + esc(e && e.message || e));
            })
            .finally(function () { setRunning(false); });
    };
})();

document.addEventListener('click', function (evt) {
    var a = evt.target && evt.target.closest ? evt.target.closest('.load-check-link[data-loadcheck-path]') : null;
    if (!a) return;
    evt.preventDefault();
    var p = a.getAttribute('data-loadcheck-path');
    if (p && window._navigateToExplorerPath) window._navigateToExplorerPath(p);
});

/* ------------------------------------------------------------------ */
