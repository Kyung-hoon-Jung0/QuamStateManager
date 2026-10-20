const assert=require('assert');const {world}=require('./v152b_ui.cjs');const w=world('?theme=qm');
w.getComputedStyle=()=>({getPropertyValue(k){return k==='--pico-form-element-border-color'?'#667788':'#eeeeee';}});
w.setTheme('qm');const t=w.UI_CONFIG.plotly.topology,iw=w.UI_CONFIG.instrumentWiring;
for(const value of [t.hoverBorder,iw.gridBorder,iw.separatorColor,iw.subSeparatorColor,iw.emptyPortStroke,iw.unassignedStroke])assert.equal(value,'#667788','QM border follows palette property');w.close();
