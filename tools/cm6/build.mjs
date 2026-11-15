// Rebuild: npm ci && node build.mjs  -> dist/cm6.min.js and dist/cm6-python.min.js
import { build } from "esbuild";
const common = { bundle: true, minify: true, format: "iife", target: ["es2020"], legalComments: "eof", charset: "utf8" };
await build({ ...common, entryPoints: ["entry.js"], outfile: "dist/cm6.min.js" });
await build({ ...common, entryPoints: ["entry-python.js"], outfile: "dist/cm6-python.min.js" });
