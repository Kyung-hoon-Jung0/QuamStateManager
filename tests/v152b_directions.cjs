const assert=require('assert');const {world,chip,mount}=require('./v152b_ui.cjs');
for(const reverse of [false,true]) {
 const w=world(), topo=chip();
 topo.edges=[{...topo.edges[0],directed:true,gate_kind:'cr',active:false,cz_fidelity:.5},
 {...topo.edges[0],pair_id:'q2-1',source:'q2',target:'q1',directed:true,gate_kind:'cr',active:true,cz_fidelity:.99}];
 if(reverse)topo.edges.reverse();mount(w,topo);
 const badges=[...w.document.querySelectorAll('.topo-pair-badge')];assert.equal(badges.length,2);
 const off=badges.find(b=>b.dataset.heroPair==='q1-2'), on=badges.find(b=>b.dataset.heroPair==='q2-1');
 assert.match(off.textContent,/q1-2.*off.*50.0%/);assert.equal(off.getAttribute('opacity'),'0.35');
 assert.match(on.textContent,/q2-1.*99.0%/);assert.equal(on.getAttribute('opacity'),null);
 assert.notEqual(off.getAttribute('transform'),on.getAttribute('transform'),'directions have separate positions');
 assert.equal(w.document.querySelector('.topo-hero-edge[data-hero-pair="q1-2"]').getAttribute('opacity'),'0.35');
 w.close();
}
