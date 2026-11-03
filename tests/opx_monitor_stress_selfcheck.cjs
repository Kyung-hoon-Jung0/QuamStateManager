'use strict';
const {JSDOM} = require('jsdom');
const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '../quam_state_manager/web/static');
const pin = (v,m) => {if (!v) throw new Error(m);};
(async()=>{
 const w = new JSDOM(`<div id="ui-opxmon-body" hx-get="/opx-monitor/body?tab=cleanup">
 <div class="ui-opxmon-health" data-valid-ms="20" data-health="green">BUSY</div>
 <p class="ui-opxmon-status"><span class="ui-opxmon-sentence">Updated 2 s ago</span></p>
 <button class="ui-opxmon-close" data-qm-id="QM-one">Close</button>
 <button class="ui-opxmon-close-all" data-qm-ids="QM-one">Close all</button>
 <button class="ui-opxmon-close-idle">Close idle</button>
 <p class="ui-opxmon-close-result" hidden></p></div>`,{url:'http://localhost',runScripts:'outside-only'}).window;
 let posts=0, refreshes=0;
 w.fetch=async()=>{posts++;return {json:async()=>({ok:true,closed:['QM-one']})};};
 w.htmx={ajax:()=>{refreshes++;}};
 w.eval(fs.readFileSync(path.join(root,'opx-monitor.js'),'utf8'));
 w.document.querySelector('.ui-opxmon-close-all').click();
 w.document.querySelector('.ui-opxmon-confirm-count').value='2';
 w.document.querySelector('.ui-opxmon-confirm-accept').click();
 pin(posts===0 && /Nothing closed. Type 1/.test(w.document.querySelector('.ui-opxmon-close-result').textContent),'D2 wrong count must explain why nothing closed');
 w.document.querySelector('.ui-opxmon-confirm-count').value='1';
 w.document.querySelector('.ui-opxmon-confirm-accept').click();
 await new Promise(r=>setTimeout(r,10));
 pin(posts===1 && refreshes===1,'D2 every close must re-render');
 w.eval(fs.readFileSync(path.join(root,'opx-health.js'),'utf8'));
 await new Promise(r=>setTimeout(r,40));
 pin(!/Updated 2 s ago/.test(w.document.querySelector('.ui-opxmon-sentence').textContent) && /No fresh answer/.test(w.document.querySelector('.ui-opxmon-sentence').textContent),'D4 status and verdict expire together');
 pin([...w.document.querySelectorAll('button')].every(b=>b.disabled),'D2 stale data disables every close control');
 pin(/freshness expired/.test(w.document.querySelector('.ui-opxmon-health').textContent),'D4 verdict expired');
 w.close();
 console.log('D2 count, re-render, stale controls; D4 common freshness: 5 pins passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
