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
    return Array.from(document.querySelectorAll('.env-rename-row')).filter(function (row) {
      return row.querySelector('.env-rename-confirm:checked');
    }).map(function (row) {
      return {class: row.dataset.class, old: row.dataset.old, new: row.dataset.new,
        confirmed: true, meaning_unchanged: !!row.querySelector('.env-rename-meaning:checked')};
    });
  }
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
}());
