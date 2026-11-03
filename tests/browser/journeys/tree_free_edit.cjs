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
    assert(hit,'amplitude search hit');
    if (!baseline) {
        await check("document.querySelector('#sm-typeahead').classList.contains('sm-typeahead-tree')",'tree typeahead participates in layout');
        await check(`(()=>{const a=document.querySelector('#sm-typeahead').getBoundingClientRect(),b=document.querySelector(${J(tree)}).getBoundingClientRect();return a.bottom<=b.top+1})()`,'suggestions never cover tree rows');
        const pos=await p.ev(`(()=>{const e=document.querySelector(${J(row(hit)+' > .tree-val')});e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})()`);
        await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:pos[0],y:pos[1],button:'left',clickCount:1});
        await check("document.querySelector('#sm-typeahead').hidden",'outside pointerdown dismisses typeahead');
        await p.ev("jsonTreeSearch('explorer-tree-state','amplitude')");await sleep(250);
        await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:pos[0],y:pos[1],button:'left',clickCount:1});
        await check(`document.activeElement.getAttribute('aria-label')===${J(hit)}`,'one press remains typeable across pending search');
    } else await click(row(hit)+' > .tree-val');
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
    await sleep(200);
    if (!baseline) await check(`document.activeElement.parentElement.querySelector('.ui-tree-target').textContent.includes('edits '+${J(targetPath)})`,'pointer editor names shared target');
    const beforePointer=await peek([pointerPath,targetPath]);
    await replace('"#/qubits/q1/resonator/operations/const/amplitude"');await key('Enter');await sleep(200);
    const afterPointer=await peek([pointerPath,targetPath]);
    assert.equal(afterPointer[pointerPath],'#/qubits/q1/resonator/operations/const/amplitude','server stores pointer text at its own leaf');
    assert.equal(afterPointer[targetPath],beforePointer[targetPath],'server leaves original pointer target untouched');
    checks.push('pointer server check leaves target untouched');
    if (!baseline) await check(`(()=>{const e=document.activeElement.parentElement,h=e.querySelector('.ui-tree-target'),r=e.closest('.tree-row').querySelector('.tree-row-actions');return h.getBoundingClientRect().top>=document.activeElement.getBoundingClientRect().bottom && (!r || getComputedStyle(r).display==='none')})()`,'pointer target hint has its own unobstructed line');
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
        await check(`document.querySelector(${J(tree+' > .ui-tree-drafts')}).textContent==='1 draft'`,'draft header counts current draft');
        await p.ev("jsonTreeCollapseAll('explorer-tree-state')");
        await click(tree+' > .ui-tree-drafts');
        await check("document.activeElement.value==='0.777'",'draft header jumps into collapsed branch');
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
        await reveal('qubits.q1.extras.value_001');
        await replace('1234567890123');await key('Enter');await sleep(150);
        await check("document.activeElement.value==='1234567890123'",'long number input stays ungrouped after commit');
        await sleep(200);
        await check("document.activeElement.clientWidth>=document.activeElement.scrollWidth",'long number fits beside type chip');
        await p.ev(`document.querySelector(${J(row('qubits.q1.extras.value_001'))}).scrollIntoView({block:'center'})`);await sleep(100);
        await p.ev("document.documentElement.setAttribute('data-theme','light')");await shot('long-light');
        await p.ev("document.documentElement.setAttribute('data-theme','dark')");await shot('long-dark');
    }
    if (!baseline) {
        // Auto-save: leaving the tab or the page commits the open editor.
        await p.ev("window.__treeCommitOnLeave=true");
        const stored=async v=>(await peek(['qubits.q1.extras.value_010']))['qubits.q1.extras.value_010']===v;
        await reveal('qubits.q1.extras.value_010');
        await replace('0.611');
        const other=await open('about:blank');
        await other.send('Page.bringToFront');           // this tab becomes hidden
        for(let i=0;i<100 && !(await stored(0.611));i++) await sleep(50);
        assert(await stored(0.611),'switching to another tab commits the typed value');
        checks.push('tab hidden commits');console.log('ok - tab hidden commits');
        await p.send('Page.bringToFront');await sleep(200);
        await reveal('qubits.q1.extras.value_010');
        await replace('0.622');
        await p.send('Emulation.setFocusEmulationEnabled',{enabled:false});
        await p.ev("window.dispatchEvent(new FocusEvent('blur'))");
        for(let i=0;i<100 && !(await stored(0.622));i++) await sleep(50);
        assert(await stored(0.622),'window blur commits the typed value');
        checks.push('window blur commits');console.log('ok - window blur commits');
        await other.close();
        await reveal('qubits.q1.extras.value_010');
        await replace('0.633');
        await click('#sidebar a[hx-get="/bulk"]');
        for(let i=0;i<100 && !(await stored(0.633));i++) await sleep(50);
        assert(await stored(0.633),'navigating away commits the typed value');
        checks.push('navigation commits');console.log('ok - navigation commits');
        await click('#sidebar a[hx-get="/explorer"]');
        await wait(`!!document.querySelector(${J(tree)})?._treeData`);
    }
    if (!baseline) {
        // D2: two windows on one chip. B renders its tray, A commits another
        // edit, B presses Apply: the live files must not receive A's edit.
        const base='http://127.0.0.1:'+(process.env.SM_PORT||5631);
        const b=await open(base+'/explorer');
        await sleep(500);
        for (let i=0;i<200 && !(await b.ev("!!document.getElementById('pending-tray')"));i++) await sleep(50);
        await b.ev("window.__asked=[];window.confirm=m=>{window.__asked.push(m);return false};");
        const seenB=await b.ev("document.getElementById('pending-tray').getAttribute('data-change-count')");
        const marker=0.4242421;
        const wrote=await p.ev(`fetch('/field/edit',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'dot_path=qubits.q1.extras.value_007&value=${marker}'}).then(r=>r.status)`);
        assert.equal(wrote,200,'window A commits an edit');
        const reply=await b.ev(`new Promise(res=>{const f=window.fetch;window.fetch=function(u,o){const pr=f.apply(this,arguments);if(String(u).indexOf('/state/sync')===0||String(u).indexOf('/state/apply-to-live')===0){window.__d2body=o&&o.body;pr.then(r=>r.clone().text().then(t=>res({status:r.status,body:String(o&&o.body),text:t.slice(0,300)})));}return pr;};window.doStateSync('apply');setTimeout(()=>res(null),15000);})`);
        assert(reply,'window B posted its apply');
        assert(new RegExp('seen_changes='+seenB+'(&|$)').test(reply.body),'B declares the count it rendered ('+seenB+'): '+reply.body);
        assert.equal(reply.status,409,'B apply is refused: '+reply.text);
        assert(/unseen_changes/.test(reply.text) && /value_007/.test(reply.text),'refusal names A edit: '+reply.text);
        if (process.env.SM_RIG) {
            const live=fs.readFileSync(path.join(process.env.SM_RIG,'copy','state.json'),'utf8');
            assert(!live.includes(String(marker)),'live state.json on disk does not hold A edit');
        }
        checks.push('D2 second window apply refused, live untouched');
        console.log('ok - D2 second window apply refused, live untouched');
        await b.close();
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
