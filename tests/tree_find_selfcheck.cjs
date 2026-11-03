/* S6: shipped find, occurrence, keyboard and batch behavior. */
'use strict';
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..');
let source = fs.readFileSync(path.join(root, 'quam_state_manager/web/static/app.js'), 'utf8').replace(/\r\n/g, '\n');
const mutations = {
    find_open: ['done = window.TreeFind.open();', 'done = false;'],
    find_mac: ["k === 'f' && e.metaKey && e.altKey", "k === 'f' && false"],
    find_model: ["TM.leaves(t._treeData, true).forEach", 'TM.leaves({}, true).forEach'],
    find_cap: ['window.__treeSearchMaterializeMax || 150', 'window.__treeSearchMaterializeMax || 10000'],
    find_case: ["flags = on('case') ? 'g' : 'gi';", "flags = 'gi';"],
    find_word: ["return on('word') ? '\\\\b(?:'", "return false ? '\\\\b(?:'"],
    find_regex: ["var s = on('regex') ? query : escapeRe(query);", 'var s = escapeRe(query);'],
    find_keys: ["if (k === 'k' && !keys) continue;", "if (k === 'k') continue;"],
    find_preview: ['plan = data.results;', 'plan = [];'],
    find_group: ["updates:updates, group:'new'", 'updates:updates'],
    find_one: ['all ? hits : [hits[Math.min(current, hits.length - 1)]]', 'hits'],
    find_occurrence: ['String(valueAt(f.tree, p))) === text', 'String(valueAt(f.tree, p))) !== text'],
    find_skip: ['if (skip && st.sel.length) st.sel.pop();', '/* no skip */'],
    find_entity: ["k === 'd' && e.altKey && !e.shiftKey", "k === 'd' && false"],
    find_typing: ["input.value = text; input.focus(); preview();", "input.value = ''; input.focus(); preview();"],
    find_all: ['st.sel = st.occ.slice();', 'st.sel = st.sel.slice();'],
    find_meta: ["if (!(e.ctrlKey || e.metaKey)) return;", "if (!e.ctrlKey) return;"],
    find_path: ["close(); tree.focus(); TM.openPattern();", "close();"],
    find_reset: ["plan = []; window.treeFindShowPaths(tree, null);", "plan = [];"],
    find_key_selection: ["|| selectedKey)", ")"],
    find_paging: ["rows.slice(shown, shown + limit).forEach", "rows.forEach"],
    find_race: ['if (id !== pserial || bar.hidden) return;', 'if (bar.hidden) return;'],
    find_correct: ['state.checked = null; state.checkedMap = null;', 'state.checked = null;'],
    find_close_query: ["find.value = ''; find.dispatchEvent", "find.dispatchEvent"],
    find_tab: ['tree = next;', '/* no tab switch */'],
    find_late_inline: ['if (records.some(function (record)', 'if (false && records.some(function (record)'],
    find_reopen: ["if (bar && !bar.hidden && tree === active)", 'if (false)'],
    find_skipped_open: ["if (plan.some(function (r) { return r.skip; }))", "if (false)"],
    find_current: ["current = hits.findIndex(function (hit)", "current = 0; hits.findIndex(function (hit)"],
    find_search_chord: ["inSearch && (k === 'h' || k === 'f')", 'false'],
    find_existing: ["toolbar.insertAdjacentElement('afterend', bar);", "document.body.appendChild(bar);"],
    find_inline: ["        paintInline();\n        settle();\n    }", "        settle();\n    }"],
    find_enter: ["replaceNow(!!((e.ctrlKey || e.metaKey) && e.altKey));", "return;"],
    // review findings (D-36..D-41)
    find_debounce: ['timer = setTimeout(function () { timer = null; pass(id); }, DEBOUNCE_MS);', 'pass(id);'],
    find_worker: ["on('regex') && typeof Worker === 'function'", "false && typeof Worker === 'function'"],
    find_budget: ["w.terminate(); if (worker === w) { worker = null; workerCorpus = null; }\n                resolve('slow');", "resolve('slow');"],
    find_slow_msg: ["status('Pattern too slow \\u2014 refine it');", "status('0 matches');"],
    find_cancel: ["if (running) { running.cancel(); running = null; }", "/* no cancel */"],
    find_phrase: ["var s = on('regex') ? query : escapeRe(query);", "var s = on('regex') ? query : query.split(' ').map(escapeRe).join('|');"],
    find_literal_rep: ["t.replace(re, function () { return rep; })", 't.replace(re, rep)'],
    find_whole_number: ["(on('case') ? t : t.toLowerCase()) === needle", "(on('case') ? t : t.toLowerCase()).indexOf(needle) >= 0"],
    find_review: ['if (!all) await applyPlan();', 'await applyPlan();'],
    find_review_more: ["box.appendChild(more);", "/* no more */"],
    find_expect_seq: [", expect_chip:token, expect_seq:planSeq}", ", expect_chip:token}"],
    find_advance: ['current = at < 0 ? 0 : at;', 'current = 0;'],
    find_label: ["(label || ' ')", "'Find'"],
    chord_palette: ["if (!st || !st.occurrence) { skipChord = 0; return; }", "if (false) { skipChord = 0; return; }"],
    chord_stop: ["skipChord = Date.now(); e.preventDefault(); e.stopPropagation(); return;", "skipChord = Date.now(); e.preventDefault(); return;"],
    chord_expiry: ["&& !(Date.now() - skipChord > CHORD_MS) &&", "&&"],
    chord_other_key: ["if (!keep) skipChord = 0;", "/* kept */"],
    empty_refuse: ["if (notText) {   // D-40", "if (false) {   // D-40"],
    empty_confirm: ["inp.value === '' && !st.emptyArmed)", "inp.value === '' && false)"],
    mac_code: ["return m ? m[1].toLowerCase() : key;", 'return key;'],
    multi_label: ["st.key ? 'Key name' : 'Equal to'", "'Path'"],
    multi_full_old: ["oldEl.title = String(fmt(r.value));", ''],
    find_rename: ["'/api/pulse/rename', {target:'#inspector-pane'", "'/field/edit-batch', {target:'#inspector-pane'"],
};
if (process.env.FIND_MUTATION) {
    const [before, after] = mutations[process.env.FIND_MUTATION];
    assert(source.includes(before), 'mutation target exists'); source = source.replaceAll(before, after);
}
const dom = new JSDOM('<!doctype html><input id="outside"><div class="tree-toolbar"><input id="explorer-search" class="tree-search"></div><div id="tree"></div><div id="wiring"></div><div id="pending-tray"></div>',
    {url:'http://localhost/explorer', runScripts:'outside-only', pretendToBeVisual:true});
