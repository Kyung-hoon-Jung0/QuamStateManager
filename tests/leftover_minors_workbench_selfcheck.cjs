"use strict";
const fs = require("fs");
const assert = require("assert");
const {JSDOM} = require("jsdom");
const page = fs.readFileSync(process.argv[2], "utf8").replace(/<script src="[^"]*"><\/script>/g, "");
const posts = [];
const dom = new JSDOM(page, {url: "http://localhost/workbench?qb=http://127.0.0.1:8061",
  runScripts: "dangerously", beforeParse(w) {
    w.Split = () => ({destroy() {}, setSizes() {}, getSizes() {return [50,50];}});
    w.localStorage.setItem("quam_workbench_qb_mode", "sm");
    w.fetch = (url, init) => {
      let status = {url: "http://127.0.0.1:8001", source: "saved", ok: true};
      if (url === "/qualibrate-ui-target" && init && init.method === "POST") {
        posts.push(JSON.parse(init.body).url);
        status.url = posts[posts.length - 1];
      }
      return Promise.resolve({json: () => Promise.resolve(status)});
    };
  }});
setTimeout(() => {
  try {
    assert.deepEqual(posts, ["http://127.0.0.1:8061"]);
    assert.equal(dom.window.document.getElementById("wb-qb").getAttribute("src"), "/qualibrate-ui/");
    assert.equal(dom.window.document.getElementById("wb-url").value, posts[0]);
    console.log("3 assertions passed");
  } finally {dom.window.close();}
}, 100);
