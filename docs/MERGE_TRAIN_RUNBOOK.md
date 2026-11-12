# Merge Train Runbook

Configure, run, inspect, and recover the Runway merge train: a system that launches agent chunks in worktrees, runs checks on each task, merges successes, logs failures, and lands to main—all without manual git work.

## Configuration

Each project declares its tests and merge settings in a `.runway.json` file at the repo root. Commit it with the project.

### `.runway.json` reference

```json
{
  "type": "site",
  "baseBranch": "main",
  "mergeStrategy": "merge",
  "checks": [
    {"name": "tests", "cmd": "./test.sh"},
    {"name": "build", "cmd": "python3 build.py", "timeout": 300}
  ]
}
```

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `type` | `"site"` or `"python"` | auto-detect | Picks defaults for test and merge settings. Detected from repo structure. |
| `baseBranch` | branch name | `null` (use current) | The approved work branch. Null uses the current checkout branch. |
| `mergeStrategy` | `"squash"` or `"merge"` | `"squash"` for site, `"merge"` for python | Land each task as one commit or keep its commits. |
| `checks` | list of objects | required | Test commands, run in order from repo root. A missing `checks` key means the merge gate refuses all tasks. |

**Checks object fields:**

- `name`: label shown in status (required)
- `cmd`: command to run with `bash -c` (required)
- `timeout`: seconds before a check times out (optional, default 600)

Top-level `timeout` key sets the default for all checks not specifying one.

**Validation:** unknown keys fail loudly with a suggestion (e.g., `"tests"` suggests `"test"`). Discard and status still work with a bad file, so a broken config never traps a worktree.

### Examples

**Site with basic tests (sample site):**
```json
{
  "type": "site",
  "baseBranch": "main",
  "checks": [
    {"name": "tests", "cmd": "./test.sh"}
  ]
}
```

**Python project with pytest and build:**
```json
{
  "type": "python",
  "checks": [
    {"name": "tests", "cmd": "python3 -m pytest -q"},
    {"name": "build", "cmd": "python3 build.py", "timeout": 180}
  ]
}
```

**No checks (disables merge train):**
```json
{
  "checks": []
}
```

## Daily use: CLI commands

The merge train helper runs on your Mac. All commands shown are copy-pasteable.

### View task worktrees

List active and stale task worktrees:

```bash
runway.py list
runway.py list --repo ~/Projects/site  # another project
```

Output: `active` or `stale` status, task id, worktree path.

### Run a task's checks

Manually run checks in a task's worktree (the same gate a merge uses):

```bash
runway.py run chunk-1 task-a              # run the checks
runway.py run chunk-1 task-a --dry-run    # print commands, run nothing
```

Output: verdict (`green`, `red`, or `none`), reason if red. Exit code: 0 green, 1 red, 3 no checks configured.

### View check failures

Show the most recent red or timed-out checks:

```bash
runway.py failures                        # last 10 failures
runway.py failures -n 25 --chunk chunk-1  # last 25 for one chunk
```

Output: timestamps, phase (create, test, merge), exit code, task branch, last 8 lines of stderr.

### Retry a failed check

After fixing the problem in the task's worktree, retry the checks:

```bash
runway.py retry chunk-1 task-a            # run checks again
runway.py retry chunk-1 task-a --dry-run  # show what would run
```

Requires a recorded failure. Exit code: 0 green, 1 red, 2 no prior failure.

### Clean up stale worktrees

Drop git's records of deleted task worktrees:

```bash
runway.py prune --dry-run                 # list what would be dropped
runway.py prune --repo ~/Projects/site    # drop them
```

## Reading the merge-train log

The merge-train log at `~/.ai-planner/merge-train.log` records every check failure: red checks, timeouts, or conflicts. One JSON object per line, append-only.

### View the log

```bash
runway.py merge-log           # last 10 failures
runway.py merge-log 25        # last 25
```

Output: grouped by chunk, with timestamp, phase (create, test, merge), exit code, branch, and last 8 lines of stderr.

### Read the log directly

```bash
tail -100 ~/.ai-planner/merge-train.log | jq .  # last 100 entries, pretty-printed
grep '"chunk": "ch202610050112iqz"' ~/.ai-planner/merge-train.log  # all failures for one chunk
```

Each log entry is:

```json
{
  "ts": "2026-10-04T18:22:15Z",
  "chunk": "c1",
  "branch": "runway-task/t3",
  "phase": "test",
  "exit_code": 1,
  "stderr_tail": "[last 2000 chars of stderr]",
  "paths": ["index.html"]  // only for merge conflicts
}
```

