/* Wizard scope checks use one shared port-test runner and the session draft. */
(function () {
  'use strict';
  if (window.PortPlay) return;
  var dialog = null, runId = null, sequence = 0, timer = null, liveHw = false, liveSet = null;
  // Rack contract (S3): the running hardware set, announced by one document event.
  function setLive(v) {
    var before = JSON.stringify(liveSet);
    liveSet = v;
    if (JSON.stringify(liveSet) !== before) document.dispatchEvent(new CustomEvent('port-play:change', {detail: liveSet}));
  }
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
  // Close always stops (decisions R1); the set itself is remembered (D-5).
  function close() {
    stop().catch(function () {});
    // The stop request already carries the id; the next popup starts with no run of its own.
    runId = null;
    if (dialog) dialog.remove();
    dialog = null; liveHw = false; setLive(null);
  }
  function esc(v) {
    return String(v).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; });
  }
  // ---- One port notation (D-12) ----
  function femWord(kind) { return kind === 'mw' ? 'OUT' : 'AO'; }
  function portLabel(o) {
    var con = 'con' + String(o.con).replace(/^con/, '');
    return con + ' \u00b7 FEM ' + o.slot + ' \u00b7 ' + femWord(o.kind) + ' ' + o.port + ' (' + String(o.kind).toUpperCase() + ')';
  }
  function renderOffsets(spec, changed) {
    var table = document.getElementById('gen-port-offsets');
    if (!table) return;
    table.replaceChildren();
    var pins = Object.keys(spec.port_offsets || {}).sort();
    if (!pins.length) {
      // D-9: an empty table says so.
      var empty = document.createElement('tr'), cell0 = document.createElement('td');
      cell0.colSpan = 4; cell0.className = 'ui-table-muted'; cell0.textContent = 'No saved offsets yet';
      empty.setAttribute('data-empty', ''); empty.appendChild(cell0); table.appendChild(empty);
    }
    pins.forEach(function (pin) {
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
    var copy = document.getElementById('gen-port-offsets-copy');
    if (copy) copy.onclick = function () {
      navigator.clipboard.writeText(JSON.stringify(spec.port_offsets || {}, null, 2)).then(function () {
        document.getElementById('gen-port-offsets-message').textContent = 'Copied JSON.';
      }).catch(function (e) {
        document.getElementById('gen-port-offsets-message').textContent = e.message;
      });
    };
  }
  // Our previous play is stopping: wait on its status (always 200), then start once.
  // Re-pressing run every 500 ms answered 409 each time and filled the console.
  function waitStopped(oldId, seq, then) {
    var delay = 400;
    function check() {
      if (seq !== sequence) return;
      fetch('/port-test/status?id=' + encodeURIComponent(oldId || '')).then(function (r) { return r.json(); })
        .then(function (st) {
          if (seq !== sequence) return;
          if (st.state === 'running') { delay = Math.min(delay * 1.5, 2000); timer = setTimeout(check, delay); }
          else then();
        }).catch(function () { if (seq === sequence) timer = setTimeout(check, 2000); });
    }
    timer = setTimeout(check, delay);
  }

  // ---- Simulation result (D-8, D-13) ----
  function traceInfo(key, t) {
    // D-17 fields; absent fields degrade to "every analog trace is a member".
    var kind = t.kind || (t.im ? 'mw' : 'lf');
    var labelled = t.con !== undefined && t.slot !== undefined && t.port !== undefined;
    return {key: key, t: t, kind: kind, member: t.member === undefined ? true : !!t.member,
      label: labelled ? portLabel({con: t.con, slot: t.slot, port: t.port, kind: kind}) : null};
  }
  function ysOf(info) {
    var re = info.t.re || [], im = info.t.im;
    if (info.kind !== 'mw' || !im) return re.slice();
    return re.map(function (v, i) { return Math.sqrt(v * v + (im[i] || 0) * (im[i] || 0)); });
  }
  function draw(div, kind, infos) {
    // S2's runner reports each trace's sampling rate; the x axis is real ns, not sample index.
    var step = function (i) { var r = Number(i.t.rate); return (Number(i.t.step) || 1) * (r > 0 ? 1e9 / r : 1); };
    var data = infos.map(function (i) {
      var ys = ysOf(i);
      return {type: 'scatter', mode: 'lines', name: i.label || i.key,
        x: ys.map(function (_, n) { return n * step(i); }), y: ys};
    });
    var lo = Infinity, hi = -Infinity;
    data.forEach(function (d) { d.y.forEach(function (v) { if (v < lo) lo = v; if (v > hi) hi = v; }); });
    if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
    var pad = (hi - lo) * 0.05 || 0.05;
    var color = (window.getComputedStyle && document.body) ? window.getComputedStyle(document.body).color : undefined;
    var bg = window.getComputedStyle ? window.getComputedStyle(document.documentElement).getPropertyValue('--ui-surface-1').trim() : '';
    var layout = {height: 260, margin: {l: 60, r: 10, t: 10, b: 40}, showlegend: true,
      paper_bgcolor: bg || undefined, plot_bgcolor: bg || undefined, font: color ? {color: color} : {},
      xaxis: {title: {text: 'time (ns)'}},
      yaxis: {title: {text: kind === 'mw' ? '|IQ| (fraction of full scale)' : 'voltage (V)'}, range: [lo - pad, hi + pad]}};
    // Traces that coincide draw on top of each other; say so instead of showing one colour.
    var seen = {}, overlap = 0;
    data.forEach(function (d) { var k = JSON.stringify(d.x) + '|' + JSON.stringify(d.y); seen[k] = (seen[k] || 0) + 1; });
    Object.keys(seen).forEach(function (k) { if (seen[k] > 1) overlap += seen[k]; });
    if (overlap > 1 && div.parentNode) {
      var note = document.createElement('p');
      note.className = 'ui-note'; note.setAttribute('data-sim-overlap', kind);
      note.textContent = overlap + ' traces overlap; see the table.';
      div.parentNode.insertBefore(note, div.nextSibling);
    }
    var plot = function (P) { if (P && P.newPlot) P.newPlot(div, data, layout, {responsive: true, displaylogo: false}); };
    if (window.Plotly) plot(window.Plotly);
    else if (window.requirePlotly) window.requirePlotly().then(plot).catch(function () {});
  }
  function playsByPin(plan) {
    var map = {};
    (plan.plays || []).forEach(function (p) {
      // S2's plan names a play's output as port: [con, slot, port].
      var at = p && Array.isArray(p.port) ? {con: p.port[0], slot: p.port[1], port: p.port[2]} : p;
      if (at && at.con !== undefined) map[String(at.con).replace(/^con/, '') + '/' + at.slot + '/' + at.port] = p;
    });
    return map;
  }
  function fmt(v, digits) { return Number.isFinite(Number(v)) && v !== null ? Number(v).toPrecision(digits || 4) : '\u2014'; }
  function showSim(node, res, plan, setOrder) {
    var box = node.querySelector('[data-sim-result]');
    if (!box) return;
    var infos = Object.keys(res.traces || {}).filter(function (k) { return !res.traces[k].digital; }).sort()
      .map(function (k) { return traceInfo(k, res.traces[k]); });
    infos.forEach(function (i, n) { if (!i.label) i.label = 'Trace ' + (n + 1); });
    // Members follow the set order (row 1 first) when the traces are labelled.
    var rank = function (i) {
      var k = String(i.t.con).replace(/^con/, '') + '/' + i.t.slot + '/' + i.t.port, at = setOrder.indexOf(k);
      return at < 0 ? 1e6 : at;
    };
    var members = infos.filter(function (i) { return i.member; }).sort(function (a, b) { return rank(a) - rank(b); });
    var others = infos.filter(function (i) { return !i.member; });
    var html = '<div class="ui-item-head"><h4 class="ui-item-title">Simulated output</h4>' +
      '<span class="ui-badge ui-badge--warn" data-sim-label>Simulation, not hardware</span></div>' +
      '<p class="ui-note">' + esc(res.backend_label || 'Simulator') + ': the same program Play runs, simulated.</p>';
    if (!members.length) html += '<p class="ui-note">The simulator returned no analog samples.</p>';
    ['lf', 'mw'].forEach(function (kind) {
      if (members.some(function (i) { return i.kind === kind; }))
        html += '<div data-sim-fig="' + kind + '"></div>';
    });
    var plays = playsByPin(plan), single = (plan.plays || []).length === 0;
    // A held level has no edge to time: only an edge play measures a first edge (D-18).
    var timed = plan.waveform === 'edge';
    var edge0 = timed && members.length ? Number(members[0].t.first_edge_ns) : NaN;
    if (members.length) {
      html += '<table class="ui-table" data-sim-table><thead><tr>' +
        ['Port', 'Peak', 'Expected'].concat(timed ? ['First edge (ns)', 'Delta to row 1 (ns)'] : []).map(function (h) {
          return '<th class="ui-table-head">' + h + '</th>'; }).join('') + '</tr></thead><tbody>';
      members.forEach(function (i) {
        var k = String(i.t.con).replace(/^con/, '') + '/' + i.t.slot + '/' + i.t.port;
        var exp = plays[k] ? plays[k].expected_peak : (single ? (plan.expected_peak !== undefined ? plan.expected_peak : res.expected_peak) : null);
        var edge = Number(i.t.first_edge_ns);
        html += '<tr data-sim-port><td class="ui-table-cell">' + esc(i.label) + '</td>' +
          '<td class="ui-table-cell ui-table-num">' + fmt(i.t.peak) + '</td>' +
          '<td class="ui-table-cell ui-table-num">' + fmt(exp) + '</td>' +
          (timed ? '<td class="ui-table-cell ui-table-num">' + (Number.isFinite(edge) && i.t.first_edge_ns !== undefined ? edge : '\u2014') + '</td>' +
          '<td class="ui-table-cell ui-table-num">' + (Number.isFinite(edge) && Number.isFinite(edge0) && i.t.first_edge_ns !== undefined ? edge - edge0 : '\u2014') + '</td>' : '') + '</tr>';
      });
      html += '</tbody></table>';
    }
    if (others.length) {
      var mx = 0;
      others.forEach(function (i) { ysOf(i).forEach(function (v) { if (Math.abs(v) > mx) mx = Math.abs(v); }); });
      html += '<details class="ui-disclosure" data-sim-others><summary class="ui-disclosure-summary">' + others.length +
        ' other declared output' + (others.length === 1 ? '' : 's') + ' stayed at 0 V (max |x| ' + mx.toPrecision(3) +
        ')</summary><div class="ui-disclosure-body"><p class="ui-note">' +
        others.map(function (i) { return esc(i.label); }).join(', ') + '</p></div></details>';
    }
    if (res.local_error)
      html += '<details class="ui-disclosure" data-sim-details><summary class="ui-disclosure-summary">Details</summary>' +
        '<div class="ui-disclosure-body"><p class="ui-note">The local QOP did not answer: ' + esc(res.local_error) + '</p></div></details>';
    box.innerHTML = html;
    box.hidden = false;
    ['lf', 'mw'].forEach(function (kind) {
      var div = box.querySelector('[data-sim-fig="' + kind + '"]');
      if (div) draw(div, kind, members.filter(function (i) { return i.kind === kind; }));
    });
  }
  // D-8: a new press keeps the last result, collapsed, instead of discarding it.
  function clearSim(node) {
    var box = node.querySelector('[data-sim-result]'), prev = node.querySelector('[data-sim-prev]');
    if (!box) return;
    if (prev && !box.hidden && box.childNodes.length) {
      var body = prev.querySelector('.ui-disclosure-body');
      body.replaceChildren();
      while (box.firstChild) body.appendChild(box.firstChild);
      prev.open = false; prev.hidden = false;
    }
    box.hidden = true; box.replaceChildren();
  }

  // ---- Run driver ----
  function wirePlay(node, params, ui, lock) {
    var $ = function (s) { return node.querySelector(s); };
    var action = null, closing = false, sawRunning = false, lastData = null, stopPressed = false;
    function failure(res) {
      ui.message('');
      // Nothing runs after a failure: the next press is a plain Play, not a restart.
      sawRunning = false; $('[data-play]').textContent = 'Play';
      var box = $('[data-error]');
      box.innerHTML = '<div class="ui-notice ui-notice--danger" role="alert"><span class="ui-notice-text"></span></div>' +
        (res.detail && res.detail !== res.error ? '<details class="ui-disclosure"><summary class="ui-disclosure-summary">Details</summary>' +
          '<div class="ui-disclosure-body"><p class="ui-note" data-error-detail></p></div></details>' : '');
      box.querySelector('.ui-notice-text').textContent = res.error;
      if (box.querySelector('[data-error-detail]')) box.querySelector('[data-error-detail]').textContent = res.detail;
      if (res.error_kind === 'connect') {
        var a = document.createElement('a');
        a.href = '#'; a.setAttribute('data-edit-host', ''); a.textContent = ui.editHostLabel || 'Edit host (step 2)';
        a.onclick = function (e) {
          e.preventDefault(); close();
          if (ui.editHost) ui.editHost();
          else if (window.QuamGen && window.QuamGen.goToStep) window.QuamGen.goToStep(2);
        };
        box.querySelector('.ui-notice').appendChild(a);
      }
      box.hidden = false;
    }
    function poll(seq) {
      if (seq !== sequence || dialog !== node || !runId) return;
      fetch('/port-test/status?id=' + encodeURIComponent(runId)).then(function (r) { return r.json(); }).then(function (st) {
        if (seq !== sequence || dialog !== node) return;
        if (st.state === 'running') {
          liveHw = action !== 'sim';
          if (liveHw) setLive({id: runId, members: ui.members(), started_at: st.started_at || null,
            max_s: st.max_s !== undefined ? st.max_s : (lastData || {}).max_s});
          // D-6: "Play again" only once the run is seen running.
          if (action === 'play' && !sawRunning) { sawRunning = true; $('[data-play]').textContent = 'Play again'; }
          ui.message(st.stopping ? (closing ? 'Closing QM...' : 'Stopping...') : action === 'open'
            ? 'QM open \u2014 port is at its offset (no program running)'
            : (action === 'sim' ? 'Simulating (no hardware)... ' : 'Playing... ') + (st.phase || ''));
          timer = setTimeout(function () { poll(seq); }, 1000);
        } else {
          liveHw = false; setLive(null);
          var res = st.result || {};
          // A Stop pressed while the run was still connecting is a stop, not a failure.
          var failed = !res.ok && !res.stopped && res.error && !stopPressed;
          if (res.simulated && res.ok) {
            ui.message('Simulation finished: no hardware was driven.');
            showSim(node, res, (st.meta || {}).plan || {}, ui.order());
          } else if (failed) {
            failure(res);
          } else {
            // A play ended by Stop leaves nothing running: the next press is a plain Play.
            if (res.stopped || stopPressed || closing) { sawRunning = false; $('[data-play]').textContent = 'Play'; }
            ui.message(res.stopped ? 'Stopped.' : closing || action === 'open'
              ? 'QM closed \u2014 outputs returned to 0 V.' : 'Stopped.');
          }
          action = null;
        }
      }).catch(function (e) { ui.message(e.message); });
    }
    function launch(kind) {
      if (lock.busy) return;
      var data;
      $('[data-error]').hidden = true; $('[data-error]').replaceChildren();
      try { data = params(); } catch (e) { ui.refuse(e.message); return; }
      // Snapshot wizard values across retries; later edits belong to the next press.
      // A simulation drives nothing, so only a hardware press needs the safety box.
      if (kind !== 'sim' && !data.hardware_ok) { ui.refuse('Tick the safety box first.'); return; }
      data = JSON.parse(JSON.stringify(data)); data.action = kind; lastData = data;
      lock.busy = true; var seq = ++sequence; clearTimeout(timer); closing = false; sawRunning = false; stopPressed = false;
      clearSim(node);
      ui.message(kind === 'open' ? 'Opening QM...' : kind === 'sim' ? 'Preparing simulation...' : 'Preparing Play...');
      function start() {
        if (seq !== sequence || dialog !== node) { lock.busy = false; return; }
        post('/port-play/confirm', data).then(function (c) {
          if (!c.ok) throw new Error(c.error);
          return post('/port-play/run', Object.assign({confirm_token: c.token}, data));
        }).then(function (j) {
          if (seq !== sequence || dialog !== node) {
            if (j.ok) post('/port-play/stop', {id: j.id});
            lock.busy = false; return;
          }
          if (j.stopping) { ui.message(j.error); waitStopped(j.id, seq, start); return; }
          lock.busy = false;
          if (!j.ok) { ui.refuse(j.error); return; }
          runId = j.id; action = kind;
          if ($('[data-open-qm]')) $('[data-open-qm]').textContent = 'Open QM';
          poll(seq);
        }).catch(function (e) { lock.busy = false; ui.refuse(e.message); });
      }
      start();
    }
    if ($('[data-open-qm]')) $('[data-open-qm]').onclick = function () { launch('open'); };
    $('[data-play]').onclick = function () { launch('play'); };
    if ($('[data-sim]')) $('[data-sim]').onclick = function () { launch('sim'); };
    if ($('[data-close-qm]')) $('[data-close-qm]').onclick = function () {
      if (!runId || !action) { ui.message('No QM open from this popup.'); return; }
      lock.busy = false; closing = true;
      stop().then(function () { ui.message('Closing QM...'); poll(sequence); });
    };
    $('[data-stop]').onclick = function () {
      lock.busy = false; closing = false; stopPressed = !!runId; stop().then(function () { ui.message('Stopping...'); poll(sequence); });
    };
    // A value edit while our QM is open: the next Open re-opens with the new value.
    node.addEventListener('input', function (e) {
      if (action === 'open' && $('[data-open-qm]') && e.target && !e.target.matches('[data-hwok]')) $('[data-open-qm]').textContent = 'Open QM again';
    });
  }

  // ---- Hardware limits (mirrors of core/port_test.py; the server re-checks every field) ----
  // Mirror of run_build._BAND_TO_DELAY_NS, pinned by tests/test_regen_populate.py.
  var BAND_DELAY_NS = {1: 141, 2: 161, 3: 141};
  var MAX_DELAY_NS = 16000;  // QM docs Guides/features.md "## Analog Delay": up to 16 us
  var MW_LO_GHZ = {1: [0, 5.5], 2: [4.5, 7.5], 3: [6.5, 11]};
  // Band ranges: QM docs Guides/opx1000_fems.md "### Bands" (1: 50 MHz - 5.5 GHz,
  // 2: 4.5 GHz - 7.5 GHz, 3: 6.5 GHz - 10.5 GHz). One list for every band select.
  var BAND_RF_GHZ = {1: [0.05, 5.5], 2: [4.5, 7.5], 3: [6.5, 10.5]};
  var BAND_LABEL = {1: '1 (50 MHz - 5.5 GHz)', 2: '2 (4.5 - 7.5 GHz)', 3: '3 (6.5 - 10.5 GHz)'};
  var MAX_IN_KEY = 'quam_port_play_max_in_dbm';
  var SET_KEY = 'quam_gen_port_play_set';
  function bandOptions() {
    return [1, 2, 3].map(function (b) { return '<option value="' + b + '">' + BAND_LABEL[b] + '</option>'; }).join('');
  }
  function mwPower(fsp, amp) {
    return amp === 0 ? null : fsp + 20 * Math.log10(Math.abs(amp));
  }
  function bandOfRf(rf, current) {
    var fits = function (b) { return rf >= BAND_RF_GHZ[b][0] && rf <= BAND_RF_GHZ[b][1]; };
    if (fits(current)) return current;
    for (var b = 1; b <= 3; b++) if (fits(b)) return b;
    return null;
  }
  function outputsOf(spec) {
    var list = [];
    ((spec.instruments || {}).controllers || []).forEach(function (c) {
      (c.fems || []).forEach(function (f) {
        if (f.fem !== 'lf' && f.fem !== 'mw') return;
        for (var p = 1; p <= 8; p++)
          list.push({con: 'con' + String(c.con).replace(/^con/, ''), slot: Number(f.slot), port: p, kind: f.fem});
      });
    });
    return list;
  }
  function pinOf(o) { return o.con + '/' + o.slot + '/' + o.port; }
  function readStore(store, key) { try { return window[store].getItem(key); } catch (e) { return null; } }
  function writeStore(store, key, v) { try { window[store].setItem(key, v); } catch (e) { /* storage blocked */ } }

  // ---- The set popup: every output is one equal row (D-1..D-5, D-9..D-12) ----
  function open(port, source) {
    if (dialog) close();
    var kind0 = /^mw/.test(String(port.femType || '')) ? 'mw' : 'lf';
    var opener = {con: 'con' + String(port.con).replace(/^con/, ''), slot: Number(port.slot), port: Number(port.port), kind: kind0};
    var ownPin = pinOf(opener);
    var chipPort = function (pin) { try { return source.chipPort ? source.chipPort(pin) : null; } catch (e) { return null; } };
    var node = document.createElement('dialog');
    node.id = 'port-play-dialog'; node.setAttribute('aria-labelledby', 'port-play-title');
    var lf = kind0 === 'lf';
    node.innerHTML = '<article class="ui-dialog ui-dialog--m">' +
      '<div class="ui-dialog-head"><header class="ui-head"><h3 class="ui-head-title" id="port-play-title"></h3>' +
      '<div class="ui-actions"><button type="button" class="btn-sm outline" data-prev title="Previous output (P)">Previous output</button>' +
      '<button type="button" class="btn-sm outline" data-next title="Next output (N)">Next output</button></div>' +
      '<p class="ui-head-gist" data-chip-note hidden>Read-only test: the open chip is not changed; filters and crosstalk are not applied.</p>' +
      '</header></div>' +
      '<div class="ui-dialog-body">' +
      '<section class="ui-item port-test" data-together><div class="ui-item-head"><h4 class="ui-item-title">Outputs in this play</h4></div>' +
      '<p class="ui-note">Closing this popup stops every output; the set itself is kept. ' +
      'All outputs play in one QM and one program, starting together; Stop stops every output.</p>' +
      '<p class="ui-note">Refused if another program holds any of these ports.</p>' +
      '<table class="ui-table" data-set-table><thead><tr>' +
      '<th class="ui-table-head">Port</th><th class="ui-table-head">Kind</th><th class="ui-table-head">Parameters</th><th class="ui-table-head"></th>' +
      '</tr></thead><tbody data-rows></tbody></table>' +
      '<p class="ui-note" data-delay-note hidden></p>' +
      '<div class="ui-actions ui-actions--start">' +
      '<button type="button" class="btn-sm outline" data-add-port>+ Add port</button>' +
      '<button type="button" class="btn-sm outline" data-add-fem>Add the other outputs of this FEM</button>' +
      '<button type="button" class="btn-sm outline" data-add-every></button>' +
      '<button type="button" class="btn-sm outline" data-apply-kind="lf">Apply to all LF rows</button>' +
      '<button type="button" class="btn-sm outline" data-apply-kind="mw">Apply to all MW rows</button></div>' +
      '<div class="ui-field-grid">' +
      '<label>Auto-stop after (s), whole run<input data-seconds type="number" min="0.1" max="3600" step="0.1" value="300" required></label>' +
      '<label>Instrument max input (dBm), optional<input data-max-in type="number" step="0.1"></label>' +
      '<label>Waveform<select data-waveform><option value="">' + (lf ? 'DC at the offset' : 'CW tone') + ' (default)</option>' +
      '<option value="edge" title="1000 ns on, every 10000 ns">Edge (1 us every 10 us)</option></select></label>' +
      '<label>Simulation length (ns), optional<input data-sim-ns type="number" min="1000" max="200000" step="1"></label></div>' +
      '<div class="ui-callout ui-callout--danger" data-power-warn hidden><p class="ui-callout-text"></p></div></section>' +
      '<section class="ui-item"><div class="ui-item-head"><h4 class="ui-item-title">Safety</h4></div>' +
      '<label class="ui-check"><input data-hwok type="checkbox"> Safe to drive: a ' + (lf ? 'scope' : 'spectrum analyser or scope') +
      ' is on every port of this play</label>' +
      '<p class="ui-note">Play: ' + (lf ? 'LF ports go to their offsets' : 'CW tones at LO + IF') +
      '. Stop: outputs off. Simulate: the same program on a simulator (no hardware, no safety box).</p>' +
      '<p class="ui-note" data-sim-reason hidden></p>' +
      '<p class="ui-note" role="status" data-message></p><div data-error hidden></div>' +
      (source.setCheck ? '<div class="ui-actions ui-actions--start" data-check>' +
        '<button type="button" class="btn-sm outline" data-verdict="ok">\u2713 Works</button>' +
        '<button type="button" class="btn-sm outline" data-verdict="bad">\u2717 Fails</button>' +
        '<label>Note<input data-check-note type="text"></label></div>' : '') + '</section>' +
      (lf ? '<details class="ui-disclosure" data-hold-qm><summary class="ui-disclosure-summary">Hold the QM open (advanced)</summary>' +
        '<div class="ui-disclosure-body"><p class="ui-note">Open QM: the ports go to their offsets with no program running. Close QM: back to 0 V.</p>' +
        '<div class="ui-actions ui-actions--start" data-qm-actions><button type="button" class="btn-sm outline" data-open-qm>Open QM</button>' +
        '<button type="button" class="btn-sm outline" data-close-qm>Close QM</button></div></div></details>' +
        '<section class="ui-item" data-save-wrap><div class="ui-actions ui-actions--start"><button type="button" class="btn-sm outline" data-save>Save offset</button></div>' +
        '<p class="ui-note">Saved to this wizard draft only; the open chip and the instrument do not change until Build.</p></section>' : '') +
      '<section class="ui-item port-test" data-sim-result hidden></section>' +
      '<details class="ui-disclosure" data-sim-prev hidden><summary class="ui-disclosure-summary">Previous simulation</summary>' +
      '<div class="ui-disclosure-body"></div></details>' +
      (lf ? '<details class="ui-disclosure" data-loopcal><summary class="ui-disclosure-summary">Loopback calibration</summary><div class="ui-disclosure-body"><div class="ui-field-grid">' +
        '<label>Analog input (same FEM)<select data-ai><option value="1">AI 1</option><option value="2">AI 2</option></select></label></div>' +
        '<ol class="ui-note" data-cal-steps></ol>' +
        '<p class="ui-note" data-ai-saved></p>' +
        '<div class="ui-actions ui-actions--start"><button type="button" class="btn-sm outline" data-cal-input>Calibrate input</button>' +
        '<button type="button" class="btn-sm outline" data-cal-output>Calibrate output</button>' +
        '<button type="button" class="btn-sm" data-cal-save disabled title="Run a calibration that converges first.">Save calibrated offset</button></div>' +
        '<p class="ui-note" role="status" data-cal-result></p>' +
        '<ol class="ui-note" data-cal-progress></ol>' +
        '<p class="ui-note">Export: a Python script that runs the same calibration without SM.</p>' +
        '<div class="ui-actions ui-actions--start"><button type="button" class="btn-sm outline" data-export>Export script</button></div></div></details>' : '') +
      '</div>' +
      '<div class="ui-dialog-foot" data-foot><div class="ui-actions ui-actions--start" data-run-actions>' +
      '<button type="button" class="btn-sm" data-play>Play</button>' +
      '<button type="button" class="btn-sm outline" data-stop>Stop</button>' +
      '<span class="ui-note" role="status" data-popup-live hidden>LIVE: <span data-popup-live-members></span> ' +
      '<span data-popup-live-left></span></span></div>' +
      '<div class="ui-actions"><button type="button" class="btn-sm outline" data-script title="The Python program this play would run">Download script</button>' +
      '<button type="button" class="btn-sm outline" data-sim>Simulate (no hardware)</button>' +
      '<button type="button" class="btn-sm outline" data-close>Close</button></div></div></article>';
    document.body.appendChild(node); dialog = node;
    var $ = function (s) { return node.querySelector(s); };
    var rows = $('[data-rows]'), add = $('[data-add-port]');
    var refusal = false;
    function message(v) { refusal = false; $('[data-message]').textContent = v; }
    function refuse(v) { $('[data-message]').textContent = v; refusal = true; }
    // D-7: a refusal (client or server) is cleared by the next edit; live status lines are not.
    node.addEventListener('input', function (e) {
      if (refusal && e.target && !e.target.matches('[data-hwok]')) { $('[data-message]').textContent = ''; refusal = false; }
    });

    // ---- Rows ----
    function rowList() { return Array.prototype.slice.call(rows.querySelectorAll('[data-row]')); }
    function rowPort(row) {
      var opt = row.querySelector('[data-pin]').selectedOptions[0], parts = opt.value.split('/');
      return {con: parts[0], slot: Number(parts[1]), port: Number(parts[2]), kind: opt.getAttribute('data-kind')};
    }
    function rowValues(row) {
      var q = function (s) { var e = row.querySelector(s); return e ? e.value : undefined; };
      return rowPort(row).kind === 'mw'
        ? {band: q('[data-band]'), rf: q('[data-rf]'), lo: q('[data-lo]'), if: q('[data-if]'), fsp: q('[data-fsp]'), amp: q('[data-amp]')}
        : {mode: q('[data-mode]'), offset: q('[data-offset]')};
    }
    function setValues(row, v) {
      Object.keys(v || {}).forEach(function (k) {
        var sel = {band: '[data-band]', rf: '[data-rf]', lo: '[data-lo]', if: '[data-if]', fsp: '[data-fsp]',
          amp: '[data-amp]', mode: '[data-mode]', offset: '[data-offset]'}[k];
        var e = sel && row.querySelector(sel);
        if (e && v[k] !== undefined && v[k] !== null) e.value = v[k];
      });
    }
    function defaultsFor(o) {
      var chip = chipPort(pinOf(o));
      if (o.kind === 'mw') {
        var v = {band: 1, lo: 5, if: 50, fsp: -11, amp: 0.1};
        if (chip) {
          if (chip.band) v.band = chip.band;
          if (Number.isFinite(Number(chip.lo_hz))) v.lo = Number(chip.lo_hz) / 1e9;
          if (Number.isFinite(Number(chip.fsp))) v.fsp = chip.fsp;
        }
        v.rf = +(Number(v.lo) + Number(v.if) / 1000).toFixed(9);
        return {values: v, chip: !!chip};
      }
      var saved = (source.spec().port_offsets || {})[pinOf(o)];
      var lv = saved && saved.output_mode ? {mode: saved.output_mode, offset: Number(saved.offset).toFixed(6)}
        : {mode: 'direct', offset: (0).toFixed(6)};
      if (chip && !saved) {
        if (chip.output_mode) lv.mode = chip.output_mode;
        if (Number.isFinite(Number(chip.offset))) lv.offset = Number(chip.offset).toFixed(6);
      }
      return {values: lv, chip: !!chip && !saved};
    }
    var DELAY_FIELD = '<label data-delay-wrap hidden>LF delay (ns)<input data-lf-delay type="number" step="1" min="0" max="16000"></label>';
    function fields(row, kind, values, chip) {
      var box = row.querySelector('[data-line]');
      box.innerHTML = kind === 'mw'
        ? '<label>RF (GHz)<input data-rf type="number" step="0.000001" required></label>' +
          '<label>Band<select data-band>' + bandOptions() + '</select></label>' +
          '<label>Full-scale power (dBm)<input data-fsp type="number" step="1" min="-11" max="18" required></label>' +
          '<label>Amplitude (|a| &lt; 1)<input data-amp type="number" step="0.001" min="-0.999" max="0.999" required></label>' +
          '<details class="ui-disclosure" data-advanced><summary class="ui-disclosure-summary">Advanced</summary><div class="ui-disclosure-body pt-row">' +
          '<label>LO (GHz)<input data-lo type="number" step="0.000001" required></label>' +
          '<label>IF (MHz)<input data-if type="number" step="0.001" min="-500" max="500" required></label></div></details>' +
          '<span class="ui-note" role="status" data-power></span><span class="ui-note" data-lo-reason></span>'
        : '<label>Output mode<select data-mode><option value="direct">direct (|offset| &lt;= 0.5 V)</option>' +
          '<option value="amplified">amplified (|offset| &lt;= 2.5 V)</option></select></label>' +
          '<label>Offset (V)<input data-offset type="number" step="0.000001" required></label>' + DELAY_FIELD;
      row.setAttribute('data-kind', kind);
      row.querySelector('[data-kind-cell]').textContent = kind.toUpperCase();
      setValues(row, values);
      var badge = row.querySelector('[data-chip-badge]');
      badge.hidden = !chip;
      if (chip) $('[data-chip-note]').hidden = false;
      if (kind === 'mw') wireMw(row);
    }
    // D-11: RF is primary (RF = LO + IF); LO / IF stay editable under Advanced.
    function wireMw(row) {
      var q = function (s) { return row.querySelector(s); };
      function num(s) { var e = q(s); return e.value === '' ? NaN : Number(e.value); }
      function validate() {
        var band = Number(q('[data-band]').value), lo = num('[data-lo]'), rf = num('[data-rf]');
        var range = MW_LO_GHZ[band], reason = '';
        if (Number.isFinite(lo) && range && (lo < range[0] || lo > range[1])) {
          var b = bandOfRf(Number.isFinite(rf) ? rf : lo, band);
          reason = 'Band ' + band + ' (' + BAND_LABEL[band].replace(/^\d+ \(|\)$/g, '') + '): LO must be ' + range[0] + '..' +
            range[1] + ' GHz.' + (b && b !== band ? ' RF ' + (Number.isFinite(rf) ? rf : lo) + ' GHz is in band ' + b + '.' : '');
        }
        if (reason) q('[data-lo]').setAttribute('aria-invalid', 'true'); else q('[data-lo]').removeAttribute('aria-invalid');
        q('[data-lo-reason]').textContent = reason;
        if (reason) q('[data-advanced]').open = true;
        var fsp = num('[data-fsp]'), amp = num('[data-amp]');
        var p = Number.isFinite(fsp) && Number.isFinite(amp) ? mwPower(fsp, amp) : null;
        q('[data-power]').textContent = 'RF ' + (Number.isFinite(rf) ? rf.toFixed(6) + ' GHz' : '?') + '; expected output ' +
          (p === null ? '-inf' : p.toFixed(2)) + ' dBm (P = FSP + 20*log10|amp|, into 50 ohm).';
        row._dbm = p;
        powerWarn();
      }
      q('[data-rf]').addEventListener('input', function () {
        var rf = num('[data-rf]'), ifm = num('[data-if]');
        if (Number.isFinite(rf) && Number.isFinite(ifm)) q('[data-lo]').value = +(rf - ifm / 1000).toFixed(9);
        validate();
      });
      q('[data-if]').addEventListener('input', function () {
        var rf = num('[data-rf]'), ifm = num('[data-if]');
        if (Number.isFinite(rf) && Number.isFinite(ifm)) q('[data-lo]').value = +(rf - ifm / 1000).toFixed(9);
        validate();
      });
      q('[data-lo]').addEventListener('input', function () {
        var lo = num('[data-lo]'), ifm = num('[data-if]');
        if (Number.isFinite(lo) && Number.isFinite(ifm)) q('[data-rf]').value = +(lo + ifm / 1000).toFixed(9);
        validate();
      });
      ['[data-band]', '[data-fsp]', '[data-amp]'].forEach(function (s) {
        q(s).addEventListener('input', validate); q(s).addEventListener('change', validate);
      });
      validate();
    }
    function powerWarn() {
      var box = $('[data-power-warn]'), raw = $('[data-max-in]').value, max = Number(raw);
      var over = raw === '' || !Number.isFinite(max) ? [] : rowList().filter(function (r) {
        return r.getAttribute('data-kind') === 'mw' && r._dbm !== null && r._dbm !== undefined && r._dbm > max;
      });
      box.hidden = !over.length;
      box.querySelector('.ui-callout-text').textContent = over.length ? 'Expected output above the instrument max input (' +
        max + ' dBm): ' + over.map(function (r) { return portLabel(rowPort(r)) + ' ' + r._dbm.toFixed(2) + ' dBm'; }).join('; ') + '.' : '';
    }
    function fillSelect(sel, all) {
      sel.replaceChildren();
      var groups = {};
      all.forEach(function (o) {
        var key = o.con + '/' + o.slot;
        if (!groups[key]) {
          groups[key] = document.createElement('optgroup');
          groups[key].label = o.con + ' \u00b7 FEM ' + o.slot + ' (' + o.kind.toUpperCase() + ')';
          sel.appendChild(groups[key]);
        }
        var opt = document.createElement('option');
        opt.value = pinOf(o); opt.textContent = femWord(o.kind) + ' ' + o.port + ' (' + o.kind.toUpperCase() + ')';
        opt.setAttribute('data-kind', o.kind);
        groups[key].appendChild(opt);
      });
    }
    function addRow(o, values, chip) {
      var all = outputsOf(source.spec());
      if (!all.some(function (x) { return pinOf(x) === pinOf(o); })) all.push(o);
      var row = document.createElement('tr');
      row.setAttribute('data-row', '');
      row.innerHTML = '<td class="ui-table-cell"><label>Port<select class="pt-pin" data-pin></select></label>' +
        '<span class="ui-badge ui-badge--accent" data-chip-badge hidden>chip values</span></td>' +
        '<td class="ui-table-cell" data-kind-cell></td><td class="ui-table-cell"><div class="pt-row" data-line></div></td>' +
        '<td class="ui-table-cell"><button type="button" class="btn-sm outline" data-remove aria-label="Remove this port">Remove</button></td>';
      var sel = row.querySelector('[data-pin]');
      fillSelect(sel, all);
      sel.value = pinOf(o);
      if (values === undefined) { var d = defaultsFor(o); values = d.values; chip = d.chip; }
      fields(row, o.kind, values, chip);
      sel.onchange = function () {
        var p = rowPort(row), last = lastOfKind(p.kind, row);
        var d = defaultsFor(p);
        fields(row, p.kind, last ? rowValues(last) : d.values, last ? false : d.chip);
        changed();
      };
      row.querySelector('[data-remove]').onclick = function () { row.remove(); changed(); };
      rows.appendChild(row);
      return row;
    }
    function lastOfKind(kind, except) {
      var list = rowList().filter(function (r) { return r !== except && r.getAttribute('data-kind') === kind; });
      return list.length ? list[list.length - 1] : null;
    }
    function chosen() { return rowList().map(function (r) { return r.querySelector('[data-pin]').value; }); }
    function freeOutputs() {
      var used = chosen();
      return outputsOf(source.spec()).filter(function (o) { return used.indexOf(pinOf(o)) < 0; });
    }
    // D-4: next free output on the same FEM (same kind) as the last row, values copied from the last row of that kind.
    function nextDefault() {
      var free = freeOutputs(), list = rowList(), last = list.length ? rowPort(list[list.length - 1]) : opener;
      if (!free.length) return null;
      var same = free.filter(function (o) { return o.con === last.con && o.slot === last.slot; });
      var after = same.filter(function (o) { return o.port > last.port; });
      var kindFree = free.filter(function (o) { return o.kind === last.kind; });
      return after[0] || same[0] || kindFree[0] || free[0];
    }
    function addFrom(o) {
      var src = lastOfKind(o.kind);
      return src ? addRow(o, rowValues(src), false) : addRow(o);
    }
    add.onclick = function () {
      var o = nextDefault();
      if (!o) { add.title = 'No other output is free.'; return; }
      addFrom(o); changed();
    };
    $('[data-add-fem]').onclick = function () {
      var list = rowList(), last = list.length ? rowPort(list[list.length - 1]) : opener;
      freeOutputs().filter(function (o) { return o.con === last.con && o.slot === last.slot; }).forEach(addFrom);
      changed();
    };
    $('[data-add-every]').onclick = function () {
      var list = rowList(), last = list.length ? rowPort(list[list.length - 1]) : opener;
      freeOutputs().filter(function (o) { return o.kind === last.kind && o.port === last.port; }).forEach(addFrom);
      changed();
    };
    node.querySelectorAll('[data-apply-kind]').forEach(function (btn) {
      var kind = btn.getAttribute('data-apply-kind');
      btn.title = 'Copies the first ' + kind.toUpperCase() + " row's values to every " + kind.toUpperCase() + ' row.';
      btn.onclick = function () {
        var same = rowList().filter(function (r) { return r.getAttribute('data-kind') === kind; });
        if (!same.length) return;
        var v = rowValues(same[0]);
        same.slice(1).forEach(function (r) { fields(r, kind, v, false); });
        changed();
      };
    });
    // ---- LF delay in an LF + MW play (multi D-10/D-11) ----
    function delayDefault() {
      var max = null;
      node.querySelectorAll('[data-band]').forEach(function (b) {
        var d = BAND_DELAY_NS[Number(b.value)];
        if (max === null || d > max) max = d;
      });
      var lfs = node.querySelectorAll('[data-lf-delay]').length;
      return max !== null && lfs ? max : null;
    }
    function refreshDelays() {
      var def = delayDefault(), note = $('[data-delay-note]');
      note.hidden = def === null;
      note.textContent = def === null ? '' : 'MW-FEM outputs start later than LF-FEM outputs. LF delay default: ' + def +
        ' ns, from the slowest MW band in this play; MW ports of a faster band are delayed by the difference.';
      node.querySelectorAll('[data-lf-delay]').forEach(function (input) {
        input.closest('[data-delay-wrap]').hidden = def === null;
        if (def !== null && !input.hasAttribute('data-touched')) input.value = def;
      });
    }
    node.addEventListener('input', function (ev) {
      if (ev.target.matches && ev.target.matches('[data-lf-delay]')) ev.target.setAttribute('data-touched', '');
    });
    function readDelay(input, o) {
      var v = input.value;
      if (v === '' || !Number.isInteger(Number(v)) || Number(v) < 0 || Number(v) > MAX_DELAY_NS)
        throw new Error(portLabel(o) + ': LF delay must be a whole number 0..' + MAX_DELAY_NS + ' ns.');
      return Number(v);
    }
    // ---- Title, limits, persistence ----
    function sync() {
      var n = rowList().length, free = freeOutputs();
      // D-1: every allocated output may join; the button disables only when none is free.
      add.disabled = !free.length;
      add.title = add.disabled ? 'No other output is free.' : '';
      var list = rowList(), last = list.length ? rowPort(list[list.length - 1]) : opener;
      $('[data-add-every]').textContent = 'Add ' + femWord(last.kind) + ' ' + last.port + ' on every FEM of this kind';
      $('#port-play-title').textContent = n > 1 ? 'Play set (' + n + ' outputs)' : n === 1 ? portLabel(rowPort(list[0])) : 'Play set (no outputs)';
      node.querySelectorAll('[data-apply-kind]').forEach(function (b) {
        b.hidden = rowList().filter(function (r) { return r.getAttribute('data-kind') === b.getAttribute('data-apply-kind'); }).length < 2;
      });
      var save = $('[data-save]');
      if (save) save.disabled = !openerRow();
    }
    function openerRow() {
      return rowList().filter(function (r) { return r.querySelector('[data-pin]').value === ownPin; })[0] || null;
    }
    function persist() {
      // D-5: rows and parameters only; the safety tick is never stored.
      writeStore('sessionStorage', SET_KEY, JSON.stringify({
        rows: rowList().map(function (r) { return {pin: r.querySelector('[data-pin]').value, kind: r.getAttribute('data-kind'), values: rowValues(r)}; }),
        seconds: $('[data-seconds]').value}));
    }
    function changed() { refreshDelays(); sync(); powerWarn(); persist(); }
    node.addEventListener('input', function (e) { if (e.target && !e.target.matches('[data-hwok]')) persist(); });
    node.addEventListener('change', function (e) {
      if (e.target && e.target.matches('[data-band]')) refreshDelays();
      if (e.target && !e.target.matches('[data-hwok]')) persist();
    });
    $('[data-max-in]').value = readStore('localStorage', MAX_IN_KEY) || '';
    $('[data-max-in]').addEventListener('input', function () { writeStore('localStorage', MAX_IN_KEY, $('[data-max-in]').value); powerWarn(); });
    // Restore a remembered set when this output is one of its members.
    var stored = null;
    try { stored = JSON.parse(readStore('sessionStorage', SET_KEY) || 'null'); } catch (e) { stored = null; }
    var kindOf = {}, dropped = [];
    outputsOf(source.spec()).forEach(function (o) { kindOf[pinOf(o)] = o.kind; });
    if (stored && Array.isArray(stored.rows) && stored.rows.some(function (r) { return r && r.pin === ownPin; })) {
      stored.rows.forEach(function (r) {
        if (!r || typeof r.pin !== 'string') return;
        var parts = r.pin.split('/');
        var now = r.pin === ownPin ? opener.kind : kindOf[r.pin];
        if (!now) return;
        // A row saved for another kind of FEM (re-allocated since) is not replayed with its old fields.
        if (r.kind !== now) { dropped.push(portLabel({con: parts[0], slot: Number(parts[1]), port: Number(parts[2]), kind: now})); return; }
        addRow({con: parts[0], slot: Number(parts[1]), port: Number(parts[2]), kind: now}, r.values, false);
      });
      if (!rowList().length) addRow(opener);
      if (stored.seconds) $('[data-seconds]').value = stored.seconds;
    } else {
      addRow(opener);
    }
    changed();
    if (dropped.length) message('Not restored: ' + dropped.join(', ') + ' (the FEM kind changed since the set was saved).');

    // ---- Request: the first row is the main entry, the rest are `extra` (D-2) ----
    function entry(row, withDelay) {
      var o = rowPort(row), q = function (s) { return row.querySelector(s); };
      var e = {kind: o.kind, con: o.con, slot: o.slot, port: o.port};
      if (o.kind === 'mw') {
        var vals = ['[data-lo]', '[data-if]', '[data-fsp]', '[data-amp]'].map(function (s) { return q(s).value; });
        if (vals.some(function (v) { return v === '' || !Number.isFinite(Number(v)); }))
          throw new Error(portLabel(o) + ': enter RF, full-scale power and amplitude.');
        var band = Number(q('[data-band]').value), lo = Number(vals[0]), ifm = Number(vals[1]);
        var fsp = Number(vals[2]), amp = Number(vals[3]), range = MW_LO_GHZ[band];
        if (!range) throw new Error(portLabel(o) + ': band must be 1, 2 or 3.');
        if (lo < range[0] || lo > range[1]) throw new Error(portLabel(o) + ': Band ' + band + ' upconverter frequency must be ' + range[0] + '..' + range[1] + ' GHz.');
        if (Math.abs(ifm) > 500) throw new Error(portLabel(o) + ': |IF| must be <= 500 MHz.');
        if (!Number.isInteger(fsp) || fsp < -11 || fsp > 18) throw new Error(portLabel(o) + ': Full-scale power must be an integer -11..18 dBm.');
        if (!(Math.abs(amp) < 1)) throw new Error(portLabel(o) + ': Amplitude must satisfy |amp| < 1 (fraction of full scale).');
        Object.assign(e, {band: band, lo_hz: lo * 1e9, if_hz: ifm * 1e6, full_scale_power_dbm: fsp, amp: amp});
      } else {
        var v = q('[data-offset]').value, mode = q('[data-mode]').value, limit = mode === 'amplified' ? 2.5 : 0.5;
        if (v === '' || !Number.isFinite(Number(v))) throw new Error(portLabel(o) + ': enter an offset.');
        if (Math.abs(Number(v)) > limit) throw new Error(portLabel(o) + ': ' + mode + ' mode requires |offset| <= ' + limit + ' V.');
        Object.assign(e, {output_mode: mode, offset: Number(v)});
        if (withDelay) e.delay_ns = readDelay(q('[data-lf-delay]'), o);
      }
      return e;
    }
    function common() {
      var seconds = $('[data-seconds]');
      if (!seconds.checkValidity() || seconds.value === '') throw new Error('Enter an auto-stop time in (0, 3600] seconds.');
      var out = {hardware_ok: !!$('[data-hwok]').checked, max_s: Number(seconds.value), spec: source.spec(), python: source.python()};
      // D-18 (client half): only a non-default choice is sent, so a dc/cw request is unchanged.
      if ($('[data-waveform]').value === 'edge') out.waveform = 'edge';
      var ns = $('[data-sim-ns]');
      if (ns.value !== '') {
        var v = Number(ns.value);
        if (!Number.isInteger(v) || v < 1000 || v > 200000) throw new Error('Simulation length must be a whole number of ns in 1000..200000.');
        out.sim_ns = v;
      }
      return out;
    }
    function params() {
      var list = rowList();
      if (!list.length) throw new Error('Add at least one output.');
      var seen = [];
      list.forEach(function (r) {
        var pin = r.querySelector('[data-pin]').value;
        if (seen.indexOf(pin) >= 0) throw new Error(portLabel(rowPort(r)) + ' is chosen twice.');
        seen.push(pin);
      });
      var withDelay = delayDefault() !== null;
      var entries = list.map(function (r) { return entry(r, withDelay); });
      var main = entries[0];
      if (main.kind === 'lf') delete main.kind;
      var data = Object.assign(main, common());
      if (entries.length > 1) data.extra = entries.slice(1);
      return data;
    }
    var ui = {message: message, refuse: refuse, editHost: source.editHost, editHostLabel: source.editHostLabel,
      order: function () { return chosen().map(function (p) { return p.replace(/^con/, ''); }); },
      members: function () { return rowList().map(function (r) { return {pin: r.querySelector('[data-pin]').value, label: portLabel(rowPort(r))}; }); }};
    var lock = {busy: false};
    wirePlay(node, params, ui, lock);
    // D-21 (client half): the program this play would run, from the same request as Play.
    $('[data-script]').onclick = function () {
      var data;
      try { data = params(); } catch (e) { refuse(e.message); return; }
      data.action = 'play';
      fetch('/port-play/script.py', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)})
        .then(function (r) {
          if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || 'Could not build the script.'); });
          var name = (/filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') || '') || [])[1] || 'port_play.py';
          return r.blob().then(function (b) {
            var a = document.createElement('a');
            a.href = URL.createObjectURL(b); a.download = name; document.body.appendChild(a); a.click();
            setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
            message('Script downloaded: ' + name);
          });
        }).catch(function (e) { refuse(e.message); });
    };
    // D-19 (client half): Simulate is disabled with the server's reason when nothing can simulate.
    fetch('/port-play/sim-check', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({spec: source.spec()})})
      .then(function (r) { return r.json(); }).then(function (j) {
        if (dialog !== node || !j || !j.reason) return;
        var b = $('[data-sim]'), note = $('[data-sim-reason]');
        b.disabled = true; b.title = j.reason; note.textContent = j.reason; note.hidden = false;
      }).catch(function () { /* the run itself still refuses with the same sentence */ });

    // ---- D-26 (S1 half): previous / next output on the same FEM order ----
    function step(d) {
      var all = outputsOf(source.spec()), at = all.map(pinOf).indexOf(ownPin);
      if (at < 0 || !all.length) return;
      var o = all[(at + d + all.length) % all.length];
      // Moving to another output closes this popup, and closing always stops: say so.
      var wasLive = !!(runId && liveSet);
      open({con: o.con, slot: o.slot, port: o.port, femType: o.kind}, source);
      if (wasLive && dialog) dialog.querySelector('[data-message]').textContent =
        'The previous play was stopped: moving to another output stops every output.';
    }
    $('[data-prev]').onclick = function () { step(-1); };
    $('[data-next]').onclick = function () { step(1); };
    node.addEventListener('keydown', function (e) {
      if (e.ctrlKey || e.metaKey || e.altKey || !e.target || e.target.matches('input, select, textarea')) return;
      if (e.key === 'n' || e.key === 'N') { e.preventDefault(); step(1); }
      else if (e.key === 'p' || e.key === 'P') { e.preventDefault(); step(-1); }
    });

    // D-26 (S1 half): a verdict for the opener port, stored by the source (degrades when absent).
    // D-26 readback: an existing verdict for this port is shown, not only the rack colour.
    var had = null;
    try { had = source.check ? source.check(ownPin) : null; } catch (e) { had = null; }
    if (had && $('[data-check]')) {
      var said = document.createElement('p');
      said.className = 'ui-note'; said.setAttribute('data-check-current', '');
      said.textContent = 'Saved verdict: ' + (had.verdict === 'ok' ? '✓ Works' : had.verdict === 'bad' ? '✗ Fails' : String(had.verdict)) +
        (had.note ? ' — ' + had.note : '') + '.';
      $('[data-check]').parentNode.insertBefore(said, $('[data-check]'));
      if (had.note) $('[data-check-note]').value = had.note;
    }
    node.querySelectorAll('[data-verdict]').forEach(function (btn) {
      btn.onclick = function () {
        try { source.setCheck(ownPin, {verdict: btn.getAttribute('data-verdict'), note: $('[data-check-note]').value}); message('Verdict saved for ' + portLabel(opener) + '.'); }
        catch (e) { refuse(e.message); }
      };
    });
    if (lf) wireLf(node, opener, source, openerRow, entry, common, refuse, message, lock);
    $('[data-close]').onclick = close;
    // The rack LIVE bar sits behind this modal; the popup shows the same live line next to its Stop.
    var liveTick = null;
    function paintLive() {
      var bar = node.querySelector('[data-popup-live]');
      if (!bar || dialog !== node) { clearInterval(liveTick); liveTick = null; document.removeEventListener('port-play:change', paintLive); return; }
      var l = window.PortPlay.live();
      bar.hidden = !l;
      if (!l) { clearInterval(liveTick); liveTick = null; return; }
      bar.querySelector('[data-popup-live-members]').textContent = (l.members || []).map(function (m) { return m.label || m.pin || m; }).join(', ');
      var end = Number(l.started_at) + Number(l.max_s), left = bar.querySelector('[data-popup-live-left]');
      if (l.started_at === null || !isFinite(end)) left.textContent = '';
      else { var t = Math.max(0, Math.round(end - Date.now() / 1000)); left.textContent = '· stops in ' + Math.floor(t / 60) + ':' + String(t % 60).padStart(2, '0'); }
      if (!liveTick) liveTick = setInterval(paintLive, 1000);
    }
    document.addEventListener('port-play:change', paintLive);
    node.addEventListener('cancel', function (e) { e.preventDefault(); close(); });
    node.showModal();
    var first = openerRow() || rowList()[0];
    var focus = first && (first.querySelector('[data-offset]') || first.querySelector('[data-rf]'));
    if (focus) focus.focus();
  }

  // ---- LF opener extras: Save offset and loopback calibration, bound to the opener port ----
  function wireLf(node, opener, source, openerRow, entry, common, refuse, message, lock) {
    var $ = function (s) { return node.querySelector(s); };
    var con = String(opener.con).replace(/^con/, ''), pin = pinOf(opener);
    function ownParams() {
      var row = openerRow();
      if (!row) throw new Error(portLabel(opener) + ' is no longer in this play; add it back first.');
      var e = entry(row, false);
      delete e.kind;
      return Object.assign(e, common());
    }
    $('[data-save]').onclick = function () {
      try {
        var data = ownParams(), spec = source.spec();
        // A popup can outlive a chassis edit. Never save onto a reassigned FEM.
        var ctrl = spec.instruments.controllers.find(function (c) { return String(c.con) === con; });
        if (!ctrl || !ctrl.fems.some(function (f) { return String(f.slot) === String(opener.slot) && f.fem === 'lf'; }))
          throw new Error('The LF-FEM allocation changed. Reopen the port.');
        (spec.port_offsets || (spec.port_offsets = {}))[pin] = {offset: data.offset, output_mode: data.output_mode};
        source.changed(); message('Saved in wizard draft. Build applies this offset.');
      } catch (e) { refuse(e.message); }
    };
    // ---- Loopback calibration (BRIEF3 E-1..E-4) ----
    var femKey = 'con' + con + '/' + opener.slot, calId = null, calSeq = 0;
    var aiChoice = (source.spec().port_play_ai || {})[femKey];
    if (aiChoice) $('[data-ai]').value = String(aiChoice);
    function aiKey() { return femKey + '/ai' + $('[data-ai]').value; }
    function showAiSaved() {
      var e = (source.spec().port_offsets || {})[aiKey()];
      $('[data-ai-saved]').textContent = e
        ? 'Saved AI offset: ' + Number(e.offset).toFixed(6) + ' V (gain ' + (e.gain_db || 0) + ' dB).'
        : 'No saved AI offset for this AI yet: calibrate the AI first.';
      $('[data-cal-output]').disabled = !e;
      $('[data-cal-output]').title = e ? '' : 'Calibrate the input first.';
      var n = $('[data-ai]').value, steps = [
        'Leave AI ' + n + ' unplugged, then Calibrate input (once per AI, gain 0 dB).', 'Save.',
        'Connect this AO to AI ' + n + ' with one cable, then Calibrate output.', 'Save, then check on the scope.'];
      var list = $('[data-cal-steps]'); list.replaceChildren();
      steps.forEach(function (t) { var li = document.createElement('li'); li.textContent = t; list.appendChild(li); });
    }
    function calMessage(v) { $('[data-cal-result]').textContent = v; }
    $('[data-ai]').onchange = function () {
      var spec = source.spec();
      (spec.port_play_ai || (spec.port_play_ai = {}))[femKey] = Number($('[data-ai]').value);
      source.changed(); showAiSaved();
    };
    function calBody(kind) {
      var data = ownParams();
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
          liveHw = true;
          var lines = st.progress || [];
          calMessage(st.stopping ? 'Stopping...' : 'Calibrating... ' +
            (lines.length ? lines[lines.length - 1] : (st.phase || 'starting')));
          timer = setTimeout(function () { calPoll(seq); }, 1000);
          return;
        }
        liveHw = false;
        var res = st.result || {};
        if (!res.ok) { calMessage(res.stopped ? 'Stopped.' : (res.error || 'Failed.')); return; }
        calMessage((res.converged ? 'Converged' : 'Not converged') + ': offset ' + Number(res.final_offset).toFixed(6) +
          ' V after ' + res.iters + ' iteration(s).' + (res.converged ? '' : ' Nothing to save; check the cable.'));
        $('[data-cal-save]').disabled = res.converged !== true;
        $('[data-cal-save]').title = res.converged === true ? '' : 'Run a calibration that converges first.';
      }).catch(function (e) { calMessage(e.message); });
    }
    function calibrate(kind) {
      if (lock.busy) return;
      var data;
      try { data = calBody(kind); } catch (e) { calMessage(e.message); return; }
      if (!data.hardware_ok) { calMessage('Tick the safety box first.'); return; }
      lock.busy = true; var seq = ++calSeq; sequence++; clearTimeout(timer);
      $('[data-cal-save]').disabled = true; $('[data-cal-progress]').replaceChildren();
      calMessage('Preparing calibration...');
      function start() {
        if (seq !== calSeq || dialog !== node) { lock.busy = false; return; }
        post('/port-play/loopcal/confirm', data).then(function (c) {
          if (!c.ok) throw new Error(c.error);
          return post('/port-play/loopcal/run', Object.assign({confirm_token: c.token}, data));
        }).then(function (j) {
          if (seq !== calSeq || dialog !== node) { if (j.ok) post('/port-play/stop', {id: j.id}); lock.busy = false; return; }
          if (j.stopping) { calMessage(j.error); timer = setTimeout(start, 500); return; }
          lock.busy = false;
          if (!j.ok) { calMessage(j.error); return; }
          runId = calId = j.id; calPoll(seq);
        }).catch(function (e) { lock.busy = false; calMessage(e.message); });
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
        var row = openerRow();
        if (j.entry.output_mode && row) {
          row.querySelector('[data-offset]').value = Number(j.entry.offset).toFixed(6);
          row.querySelector('[data-mode]').value = j.entry.output_mode;
        }
        $('[data-cal-save]').disabled = true; $('[data-cal-save]').title = 'Saved.';
        source.changed(); showAiSaved();
        calMessage('Saved ' + j.pin + ' = ' + Number(j.entry.offset).toFixed(6) + ' V in the wizard draft.' +
          (j.entry.output_mode ? ' Press Play to check it on the scope.' : ''));
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
  }

  // D-13 (R3): leaving the page stops our run, and says so.
  function stoppedToast() {
    if (liveHw && window.showToast) window.showToast('Port play stopped \u2014 outputs off.', 'info');
    liveHw = false; setLive(null);
  }
  // D-25: the open chip's own values of one output (from /api/instrument/data's instrument), or null.
  function chipPortOf(instrument, pin) {
    var m = /^con([^/]*)\/([^/]+)\/(\d+)$/.exec(String(pin || ''));
    if (!instrument || !m) return null;
    var ctl = instrument.controllers || {};
    var fem = ((ctl['con' + m[1]] || ctl[m[1]] || {}).fems || {})[m[2]];
    if (!fem) return null;
    var a = ((fem.output_ports || fem.ports || {})[m[3]] || [])[0];
    if (!a) return null;
    var v = function (x) { return x == null ? null : x; };
    if (/^mw/.test(String(fem.type || ''))) return {band: v(a.band), lo_hz: v(a.lo_frequency), fsp: v(a.full_scale_power_dbm)};
    return {output_mode: v(a.output_mode), offset: v(a.offset)};
  }

  // ---- Rack from a build spec (the wizard's step 3 and the /port-play draft source share this) ----
  // Every FEM configured in spec.instruments, with empty port lists; 8 rows per controller.
  function rackFromSpec(spec) {
    var controllers = {};
    (((spec || {}).instruments || {}).controllers || []).forEach(function (ctrl) {
      (ctrl.fems || []).forEach(function (fem) {
        var c = controllers[ctrl.con] || (controllers[ctrl.con] = {});
        if (!c[fem.slot]) {
          c[fem.slot] = {type: fem.fem === 'mw' ? 'mw-fem' : 'lf-fem',
                         output_ports: {}, input_ports: {}, digital_ports: {}};
        }
      });
    });
    var result = {};
    Object.keys(controllers).forEach(function (con) { result[con] = {fems: controllers[con], max_output_port: 8}; });
    return {controllers: result};
  }

  // The Generate wizard's unsaved draft, read only (never written or removed here).
  // Same key and version check as the wizard's loadDraft(); anything else is null.
  var DRAFT_KEY = 'quam_generate_draft', DRAFT_VERSION = 2;
  function wizardDraft() {
    var raw = null, d = null;
    try { raw = window.sessionStorage.getItem(DRAFT_KEY); } catch (e) { return null; }
    if (!raw) return null;
    try { d = JSON.parse(raw); } catch (e) { return null; }
    if (!d || d.v !== DRAFT_VERSION || !d.spec || typeof d.spec !== 'object') return null;
    return d;
  }

  window.PortPlay = {open: open, rackFromSpec: rackFromSpec, wizardDraft: wizardDraft, close: close, renderOffsets: renderOffsets, portLabel: portLabel, chipPortOf: chipPortOf,
    live: function () { return liveSet ? JSON.parse(JSON.stringify(liveSet)) : null; }};
  window.addEventListener('pagehide', function () {
    sequence++; clearTimeout(timer);
    if (runId) navigator.sendBeacon('/port-play/stop?left=1&id=' + encodeURIComponent(runId));
    stoppedToast();
  });
  document.addEventListener('htmx:beforeSwap', function (e) {
    if (dialog && e.detail && e.detail.target && e.detail.target.id === 'table-pane') { stoppedToast(); close(); }
  });
})();
