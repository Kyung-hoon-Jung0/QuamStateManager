/* D-1..D-20: execute the shipped tree, never a duplicate editor. */
'use strict';
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const staticDir = path.join(__dirname, '../quam_state_manager/web/static');
let source = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const mutations = {
    search: ['_treeResume(c, savedEditor);', '/* mutant: lost editor */'],
    off: ['if (!preserve && window.__treeCommitOnLeave !== false) flush();', 'if (!preserve) flush();'],
    render: ['_treeResume(container, savedEditor);', '/* mutant: lost drafts */'],
    pane: ['var editMemory = _treeEditMemory.get(_treeEditKey(container));', 'var editMemory = null;'],
    fsp: ['if (back) _makeValueEditable(back, dotPath);', '/* mutant: no focus return */'],
    one: ['if (tree._editor) tree._editor.leave();', '/* mutant: leave old input */'],
    escape: ['input.value = input.defaultValue;\n                dirty();', '/* mutant: retain typing */'],
    caret: ['input.setSelectionRange(input.value.length, input.value.length);', 'input.select();'],
    flight: ['if (input.value === sent) input.value = input.defaultValue;', 'input.value = input.defaultValue;'],
    visible: ["var v = n.querySelector(':scope > .tree-row > .tree-val');", "var ch = n.querySelector(':scope > .tree-children'); if (ch && ch.style.display === \"none\") { _toggleNode(n); if (dir < 0) n = _kbDeepLast(n); } var v = n.querySelector(':scope > .tree-row > .tree-val');"],
    tab: ['!editableOnly || v.classList.contains("ui-tree-value")', 'true'],
    undo: ['&& !inInline && !inTreeEditor) return;', '&& !inInline) return;'],
    text_undo: ['if (inTreeEditor && a.value !== a.defaultValue) return;', '/* mutant: let server undo dirty text */'],
    global: ['if (a && a.matches && a.matches(".ui-tree-editor")) return true;', 'if (a && a.matches && a.matches(".ui-tree-editor")) return window.TreeInlineEdit.busy(a);'],
    fsp_cancel: ['if (mode === "cancel") { resolve(); return; }', 'if (mode === "cancel") { return; }'],
    refusal: ['if (result === false) { dirty(); return false; }', '/* mutant: discard refused typing */'],
    peek_retry: ['cache.delete(path); return {};', 'return {};'],
    tooltip: ['chip.title = (expected.class_path', 'chip.unusedTitle = (expected.class_path'],
    pointer: ['if (resolved && resolved.is_pointer)', 'if (false)'],
    brackets: ["if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable)) return;", "if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable) && !(a.matches('.ui-tree-editor') && window.TreeInlineEdit && !window.TreeInlineEdit.busy(a))) return;"],
    multi: ["var nd = a && a.closest && a.closest('.tree-node');", "var nd = a && a.tagName !== 'INPUT' && a.closest && a.closest('.tree-node');"],
    cache: ['if (!cache.has(path)) cache.set', 'if (true) cache.set'],
};
if (process.env.TREE_MUTATION) {
    const [before, after] = mutations[process.env.TREE_MUTATION];
    assert(source.includes(before), 'mutation target exists');
    source = source.replaceAll(before, after);
}
const dom = new JSDOM('<!doctype html><input id="search"><div id="tree"></div><button id="after">After</button>',
    {runScripts:'outside-only', pretendToBeVisual:true, url:'http://localhost/explorer'});
