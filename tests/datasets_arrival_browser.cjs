'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');
const root = path.join(__dirname, '..');
const web = path.join(root, 'quam_state_manager', 'web', 'static');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const row = id => ({ id, f: 'folder', exp: 'ramsey', date: '2026-11-20', time: '01:00:00',
    t: 1000000 + id, q: ['q1'], p: [], oc: {}, tags: [], sm: {}, pm: {}, status: 'finished' });
let browser;
async function mount() {
    const page = await browser.newPage();
    await page.setViewport({ width: 1366, height: 768 });
    const rows = Array.from({ length: 320 }, (_, i) => row(320 - i));
    await page.setContent(`<div id="sidebar-tree" hidden><details open><summary>day</summary><ul>
        <li><span class="tree-entry-click" data-uid="folder:320">320</span></li></ul></details></div>
        <div id="inspector-pane"><div id="ds-detail-root" data-uid="folder:240"></div></div>
        <div id="table-pane"><div class="datasets-page"><div class="ds-digest-band"><span>2026-11-20</span>
        <span>320 runs</span><span>all OK</span></div>
        <script id="ds-rows-data" data-now="1000">${JSON.stringify(rows)}</script>
        <div id="datasets-scroll" class="datasets-scroll"><table class="datasets-table-virtual">
        <thead id="datasets-thead"></thead><tbody id="datasets-tbody"></tbody></table></div></div></div>`);
    await page.addStyleTag({ path: path.join(web, 'style.css') });
    await page.addStyleTag({ path: path.join(web, 'ui-kit.css') });
    await page.addStyleTag({ content: `#table-pane {position:relative;top:20px;left:240px;
        width:850px;height:550px;overflow:auto} .datasets-page {display:block}
        table {border-collapse:collapse;width:100%} th,td {padding:0;border:0}
        tbody tr:not(.ds-spacer) {height:32px} .ds-digest-band {width:230px;font-size:13px;line-height:20px}
        .ds-new-pill {display:block;height:26px;margin:0;padding:0} [hidden] {display:none!important}` });
    await page.evaluate(() => {
        window.answers = [];
        window.fetch = url => String(url).includes('changes-since')
            ? Promise.resolve({ ok: true, json: () => Promise.resolve(window.answers.shift()
                || { now: 2000, updated: [], vanished: [] }) }) : new Promise(() => {});
        window.UI_CONFIG = { datasetPollInterval: 3600 };
    });
    for (const file of ['sync-badge.js', 'dataset-virtual.js', 'live-wake.js'])
        await page.addScriptTag({ path: path.join(web, file) });
    await page.evaluate(() => window.DatasetVirtual.init());
    return page;
}
async function deliver(page, rows, wake = false) {
    await page.evaluate((rows, wake) => {
        window.answers.push({ now: Date.now() / 1000, updated: rows, vanished: [] });
        if (wake) window.LiveWake._wake({}, true);
        else window.DatasetVirtual.pollNow();
    }, rows, wake);
    await sleep(80);
}
async function scrolled(page) {
    await page.evaluate(() => { document.getElementById('table-pane').scrollTop = 2700; });
    await sleep(5200); // beyond the user-interaction gate
}
async function run() {
    browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH
        || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true,
        args: ['--no-first-run', '--no-default-browser-check'] });
    console.log('owned browser PID ' + browser.process().pid);
    const pin = process.env.ARRIVAL_PIN || 'all';
    if (pin === 'all' || pin === 'seen') {
        const page = await mount();
        await page.evaluate(() => {
            const tray = document.createElement('div');
            tray.id = 'pending-tray';
            tray.innerHTML = '<button class="state-status-badge">Synced</button>';
            document.body.prepend(tray);
        });
        await deliver(page, []);
        await scrolled(page);
        for (const [i, action] of ['escape', 'elsewhere', 'close'].entries()) {
            const id = 321 + i;
            await deliver(page, [row(id)]);
            await page.evaluate(id => window.SyncBadge.note('new', { count: 1, uids: ['folder:' + id] }), id);
            assert.equal(await page.$eval('#ds-new-pill', el => el.hidden), false, 'arrival has a table bar');
            assert.equal(await page.$eval('.state-status-notice', el => el.textContent), '1 new');
            if (action === 'escape') await page.keyboard.press('Escape');
            else if (action === 'elsewhere') await page.click('#pending-tray');
            else await page.click('#ds-arrivals-close');
            assert.equal(await page.$eval('#ds-new-pill', el => el.hidden), true, action + ' dismisses table bar');
            assert.equal(await page.$('.state-status-notice'), null, action + ' acknowledges chip');
            await deliver(page, [row(id)]);
            await page.evaluate(id => window.SyncBadge.note('new', { count: 1, uids: ['folder:' + id] }), id);
            assert.equal(await page.$('.state-status-notice'), null, 'next poll stays acknowledged');
            assert.equal(await page.$eval('#ds-new-pill', el => el.hidden), true, 'next poll stays dismissed');
        }
        await page.evaluate(() => {
            const pane = document.getElementById('table-pane');
            pane.innerHTML = '<p>Other page</p>';
            pane.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: pane } }));
            window.SyncBadge.note('new', { count: 1, uids: ['folder:400'] });
        });
        assert.equal(await page.$('#ds-arrivals'), null, 'news on another page cannot remount the table bar');
        await page.close();
        console.log('seen pin passed');
    }
    if (pin === 'all' || pin === 'inspector-news') {
        const page = await mount();
        const app = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
        const start = app.indexOf('    window.SidebarScroll = (function () {');
        const end = app.indexOf('    // Capture before the refresh', start);
        await page.addScriptTag({ content: app.slice(start, end) });
        await page.addStyleTag({ content: `#sidebar {position:absolute;top:0;left:0;width:200px;
            height:300px;overflow:auto} #sidebar-tree {display:block} .tree-entry-click {display:block;height:32px}` });
        await page.evaluate(() => {
            const tray = document.createElement('div');
            tray.id = 'pending-tray';
            tray.innerHTML = '<button class="state-status-badge">Synced</button>';
            document.body.prepend(tray);
            const tree = document.getElementById('sidebar-tree');
            tree.hidden = false;
            const sidebar = document.createElement('div');
            sidebar.id = 'sidebar';
            tree.before(sidebar);
            sidebar.append(tree);
            window.fillTree = ids => {
                tree.innerHTML = '<details open><summary>day</summary>' + ids.map(id =>
                    '<span class="tree-entry-click" data-uid="folder:' + id + '">' + id + '</span>').join('')
                    + '</details>';
            };
            window.fillTree(Array.from({ length: 100 }, (_, i) => 320 - i));
            sidebar.scrollTop = 1800; // revealing the inspector run scrolled its group into view
        });
        await deliver(page, []);
        await scrolled(page);
        await deliver(page, [row(321), row(322)]);
        await page.evaluate(() => {
            window.SyncBadge.note('new', { count: 2, uids: ['folder:321', 'folder:322'] });
            const tree = document.getElementById('sidebar-tree');
            window.SidebarScroll.arm();
            tree.dispatchEvent(new CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: tree } }));
            window.fillTree([322, 321, ...Array.from({ length: 100 }, (_, i) => 320 - i)]);
            // Replacing/opening a full group clamps its scroller before anchor restoration.
            document.getElementById('sidebar').scrollTop = 0;
            tree.querySelector('details').dispatchEvent(new Event('toggle'));
            tree.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: tree } }));
        });
        await page.waitForFunction(() => document.getElementById('sidebar').scrollTop > 1800);
        assert.equal(await page.$eval('.state-status-notice', el => el.textContent), '2 new',
            'unpainted sidebar rows do not acknowledge inspector arrivals');
        assert.equal(await page.$eval('#ds-new-pill', el => !el.hidden && parseInt(el.textContent, 10)), 2,
            'the bar shares the two unseen identities');
        assert.ok(await page.$eval('tr[data-id="folder:240"]', tr => tr.classList.contains('ds-row-active')),
            'the open run stays highlighted');
        await page.evaluate(() => { document.getElementById('table-pane').scrollTop = 0; });
        await page.waitForFunction(() => document.getElementById('ds-new-pill').hidden
            && !document.querySelector('.state-status-notice'));
        assert.ok(await page.evaluate(() => ['folder:321', 'folder:322'].every(uid =>
            window.DatasetVirtual.isOnScreen(uid) && window.SyncBadge.isSeen(uid))),
            'actually reaching the table rows acknowledges both counts');
        await page.close();
        console.log('inspector news pin passed');
    }
    if (pin === 'all' || pin === 'inspector') {
        const page = await mount();
        await page.addStyleTag({ content: '.ds-new-pill {height:auto;display:inline-block;line-height:32px}' });
        await deliver(page, []);
        await scrolled(page);
        const height = () => document.getElementById('ds-arrivals').getBoundingClientRect().height;
        const before = await page.evaluate(height);
        const rowY = () => document.querySelector('tr[data-id="folder:240"]').getBoundingClientRect().top;
        const y = await page.evaluate(rowY);
        await deliver(page, [row(321)]);
        assert.ok(Math.abs(await page.evaluate(rowY) - y) <= 1, 'open inspector row stays still on arrival');
        assert.ok(Math.abs(await page.evaluate(height) - before) < 0.5, 'arrival slot reserves control height');
        await page.click('#ds-arrivals-close');
        assert.ok(Math.abs(await page.evaluate(height) - before) < 0.5, 'dismissal keeps the reserved height');
        await page.close();
        console.log('inspector pin passed');
    }
    if (pin === 'all' || pin === 'scroll') {
        const page = await mount();
        await deliver(page, []); // initial catch-up is complete
        await scrolled(page);
        const probe = await page.evaluate(() => {
            const pane = document.getElementById('table-pane').getBoundingClientRect();
            const rows = [...document.querySelectorAll('#datasets-tbody tr[data-id]')];
            const tr = rows.find(r => r.getBoundingClientRect().top >= pane.top + 50);
            window.probeUid = tr.dataset.id;
            window.probeY = tr.getBoundingClientRect().top;
            window.samples = [];
            window.sampleTimer = setInterval(() => {
                const r = [...document.querySelectorAll('#datasets-tbody tr[data-id]')]
                    .find(r => r.dataset.id === window.probeUid);
                const bar = document.getElementById('ds-new-pill');
                window.samples.push({ dy: r ? r.getBoundingClientRect().top - window.probeY : null,
                    count: bar && !bar.hidden ? parseInt(bar.textContent, 10) : 0 });
            }, 100);
            return { uid: tr.dataset.id, rendered: rows.length, paneY: pane.top };
        });
        assert.ok(probe.rendered < 100, 'large day is virtualized');
        await deliver(page, [row(321)], true);
        await sleep(700);
        await deliver(page, [row(321)]); // duplicate poll after the wake
        await page.evaluate(() => {
            const tree = document.getElementById('sidebar-tree');
            tree.querySelector('[data-uid]').dataset.uid = 'folder:321';
            tree.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: tree } }));
        });
        await deliver(page, [row(322)], true);
        await sleep(4200);
        const samples = await page.evaluate(() => { clearInterval(window.sampleTimer); return window.samples; });
        if (process.env.ARRIVAL_EVIDENCE) {
            fs.mkdirSync(process.env.ARRIVAL_EVIDENCE, { recursive: true });
            fs.writeFileSync(path.join(process.env.ARRIVAL_EVIDENCE, 'samples.json'), JSON.stringify(samples));
            await page.screenshot({ path: path.join(process.env.ARRIVAL_EVIDENCE, 'scrolled.png') });
        }
        assert.ok(samples.length >= 50, 'sample every 100 ms for at least five seconds');
        assert.ok(samples.every(s => s.dy !== null && Math.abs(s.dy) <= 1), JSON.stringify(samples));
        assert.ok(samples.every(s => s.count >= 1), 'bar stays through sidebar swap and duplicate delivery');
        assert.equal(samples.at(-1).count, 2, 'both unseen runs counted');
        const paneY = await page.$eval('#table-pane', el => el.getBoundingClientRect().top);
        assert.ok(Math.abs(paneY - probe.paneY) <= 1, 'reserved bar space keeps the scroller origin');
        await page.click('#ds-new-pill');
        await sleep(100);
        assert.ok(await page.$eval('#ds-new-pill', el => el.hidden), 'bar click clears it');
        await deliver(page, [row(323)]);
        assert.ok(await page.$eval('#ds-new-pill', el => el.hidden), 'at top arrivals need no bar');
        await page.close();
        console.log('scroll pin passed');
    }
    if (pin === 'all' || pin === 'top') {
        const page = await mount();
        await deliver(page, []);
        await page.evaluate(() => { document.getElementById('table-pane').scrollTop = 2700; });
        await sleep(100);
        await deliver(page, [row(321)], true);
        assert.equal(await page.evaluate(() => window.DatasetVirtual.getRow('folder:321')), null,
            'fixture: scrolling holds the arrival');
        await page.evaluate(() => { document.getElementById('table-pane').scrollTop = 0; });
        await sleep(100);
        assert.ok(await page.evaluate(() => window.DatasetVirtual.getRow('folder:321')),
            'scrolling to the top reveals held arrivals immediately');
        assert.ok(await page.$eval('#ds-new-pill', el => el.hidden), 'scrolling to top clears the bar');
        await page.close();
        console.log('top pin passed');
    }
    if (pin === 'all' || pin === 'highlight') {
        const page = await mount();
        await deliver(page, []);
        await scrolled(page);
        await deliver(page, [row(321)], true);
        assert.ok(await page.$eval('tr[data-id="folder:240"]', tr => tr.classList.contains('ds-row-active')),
            'detail run stays highlighted after insertion');
        await page.evaluate(() => window.DatasetVirtual.applyFilters());
        await sleep(100);
        assert.ok(await page.$eval('tr[data-id="folder:240"]', tr => tr.classList.contains('ds-row-active')),
            'detail run stays highlighted after filtering');
        await page.close();
        console.log('highlight pin passed');
    }
    if (pin === 'all' || pin === 'load') {
        const page = await mount();
        await scrolled(page);
        const landing = Object.assign(row(322), { t: Date.now() });
        await deliver(page, [landing], true);
        assert.ok(await page.$eval('#ds-new-pill', el => el.hidden), 'landing during initial load is baseline');
        assert.equal(await page.evaluate(() => window.DatasetVirtual.rowCount()), 321);
        await page.close();
        console.log('load pin passed');
    }
}
run().catch(e => { console.error(e); process.exitCode = 1; }).finally(async () => {
    if (browser) await browser.close();
});
