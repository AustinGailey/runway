# Autopilot defect list

From the dry run in [2026-10-07-autopilot-dry-run.md](2026-10-07-autopilot-dry-run.md). Severity: **High** blocks the
PI 4 go/no-go. **Medium** gives wrong or misleading information. **Low** is rough but not misleading. Nothing here
was fixed in the dry-run task itself: that was a record for Austin. The "Fix status" section below records what the
follow-up task (fix top defects) did with each item.

| ID | Severity | Defect | Evidence | Where |
|---|---|---|---|---|
| A2 | **High** | A blocked (red-check) task's message says "The worktree is kept," but autopilot's end-of-run cleanup removes every non-running task's worktree, blocked included, whenever git allows it — which is the common case, since the agent has already committed by the time its checks run. The message is wrong exactly when a check fails, the most common blocked path. | Pass 2: `t-break-build` blocked with "...The worktree is kept," but `os.path.isdir(wtp)` was `False` immediately after the run; `state["kept"] == {}`. Also appended as D9 to [the 2026-10-04 trial's defect list](2026-10-04-merge-train-trial.md). | `helper/runway.py` line 1235 (the shared message) vs. the `finally` block's `_ap_clean(repo, keys)` call in `autopilot()` (~3021), which includes `status != "running"` tasks |
| A1 | **Medium** | A dollar cap does not guarantee the run stops before going over it. The cap only vets a task's *projected* cost before it starts; a task whose real use runs well above its own `estTokens` can push total spend over the cap, and the run still ends with stop reason `roadmap` (queue finished), not `cap`. The overage is visible only as a "Needs a person" line. | Pass 1: cap $0.50, final spend $0.555 (111%), `stop.reason == "roadmap"`. | `helper/runway.py` `_ap_fits` (~2819) projects from calibration history, not from a hard ceiling; `ap_report`'s cap-overrun note (~3181) |
| A3 | Low | Recovery still works despite A2: `runway.py run` silently recreates a missing worktree from its still-existing branch before running checks, so nothing needed for this dry run's blocked task was actually lost. But a person reading the blocked message has no way to know their worktree is already gone until they try to use it. | `runway.py run ap-t-break-build t-break-build` after the run still returned a red result from a path that did not exist a moment before. | `helper/runway.py` `_plan` (~4205) |

## Fix status (sprint task: fix top defects from the dry run)

Helper VERSION 40 and the page's `HELPER_V` 40 (the autopilot run report's wording for a blocked task changed).
Regression tests: `tests/test_autopilot_loop.py` (`test_a_blocked_check_failure_reports_that_its_worktree_was_removed_not_kept`,
`test_a_blocked_check_failure_whose_worktree_git_refuses_to_remove_still_says_kept`).

| ID | Sev | Status | What changed, or why deferred |
|---|---|---|---|
| A2 | High | **Fixed** | The shared `_gate` message (used by both `merge-batch`, which never cleans up, and `autopilot`, which does) still says "The worktree is kept" everywhere it is first produced — that stays true for `merge-batch`. But the autopilot run report (`ap_report`, both `report.md` and `report.json`, and the "Needs a person" line built from the same row) now corrects a blocked task's reason after the run's own cleanup has actually run: if the task's worktree was removed (the common case), the reason says so and tells the operator to run the task again to recreate it from its branch; if git genuinely refused (the task is in the run's `kept` map, e.g. uncommitted edits), the original "is kept" wording stands, because it is still true. New helper: `_ap_blocked_reason` (~3151). This changes only report wording for autopilot's own blocked-task rows; `merge-batch`'s message and autopilot's cleanup behaviour (what it actually removes) are both unchanged, per the Behaviour contract. |
| A1 | Medium | **Deferred: needs Austin's decision.** | The dry run's own follow-up #1 already poses this as a product choice, not a bug: either make the cap a harder ceiling (pause further launches once spend crosses it, even mid-roadmap), or keep today's "projected cost only" gate (`_ap_fits`, calibration-based, a geometric-mean multiplier rather than a worst-case one) plus the existing "Spend $X went over the $Y cap" report note. Both options change `autopilot`'s default launch behaviour (whether a task that fits today's gate is allowed to start), which the Behaviour contract reserves for Austin to decide, not an agent. Nothing changed here; the existing cap-overrun note in `ap_report` (~3187, unchanged) is the only mitigation today. |
| A3 | Low | **Fixed, as a side effect of A2.** | A3's complaint was exactly that the blocked message gives no way to know the worktree is already gone. A2's corrected wording ("Its worktree was removed; run the task again to recreate it from its branch and retry the checks.") says exactly that. No separate change was needed. |

