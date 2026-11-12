/* Runway harness shim: stands in for the claude.ai runtime so the built page runs in a plain browser.
   db     an in-memory store with the subset of the db API the page uses (collection/doc, where ==/in, orderBy, limit,
          get/set/update/delete, onSnapshot). It loads from and saves to the harness server (/__db), so state survives
          a reload and tests can read it.
   mcp    callTool("host:Desktop_Commander", "start_process" | "read_file") goes to the harness server, which runs the
          command with HOME set to the sandbox and only reads files inside the sandbox. Every other server is refused.
   sample and everything else resolve null, as on a page where they are not granted. */
(() => {
  const HOST = "host:Desktop_Commander";
  let data = {}; // "col/id" -> object
  const subs = new Set();
  const clone = v => v === undefined ? undefined : JSON.parse(JSON.stringify(v));
  const ready = fetch("/__db").then(r => r.json()).then(d => { data = d || {}; }).catch(() => {});
  let saveT = null;
  const save = () => { clearTimeout(saveT); saveT = setTimeout(() => fetch("/__db", { method: "POST", body: JSON.stringify(data) }).catch(() => {}), 50); };
  // Snapshots arrive later, batched, as from a real store: never inside the write that caused them (a page that writes
  // on every snapshot would otherwise loop forever without yielding).
  let notifyT = null;
  const notify = () => { if (notifyT) return; notifyT = setTimeout(() => { notifyT = null; for (const s of [...subs]) s(); }, 30); };

  const snapDoc = (key) => ({ id: key.split("/").pop(), exists: key in data, data: () => clone(data[key]) });
  function query(col, filters = [], order = null, lim = null){
    const run = () => {
      let docs = Object.keys(data).filter(k => k.slice(0, k.lastIndexOf("/")) === col).map(snapDoc);
      for (const [f, op, v] of filters){
        docs = docs.filter(d => { const x = d.data()[f];
          return op === "==" ? x === v : op === "in" ? v.includes(x) : op === "!=" ? x !== v : op === "array-contains" ? (x || []).includes(v) : true; });
      }
      if (order){ const [f, dir] = order; docs.sort((a, b) => { const x = a.data()[f] ?? 0, y = b.data()[f] ?? 0; return (x < y ? -1 : x > y ? 1 : 0) * (dir === "desc" ? -1 : 1); }); }
      if (lim) docs = docs.slice(0, lim);
      return { docs, size: docs.length, empty: !docs.length };
    };
    return {
      where: (f, op, v) => query(col, [...filters, [f, op, v]], order, lim),
      orderBy: (f, dir = "asc") => query(col, filters, [f, dir], lim),
      limit: n => query(col, filters, order, n),
      get: async () => { await ready; return run(); },
      onSnapshot(cb){ let live = true; const fire = () => live && cb(run()); subs.add(fire); ready.then(() => setTimeout(fire, 0)); return () => { live = false; subs.delete(fire); }; },
      doc: id => docRef(col + "/" + id),
    };
  }
  function docRef(key){
    const write = fn => ready.then(() => {
      const before = JSON.stringify(data[key]);
      fn();
      if (JSON.stringify(data[key]) !== before){ save(); notify(); }  // a write that changes nothing fires nothing
    });
    return {
      id: key.split("/").pop(),
      get: async () => { await ready; return snapDoc(key); },
      set: (body, opts) => write(() => { data[key] = opts?.merge ? { ...(data[key] || {}), ...clone(body) } : clone(body); }),
      update: body => write(() => {
        if (!(key in data)) throw Object.assign(new Error("not found"), { code: "not_found" });
        const cur = data[key];
        for (const [k, v] of Object.entries(clone(body))){
          if (v && v.__delete__) { delete cur[k]; continue; }
          cur[k] = v;
        }
      }),
      delete: () => write(() => { delete data[key]; }),
      onSnapshot(cb){ let live = true; const fire = () => live && cb(snapDoc(key)); subs.add(fire); ready.then(() => setTimeout(fire, 0)); return () => { live = false; subs.delete(fire); }; },
    };
  }
  const db = Object.freeze({ collection: col => query(col), doc: key => docRef(key) });

  async function callTool(server, tool, input){
    if (server !== HOST) throw { code: "server_not_connected", message: server + " is not available in the harness" };
    const r = await fetch("/__mcp", { method: "POST", body: JSON.stringify({ tool, input }) });
    const j = await r.json();
    if (!r.ok) throw { code: "tool_error", message: j.error || "harness tool error" };
    return { payload: j.text, content: [{ type: "text", text: j.text }] };
  }
  /* watchTool polls; the harness polls every 2 s (not the page's minute) so a test sees live updates quickly. */
  function watchTool(server, tool, input, handler, opts = {}){
    let live = true;
    const every = Math.min(opts.refetchInterval || 60000, window.__harnessWatchMs || 2000);
    const once = async () => {
      if (!live) return;
      try { const result = await callTool(server, tool, input); live && handler({ type: "data", result }); }
      catch (error){ live && handler({ type: "error", error }); }
      if (live) setTimeout(once, every);
    };
    once();
    return () => { live = false; };
  }
  const mcp = Object.freeze({ callTool, watchTool, server: async name => ({ start_process: i => callTool(name, "start_process", i), read_file: i => callTool(name, "read_file", i) }) });

  window.claude = { use: async name => name === "db" ? db : name === "mcp" ? mcp : null };
  window.__harness = { dump: () => clone(data), flush: () => fetch("/__db", { method: "POST", body: JSON.stringify(data) }) };
})();
