'use strict';
// Fake-only real Chrome checks. Start opx_monitor_stress_server.py first.
const fs = require('fs');
const path = require('path');
const cp = require('child_process');
const {open, sleep} = require('./opx_monitor_stress_cdp.cjs');
const dir = process.env.OPX_STRESS_DIR;
if (!dir) throw new Error('OPX_STRESS_DIR must name the copied fake fixtures');
const base = 'http://127.0.0.1:5687';
const shots = path.join(dir,'shots'); fs.mkdirSync(shots,{recursive:true});
const results=[];
let chromeVersion='';
function pin(value,message){if(!value)throw new Error(message);results.push(message);}
function scenario(name, modify=()=>{}) {
 cp.execFileSync(process.env.OPX_STRESS_PYTHON || 'python',[path.join(dir,'scen.py'),name]);
 const file=path.join(dir,'scenario.json');
 const d=JSON.parse(fs.readFileSync(file,'utf8'));
 for(const q of d.qms || [])for(const j of q.jobs || []){delete j.user;j.desc='Example program';}
 for(const j of d.old_jobs || []){delete j.user;j.desc='Example program';}
 modify(d);
 fs.writeFileSync(file+'.tmp',JSON.stringify(d));fs.renameSync(file+'.tmp',file);
}
async function click(p,sel){
 const r=await p.ev(`(()=>{const e=document.querySelector(${JSON.stringify(sel)}); if(!e)return null;e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})()`);
 pin(!!r,'click target exists: '+sel);await p.click(...r);
}
async function settle(p,predicate,timeout=10000){
 for(let i=0;i<timeout/200;i++){if(await p.ev(predicate))return;await sleep(200);}
 throw new Error('Timed out: '+predicate);
}
async function prepare(p,theme){
 await p.ev(`document.documentElement.setAttribute('data-theme',${JSON.stringify(theme)});localStorage.setItem('theme',${JSON.stringify(theme)})`);
 if(await p.ev("!document.querySelector('.app-layout').classList.contains('sidebar-collapsed')"))await click(p,'.sidebar-toggle');
 await sleep(250);
}
async function shot(p,name){await p.shot(path.join(shots,name+'.png'));}
(async()=>{
 scenario('normal');
 // Check the normal tiles and close controls at every requested size and theme.
 for(const theme of ['light','dark'])for(const width of [390,768,1600]){
  const p=await open(base+'/opx-monitor/live',width,1000);
  try{
   await prepare(p,theme);
   await settle(p,"document.querySelector('#ui-opxmon-body').dataset.state === 'ok'");
   pin(await p.ev("[...document.querySelectorAll('.ui-opxmon-tile summary > span')].every(e=>e.getBoundingClientRect().width>=95)"),`${theme}/${width}: tile labels have room`);
   pin(await p.ev("document.querySelector('.ui-opxmon-tiles').scrollWidth<=document.querySelector('.ui-opxmon-tiles').clientWidth+1"),`${theme}/${width}: tiles fit`);
   await shot(p,theme+'-'+width+'-live');
   await p.send('Page.navigate',{url:base+'/opx-monitor/cleanup'});await sleep(700);await prepare(p,theme);
   await settle(p,"!!document.querySelector('.ui-opxmon-close')");
   pin(await p.ev("[...document.querySelectorAll('.ui-opxmon-close')].every(e=>{let r=e.getBoundingClientRect();return r.x>=0&&r.right<=innerWidth})"),`${theme}/${width}: Close buttons fit`);
   await shot(p,theme+'-'+width+'-cleanup');
   await p.send('Page.navigate',{url:base+'/opx-monitor/settings'});await sleep(600);await prepare(p,theme);
   pin(await p.ev("document.querySelector('input[name=python]')?.list?.id === 'ui-opxmon-interpreters'"),`${theme}/${width}: interpreter accepts a typed path`);
   pin(await p.ev("Math.abs(document.querySelector('input[name=host]').getBoundingClientRect().height-document.querySelector('input[name=port]').getBoundingClientRect().height)<1"),`${theme}/${width}: Host and Port align`);
   await click(p,'input[name=python]');
   await p.ev("document.querySelector('input[name=python]').select()");
   await p.send('Input.insertText',{text:'D:/fake-sdk/python.exe'});
   pin(await p.ev("document.querySelector('input[name=python]').value === 'D:/fake-sdk/python.exe'"),`${theme}/${width}: typed path appears in field`);
   await shot(p,theme+'-'+width+'-settings');
   pin(p.errors().length===0,`${theme}/${width}: no browser errors`);
  }finally{await p.close();}
 }
 const p=await open(base+'/opx-monitor/cleanup',390,1000);
 try{
  chromeVersion=(await p.send('Browser.getVersion')).result.product;
  await prepare(p,'light');await settle(p,"!!document.querySelector('.ui-opxmon-close')");
  await click(p,'.ui-opxmon-close');await click(p,'.ui-opxmon-confirm-accept');
  await settle(p,"!document.querySelector('[data-qm-id=\"qm-17001\"]') && /Closed: qm-17001/.test(document.querySelector('.ui-opxmon-close-result').textContent)");
  pin(true,'D2 confirmed close removes row and preserves outcome');
  await click(p,'.ui-opxmon-danger summary');await click(p,'.ui-opxmon-close-all');
  await p.ev("document.querySelector('.ui-opxmon-confirm-count').value='999'");await click(p,'.ui-opxmon-confirm-accept');
  pin(await p.ev("/Nothing closed. Type 1/.test(document.querySelector('.ui-opxmon-close-result').textContent)"),'D2 wrong count explains why nothing closed');
  await p.ev("document.querySelector('.ui-opxmon-confirm-count').value='1'");await click(p,'.ui-opxmon-confirm-accept');
  await settle(p,"!document.querySelector('.ui-opxmon-close') && /FREE/.test(document.querySelector('.ui-opxmon-verdict').textContent)");
  pin(true,'D2 Close all removes all controls and updates verdict');
  scenario('many',d=>{for(const q of d.qms)for(const j of q.jobs)j.id='shared-prefix-'+j.id;});
  await p.send('Page.navigate',{url:base+'/opx-monitor/jobs'});await sleep(700);await prepare(p,'light');
  await settle(p,"document.querySelectorAll('.ui-opxmon-qm-row').length===40");
  pin(await p.ev("new Set([...document.querySelectorAll('.ui-opxmon-qm-row')].map(e=>e.querySelectorAll('td')[2].querySelector('code').textContent)).size===40"),'D6 40 QMs have distinguishable full job IDs');
  await shot(p,'many-390-jobs');
  scenario('garbage2');await sleep(2600);
  pin(await p.ev("document.querySelectorAll('.ui-opxmon-qm-row').length===2"),'D5b malformed devices retain QM and job answers');
  scenario('down');await sleep(2600);
  pin(await p.ev("/Server unreachable/.test(document.querySelector('.ui-opxmon-verdict').textContent) && !/Updated 0 s ago/.test(document.querySelector('.ui-opxmon-sentence').textContent)"),'D5 unreachable headline gives actual cause');
  await shot(p,'outage-390-jobs');
  scenario('normal');await sleep(2600);
  // A non-polling settings tab must expire the status and verdict together.
  await p.send('Page.navigate',{url:base+'/opx-monitor/settings'});await sleep(900);await prepare(p,'dark');
  await sleep(6500);
  pin(await p.ev("/freshness expired/.test(document.querySelector('.ui-opxmon-verdict').textContent) && !/Updated/.test(document.querySelector('.ui-opxmon-sentence').textContent)"),'D4 non-polling tab expires status and verdict together');
  await shot(p,'expired-390-settings');
  // Freeze local refresh responses; expiry disables individual, idle and all controls.
  await p.send('Page.navigate',{url:base+'/opx-monitor/cleanup'});await sleep(1200);await prepare(p,'dark');
  await settle(p,"!!document.querySelector('.ui-opxmon-close-all')");
  await p.send('Network.enable');await p.send('Network.setBlockedURLs',{urls:['*/opx-monitor/body*','*/opx-monitor/alarm-badge*']});
  await sleep(6500);
  pin(await p.ev("[...document.querySelectorAll('.ui-opxmon-close,.ui-opxmon-close-all,.ui-opxmon-close-idle')].every(e=>e.disabled)"),'D2 stale Chrome page disables all close controls');
  await shot(p,'stale-390-cleanup');
  await p.send('Network.setBlockedURLs',{urls:[]});
  await p.send('Page.navigate',{url:base+'/opx-monitor/settings'});await sleep(900);
  await p.ev(`(async()=>{const f=new FormData(document.querySelector('.ui-opxmon-settings'));f.set('alarms_armed','1');await fetch('/opx-monitor/settings',{method:'POST',body:f});})()`);
  await p.send('Page.navigate',{url:'about:blank'});await sleep(9000);
  scenario('down');const arrival=Date.now();
  await p.send('Page.navigate',{url:base+'/opx-monitor/cleanup'});await sleep(400);await prepare(p,'dark');
  pin(await p.ev("!(/BUSY/.test(document.querySelector('.ui-opxmon-verdict').textContent) && /Updated/.test(document.querySelector('.ui-opxmon-sentence').textContent))"),'D3/R6 returning viewer never sees cached BUSY as fresh');
  await settle(p,"/Server unreachable/.test(document.querySelector('.ui-opxmon-verdict').textContent)",4000);
  pin(Date.now()-arrival<5000,'D3 viewer wakes alarm poller before 60-second sleep ends');
  pin(await p.ev("!document.querySelector('.ui-opxmon-close')"),'D3 outage closes are unavailable');
  await shot(p,'alarm-wake-390-cleanup');
  scenario('normal');
  await p.send('Page.navigate',{url:base+'/opx-monitor/settings'});await sleep(700);
  await p.ev(`(async()=>{const f=new FormData(document.querySelector('.ui-opxmon-settings'));f.delete('alarms_armed');f.set('host','');await fetch('/opx-monitor/settings',{method:'POST',body:f});})()`);
  await p.send('Page.navigate',{url:base+'/opx-monitor/live'});await sleep(700);await prepare(p,'light');
  pin(await p.ev("/Health not assessed/.test(document.querySelector('.ui-opxmon-verdict').textContent) && !/PROBLEM/.test(document.querySelector('.ui-opxmon-verdict').textContent)"),'D1 unconfigured monitor says health not assessed');
  await shot(p,'unconfigured-390-live');

 }finally{await p.close();}
 fs.writeFileSync(path.join(dir,'chrome-results.json'),JSON.stringify({chromeVersion,checks:results.length,results},null,2));
 console.log(results.length+' real Chrome checks passed; screenshots: '+shots);
})().catch(e=>{console.error(e);process.exitCode=1;});
