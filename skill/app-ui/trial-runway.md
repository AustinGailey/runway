# app-ui checklist trial · Runway · 2026-10-04

Built `dist/runway.html` (helper v19) and ran `references/checklist.md` (from `runway-task/t-runway-skill`, commit `171f530`) against it in headless Chrome with a stub `window.claude`: example data for the normal run, a never-resolving `claude.use` for the loading run. The checklist was driven over the DevTools protocol for the interactive items. Screenshots and the driver were scratch files in `dist/` (gitignored) and are not committed.

## Result

Pass 16 · Partial 4 · Fail 2 (both fixed here, four findings) · Open 2 · n/a or not tested 4 · of 28 items

Tested items are marked by what was exercised. "Code" means checked by reading the source, not run.

| Item | Result | Note |
|---|---|---|
| 1.1 raw hex outside tokens | Pass | Only `.dock .warn` hex. `rgba()` literals also appear (scrims, dock, bulk bar); the checklist does not name them. See checklist changes. |
| 1.2 orange only for "do this" | Pass | Screenshots: primary buttons, nav "needs you" count. |
| 1.3 dark mode | Pass | Scan of near-white backgrounds across all five views. One hit: the selected segment (`Lanes`) uses `--ink` on `--canvas`, which is inverted by design. |
| 1.4 numbers mono | Pass | Visual only; not scanned. |
| 2.1 `.st` dot + word | Pass | Visual on Board; dot in the sidebar sync line. |
| 2.2 only running animates | Pass | CSS: `pulse` only on `.st.running::before`, `rot` only on `.spin`. |
| 2.3 tags not live state | **Open** | Board card shows a `Blocked` tag, which is live dependency state. See follow-ups. |
| 3.1–3.2 shell, `aria-current` | Pass | `navHTML()` sets `aria-current="page"`. Sticky header not scrolled. |
| 3.3 header title / sub-line / actions | **Open** | Board header is a breadcrumb with no sub-line. See follow-ups. |
| 3.4 nav counts | Pass | Code: orange `.count`, grey `.count.calm`. No count non-zero in example data to see it. |
| 4.1 triage keys | n/a | Example data has no review rows, so the inbox list cannot be exercised. Focus CSS (`.item.focus`, orange inset edge) read in code. |
| 4.2 side panel | Partial | Opens; header shows status and breadcrumb; tabs Brief/Run/Changes/Prompt present. Footer not checked. |
| 4.3 dock | Not tested | Needs a board selection step. Code: `renderDock` clears on no selection or non-board view. |
| 4.4 expandable table | Not tested | |
| 4.5 palette | Partial | `⌘K` opens from the page with input focused; `Esc`, backdrop and inside-click verified. Arrows, `⏎` and "No matches" read in code only. |
| 4.6 disabled buttons explain why | **Fail, fixed** | See F1. |
| 5.1 visible close control | Pass for side panel; palette exempt | The palette has no ×; its footer shows `esc close`. See checklist changes. |
| 5.2 backdrop closes, inside does not | Pass | Palette backdrop closes; inside-click keeps it open. Side panel inside-click verified; side panel backdrop not clicked. |
| 5.3 `Esc` one layer at a time | Partial | Palette and side panel close on `Esc`. Stacked layers not tested. |
| 5.4 nav overlay closes on choice | Pass | Verified at 800px. |
| 5.5 letter shortcuts guarded | Pass | `b` with palette open leaves the view unchanged. |
| 6.1 empty states | **Fail, fixed** | See F2–F4. |
| 6.2 loading | Pass | Hang stub: header renders at once, body shows `Loading…`. Long-job Stop: see follow-ups. |
| 6.3 error state | Not tested | Needs a failing `db` subscription. Code: `.banner` with retry on live-update failure; `.note.bad` in forms. |
| 6.4 degraded mode banner | Pass | "Showing example data…" banner on every view. |
| 7.1 800px | Partial | Burger shows, nav off-canvas, no page-level sideways overflow on Board, Runs, Estimates. Dock centring and row actions not tested. Lanes scroll sideways by design. |
| 7.2 reduced motion | Pass | Zero running animations under `prefers-reduced-motion: reduce`. |

