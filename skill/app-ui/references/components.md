# Components

CSS is copied from Runway's `src/runway.src.html`; markup is the rendered output of
its templates with data filled in. Each component lists its states. Shared states
(empty, loading, error) are at the end under [States](#states).

Contents: [Controls](#controls) · [Status dot and tags](#status-dot-and-tags) ·
[Shell](#shell) · [Triage list](#triage-list) · [Tabbed side panel](#tabbed-side-panel) ·
[Floating action dock](#floating-action-dock) · [Expandable table](#expandable-table) ·
[Command palette](#command-palette) · [Modal, menu, toast](#modal-menu-toast) · [States](#states)

---

## Controls

```css
.btn{display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 12px;border:1px solid var(--line-2);background:var(--panel);border-radius:var(--r);font-weight:600;font-size:13px;cursor:pointer;white-space:nowrap;box-shadow:var(--shadow-1)}
.btn:hover{background:var(--hover)}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn.primary{background:var(--act);border-color:var(--act);color:var(--act-ink);box-shadow:none}
.btn.primary:hover{background:var(--act-hover)}
.btn.quiet{background:transparent;border-color:transparent;box-shadow:none;color:var(--ink-2)}
.btn.quiet:hover{background:var(--hover);color:var(--ink)}
.btn.danger{color:var(--st-failed)}
.btn.sm{height:26px;padding:0 9px;font-size:12px}
.btn.icon{width:30px;padding:0;justify-content:center}
input[type=text],input[type=number],input[type=search],select,textarea{height:32px;width:100%;padding:0 10px;background:var(--panel);border:1px solid var(--line-2);border-radius:var(--r)}
textarea{height:auto;padding:8px 10px;line-height:1.5;resize:vertical;min-height:84px}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--act);box-shadow:0 0 0 3px var(--act-soft)}
.chk{width:15px;height:15px;accent-color:var(--act);margin:0;cursor:pointer}
.fld{display:flex;flex-direction:column;gap:6px}
.fld label{font-size:12px;font-weight:600;color:var(--ink-2)}
.fld .hint{font-size:11.5px;color:var(--ink-3)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
```

```html
<button class="btn primary"><svg class="i">…</svg>Launch 3</button>
<button class="btn">Refresh</button>
<button class="btn quiet">Discard</button>
<button class="btn quiet icon" aria-label="Close"><svg class="i">…</svg></button>
<div class="fld"><label for="t-est">Estimate (tokens)</label>
  <input type="number" id="t-est"><span class="hint">Scaled ×1.20 from similar runs</span></div>
```

States: default · hover (`--hover`) · focus (`--act` ring via `:focus-visible` / input glow) ·
disabled (45% opacity, `not-allowed`, and a `title` saying why) · busy (label changes to
"Splitting…" and the button is disabled). At most **one** `.primary` per region.
Icon-only buttons always carry `aria-label`.

Segmented switch and filter chips (view modes, filters):

```css
.seg2{display:inline-flex;border:1px solid var(--line-2);border-radius:var(--r);overflow:hidden}
.seg2 button{border:0;background:var(--panel);height:28px;padding:0 10px;font-size:12px;font-weight:500;color:var(--ink-2);cursor:pointer;border-right:1px solid var(--line)}
.seg2 button:last-child{border-right:0}
.seg2 button[aria-selected="true"]{background:var(--ink);color:var(--canvas)}
.fchip{height:26px;padding:0 9px;border:1px solid var(--line-2);border-radius:13px;background:var(--panel);font-size:12px;color:var(--ink-2);cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.fchip[aria-pressed="true"]{background:var(--ink);color:var(--canvas);border-color:var(--ink)}
```

```html
<div class="seg2" role="tablist"><button role="tab" aria-selected="true">Lanes</button><button role="tab" aria-selected="false">Table</button></div>
<button class="fchip" aria-pressed="true">Review <span class="num">4</span></button>
```

Selected = inverted ink, not orange (it's a view choice, not an action).

---

## Status dot and tags

One language everywhere: a coloured dot and a word. Running pulses; nothing else moves.

```css
.st{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:500;color:var(--ink-2);white-space:nowrap}
.st::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--st-todo);flex:none}
.st.running::before{background:var(--st-running);box-shadow:0 0 0 3px var(--run-soft);animation:pulse 1.6s ease-in-out infinite}
.st.review::before,.st.paused::before{background:var(--st-review)}
.st.done::before{background:var(--st-done)}
.st.failed::before{background:var(--st-failed)}
@keyframes pulse{50%{box-shadow:0 0 0 5px transparent}}
```

```js
const stDot = (status, label) => `<span class="st ${esc(status)}">${esc(label)}</span>`;
```

```html
<span class="st todo">To do</span>  <span class="st running">Running</span>
<span class="st review">Review</span> <span class="st paused">Paused at limit</span>
<span class="st done">Done</span>   <span class="st failed">Failed</span>
```

States: todo (grey) · running (blue, pulsing) · review/paused (amber) · done (green) ·
failed (red). Map any domain status onto these five colours; the **word** carries the
detail ("Queued 14:00", "Waiting on you", "Needs review").

Tags label a category or a verdict, never live state:

```css
.tag{display:inline-flex;align-items:center;height:20px;padding:0 7px;border-radius:3px;background:var(--sunk);color:var(--ink-2);font-size:11px;font-weight:600;white-space:nowrap}
.tag.pass{background:var(--done-soft);color:var(--st-done)} .tag.fail{background:var(--fail-soft);color:var(--st-failed)}
.tag.warn{background:var(--rev-soft);color:var(--st-review)} .tag.run{background:var(--run-soft);color:var(--st-running)}
.tag.act{background:var(--act-soft);color:var(--act)}
```

`.tag.act` only for items whose kind *is* "you must act" (Close sprint, Land).

---

## Shell

Left nav (236px) + main column (sticky header, scrolling content). Overlay roots sit
outside the shell.

```html
<div class="shell">
  <aside class="nav" id="nav" aria-label="Navigation"></aside>
  <div class="navscrim" id="navscrim" data-act="nav-close" hidden></div>
  <div class="main">
    <header class="head" id="head"></header>
    <main class="content" id="content"></main>
  </div>
</div>
<div id="dock"></div>
<div id="overlay"></div>
<div id="toast" class="toast" role="status" hidden></div>
```

```css
.shell{display:grid;grid-template-columns:236px minmax(0,1fr);height:100%}
.nav{background:var(--nav);border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0;padding:12px 10px}
.brand{display:flex;align-items:center;gap:9px;padding:4px 8px 12px;font-weight:800;font-size:15px;letter-spacing:-.02em}
.brand .navclose{display:none;margin-left:auto}
.navscrim{display:none}
.cmdk{display:flex;align-items:center;gap:8px;width:100%;height:30px;padding:0 8px;border:1px solid var(--line-2);border-radius:var(--r);background:var(--panel);color:var(--ink-3);cursor:pointer;margin-bottom:12px;font-size:12.5px}
.cmdk kbd{margin-left:auto}
.navi{display:flex;align-items:center;gap:9px;width:100%;height:30px;padding:0 8px;border:0;border-radius:var(--r);background:transparent;color:var(--ink-2);font-weight:500;cursor:pointer;text-align:left}
.navi:hover{background:var(--hover);color:var(--ink)}
.navi[aria-current="page"]{background:var(--panel);color:var(--ink);font-weight:600;box-shadow:var(--shadow-1)}
.navi .count{margin-left:auto;min-width:18px;height:18px;padding:0 5px;border-radius:9px;background:var(--act);color:var(--act-ink);font-size:11px;font-weight:700;display:inline-flex;align-items:center;justify-content:center}
.navi .count.calm{background:var(--sunk);color:var(--ink-2)}
.nav-sect{display:flex;align-items:center;justify-content:space-between;padding:18px 8px 6px;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.navfoot{border-top:1px solid var(--line);padding:12px 6px 2px;display:flex;flex-direction:column;gap:10px}

.main{display:flex;flex-direction:column;min-width:0;min-height:0}
.head{display:flex;align-items:center;gap:12px;min-height:56px;padding:0 24px;border-bottom:1px solid var(--line);background:var(--panel);flex-wrap:wrap}
.head h1{font-size:15px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.head .sub{color:var(--ink-3);font-size:12.5px;white-space:nowrap}
.head .acts{margin-left:auto;display:flex;gap:8px;align-items:center;padding:10px 0;flex-wrap:wrap}
.head .tabs{flex-basis:calc(100% + 48px);margin:0 -24px;padding:0 18px;border-top:1px solid var(--line)}
.burger{display:none}
.content{flex:1;overflow:auto;min-height:0;padding:20px 24px 120px}
```

Nav markup:

```html
<div class="brand">LOGO<span>Runway</span>
  <button class="btn quiet icon navclose" data-act="nav-close" aria-label="Close menu">×</button></div>
<button class="cmdk" data-act="palette"><svg class="i">…</svg><span>Search or jump to…</span><kbd>⌘K</kbd></button>
<button class="navi" data-v="inbox" aria-current="page"><svg class="i">…</svg>Inbox<span class="count">3</span></button>
<button class="navi" data-v="runs" aria-current="false"><svg class="i">…</svg>Runs<span class="count calm">2</span></button>
<div class="nav-sect"><span>Projects</span><button aria-label="New project">+</button></div>
<div class="projlist">…</div>
<div class="navfoot">usage gauges · connection dot · Settings</div>
```

Header markup (title + sub-line count on the left, view actions on the right):

```html
<button class="btn quiet icon burger" data-act="nav" aria-label="Menu">≡</button>
<div class="crumb"><h1>Inbox</h1><span class="sub">3 need you</span></div>
<div class="acts"><button class="btn">Refresh</button></div>
```

States: current item (`aria-current="page"`, raised panel) · count badge orange when it
needs the user (Inbox), `.calm` grey when informational (Runs) · no badge at zero ·
disabled nav action when its capability is missing · connection dot in the foot
(`.dot.on` green / `.stale` amber / grey). Under 900px the nav becomes an overlay, see
[interaction.md](interaction.md#responsive-nav-overlay).

---

## Triage list

A queue of things that need a person. One row per item; the focused row gets an orange
left edge; row actions appear on hover/focus; keyboard drives everything.

```css
.list{background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg);overflow:hidden}
.list-h{display:flex;align-items:center;gap:10px;padding:8px 14px;border-bottom:1px solid var(--line);background:var(--sunk);font-size:12px;color:var(--ink-2);min-height:40px;flex-wrap:wrap}
.list-h .acts{margin-left:auto;display:flex;gap:6px}
.item{display:grid;grid-template-columns:20px 84px minmax(0,1fr) auto;gap:12px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line);cursor:pointer;position:relative}
.item:last-child{border-bottom:0}
.item:hover{background:var(--hover)}
.item.focus{background:var(--hover);box-shadow:inset 2px 0 0 var(--act)}
.item .t{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.item .m{display:flex;gap:10px;align-items:center;color:var(--ink-3);font-size:12px;margin-top:2px;flex-wrap:wrap}
.item .a{display:flex;gap:6px;opacity:.0;transition:opacity .12s}
.item:hover .a,.item.focus .a{opacity:1}
.item .a.show{opacity:1}
.progress{display:flex;gap:10px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line);background:var(--run-soft);font-size:12.5px}
```

```html
<div class="list">
  <div class="list-h">
    <input type="checkbox" class="chk" aria-label="Select all reviews">
    <span><b>2</b> selected</span>
    <span class="acts">
      <span class="faint"><kbd>j</kbd> <kbd>k</kbd> move · <kbd>x</kbd> select · <kbd>m</kbd> merge · <kbd>⏎</kbd> open</span>
      <button class="btn sm primary">Merge 2</button></span>
  </div>
  <div class="item focus" data-k="0">
    <span><svg class="i">…</svg></span>
    <span class="kind"><span class="tag warn">Review</span></span>
    <div style="min-width:0">
      <div class="t">Draft the app-ui skill</div>
      <div class="m"><span>Runway</span><span class="tag pass">Checks pass</span><span class="num">212k tok</span><span>4m ago</span></div>
    </div>
    <div class="a"><button class="btn sm">Merge</button><button class="btn sm quiet">Discard</button></div>
  </div>
</div>
```

States:
- **focused** (`.focus`, keyboard position; scrolled into view with `block:"nearest"` after a key move)
- **hover** (row tint, actions fade in)
- **selected** (checkbox checked; header switches from "4 ready for review" to "**2** selected"; primary bulk button shows the count, disabled at 0)
- **urgent row** (`.a.show`: actions always visible when the row's whole point is one action, e.g. "Close sprint")
- **in progress** (a `.progress` strip with `.spin` at the top of the list: "Merging *X* · 2 done…")
- **outcome** (a dismissible `.note` at the top after a batch: "3 merged, 1 couldn't merge (still in Review with the reason)")
- **empty / loading / error**: see [States](#states). Empty inbox reads "Nothing needs you" with a next-step button.

---

## Tabbed side panel

Opens one record over the right edge, list still visible behind. Header: status dot +
breadcrumb + close; editable title; tabs. Body scrolls. Footer holds actions with the
primary on the far right.

```css
.scrim{position:fixed;inset:0;background:rgba(10,12,16,.38);z-index:40;display:flex;justify-content:flex-end;animation:fade .12s ease-out}
@keyframes fade{from{opacity:0}}
.drawer{width:min(620px,100%);height:100%;background:var(--panel);display:flex;flex-direction:column;box-shadow:var(--shadow-2);animation:slide .16s ease-out}
@keyframes slide{from{transform:translateX(24px);opacity:.6}}
.drawer .dh{padding:14px 20px 0;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:10px}
.drawer .dh .r1{display:flex;align-items:center;gap:10px;font-size:12px;color:var(--ink-3)}
.drawer .dh .r1 .btn{margin-left:auto}
.drawer .ttl{border:0;padding:0;height:auto;font-size:18px;font-weight:700;letter-spacing:-.01em;background:transparent;box-shadow:none!important}
.drawer .tabs{margin:0 -20px;padding:0 14px;border-bottom:0;background:transparent}
.drawer .db{flex:1;overflow:auto;padding:18px 20px;display:flex;flex-direction:column;gap:14px}
.drawer .df{padding:12px 20px calc(12px + env(safe-area-inset-bottom,0px));border-top:1px solid var(--line);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.drawer .df .sp{flex:1}
.tabs{display:flex;gap:4px;overflow-x:auto}
.tab{height:40px;padding:0 10px;border:0;background:transparent;color:var(--ink-2);font-weight:500;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;display:flex;align-items:center;gap:8px}
.tab:hover{color:var(--ink)}
.tab[aria-selected="true"]{color:var(--ink);font-weight:600;border-bottom-color:var(--ink)}
.kv{display:grid;grid-template-columns:120px 1fr;gap:6px 12px;font-size:12.5px}
.kv dt{color:var(--ink-3)} .kv dd{margin:0}
```

```html
<div class="scrim" data-act="close-scrim">
  <aside class="drawer" role="dialog" aria-label="Task">
    <div class="dh">
      <div class="r1"><span class="st review">Review</span><span>·</span><span>Runway / PI 2 / Sprint 1</span>
        <button class="btn quiet icon" data-act="close" aria-label="Close">×</button></div>
      <input type="text" class="ttl" value="Draft the app-ui skill" aria-label="Title">
      <div class="tabs" role="tablist">
        <button class="tab" role="tab" aria-selected="false">Brief</button>
        <button class="tab" role="tab" aria-selected="true">Run</button>
        <button class="tab" role="tab" aria-selected="false">Changes</button>
      </div>
    </div>
    <div class="db"><dl class="kv"><dt>Status</dt><dd><span class="st review">Review</span></dd>…</dl></div>
    <div class="df"><button class="btn quiet">Split with Claude</button><span class="sp"></span>
      <button class="btn">Discard</button><button class="btn primary">Merge</button></div>
  </aside>
</div>
```

States:
- **default tab chosen by record state**: records that need a decision open on the tab that shows it (Review/Failed → Run), others on Brief.
- **tab switch** saves the form quietly (only changed fields) before moving.
- **tab empty**: a small `.zero` with `padding:40px` ("Not run yet · Select it on the board and launch a chunk.").
- **tab loading**: `.zero` with an inline `.spin` and "Loading changes…".
- **tab error**: `.note` with the reason, plus whatever fallback data exists.
- **form error**: `.note.bad` at the bottom of the body.
- **footer** primary changes with state (Save on a plain record, Merge on a reviewable one, Resume on a paused one); only one is orange.

---

## Floating action dock

Appears when something is selected; summarises the selection and holds the action.
Inverted (ink background) so it reads as a layer above the page.

```css
.dock{position:fixed;left:calc(236px + (100% - 236px)/2);transform:translateX(-50%);bottom:calc(20px + env(safe-area-inset-bottom,0px));z-index:15;background:var(--ink);color:var(--canvas);border-radius:10px;box-shadow:var(--shadow-2);padding:8px 8px 8px 16px;display:flex;align-items:center;gap:16px;max-width:calc(100% - 236px - 32px);flex-wrap:wrap}
.dock .sum{font-weight:600;white-space:nowrap}
.dock .sum span{font-weight:400;opacity:.7}
.dock .mini{width:140px}
.dock .mini .gbar{background:rgba(255,255,255,.18);margin:0}
.dock .mini .gbar i.est{background:var(--act)}
.dock .mini .lbl{font-size:11px;opacity:.7;display:flex;justify-content:space-between}
.dock .btn{box-shadow:none}
.dock .btn.quiet{color:var(--canvas)} .dock .btn.quiet:hover{background:rgba(255,255,255,.12)}
.dock .btn:not(.primary):not(.quiet){background:transparent;border-color:rgba(255,255,255,.25);color:var(--canvas)}
.dock .warn{flex-basis:100%;font-size:12px;color:#FFB38A;padding-bottom:2px}
.dock .warn:empty{display:none}
.gbar{height:4px;border-radius:2px;background:var(--line);position:relative;overflow:hidden;margin-top:5px}
.gbar i{position:absolute;inset:0 auto 0 0;background:var(--ink-2);border-radius:2px}
.gbar.over i:first-child{background:var(--st-failed)}
```

```html
<div class="dock" role="region" aria-label="Next chunk">
  <div class="sum">3 tasks <span>· 2 waves</span></div>
  <div class="mini"><div class="lbl"><span>Chunk</span><span class="num">420k / 500k</span></div>
    <div class="gbar"><i class="est" style="width:84%"></i></div></div>
  <button class="btn sm quiet">Clear</button>
  <button class="btn sm" disabled title="Needs the scheduler on your Mac">Queue</button>
  <button class="btn sm primary">Launch 3</button>
  <div class="warn">Over the chunk budget by 20k.</div>
</div>
```

States: **hidden** when nothing is selected or on a view where the action doesn't
apply (render `""`, don't just hide) · **normal** · **warning** (`.warn` line wraps
under; empty `.warn` collapses) · **over budget** (`.gbar.over`) · **blocked**
(primary disabled with a `title` and a warning saying what to fix) · **mobile**
(centred, `max-width:calc(100% - 24px)`). Content needs ≥120px bottom padding so
the dock never covers the last row. `Esc` clears the selection.

In tables, the same idea is a sticky **bulk bar** at the top of the table:

```css
.bulk{position:sticky;top:0;z-index:4;display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 12px;margin-bottom:10px;background:var(--ink);color:var(--canvas);border-radius:var(--r-lg)}
.bulk .btn{box-shadow:none;background:transparent;color:var(--canvas);border-color:rgba(255,255,255,.3)}
.bulk .btn.primary{background:var(--act);border-color:var(--act);color:var(--act-ink)}
```

---

## Expandable table

Parent rows toggle child rows. Active parents start open; finished ones start closed;
the user's toggle wins after that. Long history goes in a `<details>` below.

```css
.table{width:100%;border-collapse:separate;border-spacing:0;background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg);overflow:hidden}
.table th{font-size:11.5px;font-weight:600;color:var(--ink-3);text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);background:var(--sunk);white-space:nowrap}
.table td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:middle}
.table tr:last-child td{border-bottom:0}
.table tr.chunk{cursor:pointer}
.table tr.chunk:hover td{background:var(--hover)}
.table tr.sub td{background:var(--sunk);padding-top:8px;padding-bottom:8px}
.table tr.sub td:first-child{padding-left:36px}
.table .r{text-align:right}
.caret{display:inline-block;width:12px;color:var(--ink-3);transition:transform .12s}
.open .caret{transform:rotate(90deg)}
.tbl-wrap{overflow-x:auto}
.sect{font-size:12px;font-weight:600;color:var(--ink-2);margin:26px 0 10px;display:flex;align-items:center;gap:8px}
details.hist > summary{cursor:pointer;list-style:none}
details.hist > summary::-webkit-details-marker{display:none}
```

```html
<div class="tbl-wrap"><table class="table">
  <thead><tr><th style="width:150px">Status</th><th>Chunk</th><th class="r">Tasks</th><th class="r">Est → actual</th><th class="r">Cost</th><th>Started</th><th></th></tr></thead>
  <tbody>
    <tr class="chunk open" data-act="chunk-toggle">
      <td><span class="st running">Running</span></td>
      <td><span class="caret">▸</span> <b>Sprint 1 · wave 1</b></td>
      <td class="r num">1/3</td><td class="r num">480k → 212k</td><td class="r num">$1.84</td>
      <td class="faint">12m ago</td><td class="r"><button class="btn sm danger">Stop</button></td></tr>
    <tr class="sub" data-act="open-task">
      <td><span class="st running">Running</span></td>
      <td>Draft the app-ui skill<div class="faint" style="font-size:11.5px">Editing files · 14 turns</div></td>
      <td class="r faint">opus</td><td class="r num">160k → 98k…</td><td class="r num">–</td><td class="faint">6 min so far</td><td></td></tr>
  </tbody>
</table></div>
<details class="hist"><summary class="sect">History · 12 finished</summary>…same table…</details>
```

States: **collapsed** / **open** (caret rotates 90°) · **row hover** · **child live
progress** (faint sub-line under the title) · **child error** (first line of the error in
`--st-failed` under the title) · **missing value** shows `–`, never blank or 0 ·
**row actions** right-aligned, `danger` for destructive ones · **no rows**: the table is
replaced by a `.list > .zero` ("No agents running") · filtered to nothing: one full-width
row `<td colspan="N"><div class="zero" style="padding:28px">No tasks match.</div></td>`.
Clicking a child opens the side panel; clicking the parent toggles.

Selected rows in a pickable table: `.table tr.trow.picked td{background:var(--act-soft)}`.

---

## Command palette

`⌘K` from anywhere. Centred, input on top, grouped results, key hints in the footer.

```css
.scrim.center{justify-content:center;align-items:flex-start;padding:8vh 16px;overflow:auto}
.pal{width:min(620px,100%);background:var(--panel);border-radius:var(--r-lg);box-shadow:var(--shadow-2);overflow:hidden;animation:pop .12s ease-out}
@keyframes pop{from{transform:translateY(6px);opacity:.6}}
.pal input{height:48px;border:0;border-bottom:1px solid var(--line);border-radius:0;font-size:15px;padding:0 16px;box-shadow:none!important}
.pal ul{list-style:none;margin:0;padding:6px;max-height:50vh;overflow:auto}
.pal li{display:flex;align-items:center;gap:10px;height:36px;padding:0 10px;border-radius:var(--r);cursor:pointer;color:var(--ink-2)}
.pal li .g{margin-left:auto;font-size:11.5px;color:var(--ink-3)}
.pal li[aria-selected="true"]{background:var(--hover);color:var(--ink)}
.pal .ft{display:flex;gap:14px;padding:8px 14px;border-top:1px solid var(--line);font-size:11.5px;color:var(--ink-3)}
```

```html
<div class="scrim center" data-act="close-scrim">
  <div class="pal" role="dialog" aria-label="Command palette">
    <input type="search" id="pal-q" placeholder="Type a command, project or task…" autocomplete="off">
    <ul role="listbox">
      <li role="option" aria-selected="true">Go to Inbox<span class="g"><kbd>g i</kbd> Command</span></li>
      <li role="option" aria-selected="false">Runway<span class="g">Project</span></li>
    </ul>
    <div class="ft"><span><kbd>↑</kbd> <kbd>↓</kbd> move</span><span><kbd>⏎</kbd> run</span><span><kbd>esc</kbd> close</span></div>
  </div>
</div>
```

States: **open** (input focused, first item selected) · **filtering** (only the list is
re-rendered so the input keeps focus and caret; selection resets to 0) · **no matches**
(`<li>No matches</li>`) · **run** (close first, then run the item). Items: commands
(with their shortcut shown as `<kbd>`), then entities (projects), then records matching
the query (max 8). Cap at 14 rows.

---

## Modal, menu, toast

```css
.modal{width:min(720px,100%);background:var(--panel);border-radius:var(--r-lg);box-shadow:var(--shadow-2);display:flex;flex-direction:column;animation:pop .14s ease-out}
.modal > *{padding-inline:20px}
.modal .mhead{display:flex;justify-content:space-between;align-items:center;gap:12px;padding-block:16px 4px}
.modal h2{font-size:16px;font-weight:700}
.modal .row-end{padding-block:12px 16px;border-top:1px solid var(--line);margin-top:4px}
.row-end{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap;align-items:center}
.menu{position:absolute;right:0;top:calc(100% + 6px);z-index:30;min-width:220px;background:var(--panel);border:1px solid var(--line-2);border-radius:var(--r-lg);box-shadow:var(--shadow-2);padding:4px}
.menu button{display:flex;align-items:center;gap:8px;width:100%;height:30px;padding:0 10px;border:0;background:transparent;border-radius:var(--r);cursor:pointer;text-align:left;color:var(--ink)}
.menu button:hover{background:var(--hover)}
.menu hr{border:0;border-top:1px solid var(--line);margin:4px 0}
.toast{position:fixed;right:20px;bottom:calc(20px + env(safe-area-inset-bottom,0px));z-index:60;background:var(--ink);color:var(--canvas);padding:10px 14px;border-radius:var(--r-lg);font-weight:500;font-size:12.5px;max-width:min(420px,calc(100% - 40px));box-shadow:var(--shadow-2)}
```

```html
<div class="scrim center" data-act="close-scrim"><div class="modal" role="dialog">
  <div class="mhead"><h2>New project</h2><button class="btn quiet sm" data-act="close">Close</button></div>
  <div class="mbody">…fields…</div>
  <div class="row-end"><button class="btn" data-act="close">Cancel</button><button class="btn primary">Create</button></div>
</div></div>
```

```js
function toast(msg){ const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => t.hidden = true, 3600); }
```

Modal states: idle · working (a `.note.act` explaining what's happening and how long,
a `.stream` of live output, and a **Stop** button instead of Cancel) · error (`.note.bad`).
Confirmations name the consequence in the body and put the destructive verb on the button.
Toasts confirm something that already happened ("Fix applied. Running it again…"); they
are never the only place an error appears. z-order: dock 15 < menu 30 < scrim 40 < nav
overlay 45/50 < toast 60.

---

## States

Every view and every async panel needs all three. Write them first, not last.

```css
.zero{padding:72px 20px;text-align:center;color:var(--ink-2)}
.zero h2{font-size:16px;color:var(--ink);margin-bottom:6px}
.zero svg{width:40px;height:40px;color:var(--ink-3);margin-bottom:12px}
.spin{width:14px;height:14px;border-radius:50%;border:2px solid var(--st-running);border-right-color:transparent;animation:rot .8s linear infinite;flex:none}
@keyframes rot{to{transform:rotate(360deg)}}
.banner{margin:0 0 16px;padding:10px 12px;border-radius:var(--r);background:var(--rev-soft);color:var(--ink);border:1px solid var(--line);font-size:12.5px;display:flex;gap:10px;align-items:center}
.note{font-size:12.5px;color:var(--ink-2);background:var(--sunk);padding:10px 12px;border-radius:var(--r);border:1px solid var(--line)}
.note.bad{background:var(--fail-soft);color:var(--st-failed);border-color:transparent}
.note.ok{background:var(--done-soft);color:var(--st-done);border-color:transparent}
.note.act{background:var(--act-soft);color:var(--ink);border-color:transparent}
```

**Empty**: icon, a heading that states the situation, one sentence on what fills it,
and a button to the next step (primary only if it's *the* next step).

```html
<div class="list"><div class="zero"><svg class="i">…</svg><h2>Nothing needs you</h2>
  <p>Pick tasks on the board and launch them. Finished work shows up here for review.</p>
  <div class="row-end" style="justify-content:center;margin-top:14px"><button class="btn primary">Open the board</button></div>
</div></div>
```

Distinguish "nothing yet" from "nothing matches the filter" ("No tasks match.") and
from "nothing because a prerequisite is missing" (say which, offer the fix).

**Loading**: the header renders immediately (title, actions); only the body waits.

```html
<div class="zero">Loading…</div>                                  <!-- whole view -->
<div class="zero" style="padding:40px"><span class="spin" style="display:inline-block"></span><p>Loading changes…</p></div>  <!-- panel -->
<div class="progress"><span class="spin"></span><span>Merging <b>X</b> · 2 done. This can take a few minutes.</span></div>   <!-- long job in a list -->
```

Long jobs say what's happening and roughly how long, and offer Stop when they can.

**Error**: never silent, never only a toast. A view-level failure is a banner above the
content with the cause and the fix; a panel-level failure is a `.note` / `.note.bad`
inline, followed by any fallback data. Degraded mode (example data, missing capability)
also gets a banner saying so.

```html
<div class="banner"><svg class="i">…</svg><span>Live updates stopped: loading tasks failed (permission).</span>
  <span style="margin-left:auto;display:flex;gap:6px"><button class="btn sm primary">Reconnect</button><button class="btn sm">Reload page</button></span></div>
<div class="note bad">Couldn't save: the title is empty.</div>
```
