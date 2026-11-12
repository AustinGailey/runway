# Merge train trial: one multi-chunk build, end to end

Date: 2026-10-04 · Sprint 3 · Prove it on sample sites · Task: run merge train end-to-end on a sample site repo

## Answer first

The merge train ran a five-task, two-chunk build from start to finish: three tasks merged, one failed its checks, one conflicted with approved work. The ledger and failure log were written, and the land into `main` worked. But the trial found two problems that matter for the sprint's go/no-go:

1. **Conflicts against approved work never reach the failure log.** `merge-log` printed "No merge-train failures logged." while a task sat in conflict.
2. **The ledger measures helper machine time only.** It recorded 0.01 minutes for five tasks. Agent time, review time and my manual work are not in it, so it cannot yet show the git/merge minutes saved.

Two things were not done as a real build. The run used a **scratch repo**, not a sample site, and the "agents" were **scripted commits**, not launched agents. Every such step is listed under Manual interventions. This trial therefore proves the helper's merge and failure paths. It does not prove the unattended build on a real sample site.

## Scope and reading taken

- **Repo.** The task asks for a sample site repo. The only real candidate, a private site monorepo, is read-only for this task and has an uncommitted change, and CLAUDE.md forbids testing git behaviour in real projects. So the trial ran in a throwaway repo under `/tmp/trial/sample-site`, with a `.runway.json` that declares the checks. Configuring a real sample site is a decision for Austin (see Follow-ups for Austin, item 1).
- **Isolation.** Every helper call ran with `HOME=/tmp/trial/home`, so `~/.ai-planner`, `~/Library/LaunchAgents` and `launchctl` were not touched. Nothing was pushed, released or published.
- **Helper.** `helper/runway.py` from this worktree, VERSION 26.
- **Shape of the build.** Chunk `c1` has tasks t1 (green), t2 (green) and t3 (deliberately red). Chunk `c2` has t4 (green) and t5 (conflicts with t4 on the same `<h1>` line).

## Setup

| Item | Value |
|---|---|
| Scratch repo | `/tmp/trial/sample-site`, branch `main`, one commit with `index.html`, `test.sh` (greps for `<title>`), `.runway.json` |
| `.runway.json` | `{"type":"site","baseBranch":"main","mergeStrategy":"merge","checks":[{"name":"tests","cmd":"./test.sh"}]}` |
| Task meta | `$HOME/.ai-planner/runs/<chunk>/<task>.meta` with `repo`, `wt`, `base` (written by hand) |
| Scripts | `/tmp/trial-run.sh` (pass 1) and `/tmp/trial-fix.sh` (pass 2). Both sit outside the repo and are not committed. |

## Chunk-by-chunk outcomes

### Pass 1 (`trial1`)

| Chunk | Task | Agent change (scripted) | `run` checks | Merge outcome | Notes |
|---|---|---|---|---|---|
| c1 | t1 | adds `contact.html` | green | **merged** | |
| c1 | t2 | adds `services.html` | green | **merged** | |
| c1 | t3 | deletes `<title>`, adds `broken.txt` | **red** (`tests`, exit 1) | **error**: "Its checks did not pass (red: tests)… The worktree is kept." | Deliberate failure. The check log is at `runs/c1/t3.checks.log`. |
| c2 | t4 | changes `<h1>` to "Fresh paint, fast quotes" | green | **merged** | |
| c2 | t5 | changes the same `<h1>` to "Interior and exterior experts" | green | **conflict**: "Conflicts with approved work in index.html." | Deliberate conflict. Its checks passed, so the conflict is found only at merge time. |

The land into `main` then ran and reported "Landed in main." `main` now has t1, t2 and t4 in a merge commit, with t3 and t5 excluded.

### Pass 2 (`trial2`), after manual intervention

| Chunk | Task | Manual step | Result |
|---|---|---|---|
| c1 | t3 | Restored the `<title>` line and removed `broken.txt`, so the branch's net diff against `main` became empty. `retry c1 t3` then went green. | **empty**: "The agent made no changes. Discard it and re-run the task." |
| c2 | t5 | Rebased onto `main` and hand-resolved `index.html` to "Fresh paint, fast quotes: interior and exterior experts". Re-ran `run c2 t5`. | **merged**, and landed in `main` |

