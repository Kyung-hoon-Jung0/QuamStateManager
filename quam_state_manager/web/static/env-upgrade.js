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
    const node = document.getElementById('env-rename-result');
    if (node) node.textContent = text;
    else if (window.showToast) window.showToast(text, 'info');
  }
  function selections() {
    return Array.from(document.querySelectorAll('.env-rename-row')).filter(function (row) {
      return row.querySelector('.env-rename-confirm:checked');
    }).map(function (row) {
      return {class: row.dataset.class, old: row.dataset.old, new: row.dataset.new,
        confirmed: true, meaning_unchanged: !!row.querySelector('.env-rename-meaning:checked')};
    });
  }
  window.EnvUpgrade = {
    check: async function (python) {
      try { await post('check', {python: python}); notice('Check queued; refresh Diagnostics when it finishes.'); }
      catch (error) { notice(error.message); }
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
        const result = await post('rename-test', {selections: selected});
        tested = {token: result.token, selections: selected};
        button.disabled = false;
        notice('Load completed. ' + result.moves.length + ' moves; skipped: ' + JSON.stringify(result.skipped));
      } catch (error) { notice(error.message); }
    },
    applyRenames: async function () {
      if (!tested) return;
      try {
        const result = await post('rename-apply', {token: tested.token, selections: selections()});
        tested = null;
        document.getElementById('env-rename-apply').disabled = true;
        notice(result.message + '; skipped: ' + JSON.stringify(result.skipped));
        document.body.dispatchEvent(new CustomEvent('diagnostics-changed'));
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
        if (!response.ok) throw new Error('Comparison could not finish');
        const html = await response.text();
        const target = document.getElementById('env-upgrade-report');
        if (window.htmx) window.htmx.swap(target, html, {swapStyle: 'outerHTML'});
        else target.outerHTML = html;
      } catch (error) { notice(error.message); } finally { button.disabled = false; }
    },
    removeReference: async function (button) {
      try { await post('delete', {reference: button.dataset.key}); button.closest('.env-reference').remove(); }
      catch (error) { notice(error.message); }
    }
  };
}());
