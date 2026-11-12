# This-or-that duels rank ideas: verification

Date: 2026-10-08 · PI 7 Sprint 1 · Task: t-ideas-duels · Branch: `runway-task/t-ideas-duels`

## What changed

`src/runway.src.html`:
- A **Duel** overlay (`duelModal`, `openDuel`, `duelPick`, `duelCandidates`) opens from Decide mode: two idea
  cards side by side under "Which moves `<outcome>` more?" (the same opportunity/scope outcome text the map already
  shows). Pick by clicking a card, `←`/`→`, or a swipe on the pair at phone width; `=` records a tie, `S` a skip,
  `Esc` closes it (the page's existing generic Escape-closes-any-overlay handler, unchanged). Every pick calls the
  existing `recordDuel` (from the prior "Idea studio data model" task), which writes a duel row and updates both
  ideas' ratings optimistically, then the overlay serves the next pair from `nextDuel` over the same scope.
- A progress line ("N duels · ranking settles around 20") and, once a scope passes a multiple of 10 duels, its
  current top 5 inside the overlay.
- The header's **Duel** button (All scope) and a per-opportunity duel icon button ("Duel within this opportunity")
  on each map column; both disable when their scope has fewer than 2 open ideas.
- Decide-mode cards and the List view now show the duel rank (`#N`) for ideas with 3+ duels, and `unranked`
  otherwise; `ideaScore` only breaks ties among unranked (still-at-1000) ideas. The **Scoring** (weights) menu and
  its slider handler are removed.

`tests/`: `test_idea_duel_harness.py` (overlay mechanics: click/arrow/tie/skip/Esc, opportunity scoping, Scoring
menu gone, `unranked` under 3 duels) and `test_idea_duel_ranking_harness.py` (20 scripted picks → expected
ranking on cards and in List), both run through the harness per CLAUDE.md.

## Screenshots (committed, see the folder alongside this file)

Captured through the harness (`harness/serve.py` + `harness/drive.mjs`) against the seeded `Harness project`,
with 5 ideas and 10-11 scripted duels already run so the progress line (and, on the 10-duel shot, the top 5) are
visible.

| File | Shows |
|---|---|
| `duel-desktop.png` | Duel overlay, 1280px: two cards, Equal/Skip, keyboard hint, "11 duels · ranking settles around 20", ranks `#1`-`#5` on the cards behind it |
| `duel-375.png` | Same, 375px: cards stay side by side (not stacked), everything still reachable |
| `duel-top5-desktop.png` | After the 10th duel: the "Current top 5" panel inside the overlay |

## How "20 scripted picks produce the expected ranking" is verified

`test_idea_duel_ranking_harness.py` doesn't hardcode a ranking. It replays the exact same 20 duels — same 5 idea
ids, same deterministic rule ("the alphabetically-lower id always wins") — through the page's own `nextDuel` and
`eloUpdate` in a standalone `node` process (independent of the harness, same pattern as
`tests/test_idea_studio.py`), to get the ranking the UI must match. It then drives the harness, seeds the same 5
ideas, opens the Duel overlay, and runs the identical 20 picks through the page's real `duelPick` (the same
function clicks/keys call) 20 times, and asserts the Decide-mode card order, their rank badges, and the List
view's order and Rank column all equal that independently-computed order. Both runs are pure functions of the same
inputs, so they must agree if (and only if) the live UI is wired to the same ranking logic as the data model —
which is what the test is actually checking.

The ranking nextDuel's matchmaking produces from 20 duels over 5 ideas is **not** always a clean monotonic order
(it's adaptive pairing, not an exhaustive round robin): with these 5 ids the real output was `rk1, rk4, rk2, rk3,
rk5`, not `rk1..rk5` in order, even though "lower id always wins" every single duel. That's expected — "ranking
settles around 20" is the product's own wording for an estimate, not a guarantee, and the test holds the UI to
whatever the engine actually produces rather than to a hand-picked "nice" answer.

## Verbatim output

```
$ ./test.sh
======================== 545 passed in 96.39s (0:01:36) ========================

$ python3 build.py --render
built dist/runway.html (738 KB), helper v44
render check passed (screenshot dist/render-check.png)
```

One pre-existing test, `tests/test_harness.py::test_worktree_setup_command_field_persists_through_the_page`, is
flaky under a full-suite run (it timed out waiting for `#p-setup` once); it fails the same way on a clean
checkout with none of this task's changes applied, run alone, so it's unrelated to this task.

## Left undone / follow-ups

- The swipe gesture (touchstart/touchend on `.duel-pair`) isn't covered by an automated test — Chrome's CDP
  doesn't make synthetic touch gestures easy to script reliably — but the 375px screenshot shows the duel pair
  laid out for it, and the same `duelPick` function backs click, keys and swipe alike.
- "Duel within this opportunity" is covered by a harness test (`test_duel_scoped_to_an_opportunity_only_offers_its_own_ideas`)
  but not separately screenshotted; it uses the same `duelModal` as the All-scope screenshots above.