Result: the trial never showed a red task going green **and** merging. My fix for t3 cancelled its change entirely, so the helper reported "empty". That is correct behaviour for what I did, but it is not the retry-to-merge path. Left as a gap (see Not covered).

## Failure log excerpts

`failures` (pass 1, before the fix):

```
2026-10-04 18:22  c1/t3  red  check tests, exit 1
    log: /tmp/trial/home/.ai-planner/runs/c1/t3.checks.log
```

`merge-log 10` (same moment): `No merge-train failures logged.`

`failures.log` after pass 2 (raw line, the only entry in the file):

```json
{"at": 1791159764, "chunk": "c1", "task": "t3", "state": "red", "check": "tests", "cmd": "./test.sh", "rc": 1, "reason": "exit 1", "worktree": "/tmp/trial/sample-site/.claude/worktrees/t3", "log": "/tmp/trial/home/.ai-planner/runs/c1/t3.checks.log"}
```

There is **no entry for the t5 conflict** in either log. See D1.

## Ledger excerpts

`ledgers/trial1.json`, totals and per-task rows:

```json
"totals": {"tasks": 5, "machine_min": 0.01, "git_merge_min": 0.01, "setup_min": 0.0,
           "test_min": 0.01, "merge_min": 0.01, "review_min": null, "client_min": null,
           "retries": 0, "conflicts_resolved": 0, "human_touches": 2}
{"ch": "c1", "tid": "t1", "outcome": "merged", "setup_ms": 0, "test_ms": 180, "merge_ms": 73, "retries": 0, "conflicts_resolved": 0, "human_touches": 0}
{"ch": "c1", "tid": "t3", "outcome": "error",  "setup_ms": 0, "test_ms": 70,  "merge_ms": 5,  "retries": 0, "conflicts_resolved": 0, "human_touches": 1}
{"ch": "c2", "tid": "t5", "outcome": "conflict", "setup_ms": 0, "test_ms": 76, "merge_ms": 68, "retries": 0, "conflicts_resolved": 0, "human_touches": 1}
```

`ledgers/trial2.json`, totals and per-task rows:

```json
"totals": {"tasks": 2, "machine_min": 0.01, "git_merge_min": 0.0, "setup_min": 0.0,
           "test_min": 0.0, "merge_min": 0.0, "review_min": null, "client_min": null,
           "retries": 2, "conflicts_resolved": 0, "human_touches": 1}
{"ch": "c1", "tid": "t3", "outcome": "empty",  "setup_ms": 0, "test_ms": 81,  "merge_ms": 34,  "retries": 1, "conflicts_resolved": 0, "human_touches": 1}
{"ch": "c2", "tid": "t5", "outcome": "merged", "setup_ms": 0, "test_ms": 86,  "merge_ms": 154, "retries": 1, "conflicts_resolved": 0, "human_touches": 0}
```

What the ledger shows: the per-task rows and outcomes are right. The minutes are not a time saving. Total machine time was under a second, `review_min` and `client_min` are null, and nothing records the time I spent on manual steps. `retries: 1` on t5 is unexplained: I re-ran `run` by hand, and the `retry` command was never used for t5 (D4).

## Merge-batch states

| Task | pass 1 | pass 2 |
|---|---|---|
| t1 | merged | — |
| t2 | merged | — |
| t3 | error (red) | empty |
| t4 | merged | — |
| t5 | conflict | merged |

Land: `Landed in main.` in both passes.

## Manual interventions

Each row is something I did that a real unattended run would not have needed, with its cause.

