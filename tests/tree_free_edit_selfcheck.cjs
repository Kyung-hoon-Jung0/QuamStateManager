/* D-1..D-20: execute the shipped tree, never a duplicate editor. */
'use strict';
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const staticDir = path.join(__dirname, '../quam_state_manager/web/static');
let source = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const mutations = {
    auto_blur: ["window.addEventListener('blur', _treeAutoCommitAll);", '/* reverted window blur commit */'],
    auto_hidden: ["if (document.visibilityState === 'hidden') _treeAutoCommitAll();", '/* reverted hidden commit */'],
    auto_pagehide: ["window.addEventListener('pagehide', _treeAutoCommitAll);", '/* reverted pagehide commit */'],
    auto_nav: ["if (target && target.id === 'table-pane') window.TreeInlineEdit.settle(target);", "if (target && target.id === 'table-pane' && window.__treeCommitOnLeave === false) window.TreeInlineEdit.settle(target);"],
    press_mount: ['el.addEventListener("pointerdown", function(e)', 'el.addEventListener("unused-pointerdown", function(e)'],
    pointer_cached: ['_treePointerPreview(el, el._treeResolved);', '/* reverted immediate pointer hint */'],
    pointer_hint: ["hint.textContent = ' edits '", "hint.textContent = ' target '"],
    pointer_toast: ["window.showToast('Edited ' + data.resolved_path, 'success')", "window.showToast('Edited value', 'success')"],
    pointer_flash: ["target.classList.add('leu-flash');", "/* reverted target flash */"],
    idle_pointer: ['if (_isPointer(shown)) _treeQueuePointer(valEl, path);', '/* reverted idle preview */'],
    draft_jump: ['_treeExpandDraft(tree, next);', '/* reverted draft jump expansion */'],
    draft_count: ['_treeDraftHeader(tree);', '/* reverted draft count */'],
    input_width: ['input.style.minWidth = input.size + "ch";', '/* reverted content width */'],
    input_literal: ["if (el.classList.contains('tree-val-number')) v = window.NumberInput.strip(v);", '/* reverted raw numeric editor */'],
    input_echo: ['typeof stored === "number" ? String(stored) : _formatValue(stored)', '_formatValue(stored)'],
    sync_snapshot: ['var _seen = opts.seenView.count;', 'var _seen = (document.getElementById("pending-tray") || {dataset:{changeCount:"0"}}).dataset.changeCount;'],
    sync_count: ['? "0" : v', '? null : v'],
    relink: ['if (valEl.classList.contains("tree-val-pointer") && _isPointer(newVal))\n                body.append("pointer_mode", "relink");', '/* reverted relink transport */'],
    echo_target: ['if (data.resolved_path && data.resolved_path !== dotPath)', 'if (false)'],
    draft_storage: ['_treeSaveDrafts(tree);', '/* reverted persistence */'],
    unload_drafts: ["if (dirty) { e.preventDefault(); e.returnValue = ''; }", '/* reverted unload guard */'],
    refusal_clear: ["if (reason) reason.remove();", '/* reverted refusal clear */'],
    lazy_resume: ["if (toggle && toggle.classList.contains('collapsed')) _toggleNode(node);", '/* reverted draft expansion */'],
    nav_drafts: ["window.TreeInlineEdit.settle(target);", '; /* reverted navigation park */'],
    search: ['_treeResume(c, savedEditor);', '/* mutant: lost editor */'],
    off: ['if (!preserve && window.__treeCommitOnLeave !== false) flush();', 'if (!preserve) flush();'],
    render: ['_treeResume(container, savedEditor);', '/* mutant: lost drafts */'],
    pane: ['var editMemory = _treeEditMemory.get(_treeEditKey(container));', 'var editMemory = null;'],
    one: ['if (tree._editor) tree._editor.leave();', '/* mutant: leave old input */'],
    escape: ['input.value = input.defaultValue;\n                clearRefusal();\n                dirty();', '/* mutant: retain typing */'],
    caret: ['input.setSelectionRange(input.value.length, input.value.length);', 'input.select();'],
    flight: ['if (input.value === sent) input.value = input.defaultValue;', 'input.value = input.defaultValue;'],
    visible: ["var v = n.querySelector(':scope > .tree-row > .tree-val');", "var ch = n.querySelector(':scope > .tree-children'); if (ch && ch.style.display === \"none\") { _toggleNode(n); if (dir < 0) n = _kbDeepLast(n); } var v = n.querySelector(':scope > .tree-row > .tree-val');"],
    tab: ['!editableOnly || v.classList.contains("ui-tree-value")', 'true'],
    undo: ['&& !inInline && !inTreeEditor) return;', '&& !inInline) return;'],
    text_undo: ['if (inTreeEditor && a.value !== a.defaultValue) return;', '/* mutant: let server undo dirty text */'],
    global: ['if (a && a.matches && a.matches(".ui-tree-editor")) return true;', 'if (a && a.matches && a.matches(".ui-tree-editor")) return window.TreeInlineEdit.busy(a);'],
    refusal: ['if (result === false) { dirty(); return false; }', '/* mutant: discard refused typing */'],
    peek_retry: ['cache.delete(path); return {};', 'return {};'],
    tooltip: ['chip.title = (expected.class_path', 'chip.unusedTitle = (expected.class_path'],
    pointer: ['if (resolved && resolved.is_pointer)', 'if (false)'],
    brackets: ["if (a && a.matches && a.matches('.ui-tree-editor')) return;\n    if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable)) return;", "if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable) && !(a.matches('.ui-tree-editor') && window.TreeInlineEdit && !window.TreeInlineEdit.busy(a))) return;"],
    source: ['var editSource = _treeEditSource(container, data, options);', 'var editSource = "";'],
    source_key: ['JSON.stringify([tree._editContext, tree.id, tree._editSource])', 'JSON.stringify([tree._editContext, tree.id])'],
    stale_token: ['container._editContext !== editContext', 'false'],
    display_order: ['_treeValueDisplay(el, _formatValue(v));\n        el.dataset.editVal', '/* mutant: baseline updated too late */\n        el.dataset.editVal'],
    multi: ["var nd = a && a.closest && a.closest('.tree-node');", "var nd = a && a.tagName !== 'INPUT' && a.closest && a.closest('.tree-node');"],
    cache: ['if (!cache.has(path)) cache.set', 'if (true) cache.set'],
};
if (process.env.TREE_MUTATION) {
    if (!['chip_context','draft_style','suggestion_flow','suggestion_outside','pointer_layout'].includes(process.env.TREE_MUTATION)) {
        const [before, after] = mutations[process.env.TREE_MUTATION];
        assert(source.includes(before), 'mutation target exists');
        source = source.replaceAll(before, after);
        if (process.env.TREE_MUTATION === 'display_order') {
            source = source.replace('if (!el.contains(input)) el.textContent = dirty ? input.value : el._treeDisplay;\n        }',
                'if (!el.contains(input)) el.textContent = dirty ? input.value : el._treeDisplay;\n        }\n        _treeValueDisplay(el, _formatValue(v));');
        }
    }
}
const dom = new JSDOM('<!doctype html><input id="search"><div id="tree"></div><button id="after">After</button>',
    {runScripts:'outside-only', pretendToBeVisual:true, url:'http://localhost/explorer'});
