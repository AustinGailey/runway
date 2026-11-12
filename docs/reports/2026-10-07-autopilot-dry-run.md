# Autopilot dry run on Runway's own repo

Date: 2026-10-07 · Sprint 3 · Prove it and report · Task: dry-run autopilot on the Runway repo and log defects

## Answer first

The autopilot ran twice, unattended, against a sandboxed clone of Runway's own `main` branch, each time generating a roadmap of low-risk docs/refactor chunks (through the real `generate_roadmap`/approve path), then running the real `autopilot()` loop with a scripted fake agent (no real Claude, no tokens, no real cost) that made and committed a small real edit per task.

- **Pass 1** (5 docs/refactor chunks, cap $0.50): all 5 merged unattended. No manual git work. Spend ended at **$0.555, 111% of the $0.50 cap**, but the run's stop reason was `roadmap` (queue finished), not `cap` — the cap only vets a task's *projected* cost before it starts, so a task whose real use runs well above its own estimate can still push total spend over the cap without ever triggering a cap stop. See finding A1.
- **Pass 2** (3 chunks, one deliberately breaking the build check, cap $1.00): 2 merged, 1 blocked unattended, with a clear reason (`red: build`) and no manual git work. The failure was correctly logged to both `failures.log` and the merge-train log (confirming the 2026-10-04 trial's D1/D2 fixes hold for autopilot too). But the blocked task's own message says **"The worktree is kept"**, and it was not: autopilot's end-of-run cleanup removed it anyway. See finding A2 (and the new merge-train-log entry D9).

Both passes satisfy this task's first acceptance criterion twice over (≥3 chunks completed unattended, and a clear stop reason). No manual git command was ever run against the subject repo; every merge, land and cleanup was the helper's own code.

## Setup and reading taken

- **Subject repo.** A clone of `~/Projects/runway` at `main` (commit `65fc619`), into `/tmp/runway-dryrun/main-check`. Not this task's worktree, and not the real checkout: CLAUDE.md forbids testing git behaviour in a real project, so this is a throwaway clone under `/tmp`, same precedent as the 2026-10-04 merge-train trial.
- **Isolation.** Every helper call ran with `HOME=/tmp/runway-dryrun/home`, so `~/.ai-planner`, `~/Library/LaunchAgents` and `launchctl` were never touched. Nothing was published, released, pushed or merged into this task's own worktree or branch; the only commits made are inside the throwaway clone.
- **Helper.** `helper/runway.py` from this worktree (branch `runway-task/t6tyg5xodd`), unmodified, VERSION 37.
- **Driver.** A standalone script in `/tmp/runway-dryrun/` (not part of the repo), built on the same pattern as `tests/test_autopilot_stops.py`: it imports `runway` directly and calls `runway.generate_roadmap`, `runway._cmd_roadmap_approve` and `runway.autopilot` for real, with a scripted fake launcher standing in for `claude`.

## Manual interventions

Each row is something done by hand that a real unattended run on a healthy project would not need, with its cause. None of these changed any helper or page code.

| # | Intervention | Cause |
|---|---|---|
| M1 | The subject's `.runway.json` was rewritten to one check (`python3 build.py`) instead of the real repo's two (`./test.sh`, `python3 build.py`) | The full suite has one pre-existing failure on `main` itself, `tests/test_harness.py::test_the_card_updates_live_from_launch_to_review` (confirmed failing on a fresh clone of `main`, unrelated to anything this task touches). Running it as a merge-train check would block every task in this dry run regardless of its own content, and misrepresent autopilot's real behaviour. Left as an aside for Austin, not fixed here (out of this task's scope; it is not an autopilot defect). |
| M2 | The roadmap was generated with a canned `ask(prompt, repo)` instead of a real headless Claude call | `generate_roadmap` takes `ask` as an injectable collaborator for exactly this reason. Using the real `claude_reply` would cost real tokens for a planning step this task does not need to validate; it is prior-tested by `tests/test_roadmap_generate.py`. The real `roadmap_prompt`/`parse_roadmap`/`plan_rule_errors` validation path still ran, unchanged. |
| M3 | Each task's "agent" was a scripted launcher, not a real `claude` run | No real agent run was authorized or needed: it would cost tokens for edits this task does not need an LLM to make (appending one tidy-up line, or one deliberately broken line, to a known file). The launcher still went through the real worktree creation, `run.sh` staging, status/usage files and merge gate — the same surface a real headless run leaves behind, as `tests/test_autopilot_stops.py` does it. |
| M4 | `HOME` was redirected to a sandbox for every call | Keeps `~/.ai-planner`, LaunchAgents and the real git identity untouched (CLAUDE.md). |

