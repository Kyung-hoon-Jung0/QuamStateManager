/* Shared inline snapshot labels for every version listing. */
(function () {
    'use strict';
    function cancel(editor) {
        var form = editor.querySelector('form');
        form.reset();
        form.hidden = true;
        var button = editor.querySelector('[data-version-label-open]');
        button.hidden = false;
        button.focus();
    }
    document.addEventListener('click', function (e) {
        var button = e.target.closest('[data-version-label-open], [data-version-label-cancel]');
        if (!button) return;
        var editor = button.closest('.version-label-edit');
        if (button.hasAttribute('data-version-label-cancel')) { cancel(editor); return; }
        button.hidden = true;
        var form = editor.querySelector('form');
        form.hidden = false;
        var input = form.elements.label;
        input.focus();
        input.select();
    });
    // Capture Escape before the page's drawer and panel dismiss handlers.
    document.addEventListener('keydown', function (e) {
        var editor = e.target.closest('.version-label-edit');
        if (!editor || editor.querySelector('form').hidden) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            e.stopImmediatePropagation();
            cancel(editor);
        } else if (e.key === 'Enter' && e.target.name === 'label') {
            e.preventDefault();
            e.stopImmediatePropagation();
            editor.querySelector('form').requestSubmit();
        }
    }, true);
})();
