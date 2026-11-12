// Drives the page in headless Chrome over the DevTools protocol (Node's built-in WebSocket; nothing installed). The helper is
// fixtured: its status carries environment records as the helper writes them, and host() is stubbed so the test can see the
// command that Retry setup sends and answer it. Checks the Runs view, the Retry setup click, the task drawer and the project
// form, and that no secret value reaches the page. Usage: node env_render_check.js <chrome> <page.html>. Exits 1 on any failure.
const { spawn } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path");

const [, , chrome, pageFile] = process.argv;
const SECRET = "sentinel-s3cr3t-value";
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "runway-env-render-"));
const sleep = ms => new Promise(r => setTimeout(r, ms));

// Chrome picks a free DevTools port and writes it to DevToolsActivePort in its profile, so this never attaches to another browser.
// The real home directory, not $HOME: the suite points HOME at a temp folder, and Chrome does not boot the page then.
const chromeHome = os.userInfo().homedir;
const chromeProc = spawn(chrome, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--user-data-dir=" + profile, "--remote-debugging-port=0", "--window-size=1280,900",
  "--host-resolver-rules=MAP * ~NOTFOUND", "about:blank"], { stdio: "ignore", detached: true,
  env: { ...process.env, HOME: chromeHome } });  // Chrome fails to load pages when HOME is the suite's missing temp home

// The fixture: what the helper would report for two tasks, one failed and one ready, plus a preparing and an error record.
const DATA = {
  home: "/fixture/home",
  projects: [{ id: "p1", name: "Site", repo: "/fixture/site", status: "active", goal: "", agentNotes: "", worktrees: true, wiki: "main", parentId: "" }],
  tasks: [{ id: "t-a", projectId: "p1", piId: "", sprintId: "", title: "Task A", detail: "", acceptance: "", status: "todo", rkey: "a",
            estTokens: 0, model: "", kind: "code", order: 1, createdAt: 1, run: {} }],
  envs: [
    { task: "a", repo: "/fixture/site", state: "failed", names: [{ name: "API_KEY", set: true }], ports: { base: 41000, count: 2 },
      setup: { cmd: "npm ci", rc: 3, timedOut: false, at: 1, log: "installing\ntoken=••••\nbroke\n[setup exit 3]" }, detail: null },
    { task: "b", repo: "/fixture/site", state: "preparing", names: [], ports: null, setup: { cmd: "npm ci", rc: null, timedOut: false, at: 2, log: "npm ci\n" }, detail: null },
    { task: "c", error: "could not read the run folder" },
  ],
  replies: [
    ["retry-setup", "@@SETUP_RETRY launched a"],
    ["env-info", "@@ENVINFO " + JSON.stringify({ repo: "/fixture/site", file: "/fixture/home/.ai-planner/env/site.env", exists: true,
      ports: 2, setup: true, names: [{ name: "API_KEY", set: true }, { name: "OTHER", set: false }] })],
  ],
};

// Runs in the page: replaces the page's state with the fixture and renders it. S is the page's own state object.
function setup(d) {
  window.__calls = [];
  S.loaded = true; S.view = "runs"; S.projects = d.projects; S.projectId = "p1"; S.tasks = d.tasks; S.settings.home = d.home;
  S.mcp = { callTool: async (h, name, args) => {
    const cmd = (args && args.command) || name;
    window.__calls.push(cmd);
    const hit = d.replies.find(([m]) => cmd.includes(m));
    return { payload: hit ? hit[1] : "" };
  }, watchTool: () => () => {} };
  S.helper = { v: 34, chunks: {}, envs: d.envs };
  render();
}