## Pass 1: five low-risk chunks, cap $0.50

Roadmap (generated, approved, one sprint, no dependencies): tidy README wording, fix a typo in `docs/config.md`, fix a stale cross-reference in the merge-train runbook, tidy wording in CLAUDE.md, and a small refactor (a clearer local name) in `helper/runway.py`. Chosen to be genuinely low-risk: each touches one file, none touches test or build logic.

Loop log:

```
dollar cap $0.50
launch t-readme: Tidy README wording
done t-readme
launch t-config-docs: Fix a typo in docs/config.md
done t-config-docs
launch t-runbook-docs: Fix a stale cross-reference in the merge train runbook
done t-runbook-docs
launch t-claude-md: Tidy wording in CLAUDE.md
done t-claude-md
launch t-refactor-var: Small refactor: clearer local name in a helper function
done t-refactor-var
stopping (roadmap): Nothing ready is left: 5 done, 0 blocked, 0 waiting on a blocked task.
```

### Run report (as written by `write_ap_report`, verbatim)

```markdown
# Autopilot run report: Runway docs tidy and small refactors

Run `autopilot-main-check-autopilot-57d582-1791403360` in `/tmp/runway-dryrun/main-check`. Stopped: **queue finished**. Nothing ready is left: 5 done, 0 blocked, 0 waiting on a blocked task.

Started 2026-10-07 20:02 UTC, ended 2026-10-07 20:02 UTC: 0.0 min wall-clock.

## Summary

| Attempted | Merged | Blocked | In review | Left to do |
| ---: | ---: | ---: | ---: | ---: |
| 5 | 5 | 0 | 0 | 0 |

Spend $0.555 of the $0.50 cap (111.0%), $-0.055 left.

## Tasks

| Task | Outcome | Est. tokens | Actual tokens | Actual ÷ est. | Model | Cost |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| t-readme: Tidy README wording | merged | 15,000 | 15,000 | ×1.00 | claude-sonnet-5 | $0.045 |
| t-config-docs: Fix a typo in docs/config.md | merged | 20,000 | 20,000 | ×1.00 | claude-sonnet-5 | $0.060 |
| t-runbook-docs: Fix a stale cross-reference in the merge train runbook | merged | 20,000 | 20,000 | ×1.00 | claude-sonnet-5 | $0.060 |
| t-claude-md: Tidy wording in CLAUDE.md | merged | 25,000 | 60,000 | ×2.40 | claude-sonnet-5 | $0.180 |
| t-refactor-var: Small refactor: clearer local name in a helper function | merged | 40,000 | 70,000 | ×1.75 | claude-sonnet-5 | $0.210 |
| **Total** | | 120,000 | 185,000 | | | $0.555 |

## Needs a person

- Spend $0.5550 went over the $0.50 cap.

## Reconciliation

Budget ledger: 5 rows, $0.555, 185,000 effective tokens. The tasks account for $0.555; unattributed $0.000. Reconciled.
```

(Time and calibration-drift tables omitted here for length; both were present and internally consistent in the real report.)

Git result in the clone: all five commits landed on `main`, each as its own merge (not squashed, since `.runway.json` left `mergeStrategy` at its `python`-repo default of `merge`), in the order the roadmap listed them. No manual git command was run.

## Pass 2: a deliberate break, cap $1.00

A second roadmap (3 tasks, no dependencies): two more low-risk docs tidy-ups, plus one task that deliberately appends invalid Python to `helper/runway.py` (`def _dry_run_broken(:`), breaking the `build` check (which `py_compile`s the helper) on purpose, to exercise the blocked path.

Loop log:

```
dollar cap $1.00
launch t-docs-2a: Tidy wording in docs/MERGE_TRAIN_RUNBOOK.md (pass 2)
done t-docs-2a
launch t-break-build: Small refactor that breaks the build (deliberate)
blocked t-break-build: Its checks did not pass (red: build). Fix it and run the task again, or run checks. The worktree is kept.
launch t-docs-2b: Tidy wording in README.md (pass 2)
done t-docs-2b
stopping (roadmap): Nothing ready is left: 2 done, 1 blocked, 0 waiting on a blocked task.
```

