/* Newcomer round, phase 2: every string on the Agent tab + the safe controls.
 * argv[2]=out json  argv[3]=CDP  argv[4]=base  argv[5]=shot dir
 */
const fs = require('fs');
const { connect } = require('C:/Users/KyunghoonJung/AppData/Local/Temp/claude/D--work-statemanager/dd0fa2c3-e492-405d-8783-2c62cd30ba4a/scratchpad/cdp_lib.cjs');
const OUT = process.argv[2], CDP = process.argv[3], BASE = process.argv[4], DIR = process.argv[5];
const out = { steps: [] };
function rec(k, v) { out.steps.push({ k, v }); }

async function main() {
  const c = await connect(CDP);
  await c.send('Page.navigate', { url: BASE + '/' });
  await c.sleep(3200);

  // Every title=/aria-label= tooltip in the agent region — what a hover would say
  out.tooltips = await c.ev(`(function(){
    var r=document.querySelector('.ag-root')||document.body; var o=[];
    r.querySelectorAll('[title],[aria-label]').forEach(function(e){
      o.push({tag:e.tagName.toLowerCase(),
              txt:(e.innerText||'').trim().replace(/\s+/g,' ').slice(0,40),
              title:e.getAttribute('title'), aria:e.getAttribute('aria-label')});
    }); return o;})()`);
  out.wireTooltips = await c.ev(`(function(){
    var r=document.querySelector('.ag-wire'); if(!r) return null; var o=[];
    r.querySelectorAll('[title],[aria-label]').forEach(function(e){
      o.push({txt:(e.innerText||'').trim().slice(0,40), title:e.getAttribute('title'), aria:e.getAttribute('aria-label')});});
    return {text:r.innerText.replace(/\s+/g,' '), items:o};})()`);

  // The "?" in the wire strip
  const qbtn = await c.ev(`(function(){var b=document.querySelector('.ag-wire-help'); if(!b) return null; var r=b.getBoundingClientRect(); return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};})()`);
  rec('wire ? button box', qbtn);
  if (qbtn) {
    await c.click(qbtn.x, qbtn.y);
    await c.sleep(700);
    await c.shot(DIR + '/n02_wire_help.png');
    rec('after clicking ?', await c.ev(`(function(){
      var pops=[].slice.call(document.querySelectorAll('.ag-wire-pop,.sm-pop,[role=dialog],.popover'));
      return pops.filter(function(p){var r=p.getBoundingClientRect(); return r.width>0&&r.height>0;})
        .map(function(p){return {cls:p.className, text:p.innerText.replace(/\s+/g,' ').slice(0,900)};});})()`));
    await c.press('Escape'); await c.sleep(300);
  }

  // the observer checkbox: what does it say, what does toggling change
  const obs = await c.ev(`(function(){
    var i=[].slice.call(document.querySelectorAll('input[type=checkbox]')).filter(function(e){
      var l=e.closest('label'); return (l&&/observer/i.test(l.innerText))||/observer/i.test((e.parentElement||{}).innerText||'');});
    if(!i.length) return null; var e=i[0]; var r=e.getBoundingClientRect();
    var lab=e.closest('label')||e.parentElement;
    return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),checked:e.checked,
            title:e.getAttribute('title'),labTitle:lab?lab.getAttribute('title'):null,
            labText:lab?lab.innerText.replace(/\s+/g,' '):null};})()`);
  rec('observer checkbox', obs);
  if (obs) {
    const before = await c.ev(`document.querySelector('.ag-root').innerText.replace(/\s+/g,' ')`);
    await c.click(obs.x, obs.y); await c.sleep(1200);
    const after = await c.ev(`document.querySelector('.ag-root').innerText.replace(/\s+/g,' ')`);
    await c.shot(DIR + '/n03_observer_on.png');
    rec('observer toggle changed the panel?', { changed: before !== after, before: before.slice(0,400), after: after.slice(0,400) });
    // any toast?
    rec('toast after observer', await c.ev(`(function(){var t=document.querySelectorAll('.toast,.sm-toast,#toast'); return [].map.call(t,function(x){return x.innerText.trim().slice(0,200);}).filter(Boolean);})()`));
    await c.click(obs.x, obs.y); await c.sleep(900);
    rec('observer back off', await c.ev(`(function(){var i=[].slice.call(document.querySelectorAll('input[type=checkbox]')).filter(function(e){var l=e.closest('label'); return (l&&/observer/i.test(l.innerText));}); return i.length?i[0].checked:null;})()`));
  }

  // the composer: placeholder, hints, the model select, the "your name" box
  out.composer = await c.ev(`(function(){
    var ta=document.querySelector('.ag-input');
    var sel=document.querySelector('.ag-backend');
    var box=ta?ta.closest('form')||ta.parentElement.parentElement:null;
    return {placeholder:ta?ta.placeholder:null,
            selOpts:sel?[].map.call(sel.options,function(o){return o.text;}):null,
            selTitle:sel?sel.getAttribute('title'):null,
            footText:box?box.innerText.replace(/\s+/g,' ').slice(0,500):null,
            inputs:[].map.call(document.querySelectorAll('.ag-root input[type=text],.ag-root input:not([type])'),function(i){
              return {ph:i.placeholder,title:i.getAttribute('title'),val:i.value};})};})()`);

  // empty main pane: is there ANY guidance for a person who has never used it
  out.emptyPane = await c.ev(`(function(){
    var f=document.querySelector('.ag-feed,.ag-stream,.ag-body,.ag-main');
    if(!f){ // find the biggest empty block inside ag-root
      var best=null; document.querySelectorAll('.ag-root *').forEach(function(e){var r=e.getBoundingClientRect();
        if(r.height>300 && (!e.innerText||e.innerText.trim().length<40)){ if(!best||r.height>best.h) best={cls:e.className,h:Math.round(r.height),txt:(e.innerText||'').trim()};}});
      return {found:'none', biggestEmpty:best};}
    return {cls:f.className, h:Math.round(f.getBoundingClientRect().height), text:f.innerText.trim().slice(0,600)};})()`);

  out.errors = c.errors;
  fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
  process.exit(0);
}
main().catch(e => { out.fatal = String(e && e.stack || e); fs.writeFileSync(OUT, JSON.stringify(out, null, 1)); process.exit(1); });
