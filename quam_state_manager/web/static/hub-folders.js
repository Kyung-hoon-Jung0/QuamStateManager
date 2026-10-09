window.HubFolders = (function () {
    var overlay, release;
    function close() {
        document.removeEventListener('keydown', onKey, true);
        if (release) release();
        release = null;
        if (overlay) overlay.remove();
        overlay = null;
    }
    function onKey(event) {
        if (event.key !== 'Escape') return;
        event.preventDefault();
        event.stopImmediatePropagation();
        close();
    }
    function open() {
        close();
        overlay = document.createElement('div');
        overlay.id = 'hub-folder-modal';
        overlay.className = 'ch-overlay';
        overlay.innerHTML = '<div class="ch-backdrop"></div>' +
            '<section class="ch-card" role="dialog" aria-modal="true" aria-labelledby="hub-folder-title" tabindex="-1">' +
            '<h2 id="hub-folder-title">Link a data folder</h2>' +
            '<button type="button" class="outline btn-sm" aria-label="Close">Close</button>' +
            '<div id="hub-folder-body" aria-live="polite">Checking folders...</div></section>';
        document.body.appendChild(overlay);
        overlay.style.display = 'flex';
        overlay.querySelector('button').addEventListener('click', close);
        overlay.querySelector('.ch-backdrop').addEventListener('click', close);
        document.addEventListener('keydown', onKey, true);
        release = window.trapFocus(overlay.querySelector('.ch-card'), close);
    }
    document.addEventListener('hubLinked', function () {
        close();
        // The drawer, column modal, Versions and State History own their refresh.
        document.querySelectorAll('.hub-notes').forEach(function (notes) {
            if (notes.closest('#field-history-panel, .colhist-overlay, #state-version-panel, #state-history-body')) return;
            var target = notes.closest('#topo-trends, #param-history-drawer, #param-history-root, #inspector-pane');
            if (!target || !window.htmx) return;
            var swap = target.matches('#topo-trends, #param-history-root') ? 'outerHTML' : 'innerHTML';
            htmx.ajax('GET', notes.dataset.hubUrl, {target: target, swap: swap});
        });
    });
    document.addEventListener('htmx:responseError', function (event) {
        if (!overlay || !overlay.contains(event.detail.elt)) return;
        overlay.querySelector('#hub-folder-body').textContent = event.detail.xhr.responseText;
    });
    return {open: open, close: close};
})();
