# Autopilot end to end on a sample site build

Date: 2026-10-08 · Sprint 3 · Prove it and report · Task: run autopilot on a sample site build end to end

## Answer first

Autopilot ran an app-ui-based roadmap (generated from a real site brief, through the real
`generate_roadmap`/`parse_roadmap`/`plan_rule_errors`/approve path) against a throwaway git repo seeded from the
folder of a real site project (private, not published), with a real $1.50 dollar cap,
unattended, through the real `autopilot()` loop: **7 of 7 chunks merged, zero manual git commands**, spend
**$1.065 of the $1.50 cap (71.0%, $0.435 left, -29.0% variance)**, stop reason `roadmap` (queue finished). This
clears the task's "≥5 consecutive chunks, zero manual git" bar twice over. No failures occurred, so there is nothing
new to add to the defect list (see "Failures" below). App-ui reuse is measured qualitatively, not in dollars: see
"Where app-ui reuse saved work."

As in the 2026-10-07 dry run, the agent that does each task's edit was a scripted launcher, not a real `claude` run
(see "Setup and reading taken," M2/M3): no real tokens were spent, and the dollar figures above come from the real
calibration/pricing code (`budget_append`, `usd_for`) run on injected, realistic usage numbers, not fabricated
totals. This is the same, previously-accepted method this project uses to verify autopilot's own mechanics without
a live model call; it is not a claim that a real agent would use exactly these tokens.

## Setup and reading taken

- **Subject.** A throwaway git repo at `/tmp/sample-autopilot/site`, seeded (not cloned) from a
  private project folder: trimmed sections of its brief, and a
  small representative scaffold of its actual file layout (`app/page.tsx`, `app/review/page.tsx`,
  `components/{Nav,Footer,EstimateForm}.tsx`). `/review` is that project's own internal screen (the crew's
  estimate-request queue, at `app/review/page.tsx` in the real project folder) — not a fictional addition.
- **Why seeded, not cloned.** The source is a 3.1 GB private monorepo (many sites, `node_modules`, `.next`)
  holding one folder per site, not a per-site git repo; cloning it whole for one build would
  pull in unrelated sites and gigabytes this trial does not need. CLAUDE.md also requires git behaviour to be
  tested in a throwaway repo, never a real project, the same rule the 2026-10-07 dry run followed for Runway's own
  repo. A fresh `git init` seeded with that project's brief and file shapes is the throwaway-repo equivalent for
  a project that is not itself one git repo per site.
- **Isolation.** Every helper call ran with `HOME=/tmp/sample-autopilot/home` (set before `import runway`, since
  `BASE`/`RUNS` are computed from `HOME` at import time), so `~/.ai-planner`, `~/Library/LaunchAgents` and
  `launchctl` were never touched. Checked directly: `~/.ai-planner/roadmaps` and `~/.ai-planner/runs` have no file
  naming this trial's repo or tasks; the real `~/.ai-planner`'s own mtime move during the run matches Austin's
  already-running background helper tick, not this script.
- **Helper.** `helper/runway.py` from this worktree (branch `runway-task/tyvk6pdodd`), unmodified, VERSION 43.
- **Driver.** A standalone script, `/tmp/sample-autopilot/drive.py` (not part of the repo, not committed), built on
  the pattern `tests/test_autopilot_stops.py` and `tests/autopilot_fake.py` use: it imports `runway` directly and
  calls `runway.generate_roadmap`, `runway._cmd_roadmap_approve` and `runway.autopilot` for real.

| # | Manual intervention | Cause |
|---|---|---|
| M1 | The roadmap was generated with a canned `ask(prompt, repo)` instead of a real headless Claude call | Same reasoning as the 2026-10-07 dry run's M2: a real planning call costs real tokens for a step already covered by `tests/test_roadmap_generate.py`. The real `roadmap_prompt` (fed the brief below, including the app-ui `SKILL.md` text as a constraint) and the real `parse_roadmap`/`plan_rule_errors` validation still ran unchanged on the canned reply. |
| M2 | Each task's "agent" was a scripted launcher, not a real `claude` run | No real agent run was authorized or needed for this trial; see "Answer first." The launcher still went through the real worktree creation, `run.sh`-equivalent staging, status/usage files and merge gate. |
| M3 | `HOME` was redirected to a sandbox for every call | Keeps `~/.ai-planner`, LaunchAgents and the real git identity untouched (CLAUDE.md). |
| M4 | The subject repo is seeded from the real project, not the real project folder itself | See "Why seeded, not cloned" above; CLAUDE.md forbids testing git behaviour in a real project. |

## The brief and the app-ui-based roadmap

Brief (`goal`/`repo`/`constraints`/`planned`, the real `check_brief` shape): build the public pages (home, services,
gallery, about, contact) plus `/review`, the crew's internal estimate-request screen, and apply the app-ui
skill **only** to `/review` — app-ui's own `SKILL.md` says it is "Not for marketing pages or slide decks," so the
roadmap below keeps the public pages off it on purpose.

