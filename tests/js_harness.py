"""The node harness a page script runs in for the JS tests (stub DOM, stubbed fetch, scripted clicks). Shared by several test files."""

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const paths = JSON.parse(process.argv[1]), sc = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const els = {}, calls = [], posts = [], downloads = [];
function el(sel) {
  if (!els[sel]) els[sel] = { sel, innerHTML: "", textContent: "", value: (sc.values || {})[sel] || "", hidden: false, href: "", handlers: {}, dataset: {}, disabled: false,
    addEventListener(ev, fn) { this.handlers[ev] = fn; }, setAttribute() {}, appendChild() {}, className: "" };
  return els[sel];
}
const doc = { querySelector: s => el(s), createElement: () => { const e = el("a" + downloads.length); e.click = () => downloads.push({ name: e.download }); e.remove = () => {}; return e; }, body: { appendChild() {} } };
const responses = (sc.responses || []).map(r => Object.assign({ n: 0 }, r));
function respond(url, opts) {
  const method = (opts && opts.method) || "GET";
  calls.push(method + " " + url);
  if (method === "POST") posts.push({ url, body: JSON.parse(opts.body) });
  const hits = responses.filter(r => url.includes(r.match) && (r.method || "GET") === method);
  if (!hits.length) return { status: 404, body: { error: "no stub for " + url } };
  const r = hits[0], i = Array.isArray(r.sequence) ? Math.min(r.n++, r.sequence.length - 1) : 0;
  return { status: (r.statuses ? r.statuses[i] : r.status) || 200, body: JSON.parse(JSON.stringify(Array.isArray(r.sequence) ? r.sequence[i] : r.body)) };
}
const ctx = { window: {}, document: doc, URL: { createObjectURL: b => { downloads.blob = b; return "blob:x"; }, revokeObjectURL() {} }, Blob: class { constructor(p) { this.text = p.join(""); } },
  setTimeout: fn => fn(), Intl, Date, Math, JSON, Object, Array, String, Number, Promise, RegExp, encodeURIComponent, console, isNaN, parseInt, parseFloat,
  fetch: (url, opts) => (sc.reject && url.includes(sc.reject) ? (calls.push("GET " + url), Promise.reject(new Error("down"))) : Promise.resolve(respond(url, opts)).then(r => ({ ok: r.status < 400, status: r.status, json: () => Promise.resolve(r.body) }))) };
ctx.window.document = doc;
vm.createContext(ctx);
for (const p of paths) vm.runInContext(fs.readFileSync(p, "utf8"), ctx);
const tick = () => new Promise(r => setImmediate(r));
(async () => {
  for (let i = 0; i < 8; i++) await tick();
  for (const step of sc.steps || []) {
    if (step.set) el(step.set[0]).value = step.set[1];
    if (step.click) el(step.click).handlers.click({ target: {} });
    if (step.fire) { const [sel, ev, closest] = step.fire; el(sel).handlers[ev]({ target: { closest: q => (closest && closest[q]) || null } }); }
    for (let i = 0; i < 8; i++) await tick();
  }
  const out = { calls, posts, downloads, blob: downloads.blob ? downloads.blob.text : "", el: {} };
  for (const k of Object.keys(els)) out.el[k] = { html: els[k].innerHTML, text: els[k].textContent, hidden: els[k].hidden, value: els[k].value };
  process.stdout.write(JSON.stringify(out));
})();
"""