## Manual interventions

See the dry run's own table; summarized here for completeness: (M1) narrowed `.runway.json` checks to `build` only, because the real suite has one pre-existing, unrelated failure on `main` (`tests/test_harness.py::test_the_card_updates_live_from_launch_to_review`) that would block every task regardless of content; (M2) roadmap generated with a canned `ask`, not a real Claude call; (M3) each task's agent was a scripted launcher, not real `claude`; (M4) `HOME` redirected to a sandbox for every call. None changed helper or page code.

## Follow-ups for Austin

1. **Still open.** Decide whether the cap should become a harder ceiling (e.g. pausing further launches once spend crosses it even mid-roadmap-exhaustion) or whether today's "projected cost only" behaviour plus a clear report note (A1) is good enough — this is a product decision, not a bug fix. See A1's fix-status row above.
2. **Done.** A2 is fixed: the autopilot run report no longer claims a blocked task's worktree is kept once the run's own cleanup has actually removed it. See A2's fix-status row above.
3. The pre-existing `tests/test_harness.py` failure on `main` (noted as M1, not an autopilot defect) is still open and worth a look separately: it fails intermittently (seen both passing and failing across back-to-back runs of the unchanged test during the "fix top defects" task), on a clean clone of `main` itself, outside any task's changes. Looks like UI-event timing flakiness in the harness drive, not a logic bug; still not investigated.

   **Resolved 2026-10-07 to 2026-10-09 (helper v40, v44, v45).** Root cause: four separate problems, two of them real
   bugs, not one flaky test.
   - **live.json dropped finished chunks (real bug).** Its final write after a chunk ended removed that chunk, so
     whether a card reached Review depended on the page reading live.json in a short window; otherwise it waited for
     the once-a-minute tick. Fixed: finished and stopped chunks stay in live.json for 10 minutes (`LIVE_KEEP`),
     tested in `tests/test_live.py`.
   - **The test sampled instead of waiting.** It read the card every 100 ms and checked the log right after the task's
     status flipped, a render before the card did; and the fake agent finished on a timer. Fixed: the fake agent waits
     on a gate file the test releases (`HARNESS_AGENT_GATE`), every change to the card is recorded with a
     MutationObserver, the test waits for the card itself to read Review, and each wait fails with a message naming
     what it waited for.
   - **Parallel suites starved Chrome.** Agents' checks run the full suite at the same time; several headless Chromes
     plus builds made timing-based steps miss. Fixed: Chrome-driven tests take a machine-wide lock (`chrome_lock`).
   - **Checks tested a stale page.** `.runway.json` ran the tests before `build.py`, so the harness served whatever page
     was last built in that worktree. Fixed: checks build first, and the harness tests build the page themselves.
   The setup-field test that later failed the same way was a page bug of its own (an overlay opened before the helper
   answered never redrew; fixed in v45). The merge gate now reruns the combined checks once before blaming a task,
   so an intermittent failure is logged as flaky instead of sending sound work back to its agent.

### Not on this list

The "fix top defects" task's brief named two more expected areas — task selection when a dependency is blocked, and
unclear stop reasons — that turned out not to correspond to any item the dry run actually logged. `ready_tasks`
already excludes a blocked task's dependents correctly (a blocked task holds back only its own dependents, per its
own docstring), and the stop-reason vocabulary (`roadmap`/`cap`/`failures`/`signal`/`interrupted`/`error`, labelled
in `AP_STOP_LABEL`/`AP_STOP_TEXT`) was not flagged as unclear by the dry run beyond A1's cap-vs-roadmap point, which
is covered above. Nothing was changed for either area, per this task's instruction to keep changes scoped to defects
actually on the list.
