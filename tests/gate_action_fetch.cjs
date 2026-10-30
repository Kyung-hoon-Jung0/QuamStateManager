'use strict';
const { execFileSync } = require('child_process');
const path = require('path');
function compute(mode, data) {
  const python = process.env.CONDA_PREFIX ? path.join(process.env.CONDA_PREFIX, 'python.exe') : 'python';
  return JSON.parse(execFileSync(python, [path.join(__dirname, 'gate_action_bridge.py')], {
    input: JSON.stringify(Object.assign({ mode }, data)), encoding: 'utf8'
  }));
}
exports.compute = compute;
exports.install = function (w) {
  const original = w.fetch;
  w.fetch = function (url, opts) {
    const u = new URL(String(url), 'http://localhost');
    let result;
    if (u.pathname === '/generate/roles' && opts && opts.method === 'POST' && opts.body &&
        JSON.parse(opts.body).display_spec)
      result = compute('display', { spec: JSON.parse(opts.body).display_spec });
    else if (u.pathname === '/generate/additions/gate-action')
      result = compute('resolve', JSON.parse(opts.body));
    else if (u.pathname === '/generate/additions/lint' && JSON.parse(opts.body).addition)
      result = compute('lint', JSON.parse(opts.body));
    else return original(url, opts);
    return Promise.resolve({ status: 200, json: () => Promise.resolve(result) });
  };
};
