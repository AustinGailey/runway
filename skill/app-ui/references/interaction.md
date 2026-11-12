# Interaction

## Keyboard model

One document-level `keydown` handler. Order matters: palette first, then `Esc`, then
bail if the user is typing or an overlay is open, then global and view keys.

| Key | Where | Does |
|---|---|---|
| `⌘K` / `Ctrl+K` | anywhere, even in inputs | open the command palette |
| `g` then `i` / `b` / `r` / `e` / `d` | not typing | go to a view (second key within 1.2s) |
| `j` / `↓`, `k` / `↑` | triage list | move focus |
| `⏎` / `o` | triage list | open the focused item in the side panel |
| `x` | triage list | toggle selection of the focused item |
| `m` | triage list | act on the selection (merge), or the focused item if nothing is selected |
| `d` | triage list | discard the focused item (asks for confirmation) |
| `↑` `↓` `⏎` `Esc` | palette | move, run, close |
| `Esc` | anywhere | back out one layer (see below) |

Show the keys where they're used: the list header carries
`<kbd>j</kbd> <kbd>k</kbd> move · <kbd>x</kbd> select · <kbd>m</kbd> merge · <kbd>⏎</kbd> open`,
the nav search button shows `<kbd>⌘K</kbd>`, palette commands show their `g x` shortcut.

```js
document.addEventListener("keydown", ev => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName);
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "k"){ ev.preventDefault(); click("palette"); return; }
  const o = S.overlay;
  if (o?.kind === "palette"){ /* ↑ ↓ wrap around, ⏎ closes then runs, Esc closes */ return; }
  if (ev.key === "Escape"){
    if (o){ S.overlay = null; render(); return; }                    // 1. overlay
    if (S.menu || S.navOpen){ S.menu = null; S.navOpen = false; render(); return; } // 2. menu / nav
    if (S.sel.size){ S.sel.clear(); render(); }                     // 3. selection (hides the dock)
    return;
  }
  if (typing || o || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (S.gAt && now() - S.gAt < 1200){ S.gAt = 0; const v = { i:"inbox", b:"board", r:"runs", e:"estimates", d:"ideas" }[ev.key]; if (v){ ev.preventDefault(); go(v); } return; }
  if (ev.key === "g"){ S.gAt = now(); return; }
  if (S.view !== "inbox") return;
  const items = S._items || [], it = items[S.kIdx];
  if (ev.key === "j" || ev.key === "ArrowDown"){ ev.preventDefault(); S.kIdx = Math.min(items.length - 1, S.kIdx + 1); S.kbdMoved = true; render(); }
  else if (ev.key === "k" || ev.key === "ArrowUp"){ ev.preventDefault(); S.kIdx = Math.max(0, S.kIdx - 1); S.kbdMoved = true; render(); }
  // ⏎/o open, x select, m act, d discard
});
```

Rules:
- Single-letter keys never fire while typing, with a modifier, or under an overlay.
- The focus index is clamped on every render (`if (S.kIdx >= items.length) S.kIdx = items.length - 1`) so acting on the last row doesn't strand focus.
- After a keyboard move, scroll the focused row into view with `block:"nearest"` (only after keyboard moves, not on every render).
- Keyboard and mouse share one action path: keys dispatch the same `data-act` as the buttons (`click("merge-selected")`), so behaviour can't drift.

## Focus

- `:focus-visible{outline:2px solid var(--act);outline-offset:1px}` on everything; inputs swap the outline for an orange border plus `0 0 0 3px var(--act-soft)` glow.
- Opening the palette or a form modal focuses its first input (`setTimeout(() => $("#pal-q")?.focus(), 0)`).
- Re-rendering an overlay preserves focus: remember `document.activeElement.id` before replacing the HTML and refocus it after.
- Re-render overlays only when their state changed (compare a JSON signature), so typing in a drawer isn't interrupted by background updates.
- Palette filtering replaces only the `<ul>`, never the input.

## Overlays

Three kinds, one host (`#overlay`), one state slot (`S.overlay`), so only one is ever open.

| Kind | Container | Use |
|---|---|---|
| side panel | `.scrim > aside.drawer` | one record, list stays visible |
| modal | `.scrim.center > .modal` | short form, confirmation, long-running job |
| palette | `.scrim.center > .pal` | jump / command |

Every overlay must have **all** of these exits:
1. **A visible close control** in its header: `×` icon button with `aria-label="Close"` (drawers) or a "Close" button (modals). Never rely on Esc alone.
2. **Backdrop click**: the scrim carries `data-act="close-scrim"` and closes only when the click target *is* the scrim, not a child:
   ```js
   if (act === "close-scrim"){ if (ev.target === el){ S.overlay = null; render(); } return; }
   ```
   Exception: modals running a job that would be lost (planner streaming) omit `close-scrim`; they show a **Stop** button instead.
3. **Esc** closes it (and aborts any job it owns).
4. Overlays use `role="dialog"` and an `aria-label` or visible `<h2>`.

## Responsive nav overlay

At ≤900px the left nav becomes an off-canvas overlay. The **overlay-nav rule**:
visible close, backdrop, close on select.

```css
@media (max-width:900px){
  .shell{grid-template-columns:1fr}
  .nav{position:fixed;inset:0 auto 0 0;width:260px;z-index:50;transform:translateX(-100%);transition:transform .16s;box-shadow:var(--shadow-2)}
  .nav.open{transform:none}
  .burger{display:inline-flex}
  .brand .navclose{display:inline-flex}
  .navscrim:not([hidden]){display:block;position:fixed;inset:0;z-index:45;background:rgba(10,12,16,.38);animation:fade .12s ease-out}
  .head{padding:0 16px}
  .content{padding:16px 16px 140px}
  .item{grid-template-columns:20px minmax(0,1fr);}
  .item .kind{display:none}
  .item .a{opacity:1;grid-column:2}
  .grid2,.grid3{grid-template-columns:1fr}
  .dock{left:50%;max-width:calc(100% - 24px)}
  .kv{grid-template-columns:1fr}
}
```

```js
// render(): state drives both the panel and its backdrop
$("#nav").className = "nav" + (S.navOpen ? " open" : "");
$("#navscrim").hidden = !S.navOpen;

// click handler
if (act === "nav-close"){ S.navOpen = false; render(); return; }   // × button and the scrim
if (S.navOpen && el.closest("#nav")) S.navOpen = false;            // any choice inside the nav closes it
case "nav": S.navOpen = !S.navOpen; render(); break;               // burger
function go(v){ S.view = v; S.navOpen = false; /* … */ render(); }  // every navigation closes it
```

1. **Visible close**: a `×` (`.navclose`) in the brand row, shown only at this width.
2. **Backdrop**: `#navscrim` dims the page and closes the nav on click.
3. **Close on select**: picking any nav item (view, project, settings, palette) closes the nav before acting. Esc closes it too.

Other small-screen rules: hover-revealed row actions are always visible (no hover on
touch); secondary columns (`.kind`) drop; two-column forms stack; the dock centres;
footers add `env(safe-area-inset-bottom)`.

## Live data

- Rendering is one `render()` from state; events mutate state then call it.
- Background refreshes must not move the user: keep the focused index, open rows, open tab, and scroll.
- Optimistic UI for quick edits; for long jobs show progress in place (list strip, table sub-line) and the outcome as a dismissible note, not a toast.
