# Phil mockups at the approach stage — design

Date: 2026-11-20 · Status: draft for the user's approval · Roadmap: "Visual proposals for UI work" (M6), called D in the 2026-11-19 sequence.

## 1. Intent

When a change has a visual side (a page section, a CTA, a layout), the user wants to see what Phil has in mind before building it, and to redirect it there. The prompt was a live run that added a CTA section with no shared picture of where it went, what it said or how it looked.

The approach stage is where the visual differences are largest, so that is where mockups live. They are **optional**: the user asks for them when they want them. Once an approach is picked and the plan is approved (`Approve? [y / edit / n]`), Phil builds without checking in again; the user confirmed this is the only confirm step needed.

Mockups are rough "this is what we're thinking" pictures, good enough to compare layout, placement and content. They are not final designs.

## 2. Goals, non-goals, anti-goals

**Goals**
- At the approach stage, the user can ask to see all approaches, or one, as a mockup.
- The mockup is an HTML page opened in the browser; with no browser, a sketch in the terminal.
- The user can ask for a redraw with a note, as often as they like, and still pick an approach as today.
- The chosen approach's mockup goes forward to the architect and implementers as a reference.

**Non-goals**
- No mockup after the approach is picked: not at plan approval, not during the run.
- No new confirm step during implementation.
- No screenshot of the real app, no running the project, no pixel fidelity.
- No full design agent or design-system extraction.

**Anti-goals**
- Spending anything when the user doesn't ask: no mockup is made unless requested.
- Misreading a described approach as a mockup request, or the reverse.
- A mockup page that loads anything from the network or runs script.

## 3. Approaches

Where the mockup appears (decided by the user):
1. One mockup of the chosen approach, after picking, before planning.
2. One mockup per approach, shown with the options.
3. A mockup beside the plan at `Approve?`.

**Chosen:** a mix of 1 and 2: optional, on request, at the approach stage, for all approaches or one. Nothing after the approach is picked, because by then the look is settled.

What form the mockup takes (decided by the user): a terminal sketch, a browser HTML mockup, or a screenshot of a throwaway build. **Chosen:** browser HTML, with the terminal sketch as fallback.

How Phil recognises a request (decided by the user):
- **A. Explicit:** a menu item and a `show` command. **Chosen**: no model call, no misreading.
- B. A small model call classifies every free-text reply at the approach stage. Rejected: a call per reply, and a misread starts planning the wrong thing.

## 4. Design

### 4.1 Asking for a mockup

At `choose_approach`:
- `show`: mockups of every approach.
- `show N`: approach N only (N as numbered on screen).
- Text after either is a note for the drawing: `show 2 put it above the footer, less copy`.
- Matching: the reply's first word is `show` (case-insensitive), followed by nothing, a number, or a number and a note. `show` followed by anything else (`show prices in red`) is the user's own approach, as today. *(Builder's call on the exact grammar within that rule.)*
- In full-screen mode, the approach menu gets an item "Show as mockups" (all approaches) next to "Describe your own". `show N` can still be typed.
- Everything else at this stage behaves as today: Enter / y / a number picks; other text is the user's own approach.

After a mockup is shown, the stage stays `choose_approach`, and the same menu and replies apply. Each `show` makes a fresh mockup; there is no cap, and each call's cost shows like any other.

### 4.2 The hint

The designer's `Approaches` contract gains `visual: bool`: true when the goal changes something people see (UI, pages, layout, styling, visual copy placement). When true, Phil prints one muted line under the approaches: `Say "show" to see these as mockups.` *(My call, with the wording left to the builder.)* The "Show as mockups" menu item appears only when `visual` is true; the typed `show` command always works.

### 4.3 Making the mockup

A new read-only agent, `mockup`, on the designer's model tier and harness (`tools=("shell",)`, light harness, `workdir` = the same base-commit snapshot the designer reads). It sees the agent stage `design`, so approved design-stage skills reach it through `load_skill` as they reach the designer.

Input (`MockupInput`): the goal, the approaches to draw (name, summary, trade-offs), the repo overview, the user's note if any, the conversation so far, the goal's images, and the format: `html` or `sketch`.

Output (`Mockup`): one entry per requested approach, each with the approach's name and either an HTML fragment (`html`) or a plain-text sketch of at most 40 lines × 100 columns (`sketch`), plus one optional sentence of notes. The agent is told to use the repo's own styles, colours, fonts and copy where it finds them, and to keep each fragment self-contained (inline CSS only, no scripts, no external URLs; images only as inline SVG or `data:` URIs).

Phil assembles the page: a fixed template with the goal as heading, the approaches side by side (stacked on narrow windows), each labelled with its number and name. The template carries a Content-Security-Policy meta tag: `default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:`. Phil also strips any `<script>` elements and `on*=` attributes from fragments before writing. The CSP is the guard; stripping is belt and braces.

The page is written to the project's folder under `~/.phil`, at `mockups/<chat id>-<n>.html`. *(Builder's call on the exact name; it must not collide across chats or redraws.)*