- `phase`: `"create"` (worktree setup failed), `"test"` (check failed), `"merge"` (conflict with approved work)
- `paths`: present only for merge conflicts; lists the conflicting files

## Conflict recovery

When a task conflicts with approved work (already merged tasks or main), the merge train catches it and keeps the task's worktree.

### Identify conflicts

```bash
runway.py merge-log 10      # show recent failures; look for phase "merge"
```

Conflicts appear with `"phase": "merge"` and a `"paths"` list.

### Resolve a conflict

The merge train leaves the branch in a conflicted state and shows the worktree path. In that worktree:

1. Inspect the conflict:
   ```bash
   cd ./.claude/worktrees/<task>
   git status         # see conflicting files
   cat <file>         # view the conflict markers
   ```

2. Edit the conflicting file and resolve manually:
   ```bash
   # Edit <file> to resolve, removing conflict markers
   git add <file>
   ```

3. Complete the merge:
   ```bash
   git commit --no-edit
   ```

4. Run checks again to verify the resolution:
   ```bash
   cd /path/to/repo
   runway.py run <chunk> <task>
   ```

5. If green, the merge train will pick it up on the next pass. If red, fix and retry.

### Skip a conflicted task

If the conflict is not worth resolving, discard the task:

```bash
runway.py discard <chunk> <task>
```

This deletes its worktree and branch. The task does not merge.

## Check failures and retries

When a check fails (exits non-zero or times out):

1. **Identify the failure:**
   ```bash
   runway.py failures -n 5                    # recent failures
   runway.py merge-log 5                      # same, with log output
   ```

2. **Read the check log:**
   ```bash
   cat ~/.ai-planner/runs/<chunk>/<task>.checks.log
   ```

3. **Fix the problem in the task's worktree:**
   ```bash
   cd ./.claude/worktrees/<task>
   # Edit files, commit, etc.
   ```

4. **Retry the checks:**
   ```bash
   runway.py retry <chunk> <task>
   ```

5. **If green, the merge train picks it up.** If red, inspect the new log and fix again.

## Disabling the merge train

To disable automatic merges entirely, remove or empty the `checks` list in `.runway.json`:

```json
{
  "checks": []
}
```

With no checks configured, tasks cannot merge. The merge gate refuses all tasks with a clear message.

To re-enable, add checks back and commit.

## Merge ledger

After each merge batch, a ledger at `~/.ai-planner/ledgers/<run-id>.json` records:

- Per-task outcome (merged, red, conflict, empty, resolved)
- Machine timings: test, merge, setup (milliseconds)
- Retry and conflict counts
- Human touches counter

```bash
cat ~/.ai-planner/ledgers/mbmuull01d.json | jq .
```

See the trial report (docs/reports/2026-10-04-merge-train-trial.md) for a full example and known limitations of the ledger.

---

# Go/No-Go: Sprint 3 · Prove it on sample sites

**Status: Partial. The merge train system is working end-to-end. Time-saving measurement is deferred.**

## Baseline (T1): Last three site builds, 2026-09-26 to 2026-09-11

Source: a private baseline report (not published; site names replaced with Site A, B, C).

The baseline measured pipeline compute time only. No hands-on git, review, or client minutes were recorded for any build.

| Build | Machine time (measured) | Hands-on git/merge time | Status |
|-------|-------------------------|------------------------|--------|
| Site A (2026-09-26) | not recorded | not recorded | No timing data; verified 2026-09-27 |
| Site B (2026-09-11) | ~18 min (pipeline) | not recorded | 0 git events during build window |
| Site C (2026-09-11) | ~32 min (pipeline) | not recorded | 0 git events during build window; deployed in 54 min total |

**Baseline finding:** Per-site git/merge work does not appear in the two measured builds' records. Pipeline compute is 18–32 minutes. Elapsed time to deploy is longer (54 min to 9 h 26 min) due to idle gaps between steps.

**Target to beat:** 2–4 hours per site of hands-on git/merge/review work (not measured in baseline).

## Trial run (T9): Merge train end-to-end, 2026-10-04

Source: docs/reports/2026-10-04-merge-train-trial.md

The trial ran a five-task, two-chunk build on a scratch repo with a `.runway.json` declaring checks. All helper commands worked (run, retry, merge, merge-batch, land, merge-log). Conflicts and check failures were properly logged.

**Trial outcome:**

