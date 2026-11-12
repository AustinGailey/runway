#!/usr/bin/env node
/* Drive the harness page in headless Chrome: node harness/drive.mjs <steps.json | -> [--url URL]
   Steps is a JSON array, run in order; the first failure stops the run (exit 1). Each step prints one JSON line.
     {"goto": "http://127.0.0.1:8765/"}            load a page (default: --url, else http://127.0.0.1:8765/)
     {"viewport": {"width": 375, "height": 812}}    resize (and set the device scale to 1)
     {"waitFor": "#content .card", "timeout": 8000} wait until a CSS selector matches...
     {"waitForText": "Merged", "timeout": 8000}     ...or until the page's text contains this
     {"click": "button[data-act=go]"}              click the first match of a selector
     {"clickText": "Go"}                           click the first button, link or [data-act] whose trimmed text is exactly this
     {"type": {"selector": "#cap", "text": "5"}}   set an input's value and fire input and change
     {"key": "Escape"}                             press a key
     {"eval": "document.title"}                    evaluate an expression (awaited) and print its value
     {"sleep": 1000}                               wait
     {"screenshot": "/tmp/shot.png"}               save a PNG of the viewport
   At the end it prints {"console": [...errors...], "exceptions": [...]}; uncaught page exceptions fail the run. */
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, existsSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir, userInfo } from "node:os";
import { join } from "node:path";

const args = process.argv.slice(2);
const urlArg = args.includes("--url") ? args[args.indexOf("--url") + 1] : null;
const src = args.find(a => !a.startsWith("--") && a !== urlArg);
if (!src) { console.error("usage: node harness/drive.mjs <steps.json | -> [--url URL]"); process.exit(2); }
const steps = JSON.parse(src === "-" ? readFileSync(0, "utf8") : readFileSync(src, "utf8"));

const CHROME = [process.env.RUNWAY_CHROME, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium"].find(p => p && existsSync(p));
if (!CHROME) { console.error("no Chrome found (set RUNWAY_CHROME)"); process.exit(2); }
const profile = mkdtempSync(join(tmpdir(), "runway-drive-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
  "--no-default-browser-check", "--disable-gpu", "--window-size=1280,900", "about:blank"], { stdio: "ignore", detached: true,
  // Chrome's network service fails when HOME is missing or fake (as under the test suite): give it the account's own.
  // Its profile is still the temp folder above, so it never reads or writes the real browser profile.
  env: { ...process.env, HOME: userInfo().homedir } });
const cleanup = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch {} try { rmSync(profile, { recursive: true, force: true }); } catch {} };
process.on("exit", cleanup);
const sleep = ms => new Promise(r => setTimeout(r, ms));

let port;
for (let i = 0; i < 100 && !port; i++){ await sleep(100); try { port = readFileSync(join(profile, "DevToolsActivePort"), "utf8").split("\n")[0]; } catch {} }
if (!port) { console.error("Chrome did not start"); process.exit(2); }
if (!port) process.exit(2);
let target;
for (let i = 0; i < 50 && !target; i++){ try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === "page"); } catch {} if (!target) await sleep(100); }
if (!target) { console.error("Chrome has no page to drive"); process.exit(2); }
if (process.env.DRIVE_DEBUG) console.error("target", target.url, target.type);
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; setTimeout(() => rej(new Error("Chrome's DevTools socket did not open")), 10000); });
let seq = 0; const pending = new Map(); const consoleErrors = [], exceptions = [];
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)){ const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(m.error.message)) : res(m.result); return; }
  if (m.method === "Page.loadEventFired") loads.splice(0).forEach(r => r());
  if (m.method === "Runtime.exceptionThrown") exceptions.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") consoleErrors.push(m.params.args.map(a => a.value ?? a.description ?? "").join(" ").slice(0, 300));
};
// Every call has a deadline: Chrome never answers an evaluate whose page navigated away mid-call.
const cdp = (method, params = {}, ms = 30000) => new Promise((res, rej) => {
  const id = ++seq; pending.set(id, { res, rej }); ws.send(JSON.stringify({ id, method, params }));
  setTimeout(() => { if (pending.has(id)){ pending.delete(id); rej(new Error(method + " got no answer in " + ms + " ms")); } }, ms);
});
const loads = []; // resolvers waiting for the next load event
await cdp("Runtime.enable"); await cdp("Page.enable");

