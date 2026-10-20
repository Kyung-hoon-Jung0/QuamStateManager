const fs=require('fs'), path=require('path');
const {JSDOM}=require('jsdom');
const read=f=>fs.readFileSync(path.join(__dirname,'../quam_state_manager/web',f),'utf8');
function world(search='', blocked=false, html='') {
 const w=new JSDOM('<html><body><div id="topo-hero"></div><div id="topo-html-wrap"></div><div id="table-pane"></div>'+html+'</body></html>',{url:'http://localhost/'+search,runScripts:'outside-only',pretendToBeVisual:true}).window;
 w.fetch=()=>new w.Promise(()=>{});w.htmx={process(){},ajax(){},on(){}};
 w.matchMedia=()=>({matches:false,addListener(){},addEventListener(){}});
 Object.defineProperty(w.HTMLElement.prototype,'offsetWidth',{get(){return 40;}});
 Object.defineProperty(w.HTMLElement.prototype,'offsetHeight',{get(){return 28;}});
 if(blocked) w.Storage.prototype.getItem=()=>{throw Error('blocked');};
 w.getComputedStyle=()=>({getPropertyValue(k){return {'--pico-background-color':'#0e0e0e','--pico-card-background-color':'#1d1d1d','--pico-color':'#e5e5e5','--pico-muted-color':'#999999','--pico-form-element-border-color':'#555555'}[k]||'';}});
 w.eval(read('templates/_theme_boot.html').match(/<script>([\s\S]*?)<\/script>/)[1]);
 for(const f of ['app.js','topo-graph.js','chip-status.js'])w.eval(read('static/'+f));
 return w;
}
function chip(count=2){const nodes=[],edges=[];for(let i=1;i<=count;i++){nodes.push({id:'q'+i,grid_location:(i-1)+',0',f_01:5e9});if(i>1)edges.push({pair_id:'q'+(i-1)+'-'+i,source:'q'+(i-1),target:'q'+i,has_cz:true,gate_kind:'cz'});}return {nodes,edges};}
function mount(w,topo){w.ChipStatus.mount({topo,rawWiring:{},defaultThresholds:{},diagFindings:[],metricMeta:{}});}
function key(w,target,key,more={}){target.dispatchEvent(new w.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,...more}));}
function open(w,anchor){anchor.dispatchEvent(new w.MouseEvent('mouseover',{bubbles:true}));anchor.dispatchEvent(new w.MouseEvent('mouseenter'));w.document.querySelector('.topo-expand').click();}
module.exports={world,chip,mount,key,open,read};
