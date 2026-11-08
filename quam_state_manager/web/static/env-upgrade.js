(function () {
  'use strict';
  let tested = null;
  async function post(action, body) {
    const response = await fetch('/env-upgrade/' + action, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})
    });
    const result = await response.json();
    if (!response.ok || result.ok === false) throw new Error(result.error || 'Action failed');
    return result;
  }
  function notice(text) {
    const node = document.getElementById('env-rename-result') || document.getElementById('env-upgrade-status');
    if (node) node.textContent = text;
    else if (window.showToast) window.showToast(text, 'info');
  }
  function selections() {
    const removals = Array.from(document.querySelectorAll('.env-remove-row')).filter(function (row) {
      return row.querySelector('.env-remove-confirm:checked');
    }).map(function (row) {
      return {class: row.dataset.class, field: row.dataset.field, remove: true, confirmed: true};
    });
    return removals.concat(renames());
  }
  function renames() {
    return Array.from(document.querySelectorAll('.env-rename-row')).filter(function (row) {
      return row.querySelector('.env-rename-confirm:checked');
    }).map(function (row) {
      return {class: row.dataset.class, old: row.dataset.old, new: row.dataset.new,
        confirmed: true, meaning_unchanged: !!row.querySelector('.env-rename-meaning:checked')};
    });
  }
  function autoSyncOn() {
    const tray = document.getElementById('pending-tray');
    return !!(tray && tray.getAttribute('data-auto-apply') === '1');
  }
  // FB-02: the numbered steps say where the user is and where a Stage lands
  function setStep(n, why) {
    const steps = document.getElementById('env-rename-steps');
    if (steps) {
      Array.from(steps.querySelectorAll('li[data-step]')).forEach(function (li) {
        const k = parseInt(li.dataset.step, 10);
        li.classList.toggle('is-current', k === n);
        li.classList.toggle('is-done', k < n);
      });
      const live = autoSyncOn();
      steps.querySelectorAll('.env-rename-where').forEach(function (node) {
        node.textContent = live ? 'writes them to the chip at once (Auto-Sync is on).'
          : 'adds them to your pending edits (the sync control at the top).';
      });
      steps.querySelectorAll('.env-rename-apply-step').forEach(function (node) {
        node.textContent = live ? 'Nothing more to press: Auto-Sync already wrote the chip.'
          : 'Apply from the sync control at the top to write the chip.';
      });
    }
    const reason = document.getElementById('env-rename-why');
    if (reason) reason.textContent = why || '';
  }
  function syncStep() {
    if (tested) { setStep(3, autoSyncOn() ? 'Stage writes the chip at once: Auto-Sync is on.' : ''); return; }
    setStep(selections().length ? 2 : 1, selections().length ? 'Stage needs a passing Test load first.'
      : 'Tick a change, then run Test load.');
  }
  document.addEventListener('change', function (event) {
    const t = event.target;
    if (!t || !t.classList) return;
    if (t.classList.contains('env-remove-confirm') || t.classList.contains('env-rename-confirm')
        || t.classList.contains('env-rename-meaning')) {
      // a changed selection voids the previous test
      if (tested) { tested = null; const b = document.getElementById('env-rename-apply'); if (b) b.disabled = true; }
      syncStep();
    }
  });
  // FB-02: after Stage the card re-renders without the review rows; the line
  // saying where the changes went (and how to finish) must survive that
  let stagedNote = null;
  function showStagedNote() {
    if (!stagedNote) return;
    const status = document.getElementById('env-upgrade-status');
    if (!status || status.querySelector('.env-staged-note')) return;
    status.textContent = '';
    const line = document.createElement('span');
    line.className = 'env-staged-note';
    line.textContent = stagedNote.text + ' ';
    status.appendChild(line);
    if (!stagedNote.live) {
      const go = document.createElement('button');
      go.type = 'button';
      go.className = 'btn-xs outline env-staged-go';
      go.textContent = 'Show the Apply button';
      go.addEventListener('click', function () {
        const tray = document.getElementById('pending-tray');
        const apply = tray && Array.from(tray.querySelectorAll('button')).find(function (b) { return /Apply/.test(b.textContent); });
        window.scrollTo(0, 0);
        if (apply) { apply.focus(); apply.classList.add('env-staged-pulse'); setTimeout(function () { apply.classList.remove('env-staged-pulse'); }, 2000); }
      });
      status.appendChild(go);
    }
  }
  document.addEventListener('htmx:afterSettle', function () {
    if (document.getElementById('env-rename-steps')) syncStep();
    showStagedNote();
  });
  function skippedText(skipped) {
    // readable items; nothing at all when there is nothing to say
    if (!skipped || !skipped.length) return '';
    return '; skipped: ' + skipped.map(function (s) {
      return typeof s === 'string' ? s : (s.path || '') + (s.reason ? ' (' + s.reason + ')' : '');
    }).join(', ');
  }
  function trayCount() {
    const tray = document.getElementById('pending-tray');
    const n = tray ? parseInt(tray.getAttribute('data-change-count'), 10) : NaN;
    return isNaN(n) ? null : n;
  }
  // G8: the staged edits were undone or the state was replaced -- the card's
  // "Working copy updated" line and its ticks no longer describe anything
  let stagedAt = null;
  let awaitingTray = false;
  function resetReview() {
    tested = null;
    stagedNote = null;
    const staged = document.querySelector('.env-staged-note');
    if (staged && staged.parentNode) staged.parentNode.textContent = '';
    stagedAt = null;
    awaitingTray = false;
    Array.from(document.querySelectorAll('.env-rename-confirm, .env-rename-meaning, .env-remove-confirm'))
      .forEach(function (box) { box.checked = false; });
    const apply = document.getElementById('env-rename-apply');
    if (apply) apply.disabled = true;
    const result = document.getElementById('env-rename-result');
    if (result) result.textContent = '';
    const status = document.getElementById('env-upgrade-status');
    if (status && /^Working copy updated/.test(status.textContent)) status.textContent = '';
    syncStep();
  }
  document.addEventListener('stateRestored', resetReview);
  document.addEventListener('sm:tray-swapped', function () {
    const now = trayCount();
    if (awaitingTray && now) { stagedAt = now; awaitingTray = false; return; }
    if (stagedAt !== null && now !== null && now < stagedAt) resetReview();
  });
  function refreshCard() {
    // the card re-polls itself (hx-trigger every 2s) while the check runs
    if (window.htmx && document.getElementById('diag-env-card')) {
      window.htmx.ajax('GET', '/diagnostics/env-card?poll=1', {target: '#diag-env-card', swap: 'outerHTML'});
    }
  }
  function replaceReport(html) {
    const target = document.getElementById('env-upgrade-report');
    if (!target) return;
    const holder = document.createElement('div');
    holder.innerHTML = html;
    const fresh = holder.querySelector('#env-upgrade-report');
    if (!fresh) return;
    target.replaceWith(fresh);
    if (window.htmx) window.htmx.process(fresh);
  }
  window.EnvUpgrade = {
    _replaceReport: replaceReport,
    _resetReview: resetReview,
    _skippedText: skippedText,
    _syncStep: function () { syncStep(); },
    check: async function (python) {
      const button = document.getElementById('env-upgrade-check');
      if (button) button.disabled = true;
      try {
        await post('check', {python: python});
        notice('Checking…');
        refreshCard();
      } catch (error) { notice(error.message); if (button) button.disabled = false; }
    },
    acknowledge: async function () {
      try { await post('acknowledge'); window.htmx.ajax('GET', '/diagnostics/env-card', '#diag-env-card'); }
      catch (error) { notice(error.message); }
    },
    copyText: async function () {
      try { await navigator.clipboard.writeText(await (await fetch('/env-upgrade/text')).text()); notice('Copied report.'); }
      catch (error) { notice(error.message); }
    },
    testRenames: async function () {
      tested = null;
      const button = document.getElementById('env-rename-apply');
      button.disabled = true;
      try {
        const selected = selections();
        if (!selected.length) { notice('Tick a rename candidate or a removal first.'); syncStep(); return; }
        notice('Running Quam.load in this env…');
        const result = await post('rename-test', {selections: selected});
        tested = {token: result.token, selections: selected};
        button.disabled = false;
        syncStep();
        notice(result.message + ': ' + result.moves.length + ' renames, ' + result.removals + ' removals' +
          skippedText(result.skipped));
      } catch (error) { notice(error.message); syncStep(); }
    },
    applyRenames: async function () {
      if (!tested) return;
      try {
        const result = await post('rename-apply', {token: tested.token, selections: selections()});
        tested = null;
        document.getElementById('env-rename-apply').disabled = true;
        const live = autoSyncOn();
        setStep(4, '');
        const text = 'Staged ' + result.changes + ' change' + (result.changes === 1 ? '' : 's') + ' - ' +
          (live ? 'Auto-Sync is on, so they were written to the chip now. Ctrl+Z undoes them.'
            : 'Apply from the sync control at the top to write the chip. Ctrl+Z undoes them.') +
          skippedText(result.skipped);
        notice(text);
        stagedNote = {text: text, live: live};
        document.body.dispatchEvent(new CustomEvent('diagnostics-changed'));
        // the tray count after the staged edits land; a later drop means an undo
        awaitingTray = true;
        // the staged edits must reach the top-bar tray now (same call the Live-Edit paths use)
        if (window.htmx && document.getElementById('pending-tray')) {
          try { await window.htmx.ajax('GET', '/state/tray', {target: '#pending-tray', swap: 'outerHTML'}); }
          catch (ignored) { /* the tray refreshes on its own poll otherwise */ }
        }
        refreshCard();  // the matched-state compare runs server-side; the card polls until it ends
      } catch (error) { notice(error.message); }
    },
    compare: async function (button) {
      const row = button.closest('.env-reference');
      button.disabled = true;
      try {
        const response = await fetch('/env-upgrade/compare', {method: 'POST',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify({reference: button.dataset.key,
            python: row.querySelector('.env-reference-python').value,
            name_filter: row.querySelector('.env-reference-filter').value})});
        if (!response.ok) {
          let reason = 'Comparison could not finish';
          try { reason = (await response.json()).error || reason; } catch (ignored) { /* not JSON */ }
          throw new Error(reason);
        }
        const html = await response.text();
        replaceReport(html);
      } catch (error) { notice(error.message); } finally { button.disabled = false; }
    },
    removeReference: async function (button) {
      try { await post('delete', {reference: button.dataset.key}); button.closest('.env-reference').remove(); }
      catch (error) { notice(error.message); }
    }
  };
  // E1: the probe / deep-validate buttons live in the card's action row but belong to the
  // Diagnostics env card. A card-only swap (Record reference) re-renders the card without
  // them, so the whole env card is fetched once to bring them back.
  if (!window.__envUpgradeDiagRefill) {
    window.__envUpgradeDiagRefill = true;
    document.addEventListener('htmx:afterSettle', function () {
      const card = document.getElementById('env-upgrade-report');
      const outer = document.getElementById('diag-env-card');
      if (!card || !outer || !outer.contains(card) || !outer.hasAttribute('data-diag-actions')) return;
      if (card.querySelector('[data-diag-action]')) return;
      const now = Date.now();
      if (now - (window.__envUpgradeDiagRefillAt || 0) < 5000) return;   // never a loop
      window.__envUpgradeDiagRefillAt = now;
      if (window.htmx) window.htmx.ajax('GET', '/diagnostics/env-card', {target: '#diag-env-card', swap: 'outerHTML'});
    });
  }
}());