| Measure | Result | Status |
|---------|--------|--------|
| 5 tasks, 2 chunks, merged and landed to main | 3 merged, 1 failed checks (red), 1 conflicted | ✓ End-to-end works |
| Merge-train log (failures) | Red checks logged, conflicts logged with conflicting file paths | ✓ Logging works (fixed in VERSION 27) |
| Ledger: helper machine time | 1.5–2.5 min per batch | ✗ Ledger measurement limited |
| Ledger: git/merge minutes saved | 0.01–0.33 min recorded; review and client time null | **✗ Cannot measure yet** |

**Trial findings:**

1. ✓ **System works end-to-end:** Chunks launch, checks run in worktrees, merges succeed and land, failures and conflicts are caught and logged.
2. ✓ **Logging is fixed:** VERSION 27 now writes all failures (red checks and conflicts) to the merge-train log. The log is queryable and actionable.
3. ✗ **Time measurement deferred:** The ledger records helper machine time only (seconds). It cannot yet prove the hours saved, because:
   - `review_min` and `client_min` are null
   - Agent launch and review time are not in the helper's scope
   - Setup time is 0 (worktree creation is in the generated launch script, not the helper)

## Comparison: baseline vs trial

| Aspect | Baseline (T1) | Trial (T9) | Verdict |
|--------|---------------|-----------|---------|
| **Per-site git/merge minutes timed** | 0 (not recorded) | Not applicable (trial was scratch repo) | Data needed from real site build |
| **Merge train system ready** | N/A (did not exist) | ✓ End-to-end tested | Ready for real builds |
| **Merge-train log and failure visibility** | N/A | ✓ Conflicts and red checks logged | Go: ready to detect problems |
| **Time-saving proof** | — | Still needed | Blocker: hand-time the next real build |

## Go/No-Go decision

### Can we run on sample sites now?

**Go:** The merge train is ready for real builds. The system works end-to-end:
- Tasks launch in worktrees, checks run, merges succeed
- Conflicts and failures are caught and logged
- The helper and page are version-matched (VERSION 27)
- The `retry` and `merge-log` commands enable recovery

**Requirement:** Each real project must have a `.runway.json` with checks. See Configuration above.

### Will it save 2–4 hours per site?

**No-go (blocked):** The ledger cannot yet measure the hours saved. The baseline has no recorded hands-on time, so there is no baseline to beat. The trial ran on a scratch repo with scripted tasks, not real agents, so it measured helper machine time only.

**What's needed:**
1. Time the next real site build by hand, tagging each minute as git, review, or client work. Record start and stop times in the client folder.
2. Run that build with the merge train enabled.
3. Compare the hand-timed build with this baseline to see if the 2–4 hour target is met.

**Deferral:** D3 from the trial report—how the ledger measures time—requires Austin's decision (manual minutes field, wall-clock timing, or both). Until then the go/no-go cannot be finalized.

## Recommendation for Austin

1. **Enable merge train on the next sample site build** (when ready). Add `.runway.json` with checks to the repo. Use the Runway planner as usual; the merge train will run in the background.

2. **Time that build by hand** with a stopwatch or time-tracking. Tag each minute:
   - **Git/merge:** git commands, branch work, resolving conflicts
   - **Review:** reading agent output, verifying it's right
   - **Client:** back-and-forth with the client, photos, content decisions

3. **Record the times** in the client folder (e.g., `git-merge: 45 min`, `review: 22 min`). Commit them or add to `BUILD_NOTES.md`.

4. **Compare against this baseline:** The baseline has 0 recorded hours. If the merge train reduces git/merge minutes, the time saving will show in step 3.

5. **Decide on time measurement (D3):** If you want the ledger to auto-measure going forward, choose one:
   - (a) Add a manual-minutes field the page lets you fill in during/after the build
   - (b) Add wall-clock timing from launch to done, reading timestamps from `~/.ai-planner/runs/`
   - (c) Both

---

## Known limitations

- **Ledger time measures helper machine time only** (not agent, review, or manual time). See D3 in the trial report.
- **Worktree setup time is 0** (it runs in the generated launch script, not the helper). See D6 in the trial report.
- **No measurement for a real site build yet.** This trial proves the system works; the next build will prove the time savings.

---

## Follow-up tasks

1. Time the next real site build by hand (git, review, client minutes).
2. Austin decides how the ledger measures time (D3).
3. Update the ledger schema and page to record human minutes.
4. Re-run the trial with real agents, real conflict resolution, and a real site repo.
