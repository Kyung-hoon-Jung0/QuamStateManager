const assert=require('assert');const {world,chip,mount}=require('./v152b_ui.cjs');
if(process.argv[2]==='blocked'){
const blocked=world('?theme=qm',true);
assert.equal(blocked.document.documentElement.dataset.theme,'qm');
assert.equal(blocked.UI_CONFIG.instrumentWiring.gridBg,'#0e0e0e','URL theme survives blocked storage');blocked.close();
} else {
const w=world('?theme=light');mount(w,chip());
const fill=()=>w.document.querySelector('.topo-pair-badge rect').style.fill;
const light=fill();assert(light);
for(const theme of ['qm','light','dark','qm','light']) {
 w.setTheme(theme);
 const expected=w.UI_CONFIG.plotly.topology.dashboard.nullCellColor;
 const probe=w.document.createElement('div');probe.style.color=expected;
 assert.equal(fill(),probe.style.color,'mounted badge repaints '+theme);
}
assert.equal(fill(),light);w.close();

}