const realms=[dom];
const w = dom.window, d = w.document;
const calls = [];
const toasts = [];
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
        if (p === 'pointer') data.resolved = {[p]:{is_pointer:true,resolvable:true,resolved_value:42,resolved_path:'target',shared_by:['one','two']}};
    }
    if (url === '/field/edit') {
        const b = new w.URLSearchParams(opts.body);
        let v = b.get('value');
        try { v = JSON.parse(v); } catch (_) {}
        data = refused ? {ok:false,error:"Refused value"} : fsp ? {ok:true,results:[{dot_path:b.get('dot_path'),applied:true,new_value:v}]} : {ok:true, stored:v, stored_kind:typeof v};
        if (b.get('dot_path') === 'pointer' && typeof v === 'number')
            data = {ok:true,stored:v,stored_kind:'number',resolved_path:'other',raw_value:'#/other'};
    }
    return new Promise(resolve => setTimeout(() => resolve({ok:true, json:async()=>data}), delay));
};
const ajax = [];
w.htmx = {ajax:async(method,url)=>{ajax.push(url);}, trigger(){}, process(){}};
w.eval(fs.readFileSync(path.join(staticDir,'search-query.js'),'utf8'));
w.eval(source);
w.eval(fs.readFileSync(path.join(staticDir,'manual.js'),'utf8'));
w.showToast=(message)=>toasts.push(message);
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
    val('a').dispatchEvent(new w.MouseEvent('pointerdown',{bubbles:true,cancelable:true,button:0}));
    assert.equal(d.activeElement.getAttribute('aria-label'),'a','first pointerdown mounts before click or debounce');
    w.jsonTreeSearch('tree','a'); await tick(230);
    assert.equal(d.activeElement.getAttribute('aria-label'),'a','pending search preserves pointer-mounted focus');
    w.jsonTreeSearch('tree',''); await tick(230);
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
    w.renderJsonTree('tree',{a:'',b:2},{defaultDepth:1,crud:true});
    focus('a');
    // jsdom has no native text insertion: apply the browser default only when
    // keydown is accepted, so navigation/preventDefault cannot fake typing.
    const bracketInput=d.activeElement;
    // Empty string literals display as two quotes; use a clean empty buffer
    // to exercise the first bracket with a caret and no selected text.
    bracketInput.value=bracketInput.defaultValue='';
    bracketInput.setSelectionRange(0,0);
    assert.equal(w.TreeInlineEdit.busy(bracketInput),false,'bracket typing starts in a clean editor');
    for (const ch of '[1,2]') {
        assert.equal(d.activeElement,bracketInput,'typing keeps the clean editor');
        if (key(ch)) {
            bracketInput.setRangeText(ch,bracketInput.selectionStart,bracketInput.selectionEnd,'end');
            bracketInput.dispatchEvent(new w.Event('input',{bubbles:true}));
        }
    }
    assert.equal(bracketInput.value,'[1,2]','clean editor accepts the complete bracket literal');
    assert.deepEqual(runMoves,[],'typing a bracket literal never navigates datasets');
    key('Escape');
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
    w.renderJsonTree('tree',{a:1,b:3},{defaultDepth:1,crud:true});focus('a');
    key('Escape');
    refused=true;type('invalid text');const refusedInput=d.activeElement;key('Enter');await tick();
    assert.equal(d.activeElement,refusedInput,'refusal keeps focus');
    assert.equal(refusedInput.value,'invalid text','refusal keeps typed text');
    assert(val('a').classList.contains('ui-tree-dirty'),'refusal stays amber');
    assert.equal(tree._treeData.a,1,'refusal leaves stored value');
    key('Escape');assert(!node('a').querySelector('.tree-edit-err'),'Escape clears refusal reason');
    type('invalid again');key('Enter');await tick();
    assert(node('a').querySelector('.tree-edit-err'),'second refusal is visible');
    type('next edit');assert(!node('a').querySelector('.tree-edit-err'),'next edit clears refusal reason');
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
    w.renderJsonTree('tree',{pointer:'#/target',target:42,other:77},{defaultDepth:1,crud:true});
    await tick(100);
    assert(val('pointer').querySelector('.ui-tree-resolved'),'unfocused pointer has resolved preview');
    // Opening and leaving the editor must not keep the preview as part of the
    // value's text: the next render showed it twice ("-> 42-> 42").
    focus('pointer');await tick(190);key('Escape');key('Escape');await tick(400);
    assert(!val('pointer').querySelector('.ui-tree-editor'),'editor left');
    assert.equal((val('pointer').textContent.match(/→/g)||[]).length,1,'resolved preview shown once after leaving the editor: '+val('pointer').textContent);
    assert.equal(val('pointer').querySelectorAll('.ui-tree-resolved').length,1,'one preview element');
    focus('pointer');
    assert(val('pointer').querySelector('.ui-tree-target'),'cached target hint is visible immediately on focus');
    await tick(190);
    assert.equal(val('pointer').querySelector('.ui-tree-target').textContent.trim(),'edits target (shared by 2)','pointer editing names target and sharing');
    const preview=val('pointer').querySelector('.ui-tree-resolved');
    assert(preview,'pointer has resolved preview');
    assert.equal(d.activeElement.value,'"#/target"','pointer input stays literal');
    assert.equal(preview.textContent.trim(),'\u2192 42','pointer preview shows resolved value');
    assert(d.activeElement.compareDocumentPosition(preview)&4,'preview follows input');
    type('"#/other"');key('Enter');await tick();
    assert.equal(new w.URLSearchParams(calls.filter(c=>c.url==='/field/edit').at(-1).body).get('pointer_mode'),'relink','pointer transport explicitly relinks');
    assert.equal(tree._treeData.pointer,'#/other','relink model uses server echo');
    type('9');key('Enter');await tick();
    assert(toasts.includes('Edited other'),'commit toast names pointer target');
    assert(val('other').classList.contains('leu-flash'),'visible target flashes');
    assert.equal(tree._treeData.other,9,'plain value updates the server write target');
    assert.equal(tree._treeData.target,42,'plain value leaves the former target unchanged');
    assert.equal(tree._treeData.pointer,'#/other','plain value preserves pointer model');
    let css=fs.readFileSync(path.join(staticDir,'ui-kit.css'),'utf8');
    if (process.env.TREE_MUTATION==='draft_style') css=css.replace('outline: var(--ui-bw) dashed var(--ui-warn-border);','');
    if (process.env.TREE_MUTATION==='pointer_layout') css=css.replace('.ui-tree-target { flex-basis: 100%;','.ui-tree-target {');
    assert(/\.ui-tree-target\s*\{[^}]*flex-basis: 100%/.test(css),'pointer target hint gets its own line');
    assert(css.includes('.tree-row:has(.ui-tree-mounted) .tree-row-actions'),'mounted editor hides overlapping actions');
    assert(/\.ui-tree-resolved\s*\{[^}]*color: var\(--ui-text-2\)/.test(css),'pointer preview is muted');
    assert(/\.ui-tree-dirty[^}]*outline: var\(--ui-bw\) dashed var\(--ui-warn-border\)/.test(css),'draft has dashed token outline');
    assert(/\.ui-tree-dirty::after[^}]*content: "draft"/.test(css),'draft has explicit marker');
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
    assert.equal(tree.querySelector('.ui-tree-drafts').textContent,'1 draft','header counts parked drafts');
    tree.querySelector('.ui-tree-drafts').click();
    assert.equal(d.activeElement.getAttribute('aria-label'),'a','draft count jumps to draft');
    focus('b');
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
    // D-44: an FSP edit commits at once through the batch door (its reply is
    // batch-shaped); no pre-commit card, and the committed NUMBER is painted
    fsp=true;focus('b');type('3');key('Enter');await tick(80);
    assert(!d.querySelector('.fsp-card'),'no FSP pre-commit offer');
    assert.strictEqual(tree._treeData.b,3,'FSP committed at once as a number');
    fsp=false;type('4');key('Enter');await tick(80);
    assert.equal(tree._treeData.b,4,'a later edit on the same field commits');
    assert.equal(d.activeElement.value,'4','later edit remains in the roving input');

    w.__treeCommitOnLeave=false;
    const swapTree = (data, sourceTag) => {
        w.TreeInlineEdit.settle(d.body);
        const next=d.createElement('div');next.id='tree';
        if (sourceTag !== undefined) next.dataset.treeSource=sourceTag;
        tree.replaceWith(next);tree=next;
        w.renderJsonTree('tree',data,{defaultDepth:1,crud:true});
    };
    // An explicit switch is the only partial allowed to advance tab identity.
    const identityTemplate=fs.readFileSync(path.join(staticDir,'../templates/_chip_context.html'),'utf8');
    const routes=fs.readFileSync(path.join(staticDir,'../routes.py'),'utf8');
    assert(routes.includes('identity = render_template("_chip_context.html",'),'in-place switch renders identity');
    assert(routes.includes('make_response(identity + "\\n" + body'),'identity precedes swapped content scripts');
    const switchIdentity=(key,token)=>{
        const script=identityTemplate.match(/<script>([\s\S]*?)<\/script>/)[1]
            .replace('{{ chip_key|tojson }}',JSON.stringify(key))
            .replace('{{ chip_token|tojson }}',JSON.stringify(token));
        if (process.env.TREE_MUTATION !== 'chip_context') w.eval(script);
    };
    switchIdentity('folder-one','token-one');
    swapTree({a:1,b:2},'state.json');focus('a');type('91');focus('b');
    switchIdentity('folder-two','token-two');
    swapTree({a:7,b:8},'state.json');
    assert.equal(val('a').textContent,'7','in-place switch cannot repaint an earlier folder draft');
    assert.equal(w.__chipKey,'folder-two','in-place switch refreshes folder key');
    assert.equal(w.__chipToken,'token-two','in-place switch refreshes write token');
    focus('a');type('9');key('Enter');await tick();
    const lastEdit=calls.filter(c=>c.url==='/field/edit').at(-1);
    assert.equal(new w.URLSearchParams(lastEdit.body).get('expect_chip'),'token-two','write uses current identity');

    type('92');focus('b');
    w.__chipToken='token-three';
    w.renderJsonTree('tree',{a:10,b:11},{defaultDepth:1,crud:true});
    assert.equal(val('a').textContent,'10','stale token clears drafts on the same element');
    assert(!val('a').classList.contains('ui-tree-dirty'),'stale token clears dirty state');

    focus('a');type('93');focus('b');
    swapTree({a:20,b:21},'snapshot:two');
    assert.equal(val('a').textContent,'20','new source with reused element id cannot inherit drafts');
    focus('a');type('94');focus('b');
    tree.dataset.treeSource='snapshot:three';
    w.renderJsonTree('tree',{a:30,b:31},{defaultDepth:1,crud:true});
    assert.equal(val('a').textContent,'30','source change on the same element clears drafts');
    focus('a');type('95');focus('b');
    swapTree({a:30,b:31},'snapshot:three');
    assert.equal(val('a').textContent,'95','same explicit source preserves drafts across pane replacement');
    focus('a');key('Escape');
    swapTree({a:40,b:41});focus('a');type('96');focus('b');
    swapTree({a:50,b:51});
    assert.equal(val('a').textContent,'50','unmarked different data objects do not share drafts');

    // A pending detached draft becomes clean when a matching patch arrives.
    // Its next repaint must read the NEW display baseline, even before settle.
    focus('a');delay=80;type('52');key('Enter');focus('b');
    w._paintTreeLeaf(tree,'a',52);
    const leaf=val('a'), writes=[];
    const textContent=Object.getOwnPropertyDescriptor(w.Node.prototype,'textContent');
    Object.defineProperty(leaf,'textContent',{
        configurable:true,get(){return textContent.get.call(this);},
        set(text){writes.push(text);textContent.set.call(this,text);}
    });
    w._paintTreeLeaf(tree,'a',53);
    assert(writes.length>0,'clean detached draft repaints');
    assert(writes.every(text=>text==='53'),'display baseline precedes every clean detached draft repaint');
    assert.equal(leaf.textContent,'53','clean detached draft shows current committed value');
    delete leaf.textContent;
    await tick(110);delay=0;
    tray.remove();
    // Real navigation parks a focused draft before the outgoing pane vanishes.
    tree.dataset.treeSource='state.json';
    w.renderJsonTree('tree',{branch:{deep:{value:1}}},{defaultDepth:99,crud:true});
    pane.appendChild(tree);focus('branch.deep.value');type('123');
    d.dispatchEvent(new w.CustomEvent('htmx:beforeSwap',{bubbles:true,cancelable:true,
        detail:{target:pane,shouldSwap:true}}));
    assert(!tree.querySelector('input'),'navigation parks the editor before the swap');
    const storedDrafts=w.sessionStorage.getItem('quam_tree_drafts_v1');
    assert(storedDrafts && storedDrafts.includes('123'),'draft text is persisted in sessionStorage');
    const unload=new w.Event('beforeunload',{cancelable:true});w.dispatchEvent(unload);
    assert(unload.defaultPrevented,'uncommitted drafts prompt on full unload');
    const next=d.createElement('div');next.id='tree';next.dataset.treeSource='state.json';
    tree.replaceWith(next);tree=next;
    w.renderJsonTree('tree',{branch:{deep:{value:1}}},{defaultDepth:0,crud:true});
    assert.equal(d.activeElement.value,'123','replacement expands lazy ancestors and restores focused draft');
    // A new document has no closure memory: restore solely from tab storage.
    const fresh=new JSDOM('<div id="tree" data-tree-source="state.json"></div>',
        {runScripts:'outside-only',pretendToBeVisual:true,url:'http://localhost/explorer'});
    realms.push(fresh);
    const persisted=JSON.parse(storedDrafts);
    const currentKey=Object.keys(persisted).find(k=>k.includes('folder-two|token-three') && k.includes('state.json'));
    fresh.window.sessionStorage.setItem('quam_tree_drafts_v1',JSON.stringify({[currentKey]:persisted[currentKey]}));
    fresh.window.htmx={ajax:async()=>{},trigger(){},process(){}};
    fresh.window.fetch=w.fetch;
    fresh.window.__chipKey=w.__chipKey;fresh.window.__chipToken=w.__chipToken;
    fresh.window.eval(source);
    fresh.window.renderJsonTree('tree',{branch:{deep:{value:1}}},{defaultDepth:0,crud:true});
    const restored=fresh.window.document.querySelector('.tree-node[data-path="branch.deep.value"] > .tree-row > .tree-val');
    assert(restored,'stored draft expands its lazy ancestors');
    assert.equal(restored.textContent,'123','session draft restores after returning with fresh data');
    restored.click();
    fresh.window.document.activeElement.dispatchEvent(new fresh.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert.equal(fresh.window.sessionStorage.getItem('quam_tree_drafts_v1'),'{}','Escape removes persisted draft');
    const cleanUnload=new fresh.window.Event('beforeunload',{cancelable:true});fresh.window.dispatchEvent(cleanUnload);
    assert(!cleanUnload.defaultPrevented,'clean tree does not prompt on unload');
    fresh.window.close();
    w.__treeCommitOnLeave = false;w.__chipToken='draft-header-pin';
    w.renderJsonTree('tree',{branch:{deep:{draft:1}},other:2},{defaultDepth:99,crud:true});
    focus('branch.deep.draft');type('3');focus('other');
    w.jsonTreeCollapseAll('tree');tree.querySelector('.ui-tree-drafts').click();
    assert.equal(d.activeElement.getAttribute('aria-label'),'branch.deep.draft','draft header jumps into collapsed branch');
    assert.equal(node('branch').querySelector('.tree-children').style.display,'','draft jump opens ancestors');
    key('Escape');
    assert.equal(tree.querySelector('.ui-tree-drafts').textContent,'0 drafts','discard updates header immediately');
    assert(tree.querySelector('.ui-tree-drafts').hidden,'no drafts: the count is not shown');
    w.__treeCommitOnLeave = true;
    w.renderJsonTree('tree',{long:1234567890123,other:1},{defaultDepth:1,crud:true});
    focus('long'); await tick(190);
    assert.equal(d.activeElement.value,'1234567890123','editing has no thousands separators');
    assert(parseInt(d.activeElement.style.minWidth)>=d.activeElement.value.length,'input minimum fits full content beside chip');
    type('2345678901234'); key('Enter'); await tick();
    assert.equal(d.activeElement.value,'2345678901234','commit echo stays ungrouped');
    d.getElementById('pending-tray')?.remove();
    w.doStateSync('apply'); await tick();
    assert.equal(new w.URLSearchParams(calls.filter(c=>c.url==='/state/sync').at(-1).body).get('seen_changes'),'0','missing tray fails closed with declared zero');
    w._applyInFlight=false;
    const syncTray=d.createElement('div');syncTray.id='pending-tray';syncTray.dataset.changeCount='1';d.body.append(syncTray);
    let releaseSync;
    w._pendingGridCommits=[new Promise(r=>releaseSync=r)];
    w.doStateSync('apply');
    syncTray.dataset.changeCount='3';
    w._pendingGridCommits=[];releaseSync({ok:true});await tick(100);
    assert.equal(new w.URLSearchParams(calls.filter(c=>c.url==='/state/sync').at(-1).body).get('seen_changes'),'2','sync Apply declares its rendered count plus its own commit, never the refreshed tray');
    tree.id='explorer-tree-state';
    const toolbar=d.createElement('div');toolbar.className='tree-toolbar';
    const searchInput=d.createElement('input');searchInput.id='explorer-search';toolbar.append(searchInput);tree.before(toolbar);
    let typeahead=fs.readFileSync(path.join(staticDir,'sidebar-typeahead.js'),'utf8');
    if(process.env.TREE_MUTATION==='suggestion_flow') typeahead=typeahead.replace("p.classList.toggle('sm-typeahead-tree', treeSearch);", "p.classList.toggle('sm-typeahead-tree', false);");
    if(process.env.TREE_MUTATION==='suggestion_outside') typeahead=typeahead.replace("document.addEventListener('pointerdown'", "document.addEventListener('unused-pointerdown'");
    w.eval(typeahead);
    searchInput.focus();searchInput.value='lon';searchInput.setSelectionRange(3,3);searchInput.dispatchEvent(new w.Event('input',{bubbles:true}));await tick(80);
    const suggestions=d.getElementById('sm-typeahead');
    assert(suggestions && !suggestions.hidden,'tree typeahead opens');
    assert(suggestions.classList.contains('sm-typeahead-tree'),'tree suggestions use flow placement so rows cannot be covered');
    d.body.dispatchEvent(new w.MouseEvent('pointerdown',{bubbles:true,button:0}));
    assert(suggestions.hidden,'outside pointerdown closes suggestions before click');
    await tick(220);
    // Auto-save: every way of leaving commits the open editor (ON default).
    w.__treeCommitOnLeave = true; refused = false; delay = 0;
    const edits = () => calls.filter(c => c.url === '/field/edit').length;
    const leaveBy = async (name, fire, value) => {
        w.renderJsonTree(tree.id,{x:1,y:2},{defaultDepth:1,crud:true});
        focus('x'); type(value); const n = edits();
        fire(); await tick(60);
        assert(edits() > n, name + ' commits the open editor');
        assert.equal(tree._treeData.x, Number(value), name + ' stores the typed value');
    };
    await leaveBy('window blur', () => w.dispatchEvent(new w.Event('blur')), '11');
    let vis = 'visible';
    Object.defineProperty(d, 'visibilityState', {configurable:true, get:() => vis});
    await leaveBy('tab hidden', () => { vis = 'hidden'; d.dispatchEvent(new w.Event('visibilitychange')); }, '12');
    vis = 'visible';
    await leaveBy('pagehide', () => w.dispatchEvent(new w.Event('pagehide')), '13');
    const navPane = d.createElement('div'); navPane.id = 'table-pane'; tree.before(navPane); navPane.append(tree);
    await leaveBy('htmx navigation', () => d.body.dispatchEvent(new w.CustomEvent('htmx:beforeSwap',
        {bubbles:true, cancelable:true, detail:{target:navPane, shouldSwap:true}})), '14');
    // A refused auto-commit keeps the draft and its reason.
    w.renderJsonTree(tree.id,{x:1,y:2},{defaultDepth:1,crud:true});
    focus('x'); type('99'); refused = true;
    w.dispatchEvent(new w.Event('blur')); await tick(60);
    assert.equal(tree._treeData.x, 1, 'refused auto-commit stores nothing');
    assert(val('x').classList.contains('ui-tree-dirty'), 'refused auto-commit keeps the draft');
    refused = false;
    // OFF is respected: leaving never commits.
    w.__treeCommitOnLeave = false;
    w.renderJsonTree(tree.id,{x:1,y:2},{defaultDepth:1,crud:true});
    focus('x'); type('77'); const nOff = edits();
    w.dispatchEvent(new w.Event('blur')); w.dispatchEvent(new w.Event('pagehide')); await tick(60);
    assert.equal(edits(), nOff, 'OFF: leaving the window does not commit');
    w.__treeCommitOnLeave = true;
    console.log('ALL OK: Stage A pins');
    w.close();
})().catch(e=>{console.error(e);process.exitCode=1;realms.forEach(r=>r.window.close());});
