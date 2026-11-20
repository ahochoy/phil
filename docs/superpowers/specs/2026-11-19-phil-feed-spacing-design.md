# Phil feed: spacing and diffs

Date: 2026-11-19. Status: draft for review.

## Why

Phil's feed is hard to scan. Every line has the same weight, blocks sit flush against each other,
and the most useful parts get cut off: a design option's trade-offs end in `…` at the window
width. Code changes show up only as `edited a, b` or `+38 −0`, and the diff itself sits behind
`/more`. The user compared Phil with Claude Code and Antigravity and wants the run to feel
transparent: you should see what Phil did and what changed, at a glance.

This is the first of two pieces. This one covers spacing, hierarchy and diffs. The second covers
milestones: decisions, rulings and insights that agents report at a pause, such as the end of a
step or a plan waiting for the user. It comes later and needs agent and contract changes.

## Goals

- Blank lines between blocks, and detail indented under its heading, so a run scans like
  mockup A ("Just air").
- Nothing cut off that matters: design option summaries, trade-offs and the recommendation reason
  wrap at the indent instead of ending in `…`.
- The answer to a question stands on its own line, not blended in as `Answer: …`.
- What each burst of work changed, shown as a short diff under its summary line.
- The same look in all three places: the full-screen frame, the inline chat and `phil attach`.

## Non-goals

- New milestone kinds (decisions, rulings, insights). That is the second piece.
- Marking trade-offs as gains or costs (`+` / `−`). The design agent returns trade-offs as plain
  sentences, so all of them use one neutral dash. Marking them needs a contract change, which goes
  with milestones.
- One line per tool call. Finished bursts stay folded into one summary line, as the October feed
  design chose (spec 2026-10-07, 2026-10-16).
- File line numbers on diffs. The recorded diff covers only the edited snippet.

## Anti-goals

- A feed that gets long. Spacing adds air, not bulk. A burst that edits many files must not flood
  the screen.
- Two sets of spacing rules that drift apart between the frame and the inline chat.

## Approaches

1. **One shared set of feed layout rules (chosen).** `FeedRenderer` (`ui/feed_view.py`) and the
   controller's own printed blocks (design options, the settled answer, milestone bands) take
   their spacing, indent, wrapping and diff lines from one module. Every screen gets the same
   output, and the tests stay with the existing feed tests. Cost: the frame's folded task blocks
   must drop blank lines and diffs when they fold.
2. **Spacing styled per screen.** The frame uses Textual margins and padding; the inline chat
   prints its own blank lines. Finer control in the frame, but the rules are kept twice and
   `phil attach` needs a third copy.
3. **Structured feed items first.** The renderer returns typed items (heading, detail, diff) and
   each screen lays them out. The cleanest base for milestones, but it's a refactor before any
   visible change, and the user chose to push that later.

For diffs, two options were weighed:

- **X. Keep the burst summary line and put diffs under it (chosen).**
- **Y. One line per tool call, with diffs under the edits.** Rejected because it undoes the
  burst folding and makes long tasks long.

## The design

### Spacing and hierarchy

- **Blocks.** One blank line before each block. Lines inside a block have no blank lines between
  them. No two blank lines in a row, and none at the very top of the conversation. (Design call.)
  The rule is that everything the chat prints at the top level starts a block, and only a task's
  own working lines are inside one:
  - **Blocks:** the user's echoed line, each design option, the approaches heading and the
    recommendation reason, the settled answer, status lines ("Planning…", "Quick change…"),
    notices and warnings, callouts, replies, every milestone band (task started, gate, verdict,
    attempt failed, task done, rule saved), and a burst summary with its diffs.
  - **Inside the block before them:** engine gate lines and in-burst failure lines sit right under
    the task band or the previous line, with no blank line.
  - **Owner.** One small writer holds the "last line was blank" state for each screen, and the
    controller's top-level prints go through it (for example a `block(*renderables)` helper on
    the chat's console side). `FeedRenderer` marks which of its items start a block; it doesn't
    print blank lines itself.
- **Headings and detail.** Headings (option names, task bands, burst summaries) keep their current
  bright styles. Detail is indented 4 spaces (`INDENT`) under its heading and stays muted.
- **Wrapping.** Option summaries, trade-offs and the recommendation reason wrap at the indent with
  a hanging indent, and are no longer `_clip`ped. (Design call.) They are wrapped into plain
  `Text` lines at the width the caller has (`console.width`, or the pane width in the frame), not
  printed as `Padding`. That keeps them `Text`, which the frame needs for selection. A trade-off's
  continuation lines line up under its text, after the `– `. Tool lines and burst summaries keep cutting to one line with `…`;
  diffs show the change in full.
- **The settled answer.** A question's settled line becomes `? <asked>  <answer>`: `<asked>` in
  the existing question style, the answer bright. Other decisions keep today's `✓` / `✗` / `·`
  settled lines and get the block spacing. (Design call.) `Decision` gets a short `asked` text,
  and `settled_line` returns styled `Text` instead of `str`:
  - an intake question: the question text without its `Question n of m:` prefix
  - the approach pick: `Which approach?`
  - the open-ended pick (`Pick one, or type your answer`): `Your answer`
  - the chat picker: `Reopen which chat?`
  - The answer drops a trailing ` (recommended)`. A typed answer shows as typed (one line, through
    `one_line()`), and "Plan with what you know" shows as its label.