## Findings and fixes (`src/runway.src.html`)

- **F1 · 4.6 · disabled buttons with no reason.** Five Claude- or helper-gated controls were disabled with no `title`: `Plan with Claude` (nav), `Refresh` (Inbox, Runs), `Plan next PI` (Board), and the `⋯` menu items `Import from Claude Inbox` and `Export plan to the vault`. Fixed: a `title` on each, from two constants `NEEDS_CLAUDE` and `NEEDS_HELPER`. Verified: the disabled-without-title scan is empty on every view in light and dark.
- **F2 · 6.1 · filtered table empty state.** "No tasks match." was a bare line with no next step. Fixed: icon, heading, sentence, and a `Clear search and filters` button (`data-act="tclear"`, which resets search and filters). Verified: typing a non-matching search shows the new state; the button clears the search.
- **F3 · 6.1 · Runs empty state had no next-step button.** Fixed: `Open the board` button added. Verified in screenshot.
- **F4 · 6.1 · Board "No program increments yet" had no icon and no button.** Fixed: plan icon and a `Plan next PI` button with the same gating and `title` as the header button. Code only; the example project always has a PI, so the empty state was not reached.

Build: `python3 build.py --render` passes after the fixes (render check and script parse).

## Checklist changes (apply on `runway-task/t-runway-skill`, `references/checklist.md`)

The checklist lives on the sibling branch, not in this worktree, so I have not edited it here.

- **1.1** Replace the exceptions with the real list: "raw hex appears only in the token blocks (`:root`, the dark token block, and the `prefers-color-scheme` block). Exceptions: `.dock .warn`. `rgba()` is allowed only for scrims (`.scrim`, `.navscrim`) and on-dark tints inside `.dock` and `.bulk`."
- **1.3** Add: "The selected segment (`aria-selected="true"`) inverts to `--ink` on `--canvas`. That is correct in both modes."
- **2.2** Change "Only 'running' animates" to "Motion appears only on running work: the pulse on a running dot and spinners on busy work. Static states do not move."
- **4.6** Define "explains why": "a `title` on the disabled element (or visible text next to it) names the missing thing, such as the connection or the helper. Scan: `button:disabled` without a `title`."
- **5.1** Add: "The command palette is exempt; its footer shows `esc close`."
- **6.1** Define "next-step button": "a button whose action leads to the next step (e.g. `Open the board`, `Clear search and filters`). A disabled button does not count."
- **6.2** Make Stop testable: "Long jobs (merge, land, Claude calls over ~10s) show a label and a Stop button. If Stop does not exist yet, record 'no' and file a follow-up."
- **7.1** Make it testable: "At 800px, `document.documentElement.scrollWidth === clientWidth` on every view. Exceptions: the Board lanes row and tables/tabs."
- **7.2** Make it testable: "With `prefers-reduced-motion: reduce` emulated, no element has a running `animationName`."
- **Scan commands:** name them in the checklist (`button:disabled` without `title`; `document.documentElement.scrollWidth`; `getComputedStyle(el).animationName`), so the 10-minute budget covers the automatable items.

## Follow-ups (not done here; scope)

1. **Board header has no sub-line** (3.3). Decide whether the project summary belongs in the header.
2. **`Blocked` card tag is live state** (2.3). Probably a `.st` dot with a word.
3. **Merge and land progress has no Stop** (6.2). Needs helper support.
4. **`Split with Claude` disabled without a reason** (4.6). Its gate is `S.sample && launchable(t)`; only the Claude half has a reason today.
5. **Dock and row actions at 800px** (7.1) and **dock on selection, expandable table, error state** (4.3, 4.4, 6.3) need a driver with selection and failing-data stubs.
6. **Git:** the checklist and SKILL.md are on `runway-task/t-runway-skill` (commit `171f530`), not on `main`. This worktree does not include them, so this file's checklist path is `skill/app-ui/references/checklist.md` on that branch.
