/* docs/167 — the notes panel's client.
 *
 * Small on purpose. The panel is server-rendered and every mutation returns
 * the re-rendered panel, so this file owns no model: it posts, swaps the HTML
 * the server sent back, and gets out of the way. That is the same discipline
 * the rest of the app uses for the Review tray, and it is why a note written
 * in one window shows up correctly in another as soon as anything re-renders.
 *
 * The one piece of real logic is the compare-and-swap: the `rev` the row was
 * rendered with is sent back with an edit, so a note somebody else changed
 * meanwhile comes back as a 409 carrying THEIR text instead of being
 * overwritten. The user then decides — which is the same shape as the FSP
 * compensation offer and docs/120's unseen-edit gate.
 */
(function () {
    'use strict';
    if (window.EntityNotes) return;

    function panel() { return document.getElementById('notes-panel'); }

    function swap(html) {
        var el = panel();
        if (el && html) {
            el.outerHTML = html;
            var block = document.getElementById('notes-block');
            var p = document.getElementById('notes-panel');
            if (block && p) {
                var n = p.getAttribute('data-count') || '0';
                var sum = block.querySelector('.notes-summary');
                if (sum) {
                    // Keep the summary honest without re-rendering the grid it
                    // sits above: the count is the panel's own attribute.
                    var c = sum.querySelector('.notes-count');
                    if (n === '0') { if (c) c.remove(); }
                    else if (c) { c.textContent = n; }
                    else {
                        c = document.createElement('span');
                        c.className = 'notes-count';
                        c.textContent = n;
                        sum.appendChild(document.createTextNode(' '));
                        sum.appendChild(c);
                    }
                }
            }
        }
    }

    function post(url, body) {
        var form = new FormData();
        Object.keys(body).forEach(function (k) {
            if (body[k] !== null && body[k] !== undefined) form.append(k, body[k]);
        });
        return fetch(url, { method: 'POST', body: form,
                            headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.json().then(function (j) {
                return { status: r.status, body: j }; }); });
    }

    function say(msg, kind) {
        if (window.showToast) window.showToast(msg, kind || undefined);
    }

    function handle(res) {
        if (res.body && res.body.panel) swap(res.body.panel);
        if (res.status === 409 && res.body && res.body.note_conflict) {
            var theirs = (res.body.stored && res.body.stored.text) || '';
            say('Not saved — somebody else changed this note. Theirs now reads: '
                + theirs, 'warning');
            return false;
        }
        if (!res.body || res.body.ok === false) {
            say((res.body && res.body.error) || 'The note could not be saved.',
                'warning');
            return false;
        }
        return true;
    }

    function chipToken() {
        return (window.__bulkChipKey || '');
    }

    document.addEventListener('click', function (ev) {
        var t = ev.target;
        if (!t || !t.classList) return;
        var item = t.closest ? t.closest('.notes-item') : null;

        if (t.classList.contains('notes-add-go')) {
            var host = t.closest('.notes-add');
            var subj = host.querySelector('.notes-add-subject');
            var text = host.querySelector('.notes-add-text');
            if (!subj.value.trim() || !text.value.trim()) {
                say('A note needs both a subject and some text.', 'warning');
                return;
            }
            var tuned = host.querySelector('.notes-add-tuned-cb');
            post('/note', { subject: subj.value.trim(), text: text.value.trim(),
                            hand_tuned: (tuned && tuned.checked) ? '1' : '' })
                .then(handle);
            return;
        }
        if (!item) return;
        var subject = item.getAttribute('data-subject');

        if (t.classList.contains('notes-del')) {
            post('/note/delete', { subject: subject }).then(handle);
            return;
        }
        if (t.classList.contains('notes-edit')) {
            var cur = item.querySelector('.notes-text');
            var next = window.prompt('Note about ' + subject, cur ? cur.textContent.trim() : '');
            if (next === null) return;
            if (!next.trim()) { say('Use × to delete a note.', 'warning'); return; }
            // The rev the row was RENDERED with — the compare-and-swap token.
            // An edit must not silently drop the mark the row is showing.
            var wasTuned = !!item.querySelector('.notes-tuned');
            post('/note', { subject: subject, text: next.trim(),
                            hand_tuned: wasTuned ? '1' : '',
                            expect_rev: item.getAttribute('data-rev') })
                .then(handle);
            return;
        }
        if (t.classList.contains('notes-readdress')) {
            var to = window.prompt(
                'Point this note at a path the chip does have:', subject);
            if (to === null || !to.trim()) return;
            post('/note/readdress', { subject: subject, new_subject: to.trim() })
                .then(handle);
        }
    });

    window.EntityNotes = { swap: swap, _post: post };
})();