async function evaluate(expr, ms = 120000){
  const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }, ms);
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}
async function until(test, timeout, what){
  const end = Date.now() + (timeout || 8000);
  while (Date.now() < end){ try { if (await evaluate(test, 3000)) return; } catch {} await sleep(150); }
  throw new Error(`timed out waiting for ${what}`);
}
const q = JSON.stringify;
const CLICKABLE = `[...document.querySelectorAll("button,a,[data-act],[role=button],summary,label")]`;

let failed = false;
for (const [i, s] of steps.entries()){
  const out = { step: i, ...s };
  try {
    if ("goto" in s){
      const loaded = new Promise(r => loads.push(r));
      const nav = await cdp("Page.navigate", { url: s.goto || urlArg || "http://127.0.0.1:8765/" });
      if (nav.errorText) throw new Error("navigation failed: " + nav.errorText);
      await Promise.race([loaded, sleep(15000).then(() => { throw new Error("page did not load in 15 s"); })]);
    }
    else if ("viewport" in s){
      // mobile:true alone isn't enough: without a <meta name=viewport> tag (this page has none — it's normally
      // embedded in claude.ai's own iframe, which sizes it), Chrome's mobile emulation falls back to the ~980px
      // default desktop-width viewport. mobile:false plus actually resizing the window makes the CSS width stick.
      await cdp("Emulation.setDeviceMetricsOverride", { width: s.viewport.width, height: s.viewport.height, deviceScaleFactor: 1, mobile: false });
      const { windowId } = await cdp("Browser.getWindowForTarget");
      await cdp("Browser.setWindowBounds", { windowId, bounds: { width: s.viewport.width, height: s.viewport.height } });
    }
    else if ("waitFor" in s) await until(`!!document.querySelector(${q(s.waitFor)})`, s.timeout, s.waitFor);
    else if ("waitForText" in s) await until(`document.body.innerText.includes(${q(s.waitForText)})`, s.timeout, q(s.waitForText));
    else if ("click" in s){ const ok = await evaluate(`(() => { const e = document.querySelector(${q(s.click)}); if (!e) return false; e.scrollIntoView({block:"center"}); e.click(); return true; })()`); if (!ok) throw new Error("no element matches " + s.click); }
    else if ("clickText" in s){ const ok = await evaluate(`(() => { const e = ${CLICKABLE}.find(x => x.innerText.trim() === ${q(s.clickText)} && !x.disabled); if (!e) return false; e.scrollIntoView({block:"center"}); e.click(); return true; })()`); if (!ok) throw new Error("nothing clickable reads exactly " + q(s.clickText)); }
    else if ("type" in s){ const ok = await evaluate(`(() => { const e = document.querySelector(${q(s.type.selector)}); if (!e) return false; e.focus(); e.value = ${q(String(s.type.text))}; e.dispatchEvent(new Event("input", {bubbles:true})); e.dispatchEvent(new Event("change", {bubbles:true})); return true; })()`); if (!ok) throw new Error("no input matches " + s.type.selector); }
    else if ("key" in s){ for (const type of ["keyDown", "keyUp"]) await cdp("Input.dispatchKeyEvent", { type, key: s.key, code: s.key }); }
    else if ("eval" in s) out.value = await evaluate(s.eval);
    else if ("sleep" in s) await sleep(s.sleep);
    else if ("screenshot" in s){ const r = await cdp("Page.captureScreenshot", { format: "png" }); writeFileSync(s.screenshot, Buffer.from(r.data, "base64")); }
    else throw new Error("unknown step");
    out.ok = true;
  } catch (e){ out.ok = false; out.error = String(e.message || e); failed = true; }
  console.log(JSON.stringify(out));
  if (failed) break;
}
console.log(JSON.stringify({ console: consoleErrors, exceptions }));
ws.close();
process.exit(failed || exceptions.length ? 1 : 0);
