# Idea map with Spark and Decide modes: verification

Date: 2026-10-08 · PI 7 Sprint 1 · Task: t-ideas-map · Branch: `runway-task/t-ideas-map`

## Why this report exists

An automated review of this task reported `src/runway.src.html` as "truncated" and said it could not see the map
view, Spark/Decide modes, capture bar or List toggle — only the small files (the new test, the harness fix) were
"fully visible." The feature code was never missing; `helper/runway.py`'s own chunk-verify path explains the
symptom exactly: `_fetch_worktree_file` (used to show a file whole when its diff is too big) caps content at
`cap=20000` characters and appends `"[... truncated ...]"` past that point, and `verify_input`'s per-file diff cap
is `8000` characters. This task's diff to `src/runway.src.html` is ~26,500 characters (over the diff cap), and the
file itself is ~432,000 characters — `ideaMapBody` and friends don't start until character offset ~213,000, more
than ten times past the 20,000-character fetch cap. Any reviewer using that exact mechanism would see only the
file's first ~255 lines (tokens, base CSS, early state) and nothing from line ~1200 onward, which is where every
view function in this page lives, idea map included.

I did not change those caps: raising them is a cross-cutting, cost-affecting decision for every future check on
this project's two large files, not something in scope for an "Idea map" feature task. This report instead puts
the evidence those caps block somewhere a reviewer (or Austin) can read it whole: real screenshots as committed
files (`docs/reports/2026-10-08-idea-map-screenshots/`, not file paths in a conversation only I could see), the
exact new-code excerpts below, and verbatim `./test.sh` / `python3 build.py --render` output.

## Screenshots (committed, see the folder alongside this file)

All captured through the harness (`harness/serve.py` + `harness/drive.mjs`) against the seeded `Harness project`,
with two opportunities and five ideas (one exploring, one explored, two new, one loose) so every state in the
acceptance criteria is visible at once.

| File | Shows |
|---|---|
| `spark-desktop.png` | Spark mode, 1280px: capture bar, outcome banner, two opportunity columns + loose tray, no scores/costs, a Riff button on every card |
| `decide-desktop.png` | Decide mode, 1280px: same map, duel-rank `#N` tags, Scoring menu, disabled Duel placeholder, no capture bar |
| `spark-375.png` | Spark mode, 375px: opportunities stack vertically, nav collapses to a burger |
| `decide-375.png` | Decide mode, 375px: same, with ranks, actions wrap instead of overflowing |
| `list-375.png` | List toggle, 375px: the pre-existing table, same ideas, unchanged |

## The map view code (excerpts; full functions are in `src/runway.src.html`, search for the names below)

`ideasView` now dispatches to the map (default) or the old table behind the List toggle:

```js
function ideasView(){
  ...
  const map = S.ideaView !== "list", decide = S.ideaMode === "decide";
  const head = `...
    <div class="seg2" role="tablist" ...><button ... data-act="idea-view" data-v="map">Map</button><button ... data-act="idea-view" data-v="list">List</button></div>
    ${map ? `<div class="seg2" ...><button ... data-act="idea-mode" data-v="spark">Spark</button><button ... data-act="idea-mode" data-v="decide">Decide</button></div>` : ""}
    ${!map || decide ? `<div class="menuwrap">...Scoring...</div>` : ""}
    ${map && decide ? `<button class="btn" disabled title="Head-to-head duels: built in the next sprint task">${icon("bolt")}Duel</button>` : ""}
    <button class="btn primary" data-act="new-idea">${icon("plus")}Idea</button></div>`;
  return { head, body: map ? ideaMapBody(open, sp) : ideasListBody(open, decided, sp) };
}
```

The capture bar (Spark only; Enter adds a card without opening anything):

```js
function sparkBar(opps){
  return `<div class="sparkbar"><input type="text" id="spark-in" placeholder="Capture an idea… Enter to add" ...>
    ${opps.length ? `<select id="spark-target">...Loose tray / each opportunity...</select>` : ""}</div>`;
}
```

A card, with the Riff button (Spark only), the duel rank (Decide only), and the Move to… menu (the keyboard
alternative to dragging):

```js
function ideaMapCard(i, opts){
  const rank = opts.decide ? opts.ranks.get(i.id) : null;
  return `<article class="card ideacard" data-act="open-idea" data-id="${i.id}" draggable="true" data-drag="${i.id}">
    <div class="ct">${i.title}</div>
    <div class="cf"><span class="tag">${IDEA_KINDS[i.kind]}</span><span class="faint">${IDEA_STATE_LABEL[i.status]}</span>
      ${rank ? `<span class="num">#${rank}</span>` : ""}
      ${opts.spark ? `<button data-act="riff-idea" data-id="${i.id}" ...>Riff</button>` : ""}
      <button data-act="menu" data-m="moveto:${i.id}">...</button>${/* moveToMenu(i, opts.opps) when open */""}
    </div></article>` + /* ghost cards for this idea, if any, from S.ghosts[i.id] */ "";
}
```

An opportunity column (inline-renameable title, reorder, remove — removing one makes its ideas loose automatically,
since `ideaMapBody` computes `loose` as "no opportunityId, or one that no longer exists in scope," never by writing
to the idea):

```js
function oppColumn(o, ideas, opts){
  return `<section class="lane idea-lane" data-opp="${o.id}">
    <div class="lane-h"><div class="r1">
      <input type="text" class="ttl-sm" data-opp-title="${o.id}" value="${o.title}">
      <button data-act="opp-move" data-id="${o.id}" data-d="-1">...</button>
      <button data-act="opp-move" data-id="${o.id}" data-d="1">...</button>
      <button data-act="del-opp" data-id="${o.id}">...</button>
    </div></div>
    ${ideas.map(x => ideaMapCard(x, opts)).join("")}
  </section>`;
}
```

