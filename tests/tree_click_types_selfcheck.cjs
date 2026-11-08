'use strict';
const assert = require('assert/strict'), fs = require('fs'), {JSDOM} = require('jsdom');
let src = fs.readFileSync('quam_state_manager/web/static/app.js', 'utf8');
const mutations = {
  undo_file: ['if (present && ["explorer-tree-state", "explorer-tree-wiring"].some', 'if (false && ["explorer-tree-state", "explorer-tree-wiring"].some'],
  focus: ['el.onmousedown = function(e) { e.preventDefault(); };', 'void 0;'],
  delay: ['}, 250);', '}, 0);'],
  open: ['_makeContainerEditable(nd, p, nd._value);', 'void 0;'],
  cancel: ['if (t) { clearTimeout(t); t = null; }', 'void 0;'],
  copy: ['_treeCopyKey(nd);', 'void 0;'],
  arrow: ['_toggleNode(node);', '_makeContainerEditable(node, path, node._value);'],
  quoted: ['var meansTypeChange = unwrapped && !isStringKind;', 'var meansTypeChange = false;'],
  quoted_flag: ['if (unwrapped) body.append("value_quoted", "1");', 'void 0;'],
  transport: ['body.append("tree_kind_change", "1");', 'void 0;'],
};
if (process.env.JT_MUTATION) {
  const [a,b] = mutations[process.env.JT_MUTATION]; assert(src.includes(a)); src = src.replaceAll(a,b);
}
const dom = new JSDOM('<div id="explorer-tree-state" class="json-tree"></div><div id="explorer-tree-wiring" class="json-tree"></div>', {runScripts:'outside-only', pretendToBeVisual:true, url:'http://localhost/explorer'});
const w = dom.window;
w.htmx = {ajax:()=>Promise.resolve(), process:()=>{}, trigger:()=>{}};
w.fetch = ()=>Promise.resolve({json:()=>Promise.resolve({ok:true, expected:{}})});
w.ResizeObserver = w.IntersectionObserver = class {observe(){} unobserve(){} disconnect(){}};
w.eval(src);
let now=0, next=1; const timers = new Map();
w.setTimeout=(fn,ms)=>{const id=next++;timers.set(id,{fn,at:now+(ms||0)});return id;};
w.clearTimeout=id=>timers.delete(id);
function advance(ms){now+=ms; for(const [id,t] of [...timers]) if(t.at<=now){timers.delete(id);t.fn();}}
const opens=[]; w.TreeJsonEdit={open:o=>opens.push(o)}; w.SmCodeMirror={};
w.renderJsonTree('explorer-tree-state', {a:3, branch:{child:1}}, {defaultDepth:2, crud:true});
const node=p=>w.document.querySelector('.tree-node[data-path="'+p+'"]');
const key=p=>node(p).querySelector(':scope > .tree-row > .tree-key');
const down=new w.MouseEvent('mousedown',{bubbles:true,cancelable:true});key('a').dispatchEvent(down);assert(down.defaultPrevented,'key prevents row focus mounting scalar input');
key('a').click();advance(249);assert.equal(opens.length,0,'single click waits');
advance(1);assert.equal(opens[0].path,'a','single click opens per-key JSON');
opens.length=0;
key('a').click();advance(50);key('a').click();key('a').dispatchEvent(new w.MouseEvent('dblclick',{bubbles:true}));advance(300);
assert.equal(opens.length,0,'double click cancels pending editor');
assert.match(w.document.getElementById('tree-copy-pill')?.textContent || '', /a/,'double click keeps value copy');
const arrow=node('branch').querySelector(':scope > .tree-row > .tree-toggle');
const before=arrow.className;arrow.click();advance(300);
assert.equal(opens.length,0,'arrow opens no editor');assert.notEqual(arrow.className,before,'arrow expands only');
// Exercise inline commit with a quoted numeric literal, guarding its type-intent transport.
(async()=>{
let body;
w.fetch=(url,opts)=>{if(url==='/field/edit') body=new URLSearchParams(opts.body); return Promise.resolve({json:()=>Promise.resolve({ok:true, stored:'3',stored_kind:'str'})});};
const val=node('a').querySelector(':scope > .tree-row > .tree-val');val.click();
const input=val.querySelector('input'); input.value='"3"';input.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
await new Promise(r=>setImmediate(r));
assert.equal(body?.get('tree_kind_change'),'1','inline commits permit type changes');
assert.equal(body?.get('value_quoted'),'1','quoted number remains a string');
w.fetch=()=>Promise.resolve({json:()=>Promise.resolve({ok:true,values:{a:null},errors:{}})});
w.renderJsonTree('explorer-tree-wiring',{}, {crud:true});
w.renderJsonTree('explorer-tree-state',{a:[1,2]}, {defaultDepth:2,crud:true});
w.document.dispatchEvent(new w.CustomEvent('cellsReverted',{detail:{entries:[{dot_path:'a',old_kind:'null',old_value_str:''}]}}));
await new Promise(r=>setImmediate(r));
assert.equal(node('a')._value,null,'undo event restores container as null');
console.log('10 assertions passed');dom.window.close();
})().catch(e=>{console.error(e);process.exit(1);});
