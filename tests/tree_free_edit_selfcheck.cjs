/* D-1..D-20: execute the shipped tree, never a duplicate editor. */
'use strict';
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const staticDir = path.join(__dirname, '../quam_state_manager/web/static');
let source = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8');
const mutations = {
    one: ['if (tree._editor) tree._editor.leave();', '/* mutant: leave old input */'],
    escape: ['input.value = input.defaultValue;\n                dirty();', '/* mutant: retain typing */'],
    caret: ['input.setSelectionRange(input.value.length, input.value.length);', 'input.select();'],
    flight: ['if (input.value === sent) input.value = input.defaultValue;', 'input.value = input.defaultValue;'],
    cache: ['if (!cache.has(path)) cache.set', 'if (true) cache.set'],
};
if (process.env.TREE_MUTATION) {
    const [before, after] = mutations[process.env.TREE_MUTATION];
    assert(source.includes(before), 'mutation target exists');
    source = source.replace(before, after);
}
const dom = new JSDOM('<!doctype html><input id="search"><div id="tree"></div><button id="after">After</button>',
    {runScripts:'outside-only', pretendToBeVisual:true, url:'http://localhost/explorer'});
const w = dom.window, d = w.document;
const calls = [];
let delay = 0;
w.fetch = (url, opts) => {
    calls.push({url:String(url), body: opts && opts.body});
    let data = {ok:true, expected:{}};
    if (url === '/field/edit') {
        const b = new w.URLSearchParams(opts.body);
        let v = b.get('value');
        try { v = JSON.parse(v); } catch (_) {}
        data = {ok:true, stored:v, stored_kind:typeof v};
    }
    return new Promise(resolve => setTimeout(() => resolve({ok:true, json:async()=>data}), delay));
};
w.htmx = {ajax:async()=>{}, trigger(){}, process(){}};
w.eval(fs.readFileSync(path.join(staticDir,'search-query.js'),'utf8'));
w.eval(source);
const tick = (ms=20) => new Promise(r=>setTimeout(r,ms));
const tree = d.getElementById('tree');
const node = p => tree.querySelector('.tree-node[data-path="'+p+'"]');
const val = p => node(p).querySelector(':scope > .tree-row > .tree-val');
const focus = p => node(p).querySelector(':scope > .tree-row').focus();
const key = (key,opts={}) => d.activeElement.dispatchEvent(new w.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,...opts}));
const type = text => {d.activeElement.value=text; d.activeElement.dispatchEvent(new w.Event('input',{bubbles:true}));};
(async()=>{
    w.renderJsonTree('tree',{a:1,b:'hello',branch:{amplitude:0.1}},{defaultDepth:1,crud:true});
    focus('a');
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
    focus('b'); await tick(); const before=count(); focus('a'); focus('b'); await tick();
    assert.equal(count(),before,'peek cached across visits');
    console.log('ALL OK: roving editor pins');
    w.close();
})().catch(e=>{console.error(e);process.exitCode=1;w.close();});