const w = dom.window, d = w.document;
const tick = (ms=30) => new Promise(r=>setTimeout(r,ms));
const posts = [], deltas = [], ajax = [];
let model = {items:{first:{amplitude:1,note:'same'}, second:{amplitude:2,note:'same'}, third:{amplitude:1,note:'other'}},
    text:'cat scatter CAT', literal:'a.b', near:0.50096, flag:false, pointer:'#/items/first/amplitude', id:'same', full_scale_power_dbm:1,
    samples:Object.fromEntries(Array.from({length:200}, (_, i)=>['group_'+i,{value:'needle'}]))};
const model0 = JSON.parse(JSON.stringify(model));
const server = process.env.FIND_SERVER;
w.fetch = async (url, options) => {
    if (server) {
        if (options?.body && typeof options.body !== 'string') options = {...options, body:options.body.toString()};
        if (url === '/field/edit-batch') posts.push(JSON.parse(options.body));
        return fetch(server + url, options);
    }
    if (url.startsWith('/field/peek')) return {ok:true,json:async()=>({ok:true,expected:{}})};
    const body = options?.body ? JSON.parse(options.body) : {};
    if (url === '/field/replace-preview') return {json:async()=>({ok:true,results:body.updates.map(r=>{
        const old = r.dot_path.split('.').reduce((v,k)=>v[k], model);
        const skip = r.key ? 'key rename' : w.TreeMulti.skipReason(tree,r.dot_path,old,false)
            || (typeof old === 'number' && !Number.isFinite(Number(r.value)) ? 'Cannot coerce' : null);
        return {dot_path:r.dot_path,old_value:old,new_value:typeof old==='number'?Number(r.value):r.value,value:r.value,skip};
    }),mutation_seq:7})};
    if (url === '/field/edit-batch') {
        posts.push(body);
        return {json:async()=>({ok:true,results:body.updates.map(r=>({dot_path:r.dot_path,applied:true,new_value:typeof r.value==='string' && /^\d+$/.test(r.value)?Number(r.value):r.value}))})};
    }
    return {json:async()=>({ok:true})};
};
w.htmx = {ajax:async(method,url,opts)=>{
    ajax.push({method,url,opts});
    if (server && /^\/(undo|redo)/.test(url)) {
        const response=await fetch(server+url,{method,headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(opts.values).toString()});
        assert(response.ok, 'undo door succeeds');
    }
},trigger(){},process(){}};
const workerSrc = fs.readFileSync(path.join(root,'quam_state_manager/web/static/tree-find-worker.js'),'utf8');
const workers = [];
w.Worker = class {
    constructor(url) { assert.equal(url,'/static/tree-find-worker.js'); workers.push(this); const me=this;
        this.scope = {postMessage:d=>setTimeout(()=>{ if(!me.dead && me.onmessage) me.onmessage({data:d}); },0)};
        new Function('self', workerSrc)(this.scope); }
    postMessage(d) { if (w.__workerHang && !d.texts) return; setTimeout(()=>{ if(!this.dead) this.scope.onmessage({data:d}); },0); }
    terminate() { this.dead = true; }
};
w.__treeFindBudgetMs = 300; w.__treeChordMs = 300;
w._treeReadOnly = {skip_leaves:['id'],skip_reason:'read-only'};
w.eval(fs.readFileSync(path.join(root,'quam_state_manager/web/static/search-query.js'),'utf8'));
w.eval(source);
w.ValueDelta = {paint:(el,old,next)=>{deltas.push([old,next]);el.textContent='delta';}};
const tree = d.getElementById('tree');
function render() { w.renderJsonTree('tree',model,{defaultDepth:1,crud:true}); }
function fresh() { model = JSON.parse(JSON.stringify(model0)); render(); }
function focus(p) {
    w.jsonTreeSetExpanded('tree',p.split('.').slice(0,-1).map((_,i)=>p.split('.').slice(0,i+1).join('.')));
    tree.querySelector('.tree-node[data-path="'+p+'"] > .tree-row > .tree-val').click();
    assert(d.activeElement.matches('.ui-tree-editor'));
}
function key(k,mods={}) {
    const e = new w.KeyboardEvent('keydown',{key:k,bubbles:true,cancelable:true,...mods});
    d.activeElement.dispatchEvent(e);return e;
}
function setNoFlush(name,value) {
    const e=name==='find'?d.getElementById('explorer-search'):d.querySelector('#tree-find [name="'+name+'"]');
    if (e.hasAttribute('aria-pressed')) { if ((e.getAttribute('aria-pressed')==='true')!==value) e.click(); }
    else { e.value=value; e.dispatchEvent(new w.Event('input',{bubbles:true})); }
    if(name==='find') w.TreeFind.refresh();
}
async function set(name,value) { setNoFlush(name,value); await w.TreeFind.flush(); }
function count(n) { assert.equal(w.TreeFind.hits().length,n,'whole-model match count');assert.equal(d.querySelector('#tree-find output').textContent,n+(n===1?' match':' matches'),'visible whole-model count'); }
function button(label) { return [...d.querySelectorAll('#tree-find button')].find(b=>b.textContent===label); }
(async()=>{
    render();
    for (const mod of [{ctrlKey:true},{metaKey:true}]) {
        focus('items.first.amplitude');
        assert(key('d',mod).defaultPrevented,'occurrence chord from roving input');
        assert.deepEqual(Array.from(w.TreeMulti.state().sel),['items.first.amplitude']);
        key('d',mod);
        assert.deepEqual(Array.from(w.TreeMulti.state().sel),['items.first.amplitude','items.third.amplitude'],'equal value, across fields');
        key('d',mod);assert.equal(w.TreeMulti.state().sel.length,3,'wrap adds equal FSP value once');
        key('d',mod);assert.equal(w.TreeMulti.state().sel.length,3,'no duplicates');
        key('Escape');
        focus('items.first.note');key('d',mod);key('k',mod);key('d',mod);
        assert.deepEqual(Array.from(w.TreeMulti.state().sel),['items.second.note'],'skip replaces current selection');
        key('l',{...mod,shiftKey:true});
        assert.equal(w.TreeMulti.state().sel.length,3,'select all equal values including read-only');
        key('Escape');
        focus('items.first.amplitude');assert(key('d',{...mod,altKey:true}).defaultPrevented,'entity chord handled');key('d',{...mod,altKey:true});
        assert(w.TreeMulti.state(),'entity selection exists');
        assert.deepEqual(Array.from(w.TreeMulti.state().sel),['items.first.amplitude','items.second.amplitude'],'entity chord preserved');
        key('Escape');
    }
    focus('items.first.amplitude');
    const range = d.createRange();
    range.selectNodeContents(tree.querySelector('.tree-node[data-path="items.first.amplitude"] > .tree-row > .tree-key'));
    w.getSelection().removeAllRanges();w.getSelection().addRange(range);key('d',{metaKey:true});key('l',{metaKey:true,shiftKey:true});
    assert.deepEqual(Array.from(w.TreeMulti.state().sel),['items.first.amplitude','items.second.amplitude','items.third.amplitude'],'selected keys match key names');
    key('Escape');w.getSelection().removeAllRanges();
    for (const chord of [{ctrlKey:true,key:'h'},{metaKey:true,altKey:true,key:'f'}]) {
        focus('items.first.amplitude');
        assert(key(chord.key,chord).defaultPrevented,'find chord prevented inside input');
        assert(!d.getElementById('tree-find').hidden,'find opens');
        assert.equal(d.querySelector('.tree-toolbar').nextElementSibling.id,'tree-find','replace row below existing search');
        assert.equal(d.querySelectorAll('#tree-find [name=find]').length,0,'no duplicate find input');
        key('Escape');
    }
    w._activeTreeId=()=> 'tree';
    d.getElementById('explorer-search').focus();assert(key('h',{ctrlKey:true}).defaultPrevented,'search field opens replace row');
    await set('find','needle');d.getElementById('explorer-search').focus();key('h',{ctrlKey:true});
    assert.equal(d.getElementById('explorer-search').value,'needle','reopening retains find text');key('Escape');
    focus('items.first.amplitude');assert(!key('h',{metaKey:true}).defaultPrevented,'Cmd H belongs to application');
    key('h',{ctrlKey:true});
    await set('find','needle');count(200);
    assert.equal(tree.querySelectorAll('.tree-highlight').length,150,'only first 150 hits materialise');
    assert.equal(d.querySelectorAll('.ui-tree-find-result').length,150,'result page respects shared cap');
    await set('replace','thread');
    w.jsonTreeSetExpanded('tree',['samples','samples.group_199']);await tick();
    assert(tree.querySelector('.tree-node[data-path="samples.group_199.value"] .ui-tree-find-inline'),'newly materialised match has inline preview');
    await set('replace','');
    assert(d.querySelector('.ui-tree-find-more'),'more rows offered');[...d.querySelectorAll('.ui-tree-find-more')].at(-1).click();
    assert(d.querySelector('.ui-tree-find-results').textContent.includes('samples.group_199.value'),'unmaterialised path listed');
    await set('find','cat');count(1);await set('case',true);await set('find','Cat');count(0);
    await set('case',false);await set('word',true);await set('find','scatter');count(1);await set('find','scat');count(0);
    await set('word',false);await set('regex',true);await set('find','^cat.*CAT$');count(1);
    await set('find','[');assert(/Invalid regex/.test(d.querySelector('#tree-find output').textContent));
    await set('regex',false);await set('find','a.b');count(1);await set('find','cat | other');count(0); // D-37: one literal phrase, no OR grammar
    await set('find','amplitude');count(1); // pointer text, never paths by default
    await set('keys',true);count(4);await set('keys',false);
    await set('find','same');await set('replace','changed');
    assert(tree.querySelector('.ui-tree-find-inline')?.textContent.includes('same \u2192 changed'),'inline old to new preview');
    await w.TreeFind.preview(false);
    assert.equal(d.querySelectorAll('.ui-tree-find-result').length,1,'Replace One preview');
    await set('find','same');
    [...d.querySelectorAll('.ui-tree-find-result button')].find(b=>b.textContent==='items.second.note').click();
    await w.TreeFind.preview(false);assert(d.querySelector('.ui-tree-find-results').textContent.includes('items.second.note'),'Replace One follows selected match');
    await set('find','same');[...d.querySelectorAll('.ui-tree-find-result button')].find(b=>b.textContent==='id').click();
    d.querySelector('#tree-find details').open=false;await w.TreeFind.preview(false);
    assert(d.querySelector('#tree-find details').open,'skipped reasons are revealed');
    await w.TreeFind.preview(true);
    assert(/read-only/.test(d.querySelector('.ui-tree-find-results').textContent),'read-only listed');
    assert(deltas.length,'ValueDelta paints preview');
    await w.TreeFind.apply();
    assert.equal(posts.at(-1).group,'new','one undo group');
    assert.equal(posts.at(-1).updates.length,2,'only eligible rows sent');
    if(server) {
        const peek=async()=> (await (await fetch(server+'/field/peek?dot_path=items.first.note&dot_path=items.second.note')).json()).values;
        assert.deepEqual(await peek(),{'items.first.note':'changed','items.second.note':'changed'});
        focus('items.first.note');key('z',{ctrlKey:true});await tick(250);
        assert.deepEqual(await peek(),{'items.first.note':'same','items.second.note':'same'},'Ctrl Z server restores entire replacement group');
    }
    await set('regex',true);await set('find','.*');await set('replace','invalid');await w.TreeFind.preview(true);
    const reasons=d.querySelector('.ui-tree-find-results').textContent;
    assert(/pointer/.test(reasons) && /FSP/.test(reasons) && /coerce/i.test(reasons),'all skip reasons listed including type refusal');
    // A stale preview response must never arm Apply for a newer query.
    const liveFetch=w.fetch;let release;
    w.fetch=async(url,opts)=>url==='/field/replace-preview'?new Promise(r=>{release=r;}):liveFetch(url,opts);
    const pending=w.TreeFind.preview(true);await tick(5);await set('find','needle');
    release({json:async()=>({ok:true,results:[{dot_path:'text',old_value:'old',new_value:'new',value:'new'}]})});
    await pending;assert(!w.TreeFind.ready(),'stale preview cannot be applied');
    // Key operations retain the structural rename door and its inspector response.
    w.fetch=async(url,opts)=>url==='/field/replace-preview'?{json:async()=>({ok:true,results:[{
        dot_path:'operation',old_value:'old',skip:'key rename',rename:{path:'operation',new_name:'new',retarget:'1'}}]})}:liveFetch(url,opts);
    await w.TreeFind.preview(true);button('Rename key').click();
    assert.equal(ajax.at(-1).url,'/api/pulse/rename','key rename uses structural door');
    assert.equal(ajax.at(-1).opts.target,'#inspector-pane');
    assert.equal(ajax.at(-1).opts.values.new_name,'new');w.fetch=liveFetch;
    w.TreeFind.close();assert.equal(d.getElementById('explorer-search').value,'','close clears obsolete query');assert.equal(tree.querySelectorAll('.tree-search-hidden').length,0,'close restores tree');render();focus('items.first.note');key('d',{metaKey:true});key('d',{metaKey:true});
    d.activeElement.dispatchEvent(new w.InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'insertText',data:'X'}));
    assert.equal(d.activeElement.value,'X','typing enters multi editor');
    key('Enter');await tick(100);
    assert.equal(posts.at(-1).updates.length,2,'occurrence typing commits one batch');
    focus('items.first.amplitude');key('d',{ctrlKey:true});
    const multi=d.querySelector('.tree-multi-value');multi.value='invalid';multi.dispatchEvent(new w.Event('input',{bubbles:true}));await tick(80);
    assert(w.TreeMulti.state().checked[0].skip,'invalid multi text refused in preview');
    multi.value='2';multi.dispatchEvent(new w.Event('input',{bubbles:true}));await tick(80);
    assert.equal(w.TreeMulti.state().checked[0].new_value,2,'corrected text is checked again');
    w.TreeMulti.clear();
    focus('items.first.amplitude');key('h',{ctrlKey:true});button('By path').click();
    assert(!d.getElementById('tree-multi').hidden,'By path opens existing pattern editor');
    w.TreeMulti.clear();d.getElementById('outside').focus();assert(!key('d',{metaKey:true}).defaultPrevented,'outside untouched');
    w.renderJsonTree('wiring',{value:'wiring-only'},{defaultDepth:1,crud:true});
    focus('items.first.amplitude');key('h',{ctrlKey:true});
    w.jsonTreeSearch('wiring','');await set('find','needle');count(0);
    d.querySelector('#wiring .tree-val').click();key('h',{ctrlKey:true});await set('find','needle');count(0);
    await set('find','wiring-only');count(1);
    if(!server) {
        await set('regex',false);await set('find','wiring-only');await set('replace','single');
        d.querySelector('#tree-find [name=replace]').focus();key('Enter');await tick(100);
        assert.equal(posts.at(-1).updates.length,1,'Enter replaces one');
        assert.equal(posts.at(-1).updates[0].value,'single','Enter uses replacement');
        w.renderJsonTree('wiring',{value:'wiring-only'},{defaultDepth:1,crud:true});
    }
    if(!server) for(const mod of [{ctrlKey:true},{metaKey:true}]) {
        await set('regex',false); await set('find','wiring-only');await set('replace','updated');
        d.querySelector('#tree-find [name=replace]').focus();
        const before=posts.length;
        key('Enter',{...mod,altKey:true});await tick(100);
        assert.equal(posts.length,before,'D-38: replace-all chord only opens the review');
        assert(w.TreeFind.reviewing(),'review step shown');
        assert(/Replace 1/.test(d.querySelector('#tree-find [name=confirm]').textContent),'review names the count');
        key('Enter');await tick(100);
        assert.equal(posts.at(-1).updates[0].dot_path,'value','replace-all confirmed commits');
        assert.equal(posts.at(-1).updates[0].value,'updated','replace text sent');
        w.renderJsonTree('wiring',{value:'wiring-only'},{defaultDepth:1,crud:true});
    }
    if(!server) {
    // ---- review findings D-36..D-41 ----
    fresh();w._activeTreeId=()=> 'tree';focus('items.first.amplitude');key('h',{ctrlKey:true});
    await set('regex',false);await set('keys',false);await set('case',false);await set('word',false);await set('replace','');
    await set('find','needle');count(200);
    // D-36 debounce: a keystroke does not run the pass at once
    setNoFlush('find','cat');await tick(20);
    assert.equal(w.TreeFind.hits().length,200,'no pass before the debounce');
    await tick(200);count(1);
    // D-36 a hung regex is stopped past its budget and lists nothing
    await set('regex',true);
    w.__workerHang=true;
    const t0=Date.now();await set('find','(.*)*x$');
    assert(Date.now()-t0<2000,'budgeted');
    assert.equal(d.querySelector('#tree-find output').textContent,'Pattern too slow \u2014 refine it','too slow said');
    assert.equal(w.TreeFind.hits().length,0,'no hits from a stopped pass');
    assert(workers.some(x=>x.dead),'worker terminated past its budget');
    // a new keystroke cancels (and terminates) the regex pass in flight
    setNoFlush('find','(a+)+$');await tick(170);
    const busy=workers.at(-1);assert(!busy.dead,'one worker busy');
    w.__workerHang=false;setNoFlush('find','^cat');await w.TreeFind.flush();
    count(1);
    assert(busy.dead && workers.at(-1)!==busy,'the cancelled pass terminated its busy worker');
    await set('regex',false);
    // D-37 a phrase is one literal, the replacement is literal
    await set('find','cat CAT');count(0);
    await set('find','a.b');await set('replace','$&$&');
    assert.equal(w.TreeFind.hits()[0].next,'$&$&','$& is literal with regex off');
    await set('replace','');
    // D-38 a number matches only as a whole value
    await set('find','0.5');count(0);await set('find','0.50096');count(1);
    await set('find','false');count(1);
    // D-38 review lists every replacement, not only the first 150
    await set('find','needle');await set('replace','thread');
    const before=posts.length;
    d.querySelector('#tree-find [name=replace]').focus();key('Enter',{ctrlKey:true,altKey:true});await tick(120);
    assert.equal(posts.length,before,'Replace All waits for review');
    assert(/Review: 200 replacements/.test(d.querySelector('.ui-tree-find-review-sum').textContent),'review total');
    assert.equal(d.querySelectorAll('.ui-tree-find-results .ui-tree-find-result').length,150,'first page');
    assert(d.querySelector('.ui-tree-find-more'),'show more offered');d.querySelector('.ui-tree-find-more').click();
    assert.equal(d.querySelectorAll('.ui-tree-find-results .ui-tree-find-result').length,200,'full list reachable');
    key('Escape');assert(!w.TreeFind.reviewing(),'Escape cancels the review');assert.equal(posts.length,before,'nothing written');
    // P2: the client sends the preview's mutation_seq
    key('Enter',{ctrlKey:true,altKey:true});await tick(120);key('Enter');await tick(120);
    assert.equal(posts.at(-1).expect_seq,7,'expect_seq rides the batch');
    w.TreeFind.close();fresh();focus('items.first.amplitude');key('h',{ctrlKey:true});
    // D-40 Replace One advances past the field it replaced
    await set('find','same');await set('replace','same2');
    d.querySelector('#tree-find [name=replace]').focus();key('Enter');await tick(150);
    assert.equal(posts.at(-1).updates[0].dot_path,'items.first.note','first replace');
    key('Enter');await tick(150);
    assert.equal(posts.at(-1).updates[0].dot_path,'items.second.note','Replace One advances to the next hit');
    // the no-match message names the query, never a placeholder
    await set('find','zzzq');
    assert(/zzzq/.test(tree.querySelector('.tsr-head')?.textContent||''),'no-match message names the query');
    w.TreeFind.close();
    // D-39 Ctrl+K belongs to the palette unless an occurrence selection is active
    fresh();focus('items.first.note');
    let palette=0;const pal=()=>palette++;d.addEventListener('keydown',pal);
    let ek=key('k',{ctrlKey:true});assert.equal(palette,1,'Ctrl+K without a selection reaches the palette');
    key('d',{ctrlKey:true});assert.equal(w.TreeMulti.state().sel.length,1,'selection started');
    palette=0;ek=key('k',{ctrlKey:true});assert(ek.defaultPrevented,'chord taken while selecting');assert.equal(palette,0,'palette not reached');
    d.removeEventListener('keydown',pal);
    await tick(400);key('d',{ctrlKey:true});
    assert.equal(w.TreeMulti.state().sel.length,2,'expired chord adds instead of skipping');
    key('k',{ctrlKey:true});key('x');key('d',{ctrlKey:true});
    assert.equal(w.TreeMulti.state().sel.length,3,'another key ends the chord');
    key('Escape');
    // D-41 Option-modified key on a Mac still triggers
    focus('items.first.amplitude');
    assert(key('\u2202',{metaKey:true,altKey:true,code:'KeyD'}).defaultPrevented,'Cmd+Option+D by e.code');
    assert(w.TreeMulti.state() && !w.TreeMulti.state().occurrence,'entity walk from e.code');
    key('Escape');
    // D-40 empty value: refused for numbers, confirmed for text
    focus('items.first.amplitude');key('d',{ctrlKey:true});key('d',{ctrlKey:true});
    assert.equal(d.querySelector('.tree-multi-pat-label').textContent,'Equal to','occurrence anchor is a value, not a path');
    d.activeElement.dispatchEvent(new w.InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'deleteContentBackward'}));
    assert(/refused/.test(d.querySelector('.tree-multi-msg').textContent),'empty refused for numbers');
    assert(!w.TreeMulti.state().editing,'no edit started');
    key('Escape');
    focus('items.first.note');key('d',{ctrlKey:true});
    const nb=posts.length;
    d.activeElement.dispatchEvent(new w.InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'insertFromPaste',data:null}));
    await tick(80);
    const mv=d.querySelector('.tree-multi-value');mv.focus();key('Enter');await tick(80);
    assert.equal(posts.length,nb,'first Enter only asks');
    assert(/Enter again/.test(d.querySelector('.tree-multi-msg').textContent),'confirmation asked');
    key('Enter');await tick(100);
    assert.equal(posts.length,nb+1,'second Enter writes');
    assert.equal(posts.at(-1).updates[0].value,'','empty text written after confirmation');
    // full old values in the multi panel
    fresh();focus('items.first.note');key('d',{ctrlKey:true});
    assert(/same/.test(d.querySelector('.tree-multi-old').title),'old value carries its full text');
    key('Escape');
    }
    console.log('ALL OK: S6 find pins'+(server?' with server undo':''));dom.window.close();process.exit(0);
})().catch(err=>{console.error(err);dom.window.close();process.exit(1);});