const w = dom.window, d = w.document;
const calls = [];
let delay = 0;
let fsp = false;
let refused = false;
let peekFailure = null;
w.fetch = (url, opts) => {
    calls.push({url:String(url), body: opts && opts.body});
    let data = {ok:true, expected:{}};
    if (String(url).startsWith('/field/peek')) {
        const p = new w.URL(url, w.location.href).searchParams.get('dot_path');
        if (peekFailure === 'network') return Promise.reject(new Error('offline'));
        if (peekFailure === 'http') return Promise.resolve({ok:false,json:async()=>({ok:true})});
        if (peekFailure === 'body') return Promise.resolve({ok:true,json:async()=>({ok:false})});
        data.expected[p] = {type:'real',source:'schema',class_path:'model.Settings',field:p,detail:'Expected scalar'};
        if (p === 'pointer') data.resolved = {[p]:{is_pointer:true,resolvable:true,resolved_value:42}};
    }
    if (url === '/field/edit') {
        const b = new w.URLSearchParams(opts.body);
        let v = b.get('value');
        try { v = JSON.parse(v); } catch (_) {}
        data = refused ? {ok:false,error:"Refused value"} : fsp && !b.get('fsp_ack') ? {ok:false,fsp_compensation:{port:'test',fsp_old:1,fsp_new:2,factor:1,amps:[]}} : {ok:true, stored:v, stored_kind:typeof v};
    }
    return new Promise(resolve => setTimeout(() => resolve({ok:true, json:async()=>data}), delay));
};
const ajax = [];
w.htmx = {ajax:async(method,url)=>{ajax.push(url);}, trigger(){}, process(){}};
w.eval(fs.readFileSync(path.join(staticDir,'search-query.js'),'utf8'));
w.eval(source);
w.eval(fs.readFileSync(path.join(staticDir,'manual.js'),'utf8'));
const manuals=[]; w.openConfigManual=o=>manuals.push(o.path);
const tick = (ms=20) => new Promise(r=>setTimeout(r,ms));
let tree = d.getElementById('tree');
const node = p => tree.querySelector('.tree-node[data-path="'+p+'"]');
const val = p => node(p).querySelector(':scope > .tree-row > .tree-val');
const focus = p => node(p).querySelector(':scope > .tree-row').focus();
const key = (key,opts={}) => d.activeElement.dispatchEvent(new w.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,...opts}));
const type = text => {d.activeElement.value=text; d.activeElement.dispatchEvent(new w.Event('input',{bubbles:true}));};
(async()=>{
    w.renderJsonTree('tree',{a:1,b:'hello',branch:{inner:{amplitude:0.1}}},{defaultDepth:1,crud:true});
    focus('a');
    assert.equal(tree.getAttribute('role'),'tree','accessible tree role');
    assert.equal(d.activeElement.tagName,'INPUT','focus mounts immediately');
    assert.equal(d.activeElement.selectionStart,d.activeElement.value.length,'caret at end');
    focus('b');
    assert.equal(tree.querySelectorAll('input').length,1,'one input only');
    assert.equal(d.activeElement.value,'"hello"','string literal');
    assert.equal(d.activeElement.selectionStart,d.activeElement.value.length,'no select all');
    type('"changed"'); key('Escape');
    assert.equal(d.activeElement.value,'"hello"','Esc reverts and stays');
    focus('a'); type('2'); key('Enter'); await tick();
    assert.equal(d.activeElement.value,'2','Enter commits and stays');
    assert.equal(tree._treeData.a,2,'server echo updates model');
    delay=40; type('3'); key('Enter'); type('34'); await tick(70);
    assert.equal(d.activeElement.value,'34','in-flight typing survives');
    key('Escape'); delay=0;
    const count=()=>calls.filter(c=>c.url.startsWith('/field/peek?dot_path=b')).length;
    focus('b'); await tick(190); const before=count(); focus('a'); focus('b'); await tick(190);
    assert.equal(count(),before,'peek cached across visits');

    const collapsed = {top:1};
    for (let i=0;i<5;i++) collapsed['group'+i]=Object.fromEntries(Array.from({length:20},(_,j)=>['value'+j,j]));
    collapsed.last=2;
    w.renderJsonTree('tree',collapsed,{defaultDepth:0,crud:true});
    focus('top');
    let presses=0;
    while (d.activeElement.getAttribute('aria-label')!=='last' && presses<6) {key('ArrowDown');presses++;}
    assert.equal(d.activeElement.getAttribute('aria-label'),'last','Down reaches last within six visible-row presses');
    for (let i=0;i<5;i++) {
        assert.equal(node('group'+i).querySelector(':scope > .tree-children').style.display,'none','groups stay collapsed');
        assert(node('group'+i)._lazyData,'groups remain lazy');
    }
    key('ArrowUp');assert.equal(d.activeElement.getAttribute('aria-label'),'top','Up skips collapsed branches too');
    w.renderJsonTree('tree',{branch:{inner:{amplitude:0.1}}},{defaultDepth:99,crud:true});
    focus('branch.inner.amplitude');
    key('F1'); assert.deepEqual(manuals,['branch.inner.amplitude'],'F1 from value');
    w._treeReadOnly={skip_leaves:['locked']};
    w.renderJsonTree('tree',{a:1,locked:2,b:3},{defaultDepth:1,crud:true});
    focus('a'); key('Tab');
    assert.equal(d.activeElement.getAttribute('aria-label'),'b','Tab skips read-only');
    key('Tab',{shiftKey:true});
    assert.equal(d.activeElement.getAttribute('aria-label'),'a','Shift Tab skips read-only');
    const tray=d.createElement('div'); tray.id='pending-tray'; tray.dataset.changeCount='1';d.body.appendChild(tray);
    type('9');
    const event=new w.KeyboardEvent('keydown',{key:'z',ctrlKey:true,bubbles:true,cancelable:true});
    d.activeElement.dispatchEvent(event); await tick();
    assert.equal(event.defaultPrevented,false,'dirty Ctrl Z belongs to native text history');
    assert.equal(ajax.filter(u=>u==='/undo').length,0,'dirty Ctrl Z never reaches server');
    key('Escape'); key('z',{ctrlKey:true}); await tick();
    assert(ajax.some(u=>u==='/undo'),'clean Ctrl Z reaches server');
    const pane=d.createElement('div');pane.id='table-pane';d.body.appendChild(pane);
    const search=d.getElementById('search'); search.type='search';pane.appendChild(search);
    const detail=d.createElement('div');detail.id='ds-detail-root';d.body.appendChild(detail);
    const runMoves=[];w.dsNavRun=step=>runMoves.push(step);
    focus('a');
    for (const dirtyText of [false,true]) {
        if (dirtyText) type('19');
        for (const ch of ['/', '?', 'x', '[', ']']) {
            const input=d.activeElement;
            assert.equal(key(ch),true,'printable key is not prevented');
            assert.equal(d.activeElement,input,'printable key keeps value focus, clean or dirty');
            assert(!d.querySelector('#kb-cheatsheet'),'printable key never opens shortcut sheet');
        }
    }
    assert.equal(runMoves.length,0,'brackets never navigate while a value has focus');
    detail.remove();
    key('Escape');
    refused=true;type('invalid text');const refusedInput=d.activeElement;key('Enter');await tick();
    assert.equal(d.activeElement,refusedInput,'refusal keeps focus');
    assert.equal(refusedInput.value,'invalid text','refusal keeps typed text');
    assert(val('a').classList.contains('ui-tree-dirty'),'refusal stays amber');
    assert.equal(tree._treeData.a,1,'refusal leaves stored value');
    refused=false;key('Escape');
    for (const failure of ['network','http','body']) {
        const retry='retry_'+failure;
        w.renderJsonTree('tree',{[retry]:1,other:2},{defaultDepth:1,crud:true});
        peekFailure=failure;focus(retry);await tick(190);
        const countRetry=()=>calls.filter(c=>c.url.includes('dot_path='+retry)).length;
        const beforeRetry=countRetry();
        peekFailure=null;focus('other');focus(retry);await tick(190);
        assert.equal(countRetry(),beforeRetry+1,'failed peek retries on next focus: '+failure);
        assert.equal(val(retry).querySelector('.tree-type-chip').title,'model.Settings.'+retry+' \u2014 Expected scalar','type tooltip includes field and detail');
    }
    w.renderJsonTree('tree',{pointer:'#/target',target:42},{defaultDepth:1,crud:true});
    focus('pointer');await tick(190);
    const preview=val('pointer').querySelector('.ui-tree-resolved');
    assert(preview,'pointer has resolved preview');
    assert.equal(d.activeElement.value,'"#/target"','pointer input stays literal');
    assert.equal(preview.textContent.trim(),'\u2192 42','pointer preview shows resolved value');
    assert(d.activeElement.compareDocumentPosition(preview)&4,'preview follows input');
    const css=fs.readFileSync(path.join(staticDir,'ui-kit.css'),'utf8');
    assert(/\.ui-tree-resolved\s*\{[^}]*color: var\(--ui-text-2\)/.test(css),'pointer preview is muted');
    w.renderJsonTree('tree',{items:{first:{amplitude:1},second:{amplitude:2}}},{defaultDepth:99,crud:true});
    focus('items.first.amplitude'); key('d',{ctrlKey:true});
    assert(w.TreeMulti.state(),'Ctrl D creates selection');
    assert.equal(w.TreeMulti.state().sel[0],'items.first.amplitude','Ctrl D anchors focused value');
    key('Escape'); key('Escape');
    assert(!tree.contains(d.activeElement),'Esc twice exits tree');

    w.renderJsonTree('tree',{a:'line\ntext',b:2},{defaultDepth:1,crud:true});
    focus('a'); assert.equal(d.activeElement.value,JSON.stringify('line\ntext'),'newlines use JSON literal');
    type('"uncommitted"');d.activeElement.setSelectionRange(3,6);
    w.jsonTreeSearch('tree','a');await tick(260);
    assert.equal(d.activeElement.getAttribute('aria-label'),'a','search restores dot path');
    assert.equal(d.activeElement.value,'"uncommitted"','search keeps typed text');
    assert.equal(d.activeElement.selectionStart,3,'search keeps caret start');
    assert.equal(d.activeElement.selectionEnd,6,'search keeps selection end');
    key('Escape');
    w.__treeCommitOnLeave=false;
    type('"OFF draft"');focus('b');await tick(140);
    assert.equal(tree._treeData.a,'line\ntext','OFF leaves uncommitted');
    assert(val('a').classList.contains('ui-tree-dirty'),'OFF draft is amber');
    w.renderJsonTree('tree',tree._treeData,{defaultDepth:1,crud:true});
    assert.equal(val('a').textContent,'"OFF draft"','OFF draft survives re-render away from focus');
    assert(val('a').classList.contains('ui-tree-dirty'),'OFF draft stays amber after render');
    focus('a');assert.equal(d.activeElement.value,'"OFF draft"','OFF draft returns on focus');
    d.activeElement.setSelectionRange(2,5);
    const rebuildData=tree._treeData;
    w.TreeInlineEdit.settle(d.body);
    const replacement=d.createElement('div');replacement.id='tree';tree.replaceWith(replacement);tree=replacement;
    w.renderJsonTree('tree',rebuildData,{defaultDepth:1,crud:true});
    assert.equal(d.activeElement.value,'"OFF draft"','OFF draft survives a full pane replacement');
    assert.equal(d.activeElement.selectionStart,2,'pane replacement restores caret');
    assert.equal(d.activeElement.selectionEnd,5,'pane replacement restores selection');
    key('Escape'); assert.equal(d.activeElement.value,JSON.stringify('line\ntext'),'OFF Escape reverts');
    w.__treeCommitOnLeave=true;
    fsp=true;focus('b');type('3');key('Enter');await tick();
    const popup=d.querySelector('.fsp-card');
    assert(popup,'FSP commit opens existing offer');
    const close=popup.querySelector('.ch-close');close.focus();
    await tick(130);
    w.renderJsonTree("tree",tree._treeData,{defaultDepth:1,crud:true});
    close.click();await tick();
    assert.equal(d.activeElement.getAttribute('aria-label'),'b','FSP close restores field focus');
    assert.equal(d.activeElement.value,'2','FSP close without choice reverts');
    assert.equal(tree._treeData.b,2,'FSP never writes before a choice');
    // Use the same roving state: a pending Cancel must settle before Enter can write again.
    type('3');key('Enter');await tick();
    d.querySelector('.fsp-card .ch-close').click();await tick();
    fsp=false;type('4');key('Enter');await tick(80);
    assert.equal(tree._treeData.b,4,'FSP Cancel resolves so a later edit on the same field commits');
    assert.equal(d.activeElement.value,'4','later edit remains in the roving input');
    console.log('ALL OK: Stage A pins');
    w.close();
})().catch(e=>{console.error(e);process.exitCode=1;w.close();});