Target look, taken from mockup A:

```
Build-time script via an Astro integration hook
    Write a small local integration that hooks `astro:build:done`, walks the
    devlog collection plus a list of static routes, and writes one PNG per page
    into `dist/og/...`.
    – Keeps the OG logic in one standalone module, easier to unit-test
    – Loads the devlog collection a second way, outside Astro's content APIs

On-demand SSR endpoint via the Cloudflare adapter
    Renders the image at request time on Cloudflare.
    – First server-rendered route on a fully static site

? Which approach?  Astro image endpoints (prerendered routes)

Planning…

▸ Task 1 Add OG image endpoint

    implementer ran 9 tools · edited 2 files  (/more #12)

        src/pages/og/[...slug].png.ts  +38 −0
          + export const prerender = true;
          + export async function getStaticPaths() {
          … 30 more lines

        src/layouts/BaseLayout.astro  +3 −1
          − <meta property="og:image" content={image} />
          + <meta property="og:image" content={ogImage ?? `/og/${slug}.png`} />

✓ Task 1 green: tests pass
```

### Diffs

- **Where.** Under a burst's summary line, after a blank line, for each file the burst's
  successful `edit_file` / `write_file` calls touched, in first-touched order.
- **File header.** `<path>  +A −R`, indented 8 spaces. `+A −R` sums the burst's edits to that
  file, read from each record's `result` (`+3 −1` for an edit, `+N` for a write, which counts as
  `−0`). (Design call.)
- **Burst summary.** With diffs below it, the summary names the count (`edited 2 files`) instead
  of the file names, since the headers name them.
- **Lines.** Only added and removed lines: no `---`/`+++` headers, no `@@` hunks, no context
  lines. Added lines use `+` in a green style, removed lines use `−` in a red style, indented
  10 spaces. (Design call.) Parsing:
  - An `edit_file` detail is `difflib.unified_diff` output. Skip its first two lines (the
    `--- before` / `+++ after` headers) and every `@@` line. After that, a line's first character
    alone decides: `+` added, `-` removed, space context (dropped). So a removed `-- x` is read
    correctly.
  - A `write_file` detail is raw content. Every line is added, and nothing in it is parsed as
    diff syntax.
  - A detail cut at `MAX_DETAIL_CHARS` ends with `… (N more characters not shown)`. That line is
    dropped, and the file's lines end with `… more in /more #<seq>` (or `… more` without `/more`).
- **Caps.**
  - At most 8 diff lines per file, then `… N more lines`.
  - At most 3 files per burst, then `+N more files · /more #<n>`.
- **Long lines** are cut to the width with `…`. A diff line never wraps.
- **Safety.** Diff text is agent and repo content: every line goes through
  `printable(text.expandtabs(4).rstrip())`, so escape codes and hidden characters show as `?`.
  It keeps a line's leading spaces (tabs become spaces), because the indentation is what makes
  a diff readable; `one_line()` would collapse it.
- **Source.** The diff text is each edit's recorded detail file (`ActivityLog.detail_path(seq)`,
  `store/activity.py`). `FeedRenderer` has no file access today, so it gets a detail reader
  (for example a `details: Callable[[int], str | None]` passed in by the chat and by `attach`).
  A missing or unreadable detail drops that file's lines and keeps its header.
- **In-burst failures** keep printing at once, as today. Diffs come only with the burst's summary.
- **`more_hint=False`** (`phil attach`, which has no `/more`) shows `+N more files` without
  `/more #<n>`.

### New styles

Two theme styles for diff lines, such as `phil.diff.add` and `phil.diff.del`, in the theme that
holds `phil.muted`. They must read in light and dark terminals; for example green and red
foregrounds, not backgrounds.

### Screens

- **Inline chat and `phil attach`.** Blank lines and diffs print to scrollback.
- **Feed items.** (Design call.) A burst becomes several `FeedItem`s in order: the summary line
  (marked as starting a block), then one item per file header and per diff line. Every one of them
  carries the burst's records, so `/feed <agent>` filtering keeps or drops the whole burst
  together. `FeedItem` gains a `starts_block: bool` (default False).
- **Full-screen frame.** Blank lines are empty items, or a top margin on the item, whichever keeps
  drag-select copying clean. A task block that folds to one line drops its blank lines and diffs
  along with the rest of its lines. Unfolding it again, if that happens, shows them again.
- **Width.** Everything is laid out at the pane or console width the renderer already gets. A
  resize doesn't need to reflow lines that are already printed in the inline chat.

## Constraints

- Python, Rich 13+, Textual `>=8.2.8,<8.3`. No new dependencies.
- Agent text reaches the screen only through `one_line()` / `printable()`.
- Existing tests that pin exact feed output will change; update them to the new layout instead of
  keeping the old one.
- `/more #n` keeps working and shows the full detail.

## Left to the builder

- Where the shared layout rules live (a new module next to `feed_view.py`, or inside it), and how
  the controller's printed blocks call them.
- How a blank line is represented in the frame.
- Exact style names and colours, within the theme's existing conventions.
