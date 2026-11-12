# Task environments in `.runway.json`

Four optional keys say what a task needs before its agent starts. A file without them behaves exactly as before.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `setup` | command, or list of commands | none | Run once in a fresh task worktree before the agent starts. A list runs in order and stops at the first failure, which blocks the task. |
| `teardown` | command, or list of commands | none | Run when the task's environment ends (merge, discard, stop, cleanup). Releases its ports. |
| `env` | list of variable names | `[]` | Names the agent may receive, such as `["DATABASE_URL"]`. Values come from `~/.ai-planner/env/<repo-folder-name>.env`, a file outside every repo that you fill in. Runway never reads the repo's own `.env` files. Names only: `"DATABASE_URL=..."` is rejected. |
| `ports` | whole number, 0 to 20 | `0` | How many ports the task needs. |

A bad value is reported with a message that names the key, for example `"ports" must be a whole number from 0 to 20, not 21.` It blocks the task's setup or the merge, and the rest of the file is not guessed at.

Example 1, a Next.js project run with pnpm:

```json
{
  "setup": ["pnpm install --frozen-lockfile", "pnpm exec playwright install chromium"],
  "teardown": "rm -rf .next",
  "env": ["NEXT_PUBLIC_API_URL", "DATABASE_URL"],
  "ports": 1,
  "checks": [{"name": "build", "cmd": "pnpm build"}]
}
```

Example 2, a Python project with a virtualenv:

```json
{
  "type": "python",
  "setup": ["python3 -m venv .venv", ".venv/bin/pip install -r requirements.txt"],
  "teardown": "rm -rf .venv",
  "env": ["OPENAI_API_KEY"],
  "ports": 0,
  "checks": [{"name": "tests", "cmd": ".venv/bin/python -m pytest -q"}]
}
```

`setup`, `env` and `ports` apply to each task the autopilot starts, before its agent runs. Each task with `ports` gets its own block from `~/.ai-planner/ports.json`, given to setup, the agent and the checks as `RUNWAY_PORT_BASE` and `RUNWAY_PORTS` (comma-separated), with `RUNWAY_TASK` set to the task's key. Named variables come from the env file and go to the same processes through their environment only. If a variable is missing or setup fails, the task stops with an environment blocker and its agent never starts. `teardown` runs when a task's environment ends (after a merge, on discard, on stop), with the same variables, and releases its port block. Environments whose worktree is gone, or whose chunk ended over a day ago, are torn down hourly. See the Status section in [docs/config.md](docs/config.md).