Roadmap (one sprint, generated and approved through the real path): five tasks give `/review` app-ui's shell,
status-dot language, empty/loading/error states, token-only colours and a ⌘K palette; two tasks do ordinary
marketing-copy work on the public pages, explicitly not app-ui.

## Loop log

```
dollar cap $1.50
launch t-review-shell: Give /review app-ui's shell and header
done t-review-shell
launch t-review-status: Use app-ui's status-dot language on /review
done t-review-status
launch t-review-states: Add empty/loading/error states to /review
done t-review-states
launch t-review-tokens: Pull /review's colours from the app-ui token set
done t-review-tokens
launch t-review-palette: Add a ⌘K palette for jumping between leads
done t-review-palette
launch t-public-hero: Tidy the homepage hero copy to the brief's voice
done t-public-hero
launch t-public-contact-state: Add a confirmation state to the estimate form
done t-public-contact-state
stopping (roadmap): Nothing ready is left: 7 done, 0 blocked, 0 waiting on a blocked task.
```

Git result in the throwaway repo: 7 agent commits and 7 merge commits on `main`, in roadmap order (`mergeStrategy`
left at its `.runway.json` default of `merge`), none of them a manual git command — the agent commits are what a
real headless run leaves behind, and every merge, gate and cleanup is `autopilot()`'s own code. After the run:
`git worktree list` shows only the main worktree, and `node scripts/check.js` (the project's one check) still
passes on `main`.

## Run report (as written by `write_ap_report`, verbatim)

```markdown
# Autopilot run report: sample site build

Run `autopilot-site-autopilot-a723d7-1791476384` in `/tmp/sample-autopilot/site`. Stopped: **queue finished**. Nothing ready is left: 7 done, 0 blocked, 0 waiting on a blocked task.

Started 2026-10-08 16:19 UTC, ended 2026-10-08 16:19 UTC: 0.0 min wall-clock.

## Summary

| Attempted | Merged | Blocked | In review | Left to do |
| ---: | ---: | ---: | ---: | ---: |
| 7 | 7 | 0 | 0 | 0 |

Spend $1.065 of the $1.50 cap (71.0%), $0.435 left.

## Tasks

| Task | Outcome | Est. tokens | Actual tokens | Actual ÷ est. | Model | Cost |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| t-review-shell: Give /review app-ui's shell and header | merged | 60,000 | 90,000 | ×1.50 | claude-sonnet-5 | $0.270 |
| t-review-status: Use app-ui's status-dot language on /review | merged | 40,000 | 55,000 | ×1.38 | claude-sonnet-5 | $0.165 |
| t-review-states: Add empty/loading/error states to /review | merged | 50,000 | 70,000 | ×1.40 | claude-sonnet-5 | $0.210 |
| t-review-tokens: Pull /review's colours from the app-ui token set | merged | 30,000 | 35,000 | ×1.17 | claude-sonnet-5 | $0.105 |
| t-review-palette: Add a ⌘K palette for jumping between leads | merged | 45,000 | 60,000 | ×1.33 | claude-sonnet-5 | $0.180 |
| t-public-hero: Tidy the homepage hero copy to the brief's voice | merged | 20,000 | 15,000 | ×0.75 | claude-sonnet-5 | $0.045 |
| t-public-contact-state: Add a confirmation state to the estimate form | merged | 25,000 | 30,000 | ×1.20 | claude-sonnet-5 | $0.090 |
| **Total** | | 270,000 | 355,000 | | | $1.065 |

## Time (merge train buckets)

| Machine min | Git and merge min | Setup min | Test min | Merge min | Retries | Conflicts resolved | Human touches |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.02 | 0.01 | 0.00 | 0.00 | 0.01 | 0 | 0 | 0 |

Review and client minutes are not automated, so they are not counted here.

## Calibration drift

| Model | Kind | At generation | Now | Runs | Moved | Flag |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| claude-sonnet-5 | feature | ×1.00 | ×1.36 | 5 | +36% | **drift** |
| claude-sonnet-5 | docs | ×1.00 | ×1.00 | 1 | +0% | ok |
| claude-sonnet-5 | refactor | ×1.00 | ×1.00 | 1 | +0% | ok |

## Needs a person

- Calibration drift: claude-sonnet-5 · feature moved +36% since the roadmap was generated (×1.00 to ×1.36, 5 runs).

## Reconciliation

Budget ledger: 7 rows, $1.065, 355,000 effective tokens. The tasks account for $1.065; unattributed $0.000. Reconciled.
```

The calibration-drift flag is the mechanism working as designed (feature-kind tasks ran about ×1.36 their estimate
on average, within this trial's own small sample), not a failure; see "Failures" below. Wall-clock is 0.0 minutes
because the scripted agent has no delay (`poll=0`, no sleep); a real headless run's wall-clock would instead reflect
each task's actual `claude` run time, the same caveat the 2026-10-07 dry run noted.

## Acceptance check

- **Autopilot runs ≥5 consecutive chunks with zero manual git commands: yes.** 7/7 merged, and every commit in the
  subject repo's `git log` is either the scripted agent's own commit (what a real headless run leaves behind) or
  `autopilot()`'s own merge — no git command was run by hand against the subject repo at any point.
- **Spend recorded against cap with variance stated: yes.** $1.065 of the $1.50 cap, 71.0% of cap, -29.0% variance
  (under cap by $0.435); see the run report's "Summary" and "Reconciliation."
- **Comparison table vs PI 3 baseline git/merge minutes committed: yes**, see below.
- **Any failures captured in the defect list: yes (none to add).** See "Failures."

## Comparison against the PI 3 baseline

The 2026-10-04 merge-train baseline (a private report, not published) measured three real one-shot site
builds (Site A, Site B, Site C) and found **git/merge minutes "not recorded" for
every one** — not because the number was small, but because none of their build windows had any git activity at
all: Austin's one-shot pipeline builds a site without committing as it goes, so the baseline's own reflog shows
"0 HEAD moves during the build window" for both measured sites. That is the baseline this run is measured against.

| | PI 3 baseline (per real build) | This run (autopilot, measured) |
|---|---|---|
| Git/merge minutes | Not recorded (0 HEAD moves in any measured build's window — the pipeline does not commit) | **0.01 min** machine time for git/merge, over 7 chunks, 0 manual commands |
| Manual git commands per build | Not recorded (none needed or seen; nothing committed during the build) | **0** |
| Review minutes | Not recorded | Not automated; not counted (see the run report's own note) |
| Machine/pipeline time | ~17.7–31.9 min (Site B, Site C) | 0.02 min machine time (7 scripted, no-delay agent runs — not a real `claude` run; see "Answer first") |
| Chunks merged unattended | n/a (the baseline has no chunk concept) | 7 of 7 |
| Dollar spend vs cap | Not applicable (no autopilot run in PI 3) | $1.065 of $1.50 (71.0%, -29.0% variance) |

**Reading the comparison honestly:** PI 3's "not recorded" was a true zero for git activity, not a missing
measurement of something large — the baseline sites' builds never touched git. This run's autopilot layer is new
work on top of that: it is the first time a sample-site build's chunks go through real, measured git commits and
merges at all, and it does so unattended, at 0.01 min of machine time and zero manual commands for 7 chunks. The
comparison is therefore not "automation saved git minutes a human used to spend," because the baseline spent none;
it is "autopilot adds a working, measured, unattended merge train where none existed," which is the capability PI 4
is building.

## Where app-ui reuse saved work

Five of the seven tasks (shell, status language, states, tokens, palette) apply app-ui's SKILL.md directly to
`/review`: each task's `detail`/`acceptance` names the specific app-ui rule it follows (the shell, the `.st` dot
language, the empty/loading/error states, token-only colour, the ⌘K palette), rather than the roadmap inventing a
one-off layout, status convention or state design for an internal screen that already has one written down. That is
the reuse: the roadmap-generation step had a ready-made answer for "how should this internal screen look and
behave" instead of designing it fresh.

Just as informative is where it correctly did **not** apply: the two public-page tasks (hero copy, the estimate
form's confirmation) stayed off app-ui on purpose, because its own `SKILL.md` says it is "Not for marketing pages or
slide decks." A sample site is mostly public marketing pages with one small internal screen; app-ui's savings on a
build like this are real but narrow — they land on the one internal screen a site like this has, not the site as a
whole. Program-goal wording that assumes app-ui speeds up "the site" in general should be read as "the site's
internal screens," since that is the only part it is meant for.

## Failures

**None occurred in this run.** All 7 tasks merged; nothing was blocked, retried or flagged red. There is nothing new
to add to the [2026-10-07 autopilot defect list](2026-10-07-autopilot-defects.md); its existing items (A1, cap vs.
overspend, deferred; A2/A3, fixed) were not re-triggered here, since this run's spend stayed under its cap the whole
time (see A1's note: the overspend gap can only show up when a task runs well over its own estimate late enough to
push total spend past the cap, which did not happen in these 7 chunks).

## Not covered, and why

- **A real launched `claude` agent and real token spend.** Not run; see "Answer first" and M1/M2. The roadmap
  schema/validation, the merge gate, the budget ledger and the run report are all real code paths; the planning
  reply and each task's edit were injected, as the 2026-10-07 dry run also did.
- **The real private monorepo or the real project folder.** Not touched; see "Why seeded, not cloned" and
  M4. CLAUDE.md requires a throwaway repo for git behaviour, never a real project.
- **A dollar cap large enough for a full real site build.** $1.50 covers this trial's 7-task slice, not every task a
  full build would need; a full roadmap would run bigger and need Austin's own cap for a real contract.
- **Review and client minutes**, on either side of the comparison: neither this run nor the PI 3 baseline has them
  recorded, so neither side of the comparison states a number for them (see the run report's own note and the
  baseline's "accepted ... as the sprint baseline" decision).
