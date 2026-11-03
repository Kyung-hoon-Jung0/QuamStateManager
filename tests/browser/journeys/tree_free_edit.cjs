/* Stage A: real Chrome key events, isolated fixture copies, baseline metrics.
 * SM_PORT, SM_CDP_PORT, SHOT_DIR and LABEL select the rig; BASELINE=1 uses
 * the release's click-to-open editor for an equivalent typing workload.
 */
'use strict';
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const {open, sleep} = require('./cdp.cjs');
const dir = process.env.SHOT_DIR;
const label = process.env.LABEL || 'after-small';
const baseline = process.env.BASELINE === '1';
const tree = '#explorer-tree-state';
const J = JSON.stringify;
const samples = [];
const checks = [];
let p;
async function check(expression, message) {
    const value = await p.ev(expression);
    assert.equal(value, true, message + ': ' + J(value));
    checks.push(message);
    if (!message.startsWith('arrow ') || Number(message.slice(6)) % 50 === 0) console.log('ok - ' + message);
}
async function wait(expression, timeout=30000) {
    const start=Date.now();
    while (Date.now()-start < timeout) {
        const value=await p.ev(expression);
        if (value === true) return;
        await sleep(50);
    }
    throw new Error('Timed out: '+expression);
}
async function click(selector) {
    const pos=await p.ev(`(()=>{const e=document.querySelector(${J(selector)});if(!e)return null;e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})()`);
    assert(pos,'click target '+selector);
    await p.click(...pos);
}
async function key(key, modifiers=0, text) {
    const before=await p.ev('performance.now()');
    const vk={ArrowDown:40,ArrowUp:38,ArrowLeft:37,ArrowRight:39,Enter:13,Escape:27,Tab:9,Backspace:8,End:35,Home:36}[key]
        || key.toUpperCase().charCodeAt(0);
    await p.send('Input.dispatchKeyEvent',{type:'keyDown',key,windowsVirtualKeyCode:text?0:vk,modifiers,...(text?{text,unmodifiedText:text}: {})});
    await p.send('Input.dispatchKeyEvent',{type:'keyUp',key,windowsVirtualKeyCode:text?0:vk,modifiers});
    await p.ev('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
    const record=await p.ev(`({end:performance.now(), tasks:window.__keyTasks.filter(t=>t.start>=${before})})`);
    samples.push({key,modifiers,start:before,elapsed:record.end-before,longTasks:record.tasks});
}
async function text(value) { for (const c of value) await key(c,0,c); }
async function replace(value) { await key('a',2); await key('Backspace'); await text(value); }
function row(path) {return tree+' .tree-node[data-path="'+path+'"] > .tree-row';}
async function reveal(path) {
    await p.ev(`jsonTreeSetExpanded('explorer-tree-state',${J(path.split('.').slice(0,-1).map((_,i)=>path.split('.').slice(0,i+1).join('.')))})`);
    await click(row(path)+' > .tree-val');
    await wait('document.activeElement.matches("'+tree+' input.tree-edit-input")');
}
async function shot(name) {await p.shot(path.join(dir,label+'-'+name+'.png'));}
async function peek(paths) {
    return p.ev(`(async()=>{const paths=${J(paths)}, values={};for(let i=0;i<paths.length;i+=100){
        const response=await fetch('/field/peek?'+paths.slice(i,i+100).map(p=>'dot_path='+encodeURIComponent(p)).join('&'));
        if(!response.ok)throw new Error('Peek failed: '+response.status);
        const data=await response.json();Object.assign(values,data.values);
    }return values})()`);
}
(async()=>{
    fs.mkdirSync(dir,{recursive:true});
    p=await open('http://127.0.0.1:'+(process.env.SM_PORT||5631)+'/explorer');
    await p.send('Page.bringToFront');
    await p.send('Network.enable');
    await p.send('Network.setCacheDisabled',{cacheDisabled:true});
    await p.send('Page.reload',{ignoreCache:true});
    await sleep(500);
    await wait(`!!document.querySelector(${J(tree)})?._treeData`);
    await p.ev("window.__keyTasks=[];window.__taskObserver=new PerformanceObserver(l=>{for(const e of l.getEntries())window.__keyTasks.push({start:e.startTime,duration:e.duration})});window.__taskObserver.observe({type:'longtask',buffered:false})");
    await reveal('qubits.q1.extras.value_000');
    await shot('focused');
    for(let i=0;i<200;i++) {
        if(i%20===0) {await replace('0.125');await key('Enter');await sleep(80);}
        if (baseline) {
            // Release editors close on Enter; arrows act on rows, then click
            // the next value. Every step still sends a real ArrowDown.
            await p.ev(`document.querySelector(${J(row('qubits.q1.extras.value_'+String(i).padStart(3,'0')))}).focus()`);
            await key('ArrowDown');
            await click(row('qubits.q1.extras.value_'+String(i+1).padStart(3,'0'))+' > .tree-val');
        } else await key('ArrowDown');
        if(!baseline) await check(`document.activeElement.getAttribute('aria-label')===${J('qubits.q1.extras.value_'+String(i+1).padStart(3,'0'))}`,'arrow '+(i+1));
    }
    if(!baseline) await check(`document.querySelector(${J(tree)}).querySelectorAll('input').length<=1`,'at most one tree input after 200 moves');
    await click('#explorer-search');await text('amplitude');await sleep(400);
    const hit=await p.ev(`document.querySelector(${J(tree+' .tree-highlight > .tree-row > .tree-val')})?.closest('.tree-node').dataset.path`);
    assert(hit,'amplitude search hit');await click(row(hit)+' > .tree-val');
    await replace('0.21');await key('Enter');await sleep(150);
    await check(`document.querySelector(${J(tree)})._treeData.qubits.q1.resonator.operations.readout.amplitude===0.21`,'search hit edit stores the typed decimal');
    await shot('search-hit');
    if(!baseline) {
        await key('9',0,'9');
        const dirty=await p.ev('document.activeElement.value');
        const before=p.events.filter(e=>e.method==='Network.requestWillBeSent' && /\/undo(?:\?|$)/.test(e.params.request.url)).length;
        await key('z',2);
        await check('document.activeElement.value!=='+J(dirty),'dirty Ctrl Z uses native text undo');
        assert.equal(p.events.filter(e=>e.method==='Network.requestWillBeSent' && /\/undo(?:\?|$)/.test(e.params.request.url)).length,before,'text undo never posts server undo');
        await key('Escape');
    }
    for(let i=0;i<5;i++) await key('z',2);
    await wait('!window.UndoQueue.busy()');await sleep(200);
    await check(`document.querySelector(${J(tree)})._treeData.qubits.q1.resonator.operations.readout.amplitude===0.2`,'five server undos include the search edit');
    for(let i=0;i<5;i++) await key('Z',10);
    await wait('!window.UndoQueue.busy()');await sleep(200);
    await check(`document.querySelector(${J(tree)})._treeData.qubits.q1.resonator.operations.readout.amplitude===0.21`,'five server redos restore the search edit');
    await shot('redo');
    await click('#explorer-search');await key('a',2);await key('Backspace');await sleep(350);
    await reveal('ports.mw_outputs.con1.1.1.full_scale_power_dbm');
    await replace('-1');await key('Enter');
    await wait("!!document.querySelector('.fsp-card') && document.querySelector('.fsp-card').closest('.ch-overlay').style.display !== 'none'");
    await shot('fsp-offer');
    await click('.fsp-card .ch-close');await sleep(150);
    if(!baseline) await check("document.activeElement.getAttribute('aria-label')==='ports.mw_outputs.con1.1.1.full_scale_power_dbm' && document.activeElement.value==='0'",'FSP dismissal reverts with focus');
    await reveal('ports.mw_outputs.con1.1.1.full_scale_power_dbm');
    await replace('-1');await key('Enter');
    await wait("document.querySelector('.fsp-card').closest('.ch-overlay').style.display !== 'none'");
    await click('.fsp-card .btn-sync');
    await wait(`document.querySelector(${J(tree)})._treeData.ports.mw_outputs.con1['1']['1'].full_scale_power_dbm===-1`);
    checks.push('FSP commits only after explicit compensation choice');
    await shot('fsp-applied');
    await reveal('qubits.q1.resonator.operations.weird.amplitude');
    await check("document.activeElement.value.startsWith('\"#/')",'pointer edits its literal');
    const pointerPath='qubits.q1.resonator.operations.weird.amplitude';
    const targetPath='qubits.q1.resonator.operations.readout.amplitude';
    const beforePointer=await peek([pointerPath,targetPath]);
    await replace('"#/qubits/q1/resonator/operations/const/amplitude"');await key('Enter');await sleep(200);
    const afterPointer=await peek([pointerPath,targetPath]);
    assert.equal(afterPointer[pointerPath],'#/qubits/q1/resonator/operations/const/amplitude','server stores pointer text at its own leaf');
    assert.equal(afterPointer[targetPath],beforePointer[targetPath],'server leaves original pointer target untouched');
    checks.push('pointer server check leaves target untouched');
    await shot('pointer');
    if(!baseline) {
        await p.ev("window.__treeCommitOnLeave=false");
        await reveal('qubits.q1.extras.value_200');await replace('0.777');
        await key('ArrowDown');
        await p.ev(`(()=>{const t=document.querySelector(${J(tree)});renderJsonTree(t.id,t._treeData,{defaultDepth:1,crud:true})})()`);
        await p.ev("jsonTreeSetExpanded('explorer-tree-state',['qubits','qubits.q1','qubits.q1.extras'])");
        await check(`document.querySelector(${J(row('qubits.q1.extras.value_200')+' > .tree-val')}).classList.contains('ui-tree-dirty')`,'OFF dirty draft survives real rebuild');
        await click(row('qubits.q1.extras.value_200')+' > .tree-val');
        // A real response preserves the tree's source marker and supplies new
        // data objects. The old hand-made replacement discarded that marker.
        await p.ev(`htmx.ajax('GET','/explorer',{target:'#table-pane',swap:'innerHTML'})`);
        await wait(`!!document.querySelector(${J(tree)})?._treeData`);
        await check("document.activeElement.value==='0.777'",'OFF draft survives full pane replacement');
        await shot('off-dirty');
        const outline=await p.ev(`getComputedStyle(document.activeElement.parentElement).outlineStyle`);
        assert.equal(outline,'dashed','draft has a distinct dashed outline');
        const dialogsBefore=p.events.filter(e=>e.method==='Page.javascriptDialogOpening').length;
        // Trigger a genuine full unload with a user gesture, then cancel it.
        const unloading=p.ev("location.href='about:blank'");
        for(let i=0;i<100 && p.events.filter(e=>e.method==='Page.javascriptDialogOpening').length===dialogsBefore;i++) await sleep(50);
        const dialog=p.events.filter(e=>e.method==='Page.javascriptDialogOpening').at(-1);
        assert.equal(dialog?.params.type,'beforeunload','dirty full unload prompts in Chrome');
        await p.send('Page.handleJavaScriptDialog',{accept:false});await unloading;
        checks.push('dirty full unload prompts in Chrome');
        // The same draft must survive leaving via the sidebar and returning.
        await click('#sidebar a[hx-get="/bulk"]');
        await wait("!document.querySelector('#explorer-tree-state')");
        await click('#sidebar a[hx-get="/explorer"]');
        await wait(`!!document.querySelector(${J(tree)})?._treeData`);
        await reveal('qubits.q1.extras.value_200');
        await check("document.activeElement.value==='0.777'",'OFF draft survives sidebar navigation and return');
        await key('Escape');
        await p.ev("window.__treeCommitOnLeave=true");
        // The fixture's id is read-only, between this key's preceding field
        // and resonator's first editable leaf in insertion order.
        await p.ev("jsonTreeSetExpanded('explorer-tree-state',['qubits','qubits.q2','qubits.q2.resonator'])");
        await p.ev(`document.querySelector(${J(row('qubits.q2'))}).focus()`);
        await key('Tab');
        await check("document.activeElement.getAttribute('aria-label')==='qubits.q2.resonator.opx_output'",'Tab skips the read-only id key');
    }
    if (!baseline) {
        // Real Cmd+Option+F, typed find/replace, then server-verified grouped undo.
        await reveal('qubits.q1.extras.value_002');
        await key('f', 5);
        await wait("!document.querySelector('#tree-find').hidden");
        await click('#tree-find [name="regex"]');
        await click('#explorer-search'); await replace('^0\\.1$');
        await click('#tree-find [name="replace"]'); await text('0.15');
        const findPaths = await p.ev('TreeFind.hits().map(r=>r.path)');
        assert(findPaths.length > 150, 'whole-model search includes lazy rows');
        const original = await peek(findPaths);
        await p.ev("[...document.querySelectorAll('#tree-find button')].find(b=>b.textContent==='Replace All').scrollIntoView()");
        const findButton = async label => {
            const selector=await p.ev(`(()=>{const a=[...document.querySelectorAll('#tree-find button')];return '#tree-find button:nth-of-type('+(a.findIndex(b=>b.textContent===${J(label)})+1)+')'})()`);
            await click(selector);
        };
        await p.ev('TreeFind.preview(true)');
        await wait("TreeFind.ready()");
        await p.ev("document.querySelector('.tree-toolbar').scrollIntoView({block:'center'})");
        await shot('find-preview');
        await p.ev("document.documentElement.setAttribute('data-theme','light')");
        await shot('find-preview-light');
        await p.ev("document.documentElement.setAttribute('data-theme','dark')");
        await click('#tree-find [name="replace"]'); await key('Enter',5);
        await wait("document.querySelector('#tree-find output').textContent.startsWith('Replaced ')");
        const replaced = await peek(findPaths);
        assert(findPaths.every(p=>replaced[p]===0.15), 'replace-all stores real values on server');
        await findButton('Close');
        await reveal('qubits.q1.extras.value_002'); await key('z',2);
        await wait('!window.UndoQueue.busy()');
        assert.deepEqual(await peek(findPaths), original, 'one Ctrl+Z restores the whole replacement group on server');
        await wait(`document.querySelector(${J(tree)})?._treeData?.qubits?.q1?.extras?.value_002===${J(original['qubits.q1.extras.value_002'])}`);
        checks.push('find replace-all and one Ctrl+Z verified through server peek');
        await reveal('qubits.q1.extras.value_002');
        await key('d',4); await key('d',4);
        await check('TreeMulti.state().sel.length===2','Cmd+D adds next equal occurrence from input');
        const selected = await p.ev('TreeMulti.state().sel.slice()');
        const beforeOccurrence = await peek(selected);
        await text('0.19'); await key('Enter');
        await wait('!TreeMulti.state()');
        const afterOccurrence = await peek(selected);
        assert(selected.every(p=>afterOccurrence[p]===0.19),'Cmd+D typing commits every selected leaf');
        await reveal('qubits.q1.extras.value_002'); await key('z',2);
        await wait('!window.UndoQueue.busy()');
        assert.deepEqual(await peek(selected),beforeOccurrence,'one undo restores occurrence batch on server');
        checks.push('Cmd+D batch and grouped undo verified through server peek');
        await shot('find-occurrence');
    }
    const allLongTasks=await p.ev('window.__keyTasks');
    for(let i=0;i<samples.length;i++) samples[i].longTasks=allLongTasks.filter(t=>t.start>=samples[i].start && (i===samples.length-1 || t.start<samples[i+1].start));
    const summary={label,baseline,checks:checks.length,keys:samples.length,allLongTasks,
        longTasks:samples.reduce((n,s)=>n+s.longTasks.length,0),
        longTaskMs:samples.reduce((n,s)=>n+s.longTasks.reduce((a,t)=>a+t.duration,0),0),
        elapsedMedian:samples.map(s=>s.elapsed).sort((a,b)=>a-b)[Math.floor(samples.length/2)],samples,
        dom:await p.ev(`({rows:document.querySelector(${J(tree)}).querySelectorAll('.tree-row').length,nodes:document.querySelector(${J(tree)}).querySelectorAll('*').length})`),
        errors:p.errors()};
    assert.deepEqual(summary.errors.filter(e=>!/409 \(CONFLICT\)/.test(e)),[], 'no unexpected browser errors');
    fs.writeFileSync(path.join(dir,label+'-metrics.json'),J(summary));
    console.log(J({label,keys:summary.keys,longTasks:summary.longTasks,errors:summary.errors}));
    await p.close();process.exit(0);
})().catch(async e=>{console.error(e);if(p){await shot('failure');await p.close();}process.exit(1);});
