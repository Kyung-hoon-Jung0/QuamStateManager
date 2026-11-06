'use strict';
// Browser evidence defaults to system temp; SM_EVIDENCE_DIR can select scratch.
const fs = require('fs');
const path = require('path');
const os = require('os');
exports.directory = function (name) {
  const target = path.resolve(process.env.SM_EVIDENCE_DIR || os.tmpdir(), name);
  const root = path.resolve(__dirname, '../..');
  const relative = path.relative(root, target);
  if (!relative || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative))) {
    throw new Error('Evidence output must be outside the repository');
  }
  fs.mkdirSync(target, {recursive: true});
  return target;
};
