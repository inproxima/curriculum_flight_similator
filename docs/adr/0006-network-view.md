# ADR 0006: Network view of the Alignment Web

Status: accepted (2026-09-27). Scope: how the Web is drawn once connections are mapped.

## Context
After the first full connection mapping (ADR 0005), the BHSc 2026–27 draft held 426 elements and 1,311 links, 909
of them AI proposals. The column layout draws every link as an SVG path between fixed columns, so the Web became a
wall of crossing lines, and the question "what does this outcome connect to?" could not be answered by looking.

The user pointed at [meta-knowledge-graph](https://github.com/Seaual/meta-knowledge-graph) as a model. Its concept
graph (`frontend/src/pages/ConceptsGraph`) is drawn with [force-graph](https://github.com/vasturiano/force-graph),
a canvas renderer driven by d3-force. Its techniques:
- faint links, with nodes coloured and sized by category;
- labels only when zoomed in;
- category filters that **dim** rather than delete;
- hover tooltips, click to focus with a glow, and moving particles along links;
- a repulsion slider, `zoomToFit` once the layout settles, and drill-down with "back".

## Decisions
1. **A Network view, now the default; Columns kept.** A toggle above the canvas switches between them, and the
   choice is remembered. Both share everything else on the page:
   - the Layers panel, the Inspector, search, scenarios and simulation;
   - link review, the proposed-links queue and the outline import.
2. **Same library and approach as the model repo:** `force-graph` with d3-force (canvas).
   - Nodes are drawn by hand: a muted colour and a shape per type (◆ program outcome, ▭ course, ● course outcome,
     ■ assessment, ⬢ topic). Saturated colour is reserved for links, which show the connection level (ADR 0005).
   - Dash shows provenance: solid documented, long dash accepted, short dash an AI proposal.
   - Width shows strength.
   - Rings mark the mapping state: red for "none found", dashed grey for "not mapped".
   - Scenario changes get a status halo.
   - No code was copied; the ideas were.
3. **Layout has meaning.**
   - **By year** (default): each course is pulled to its year band (terms spread within it). Its members are held
     to it by the containment links, whether or not those links are drawn. Program outcomes form a fixed row
     above.
   - **Clusters:** the structure finds its own shape.
   - Alignment links are weak springs, so they shape the picture without collapsing it.
   - Positions persist across data refreshes: accepting a link does not re-run the layout, and only a change in
     the set of elements does.
4. **Layers never move anything; they brighten, fade or hide** ("dim, don't delete").
   - An element is **bright** when it passes the element layers.
   - A **link is drawn** when it passes the link layers and touches a bright element. Its other end is drawn as
     faint **context**.
   - Everything else **fades**, or is removed with *Hide the rest*.
   - Link opacity falls with the number of links drawn: `0.32·√(220/n)`, between 0.07 and 0.32.
5. **Views ("Show") by relationship, not by node type:** *Outcomes → program outcomes*, *Assessments → outcomes*,
   *Topics → outcomes*, *Earlier ↔ later*, *Everything*. Each sets the element and link layers together, so a view
   answers one question.
   - A combination made in the Layers panel shows as "custom".
   - Picking a view clears the selection and fits the view.
6. **Focus.**
   - **Hover** previews an element's neighbourhood and shows a tooltip.
   - **Clicking** (or picking from search) selects it. Its drawn links and their ends stay lit, with particles
     showing direction, and everything else drops to about 9% opacity.
   - Clicking another element switches straight to it. Clicking empty canvas, or pressing Escape, clears the
     selection. A click on the canvas also replaces a highlight (a finding, expected preparation, future use).
   - A course lights its members and their links. A program outcome also lights the courses of the outcomes that
     serve it.
   - An element with no drawn link (outside the chosen view) lights all of its links instead, so a selection never
     lights up nothing.
   - *Only the selection* hides the rest.
   - **The camera stays put for clicks on the canvas.** A selection or highlight made elsewhere (search, the
     Inspector, a finding) is brought into view. When the canvas resizes, its top-left stays fixed, so nothing moves
     under the pointer. (The change composer below it gains a line when something is selected.)
7. **Accessibility.** A canvas cannot be navigated by keyboard, so:
   - selection works through the search box, and the Inspector then lists every connection;
   - the Connections page and the Table give the same information as tables;
   - controls are real buttons with `aria-pressed`;
   - the status line (counts, what the selection lights up) is announced politely.
8. **The app does its own hit-testing** (`pickAt` in `networkModel.ts`). force-graph only draws, pans, zooms and
   drags. Its own hit-testing failed in four ways (measured on BHSc 2026–27, added after a user report):
   - It resolves hover and clicks from a hidden canvas repainted at most every 800 ms, so after a zoom or pan they
     landed where things had been.
   - It kept links clickable after they faded to 3%. After a selection, two-thirds of the "empty" canvas selected a
     hidden link instead of clearing.
   - With the view fitted, topics and assessments had a hit radius of about 1.5 px.
   - Any pointer movement during a press dropped the click.

   The rules now:
   - **Order:** inside a shape (the topmost), within 3 px of one (the nearest), the name of a program outcome or
     course, the nearest element within its reach, the nearest visible link within 4 px, else the background.
   - **Reach:** every lit element, or with nothing lit every element in the view, reaches at least 9 px from its
     centre plus 3 px of slack, a target of at least 24 px. Faded and unlit elements reach only their shape, so they
     cannot take clicks aimed at lit things.
   - **Links:** only visible links can be picked. While something is selected, only its lit links.
   - **Clicks:** a press that moves less than 6 px (10 px on touch) is a click on what was under it when pressed.
     More is a pan or a drag.
   - **Positions** are read live, so a click right after zooming lands on what is there now.

## Consequences
- **Scale.** The dense program reads as clusters with a texture of links. Questions are answered by hovering,
  clicking or choosing a view. The 1,112-element stress program renders on a canvas without SVG costs.
- **Bundle.** It grows by force-graph and d3-force; the main chunk was already over Vite's 500 kB warning.
- **Tests.**
  - End-to-end tests of the column layout switch to Columns first.
  - Network tests find nodes through a development-only hook on the canvas container (`__net`: positions,
    `pickAt`, the elements and the links). They click where an element is drawn, with no hovering first.
  - The tests cover:
    - every element type;
    - a click that wobbles;
    - switching straight to another element;
    - clearing on empty canvas, even where a link that is not lit runs;
    - the camera staying put;
    - a click right after a zoom;
    - a high-density screen.