`failures.log`:

```json
{"at": 1791403455, "chunk": "ap-t-break-build", "task": "t-break-build", "state": "red", "check": "build", "cmd": "python3 build.py", "rc": 1, "reason": "exit 1", "worktree": "/tmp/runway-dryrun/main-check/.claude/worktrees/runway-t-break-build", "log": ".../t-break-build.checks.log"}
```

The merge-train log also got a matching `"phase": "test", "exit_code": 1` entry with the build traceback's tail. Both logs agreeing on the red check confirms the 2026-10-04 merge-train trial's D1/D2 fixes still hold when the failure comes through the autopilot loop rather than `merge-batch`.

`runway.py run ap-t-break-build t-break-build` after the run ended still reported the same red result: `run`'s planner (`_plan`) recreates a missing worktree from the still-existing branch before running checks, so the task is recoverable even though its worktree was removed (see A2). No data was lost in this dry run because the agent always commits before the checks run; a real agent that died with uncommitted edits would be a different, better-covered case (git refuses to remove a dirty worktree, and that *is* correctly recorded in the run's `"kept"` map).

### Run report, verbatim

```markdown
# Autopilot run report: Runway docs tidy and small refactors (pass 2: a deliberate break)

Run `autopilot-main-check-autopilot-57d582-1791403454` in `/tmp/runway-dryrun/main-check`. Stopped: **queue finished**. Nothing ready is left: 2 done, 1 blocked, 0 waiting on a blocked task.

## Summary

| Attempted | Merged | Blocked | In review | Left to do |
| ---: | ---: | ---: | ---: | ---: |
| 3 | 2 | 1 | 0 | 0 |

Spend $0.180 of the $1.00 cap (18.0%), $0.820 left.

## Tasks

| Task | Outcome | Est. tokens | Actual tokens | Actual ÷ est. | Model | Cost |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| t-docs-2a: Tidy wording in docs/MERGE_TRAIN_RUNBOOK.md (pass 2) | merged | 15,000 | 15,000 | ×1.00 | claude-sonnet-5 | $0.045 |
| t-break-build: Small refactor that breaks the build (deliberate) | blocked (red) | 30,000 | 30,000 | ×1.00 | claude-sonnet-5 | $0.090 |
| t-docs-2b: Tidy wording in README.md (pass 2) | merged | 15,000 | 15,000 | ×1.00 | claude-sonnet-5 | $0.045 |
| **Total** | | 60,000 | 60,000 | | | $0.180 |

## Needs a person

- **t-break-build:** Blocked: Its checks did not pass (red: build). Fix it and run the task again, or run checks. The worktree is kept.

## Reconciliation

Budget ledger: 3 rows, $0.180, 60,000 effective tokens. The tasks account for $0.180; unattributed $0.000. Reconciled.
```

## Findings

Full defect write-ups: a new finding appended to [the 2026-10-04 merge-train trial's defect list](2026-10-04-merge-train-trial.md) (D9: the worktree-kept message, shared merge-gate code), and a new [autopilot defect list](2026-10-07-autopilot-defects.md) (the cap-vs-overspend gap, and smaller items). Severity is ordered High/Medium/Low in both. Nothing was fixed in helper or page code during this dry run.

## Acceptance check

- Autopilot completed 3+ chunks unattended: **yes**, 5/5 in pass 1 and 2/3 in pass 2, with pass 2's one blocked task stopping with a clear, correctly-logged reason.
- Run report attached and committed under `docs/`: **yes**, both passes' reports are reproduced verbatim above.
- Every manual intervention recorded with cause: **yes**, see the table above; none touched helper or page code.
- Defect list committed with severity ordering: **yes**, see the two linked files.

## Not covered, and why

- **A real sample-site repo.** Out of scope: this task asks for a dry run on Runway's own repo specifically.
- **A real launched `claude` agent.** Not run, so the roadmap-generation and agent-execution paths are exercised through their real code with injected collaborators, not through a live model call; see M2 and M3.
- **Resume, stop-signal and multi-task-in-flight (`--parallel > 1`) paths.** Not exercised in either pass; both ran serially to keep the dry run small, per the task's "3-5 chunks" scope.
