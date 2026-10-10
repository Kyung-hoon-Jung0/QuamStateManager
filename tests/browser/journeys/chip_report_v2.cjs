/* docs/277 -- the shareable chip report, walked in real Chrome.
 *
 * usage: SM_CDP_PORT=<cdp> node tests/browser/journeys/chip_report_v2.cjs <smPort> <outDir> [secrets]
 *   secrets = comma-separated strings that must be absent from a redacted file
 *             and present in an unredacted one (a network host, a folder path)
 * Needs SM on <smPort> with a chip loaded. Exit 1 on any FAIL.
 *
 * J1  open the report, toggle sections with real clicks, the panel says what
 *     the file will hold
 * J2  press Download (a real download), open the saved file from disk with the
 *     network OFF: every checked section, zero requests, zero errors
 * J3  fault (a): leave each section out in turn; a value only that section
 *     shows is absent from the file (and present when it is checked)
 * J4  fault (b): with the switch on the secrets are absent everywhere, the
 *     packed raw tree included; off, they are there
 * J5  fault (d): numbers in the report equal the live pages' numbers
 * J6  fault (e): a section fetch and the rack frame refused by the network
 *     render the honest line, and the file carries it
 * J7  reload and Back: the page comes back with the same choices, intact
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { open, sleep } = require('./cdp.cjs');

const PORT = process.argv[2] || '5145';
const OUT = process.argv[3] || '.';
const SECRETS = (process.argv[4] || '').split(',').filter(Boolean);
const BASE = `http://127.0.0.1:${PORT}`;
const ALL = ['overview', 'chip_status', 'trends', 'pulses', 'zline', 'wiring', 'diagnostics', 'raw'];
const res = [];
function rec(name, ok, detail) {
  res.push({ name, ok, detail });
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail !== undefined ? '  ' + JSON.stringify(detail).slice(0, 500) : ''));
}

async function ready(p, maxMs) {
  const t0 = Date.now();
  for (;;) {
    const o = JSON.parse(await p.ev(`(function(){var lazy=[].slice.call(document.querySelectorAll('section[data-rep-lazy]')).filter(function(s){return !s.hidden}).map(function(s){return s.getAttribute('data-rep-sec')});
      var iw=document.querySelector('[data-rep-iw]'); var map=document.querySelector('#component-map');
      return JSON.stringify({lazy:lazy, iw: iw && iw.getAttribute('data-rep-iw'), map: !map || !!map.querySelector('svg')});})()`));
    if (!o.lazy.length && (!o.iw || o.iw !== 'pending') && o.map) return Date.now() - t0;
    if (Date.now() - t0 > (maxMs || 120000)) return -1;
    await sleep(100);
  }
}
async function clickBox(p, key) {
  const c = JSON.parse(await p.ev(`(function(){var b=document.querySelector('input[data-rep-box][value="${key}"]'); b.scrollIntoView({block:'center'}); var r=b.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()`));
  await p.click(c.x, c.y);
  await sleep(150);
}
async function build(p) {
  const r = await p.ev(`window.ChipReport.buildStandalone().then(function(h){ window.__rep=h; return 'len:'+h.length; }, function(e){ return 'ERR '+e.message; })`);
  if (!/^len:/.test(r)) return { error: r };
  const len = +r.slice(4);
  let html = '';
  for (let i = 0; i < len; i += 2000000) html += await p.ev(`window.__rep.slice(${i}, ${i + 2000000})`);
  return { html };
}
async function rawText(p) {
  // the raw tree's JSON as text, unpacked the way the file unpacks it
  return p.ev(`(function(){var el=document.getElementById('rep-raw-data'); if(!el) return '';
    if (el.getAttribute('data-enc')!=='gzip-base64') return el.textContent;
    var bin=atob(el.textContent), b=new Uint8Array(bin.length); for(var i=0;i<bin.length;i++) b[i]=bin.charCodeAt(i);
    return new Response(new Blob([b]).stream().pipeThrough(new DecompressionStream('gzip'))).text();})()`);
}
async function openOffline(file) {
  const f = await open('about:blank', 1400, 950);
  await f.send('Network.enable');
  await f.send('Network.emulateNetworkConditions', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  const mark = f.events.length;
  await f.send('Page.navigate', { url: 'file:///' + file.replace(/\\/g, '/').replace(/^\/+/, '') });
  for (let i = 0; i < 150; i++) { if (await f.ev('document.readyState') === 'complete') break; await sleep(100); }
  await sleep(1200);
  const reqs = f.events.slice(mark).filter(e => e.method === 'Network.requestWillBeSent')
    .map(e => e.params.request.url).filter(u => !/^(file|data|blob):/.test(u));
  const failed = f.events.slice(mark).filter(e => e.method === 'Network.loadingFailed');
  return { f, reqs, failed, errs: f.errors(mark) };
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const dl = path.join(OUT, 'downloads');
  fs.mkdirSync(dl, { recursive: true });
  // ---- J1: open, toggle with real clicks
  const p = await open(`${BASE}/chip-status/report`, 1400, 950);
  await p.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: dl });
  const t1 = await ready(p);
  rec('J1a the default sections build', t1 >= 0, { ms: t1 });
  await clickBox(p, 'raw');
  await clickBox(p, 'diagnostics');
  const t1b = await ready(p);
  const st = JSON.parse(await p.ev(`JSON.stringify({checked: ChipReport.checked(), url: location.search, will: document.getElementById('rep-will').textContent, cal: document.querySelector('input[value="calibration_log"]').disabled, diagHidden: document.querySelector('section[data-rep-sec="diagnostics"]').hidden})`));
  rec('J1b real clicks change the selection and the URL', st.checked.indexOf('raw') >= 0 && st.checked.indexOf('diagnostics') < 0
      && /raw/.test(decodeURIComponent(st.url)) && st.diagHidden, st);
  rec('J1c the panel says what is in and out of the file', /Not in the file:.*Diagnostics/.test(st.will) && /Raw state tree/.test(st.will), st.will);
  // docs/281: old "the calibration log cannot be checked" -> it reads the chip ledger now: offered, off by default
  rec('J1d the calibration log can be checked, and is off by default', st.cal === false && st.checked.indexOf('calibration_log') < 0, st.checked);
  await p.shot(path.join(OUT, 'j1_panel.png'));

  // ---- J2: a REAL download, opened from disk with the network off
  const before = new Set(fs.readdirSync(dl));
  const btn = JSON.parse(await p.ev(`(function(){var b=document.getElementById('rep-download'); b.scrollIntoView({block:'center'}); var r=b.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()`));
  const tp = Date.now();
  await p.click(btn.x, btn.y);
  let saved = null;
  for (let i = 0; i < 600 && !saved; i++) {
    await sleep(100);
    const now = fs.readdirSync(dl).filter(n => !before.has(n) && /\.html$/.test(n));
    if (now.length) saved = path.join(dl, now[0]);
  }
  const pressMs = Date.now() - tp;
  const status = await p.ev(`document.getElementById('rep-status').textContent`);
  rec('J2a pressing Download saves one .html file', !!saved, { saved, pressMs, status });
  if (saved) {
    await sleep(300);
    const size = fs.statSync(saved).size;
    const o = await openOffline(saved);
    const info = JSON.parse(await o.f.ev(`JSON.stringify({secs:[].slice.call(document.querySelectorAll('section[data-rep-sec]')).map(function(s){return s.getAttribute('data-rep-sec')}), raw:(document.querySelector('[data-rep-raw-tree]')||{getAttribute:function(){return null}}).getAttribute('data-rep-raw-ready'), rack: document.querySelectorAll('[data-rep-iw] svg').length, map: document.querySelectorAll('#component-map svg').length})`));
    rec('J2b the file holds the checked sections', JSON.stringify(info.secs) === JSON.stringify(st.checked), info);
    rec('J2c opened offline: zero network requests, zero failures', o.reqs.length === 0 && o.failed.length === 0, { reqs: o.reqs.slice(0, 5), failed: o.failed.length });
    rec('J2d opened offline: zero console errors', o.errs.length === 0, o.errs);
    rec('J2e the raw tree opens and the pictures are there', info.raw === '1' && info.rack >= 1 && info.map >= 1, info);
    // the pruned stylesheet still styles the file the way the page is styled
    const look = `JSON.stringify({bg: getComputedStyle(document.body).backgroundColor, color: getComputedStyle(document.body).color,
      th: (function(){var t=document.querySelector('section[data-rep-sec] th'); return t ? getComputedStyle(t).backgroundColor + '|' + getComputedStyle(t).borderTopColor : null;})(),
      h2: (function(){var h=document.querySelector('section[data-rep-sec] h2'); return h ? getComputedStyle(h).borderBottomWidth + '|' + getComputedStyle(h).fontSize : null;})(),
      css: [].slice.call(document.querySelectorAll('style')).reduce(function(a,s){return a+s.textContent.length},0)})`;
    const fileLook = JSON.parse(await o.f.ev(look));
    const pageLook = JSON.parse(await p.ev(look));
    rec('J2g the file is styled like the page (body, table header, heading)', fileLook.bg === pageLook.bg && fileLook.color === pageLook.color
        && fileLook.th === pageLook.th && fileLook.h2 === pageLook.h2, { file: fileLook, page: pageLook });
    await o.f.shot(path.join(OUT, 'j2_file_top.png'));
    for (const k of info.secs) {
      await o.f.ev(`document.querySelector('section[data-rep-sec="${k}"]').scrollIntoView({block:'start'})`);
      await sleep(150);
      await o.f.shot(path.join(OUT, 'j2_file_' + k + '.png'));
    }
    rec('J2f size', true, { bytes: size, mb: +(size / 1048576).toFixed(2) });
    await o.f.close();
  }

  // ---- J3: fault (a) -- each section out in turn
  for (const k of ALL) { const on = await p.ev(`ChipReport.checked().indexOf('${k}')>=0`); if (!on) await clickBox(p, k); }
  await ready(p);
  const uniq = JSON.parse(await p.ev(`(function(){
    var secs=[].slice.call(document.querySelectorAll('section[data-rep-sec]')); var out={};
    secs.forEach(function(s){
      var others=secs.filter(function(x){return x!==s}).map(function(x){return x.outerHTML}).join('\\n');
      var raw=document.getElementById('rep-raw-data'); var shell=document.body.outerHTML.replace(s.outerHTML,'');
      // prefer a VALUE (a number in a cell, a legend, a stats line) over a heading
      var pick=null;
      [true, false].some(function(valuesOnly){
        var w=document.createTreeWalker(s, NodeFilter.SHOW_TEXT), n;
        while((n=w.nextNode())){ var t=(n.nodeValue||'').trim(); if(t.length<5||t.length>60) continue;
          if(n.parentNode.closest('script,style')) continue;
          if(valuesOnly && (!/[0-9]/.test(t) || n.parentNode.closest('h1,h2,h3,figcaption,th,summary,.rep-cap,.rep-meta'))) continue;
          var esc=t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
          if(shell.indexOf(t)<0 && shell.indexOf(esc)<0){ pick=t; return true; } }
        return false; });
      out[s.getAttribute('data-rep-sec')]=pick; });
    return JSON.stringify(out); })()`));
  const full = await build(p);
  // the raw tree's text is a lazily built DOM, its VALUES live in the packed
  // JSON: pick a stored string no other section shows, check it in the packed
  // payload of the full file and in no form in the file without the tree
  const rawFull = await rawText(p);
  const otherHtml = await p.ev(`[].slice.call(document.querySelectorAll('section[data-rep-sec]')).filter(function(s){return s.getAttribute('data-rep-sec')!=='raw'}).map(function(s){return s.outerHTML}).join('')`);
  let rawPick = null;
  for (const m of rawFull.matchAll(/"([A-Za-z_][\w.]{7,40})"/g)) {
    if (otherHtml.indexOf(m[1]) < 0) { rawPick = m[1]; break; }
  }
  uniq.raw = rawPick ? '\u0000raw:' + rawPick : uniq.raw;
  for (const k of ALL) {
    const v0 = uniq[k];
    if (v0 && v0.startsWith('\u0000raw:')) {
      const v = v0.slice(5);
      await clickBox(p, 'raw');
      const b = await build(p);
      const ok = rawFull.indexOf(v) >= 0 && b.html && b.html.indexOf(v) < 0
        && b.html.indexOf('rep-raw-data') < 0 && b.html.indexOf('data-rep-sec="raw"') < 0;
      rec(`J3 raw unchecked: stored "${v}" (in the packed tree) is not in the file, nor the blob`, ok,
          { inPacked: rawFull.indexOf(v) >= 0, inWithout: b.html && b.html.indexOf(v) >= 0 });
      await clickBox(p, 'raw');
      continue;
    }
    const v = v0;
    if (!v) { rec(`J3 ${k}: a value only this section shows`, false, 'none found'); continue; }
    await clickBox(p, k);
    const b = await build(p);
    const inFull = full.html && full.html.indexOf(v) >= 0;
    const inWithout = b.html && b.html.indexOf(v) >= 0;
    const secGone = b.html && b.html.indexOf(`data-rep-sec="${k}"`) < 0;
    rec(`J3 ${k} unchecked: "${v}" is not in the file`, inFull && !inWithout && secGone, { inFull, inWithout, secGone, err: b.error });
    await clickBox(p, k);
  }
  await ready(p);

  // ---- J4: fault (b) -- redaction on / off
  if (SECRETS.length) {
    const on = await build(p);
    const rawOn = await (async () => { await p.ev(`(function(){var d=document.createElement('div'); d.id='__tmp'; d.innerHTML=window.__rep.match(/<script type="application\\/json" id="rep-raw-data"[^>]*>[\\s\\S]*?<\\/script>/)[0]; document.body.appendChild(d); return 1;})()`);
      const t = await p.ev(`(function(){var el=document.querySelector('#__tmp #rep-raw-data'); var enc=el.getAttribute('data-enc'); var txt=el.textContent; document.getElementById('__tmp').remove();
        if(enc!=='gzip-base64') return txt; var bin=atob(txt), b=new Uint8Array(bin.length); for(var i=0;i<bin.length;i++) b[i]=bin.charCodeAt(i);
        return new Response(new Blob([b]).stream().pipeThrough(new DecompressionStream('gzip'))).text();})()`);
      return t; })();
    const leaks = SECRETS.filter(s => on.html.indexOf(s) >= 0 || on.html.indexOf(s.replace(/\//g, '\\')) >= 0 || rawOn.indexOf(s) >= 0);
    rec('J4a switch ON: no secret in the file, the packed raw tree included', leaks.length === 0 && rawOn.length > 1000, { leaks, rawLen: rawOn.length });
    // flip the switch with a real click: the page rebuilds under it
    const rc = JSON.parse(await p.ev(`(function(){var b=document.getElementById('rep-redact'); b.scrollIntoView({block:'center'}); var r=b.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()`));
    await p.click(rc.x, rc.y);
    await sleep(1500);
    for (let i = 0; i < 100; i++) { if (await p.ev(`document.readyState`) === 'complete' && await p.ev(`!!window.ChipReport`)) break; await sleep(200); }
    await ready(p);
    const offState = JSON.parse(await p.ev(`JSON.stringify({url: location.search, sw: document.getElementById('rep-redact').checked, checked: ChipReport.checked()})`));
    rec('J4b the switch reloads the page under it, the selection kept', /redact=0/.test(offState.url) && !offState.sw && offState.checked.length === ALL.length, offState);
    const off = await build(p);
    const rawOff = await rawText(p);
    const present = SECRETS.filter(s => off.html.indexOf(s) >= 0 || off.html.indexOf(s.replace(/\//g, '\\')) >= 0 || rawOff.indexOf(s) >= 0);
    rec('J4c switch OFF: the same values are in the file, as stored', present.length === SECRETS.length, { present, missing: SECRETS.filter(s => present.indexOf(s) < 0) });
    if (off.html) {
      const fOff = path.join(OUT, 'j4_unredacted.html');
      fs.writeFileSync(fOff, off.html, 'utf8');
      const o = await openOffline(fOff);
      rec('J4d the unredacted file also opens offline cleanly', o.reqs.length === 0 && o.errs.length === 0, { reqs: o.reqs.slice(0, 3), errs: o.errs });
      await o.f.close();
    }
    // back ON for the rest
    const rc2 = JSON.parse(await p.ev(`(function(){var b=document.getElementById('rep-redact'); b.scrollIntoView({block:'center'}); var r=b.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()`));
    await p.click(rc2.x, rc2.y);
    await sleep(1500);
    for (let i = 0; i < 100; i++) { if (await p.ev(`document.readyState`) === 'complete' && await p.ev(`!!window.ChipReport`)) break; await sleep(200); }
    await ready(p);
  }

  // ---- J5: fault (d) -- the same numbers as the live pages
  const same = JSON.parse(await p.ev(`(async function(){
    var out={};
    var z=document.querySelector('section[data-rep-sec="zline"]').textContent;
    var line=(document.querySelector('section[data-rep-sec="zline"] figcaption')||{}).textContent||'';
    var first=(line.match(/—\\s*([^,·]+)/)||[])[1]; first=(first||'').replace(/\\(coupler\\)/,'').trim();
    var path = first ? ((/-/.test(first)) ? 'qubit_pairs.'+first+'.coupler' : 'qubits.'+first+'.z') : '';
    if (path) { var d=await (await fetch('/zline/data?line='+encodeURIComponent(path))).json();
      out.zline = d.step ? [z.indexOf('first sample '+d.step.first.toFixed(4))>=0, z.indexOf('peak '+d.step.peak.toFixed(4))>=0] : 'no step'; }
    var inst=(await (await fetch('/api/instrument/data')).json()).instrument; var a=null;
    Object.keys(inst.controllers).some(function(c){ var f=inst.controllers[c].fems; return Object.keys(f).some(function(fid){ var o=f[fid].output_ports||{}; return Object.keys(o).some(function(pn){ return (o[pn]||[]).some(function(x){ if(x.role==='xy'&&x.lo_frequency){a=x;return true;} return false;});});});});
    var wt=document.querySelector('section[data-rep-sec="wiring"]').innerHTML;
    if (a) { var i=wt.indexOf('>'+a.label+'<'); var row=wt.slice(i, wt.indexOf('</tr>', i)); out.wiring=[a.label, row.indexOf((a.lo_frequency/1e9).toFixed(4))>=0, row.indexOf('<td>'+a.full_scale_power_dbm+'</td>')>=0]; }
    var dg=await (await fetch('/diagnostics',{headers:{'HX-Request':'true'}})).text(); var msgs=(dg.match(/<div class="diag-msg">[\\s\\S]*?<\\/div>/g)||[]);
    var rd=document.querySelector('section[data-rep-sec="diagnostics"]').textContent.replace(/\\s+/g,' ');
    var txt=function(h){ var e=document.createElement('div'); e.innerHTML=h; return e.textContent.replace(/\\s+/g,' ').trim(); };
    out.diag=[msgs.length, msgs.filter(function(m){return rd.indexOf(txt(m))<0}).length];
    var tr=await (await fetch('/topology/trends?metrics=f_01')).text(); var m=tr.match(/id="topo-trends-data">([\\s\\S]*?)<\\/script>/);
    if (m) { var ch=JSON.parse(m[1]).filter(function(c){return c.metric==='f_01'})[0];
      if (ch && ch.series.length) { var s=ch.series[0]; var v=s.points[s.points.length-1][1];
        var fig=document.querySelector('figure[data-rep-metric="f_01"]'); out.trends=[s.entity, (v/1e9).toFixed(4), !!fig && fig.textContent.indexOf(s.entity+' '+(v/1e9).toFixed(4)+' GHz')>=0]; } }
    var pl=await (await fetch('/pulses?per_page=50',{headers:{'HX-Request':'true'}})).text();
    var pm=pl.match(/data-pulse-path="([^"]+)"[\\s\\S]*?<\\/tr>/);
    if (pm) { var cells=(pm[0].match(/<td[^>]*>[\\s\\S]*?<\\/td>/g)||[]).map(function(c){return c.replace(/<[^>]+>/g,' ').replace(/\\s+/g,' ').trim()});
      var op=cells[3].split(' ')[0]; var ps=document.querySelector('section[data-rep-sec="pulses"]'); var tds=[].slice.call(ps.querySelectorAll('tr')).filter(function(r){return r.cells.length>6 && r.cells[0].textContent.trim()===cells[1] && r.cells[2].textContent.trim().split(' ')[0]===op})[0];
      out.pulses=[cells[1], op, cells[6], cells[7], tds ? [tds.cells[5].textContent.trim(), tds.cells[6].textContent.trim()] : null]; }
    return JSON.stringify(out); })()`));
  rec('J5a Z-line stats = /zline/data', Array.isArray(same.zline) && same.zline.every(Boolean), same.zline);
  rec('J5b wiring LO and FSP = /api/instrument/data', Array.isArray(same.wiring) && same.wiring[1] && same.wiring[2], same.wiring);
  rec('J5c every Diagnostics message is in the report', Array.isArray(same.diag) && same.diag[0] > 0 && same.diag[1] === 0, same.diag);
  rec('J5d Trends newest f_01 = /topology/trends last point', !same.trends || same.trends[2] === true, same.trends || 'no f_01 history');
  rec('J5e a pulse row = the Pulses page row', !!same.pulses && same.pulses[4] && same.pulses[4][0] === same.pulses[2] && same.pulses[4][1] === same.pulses[3], same.pulses);

  // ---- J6: fault (e) -- the network refuses a section and the rack frame
  {
    const q = await open('about:blank', 1400, 950);
    await q.send('Fetch.enable', { patterns: [{ urlPattern: '*report/section/zline*' }, { urlPattern: '*report/frame/wiring*' }] });
    const mark = q.events.length;
    const pump = setInterval(async () => {
      for (const e of q.events.slice(mark)) {
        if (e.method === 'Fetch.requestPaused' && !e.__done) {
          e.__done = true;
          await q.send('Fetch.fulfillRequest', { requestId: e.params.requestId, responseCode: 500,
            responseHeaders: [{ name: 'Content-Type', value: 'text/plain' }],
            body: Buffer.from('planted fault').toString('base64') });
        }
      }
    }, 50);
    await q.send('Page.navigate', { url: `${BASE}/chip-status/report?sections=overview,zline,wiring&redact=1` });
    await sleep(1500);
    const t6 = await ready(q, 30000);
    const z = await q.ev(`document.querySelector('section[data-rep-sec="zline"]').textContent.trim().replace(/\\s+/g,' ').slice(0,120)`);
    const w = await q.ev(`(document.querySelector('[data-rep-iw]')||{textContent:''}).textContent.trim()`);
    rec('J6a a refused section says "Could not be built" in its place', /Could not be built: HTTP 500/.test(z), z);
    rec('J6b a refused rack frame says so, without the 20 s wait', /Could not be built: the drawing page could not be loaded/.test(w) && t6 >= 0 && t6 < 8000, { w, t6 });
    const b6 = await build(q);
    rec('J6c the file carries the honest lines, not blanks', !!b6.html && /Could not be built: HTTP 500/.test(b6.html) && /drawing page could not be loaded/.test(b6.html), b6.error);
    clearInterval(pump);
    await q.close();
  }

  // ---- J7: reload and Back
  {
    const url0 = await p.ev('location.href');
    await p.send('Page.reload', {});
    await sleep(1500);
    await ready(p);
    const r1 = JSON.parse(await p.ev(`JSON.stringify({checked: ChipReport.checked(), url: location.href})`));
    rec('J7a reload keeps the sections and the switch', r1.url === url0 && r1.checked.length === ALL.length, r1);
    await p.send('Page.navigate', { url: `${BASE}/qubits` });
    await sleep(2500);
    await p.ev('history.back()');
    await sleep(2500);
    await ready(p);
    const r2 = JSON.parse(await p.ev(`JSON.stringify({checked: (window.ChipReport ? ChipReport.checked() : null), url: location.href, secs: document.querySelectorAll('section[data-rep-sec]:not([hidden])').length})`));
    rec('J7b Back returns to the same report, intact', r2.url === url0 && r2.checked && r2.checked.length === ALL.length && r2.secs === ALL.length, r2);
  }
  const errs = p.errors(0);
  rec('J8 zero console errors on the report page through the whole walk', errs.length === 0, errs);
  await p.close();
  const failed = res.filter(r => !r.ok);
  console.log(`\n${res.length - failed.length}/${res.length} passed`);
  fs.writeFileSync(path.join(OUT, 'journey.json'), JSON.stringify(res, null, 1));
  process.exit(failed.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
