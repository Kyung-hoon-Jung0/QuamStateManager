'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const Module = require('module');
const {JSDOM} = require('jsdom');
const f = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const item = Number(process.argv[3]);
const root = path.join(__dirname, '..');
const source = name => fs.readFileSync(path.join(root, 'quam_state_manager/web/static', name), 'utf8');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
    const dom = new JSDOM(f.html, {url:'http://localhost/cz-plan?pair=q1-2', runScripts:'outside-only', pretendToBeVisual:true});
    const w=dom.window, d=w.document, r=d.getElementById('czp-root'), renders=[], scrolls=[];
    w.HTMLElement.prototype.scrollIntoView=function(){scrolls.push(this)};
    const detail=d.querySelector('.czp-detail');
    detail.getBoundingClientRect=()=>({top:900, bottom:1300, width:1000, height:400});
    w.requirePlotly=()=>Promise.resolve();
    w.PlotTheme={houseLayout:l=>l,houseConfig:c=>c};
    w._plotlyRender=(el,traces,layout)=>{renders.push({id:el.id,traces,layout});return Promise.resolve()};
    w.CzMap={mount(){},select(){},highlight(){},loss(){return null},scale(){return .25}};
    w.fetch=url=>Promise.resolve({json:()=>Promise.resolve(structuredClone(
        url.startsWith('/cz-plan/solve') ? f.solve : url.startsWith('/cz-plan/history') ? f.history : f.data))});
    w.eval(source('cz-plan.js'));w.CzPlan.mount();await wait(40);
    const flux=()=>renders.filter(x=>x.id==='czp-flux').at(-1);
    if(item===1){
        assert(detail.compareDocumentPosition(d.querySelector('.czp-map-box')) & w.Node.DOCUMENT_POSITION_FOLLOWING);
        assert(scrolls.includes(detail),'query pair reveals detail after loading');
        scrolls.length=0; d.querySelector('.czp-row').click();await wait(20);
        assert(scrolls.includes(detail),'table selection reveals detail');
        scrolls.length=0;r.dispatchEvent(new w.CustomEvent('czp:open',{detail:{op:f.data.row.path}}));await wait(20);
        assert(scrolls.includes(detail),'map selection reveals detail');
    }
    if(item===2){
        for(const [k,ln] of f.data.row.lines.entries()){
            assert(flux().traces.some(t=>(t.name||'').startsWith(ln.name+' · ') && t.name.endsWith(ln.point.validity)));
            assert(d.querySelector('#czp-card').textContent.includes(ln.point.validity));
        }
    }
    if(item===3){
        const match=d.getElementById('czp-match');assert.strictEqual(match.textContent,f.solve.summary);
        for(const key of ['target_detuning_hz','amplitude_v','stored_amplitude_v','difference_v','tier','line'])
            assert.strictEqual(match.getAttribute('data-'+key.replaceAll('_','-')),String(f.solve[key]));
        const blob=JSON.parse(d.getElementById('cz-operating-points').textContent);
        assert.deepStrictEqual(blob.find(p=>p.op===f.data.row.path&&p.line==='control').solve, f.solve);
        d.querySelector('.czp-row-selected').click();await wait(20);
        assert.strictEqual(match.textContent,f.solve.summary,'reselecting retains the answer');
    }
    if(item===4){
        const levels=flux().layout.annotations.filter(a=>(a.text||'').startsWith('control on'));
        assert(levels.length===3&&levels.every(a=>a.x===1&&a.xanchor==='left'&&a.xshift>=8));
        assert(flux().layout.margin.r>=140&&flux().layout.margin.t>=40);
    }
    if(item===5){
        const time=renders.find(x=>x.id==='czp-time');assert.deepStrictEqual(Array.from(time.layout.yaxis2.domain),[0,1]);
        assert(time.traces.every(t=>t.yaxis==='y2'));
        const box=d.getElementById('czp-frequency-box');assert(!box.open&&!renders.some(x=>x.id==='czp-frequency'));
        box.open=true;box.dispatchEvent(new w.Event('toggle'));await wait(10);
        assert(renders.some(x=>x.id==='czp-frequency'&&x.traces.length>=2));
        assert.deepStrictEqual(Array.from(time.traces[0].y),f.data.time_panel.detuning_mhz);
    }
    if(item===6){
        const badge=d.querySelector('.czp-no-quad');assert.strictEqual(badge.textContent,'no quad term');
        assert(badge.title.includes('freq_vs_flux_01_quad_term is missing')&&badge.title.includes('validity unknown'));
        assert(d.querySelectorAll('.czp-row[data-pair-start="0"]').length>0);
        for(const cell of d.querySelectorAll('td[data-last]')){assert(/^(\d{4}-\d\d-\d\d|-)$/.test(cell.textContent));assert(cell.firstChild.title.includes(cell.dataset.last))}
        const picker=d.getElementById('czp-map-op');
        picker.innerHTML='<option value="cz_two">variant</option>';picker.value='cz_two';
        r.dispatchEvent(new w.Event('czp:mapop'));await wait(20);
        const visible=Array.from(d.querySelectorAll('.czp-row')).filter(tr=>!tr.hidden);
        assert(visible.length&&visible.every(tr=>tr.dataset.pairStart==='1'),'filtered variants retain their pair labels');
    }
    if(item===7){
        const card=d.getElementById('czp-card'), hover=Array.from(card.querySelectorAll('.czp-help-hover'));
        assert(hover.some(e=>e.title.includes('runtime property')),'duration explanation retained on hover');
        assert(hover.some(e=>e.title.includes('first recorded')||e.title.includes('history')),'history explanation retained on hover');
        assert(hover.some(e=>e.title.includes('limit ')),'validity limit retained on hover');
        assert(hover.some(e=>e.title.includes('excursion ')&&e.title.includes('frequency frame reference')));
        assert(card.textContent.includes('Defects touched during the gate: none')&&!card.textContent.includes('excursion '));
        assert(card.textContent.includes('Durationunknown')&&!card.textContent.includes('runtime property'));
        assert(!card.textContent.includes('(limit '));
    }
    if(item===8){
        // Reuse the full geometry harness with real frozen maps; inspect its rendered partners.
        const filename=path.join(__dirname,'cz_map_selfcheck.cjs'), mod=new Module(filename,module);
        mod.filename=filename;mod.paths=module.paths;
        const text=fs.readFileSync(filename,'utf8').split('(async () => {')[0];
        mod._compile(text+'\nmodule.exports={page,FX};',filename);
        const env=await mod.exports.page({map:mod.exports.FX('map_rig21.json'),width:1000});await wait(50);
        const plot=env.last(), labels=plot.traces.find(t=>t.meta==='partners');
        assert(labels&&labels.textfont.size>=11,'operating labels readable');
        assert(labels.textposition.every(p=>/^(top|bottom) (left|right)$/.test(p)));
        assert(source('cz-map.js').includes('!occupied.every'),'all label placements clear markers');
        env.dom.window.close();
    }
    dom.window.close(); console.log('UX-'+item+' passed');
})().catch(e=>{console.error(e);process.exit(1)});