| # | Intervention | Cause |
|---|---|---|
| M1 | Used a scratch repo, not the private site monorepo | The real repo has an uncommitted change, CLAUDE.md forbids git tests in real projects, and project registration lives in `~/.ai-planner`, which is off limits. |
| M2 | Redirected `HOME` to `/tmp/trial/home` for every helper call | Keeps `~/.ai-planner` untouched (CLAUDE.md). |
| M3 | Made the "agent" edits and commits with scripts, not launched agents | No agent launches: they cost budget, and launching goes through `run.sh` and the LaunchAgent, which this task forbids. |
| M4 | Created each task worktree with `git worktree add` and wrote each `.meta` file by hand | Normally `launch` does this. Without it the helper has no task metadata. Side effect: `setup_ms` is 0 for every task (D6). |
| M5 | Ran `run <chunk> <task>` for each task myself | Normally the tick or launch runs checks. |
| M6 | Started `merge-batch` from the CLI with `resolve: false` | The page starts it with `resolve` on, which sends conflicts to Claude. Turning it off meant no Claude calls and no cost, and made the conflict fail cleanly. |
| M7 | Restored t3's `<title>` and removed `broken.txt` by hand, then `retry` | Cause: the deliberate red check. The fix nets to no change, so the next run reported "empty". |
| M8 | Rebased t5 onto `main` and hand-wrote the merged `<h1>` | Cause: the deliberate conflict. The helper leaves a conflicted branch untouched and its message offers no resolve step (D5). |
| M9 | Re-ran `run c2 t5` after the rebase | Cause: the branch changed, so its checks had to run again. |

Nothing was fixed in helper or page code during the trial. Every problem found is recorded below and was not hand-patched.

## Defect list (prioritised)

Severity: **High** blocks the sprint's go/no-go. **Medium** gives wrong or misleading information. **Low** is rough, but the operator is not misled.

| ID | Severity | Defect | Evidence | Where |
|---|---|---|---|---|
| D1 | **High** | Conflicts against approved work are not logged as merge failures. `merge-log` says none happened. | t5 gave `conflict`, but `failures.log` holds only t3's red entry and `merge-log` printed "No merge-train failures logged." | `helper/runway.py` ~1096 (`sync_integration` returns `conflict` without `log_failure`) |
| D2 | **High** | `merge-log` reads `merge-train.log`, which nothing in this trial wrote, while red checks go to `failures.log`. The two logs disagree. | `merge-log` empty after a red check; `failures` shows it. `merge-train.log` was never created. `merge_log_path()` is ~1969 and `log_failure` is ~1980. | `helper/runway.py` 1969–1996 |
| D3 | **High** | The ledger cannot prove time saved. It counts helper machine time only (0.01 min for 5 tasks). No agent, review, setup or manual minutes. | `ledgers/trial1.json` totals. `review_min`/`client_min` null. | `write_ledger`, `ledger_totals` (~1423–1450) |
| D4 | Medium | Retry count is unclear. `t5` shows `retries: 1`, but I only re-ran `run`, not `retry`. | `ledgers/trial2.json` row for t5. | `ledger` row builder (~1388–1420); not verified in code |
| D5 | Medium | The approved-work conflict message gives no next step. It omits the worktree path, the branch name and the command to run. | t5 msg "Conflicts with approved work in index.html." Compare the branch-sync message, which says "resolve them in the task, then merge again." | `helper/runway.py` ~1096 |
| D6 | Medium | `setup_ms` is 0 when the worktree already exists, so worktree creation is never timed. Normally it is created at launch, which this trial bypassed. | Every row shows `setup_ms: 0`. | Ledger timing (~1388+) |
| D7 | Low | A fix that cancels the change is reported as `empty` and says "discard it and re-run". That is correct, but the wording reads like a failure. | `trial2` t3 result. | merge result messages |
| D8 | Low | `merge-batch` does nothing to a red task beyond skipping it. The worktree is kept, but nothing says which branch to fix or where the check log is (the `failures` output has it). | `trial1` t3 msg. | merge result messages |

### Not covered, and why

- **Retry to merge (red → fixed → merged).** Not shown. My fix netted to no change (M7). A proper fix would add a file.
- **Claude conflict resolution (`resolve: true`).** Off by design, so it costs nothing. Not exercised, so its ledger fields (`conflicts_resolved`, `tokens`, `cost`) are still untested.
- **Real site repo and real agents.** Not run. Austin's go/no-go needs this.
- **`release.sh` and the installed helper.** Not run, as CLAUDE.md requires.

## Follow-ups for Austin

1. **Decision (not a defect):** pick a real site repo and a branch for the real trial. It has to be committed or stashed first, because it has an uncommitted SKILL.md change.
2. Fix D1 and D2 before the next real build. They decide whether a failed or conflicting task is visible at all.
3. Decide how D3 gets minutes. Options: (a) a manual-minutes field the page fills in; (b) wall-clock from launch to done, from `runs/` timestamps; (c) both.
4. Re-run this trial with real launched agents once D1 and D2 are fixed.

