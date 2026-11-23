# Site Reframe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework `site/` into five plain sections (hero, example run, how it works, five decisions, install), and remove the philia wording from the site and the product.

**Architecture:** Edit the existing Astro components in place, keeping today's look. A vitest test reads the built page (`site/dist/client/index.html`) and checks structure and wording. The example run's reveal timing is a small pure function with its own unit test.

**Tech Stack:** Astro 7, Tailwind 4, vitest 5, TypeScript; Phil's Python suite for the product wording.

**Spec:** `docs/superpowers/specs/2026-11-22-site-reframe-design.md`

## Global Constraints

- Phil is "a coding agent experiment". The hero heading is "Phil", with no tagline.
- No colour, type or layout restyle. Reuse the existing classes and palette. No change to the orbit clip or `hero-frames.ts`.
- A number appears only if `docs/guide.md` or `docs/journey/` measured it, and the sentence says what was measured. Never say "blazing", "revolutionary" or "the best".
- The example run is labelled illustrative and shortened.
- Install is `uv tool install phil-agent` (or `pipx install phil-agent`), then `phil setup`, then `phil`.
- No philia, φιλία, devlog or schedule-a-chat link anywhere on the page.
- Section ids: `run`, `how-it-works`, `decisions`, `install`. Nav: How it works · Decisions · Install · GitHub.
- Site check: `cd site && npm run build && npm test`. Phil check: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q -n auto -p no:cacheprovider`.

## Review Focus

1. A phone at 375 px wide: the diagram, the five cards and the terminal fit with no sideways page scroll.
2. No JavaScript, or reduced motion: every line of the example run is visible.
3. Every claim on the page is true of the code: keys stripped from agent shells (`src/phil/workspace/shell.py`), shell operators rejected, the three paths, a vision model used only for images, skills loaded on demand.
4. Scrolling the example run out of view and back: it plays once and doesn't restart or flicker.
5. A short laptop screen (height ≤ 760 px): the pinned hero still fits with the new copy.

Items 2 and 4 are pinned by tests in Task 2. Items 1, 3 and 5 are for the reviewer to check by hand: at a 375 px viewport, against the code, and at 1280×720.

---

### Task 1: Cuts, hero, install, nav and footer

**Files:**
- Delete: `site/src/components/HeroMetrics.astro`, `site/src/components/GenesisStory.astro`, `site/src/components/Benchmarks.astro`
- Modify: `site/src/pages/index.astro`, `site/src/config.ts`, `site/src/components/Hero.astro`, `site/src/components/QuickStart.astro`, `site/src/layouts/Layout.astro`
- Create: `site/src/page.test.ts`

**Interfaces:**
- Produces: `site/src/page.test.ts` exports nothing. It holds a `page` string, read once from `dist/client/index.html`, and a helper `count(selector: string): number` that counts elements matching a `[data-…]` attribute. Later tasks add `it(...)` blocks to this file.
- Produces: `SITE_CONFIG.links.guide = "https://github.com/ahochoy/phil/blob/main/docs/guide.md"`. Remove `metrics`, `devlog`, `devlogPart1`, `cal` and `author.devlog`. The `tagline` field goes.

- [ ] **Step 1: Write the failing test** in `site/src/page.test.ts`. It reads the built page; if the file is missing, it throws "run npm run build first".

```ts
it("says what Phil is, plainly", () => {
  expect(page).toMatch(/<h1[^>]*>\s*Phil\s*<\/h1>/);
  expect(page).toContain("a coding agent experiment");
  expect(page).toContain("uv tool install phil-agent");
  expect(page).toContain("pipx install phil-agent");
});
it("drops what was cut", () => {
  for (const gone of [/philia/i, /φιλία/, /devlog/i, /cal\.com/, /--editable/, /Schedule a chat/])
    expect(page).not.toMatch(gone);
});
it("links the nav and the about-the-dev footer", () => {
  for (const href of ['href="#how-it-works"', 'href="#decisions"', 'href="#install"'])
    expect(page).toContain(href);
  expect(page).toContain("About the dev");
  for (const url of ["andrewhochoy.com", "linkedin.com/in/andrewhochoy", "github.com/ahochoy\"", "docs/guide.md"])
    expect(page).toContain(url);
});
it("describes itself as an experiment", () => {
  expect(page).toMatch(/<meta name="description" content="[^"]*experiment[^"]*"/);
});
```

- [ ] **Step 2: Run it to see it fail.** Run `cd site && npm run build && npm test`. Expected: the four new tests FAIL (the old h1, philia and devlog are still on the page). `hero-math` passes.

- [ ] **Step 3: Make the cuts.**
  - Delete the three components and drop them from `index.astro`. The order becomes Hero, TerminalDemo, Architecture, DeepDives, QuickStart.
  - Hero: remove the philia badge and put "a coding agent experiment" in the eyebrow's place, styled as the old badge's small uppercase text, under the h1 "Phil". Replace the "How it started" button with "See a run ↓" (`#run`). The paragraph gets two plain sentences: Phil is built on LangGraph and sends each request down the fixed workflow that fits it; it uses the cheaper model wherever that's enough, and it's open source. Set `installCommand` to `uv tool install phil-agent`.
  - QuickStart: step 1 becomes `uv tool install phil-agent`, with "or `pipx install phil-agent`" in its note. The heading is "Install it and point it at a repo." Delete the whole About card.
  - Layout: the brand sub-line becomes "a coding agent experiment". The nav is How it works · Decisions · Install, plus the existing GitHub button. The footer keeps the GitHub link, adds "Guide ↗" (`links.guide`), and replaces the devlog and cal links with "About the dev:" and three links: andrewhochoy.com, LinkedIn, GitHub (@ahochoy).
  - Config `description`: "Phil is a coding agent experiment for the terminal, built on LangGraph: each request goes down the workflow that fits it, and the cheaper model does the work wherever that's enough."

