const fs=require('fs');
const base='C:/Users/KYUNGH~1/AppData/Local/Temp/smbrowse_topology_haIbfV/quam_state';
const st=JSON.parse(fs.readFileSync(base+'/state.json','utf8'));
const keys=['T1','T2echo','T2ramsey','gate_fidelity','grid_location','f_01','extras'];
const q=st.qubits;
const rep={};
for(const [id,v] of Object.entries(q)){
  rep[id]={T1:v.T1, T2echo:v.T2echo, T2ramsey:v.T2ramsey, grid:v.grid_location, extras: v.extras? Object.keys(v.extras):null, topKeys:Object.keys(v).length};
}
console.log(JSON.stringify(rep.q1,null,1));
console.log('all qubit top-level keys of q1:', Object.keys(q.q1).join(','));
const anyT1=Object.values(q).filter(v=>v.T1!=null).length;
const anyT2=Object.values(q).filter(v=>v.T2echo!=null).length;
const grid=Object.values(q).filter(v=>v.grid_location!=null).length;
console.log('qubits with T1:',anyT1,'T2echo:',anyT2,'grid_location:',grid,'of',Object.keys(q).length);
// pair cz fidelity
const p=st.qubit_pairs; const pk=Object.keys(p)[0];
console.log('pair keys:', Object.keys(p[pk]).join(','));
