/* Wizard scope checks use one shared port-test runner and the session draft. */
(function () {
  'use strict';
  if (window.PortPlay) return;
  var dialog = null, runId = null, sequence = 0, timer = null;
  function post(url, data) {
    return fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data)}).then(function (r) { return r.json(); });
  }
  function stop() {
    sequence++;
    clearTimeout(timer);
    return post('/port-test/stop', runId ? {id: runId} : {});
  }
  function close() {
    stop().catch(function () {});
    if (dialog) dialog.remove();
    dialog = null;
  }
  function renderOffsets(spec, changed) {
    var table = document.getElementById('gen-port-offsets');
    if (!table) return;
    table.replaceChildren();
    Object.keys(spec.port_offsets || {}).sort().forEach(function (pin) {
      var row = document.createElement('tr'), settings = spec.port_offsets[pin];
      [pin, settings.output_mode, Number(settings.offset).toFixed(6)].forEach(function (value) {
        var cell = document.createElement('td'); cell.textContent = value; row.appendChild(cell);
      });
      var cell = document.createElement('td'), remove = document.createElement('button');
      remove.type = 'button'; remove.className = 'btn-sm outline'; remove.textContent = 'Remove';
      remove.onclick = function () { delete spec.port_offsets[pin]; changed(); };
      cell.appendChild(remove); row.appendChild(cell); table.appendChild(row);
    });
    document.getElementById('gen-port-offsets-copy').onclick = function () {
      navigator.clipboard.writeText(JSON.stringify(spec.port_offsets || {}, null, 2)).then(function () {
        document.getElementById('gen-port-offsets-message').textContent = 'Copied JSON.';
      }).catch(function (e) {
        document.getElementById('gen-port-offsets-message').textContent = e.message;
      });
    };
  }
  function open(port, source) {
    if (dialog) close();
    var con = String(port.con).replace(/^con/, '');
    var pin = 'con' + con + '/' + port.slot + '/' + port.port;
    var saved = (source.spec().port_offsets || {})[pin] || {offset: 0, output_mode: 'direct'};
    var node = document.createElement('dialog');
    node.id = 'port-play-dialog'; node.setAttribute('aria-labelledby', 'port-play-title');
    node.innerHTML = '<article class="ui-dialog ui-dialog--s">' +
      '<div class="ui-dialog-head"><header class="ui-head"><h3 class="ui-head-title" id="port-play-title"></h3></header></div>' +
      '<div class="ui-dialog-body"><label>Output mode<select data-mode><option value="direct">direct</option><option value="amplified">amplified</option></select></label>' +
      '<label>Offset (V)<input data-offset type="number" step="0.000001" required></label>' +
      '<label>Max play time (s)<input data-seconds type="number" min="0.1" max="3600" step="0.1" value="300" required></label>' +
      '<label><input data-hwok type="checkbox"> This port is safe to drive (scope cable, or a DC offset on this line is intended)</label>' +
      '<p class="ui-note">Plays a zero-amplitude waveform. Closing the QM resets OPX1000 outputs to 0 V.</p>' +
      '<p class="ui-note" role="status" data-message></p></div>' +
      '<div class="ui-dialog-foot"><button type="button" class="btn-sm" data-play>Play</button>' +
      '<button type="button" class="btn-sm outline" data-stop>Stop</button>' +
      '<button type="button" class="btn-sm outline" data-save>Save offset</button>' +
      '<button type="button" class="btn-sm outline" data-close>Close</button></div></article>';
    document.body.appendChild(node); dialog = node;
    var $ = function (s) { return node.querySelector(s); }, busy = false;
    $('#port-play-title').textContent = 'con' + con + ' / FEM ' + port.slot + ' / AO ' + port.port;
    $('[data-offset]').value = Number(saved.offset).toFixed(6);
    $('[data-mode]').value = saved.output_mode;
    function message(value) { $('[data-message]').textContent = value; }
    function params() {
      var input = $('[data-offset]'), seconds = $('[data-seconds]'), mode = $('[data-mode]').value;
      var limit = mode === 'amplified' ? 2.5 : 0.5;
      if (!input.checkValidity() || !seconds.checkValidity() || !Number.isFinite(Number(input.value)))
        throw new Error('Enter an offset and a maximum time in (0, 3600] seconds.');
      if (Math.abs(Number(input.value)) > limit)
        throw new Error(mode + ' mode requires |offset| <= ' + limit + ' V.');
      return {con: 'con' + con, slot: port.slot, port: port.port, offset: Number(input.value),
        output_mode: mode, hardware_ok: !!$('[data-hwok]').checked, max_s: Number(seconds.value), spec: source.spec(), python: source.python()};
    }
    function poll(seq) {
      if (seq !== sequence || dialog !== node || !runId) return;
      fetch('/port-test/status?id=' + encodeURIComponent(runId)).then(function (r) { return r.json(); }).then(function (st) {
        if (seq !== sequence || dialog !== node) return;
        if (st.state === 'running') {
          message(st.stopping ? 'Stopping...' : 'Playing... ' + (st.phase || ''));
          timer = setTimeout(function () { poll(seq); }, 1000);
        } else message(st.result && !st.result.ok ? st.result.error : 'Stopped.');
      }).catch(function (e) { message(e.message); });
    }
    $('[data-play]').onclick = function () {
      if (busy) return;
      var data;
      try { data = params(); } catch (e) { message(e.message); return; }
      // Snapshot wizard values across retries; later edits belong to the next play.
      if (!data.hardware_ok) { message('Tick the safety box first.'); return; }
      data = JSON.parse(JSON.stringify(data));
      busy = true; var seq = ++sequence; clearTimeout(timer); message('Preparing Play...');
      function start() {
        if (seq !== sequence || dialog !== node) { busy = false; return; }
        post('/port-play/run', data).then(function (j) {
          if (seq !== sequence || dialog !== node) {
            if (j.ok) post('/port-test/stop', {id: j.id});
            busy = false; return;
          }
          if (j.stopping) { message(j.error); timer = setTimeout(start, 500); return; }
          busy = false;
          if (!j.ok) { message(j.error); return; }
          runId = j.id; $('[data-play]').textContent = 'Play again'; poll(seq);
        }).catch(function (e) { busy = false; message(e.message); });
      }
      start();
    };
    $('[data-stop]').onclick = function () {
      busy = false; stop().then(function () { message('Stopping...'); poll(sequence); });
    };
    $('[data-save]').onclick = function () {
      try {
        var data = params(), spec = source.spec();
        // A popup can outlive a chassis edit. Never save onto a reassigned FEM.
        var ctrl = spec.instruments.controllers.find(function (c) { return String(c.con) === con; });
        if (!ctrl || !ctrl.fems.some(function (f) { return String(f.slot) === String(port.slot) && f.fem === 'lf'; }))
          throw new Error('The LF-FEM allocation changed. Reopen the port.');
        (spec.port_offsets || (spec.port_offsets = {}))[pin] = {offset: data.offset, output_mode: data.output_mode};
        source.changed(); message('Saved in wizard draft. Build applies this offset.');
      } catch (e) { message(e.message); }
    };
    $('[data-close]').onclick = close;
    node.addEventListener('cancel', function (e) { e.preventDefault(); close(); });
    node.showModal(); $('[data-offset]').focus();
  }
  window.PortPlay = {open: open, close: close, renderOffsets: renderOffsets};
  window.addEventListener('pagehide', function () {
    sequence++; clearTimeout(timer);
    if (dialog || runId) navigator.sendBeacon('/port-test/stop?left=1' + (runId ? '&id=' + encodeURIComponent(runId) : ''));
  });
  document.addEventListener('htmx:beforeSwap', function (e) {
    if (dialog && e.detail && e.detail.target && e.detail.target.id === 'table-pane') close();
  });
})();
