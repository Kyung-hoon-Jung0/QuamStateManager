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
    find_model: ["TM.leaves(tree._treeData, control('keys').checked).forEach", 'TM.leaves({}).forEach'],
    find_cap: ['window.__treeSearchMaterializeMax || 150', 'window.__treeSearchMaterializeMax || 10000'],
    find_case: ["control('case').checked ? 'g' : 'gi'", "false ? 'g' : 'gi'"],
    find_word: ["control('word').checked ?", 'false ?'],
    find_regex: ["control('regex').checked ? s : escape(s)", 'escape(s)'],
    find_keys: ["control('keys').checked &&", 'false &&'],
    find_preview: ['plan = data.results;', 'plan = [];'],
    find_group: ["updates:updates, group:'new'", 'updates:updates'],
    find_one: ['all ? hits : [hits[current]]', 'hits'],
    find_occurrence: ['String(valueAt(f.tree, p))) === text', 'String(valueAt(f.tree, p))) !== text'],
    find_skip: ['if (skip && st.sel.length) st.sel.pop();', '/* no skip */'],
    find_entity: ["k === 'd' && e.altKey && !e.shiftKey", "k === 'd' && false"],
    find_typing: ["input.value = e.data || ''; input.focus(); preview();", "input.value = ''; input.focus(); preview();"],
    find_all: ['st.sel = st.occ.slice();', 'st.sel = st.sel.slice();'],
    find_meta: ["if (!(e.ctrlKey || e.metaKey)) return;", "if (!e.ctrlKey) return;"],
    find_path: ["close(); tree.focus(); TM.openPattern();", "close();"],
    find_reset: ["plan = []; window.treeFindShowPaths(tree, null);", "plan = [];"],
    find_key_selection: ["|| selectedKey)", ")"],
    find_paging: ["rows.slice(offset, offset + limit).forEach", "rows.forEach"],
    find_race: ['if (id !== serial || bar.hidden) return;', 'if (bar.hidden) return;'],
    find_correct: ['state.checked = null; state.checkedMap = null;', 'state.checked = null;'],
    find_close_query: ["find.value = ''; find.dispatchEvent", "find.dispatchEvent"],
    find_tab: ['tree = next;', '/* no tab switch */'],
    find_late_inline: ['if (records.some(function (record)', 'if (false && records.some(function (record)'],
    find_reopen: ["if (bar && !bar.hidden && tree === active)", 'if (false)'],
    find_skipped_open: ["if (plan.some(function (r) { return r.skip; }))", "if (false)"],
    find_current: ["current = hits.findIndex(function (hit)", "current = 0; hits.findIndex(function (hit)"],
    find_search_chord: ["inSearch && (k === 'h' || k === 'f')", 'false'],
    find_existing: ["toolbar.insertAdjacentElement('afterend', bar);", "document.body.appendChild(bar);"],
    find_inline: ["            paintInline();", "            /* no inline */"],
    find_enter: ["replaceNow(!!((e.ctrlKey || e.metaKey) && e.altKey));", "return;"],
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
const model = {items:{first:{amplitude:1,note:'same'}, second:{amplitude:2,note:'same'}, third:{amplitude:1,note:'other'}},
    text:'cat scatter CAT', literal:'a.b', pointer:'#/items/first/amplitude', id:'same', full_scale_power_dbm:1,
    samples:Object.fromEntries(Array.from({length:200}, (_, i)=>['group_'+i,{value:'needle'}]))};
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
    })})};
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
w._treeReadOnly = {skip_leaves:['id'],skip_reason:'read-only'};
w.eval(fs.readFileSync(path.join(root,'quam_state_manager/web/static/search-query.js'),'utf8'));
w.eval(source);
w.ValueDelta = {paint:(el,old,next)=>{deltas.push([old,next]);el.textContent='delta';}};
const tree = d.getElementById('tree');
function render() { w.renderJsonTree('tree',model,{defaultDepth:1,crud:true}); }
function focus(p) {
    w.jsonTreeSetExpanded('tree',p.split('.').slice(0,-1).map((_,i)=>p.split('.').slice(0,i+1).join('.')));
    tree.querySelector('.tree-node[data-path="'+p+'"] > .tree-row > .tree-val').click();
    assert(d.activeElement.matches('.ui-tree-editor'));
}
function key(k,mods={}) {
    const e = new w.KeyboardEvent('keydown',{key:k,bubbles:true,cancelable:true,...mods});
    d.activeElement.dispatchEvent(e);return e;
}
function set(name,value) {
    const e=name==='find'?d.getElementById('explorer-search'):d.querySelector('#tree-find [name="'+name+'"]');
    if (e.type==='checkbox') e.checked=value;else e.value=value;
    e.dispatchEvent(new w.Event('input',{bubbles:true}));
    if(name==='find') w.TreeFind.refresh();
}
function count(n) { assert.equal(w.TreeFind.hits().length,n,'whole-model match count');assert.equal(d.querySelector('#tree-find output').textContent,n+' matches','visible whole-model count'); }
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
    set('find','needle');d.getElementById('explorer-search').focus();key('h',{ctrlKey:true});
    assert.equal(d.getElementById('explorer-search').value,'needle','reopening retains find text');key('Escape');
    focus('items.first.amplitude');assert(!key('h',{metaKey:true}).defaultPrevented,'Cmd H belongs to application');
    key('h',{ctrlKey:true});
    set('find','needle');count(200);
    assert.equal(tree.querySelectorAll('.tree-highlight').length,150,'only first 150 hits materialise');
    assert.equal(d.querySelectorAll('.ui-tree-find-result').length,150,'result page respects shared cap');
    set('replace','thread');
    w.jsonTreeSetExpanded('tree',['samples','samples.group_199']);await tick();
    assert(tree.querySelector('.tree-node[data-path="samples.group_199.value"] .ui-tree-find-inline'),'newly materialised match has inline preview');
    set('replace','');
    button('Next results').click();
    assert(d.querySelector('.ui-tree-find-results').textContent.includes('samples.group_199.value'),'unmaterialised path listed');
    set('find','cat');count(1);set('case',true);set('find','Cat');count(0);
    set('case',false);set('word',true);set('find','scatter');count(1);set('find','scat');count(0);
    set('word',false);set('regex',true);set('find','^cat.*CAT$');count(1);
    set('find','[');assert(/Invalid regex/.test(d.querySelector('#tree-find output').textContent));
    set('regex',false);set('find','a.b');count(1);set('find','cat | other');count(2);
    set('find','amplitude');count(1); // pointer text, never paths by default
    set('keys',true);count(4);set('keys',false);
    set('find','same');set('replace','changed');
    assert(tree.querySelector('.ui-tree-find-inline')?.textContent.includes('same \u2192 changed'),'inline old to new preview');
    await w.TreeFind.preview(false);
    assert.equal(d.querySelectorAll('.ui-tree-find-result').length,1,'Replace One preview');
    set('find','same');
    [...d.querySelectorAll('.ui-tree-find-result button')].find(b=>b.textContent==='items.second.note').click();
    await w.TreeFind.preview(false);assert(d.querySelector('.ui-tree-find-results').textContent.includes('items.second.note'),'Replace One follows selected match');
    await w.TreeFind.preview(true);
    assert(/read-only/.test(d.querySelector('.ui-tree-find-results').textContent),'read-only listed');
    assert(d.querySelector('#tree-find details').open,'skipped reasons are revealed');
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
    set('regex',true);set('find','.*');set('replace','invalid');await w.TreeFind.preview(true);
    const reasons=d.querySelector('.ui-tree-find-results').textContent;
    assert(/pointer/.test(reasons) && /FSP/.test(reasons) && /coerce/i.test(reasons),'all skip reasons listed including type refusal');
    // A stale preview response must never arm Apply for a newer query.
    const liveFetch=w.fetch;let release;
    w.fetch=async(url,opts)=>url==='/field/replace-preview'?new Promise(r=>{release=r;}):liveFetch(url,opts);
    const pending=w.TreeFind.preview(true);set('find','needle');
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
    w.jsonTreeSearch('wiring','');set('find','needle');count(0);
    d.querySelector('#wiring .tree-val').click();key('h',{ctrlKey:true});set('find','needle');count(0);
    set('find','wiring-only');count(1);
    if(!server) {
        set('regex',false);set('find','wiring-only');set('replace','single');
        d.querySelector('#tree-find [name=replace]').focus();key('Enter');await tick(100);
        assert.equal(posts.at(-1).updates.length,1,'Enter replaces one');
        assert.equal(posts.at(-1).updates[0].value,'single','Enter uses replacement');
        w.renderJsonTree('wiring',{value:'wiring-only'},{defaultDepth:1,crud:true});
    }
    if(!server) for(const mod of [{ctrlKey:true},{metaKey:true}]) {
        set('regex',false); set('find','wiring-only');set('replace','updated');
        d.querySelector('#tree-find [name=replace]').focus();
        key('Enter',{...mod,altKey:true});await tick(100);
        assert.equal(posts.at(-1).updates[0].dot_path,'value','replace-all chord commits');
        assert.equal(posts.at(-1).updates[0].value,'updated','replace text sent');
        w.renderJsonTree('wiring',{value:'wiring-only'},{defaultDepth:1,crud:true});
    }
    console.log('ALL OK: S6 find pins'+(server?' with server undo':''));dom.window.close();process.exit(0);
})().catch(err=>{console.error(err);dom.window.close();process.exit(1);});