async function main() {
  const checks = [];
  const check = (name, ok, extra = "") => checks.push({ name, ok: !!ok, extra });
  let ws, evaluate = null;
  try {
    // The DevTools port: the page target's websocket URL.
    let wsUrl = null, port = null;
    for (let i = 0; i < 300 && !wsUrl; i++) {
      if (!port) {
        const active = path.join(profile, "DevToolsActivePort");
        if (fs.existsSync(active)) port = fs.readFileSync(active, "utf8").split("\n")[0].trim();
      }
      if (!port) { await sleep(100); continue; }
      try {
        const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
        wsUrl = (targets.find(t => t.type === "page") || {}).webSocketDebuggerUrl || null;
      } catch (e) { /* Chrome is still starting */ }
      if (!wsUrl) await sleep(100);
    }
    if (!wsUrl) throw new Error("Chrome did not open its DevTools port");
    ws = new WebSocket(wsUrl);
    let id = 0;
    const pending = new Map();
    ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
    evaluate = async expr => {
      const m = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
      if (m.result.exceptionDetails) throw new Error((m.result.exceptionDetails.exception || {}).description || m.result.exceptionDetails.text);
      return m.result.result.value;
    };
    const text = () => evaluate("document.body.innerText");
    // Waits until the page shows what the step should show (up to 8 s), so a slow render does not fail the check.
    const waitFor = async expr => { for (let i = 0; i < 80; i++) { if (await evaluate(`!!(${expr})`).catch(() => false)) return true; await sleep(100); } return false; };

    await send("Page.enable");
    await send("Page.navigate", { url: "file://" + pageFile });
    for (let i = 0; i < 300; i++) { // up to 30 s for the page to boot
      if (await evaluate("!!document.querySelector('#content') && typeof S !== 'undefined' && typeof render === 'function'").catch(() => false)) break;
      await sleep(100);
    }
    await evaluate(`(${setup.toString()})(${JSON.stringify(DATA)})`);
    await sleep(300);

    // Runs: the environment section, the failed card and the log.
    let body = await text();
    check("Runs shows the Environments section", body.includes("Environments"));
    check("a failed environment shows its card", body.includes("Environment not ready"));
    check("the failed card has a Retry setup button", (await evaluate(`!!document.querySelector('[data-act="env-retry"]')`)) === true);
    check("the setup log is shown, with its exit code", body.includes("Setup log") && body.includes("[setup exit 3]"));
    check("a secret the setup printed is shown blanked", body.includes("token=••••") && !body.includes(SECRET));
    check("the ports show as a block", body.includes("41000–41001"));
    check("a preparing environment shows as preparing", body.includes("Preparing"));
    check("an environment that could not be read shows its error", body.includes("could not read the run folder"));

    // Retry setup: the click sends the helper's retry command for this project and task, and the toast says what happened.
    await evaluate(`document.querySelector('[data-act="env-retry"]').click()`);
    await sleep(500);
    await waitFor(`window.__calls.some(c => c.includes("retry-setup"))`);
    await waitFor(`document.querySelector('#toast') && !document.querySelector('#toast').hidden`);
    const calls = await evaluate("window.__calls");
    check("Retry setup sends retry-setup for the project's repo and the task", calls.some(c => /retry-setup '\/fixture\/site' 'a'/.test(c)), JSON.stringify(calls));
    check("the result of the retry is reported", (await evaluate("document.querySelector('#toast') && !document.querySelector('#toast').hidden && document.querySelector('#toast').textContent")) === "Setup passed. The agent is starting.");

    // The task drawer: the same environment for a task imported with that roadmap key.
    await evaluate(`S.overlay = { kind: "task", id: "t-a", tab: "run" }; S._ovSig = null; render();`);
    await waitFor(`document.querySelector('.drawer') && document.querySelector('.drawer').innerText.includes('Environment not ready')`);
    body = await text();
    check("the task drawer shows the environment", body.includes("Environment not ready") && body.includes("Ports"));

    // The project form: names and whether each is set, the env file to fill in, and never a value.
    const formRender = await evaluate(`(() => { S.overlay = { kind: "project", id: "p1" }; S._ovSig = null;
      try { render(); return "rendered, modal " + !!document.querySelector(".modal"); } catch (e) { return String(e.stack || e); } })()`);
    if (!/^rendered, modal true/.test(formRender)) console.log("project form render: " + formRender);
    await waitFor(`document.querySelector('.modal') && document.querySelector('.modal').innerText.includes('OTHER')`);
    body = await text();
    const shown = await evaluate(`(document.querySelector(".modal") || {}).innerText || "(no modal)"`);
    check("the project form lists the variable names and whether each is set", body.includes("API_KEY") && body.includes("OTHER") && body.includes("not set"), shown);
    check("the project form shows the env file path", body.includes("/fixture/home/.ai-planner/env/site.env"));
    check("no secret value is on the page", !body.includes(SECRET));
  } catch (e) {
    let state = "";
    try { state = await evaluate(`JSON.stringify({ href: location.href, ready: document.readyState, typeofS: typeof S, errors: window.__renderErrors, body: document.body.innerText.slice(0, 200) })`); } catch (e2) { state = String(e2); }
    check("the page ran", false, String(e && e.stack || e) + " :: " + state);
  } finally {
    if (ws) ws.close();
    try { process.kill(-chromeProc.pid, "SIGKILL"); } catch (e) { /* already gone */ }
  }
  const failed = checks.filter(c => !c.ok);
  for (const c of checks) console.log((c.ok ? "ok    " : "FAIL  ") + c.name + (c.ok || !c.extra ? "" : " :: " + c.extra));
  process.exit(failed.length ? 1 : 0);
}

main();