- [ ] **Step 4: Run it to see it pass.** Run `cd site && npm run build && npm test`. Expected: all pass. `rg -n "HeroMetrics|GenesisStory|Benchmarks|devlog|metrics" site/src` prints nothing.

- [ ] **Step 5: Commit.** `git add -A site/src` and commit "Site: cut to five sections, plain hero and install".

### Task 2: The example run

**Files:**
- Modify: `site/src/components/TerminalDemo.astro`
- Create: `site/src/scripts/run-reveal.ts`, `site/src/scripts/run-reveal.test.ts`
- Test: `site/src/page.test.ts`

**Interfaces:**
- Produces: `lineDelays(count: number, stepMs: number): number[]` returns `[0, stepMs, 2*stepMs, …]` (length `count`; `[]` for 0 or less). And `startRunReveal(root: HTMLElement): void`. If `matchMedia("(prefers-reduced-motion: reduce)")` matches, it does nothing. Otherwise it adds `is-pending` to each `[data-run-line]`, and the first time the root intersects it removes `is-pending` from each line on `lineDelays(n, 450)`, then disconnects the observer.

- [ ] **Step 1: Write the failing tests.**

```ts
// run-reveal.test.ts
expect(lineDelays(3, 450)).toEqual([0, 450, 900]);
expect(lineDelays(0, 450)).toEqual([]);
expect(lineDelays(-1, 450)).toEqual([]);
// page.test.ts
it("plays one full-path run, visible without JavaScript", () => {
  expect(page).toContain('id="run"');
  expect(count("data-run-line")).toBeGreaterThanOrEqual(10);
  expect(page).not.toMatch(/data-run-line[^>]*(hidden|is-pending)/);
  expect(page).toMatch(/illustrative/i);
  expect(page).not.toContain('data-tab=');
});
```

- [ ] **Step 2: Run them to see them fail.** Run `cd site && npm run build && npm test`. Expected: FAIL (no `run-reveal` module, no `id="run"`).

- [ ] **Step 3: Rewrite TerminalDemo as one run.**
  - Delete the tabs and their script. Give the section `id="run"`, a short heading ("One run, start to finish"), and a caption: "An illustrative run, shortened. Real runs print more."
  - Each line carries `data-run-line`, in this order:
    1. the request: `add a --json flag to calc history`;
    2. the router: full path, Jev, confidence and latency;
    3. Phil's one clarifying question, with the user's picked answer;
    4. the design options and the pick;
    5. the architect (high tier) writing the plan as three tasks;
    6. the critic (high tier) approving it;
    7. the worktree and branch;
    8. task 1/3, task 2/3 and task 3/3, each by the implementer (low tier) with its tests passing;
    9. the reviewer: no open issues;
    10. "branch ready · phil pr …", with the run's cost.
  - Keep the existing terminal frame and status bar.
  - `.is-pending { opacity: 0 }`, with a short opacity transition.
  - The script calls `startRunReveal` on the section.

- [ ] **Step 4: Run them to see them pass.** Run `cd site && npm run build && npm test`. Expected: all pass.

- [ ] **Step 5: Commit.** "Site: the example run is one full-path run".

### Task 3: How it works

**Files:**
- Modify: `site/src/components/Architecture.astro`
- Test: `site/src/page.test.ts`

- [ ] **Step 1: Write the failing test.**

