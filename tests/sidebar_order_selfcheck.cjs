'use strict';
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/sidebar-order.js'), 'utf8');
const KEY = 'quam_sidebar_order_v1';
function setup(saved, extra = '') {
    const dom = new JSDOM(`<aside id="sidebar"><nav class="sidebar-nav"><ul data-nav-group="main"><li data-nav-key="a"><a href="#a">A</a><ul data-nav-group="sub"><li data-nav-key="s"><a href="#s">S</a></li><li data-nav-key="t"><a href="#t">T</a></li></ul></li>${extra}<li data-nav-key="b"><a href="#b">B</a></li><li data-nav-key="c"><a href="#c">C</a></li></ul><ul data-nav-group="other"><li data-nav-key="d"><a href="#d">D</a></li></ul></nav></aside><div id="settings-dropdown"><button id="reset-menu-order">Reset menu order</button></div>`, {url:'http://localhost', pretendToBeVisual:true});
    const w = dom.window;
    for (const key of ['window','document','localStorage','Event','MouseEvent','KeyboardEvent','CustomEvent']) global[key] = key === 'window' ? w : w[key];
    if (saved !== undefined) localStorage.setItem(KEY, saved);
    new Function(source)();
    const group = document.querySelector('[data-nav-group="main"]');
    const row = key => document.querySelector(`[data-nav-key="${key}"]`);
    const order = () => Array.from(group.children, el => el.dataset.navKey).join('');
    const pointer = (type, target, x=0, y=0) => target.dispatchEvent(new w.MouseEvent(type, {bubbles:true,cancelable:true,button:0,clientX:x,clientY:y}));
    const alt = (key, direction) => row(key).querySelector('a').dispatchEvent(new w.KeyboardEvent('keydown',{key:direction,altKey:true,bubbles:true,cancelable:true}));
    return {w, row, order, pointer, alt};
}
function check(name, fn) { fn(); console.log('ok - '+name); }
let h = setup();
check('plain click still navigates', () => {
    h.pointer('pointerdown', h.row('a').querySelector('a'));
    h.pointer('pointerup', h.row('a'));
    assert.equal(h.row('a').querySelector('a').dispatchEvent(new h.w.MouseEvent('click',{bubbles:true,cancelable:true})), true);
});
check('drag threshold, indicator, reorder and persistence', () => {
    h.pointer('pointerdown',h.row('a'));
    h.pointer('pointermove',h.row('c'),0,3);
    assert.equal(document.querySelector('.ui-reorder-dragging'),null);
    h.pointer('pointermove',h.row('c'),0,10);
    assert(h.row('c').classList.contains('ui-reorder-after'));
    h.pointer('pointerup',h.row('c'),0,10);
    assert.equal(h.order(),'bca');
    assert.deepEqual(JSON.parse(localStorage.getItem(KEY)).main,['b','c','a']);
    assert.equal(h.row('a').querySelector('a').dispatchEvent(new h.w.MouseEvent('click',{bubbles:true,cancelable:true})), false);
    assert(h.row('a').querySelector('[data-nav-group="sub"]'));
});
const saved = localStorage.getItem(KEY);
check('reload applies saved order', () => { h=setup(saved); assert.equal(h.order(),'bca'); });
check('new item keeps default slot', () => { h=setup(saved,'<li data-nav-key="n"><a href="#n">N</a></li>'); assert.equal(h.order(),'bnca'); });
check('Alt+Arrow and default order is not stored', () => {
    h=setup(); h.alt('b','ArrowUp'); assert.equal(h.order(),'bac');
    h.alt('b','ArrowDown'); assert.equal(h.order(),'abc'); assert.equal(localStorage.getItem(KEY),null);
    h.alt('t','ArrowUp'); assert.equal(h.row('t').parentElement.firstElementChild,h.row('t')); assert.equal(h.order(),'abc');
});
check('Escape cancels drag', () => {
    h=setup(); h.pointer('pointerdown',h.row('a')); h.pointer('pointermove',h.row('c'),0,10);
    document.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    h.pointer('pointerup',h.row('c')); assert.equal(h.order(),'abc'); assert.equal(localStorage.getItem(KEY),null);
    assert.equal(document.querySelector('.ui-reorder-after'),null);
    assert.equal(h.row('a').querySelector('a').dispatchEvent(new h.w.MouseEvent('click',{bubbles:true,cancelable:true})), false);
});
check('a point over an expanded sub-menu targets its parent item', () => {
    h=setup(); h.pointer('pointerdown',h.row('c')); h.pointer('pointermove',h.row('s'),0,10);
    assert(h.row('a').classList.contains('ui-reorder-after')); h.pointer('pointerup',h.row('s'),0,10);
    assert.equal(h.order(),'acb'); assert.equal(h.row('s').parentElement.dataset.navGroup,'sub');
});
check('cannot drag across groups', () => {
    h=setup(); h.pointer('pointerdown',h.row('a')); h.pointer('pointermove',h.row('d'),0,10); h.pointer('pointerup',h.row('d')); assert.equal(h.order(),'abc');
});
check('reset restores defaults and clears storage', () => {
    h=setup(saved); document.getElementById('reset-menu-order').click(); assert.equal(h.order(),'abc'); assert.equal(localStorage.getItem(KEY),null);
});
check('corrupt storage ignored', () => {
    for(const value of ['{','null','[]','{"main":42}','{"main":["b","b"]}','{"main":[2]}']) {h=setup(value);assert.equal(h.order(),'abc');}
});
check('sidebar swap applies saved order', () => {
    h=setup(); localStorage.setItem(KEY,saved);
    document.querySelector('[data-nav-group="main"]').outerHTML='<ul data-nav-group="main"><li data-nav-key="a"><a>A</a></li><li data-nav-key="b"><a>B</a></li><li data-nav-key="c"><a>C</a></li></ul>';
    document.dispatchEvent(new h.w.CustomEvent('htmx:afterSwap',{bubbles:true}));
    assert.equal(Array.from(document.querySelector('[data-nav-group="main"]').children,el=>el.dataset.navKey).join(''),'bca');
});
check('storage access errors ignored', () => {
    h=setup(); Object.defineProperty(h.w,'localStorage',{get(){throw new Error('blocked');}});
    Object.defineProperty(global,'localStorage',{configurable:true,get(){throw new Error('blocked');}});
    h.alt('b','ArrowUp'); assert.equal(h.order(),'bac'); document.getElementById('reset-menu-order').click(); assert.equal(h.order(),'abc');
});
// Top-level groups share a zone: an item alone between dividers still moves.
function zone(saved) {
    const dom = new JSDOM(`<aside id="sidebar"><ul data-nav-group="projects" data-nav-zone="top"><li data-nav-key="p"><a href="#p">P</a></li></ul><hr><ul data-nav-group="creation" data-nav-zone="top"><li data-nav-key="g"><a href="#g">G</a><ul data-nav-group="gsub"><li data-nav-key="v"><a>V</a></li></ul></li></ul><hr><ul data-nav-group="main" data-nav-zone="top"><li data-nav-key="a"><a href="#a">A</a></li><li data-nav-key="b"><a href="#b">B</a></li></ul></aside><button id="reset-menu-order">Reset</button>`, {url:'http://localhost', pretendToBeVisual:true});
    const w = dom.window;
    for (const key of ['window','document','localStorage','Event','MouseEvent','KeyboardEvent','CustomEvent']) Object.defineProperty(global, key, {configurable:true, writable:true, value: key === 'window' ? w : w[key]});
    if (saved !== undefined) localStorage.setItem(KEY, saved);
    new Function(source)();
    const row = key => document.querySelector(`[data-nav-key="${key}"]`);
    const order = () => ['projects','creation','main'].map(g => Array.from(document.querySelector(`[data-nav-group="${g}"]`).children, el => el.dataset.navKey).join('')).join('|');
    const pointer = (type, target, x=0, y=0) => target.dispatchEvent(new w.MouseEvent(type, {bubbles:true,cancelable:true,button:0,clientX:x,clientY:y}));
    const alt = (key, direction) => row(key).querySelector('a').dispatchEvent(new w.KeyboardEvent('keydown',{key:direction,altKey:true,bubbles:true,cancelable:true}));
    return {w, row, order, pointer, alt};
}
let zsaved;
check('a lone top-level item drags into another top-level group', () => {
    const z = zone(); assert.equal(z.order(), 'p|g|ab');
    z.pointer('pointerdown', z.row('g')); z.pointer('pointermove', z.row('a'), 0, 10); z.pointer('pointerup', z.row('a'), 0, 10);
    assert.equal(z.order(), 'p||agb'); assert(z.row('g').querySelector('[data-nav-group="gsub"]'));
    zsaved = localStorage.getItem(KEY);
    assert.deepEqual(JSON.parse(zsaved), {projects:['p'], creation:[], main:['a','g','b']});
});
check('the cross-group order survives a reload and a reset restores it', () => {
    const z = zone(zsaved); assert.equal(z.order(), 'p||agb');
    document.getElementById('reset-menu-order').click(); assert.equal(z.order(), 'p|g|ab'); assert.equal(localStorage.getItem(KEY), null);
});
check('Alt+Arrow at a group edge steps into the next top-level group and back', () => {
    const z = zone(); z.alt('g','ArrowDown'); assert.equal(z.order(), 'p||gab');
    z.alt('g','ArrowUp'); assert.equal(z.order(), 'p|g|ab'); assert.equal(localStorage.getItem(KEY), null);
    z.alt('v','ArrowUp'); assert.equal(z.row('v').parentElement.dataset.navGroup, 'gsub');
});
// Execute in the browser realm while the parser is still in the head.
const early = new JSDOM(`<!doctype html><head><script>${source}</script></head><body><aside id="sidebar"><ul data-nav-group="main"><li data-nav-key="a"><a>A</a></li><li data-nav-key="b"><a>B</a></li></ul></aside><main></main></body>`, {
    url:'http://localhost', runScripts:'dangerously',
    beforeParse(w) { w.localStorage.setItem(KEY, JSON.stringify({main:['b','a']})); }
});
setImmediate(() => {
    check('head core script restores order during parsing', () => {
        assert.equal(early.window.document.querySelector('[data-nav-group]').firstElementChild.dataset.navKey, 'b');
    });
    early.window.close();
});