### 4.4 Showing it

- **Browser available:** Phil opens the page with Python's `webbrowser` and prints one line with the file path as a clickable link, so it can be reopened.
- **No browser:** the request asks for `sketch`, and Phil prints each approach's sketch in the feed under its number and name.
- "No browser" means: an SSH session (`SSH_CONNECTION` or `SSH_TTY` set), or Linux with neither `DISPLAY` nor `WAYLAND_DISPLAY`. This is decided **before** the call, so only one format is generated. If `webbrowser.open` then returns false, Phil prints the link and says the browser didn't open.
- While it is drawing, the status shows it the way other agent work shows.

### 4.5 Carrying the mockup forward

When the user picks an approach that has a mockup (the latest one drawn for it), its fragment or sketch goes into the architect's input and into the implementers' input as `mockup`, described as "a rough picture the user saw and accepted; match its layout, placement and content, not its exact pixels". It is clipped by the packet budget like other inputs. A user's own described approach carries no mockup.

*(My calls, from the builder check:)*
- **Latest per approach.** Phil keeps the latest mockup per approach. `show 2 …` replaces only approach 2's entry; `show` replaces all. Picking an approach counts as accepting its latest mockup. Mockups of other approaches are not carried.
- **Who gets it.** The architect, and every implementer task (full and quick paths), clipped by the packet budget. A fragment is typically a few KB, so roughly 1k tokens a task. Picking which tasks are "UI" isn't worth a new plan field.
- **Survives reopening.** Each approach's latest fragment or sketch is also written next to the page (`mockups/<chat id>-<n>-<approach>.html` or `.txt`). Saved chat state records only those paths. On restore, Phil re-reads them, and a missing file means no mockup, never an error.

### 4.6 While drawing *(my calls)*

- The mockup runs as a background job, like the designer, while the stage stays `choose_approach`.
- Picking an approach, describing one, or `/cancel` while it draws drops the job. A late result for an older job generation is discarded, and planning goes ahead without it.
- In full-screen mode, "Show as mockups" returns a sentinel through `_choose_approach` the same way the `show` text does. The approach menu reappears once the mockup is shown or has failed.
- `visual` defaults to false, so saved chats and a designer that omits it show no menu item and no hint. The designer prompt and the fake agent learn the field.
- Sketches wider than the terminal are clipped, not wrapped, so the layout stays readable.

### 4.7 Failures

- A mockup call fails or times out: one muted line ("Couldn't draw the mockup: …"), and the stage stays `choose_approach`.
- Writing the file fails: as above.
- An approach number out of range: the existing "Pick 1–N…" message, mentioning `show`.

## 5. Constraints

- Phil's thesis is cost efficiency: no tokens spent on mockups unless the user asks.
- The mockup agent is read-only. Phil writes the file, not the agent.
- The mockup page never fetches from the network or runs script (§4.3).
- Works in full-screen (Textual) and inline chat modes, on macOS, Linux and Windows.
- Tests use a temp `PHIL_HOME` and the fake agent; no live model calls in the suite.

## 6. Left to the builder

- The exact `show` grammar within §4.1's rule, the hint wording, and the file name.
- The page template's look (plain and readable; it frames the mockups, it isn't one).
- How the fragment is stored on the session so §4.5 can find the latest mockup per approach.
- The mockup agent's prompt, within §4.3.
