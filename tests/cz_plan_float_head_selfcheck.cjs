'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
let checks = 0;
function check(value, message) { checks++; assert.ok(value, message); }
(async () => {
    const html = fs.readFileSync(process.argv[2], 'utf8');
    const dom = new JSDOM('<!doctype html><div id="table-pane" style="overflow-y:auto">' + html + '</div>',
        {url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true});
    const w = dom.window, d = w.document;
    const pane = d.getElementById('table-pane'), table = d.getElementById('czp-table');
    const wrap = table.parentElement, head = table.tHead;
    const columns = Array.from(head.querySelectorAll('th'));
    // Include identifiers at every depth to exercise sanitizing the deep clone.
    head.id = 'original-head';
    columns[0].id = 'original-pair';
    columns[0].querySelector('button').id = 'original-sort';
    let paneTop = 84, headBottom = 180, tableBottom = 900, tableWidth = 1600, cellExtra = 0;
    const rect = (left, top, width, height) => ({left, top, width, height, right: left + width, bottom: top + height});
    pane.getBoundingClientRect = () => rect(0, paneTop, 1000, 600);
    wrap.getBoundingClientRect = () => rect(24, headBottom - 40, 950, tableBottom - headBottom + 40);
    Object.defineProperty(wrap, 'clientWidth', {value: 950});
    table.getBoundingClientRect = () => rect(24, headBottom - 40, tableWidth, tableBottom - headBottom + 40);
    head.getBoundingClientRect = () => rect(24, headBottom - 40, tableWidth, 40);
    columns.forEach((th, i) => { th.getBoundingClientRect = () => rect(24, headBottom - 40, 90 + i * 10 + cellExtra, 40); });
    const nativeRect = w.Element.prototype.getBoundingClientRect;
    w.Element.prototype.getBoundingClientRect = function () {
        return this.closest('.czp-float-head') ? rect(24, paneTop, tableWidth, 40) : nativeRect.call(this);
    };
    let frames = new Map(), nextFrame = 0;
    w.requestAnimationFrame = callback => { frames.set(++nextFrame, callback); return nextFrame; };
    w.cancelAnimationFrame = id => frames.delete(id);
    function flush() { const pending = frames; frames = new Map(); pending.forEach(callback => callback()); }
    const observers = [];
    w.ResizeObserver = class {
        constructor(callback) { this.callback = callback; this.targets = []; observers.push(this); }
        observe(target) { this.targets.push(target); }
        disconnect() { this.disconnected = true; }
    };
    w.fetch = () => Promise.resolve({json: () => Promise.resolve({ok: false, error: 'stub'}), text: () => Promise.resolve('')});
    w.eval(fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/cz-plan.js'), 'utf8'));
    w.CzPlan.mount();
    await new Promise(resolve => w.setTimeout(resolve, 0));
    flush();
    const floating = d.querySelector('.czp-float-head');
    check(floating, 'floating header exists');
    const copy = floating.querySelector('table');
    check(floating.getAttribute('aria-hidden') === 'true', 'clone is hidden from accessibility tools');
    check(floating.parentElement === wrap.parentElement && floating === wrap.parentElement.lastElementChild &&
        !!(wrap.compareDocumentPosition(floating) & w.Node.DOCUMENT_POSITION_FOLLOWING), 'clone is the last sibling after the wrap');
    check(copy.className === table.className, 'table classes match');
    check(!floating.querySelector('[id]'), 'all cloned identifiers are stripped');
    check(new Set(Array.from(d.querySelectorAll('[id]'), el => el.id)).size === d.querySelectorAll('[id]').length, 'no duplicate identifiers');
    check(d.querySelectorAll('th[data-sort-key]').length === head.querySelectorAll('th[data-sort-key]').length &&
        d.querySelector('th[data-sort-key="pair"]') === columns[0], 'original sort keys remain unique');
    check(Array.from(floating.querySelectorAll('button')).every(button => button.tabIndex === -1), 'cloned buttons are outside tab order');
    check(floating.hidden, 'hidden while the real header is visible');
    function scroll() { pane.dispatchEvent(new w.Event('scroll')); flush(); }
    headBottom = 70;
    scroll();
    check(!floating.hidden, 'shown when the real header is above the pane');
    check(floating.style.top === '84px' && floating.style.left === '24px' && floating.style.width === '950px', 'overlay matches the pane top and wrap visible width');
    check(copy.style.width === '1600px', 'copy matches the complete table width');
    d.documentElement.style.setProperty('--topbar-height', '96px');
    scroll();
    check(floating.style.top === '96px', 'top is clamped to the root topbar height');
    d.documentElement.style.removeProperty('--topbar-height');
    scroll();
    function checkWidths() {
        check(Array.from(copy.querySelectorAll('th')).every((th, i) =>
            ['width', 'minWidth', 'maxWidth'].every(key => th.style[key] === (90 + i * 10 + cellExtra) + 'px')), 'all column widths match the originals');
    }
    checkWidths();
    wrap.scrollLeft = 120;
    wrap.dispatchEvent(new w.Event('scroll'));
    check(copy.style.transform === 'translateX(-120px)', 'horizontal offset follows the sole scrollbar');
    check(Array.from(copy.querySelectorAll('.czp-stick')).every(th => th.style.transform === 'translateX(120px)'), 'sticky columns counter the horizontal offset');
    copy.querySelector('[data-czp-float-key="delta"] button').click();
    check(head.querySelector('[data-sort-key="delta"]').getAttribute('aria-sort') === 'ascending', 'cloned click sorts the original');
    check(copy.querySelector('[data-czp-float-key="delta"]').getAttribute('aria-sort') === 'ascending', 'clone reflects the forwarded sort');
    const values = Array.from(table.tBodies[0].rows, row => row.getAttribute('data-s-delta')).filter(value => value !== '').map(Number);
    check(values.length >= 2 && values.every((value, i) => !i || values[i - 1] <= value), 'forwarded sort orders original rows numerically');
    head.querySelector('[data-sort-key="pair"] button').click();
    await Promise.resolve();
    check(copy.querySelector('[data-czp-float-key="pair"]').getAttribute('aria-sort') === 'ascending', 'observer mirrors sorts initiated on the original');
    const resize = observers.find(observer => [table, wrap, head].every(el => observer.targets.includes(el)));
    check(resize, 'resize observer watches the table, wrap, and header');
    cellExtra = 7;
    resize.callback(); flush(); checkWidths();
    cellExtra = 11;
    w.dispatchEvent(new w.Event('resize')); flush(); checkWidths();
    tableBottom = 120;
    scroll();
    check(floating.hidden, 'hidden once the table bottom cannot fit the cloned header');
    tableBottom = -10;
    scroll();
    check(floating.hidden, 'hidden when the table has scrolled fully past');
    tableBottom = 900; headBottom = 180;
    scroll();
    check(floating.hidden, 'hidden again when the real header returns');
    headBottom = 70;
    tableWidth = 900;
    resize.callback(); flush();
    check(wrap.classList.contains('czp-wrap-fits'), 'existing fit-based sticky path remains available');
    check(floating.hidden, 'fitting tables retain their original sticky header without an overlay');
    table.remove();
    await Promise.resolve();
    check(!d.contains(floating) && resize.disconnected, 'removal tears down the clone and observers');
    scroll();
    check(frames.size === 0, 'removed table leaves no scheduled scroll work');
    console.log(checks + ' floating header assertions passed');
    w.close();
})().catch(error => { console.error(error); process.exitCode = 1; });
