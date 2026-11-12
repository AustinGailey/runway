# Tokens

Copy these verbatim. Components reference only `var(--…)`; never a raw hex.

## Roles

| Token | Use |
|---|---|
| `--canvas` | page background behind content |
| `--panel` | cards, lists, tables, header, drawers, inputs |
| `--nav` | left nav background |
| `--hover` | hover and keyboard-focus row background |
| `--sunk` | table/list headers, sub-rows, code, neutral tags |
| `--ink` / `--ink-2` / `--ink-3` | primary text / secondary / faint (labels, meta) |
| `--line` / `--line-2` | dividers / control borders |
| `--act`, `--act-hover`, `--act-ink`, `--act-soft` | **the next action only**: primary button, focus ring, unread count, selection |
| `--st-*` | status dot colours (todo, running, review, paused, done, failed) |
| `--run-soft`, `--rev-soft`, `--done-soft`, `--fail-soft` | tinted backgrounds that pair with a status (progress bar, banners, pass/fail tags) |
| `--add`, `--add-bg`, `--del`, `--del-bg` | diff lines |
| `--shadow-1` | resting controls, current nav item |
| `--shadow-2` | floating things: menus, drawers, modals, dock, toast |
| `--r` (4px) / `--r-lg` (8px) | controls / containers |
| `--sans` / `--mono` | Inter / JetBrains Mono (numbers, code, kbd) |

Inverted surfaces (dock, bulk bar, toast, tooltips) use `background:var(--ink); color:var(--canvas)`,
so they flip correctly in dark mode with no extra tokens.

## Light (default)

```css
:root{
  --canvas:#F6F7F9; --panel:#FFFFFF; --nav:#F0F2F5; --hover:#EBEEF2; --sunk:#F1F3F6;
  --ink:#0F1217; --ink-2:#464C57; --ink-3:#848B96; --line:#E3E6EB; --line-2:#D2D6DD;
  --act:#E8590C; --act-hover:#D14F09; --act-ink:#FFFFFF; --act-soft:#FDEEE5;
  --st-todo:#9AA1AC; --st-running:#2F6FEB; --st-review:#C27C0E; --st-paused:#C27C0E; --st-done:#1F883D; --st-failed:#CF222E;
  --run-soft:#E7EFFD; --rev-soft:#FBF1E0; --done-soft:#E3F3E7; --fail-soft:#FCE8E9;
  --add:#1F883D; --add-bg:#E6F4EA; --del:#CF222E; --del-bg:#FCEBEC;
  --shadow-1:0 1px 2px rgba(15,18,23,.05); --shadow-2:0 8px 28px rgba(15,18,23,.12),0 1px 3px rgba(15,18,23,.08);
  --r:4px; --r-lg:8px;
  --sans:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
```

## Dark

Dark follows the OS unless the page forces a theme with `<html data-theme="light|dark">`.
The same block appears twice: once under the media query (skipped when forced light),
once for forced dark. Keep them identical.

```css
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  color-scheme:dark;
  --canvas:#0D0F12; --panel:#14171C; --nav:#101318; --hover:#1B1F26; --sunk:#181C22;
  --ink:#E9EBEE; --ink-2:#A6ACB5; --ink-3:#6D7480; --line:#22262D; --line-2:#2D323A;
  --act:#FF7A2E; --act-hover:#FF8C49; --act-ink:#1A0D05; --act-soft:#2E1C11;
  --st-todo:#5E6571; --st-running:#5B93FF; --st-review:#E3A23C; --st-paused:#E3A23C; --st-done:#46C46A; --st-failed:#FF6B6B;
  --run-soft:#152238; --rev-soft:#2B2213; --done-soft:#14271B; --fail-soft:#2E1517;
  --add:#46C46A; --add-bg:#132A1B; --del:#FF6B6B; --del-bg:#2E1517;
  --shadow-1:0 1px 2px rgba(0,0,0,.4); --shadow-2:0 12px 32px rgba(0,0,0,.5),0 1px 3px rgba(0,0,0,.4);
}}
:root[data-theme="dark"]{ /* same declarations as above */ }
```

Note in dark: `--act-ink` becomes near-black, because the brighter orange needs dark text on it.

## Type and base

13px/1.5 Inter body; 15px bold for the header title; 16px for modal and empty-state
headings; 11–12px for meta, labels and tags. Headings get `letter-spacing:-.01em`.

```css
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--canvas);color:var(--ink);font:13px/1.5 var(--sans);-webkit-font-smoothing:antialiased;font-feature-settings:"cv11","ss01"}
button,input,select,textarea{font:inherit;color:inherit}
h1,h2,h3{margin:0;text-wrap:balance;letter-spacing:-.01em}
.num,.mono{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:12px;letter-spacing:-.02em}
.muted{color:var(--ink-2)} .faint{color:var(--ink-3)}
:focus-visible{outline:2px solid var(--act);outline-offset:1px}
kbd{font:500 11px var(--mono);color:var(--ink-3);border:1px solid var(--line-2);border-bottom-width:2px;border-radius:3px;padding:0 4px;background:var(--panel)}
svg.i{width:16px;height:16px;flex:none;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
```

Icons: 20×20 viewBox, stroked, `currentColor`, rendered at 16px, `aria-hidden="true"`.

## Spacing and sizes

No spacing scale token; the source uses a small fixed set. Stick to it:

- Control heights: 30px (`.btn`, nav items), 26px (`.btn.sm`, chips), 32px (inputs), 36px (palette rows), 40px (tabs, list header), 56px (header min).
- Gaps: 6, 8, 10, 12, 16px. Row padding 10px 14px (list) / 10px 12px (table).
- Content padding 20px 24px desktop, 16px under 900px; bottom padding 120px+ so the dock never covers the last row.
- Nav width 236px (260px when it becomes an overlay).

## Motion

Fast and small: fades 120ms, drawer slide 160ms (24px), modal pop 140ms (6px),
hover reveals 120ms. The only looping animations are the running-dot pulse and the
spinner. All of it turns off under `prefers-reduced-motion`.
