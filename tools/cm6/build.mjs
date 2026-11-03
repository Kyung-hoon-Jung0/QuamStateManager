// Rebuild: npm ci && node build.mjs  -> dist/cm6.min.js
import { build } from "esbuild";
await build({ entryPoints: ["entry.js"], bundle: true, minify: true, format: "iife",
  target: ["es2020"], outfile: "dist/cm6.min.js", legalComments: "eof", charset: "utf8" });
