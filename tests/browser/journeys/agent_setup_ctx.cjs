/* Agent setup section 5 (lab context): Show the questions -> answer -> Preview
 * -> Write ONLY when every previewed file is inside ALLOWED_ROOT (the rig).
 *   SM_CDP_PORT=9413 node agent_setup_ctx.cjs 5113 OUTDIR WIDTH ALLOWED_ROOT
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const [SM, OUT, W, ROOT] = [process.argv[2], process.argv[3], +process.argv[4] || 1366, process.argv[5]];

(async () => {
  const p = await open(`http://127.0.0.1:${SM}/agent/setup`, W, 900);
  await sleep(2000);
  const at = async (re) => {
    const r = await p.ev(`(() => { const d = [...document.querySelectorAll('details.as-sec')].find(d => /Lab context/.test(d.textContent)); d.open = true;
      const e = [...d.querySelectorAll('button')].find(b => ${re}.test(b.textContent) && b.getBoundingClientRect().width > 0);
      if (!e) return null; e.scrollIntoView({block:'center'}); const b = e.getBoundingClientRect(); return JSON.stringify([b.x + b.width/2, b.y + b.height/2]); })()`);
    return r ? JSON.parse(r) : null;
  };
  const press = async (re) => { const xy = await at(re); if (!xy) return false; await p.click(xy[0], xy[1]); await sleep(1500); return true; };
  console.log('show', await press('/Show the questions/'));
  const ctl = `(() => { const d = [...document.querySelectorAll('details.as-sec')].find(d => /Lab context/.test(d.textContent));
    return [...d.querySelectorAll('select, input, textarea, button')].map(e => e.tagName + ':' + (e.id || e.name || '') + '=' + (e.type === 'checkbox' ? e.checked : (e.value || e.textContent || '').trim().slice(0, 30))).join(' ; '); })()`;
  console.log('controls', await p.ev(ctl));
  console.log('facts', await p.ev(`(() => { const d = [...document.querySelectorAll('details.as-sec')].find(d => /Lab context/.test(d.textContent)); return d.innerText.replace(/\\s+/g, ' ').slice(0, 700); })()`));
  await p.shot(`${OUT}/ctx_questions_${W}.png`);
  console.log('preview', await press('/^\\s*Preview/'));
  const files = JSON.parse(await p.ev(`JSON.stringify([...document.querySelectorAll('#as-ctx-prev h5 code')].map(c => c.textContent))`) || '[]');
  console.log('files', JSON.stringify(files));
  await p.shot(`${OUT}/ctx_preview_${W}.png`);
  const norm = s => s.replace(/\//g, '\\').toLowerCase();
  if (files.length && files.every(f => norm(f).startsWith(norm(ROOT)))) {
    console.log('write', await press('/Write/'));
    await sleep(1500);
    console.log('after write', await p.ev(`(() => { const s = [...document.querySelectorAll('details.as-sec > summary')].find(s => /Lab context/.test(s.textContent)); return s && s.textContent.trim().slice(0, 40); })()`));
    await p.shot(`${OUT}/ctx_written_${W}.png`);
  } else console.log('NOT writing: a file outside the rig');
  const e = p.errors(); if (e.length) console.log('ERRORS', e.join(' ## '));
  await p.close(); process.exit(0);
})();
