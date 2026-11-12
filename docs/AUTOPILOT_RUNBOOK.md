# Autopilot Runbook

Run a token-budgeted roadmap unattended from start to finish: generate a project brief, approve a roadmap, set a dollar cap, press go, and let the autopilot loop launch agents and merge chunks while staying under budget.

## Prerequisites

1. Your project repo has a `.runway.json` file with checks configured (see [MERGE_TRAIN_RUNBOOK.md](MERGE_TRAIN_RUNBOOK.md#configuration) for the schema).
2. Runway's helper is installed: `~/.ai-planner/runway.py` (installed from the Claude artifact; ask Austin).
3. You have Runway's latest code checked out locally (branch `runway/main` or the task branch).

## Workflow: from brief to run

### 1. Write the brief

A brief tells the autopilot what to build, with constraints and a token estimate for the whole project. Briefs live in your project repo or in `~/.ai-planner/briefs/`.

**Brief structure** (passed to `generate_roadmap` as a dict with these keys):

```json
{
  "goal": "What the project achieves when done (1–2 sentences)",
  "repo": "Path to the project repo (e.g., ~/Projects/site or ~/Projects/sample-site)",
  "constraints": "Brand voice, technical stack, design system rules, or other fixed boundaries",
  "planned": ["t-foo: Do foo", "t-bar: Do bar"],
  "estTokens": 500000
}
```

- **`goal`**: A clear outcome. Example: "Build the public pages (home, services, gallery, about, contact) plus `/review`, the crew's internal estimate-request screen, applying app-ui to the internal screen only."
- **`constraints`**: Voice, design system rules, technical stack constraints. Example: "App-ui applies to internal screens only, not marketing pages (per app-ui's own SKILL.md). Use the existing Nav, Footer and EstimateForm components."
- **`planned`**: A rough outline of tasks. Autopilot will generate a full roadmap from this sketch. Example: `["t-public-home: Build the home page", "t-public-services: Build services page", "t-review-shell: Build /review with app-ui shell"]`.
- **`estTokens`**: A rough estimate of the entire project's token budget. Autopilot uses this to calibrate per-task estimates. Example: 1,500,000 for a full sample-site build.

### 2. Generate the roadmap

From your project directory, call the roadmap generator. It will read your brief and produce a full roadmap (one sprint, task breakdown, dependencies, estimated tokens per task).

**CLI:**

```bash
runway.py roadmap-generate ~/Projects/site
```

Or from Python, in a local script:

```python
from runway import generate_roadmap, _cmd_roadmap_approve

brief = {
    "goal": "Build the public pages and /review screen",
    "repo": "~/Projects/sample-site",
    "constraints": "App-ui for /review only; use existing components",
    "planned": ["t-public-home: Build home page", "t-review-shell: Build /review shell"],
    "estTokens": 1500000
}

roadmap = generate_roadmap(brief)
print(roadmap)
```

The roadmap is a dict with a list of tasks, each with:
- `key`: Task ID (e.g., `"t-review-shell"`)
- `detail`: One-sentence description
- `acceptance`: Acceptance criteria
- `estTokens`: Estimated tokens for this task
- `waitFor`: List of task keys this task depends on (empty list if no dependencies)

**Output:** The roadmap is written to `~/.ai-planner/roadmaps/` and printed. Check it for sense:
- Do the tasks build toward the goal?
- Are estimates reasonable?
- Are dependencies correct?

### 3. Review and edit the roadmap

Read the generated roadmap and edit if needed. The roadmap is a JSON file in `~/.ai-planner/roadmaps/`; you can edit it by hand or ask an agent to refine it in a separate task.

**Editing by hand:** Each task is a dict. You can:
- Change `detail` or `acceptance` to clarify the scope
- Change `estTokens` if you think the estimate is way off
- Add or remove items from `waitFor` to change dependencies
- Reorder tasks by moving them in the list

**Editing in a Claude task:** Ask an agent to review the roadmap, read feedback, and update it. The agent is a real Claude run (costs tokens), not part of the autopilot loop.

### 4. Approve the roadmap

Once the roadmap looks right, approve it. This locks it in for the autopilot run.

**CLI:**

```bash
runway.py roadmap-approve ~/Projects/site
```

Or in Python:

```python
from runway import _cmd_roadmap_approve
_cmd_roadmap_approve(repo_path)
```

**What it does:**
- Marks the roadmap as approved in `~/.ai-planner/status.json`
- Reads through the roadmap one more time to check for plan-rule errors (circular dependencies, missing referenced tasks, etc.)
- Prints any errors and refuses to approve if there are problems

**Approval is idempotent:** You can approve the same roadmap multiple times. If you edited it, generate a new one and approve that.

### 5. Set the dollar cap

The dollar cap is per-run and is set when you launch the autopilot. It is a hard constraint on the autopilot's launch gate: a task that would cause total spend to exceed the cap is not launched (unless autopilot is already past cap due to an earlier task running over its estimate). See "Known limits" for how the cap works.

**The cap is:** `cap = (estTokens * hourlyRate) / 1000` for the run. Example:
- Roadmap estimated at 500,000 tokens
- `claude-sonnet-5` at ~$3 per 1M tokens = $0.003 per 1000 tokens
- Cap = (500,000 * 0.003) = $1.50

Set the cap when you press go (see next section).

### 6. Launch the autopilot

Press go to start the unattended run. Autopilot will:
1. Read the approved roadmap
2. Launch tasks in order (respecting dependencies)
3. Run checks on each task's code changes
4. Merge or block tasks based on check results
5. Write a run report when done

**CLI:**

```bash
runway.py autopilot ~/Projects/site --cap 1.50
```

**Options:**

- `--cap DOLLARS`: The dollar cap for this run (required)
- `--parallel N`: How many tasks to run in parallel (default 3; each takes its own worktree)
- `--poll N`: Seconds between polling the merge-train log for new work (default 5; 0 for instant)

**What happens:**
- Autopilot launches in the background (does not block your terminal)
- The roadmap's approved status is marked as "running"
- A run folder is created in `~/.ai-planner/runs/<repo-folder-name>/`
- Tasks launch in order of the roadmap, skipping blocked tasks' dependents

**You do not need to stay at the terminal.** Autopilot runs unattended. Check the status with `runway.py status ~/Projects/site` or read the live run report (see "Monitor a running autopilot" below).

### 7. Monitor a running autopilot

While autopilot is running, you can:

**Check the live status:**

```bash
runway.py status ~/Projects/site
```

Output: roadmap key, run ID, current spend, cap, stop reason if done.

**Read the run report (updated live):**

```bash
cat ~/.ai-planner/runs/<repo>/report.md
cat ~/.ai-planner/runs/<repo>/report.json  # JSON version
```

The report shows:
- Attempted, merged, blocked task counts
- Per-task status, estimated vs. actual tokens, cost
- Total spend vs. cap
- Stop reason

**Watch task launches and merges:**

```bash
tail -f ~/.ai-planner/status.log
```

Each line is one task launch, merge, or block. Timestamps are UTC.

### 8. Read the final run report

When autopilot finishes, read the full run report. It is written to:
- `~/.ai-planner/runs/<repo>/report.md` (markdown, human-readable)
- `~/.ai-planner/runs/<repo>/report.json` (JSON, machine-parseable)

**The report includes:**

1. **Summary:** Attempted, merged, blocked, in-review, left-to-do counts
2. **Per-task breakdown:** Task name, outcome, estimated tokens, actual tokens, cost
3. **Reconciliation:** Total spend vs. cap, any unattributed cost (should be $0)
4. **Needs a person:** Any issues (e.g., "spend went over cap", blocked tasks)
5. **Calibration drift:** How actual token use compared to estimates (by task kind and model)

**Stop reason:**
- `roadmap`: All tasks from the roadmap merged or blocked; queue is exhausted
- `cap`: Spend reached the cap; no more tasks can launch
- `failures`: A task or the merge train itself failed catastrophically
- `signal`: Autopilot was stopped (you ran `runway.py autopilot-stop`)
- `interrupted`: Autopilot was killed (timeout, power loss, etc.)
- `error`: Unexpected error

## Stop, resume, and recover

### Stop a running autopilot

To stop autopilot gracefully (after current launches finish):

```bash
runway.py autopilot-stop ~/Projects/site
```

The run report will show stop reason `signal`. All running tasks continue to merge or block; no new tasks launch.

### Resume an autopilot that stopped

If autopilot stopped early (signal, interrupt, or cap), you can resume it. The run report will show which tasks are done and which are left.

**Resume a stopped run:**

```bash
runway.py autopilot ~/Projects/site --cap 1.50
```

(Same command as the initial launch.)

Autopilot will:
- Detect the existing run folder
- Skip tasks that already merged
- Continue with the rest of the roadmap
- Use the same cap (or override with `--cap` if you raise the cap)

### Recover a blocked task

If a task was blocked (red checks), its worktree is kept in `.claude/worktrees/<task>/` so you can inspect and fix it.

**Steps:**

1. **Read the failure reason:**
   ```bash
   runway.py failures -n 1
   cat ~/.ai-planner/runs/<repo>/<task>.checks.log
   ```

2. **Inspect the worktree:**
   ```bash
   cd ./.claude/worktrees/<task>
   git status
   git log --oneline -3
   ```

3. **Fix the problem:**
   - Edit files, run tests by hand, commit changes

4. **Retry the checks:**
   ```bash
   cd /path/to/repo  # back to repo root
   runway.py retry <chunk> <task>
   ```

5. **If green, autopilot picks it up on the next poll.** If red, fix again and retry.

### Recover a killed or interrupted autopilot

If autopilot was killed (timeout, power loss, etc.), its run folder still exists. Resume it:

```bash
runway.py autopilot ~/Projects/site --cap 1.50
```

Same command as normal resume. Autopilot will:
- Detect the existing run (unfinished)
- Check which tasks merged and which are still running
- Wait briefly for any in-flight tasks to finish (or time out after a few seconds)
- Continue or finish, as needed

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| **Roadmap generation fails with "circular dependency" or similar** | Plan-rule error: a task depends on a task that doesn't exist, or there's a cycle | Edit the roadmap: check `waitFor` lists for non-existent keys or cycles. Approve again. |
| **Autopilot launches but tasks don't merge** | Checks are failing. Autopilot is blocked on the first red task. | Run `runway.py failures` to see the check failure. Fix the task's code and retry with `runway.py retry`. |
| **Spend goes over the cap** | A task's actual token use was well above its estimate. Calibration drift. | This is not a bug; it is a sign your estimates are off. See "Known limits" below. Review estimates for future runs. The cap does not stop the run mid-roadmap; it only prevents new launches once spend is already over. |
| **"The worktree is kept" message but the worktree is missing** | Autopilot cleaned it up despite the message. A fixed issue (A2 in defect list). | Run the task again: `runway.py run <chunk> <task>`. Autopilot will recreate the worktree from the branch. |
| **Autopilot seems stuck (no new task launches for a while)** | Waiting on a running task or blocked task. Or polling is slow. | Check `runway.py status` for the current task. If it's been running > 1 hour, inspect its worktree and kill it if hung. Resume autopilot. |
| **Run report shows "Needs a person: Calibration drift: X moved +N%" ** | Your actual token use on a task kind (e.g., "feature") is drifting from the estimate. Normal after a few runs; not an error. | No immediate action needed. Review the per-task rows in the report to spot patterns. Adjust `estTokens` for future roadmaps. |

## Known limits

1. **The dollar cap is a launch gate, not a hard ceiling.** Autopilot checks *projected* cost before launching (based on calibration history), not actual cost. A task can run well over estimate and push total spend past the cap. Once the cap is exceeded, no new tasks launch, but the run continues to completion. See the run report's "Needs a person" section for cap overruns.

2. **Calibration can drift.** Token estimates are based on your team's recent runs (geometric mean of actual ÷ estimate multiplier). New task kinds or unusual scope can fall outside the distribution. After each run, review the calibration-drift table in the report.

3. **Wall-clock time is not measured.** The run report shows task token counts and cost, not real time. A $1.50 run might finish in 5 minutes or 5 hours depending on queue depth (each task waits for its agent launch and edge-case slowdowns).

4. **No real-time progress streaming.** Autopilot polls the merge-train log every few seconds (default 5). There is a small lag between a merge finishing and autopilot picking it up and launching the next task.

5. **Task timeouts are set per check.** Autopilot does not timeout a task itself; it relies on the merge train's check timeouts. A task whose checks run forever will block the queue until the check times out (default 600 seconds, configurable per check in `.runway.json`).

## Full example: a sample site build

This is the 2026-10-08 run on a sample site build (scripted agent, seeded throwaway repo; see GO_NO_GO.md). Roadmap generated from the site brief, approved, run with a $1.50 cap, 7 tasks merged unattended.

**Brief:**

```json
{
  "goal": "Build the public pages (home, services, gallery, about, contact) plus /review, the crew's internal estimate-request screen, and apply the app-ui skill to /review only",
  "repo": "~/Projects/sample-site",
  "constraints": "App-ui applies to internal screens only, not marketing pages (per app-ui's own SKILL.md). Use the existing Nav, Footer and EstimateForm components. Brand voice is friendly and professional.",
  "planned": [
    "t-review-shell: Give /review app-ui's shell and header",
    "t-review-status: Use app-ui's status-dot language on /review",
    "t-review-states: Add empty/loading/error states to /review",
    "t-review-tokens: Pull /review's colours from the app-ui token set",
    "t-review-palette: Add a ⌘K palette for jumping between leads",
    "t-public-hero: Tidy the homepage hero copy to the brief's voice",
    "t-public-contact-state: Add a confirmation state to the estimate form"
  ],
  "estTokens": 1500000
}
```

**Run:**

```bash
runway.py roadmap-generate ~/Projects/sample-site
runway.py roadmap-approve ~/Projects/sample-site
runway.py autopilot ~/Projects/sample-site --cap 1.50
```

**Result:** All 7 tasks merged, spend $1.065 of $1.50 cap (71%), zero manual git commands. See docs/reports/2026-10-08-sample-site-autopilot-run.md for the full report.
