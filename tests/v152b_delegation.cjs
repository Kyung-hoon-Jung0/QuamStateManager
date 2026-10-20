const assert=require('assert');const {world,chip,mount,key,open}=require('./v152b_ui.cjs');
for(const n of [3,21,400]){
 const w=world();let pairListeners=0,hostListeners=0;
 const add=w.Element.prototype.addEventListener;
 w.Element.prototype.addEventListener=function(type,fn,options){
  if(this.matches('[data-hero-pair], [data-cm]'))pairListeners++;
  if(this.id==='topo-hero')hostListeners++;
  return add.call(this,type,fn,options);
 };
 mount(w,chip(n));const initial=hostListeners;mount(w,chip(n));
 if(process.argv[2]==='listeners'){
  assert.equal(pairListeners,0,'no per-edge listeners');assert(initial<=20,'constant host listener budget');assert.equal(hostListeners,initial,'rerender adds no host listeners');
 }
 const host=w.document.getElementById('topo-hero'),anchor=host.querySelector('[data-link-id="q1"]');
 let scans=0;const query=host.querySelectorAll.bind(host);
 host.querySelectorAll=function(sel){if(sel==='[data-link-ends]')scans++;return query(sel);};
 anchor.dispatchEvent(new w.MouseEvent('mouseover',{bubbles:true}));
 if(process.argv[2]==='hover')assert.equal(scans,0,'qubit hover never scans all pairs');
 assert(host.querySelector('[data-hero-pair="q1-2"]').classList.contains('topo-linked'));
 assert(!host.querySelector('[data-hero-pair="q2-3"]').classList.contains('topo-linked'));
 open(w,host.querySelector('.topo-hero-edge'));assert(w.document.querySelector('.topo-pair-popup'));
 key(w,w.document.activeElement,'Escape');assert(!w.document.querySelector('.topo-pair-popup'));
 const component=w.document.createElement('div');w.document.body.append(component);
 w.TopoGraph.renderLayout(component,{nodes:chip(n).nodes,edges:chip(n).edges});
 w.TopoGraph.bindDetails(component,'[data-cm]',el=>el.getAttribute('data-cm'),()=>null);
 w.TopoGraph.bindLinked(component);
 const legend=w.document.createElement('div');legend.className='cm-feed-legend';legend.textContent='feed key';component.appendChild(legend);
 w.setTheme('qm');
 assert.equal(component.querySelector('.cm-feed-legend'),legend,'theme repaint keeps the feed key');
 const stone=component.querySelector('[data-link-id="q1"]');assert.equal(stone.getAttribute('tabindex'),'0');
 stone.dispatchEvent(new w.MouseEvent('mouseover',{bubbles:true}));
 assert(component.querySelector('[data-cm="p:q1-2"]').classList.contains('topo-linked'));
 if(process.argv[2]==='listeners')assert.equal(pairListeners,0,'component map also delegates');
 w.close();
}
