# app-ui checklist

Run against a live page. Budget: ~10 minutes. Mark each item yes / no / n/a; every
"no" gets a line in the report as `item → what you saw → fix`.

Setup (1 min): open the page wide (≥1200px) in light mode; have DevTools ready to
toggle dark (`Rendering → prefers-color-scheme`) and to resize to 800px.

## 1. Tokens and colour (2 min)

Search the page's CSS for `#` colours outside `:root` (e.g. regex `#[0-9a-fA-F]{3,6}` and skim the hits).

- [ ] 1.1 Components use `var(--…)` only; raw hex appears only in the token blocks (overlay rgba and `.dock .warn` are the known exceptions).
- [ ] 1.2 Orange (`--act`) appears only on: one primary button per region, focus rings, the "needs you" count, selection. No orange status, link, or decoration.
- [ ] 1.3 Toggle dark: every surface, text, border and tag flips; nothing stays white or unreadable; primary button text is dark on orange.
- [ ] 1.4 Numbers (counts, tokens, money, durations) are mono/tabular (`.num`).

## 2. Status language (1 min)

- [ ] 2.1 Every status is a `.st` dot + word; same five colours (grey, blue-pulsing, amber, green, red) on every view.
- [ ] 2.2 Only "running" animates.
- [ ] 2.3 Tags are used for kinds/verdicts, not for live state.

## 3. Shell (1 min)

- [ ] 3.1 Left nav, header, scrolling content; header stays put while content scrolls.
- [ ] 3.2 Current view is marked (`aria-current="page"`, raised item).
- [ ] 3.3 Header has the view title, a sub-line count/status, and the view's actions on the right.
- [ ] 3.4 Nav counts: orange only for "needs you", grey for informational, none at zero.

## 4. Components (3 min, open each that exists)

- [ ] 4.1 **Triage list**: `j`/`k` move a visible focus (orange left edge) and scroll it into view; `⏎` opens; `x` selects; header shows the key hints and the selected count; bulk primary is disabled at 0.
- [ ] 4.2 **Side panel**: opens on the right with list visible; header has status, breadcrumb, `×`; tabs switch without losing edits; footer has one primary on the right.
- [ ] 4.3 **Dock**: appears on selection, summarises it, disappears on clear / `Esc` / leaving the view; warnings say what to fix; last content row isn't hidden behind it.
- [ ] 4.4 **Expandable table**: parent row toggles children with a rotating caret; missing values show `–`; destructive row actions are red.
- [ ] 4.5 **Palette**: `⌘K` opens from anywhere (even in an input) with the input focused; arrows move; `⏎` runs and closes; "No matches" when empty.
- [ ] 4.6 Buttons: disabled ones explain why (`title` or nearby text); icon-only buttons have `aria-label`.

## 5. Overlays (1 min)

For each of: side panel, one modal, palette, and (at 800px) the nav:

- [ ] 5.1 Visible close control.
- [ ] 5.2 Backdrop click closes (clicking inside does not).
- [ ] 5.3 `Esc` closes, one layer at a time (overlay → menu/nav → selection).
- [ ] 5.4 Nav overlay: choosing any item closes it.
- [ ] 5.5 Single-letter shortcuts do nothing while typing or while an overlay is open.

## 6. States (2 min)

For every view (and each async tab in the side panel). Force them: empty filters,
throttle network / block the data call, or use a fresh account.

- [ ] 6.1 **Empty**: icon + heading + one sentence + next-step button. "No results for this filter" is distinct from "nothing yet".
- [ ] 6.2 **Loading**: header renders immediately; body shows "Loading…" or a spinner with a label. Long jobs say what's happening and offer Stop.
- [ ] 6.3 **Error**: a banner or `.note.bad` with the cause in plain words and a retry/fix button. Not only a toast; never a blank view.
- [ ] 6.4 Degraded mode (example data, missing capability) is announced by a banner.

## 7. Small screen and motion (30 s)

- [ ] 7.1 At 800px: burger shows, nav is off-canvas, row actions always visible, forms stack, dock centred, nothing scrolls sideways except tables/tabs.
- [ ] 7.2 With `prefers-reduced-motion: reduce`, nothing animates (pulse and spinner included).

## Report

```
app-ui checklist · <page> · <date>
Pass: N / total (n/a: M)
Fails:
- 4.3 → dock covers last table row on Runs → add 120px bottom padding to .content
```
