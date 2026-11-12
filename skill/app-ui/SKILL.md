---
name: app-ui
description: Build or review internal app and admin screens (dashboards, triage queues, CRUD tables, record editors, M4P admin) to the Runway standard: one token set with dark mode, a single status-dot language, left-nav shell, keyboard-driven triage list, tabbed side panel, floating action dock, expandable table, ⌘K palette, and an empty/loading/error state for every view. Use when asked to create, restyle or audit an app/admin UI, "make it look like Runway", or apply the app-ui checklist. Not for marketing pages or slide decks.
---

# app-ui

Runway's design system and interaction rules, written down so other admin screens
(M4P admin first) come out the same: quiet, dense, keyboard-friendly, and honest
about state. Everything here is lifted from `src/runway.src.html` in the Runway repo;
when in doubt, that file is the reference implementation.

## The five rules

1. **Tokens only.** Every colour, radius, shadow and font comes from the `:root`
   tokens in [references/tokens.md](references/tokens.md). No raw hex in components.
   Dark mode is the same tokens redefined, never a second stylesheet.
2. **Orange means "do this".** `--act` is reserved for the one next action on a
   screen (primary button, focus ring, unread count, selection). Status never uses it.
3. **One status language.** State is always the `.st` dot plus a word
   (To do · Running · Review · Done · Failed). Tags are for categories, not state.
4. **Every view has three more states**: empty (says what to do next, with a
   button), loading, and error (says what failed and offers a retry). See
   [references/components.md](references/components.md#states).
5. **Overlays always have an exit**: a visible close button, a backdrop click,
   `Esc`, and selecting something inside a navigation overlay closes it.

## Procedure

Building a new screen:

1. Read [tokens.md](references/tokens.md) and paste the token block, base and
   controls CSS into the page (or confirm the page already has them).
2. Lay out the **shell** (left nav, header, scrolling content) from
   [components.md](references/components.md#shell). Put the view title and its
   actions in the header; nothing else goes in the header except tabs.
3. Pick the component per job:
   - queue of things a person must act on → **triage list**
   - records with columns, possibly with children → **expandable table**
   - viewing/editing one record without losing the list → **tabbed side panel**
   - acting on a multi-selection → **floating action dock** (or sticky bulk bar in tables)
   - jumping anywhere / running commands → **command palette**
   - short forms and confirmations → **modal**
4. Wire the keyboard and overlay behaviour from
   [interaction.md](references/interaction.md): `⌘K`, `g`-then-key navigation,
   `j/k/x/⏎` in lists, `Esc` backs out one layer at a time.
5. Write the empty, loading and error state for each view before calling it done.
6. Run [checklist.md](references/checklist.md) against the page (≈10 minutes),
   in light and dark, wide and at 900px or narrower. Fix every "no".

Reviewing an existing screen: go straight to step 6 and report failures as
`item → what you saw → fix`.

## Copy rules

- Sentence case everywhere. Buttons are verbs ("Merge 3", "Launch 2", "Run again").
- Counts live in the button or header sub-line ("3 need you", "All clear").
- Empty states say why it's empty and what to do: "Nothing needs you. Pick tasks
  on the board and launch them." plus an `Open the board` button.
- Errors say what failed, in plain words, and offer the fix: "Live updates stopped:
  loading tasks failed (permission). [Reconnect] [Reload page]".
- Numbers use `.num` (mono, tabular).

## Files

- [references/tokens.md](references/tokens.md): colour, type, radius, shadow tokens; light and dark; base CSS.
- [references/components.md](references/components.md): every component with markup, CSS and states.
- [references/interaction.md](references/interaction.md): keyboard model, focus, overlays, responsive nav.
- [references/checklist.md](references/checklist.md): the 10-minute review.
