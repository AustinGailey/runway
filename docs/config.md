# Project config: `.runway.json`

Each project can declare how its work is tested and merged in a `.runway.json` file at the repo root, committed with the project. The helper reads it before any merge into the base branch. A missing file means the defaults below.

```json
{
  "type": "python",
  "test": ["python3 -m pytest -q"],
  "build": null,
  "baseBranch": "main",
  "mergeStrategy": "merge"
}
```

Every key is optional. Keys you leave out take the default for the project's type.

## Fields

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `type` | `"site"` or `"python"` | detected (see below) | Picks the defaults for the other fields. |
| `test` | command, or list of commands | `site`: `[]` · `python`: `["python3 -m pytest -q"]` | Test commands, run in order from the repo root. `[]` means no tests. |
| `build` | command, or `null` | `null` | Optional build or render check, one command. `null` means none. |
| `baseBranch` | branch name, or `null` | `null` | Branch that approved work lands on. `null` uses whatever the checkout is on, as today. |
| `mergeStrategy` | `"squash"` or `"merge"` | `site`: `"squash"` · `python`: `"merge"` | `squash` lands each task as one commit. `merge` keeps the task's commits. |

Unknown keys are an error, so a typo such as `tests` fails loudly instead of being ignored. The error names the closest known key.

## Checks

The merge gate runs the project's checks in the task's worktree before it merges. They are a `checks` list in the same `.runway.json`, separate from the settings table above:

```json
{
  "checks": [
    {"name": "tests", "cmd": "./test.sh"},
    {"name": "build", "cmd": "python3 build.py"}
  ]
}
```

- `checks` is a list of objects, run in order. Each object needs:
  - `name`: a non-empty string, shown in status. If it is left out, the `cmd` is shown instead.
  - `cmd`: a non-empty string, run with `bash -c` from the worktree root.
- Optional per-check `timeout` in seconds. It defaults to 600, and a top-level `timeout` sets the default for every check.
- Checks run in order. A check that exits non-zero, or runs past its timeout, stops the run. Its output is written to the task's check log, and a red or timed-out check adds a line to `failures.log` and to the merge-train log (`merge-log`).
- A project with no `checks` list cannot merge: the gate refuses and says so.
- A malformed `checks` value is reported as a bad `.runway.json`, for example `"checks" must be a list of {"name", "cmd"} objects`. The settings keys ignore `checks`, so the two parts of the file do not interfere.

## Project type

If `type` is not set, the helper detects it:

- `python` when the repo root has `pyproject.toml`, `setup.py`, `setup.cfg` or `requirements.txt`, or has a `.py` file at the root or one folder down.
- `site` otherwise (a static site, such as a brochure site built from HTML).

Set `type` yourself when detection guesses wrong.

## Precedence

Settings are resolved key by key, later sources winning:

1. Built-in defaults for the type.
2. `.runway.json` in the repo.
3. Overrides from the page's project record, when the page sends them.

The type is settled first: an override's `type` picks the defaults, and the file's `type` beats detection. For example, a file with `"type": "site"` and an override with `"type": "python"` gets the python defaults.

The project record's allowed-commands list (`allowedTools` in the page) is separate. It is not read from or written to `.runway.json`.

## Validation

A bad file stops merges for that project with a message that names the file and the problem, for example:

```
.runway.json has an unknown key "tests" (did you mean "test"?). Known keys: type, test, build, baseBranch, mergeStrategy.
.runway.json: "mergeStrategy" must be "squash" or "merge", not "rebase".
```

Discard and status still work with a bad file, so a broken config never traps a task's worktree.

## Status

The loader validates the file and the merge path runs it first. Two parts are not acted on yet: `test` and `build` are not run, and `mergeStrategy` does not change how work lands. Those come in later sprints.

The task environment keys (`setup`, `teardown`, `env`, `ports`) are described in the README. `setup`, `env` and `ports` are acted on before a task's agent starts: the autopilot takes the task's port block from `~/.ai-planner/ports.json`, reads the named variables from `~/.ai-planner/env/<repo folder>.env`, and runs `setup` with both. A missing variable or a failed setup finishes the task as an environment blocker, with no agent run. `teardown` runs once the task's environment ends: after a merge, on discard, on stop, when its worktree is cleaned up, and from `runway.py env-gc` (run hourly by tick) for environments whose worktree is gone or whose chunk ended over a day ago. It gets the same `RUNWAY_*` variables, runs in the worktree (or the repo, once the worktree is gone), and releases the task's port block. A failed teardown is logged to `<key>.teardown.log` and never blocks a merge.

## Editing from the page

`.runway.json` is never read from or written to by the page's own project record (see Precedence above): it is a file in the repo, committed with the project. The one exception is the worktree setup command: the "Edit project" form has a "Worktree setup command" field that reads and writes the `setup` key directly, via `runway.py config-get REPO KEY` and `config-set REPO KEY VALUE` on your Mac. An empty field clears the key back to the default (no setup step). This is the same `setup` key `_ap_prepare` reads through `project_config`, so what the form saves is what the next worktree runs.
