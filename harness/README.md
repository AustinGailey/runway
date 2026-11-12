# Runway harness

The built page, a sandboxed Desktop Commander and an in-memory database, on localhost. With it, agents and tests can run Runway end to end without the Claude app, real agents or the real `~/.ai-planner`. See the "Verifying the page" section of `CLAUDE.md`.

```
python3 build.py
python3 harness/serve.py --port 0          # prints: HARNESS http://127.0.0.1:PORT/ /path/to/sandbox
node harness/drive.mjs steps.json          # one JSON line per step; exit 1 on the first failure
```

- `serve.py`: makes the sandbox (helper copy, scratch repo `site/`, fake `claude`), serves the page with `shim.js` injected, answers `start_process` (HOME = sandbox, `RUNWAY_CLAUDE` = the fake) and `read_file` (sandbox paths only), keeps the db in `<sandbox>/harness-db.json`, and ticks the helper.
- `shim.js`: `window.claude.use("db" | "mcp")`. Snapshots arrive asynchronously and only on real changes, as from the real store.
- `drive.mjs`: headless Chrome over the DevTools protocol, no npm packages.
- `fake_claude.py`: prints a stream and a result, writes `harness-agent.txt`. `HARNESS_AGENT=fail`, `HARNESS_AGENT_SLEEP=<s>`.
- `seed.json`: the starting db; `{{HOME}}`, `{{REPO}}` and `{{CLAUDE}}` are filled in.
