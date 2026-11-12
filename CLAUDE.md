# Runway

Runway is Austin's agent sprint planner: a claude.ai artifact (the page) plus a local helper on his Mac.

## Layout
- `src/runway.src.html`: the whole page (styles, markup, script). Edit this.
- `helper/runway.py`: the local helper (usage scan, chunk status, launch/stop, merge-batch, land, export). Its `VERSION` must match `HELPER_V` in the page.
- `build.py`: builds `dist/runway.html` (gitignored) with the helper embedded and checks it. Always run `python3 build.py` before finishing.

## Rules for agents
- Never publish the artifact, and never touch the real `~/.ai-planner`, `~/Library/LaunchAgents` or `launchctl`. Austin publishes, and installs the helper from the page.
- Test git behaviour in throwaway repos under a temp dir (see `tests/` once it exists), never in a real project.
- Run the tests with `./test.sh` (from the repo or any worktree; it uses the main checkout's `.venv` and creates it on first use). Don't `pip install` anything and don't write stand-in test runners.
- The page talks to the outside world only through `window.claude.use(...)` capabilities (db, sample, mcp). Don't add network calls.
- Keep the design system in `src/runway.src.html` (`:root` tokens, the `.st` status dot, orange only for "do this"). Every view needs empty, loading and error states.
- Status, merge and land outcomes are data the page reads from `~/.ai-planner/status.json`. Change the helper and the page together, and bump both versions.

## Verifying the page end to end: the harness
You can't open the artifact in the Claude app, and the real Desktop Commander would act on Austin's `~/.ai-planner`. Use the harness instead. It is the page running against a sandboxed Desktop Commander, and it is how to meet criteria like "a real Go/Stop run", "live updates seen in the running app" or "the app-ui checklist passes":
- `python3 build.py`, then `python3 harness/serve.py --port 0` (prints `HARNESS <url> <sandbox>`). It serves `dist/runway.html` with `harness/shim.js` standing in for `window.claude`: an in-memory db seeded from `harness/seed.json` (one project on a scratch git repo, two tasks), and `start_process`/`read_file` run against a temp HOME with this checkout's helper, a fake `claude` (`harness/fake_claude.py`, no tokens) and a helper tick every few seconds.
- Drive it headless with `node harness/drive.mjs steps.json`: goto, waitFor, waitForText, click, clickText, type, key, eval, sleep, viewport and screenshot steps (see the file's header). `eval` can call the page's own functions (`launchNow`, `mergeSelected`, `render`, `S`). Look at screenshots with your Read tool.
- Real runs are fine here: launch, autopilot Go/Stop, merge and land all act on the sandbox only. `HARNESS_AGENT_SLEEP=<s>` slows the fake agent so you can watch a run live; `HARNESS_AGENT=fail` makes it fail.
- `tests/test_harness.py` launches, merges and lands a task through the page this way. Copy it for new end-to-end tests.

## Behaviour contract (2026-10-04)
Runway runs Austin's real projects while agents keep changing it, so:
- Never change the default behaviour of launching, merging, landing or checks. New gates, checks and automation are opt-in (off unless a project or setting turns them on) and keep today's behaviour when unconfigured. Example: the checks gate applies only when a repo has a .runway.json.
- Exception by Austin's decision (2026-10-16): the hands-off policies (auto-merge verified green work into the integration branch, infra retry, one budget resume, already-done, sprint close) are on by default; each is a Settings toggle. Auto-land to the user's branch stays opt-in, and any other new automation remains opt-in.
- Exception by Austin's decision (2026-10-19, PI 10): the can-this-start check (startCheck) and the AI lead (aiLead) are on by default. The AI lead decides only inside the escalation line (never money, clients, anything outside the repo, the brand, or anything irreversible; confidence below 0.6 escalates) and every decision it makes is logged and can be overturned.
- tests/test_contract.py pins what real projects depend on, using plain repos with no Runway config. Never edit it, and never change shared test helpers' defaults, to make a change pass. If your change can't pass it, stop and say so in your report: that is a decision for Austin.
- Changes reach Austin's Mac only through ./release.sh (tests, build, render, a diff of what changed). Never copy the helper into ~/.ai-planner yourself.
