/* Local resources are resolved by key through the external lab map. */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
function loadMap() {
  try {
    const value = JSON.parse(fs.readFileSync(process.env.SM_LAB_MAP || 'D:/work/sm_lab_map.json', 'utf8'));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch (_) { return {}; }
}
function missingPath(key, field) {
  let value = path.join(os.tmpdir(), '__sm_missing_lab_map__', key, field);
  while (fs.existsSync(value)) value += '.missing';
  return value;
}
function labValue(key, field) {
  const value = (loadMap()[key] || {})[field];
  return typeof value === 'string' && value ? value : '__sm_missing_' + key + '_' + field + '__';
}
function labPath(key, field = 'chip') {
  const value = ((loadMap()[key] || {}).paths || {})[field];
  return typeof value === 'string' && value ? value : missingPath(key, field);
}
module.exports = { labValue, labPath };
