# Jobs contract (shared with Take One)

Runway follows `~/Projects/video-studio/JOBS-CONTRACT.md` (2026-10-17): a task is Running only while a live process
holds a fresh lease; the helper owns the terminal state; a reaper ends dead runs.

- Helper (`helper/runway.py`): `lease_reap()` runs on every tick. It renews `runs/<chunk>/<task>.lease.json`
  (`until` = now + 180 s) while the run's process group lives, interrupts an agent past the hard timeout
  (`runs/<chunk>/_timeout`, default 7200 s: SIGINT, then SIGTERM a minute later; the task ends `handed-back`), and gives
  every open task of a dead run one terminal (`<task>.terminal.json`): `requeued`, then `needs-austin` after 2 reaps.
  `terminal_of()` classifies finished tasks. `chunk_status` carries `lease`, `terminal`, `timedOut`.
- Page: `leaseLive(t)` decides Running (helper lease when status.json is under 3 min old, else `run.leaseUntil`,
  written at launch as start + `settings.agentTimeoutMin` (default 120) + 5 min). Labels: Not running, Needs you.
  State `reaped` from the helper moves the task to To do (requeued) or Failed with the ask.
- run.sh: the tick and live loops also stop when run.sh itself is gone (`kill -0 $$`), so a killed run leaves no loops.
- Tests: `tests/test_leases.py`.
