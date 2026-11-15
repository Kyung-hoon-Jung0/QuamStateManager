'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..');
const source = name => fs.readFileSync(path.join(root, 'quam_state_manager/web/static', name), 'utf8');
const input = fs.readFileSync(0, 'utf8');
const dom = new JSDOM('<!doctype html><body></body>', {runScripts: 'outside-only', url: 'http://localhost/pulses'});
const w = dom.window, d = w.document;
async function main() {
    if (process.argv[2] === 'tabs') {
        d.body.innerHTML = input;
        w.eval(source('ui-menu.js'));
        const nav = d.querySelector('#pulse-channel-tabs .ui-segmented');
        assert(nav && nav.getAttribute('role') === 'group');
        const tabs = [...nav.children];
        const channels = ['', 'xy', 'z', 'resonator', 'flux', 'pair_drive', 'found'];
        assert.equal(tabs.length, channels.length);
        tabs.forEach((tab, i) => {
            assert(tab.classList.contains('ui-segmented-opt'));
            const url = new URL(tab.getAttribute('hx-get'), w.location.href);
            assert.equal(url.pathname, '/pulses');
            assert.equal(url.searchParams.get('channel') || '', channels[i]);
            assert.equal(url.searchParams.get('rows'), '1');
            assert.equal(url.searchParams.get('per_page'), '50');
            assert.equal(tab.getAttribute('hx-target'), '#pulses-rows-wrap');
            assert.equal(tab.getAttribute('hx-include'), "[name='q']");
            assert.equal(tab.getAttribute('onclick'), 'pulseTabActive(this)');
            assert.equal(new URL(tab.href).searchParams.get('channel') || '', channels[i]);
            if (i) assert.equal(new URL(tab.href).searchParams.get('q'), 'probe');
        });
        assert.equal(tabs[0].getAttribute('href'), '/pulses', 'the All tab carries neither channel nor query');
        assert.equal(tabs[0].getAttribute('hx-get'), '/pulses?rows=1&per_page=50');
        assert.equal(new URL(tabs[1].href).searchParams.get('q'), 'probe');
        assert.equal(nav.querySelector('.ui-segmented-on'), tabs[1]);
        const clicks = [];
        nav.addEventListener('click', e => { clicks.push(e.target); e.preventDefault(); });
        tabs[1].focus();
        for (const [key, index] of [['ArrowRight', 2], ['End', 6], ['ArrowRight', 0], ['Home', 0], ['ArrowLeft', 6]]) {
            d.activeElement.dispatchEvent(new w.KeyboardEvent('keydown', {key, bubbles:true}));
            assert.equal(d.activeElement, tabs[index]);
            assert.equal(nav.querySelector('.ui-segmented-on'), tabs[index]);
            assert.equal(nav.querySelector('[aria-current="true"]'), tabs[index]);
            assert.equal(nav.querySelector('a.active'), tabs[index]);
            assert.equal(clicks.at(-1), tabs[index]);
        }
        // selected state: links that navigate carry aria-current, never aria-pressed
        tabs.forEach((tab, i) => {
            assert.equal(tab.hasAttribute('aria-pressed'), false);
            assert.equal(tab.getAttribute('aria-current'), i === 6 ? 'true' : null);
        });
        // a modifier leaves the key to the browser: nothing moves, nothing is prevented
        tabs[6].focus();
        const before = clicks.length;
        for (const mod of ['altKey', 'ctrlKey', 'metaKey', 'shiftKey']) {
            for (const key of ['ArrowLeft', 'ArrowRight', 'Home', 'End']) {
                const ev = new w.KeyboardEvent('keydown', {key, bubbles: true, cancelable: true, [mod]: true});
                tabs[6].dispatchEvent(ev);
                assert.equal(ev.defaultPrevented, false, mod + key);
                assert.equal(d.activeElement, tabs[6]);
            }
        }
        assert.equal(clicks.length, before);
        assert.equal(nav.querySelector('.ui-segmented-on'), tabs[6]);
        // Space activates the focused option and is prevented; Enter is left to the browser
        tabs[2].focus();
        const sp = new w.KeyboardEvent('keydown', {key: ' ', bubbles: true, cancelable: true});
        tabs[2].dispatchEvent(sp);
        assert.equal(sp.defaultPrevented, true);
        assert.equal(clicks.at(-1), tabs[2]);
        assert.equal(nav.querySelector('.ui-segmented-on'), tabs[2]);
        assert.equal(tabs[2].getAttribute('aria-current'), 'true');
        assert.equal(tabs[6].hasAttribute('aria-current'), false);
        const n = clicks.length;
        const en = new w.KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true});
        tabs[2].dispatchEvent(en);
        assert.equal(en.defaultPrevented, false);
        assert.equal(clicks.length, n);
        const css = source('ui-kit.css');
        assert(css.includes('.ui-segmented.ui-segmented > .ui-segmented-opt:focus-visible'));
        assert(css.includes('outline-offset: var(--ui-focus-inset)'));
        assert(css.includes('outline: var(--ui-focus-w) solid var(--ui-accent)'));
    } else if (process.argv[2] === 'tabs_min') {
        d.body.innerHTML = input;
        const nav = d.querySelector('#pulse-channel-tabs .ui-segmented');
        const tabs = [...nav.children];
        assert.deepEqual(tabs.map(t => t.textContent), ['All', 'XY', 'Z', 'Resonator'], 'optional tabs are absent when the chip lacks them');
        assert.equal(nav.querySelector('.ui-segmented-on'), tabs[0], 'no channel selects All');
        assert.equal(nav.querySelectorAll('[aria-current="true"]').length, 1);
        assert.deepEqual(tabs.map(t => t.getAttribute('href')), ['/pulses', '/pulses?channel=xy', '/pulses?channel=z', '/pulses?channel=resonator'],
            'without a query no q parameter is appended');
    } else if (process.argv[2] === 'segmented') {
        w.eval(source('ui.js'));
        const group = w.eval("UI.segmented([{label:'A', href:'/a', selected:true}, {label:'B', href:'/b'}, {label:'C', selected:true}, {label:'D'}, {label:'E', command:true}], 'probe')");
        const [a, b, c, dd, e] = [...group.children];
        assert.equal(a.tagName, 'A');
        assert.equal(a.getAttribute('aria-current'), 'true');
        assert.equal(a.hasAttribute('aria-pressed'), false);
        assert.equal(b.hasAttribute('aria-current'), false);
        assert.equal(b.hasAttribute('aria-pressed'), false);
        assert.equal(c.tagName, 'BUTTON');
        assert.equal(c.getAttribute('aria-pressed'), 'true');
        assert.equal(c.hasAttribute('aria-current'), false);
        assert.equal(dd.getAttribute('aria-pressed'), 'false');
        assert.equal(dd.hasAttribute('aria-current'), false);
        assert.equal(e.hasAttribute('aria-pressed'), false);
        // switching the selection moves the attribute (links) without ever adding aria-pressed
        d.body.appendChild(group);
        w.eval(source('ui-menu.js'));
        b.dispatchEvent(new w.MouseEvent('click', {bubbles: true, cancelable: true}));
        assert.equal(b.getAttribute('aria-current'), 'true');
        assert.equal(a.hasAttribute('aria-current'), false);
        assert.equal(b.hasAttribute('aria-pressed') || a.hasAttribute('aria-pressed'), false);

        // request attributes reach the element; a selected link also carries the link class `active`
        const names = el => [...el.attributes].map(x => x.name).sort().join(',');
        const g2 = w.eval("UI.segmented([{label:'L1', href:'/l1', selected:true, attrs:{'data-probe':'1', 'hx-get':'/x'}}, {label:'L2', href:'/l2', attrs:{'data-probe':'2'}}, {label:'B1', selected:true, attrs:{'data-probe':'3'}}, {label:'B2'}], 'probe2')");
        d.body.appendChild(g2);
        const [l1, l2, b1, b2] = [...g2.children];
        assert.equal(l1.getAttribute('data-probe'), '1');
        assert.equal(l1.getAttribute('hx-get'), '/x');
        assert.equal(l2.getAttribute('data-probe'), '2');
        assert.equal(b1.getAttribute('data-probe'), '3');
        assert.equal(l1.getAttribute('href'), '/l1');
        assert.equal(l1.className, 'ui-segmented-opt ui-segmented-on active');
        assert.equal(l2.className, 'ui-segmented-opt');
        assert.equal(b1.className, 'ui-segmented-opt ui-segmented-on', 'a selected button never gets the link class');
        assert.equal(b2.className, 'ui-segmented-opt');
        assert.equal(names(l1), 'aria-current,class,data-probe,href,hx-get');
        assert.equal(names(l2), 'class,data-probe,href', 'a link has no type and no aria state when not selected');
        assert.equal(names(b1), 'aria-pressed,class,data-probe,type');
        assert.equal(names(b2), 'aria-pressed,class,type', 'an option without attrs gets no stray attribute');
        assert.equal(b1.getAttribute('type'), 'button');
        // switching moves `active` and the selected class between links
        l2.dispatchEvent(new w.MouseEvent('click', {bubbles: true, cancelable: true}));
        assert.equal(l2.className, 'ui-segmented-opt ui-segmented-on active');
        assert.equal(l1.className, 'ui-segmented-opt', 'the previous link loses `active` and the selected class');
        assert.equal(l2.getAttribute('aria-current'), 'true');
        assert.equal(l1.hasAttribute('aria-current'), false);
        // a modified click still marks the link selected (the browser decides where it opens)
        l1.dispatchEvent(new w.MouseEvent('click', {bubbles: true, cancelable: true, ctrlKey: true}));
        assert.equal(l1.className, 'ui-segmented-opt ui-segmented-on active');
        assert.equal(l2.className, 'ui-segmented-opt');
        // button options: the handler never selects them by click or key, and selectSegment sets aria-pressed only
        const g3 = w.eval("UI.segmented([{label:'P', selected:true}, {label:'Q'}], 'buttons')");
        d.body.appendChild(g3);
        const [p, q] = [...g3.children];
        q.dispatchEvent(new w.MouseEvent('click', {bubbles: true, cancelable: true}));
        assert.equal(q.className, 'ui-segmented-opt');
        assert.equal(q.getAttribute('aria-pressed'), 'false');
        p.focus();
        const bk = new w.KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true, cancelable: true});
        p.dispatchEvent(bk);
        assert.equal(bk.defaultPrevented, false, 'a button option keeps the browser key handling');
        assert.equal(d.activeElement, p);
        w.UIMenu.selectSegment(q);
        assert.equal(q.className, 'ui-segmented-opt ui-segmented-on');
        assert.equal(q.getAttribute('aria-pressed'), 'true');
        assert.equal(p.className, 'ui-segmented-opt');
        assert.equal(p.getAttribute('aria-pressed'), 'false');
        assert.equal(names(q), 'aria-pressed,class,type', 'a button never gets aria-current');
        // degenerate calls change nothing and do not throw
        const stray = d.createElement('button'); stray.className = 'ui-segmented-opt';
        d.body.appendChild(stray);
        w.UIMenu.selectSegment(stray);
        w.UIMenu.selectSegment(null);
        assert.equal(q.className, 'ui-segmented-opt ui-segmented-on');
        // keys aimed at the document, a plain element or a non-navigation key are ignored
        for (const t of [d, d.body]) {
            const ev = new w.KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true, cancelable: true});
            t.dispatchEvent(ev);
            assert.equal(ev.defaultPrevented, false);
        }
        l1.focus();
        for (const key of ['ArrowUp', 'ArrowDown', 'a', 'Enter', 'Tab']) {
            const ev = new w.KeyboardEvent('keydown', {key, bubbles: true, cancelable: true});
            l1.dispatchEvent(ev);
            assert.equal(ev.defaultPrevented, false, key);
            assert.equal(d.activeElement, l1, key);
        }
        // a click on something that is not an option leaves every option alone
        d.body.dispatchEvent(new w.MouseEvent('click', {bubbles: true, cancelable: true}));
        assert.equal(l1.className, 'ui-segmented-opt ui-segmented-on active');
    } else if (process.argv[2] === 'columns') {
        const [paged, virtual] = JSON.parse(input);
        d.body.innerHTML = paged;
        assert.deepEqual([...d.querySelectorAll('thead th')].map((th,i) => th.classList.contains('ui-col-secondary') ? i : -1).filter(i => i >= 0), [4,5]);
        const check = row => {
            assert.equal(row.cells.length, 9);
            assert.deepEqual([...row.cells].map((td,i) => td.classList.contains('ui-col-secondary') ? i : -1).filter(i => i >= 0), [4,5]);
            assert(row.cells[3].textContent.includes('probe_operation_name'));
            assert(row.getAttribute('hx-get').startsWith('/pulse/detail?path='));
        };
        check(d.querySelector('tr[data-pulse-path]'));
        d.body.innerHTML = '<table><tbody>' + virtual + '</tbody></table>';
        check(d.querySelector('tr'));
    } else if (process.argv[2] === 'widths') {
        d.body.innerHTML = '<table id="pulses-table"><thead><tr><th>Name</th><th class="ui-col-secondary">Class</th><th>Amp</th></tr></thead></table>';
        let callback;
        w.ResizeObserver = class { constructor(cb) { callback = cb; } observe() {} };
        w.requestAnimationFrame = cb => { cb(); return 0; };
        const app = source('app.js');
        const start = app.indexOf('window.enhanceColumnResize = function');
        w.eval(app.slice(start, app.indexOf('\n};', start) + 3));
        const table = d.querySelector('table'), heads = [...d.querySelectorAll('th')];
        let natural = [120,200,80];
        const layoutAtMeasure = [];
        heads.forEach((th,i)=>Object.defineProperty(th,'offsetWidth',{get:()=>{ layoutAtMeasure.push(table.style.tableLayout); return natural[i]; }}));
        w.localStorage.setItem('widths', JSON.stringify({2:90}));
        w.enhanceColumnResize('pulses-table', 'widths');
        assert.equal(table.style.width, '410px');
        assert.equal(table.style.tableLayout, 'fixed', 'a priority column table is fixed after the first fit');
        layoutAtMeasure.length = 0;
        callback();
        assert(layoutAtMeasure.length > 0, 'a refit measures the natural column widths');
        assert(layoutAtMeasure.every(v => v === ''), 'columns are measured in the automatic layout, got ' + layoutAtMeasure.join('|'));
        assert.equal(table.style.tableLayout, 'fixed', 'the fixed layout returns after the refit');
        heads[1].style.display = 'none';
        natural = [180,0,80];
        callback();
        assert.equal(table.style.width, '270px', 'hidden columns do not contribute saved or frozen width');
        assert.equal(heads[0].style.width, '180px');
        assert.equal(heads[2].style.width, '90px', 'manual widths survive');
        heads[1].style.display = '';
        natural = [120,200,80];
        callback();
        assert.equal(table.style.width, '410px', 'wide columns return');
    } else if (process.argv[2] === 'widths-unsaved') {
        d.body.innerHTML = '<table id="pulses-table"><thead><tr><th>Name</th><th class="ui-col-secondary">Class</th><th>Amp</th></tr></thead></table>';
        let callback;
        w.ResizeObserver = class { constructor(cb) { callback = cb; } observe() {} };
        w.requestAnimationFrame = cb => { cb(); return 0; };
        const app = source('app.js');
        const start = app.indexOf('window.enhanceColumnResize = function');
        w.eval(app.slice(start, app.indexOf('\n};', start) + 3));
        const table = d.querySelector('table'), heads = [...d.querySelectorAll('th')];
        let natural = [120,200,80];
        heads.forEach((th,i)=>Object.defineProperty(th,'offsetWidth',{get:()=>natural[i]}));
        assert.equal(w.localStorage.getItem('widths-unsaved'), null, 'fixture: no saved width');
        w.enhanceColumnResize('pulses-table', 'widths-unsaved');
        assert.equal(heads[0].style.width, '120px');
        natural = [100,150,60];
        callback();
        assert.equal(table.style.width, '310px', 'a priority column table with no saved width follows its box');
        assert.equal(heads[0].style.width, '100px');
        assert.equal(table.style.tableLayout, 'fixed', 'the fixed layout returns after the refit');
    } else if (process.argv[2] === 'map') {
        d.body.innerHTML = '<div id="czp-root"><div id="czp-map"></div></div>';
        const host = d.querySelector('#czp-root'), el = d.querySelector('#czp-map');
        Object.defineProperty(el, 'clientWidth', {value: 280});
        let rendered;
        w.requirePlotly = () => Promise.resolve();
        w._plotlyRender = (node, traces, layout) => { rendered = {traces,layout}; return Promise.resolve(); };
        w.PlotTheme = {houseLayout:l=>l, houseConfig:c=>c, palette:()=>['gray']};
        w.eval(source('cz-map.js'));
        const names = Array.from({length:15}, (_,i) => 'q' + i);
        const pair = names[0] + '-' + names[1];
        host._czMap = {ok:true, qubits:names.map((name,i)=>({name, path:'qubits.'+name, f01_hz:4e9+i*1e7, defects:[], safe:[]})),
            segments:[0,1].map(i=>({pair, operation:'cz', path:'qubit_pairs.'+pair+'.macros.cz', role:i?'target':'control',
                qubit:names[i], qubit_path:'qubits.'+names[i], frequency_hz:4e9+i*1e8, moves:true, kind:'estimate'})), refused:[], unavailable:[], missing:[], planned:[]};
        await w.CzMap.paint(host);
        assert(rendered);
        assert.deepEqual(Array.from(rendered.layout.xaxis.ticktext), names);
        const labels = rendered.traces.filter(t=>t.meta==='partners').flatMap(t=>t.text).join(' ');
        assert(labels.includes(names[0]) && labels.includes(names[1]), labels);
        const segments = rendered.traces.filter(t=>t.meta==='seg');
        assert(segments.some(t=>JSON.stringify(t).includes(pair)));
        const nameW = Math.max(...names.map(x => w.CzMap.partnerWidth(x))), mg = rendered.layout.margin;
        const expected = Math.max(240, 280 - mg.l - mg.r, names.length * (nameW + 8) / 0.76) + mg.l + mg.r;
        assert(expected > 280 + 100, 'fixture: the width rule, not the box, decides');
        assert(Math.abs(rendered.layout.width - expected) < 1e-6, 'plot width follows n * (widest name + 8) / 0.76: ' + rendered.layout.width + ' vs ' + expected);
        const template = fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_cz_plan.html'), 'utf8');
        assert(template.includes('czp-map-view ui-xscroll'));
    }
}
main().then(()=>{dom.window.close(); console.log('ALL OK');}, e=>{dom.window.close(); console.error(e); process.exitCode=1;});
