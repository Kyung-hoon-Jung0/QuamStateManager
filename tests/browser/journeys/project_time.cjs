/* docs/263 journey, in real Chrome: the project time zone and the run clock.
 *
 *   J1  landing (browser emulated in America/Los_Angeles, the PC is UTC+9):
 *       the zone picker sits ABOVE the env picker; the PC's clock line
 *       arrives after the render
 *   J2  pick a zone that differs from the PC -> the popup -> "view in ..."
 *       -> the watch check ("now HH:MM" matches the real time) -> stored
 *   J3  reload: the zone is SHOWN, nothing asked
 *   J4  a second project defaults to the first project's zone, and keeps it
 *       when opened; every page renders in it (<html data-sm-zone>)
 *   J5  Settings: no second zone -- the project's zone + "Change on Projects"
 *   J6  three runs land live with their own clock 1 h ahead -> asked ONCE,
 *       with the evidence -> answered -> stored
 *   J7  reload + one more run with the same skew -> not asked again
 *   J8  Diagnostics: the one clock line names the answer
 *   J9  run detail: a corrected run is labelled with its recorded time kept;
 *       an older run (same clock, other zone) is just shown in the viewer zone
 *   J10 open -> back -> reload intact; console errors 0 everywhere
 *
 * usage: SM_CDP_PORT=9443 node tests/browser/journeys/project_time.cjs 5123 <shots> <rig>
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const fs = require('fs');
const path = require('path');
const PORT = process.argv[2] || '5123';
const SHOTS = process.argv[3] || '.';
const RIG = process.argv[4];
const BASE = `http://127.0.0.1:${PORT}`;
const VIEWER_TZ = 'America/Los_Angeles';
const res = [];
const allErrors = [];
function rec(name, ok, detail) {
  res.push({ name, ok });
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail !== undefined ? '  ' + JSON.stringify(detail).slice(0, 600) : ''));
}
const J = async (p, e) => { const v = await p.ev(e); try { return JSON.parse(v); } catch (x) { return v; } };
async function center(p, sel, text) {
  return J(p, `(function(){ var b=Array.from(document.querySelectorAll(${JSON.stringify(sel)})).filter(function(x){var r=x.getBoundingClientRect(); return r.width>0&&r.height>0 && (${JSON.stringify(text || '')}==='' || (x.textContent||'').trim().indexOf(${JSON.stringify(text || '')})===0);})[0];
    if(!b) return 'null'; b.scrollIntoView({block:'nearest'}); var r=b.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)}); })()`);
}
async function press(p, sel, text) {
  const c = await center(p, sel, text);
  if (!c || c === 'null') return false;
  await p.click(c.x, c.y);
  return true;
}
async function waitFor(p, expr, ms) {
  const t0 = Date.now();
  while (Date.now() - t0 < (ms || 15000)) { if (await p.ev(expr)) return true; await sleep(250); }
  return false;
}
async function page(url) {
  const p = await open(`${BASE}${url}`);
  await p.send('Emulation.setTimezoneOverride', { timezoneId: VIEWER_TZ });
  await p.send('Page.reload', {});
  await sleep(1500);
  await waitFor(p, "document.readyState==='complete'");
  return p;
}
function store() {
  return JSON.parse(fs.readFileSync(path.join(RIG, 'inst', 'project_time.json'), 'utf8'));
}
function laNow() {
  const f = new Intl.DateTimeFormat('en-GB', { timeZone: VIEWER_TZ, hour: '2-digit', minute: '2-digit', hour12: false });
  return f.format(new Date());
}
/* a run saved by the experiment PC whose own clock is AHEAD by skew_s */
let RUN_ID = 100;
function writeRun(project, skewS) {
  const id = ++RUN_ID;
  const src = path.join(RIG, project, 'data', '2026-09-30');
  const tmpl = fs.readdirSync(src).find((d) => d.startsWith('#1_'));
  const claim = new Date(Date.now() + skewS * 1000);
  const local = new Date(claim.getTime() + 9 * 3600 * 1000);           // written in +09:00
  const p2 = (n) => String(n).padStart(2, '0');
  const day = `${local.getUTCFullYear()}-${p2(local.getUTCMonth() + 1)}-${p2(local.getUTCDate())}`;
  const hms = `${p2(local.getUTCHours())}${p2(local.getUTCMinutes())}${p2(local.getUTCSeconds())}`;
  const iso = `${day}T${p2(local.getUTCHours())}:${p2(local.getUTCMinutes())}:${p2(local.getUTCSeconds())}+09:00`;
  const dir = path.join(RIG, project, 'data', day, `#${id}_03_resonator_spectroscopy_single_${hms}`);
  fs.mkdirSync(dir, { recursive: true });
  const node = JSON.parse(fs.readFileSync(path.join(src, tmpl, 'node.json'), 'utf8'));
  node.id = id; node.created_at = iso;
  node.metadata = Object.assign({}, node.metadata || {}, { run_start: iso, run_end: iso });
  fs.copyFileSync(path.join(src, tmpl, 'data.json'), path.join(dir, 'data.json'));
  fs.writeFileSync(path.join(dir, 'node.json'), JSON.stringify(node));
  return { id, dir, iso };
}

