const cdp = require('../../tests/browser/journeys/cdp.cjs');
const fs = require('fs');
const out = require('../../tests/browser/evidence-output.cjs').directory('review5_merge');
fs.mkdirSync(out, {recursive:true});
const records=[];
(async()=>{
for(const theme of ['light','dark']) for(const width of [1440,1024]) {
 for(const tab of ['live','jobs','history','cleanup','settings']) {
  const pair=[];
  for(const legacy of [true,false]) {
   const s=await cdp.open(`http://127.0.0.1:5745/opx-monitor/${tab}?theme=${theme}${legacy?'&legacy=1':''}`,width,900);
   await s.sleep(350);
   const name=`${legacy?'before':'after'}_${tab}_${theme}_${width}`;
   await s.shot(`${out}/${name}.png`);
   const geometry=await s.ev(`JSON.stringify(Array.from(document.querySelectorAll('.ui-opxmon-page,.ui-opxmon-verdict,.ui-opxmon-status,.ui-opxmon-tile,.ui-opxmon-settings input')).map(e=>{const r=e.getBoundingClientRect();const c=getComputedStyle(e);return {tag:e.tagName,x:r.x,y:r.y,w:r.width,h:r.height,font:c.fontSize,color:c.color,border:c.borderColor}}))`);
   pair.push({name,geometry:JSON.parse(geometry),errors:s.errors(),overflow:await s.ev('document.body.scrollWidth>innerWidth')});
   if(!legacy && tab==='live') {
    const box=await s.ev("document.querySelector('.ui-opxmon-tile summary').getBoundingClientRect().toJSON()");
    await s.click(box.x+box.width/2,box.y+box.height/2); await s.sleep(100);
    if(!await s.ev("document.querySelector('.ui-opxmon-tile').classList.contains('ui-opxmon-tile--open')")) throw Error('Expanded modifier missing');
    await s.shot(`${out}/expanded_live_${theme}_${width}.png`);
   }
   if(!legacy && tab==='settings') {
    await s.ev("document.querySelectorAll('*').forEach(e=>{if(e.clientHeight>200&&e.scrollHeight>e.clientHeight+50&&/(auto|scroll)/.test(getComputedStyle(e).overflowY))e.scrollTop=e.scrollHeight})");
    await s.shot(`${out}/after_settings_${theme}_${width}_p2.png`);
   }
   if(!legacy && tab==='cleanup') {
    const box=await s.ev("document.querySelector('.ui-opxmon-close').getBoundingClientRect().toJSON()");
    await s.click(box.x+box.width/2,box.y+box.height/2); await s.sleep(100);
    await s.shot(`${out}/after_cleanup_${theme}_${width}_confirm.png`);
    const cancel=await s.ev("document.querySelector('.ui-opxmon-confirm-cancel').getBoundingClientRect().toJSON()");
    await s.click(cancel.x+cancel.width/2,cancel.y+cancel.height/2);
   }
   await s.close();
  }
  records.push({theme,width,tab,pair,equal:JSON.stringify(pair[0].geometry)===JSON.stringify(pair[1].geometry)});
 }
}
fs.writeFileSync(`${out}/comparison.json`,JSON.stringify(records,null,2));
console.log(JSON.stringify(records.map(({theme,width,tab,equal,pair})=>({theme,width,tab,equal,errors:pair.flatMap(p=>p.errors),overflow:pair.some(p=>p.overflow)})),null,2));
process.exit(0);
})().catch(e=>{console.error(e);process.exit(1)});