## How this was checked

- Pass 1 and pass 2 ran with the helper in this worktree, `HOME` redirected to `/tmp/trial/home`. All outputs above are copied from the run.
- No file under `tests/` was edited. The helper and page are unchanged, so the contract tests cover the same behaviour as before.

## Fix status (sprint task: fix top defects)

Helper VERSION 27 and the page's `HELPER_V` 27 (outcome messages changed). Regression tests: `tests/test_trial_regressions.py`.

| ID | Sev | Status | What changed, or why deferred |
|---|---|---|---|
| D1 | High | **Fixed** | A conflict with approved work (with or without the resolver) is written to the merge-train log as a `merge` failure, with the conflicting paths. Tests: `test_d1_*`. |
| D2 | High | **Fixed** | A red or timed-out check (including a bad `.runway.json`) is also written to the merge-train log as a `test` failure, so `merge-log` and `failures` agree. Tests: `test_d2_*`. |
| D3 | High | **Deferred: needs Austin's decision.** | The ledger still counts helper machine time only. Follow-up 3 asks Austin to choose how minutes are measured: (a) a manual-minutes field the page fills in, (b) wall-clock from launch to done, or (c) both. Either adds to the ledger schema and the page, so it waits for that choice. Until then the ledger cannot show a time saving, so the go/no-go still lacks its numbers. |
| D4 | Medium | **Fixed** | `retries` now counts re-runs after a red or timed-out result (recorded in the task's timing record). A manual re-run after a green result is not a retry. Tests: `test_d4_*`. The two `test_ledger.py` fixtures that encoded the old `test_runs - 1` rule now pass `retries` explicitly; their expected values did not change. |
| D5 | Medium | **Fixed** | The conflict message names the task branch, its kept worktree, and the command to run (`git -C <worktree> merge <integration branch>`), then "merge again". The resolver-failed message gets the same next step. Test: `test_d5_*`. |
| D6 | Medium | **Deferred: launch-script change.** | Worktrees are created by the generated `run.sh` at launch (the `git worktree add` line in `src/runway.src.html`), not by the helper, so no helper timer sees that time. Timing it means changing the launch script, which the behaviour contract makes Austin's call. Until then `setup_ms` is 0 for launched tasks and `git_merge_min` is understated. |
| D7 | Low | Known issue, not fixed | A fix that cancels the change reports `empty` ("discard it and re-run"). Correct, but the wording reads like a failure. |
| D8 | Low | Known issue, not fixed | A red task is skipped by `merge-batch` with no branch or check-log path in the message. The log path is in `failures`. |

Still open from "Not covered": a real retry-to-merge run (red, fixed, merged), Claude conflict resolution (`resolve: true`), a real site repo with launched agents, and `release.sh`. None of these was run in this pass.

## Addendum: a defect found via the autopilot dry run (2026-10-07)

The merge gate's red/blocked message is shared by `merge-batch` and `autopilot`, but only one of them actually keeps the worktree the message promises.

| ID | Sev | Defect | Evidence | Where |
|---|---|---|---|---|
| D9 | High | Known issue, not fixed | A blocked (red-check) task's message says "The worktree is kept," but `autopilot`'s end-of-run cleanup removes every non-running task's worktree, blocked included, whenever git allows it — the common case, since the agent has already committed by the time its checks run. The message is wrong exactly when a check fails, the most common blocked path. `merge-batch` does not run this cleanup, so for it the message is accurate; for `autopilot` it usually is not. | Full write-up and evidence in [the autopilot dry run](2026-10-07-autopilot-dry-run.md) and the [autopilot defect list](2026-10-07-autopilot-defects.md), finding A2: a deliberately broken-build task was blocked with "...the worktree is kept," but its worktree directory did not exist immediately after the run (`state["kept"] == {}`). | `helper/runway.py` line 1235 (the shared message) vs. the `finally` block's `_ap_clean(repo, keys)` call in `autopilot()` (~3021), which includes every `status != "running"` task, blocked included |

Not fixed in the autopilot dry-run task either: recorded here for the same reason D7 and D8 were left open, so the fix (when it happens) can choose between correcting the message for autopilot or leaving blocked worktrees in place the way `merge-batch` does.
