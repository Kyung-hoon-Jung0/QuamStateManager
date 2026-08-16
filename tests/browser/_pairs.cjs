const fs=require('fs');
const base='C:/Users/KYUNGH~1/AppData/Local/Temp/smbrowse_topology_haIbfV/quam_state';
const st=JSON.parse(fs.readFileSync(base+'/state.json','utf8'));
const pairs=st.qubit_pairs||{};
const out={};
for(const [k,v] of Object.entries(pairs)){
  out[k]={control:v.qubit_control, target:v.qubit_target, moving:v.moving_qubit};
}
console.log(JSON.stringify(out,null,1));
console.log('nQ', Object.keys(st.qubits||{}).length, 'nP', Object.keys(pairs).length);
const q=st.qubits;
const f={}; for(const [k,v] of Object.entries(q)) f[k]={f01:v.f_01, grid:v.grid_location};
fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/_chip_truth.json', JSON.stringify({pairs:out, qubits:f},null,1));
console.log(JSON.stringify(f,null,1).slice(0,1500));