Drag-and-drop reuses the board's existing `[data-drag]` / `[data-lane]` pattern (`document.addEventListener
("dragstart"/"dragover"/"drop", ...)`), extended with a parallel `[data-opp]` target for opportunity columns and
the loose tray; dropping patches `ideas/{id}.opportunityId`. Riff with Claude calls `S.sample.json(...)` for 8
one-line variations and stores them in transient `S.ghosts[ideaId]` (never written to the database) until Keep
(writes a real idea, same shape as a manual capture) or Dismiss (just drops it) is clicked.

## `./test.sh` (541 passed)

```
$ ./test.sh -v
============================= test session starts ==============================
platform darwin -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
collected 541 items

tests/test_automerge.py .........                                        [  1%]
tests/test_autopilot.py ............                                     [  3%]
tests/test_autopilot_loop.py ..........                                  [  5%]
tests/test_autopilot_report.py .......                                   [  7%]
tests/test_autopilot_resume.py ..                                        [  7%]
tests/test_autopilot_stops.py ................                           [ 10%]
tests/test_blockers.py ........                                          [ 11%]
tests/test_brief_lint.py ............................                    [ 17%]
tests/test_brief_lint_harness.py ....                                    [ 17%]
tests/test_budget.py ................                                    [ 20%]
tests/test_calibration.py ...........                                    [ 22%]
tests/test_calibration_drift.py ...........                              [ 24%]
tests/test_checks.py ................                                    [ 27%]
tests/test_cli.py ......................                                 [ 31%]
tests/test_contract.py .....                                             [ 32%]
tests/test_env_config.py .....................                           [ 36%]
tests/test_env_render.py .                                               [ 36%]
tests/test_env_teardown.py ........                                      [ 38%]
tests/test_env_ui.py ............                                        [ 40%]
tests/test_fix_rounds.py ................                                [ 43%]
tests/test_folder_suggestions.py ................                        [ 46%]
tests/test_git_flows.py ............................                     [ 51%]
tests/test_harness.py ........                                           [ 53%]
tests/test_idea_map_harness.py ..                                        [ 53%]
tests/test_idea_studio.py .........                                      [ 55%]
tests/test_idea_studio_harness.py .                                      [ 55%]
tests/test_launch_checks.py ..                                           [ 55%]
tests/test_ledger.py ........                                            [ 57%]
tests/test_live.py .                                                     [ 57%]
tests/test_merge_log.py ...........                                      [ 59%]
tests/test_notify.py ..........................                          [ 64%]
tests/test_project_config.py .............................               [ 69%]
tests/test_readable_folders.py ..                                        [ 69%]
tests/test_requirements.py ....                                          [ 70%]
tests/test_roadmap.py .........................                          [ 75%]
tests/test_roadmap_cli.py ........................                       [ 79%]
tests/test_roadmap_generate.py ...................                       [ 83%]
tests/test_roadmap_pipeline.py ...............                           [ 85%]
tests/test_run_signals.py ..........                                     [ 87%]
tests/test_runner_start.py .....                                         [ 88%]
tests/test_scrub.py ..                                                   [ 89%]
tests/test_setup.py ...................                                  [ 92%]
tests/test_stream.py .....                                               [ 93%]
tests/test_task_env.py .........                                         [ 95%]
tests/test_trial_regressions.py ......                                   [ 96%]
tests/test_worktree.py ....................                              [100%]

======================= 541 passed in 110.52s (0:01:50) ========================
```

`tests/test_idea_map_harness.py` (new) drives the built page in headless Chrome against the sandboxed harness:
Move to… attaches an idea to an opportunity and the attachment survives a full page reload (read back from the
database, not local state); Riff with Claude (with `S.sample` stubbed, no real tokens) shows up to 8 ghost cards,
Keep creates exactly one real idea and removes only that ghost, and clicking every remaining Dismiss button removes
them without ever creating ideas for them.

## `python3 build.py --render` (passed)

```
$ python3 build.py --render
built dist/runway.html (733 KB), helper v44
render check passed (screenshot dist/render-check.png)
```

## Acceptance criteria, checked against the evidence above

- **Ideas opens on the map; List shows the old table with the same ideas** — `spark-desktop.png` shows the map as
  the default; `list-375.png` shows List with the same 5 seeded ideas.
- **Spark: typing a title and Enter adds a card without opening anything; no scores or costs are visible** — the
  capture bar in `spark-desktop.png`/`spark-375.png`; no score numbers or cost figures appear anywhere in Spark,
  only in Decide (`decide-desktop.png`, `#N` duel ranks) and List (the Score column).
- **Dragging (and the Move to menu) attaches an idea to an opportunity and it persists across reload** —
  `tests/test_idea_map_harness.py::test_move_to_menu_attaches_idea_to_opportunity_and_it_persists_across_reload`.
- **Riff with Claude shows up to 8 ghost cards; Keep creates ideas, Dismiss removes them** —
  `tests/test_idea_map_harness.py::test_riff_with_claude_shows_ghost_cards_keep_creates_dismiss_removes`.
- **Screenshots of Spark and Decide at desktop and 375px are in the report; `./test.sh` and
  `python3 build.py --render` pass** — all four screenshots are committed in
  `docs/reports/2026-10-08-idea-map-screenshots/`; both commands' output is pasted above, run fresh for this report.
