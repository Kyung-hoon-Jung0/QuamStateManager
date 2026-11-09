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
    // Only this popup's own run is ever stopped; with no id there is nothing of ours.
    if (!runId) return Promise.resolve({ok: true, stopping: false});
    return post('/port-play/stop', {id: runId});
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
      var what = settings.output_mode || ('AI, gain ' + (settings.gain_db || 0) + ' dB');
      if (settings.source === 'loopback') what += ' (loopback)';
      [pin, what, Number(settings.offset).toFixed(6)].forEach(function (value) {
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
      '<p class="ui-note" role="status" data-message></p>' +
      '<section data-loopcal><h4>Loopback calibration</h4>' +
      '<label>Analog input (same FEM)<select data-ai><option value="1">AI 1</option><option value="2">AI 2</option></select></label>' +
      '<p class="ui-note" data-ai-saved></p>' +
      '<p class="ui-note">First calibrate the AI with nothing connected to it (once per FEM/AI and per gain_db; gain_db 0 here). ' +
      'Then connect this AO to that AI with one cable and calibrate the AO. Uses the safety box above and the network of step 2.</p>' +
      '<div><button type="button" class="btn-sm outline" data-cal-input>Calibrate AI (input open)</button> ' +
      '<button type="button" class="btn-sm outline" data-cal-output>Calibrate this AO (loopback)</button> ' +
      '<button type="button" class="btn-sm outline" data-export>Export script</button></div>' +
      '<ol class="ui-note" data-cal-progress></ol>' +
      '<p class="ui-note" role="status" data-cal-result></p>' +
      '<button type="button" class="btn-sm" data-cal-save disabled>Save calibrated offset</button></section></div>' +
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
        post('/port-play/confirm', data).then(function (c) {
          if (!c.ok) throw new Error(c.error);
          return post('/port-play/run', Object.assign({confirm_token: c.token}, data));
        }).then(function (j) {
          if (seq !== sequence || dialog !== node) {
            if (j.ok) post('/port-play/stop', {id: j.id});
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
    // ---- Loopback calibration (BRIEF3 E-1..E-4) ----
    var femKey = 'con' + con + '/' + port.slot, calId = null, calSeq = 0;
    var aiChoice = (source.spec().port_play_ai || {})[femKey];
    if (aiChoice) $('[data-ai]').value = String(aiChoice);
    function aiKey() { return femKey + '/ai' + $('[data-ai]').value; }
    function showAiSaved() {
      var e = (source.spec().port_offsets || {})[aiKey()];
      $('[data-ai-saved]').textContent = e
        ? 'Saved AI offset: ' + Number(e.offset).toFixed(6) + ' V (gain ' + (e.gain_db || 0) + ' dB).'
        : 'No saved AI offset for this AI yet: calibrate the AI first.';
      $('[data-cal-output]').disabled = !e;
    }
    function calMessage(v) { $('[data-cal-result]').textContent = v; }
    $('[data-ai]').onchange = function () {
      var spec = source.spec();
      (spec.port_play_ai || (spec.port_play_ai = {}))[femKey] = Number($('[data-ai]').value);
      source.changed(); showAiSaved();
    };
    function calBody(kind) {
      var data = params();
      data.kind = kind; data.ai = Number($('[data-ai]').value);
      return JSON.parse(JSON.stringify(data));
    }
    function calPoll(seq) {
      if (seq !== calSeq || dialog !== node || !calId) return;
      fetch('/port-test/status?id=' + encodeURIComponent(calId)).then(function (r) { return r.json(); }).then(function (st) {
        if (seq !== calSeq || dialog !== node) return;
        var list = $('[data-cal-progress]');
        list.replaceChildren();
        (st.progress || []).forEach(function (line) {
          var li = document.createElement('li'); li.textContent = line; list.appendChild(li);
        });
        if (st.state === 'running') {
          calMessage(st.stopping ? 'Stopping...' : 'Calibrating...');
          timer = setTimeout(function () { calPoll(seq); }, 1000);
          return;
        }
        var res = st.result || {};
        if (!res.ok) { calMessage(res.stopped ? 'Stopped.' : (res.error || 'Failed.')); return; }
        calMessage((res.converged ? 'Converged' : 'Not converged') + ': offset ' + Number(res.final_offset).toFixed(6) +
          ' V after ' + res.iters + ' iteration(s).' + (res.converged ? '' : ' Nothing to save; check the cable.'));
        $('[data-cal-save]').disabled = res.converged !== true;
      }).catch(function (e) { calMessage(e.message); });
    }
    function calibrate(kind) {
      if (busy) return;
      var data;
      try { data = calBody(kind); } catch (e) { calMessage(e.message); return; }
      if (!data.hardware_ok) { calMessage('Tick the safety box first.'); return; }
      busy = true; var seq = ++calSeq; sequence++; clearTimeout(timer);
      $('[data-cal-save]').disabled = true; $('[data-cal-progress]').replaceChildren();
      calMessage('Preparing calibration...');
      function start() {
        if (seq !== calSeq || dialog !== node) { busy = false; return; }
        post('/port-play/loopcal/confirm', data).then(function (c) {
          if (!c.ok) throw new Error(c.error);
          return post('/port-play/loopcal/run', Object.assign({confirm_token: c.token}, data));
        }).then(function (j) {
          if (seq !== calSeq || dialog !== node) { if (j.ok) post('/port-play/stop', {id: j.id}); busy = false; return; }
          if (j.stopping) { calMessage(j.error); timer = setTimeout(start, 500); return; }
          busy = false;
          if (!j.ok) { calMessage(j.error); return; }
          runId = calId = j.id; calPoll(seq);
        }).catch(function (e) { busy = false; calMessage(e.message); });
      }
      start();
    }
    $('[data-cal-input]').onclick = function () { calibrate('input'); };
    $('[data-cal-output]').onclick = function () { calibrate('output'); };
    $('[data-cal-save]').onclick = function () {
      if (!calId) return;
      post('/port-play/loopcal/save', {id: calId}).then(function (j) {
        if (!j.ok) { calMessage(j.error); return; }
        var spec = source.spec();
        (spec.port_offsets || (spec.port_offsets = {}))[j.pin] = j.entry;
        if (j.entry.output_mode) {
          $('[data-offset]').value = Number(j.entry.offset).toFixed(6);
          $('[data-mode]').value = j.entry.output_mode;
          $('[data-play]').textContent = 'Play again';
        }
        $('[data-cal-save]').disabled = true;
        source.changed(); showAiSaved();
        calMessage('Saved ' + j.pin + ' = ' + Number(j.entry.offset).toFixed(6) + ' V in the wizard draft.' +
          (j.entry.output_mode ? ' Press Play again to check it on the scope.' : ''));
      }).catch(function (e) { calMessage(e.message); });
    };
    $('[data-export]').onclick = function () {
      var data;
      try { data = calBody('output'); } catch (e) { calMessage(e.message); return; }
      fetch('/port-play/loopcal/script.py', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)}).then(function (r) {
        if (!r.ok) return r.json().then(function (j) { throw new Error(j.error); });
        var m = /filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') || '');
        return r.blob().then(function (b) {
          var a = document.createElement('a'); a.href = URL.createObjectURL(b);
          a.download = m ? m[1] : 'lf_offset.py'; document.body.appendChild(a); a.click(); a.remove();
          calMessage('Script downloaded: ' + a.download);
        });
      }).catch(function (e) { calMessage(e.message); });
    };
    showAiSaved();
    $('[data-close]').onclick = close;
    node.addEventListener('cancel', function (e) { e.preventDefault(); close(); });
    node.showModal(); $('[data-offset]').focus();
  }
  window.PortPlay = {open: open, close: close, renderOffsets: renderOffsets};
  window.addEventListener('pagehide', function () {
    sequence++; clearTimeout(timer);
    if (runId) navigator.sendBeacon('/port-play/stop?left=1&id=' + encodeURIComponent(runId));
  });
  document.addEventListener('htmx:beforeSwap', function (e) {
    if (dialog && e.detail && e.detail.target && e.detail.target.id === 'table-pane') close();
  });
})();