(async () => {
  // ---------------------------------------------------------------- J1
  let p = await page('/?landing=1');
  await waitFor(p, "!!document.getElementById('landing-tz')");
  await waitFor(p, "/This PC:/.test((document.querySelector('[data-tz-clock]')||{}).textContent||'')");
  const geo = await J(p, `JSON.stringify({tz:document.getElementById('landing-tz').getBoundingClientRect().top,
      env:document.getElementById('landing-env-picker').getBoundingClientRect().top,
      cur:document.querySelector('[data-tz-current]').textContent, st:document.querySelector('[data-tz-state]').textContent,
      clock:document.querySelector('[data-tz-clock]').textContent, project:document.querySelector('[data-tz-project]').textContent,
      browser:Intl.DateTimeFormat().resolvedOptions().timeZone})`);
  rec('J1 the zone picker sits above the env picker', geo.tz < geo.env, geo);
  rec('J1 the browser is emulated in Los Angeles, the PC clock line arrives after the render',
      geo.browser === VIEWER_TZ && /This PC: UTC\+9/.test(geo.clock), geo);
  await p.shot(path.join(SHOTS, 'j1_landing.png'));

  // ---------------------------------------------------------------- J2
  await press(p, '[data-tz-change]');
  await sleep(300);
  await p.send('Input.insertText', { text: 'los ang' });
  await sleep(400);
  rec('J2 the search lists Los Angeles with its offset',
      await waitFor(p, "/America\\/Los_Angeles.*UTC-7/.test((document.querySelector('[data-tz-list] [data-zone]')||{}).textContent||'')", 3000),
      await p.ev("(document.querySelector('[data-tz-list]')||{}).textContent"));
  await p.shot(path.join(SHOTS, 'j2a_search.png'));
  await press(p, '[data-tz-list] [data-zone="America/Los_Angeles"]');
  const popped = await waitFor(p, "!!(document.getElementById('pt-zone-dialog')||{}).open", 5000);
  const ptext = await p.ev("(document.getElementById('pt-zone-dialog')||{}).textContent||''");
  rec('J2 a zone that differs from the PC asks first', popped && /This PC is UTC\+9/.test(ptext) && /16 h apart/.test(ptext), ptext);
  await p.shot(path.join(SHOTS, 'j2b_pc_popup.png'));
  await press(p, '[data-pt-answer="view"]');
  await waitFor(p, "/Does it match your watch/.test((document.getElementById('pt-zone-dialog')||{}).textContent||'')", 5000);
  const wtext = await p.ev("document.getElementById('pt-zone-dialog').textContent");
  const m = /Now (\d\d:\d\d) in America\/Los_Angeles \(UTC-7\)/.exec(wtext || '');
  rec('J2 the watch check shows now in the chosen zone (matches the real clock)', !!m && (m[1] === laNow() || Math.abs(
      (+m[1].slice(0, 2) * 60 + +m[1].slice(3)) - (+laNow().slice(0, 2) * 60 + +laNow().slice(3))) <= 1), { wtext, real: laNow() });
  rec('J2 with the time-sync line', /Time sync:/.test(wtext), wtext);
  await p.shot(path.join(SHOTS, 'j2c_watch.png'));
  await press(p, '[data-pt-answer="matches"]');
  await sleep(800);
  const s1 = store();
  rec('J2 stored per project with both answers', s1.projects.alpha.zone === VIEWER_TZ &&
      s1.projects.alpha.os_check.answer === 'view' && s1.projects.alpha.watch.answer === 'matches', s1.projects.alpha);
  allErrors.push(...p.errors(0));

  // ---------------------------------------------------------------- J3
  await p.send('Page.reload', {});
  await sleep(2500);
  await waitFor(p, "!!document.querySelector('[data-tz-current]') && /now/.test(document.querySelector('[data-tz-current]').textContent)");
  const after = await J(p, `JSON.stringify({cur:document.querySelector('[data-tz-current]').textContent,
      st:document.querySelector('[data-tz-state]').textContent, dlg:!!document.querySelector('dialog[open]'),
      card:(document.querySelector('[data-tz-card="alpha"]')||{}).textContent})`);
  rec('J3 a reload SHOWS the zone and asks nothing', /^America\/Los_Angeles · UTC-7 · now/.test(after.cur) &&
      after.st === 'saved' && !after.dlg && /Los Angeles UTC-7/.test(after.card), after);
  await p.shot(path.join(SHOTS, 'j3_reload.png'));

  // ---------------------------------------------------------------- J4
  await press(p, '[data-env-change="beta"]');
  await sleep(700);
  const beta = await J(p, `JSON.stringify({project:document.querySelector('[data-tz-project]').textContent,
      cur:document.querySelector('[data-tz-current]').textContent, st:document.querySelector('[data-tz-state]').textContent})`);
  rec('J4 a second project defaults to the first project\'s zone', beta.project === 'beta' &&
      /^America\/Los_Angeles/.test(beta.cur) && /default from alpha/.test(beta.st), beta);
  await p.shot(path.join(SHOTS, 'j4a_beta_default.png'));
  await p.ev(`(function(){ var f=Array.from(document.querySelectorAll('form.landing-card-open')).filter(function(f){return f.querySelector('input[name=project]').value==='beta';})[0]; f.querySelector('button').scrollIntoView({block:'center'}); return 1; })()`);
  await sleep(300);
  const openBeta = await J(p, `(function(){ var f=Array.from(document.querySelectorAll('form.landing-card-open')).filter(function(f){return f.querySelector('input[name=project]').value==='beta';})[0]; var r=f.querySelector('button').getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)}); })()`);
  await p.click(openBeta.x, openBeta.y);
  await waitFor(p, "location.pathname!=='/'", 20000);
  await sleep(2500);
  const bpage = await J(p, `JSON.stringify({path:location.pathname, zone:document.documentElement.getAttribute('data-sm-zone'),
      project:document.documentElement.getAttribute('data-sm-project')})`);
  const s2 = store();
  rec('J4 opening it keeps that zone, and the page renders in it', bpage.zone === VIEWER_TZ && bpage.project === 'beta' &&
      s2.projects.beta.zone === VIEWER_TZ && s2.projects.beta.how === 'default', { bpage, beta: s2.projects.beta });

  // ---------------------------------------------------------------- J5
  await p.ev("toggleSettings(document.querySelector('.settings-btn'))");
  await sleep(600);
  const set = await J(p, `JSON.stringify({select:!!document.getElementById('tz-select'),
      cur:(document.getElementById('tz-current')||{}).textContent, note:(document.getElementById('tz-note')||{}).textContent,
      link:(document.querySelector('.settings-tz-change')||{}).textContent})`);
  rec('J5 Settings has ONE zone: the project\'s, with Change on Projects', !set.select &&
      /America\/Los_Angeles \(UTC-7\)/.test(set.cur) && /project beta/.test(set.note) && set.link === 'Change on Projects', set);
  await p.shot(path.join(SHOTS, 'j5_settings.png'));
  await p.ev("toggleSettings()");
  allErrors.push(...p.errors(0));
  await p.close();

  // ---------------------------------------------------------------- J6
  p = await page('/?landing=1');
  await waitFor(p, "!!document.querySelector('form.landing-card-open')");
  const openAlpha = await J(p, `(function(){ var f=Array.from(document.querySelectorAll('form.landing-card-open')).filter(function(f){return f.querySelector('input[name=project]').value==='alpha';})[0]; f.querySelector('button').scrollIntoView({block:'center'}); var r=f.querySelector('button').getBoundingClientRect(); return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)}); })()`);
  await p.click(openAlpha.x, openAlpha.y);
  await waitFor(p, "location.pathname!=='/'", 20000);
  await p.send('Page.navigate', { url: `${BASE}/datasets` });
  await sleep(4000);
  const proj = await p.ev("document.documentElement.getAttribute('data-sm-project')");
  rec('J6 project alpha is open (the watcher is watching its data folder)', proj === 'alpha', proj);
  const mark = p.events.length;
  const runs = [];
  for (let i = 0; i < 3; i++) { runs.push(writeRun('alpha', 3600)); await sleep(3000); }
  const asked = await waitFor(p, "!!(document.getElementById('pt-skew-dialog')||{}).open", 30000);
  const stext = await p.ev("(document.getElementById('pt-skew-dialog')||{}).textContent||''");
  rec('J6 three runs with the run clock 1 h ahead raise the question once, with the evidence',
      asked && /Two clocks disagree by 1 h 00 min/.test(stext) && /SM saw 3 runs of alpha arrive/.test(stext), stext);
  rec('J6 the correction starts at a named run (not at its skewed clock)',
      new RegExp('corrected from run #' + runs[0].id + '_').test(stext), stext.slice(stext.indexOf('Only')));
  await p.shot(path.join(SHOTS, 'j6_skew_ask.png'));
  const s3 = store();
  rec('J6 it is marked shown (asked once)', s3.projects.alpha.clock.shown && Math.abs(s3.projects.alpha.clock.shown.skew_s - 3600) < 120,
      s3.projects.alpha.clock.shown);
  await press(p, '[data-pt-choice="experiment_pc"]');
  await sleep(1200);
  const s4 = store();
  const ans = (s4.projects.alpha.clock.answers || [])[0] || {};
  rec('J6 the answer is stored with the measured skew', ans.choice === 'experiment_pc' && Math.abs(ans.skew_s - 3600) < 120 &&
      !(await p.ev("!!(document.getElementById('pt-skew-dialog')||{}).open")), ans);
  allErrors.push(...p.errors(mark));

  // ---------------------------------------------------------------- J7
  await p.send('Page.reload', {});
  await sleep(2000);
  runs.push(writeRun('alpha', 3600));
  await sleep(12000);
  const again = await p.ev("!!(document.getElementById('pt-skew-dialog')||{}).open");
  const s5 = store();
  rec('J7 reload + another run at the same skew: not asked again', !again && (s5.projects.alpha.clock.answers || []).length === 1 &&
      s5.projects.alpha.clock.witnesses.filter((w) => w.src === 'live').length >= 4,
      { again, live: s5.projects.alpha.clock.witnesses.filter((w) => w.src === 'live').length });
  await p.shot(path.join(SHOTS, 'j7_not_again.png'));

  // ---------------------------------------------------------------- J8
  await p.send('Page.navigate', { url: `${BASE}/diagnostics` });
  await sleep(3000);
  const line = await p.ev("(document.querySelector('[data-clock-line]')||{}).textContent||''");
  rec('J8 Diagnostics: one clock line names the answer', /Run clock: 1 h 00 min ahead of this PC/.test(line) &&
      /the experiment PC's clock is wrong/.test(line) && (await p.ev("document.querySelectorAll('[data-clock-line]').length")) === 1, line);
  await p.ev("(document.querySelector('[data-clock-line]')||document.body).scrollIntoView({block:'center'})");
  await p.shot(path.join(SHOTS, 'j8_diagnostics.png'));

  // ---------------------------------------------------------------- J9
  await p.send('Page.navigate', { url: `${BASE}/dataset/by-run/${runs[0].id}` });
  await sleep(3500);
  const corr = await J(p, `JSON.stringify({text:(document.querySelector('.ts-run')||{}).textContent, title:(document.querySelector('.ts-run')||{}).title})`);
  rec('J9 a corrected run is labelled and keeps its recorded time', /corrected/.test(corr.text) && /recorded .*\(UTC\+9\)/.test(corr.text) &&
      /\(UTC-7\)/.test(corr.text), corr);
  await p.ev("(document.querySelector('.ts-run')||document.body).scrollIntoView({block:'center'})");
  await p.shot(path.join(SHOTS, 'j9a_run_corrected.png'));
  await p.send('Page.navigate', { url: `${BASE}/dataset/by-run/1` });
  await sleep(3500);
  const plain = await J(p, `JSON.stringify({text:(document.querySelector('.ts-run')||{}).textContent, title:(document.querySelector('.ts-run')||{}).title})`);
  rec('J9 an older run in another zone is just shown in the viewer zone (no error, recorded time in the tooltip)',
      /\(UTC-7\)$/.test(plain.text || '') && !/corrected/.test(plain.text || '') && /^recorded 2026-09-30 13:54:25 \(UTC\+9\)/.test(plain.title || ''), plain);
  await p.ev("(document.querySelector('.ts-run')||document.body).scrollIntoView({block:'center'})");
  await p.shot(path.join(SHOTS, 'j9b_run_zone_only.png'));

  // ---------------------------------------------------------------- J10
  await p.ev('history.back()');
  await sleep(3000);
  const back = await J(p, `JSON.stringify({path:location.pathname, ts:(document.querySelector('.ts-run')||{}).textContent||null})`);
  rec('J10 back returns to the corrected run, still rendered', /\/dataset\//.test(back.path) && /corrected/.test(back.ts || ''), back);
  await p.send('Page.reload', {});
  await sleep(3500);
  const re = await J(p, `JSON.stringify({path:location.pathname, ts:(document.querySelector('.ts-run')||{}).textContent||null, zone:document.documentElement.getAttribute('data-sm-zone')})`);
  rec('J10 reload keeps it', /corrected/.test(re.ts || '') && re.zone === VIEWER_TZ, re);
  allErrors.push(...p.errors(0));
  rec('J10 console errors 0 across the journey', allErrors.length === 0, allErrors);
  await p.close();
  const failed = res.filter((r) => !r.ok).length;
  console.log(`\n${res.length - failed}/${res.length} passed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('CRASH', e && e.stack || e); process.exit(1); });
