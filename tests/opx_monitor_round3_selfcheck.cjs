'use strict';
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const pages = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const doc = key => new JSDOM(pages[key]).window.document;
let count = 0;
function pin(value, message) {if (!value) throw new Error(message); count++;}
pin(doc('d21').querySelector('[data-state="error"]') && /as of/.test(doc('d21').body.textContent) && !/Updated/.test(doc('d21').body.textContent), 'D-21 refused data must not look updated');
pin(/closed: none/.test(doc('d22').body.textContent) && /failed/.test(doc('d22').body.textContent), 'D-22 unverified close must not appear closed in History');
pin(doc('d23').querySelector('.opxmon-verdict strong').textContent === 'FREE' && doc('d23').querySelector('.opxmon-details:not([open])'), 'D-23 verdict first with details collapsed');
pin(doc('d24').querySelector('.opxmon-verdict[data-health="red"]') && /limit 50/.test(doc('d24').body.textContent), 'D-24 exceeded limit must be red even with unknown admin');
pin(doc('d25').querySelector('.opxmon-danger:not([open]) .opxmon-close-all') && /close_other_machines/.test(doc('d25').body.textContent), 'D-25 close all must be collapsed and sharing explained');
pin(/100 of 313 jobs shown/.test(doc('d26').body.textContent) && doc('d26').querySelector('.ts-local[data-utc]') && /Show more/.test(doc('d26').body.textContent), 'D-26 bounded count and local created time');
pin(doc('d27').querySelector('.opxmon-tiles .opxmon-tile:not([open]) svg') && /empty slot/.test(doc('d27').body.textContent) && /unchanged for 3 min/.test(doc('d27').body.textContent), 'D-27 compact tiles distinguish empty and frozen sensors');
pin(doc('d28').querySelector('.opxmon-health[title]') && /temperature above limit/.test(doc('d28').body.textContent), 'D-28 top bar contains one worst reason with hover details');
const window = new JSDOM(pages.d25, {url:'http://localhost', runScripts:'outside-only'}).window;
let posts = 0;
window.confirm = () => {throw new Error('Native confirm is forbidden');};
window.fetch = () => {posts++; return Promise.resolve({json:async()=>({ok:false,closed:[],failed:{'QM-a':'still open'},error:'still open after close'})});};
window.eval(fs.readFileSync(path.join(__dirname,'../quam_state_manager/web/static/opx-monitor.js'),'utf8'));
window.document.querySelector('.opxmon-close').click();
pin(posts===0 && /FEMs/.test(window.document.querySelector('.opxmon-confirm').textContent), 'D-25 inline confirmation names resources before any POST');
window.document.querySelector('.opxmon-confirm-accept').click();
setTimeout(()=>{
pin(posts===1 && /Not closed: still open/.test(window.document.querySelector('.opxmon-close-result').textContent), 'D-22 refused close stays visible');
console.log(count+' round-three jsdom pins passed');
window.close();
},20);