```ts
it("shows the three paths and what it's built with", () => {
  expect(page).toContain('id="how-it-works"');
  expect(count("data-path")).toBe(3);
  for (const p of ['data-path="answer"', 'data-path="quick"', 'data-path="full"']) expect(page).toContain(p);
  for (const t of ["Python", "LangGraph", "LangChain", "DeepAgents", "Textual", "Rich", "Typer", "SQLite"])
    expect(page).toContain(t);
});
```

- [ ] **Step 2: Run it to see it fail.** Expected: FAIL (no `how-it-works`).

- [ ] **Step 3: Rewrite Architecture.**
  - Heading: "How it works". Lead: a classifier reads each request and picks one of three fixed workflows, each a LangGraph graph.
  - The diagram is HTML: "request" then "classifier" (note: "Jev; your low model if Jev is unavailable"), then three columns or rows, each with `data-path`:
    - answer: "read-only snapshot · no branch, no commits";
    - quick: "one implementer · one review";
    - full: "intake conversation, design options (mockups on request), architect's plan, critic, tasks in a worktree, reviewer, branch ready for a PR".
  - It stacks vertically below `md`.
  - Then the "Built with" strip: Python 3.14, LangGraph, LangChain, DeepAgents, Textual, Rich, Typer, SQLite checkpoints.

- [ ] **Step 4: Run it to see it pass.** Expected: all pass.

- [ ] **Step 5: Commit.** "Site: how it works, as a diagram of the three paths".

### Task 4: Five decisions

**Files:**
- Modify: `site/src/components/DeepDives.astro`
- Test: `site/src/page.test.ts`

- [ ] **Step 1: Write the failing test.**

```ts
it("has five decisions and no budget card", () => {
  expect(page).toContain('id="decisions"');
  expect(count("data-decision")).toBe(5);
  expect(page).not.toContain("04 · PROCESS");
});
```

- [ ] **Step 2: Run it to see it fail.** Expected: FAIL (the old `id="design"`, four cards).

- [ ] **Step 3: Rewrite DeepDives.**
  - Section `id="decisions"`, heading "Five decisions". Lead: "The choices that shape the experiment, and what we saw."
  - Each card has `data-decision`, keeps the current card style, and says what was chosen, why, and "What we saw". Cite only these figures:
    1. **A classifier picks the workflow.** Sorting a request is classification, so a small decision model (TypeSafe Jev) does it, and the low model takes over if Jev is unavailable. Seen: in the 2026-10-06 benchmark, 50 cases with no wrong-path or unsafe routes, about 12× faster than the low model at p95 and about 4× cheaper.
    2. **Two tiers, plus vision.** High for judgement (architect, critic, reviewer), low for doing the work (implementer, tester). An optional vision model reads images only when an agent's own model can't. Any provider: OpenRouter, OpenAI, Anthropic, Google, Ollama, or an OpenAI-compatible endpoint.
    3. **A fixed graph, not a free-roaming agent.** Each workflow is a LangGraph graph whose steps hand the next a contract it can check. State is checkpointed in SQLite, so a stopped run resumes where it left off.
    4. **Context kept small.** Sub-agents return short answers, repeated history is prompt-cached, and skills load only when a stage needs them. Seen: with caching, 68% of the architect's input came from the cache and its cost fell from $0.38 to $0.21 (`docs/journey/04`).
    5. **Agent shells never see your keys.** API keys and tokens are removed from every command an agent runs, those commands can't reach the OS key store, and commands with shell operators are rejected.

- [ ] **Step 4: Run it to see it pass.** Expected: all pass.

- [ ] **Step 5: Commit.** "Site: five decisions, in plain words".

### Task 5: Product wording

**Files:**
- Modify: `src/phil/ui/mascot.py:1`, `CHANGELOG.md:15`, `README.md:2,7-10`

- [ ] **Step 1: Check the starting point.** Run `git grep -n -i "philia" -- src README.md CHANGELOG.md`. Expected: 3 lines.
- [ ] **Step 2: Reword.**
  - `mascot.py` docstring: "a lowercase φ".
  - `CHANGELOG.md`: "A welcome card with Phil's φ mascot, with a line under it instead of a box."
  - `README.md`: the opening becomes "Phil is a coding agent experiment for the terminal, built to spend fewer tokens.", followed by the rest of the existing paragraph from "Run it inside a git repository". The banner alt text becomes "Phil, a coding agent experiment".
- [ ] **Step 3: Verify.** The grep from Step 1 prints nothing. Run the Phil check from Global Constraints. Expected: all pass.
- [ ] **Step 4: Commit.** "Drop the philia wording from the product".
