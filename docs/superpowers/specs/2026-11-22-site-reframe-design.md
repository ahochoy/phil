# Site reframe: five sections, plain language (short design)

**Status:** approved by the user, 2026-11-22. Visual treatment (darker look, new branding) is a later, separate pass.

## Intent

The site's language overreaches: big claims, self-congratulation, and a lot of sections. Phil is
an experiment, and the site should say so plainly. The experiment: build a deterministic coding
agent on LangGraph that spends fewer tokens. Each request goes down the fixed workflow that
fits it, and the cheaper model does the work wherever that's enough. Along the way Phil tried
prompt caching, a classifier model for routing, sub-agents, context management and, most
recently, skills.

The φ is now a STEM symbol (maths, science), not the Greek *philia*. All philia wording goes,
on the site and in the product.

## Goals

- Five sections, in this order: hero, example run, how it works, five decisions, install.
- An honest, modest tone. Phil is "a coding agent experiment".
- Correct install instructions (Phil is on PyPI as `phil-agent` since 0.1.0).

## Non-goals

- No colour, type or layout restyle; that comes in the visual pass.
- No change to the hero's orbit clip or its player.
- No tagline.

## Anti-goals

- Claims a benchmark in this repo didn't measure, or numbers stated without what was measured.
- Words that sell rather than describe ("blazing", "revolutionary", "the best").
- An example run that reads as a real recording when it's illustrative.

## Approaches

1. **Rework the existing Astro components in `site/` (picked).** It keeps the stack, the
   Cloudflare deploy, the hero player and its tests, and the current look until the visual pass.
2. A new site from scratch. It would drop working pieces (hero player, deploy) for no gain,
   since the look is changing later anyway.

There was one sensible way; the user approved it with the design below.

## Design

1. **Hero.** The heading is "Phil", with "a coding agent experiment" under it. Two plain lines
   follow: built on LangGraph; each request goes down the fixed workflow that fits it; the
   cheaper model is used where that's enough; open source. Then a GitHub button and
   `uv tool install phil-agent` with a copy button. The orbit clip stays. The philia badge goes.
2. **Example run.** One full-path run, in the existing terminal frame, revealed line by line when
   it scrolls into view: the request; routed to the full path; one clarifying question and its
   answer; the high tier writes the plan; three tasks run in a worktree on the low tier; a
   reviewer checks them; the PR is ready, with the run's cost. Labelled illustrative and
   shortened. With reduced motion (or no JavaScript) every line shows at once.
3. **How it works.** A diagram: a request goes to the classifier, which picks one of three paths.
   - answer: a read-only snapshot, no branch or commits;
   - quick: one implementer and a review;
   - full: the intake conversation, design options (and mockups on request), the architect's
     plan, the critic, tasks in a worktree, the reviewer, and a branch ready for a PR.

   Then a "Built with" strip: Python, LangGraph, LangChain, DeepAgents, Textual, Rich, Typer,
   SQLite checkpoints.
4. **Five decisions,** one card each, saying what was chosen, why, and what we saw:
   1. a classifier picks the workflow (Jev, with the low model as fallback);
   2. two model tiers plus vision (high for judgement, low for doing the work, a vision model
      only when an image needs it);
   3. a fixed graph rather than a free-roaming agent (LangGraph steps that hand each other
      checkable contracts, so a run is predictable and resumable);
   4. context kept small (sub-agents return short answers, prompt caching, skills loaded only
      when they're needed);
   5. agent shells never see your keys.

   A card cites a number only when a benchmark or journal entry in this repo (`docs/journey/`,
   `docs/guide.md`) measured it, and says what was measured. The budget card is dropped.
5. **Install.** `uv tool install phil-agent` (or `pipx install phil-agent`), then `phil setup`,
   then `phil` in a git repository.
6. **Nav and footer.** Nav: How it works · Decisions · Install · GitHub. Footer: GitHub, the
   guide, and "About the dev" linking to the author's site, LinkedIn and GitHub.
7. **Removed:** the metrics strip, Origin (devlog Part 1), the devlog list and every devlog link,
   the About/schedule-a-chat card, the nav's "φιλία · dev agent", and the philia wording. The
   meta description is rewritten to match.

## Product wording (same PR)

Remove the philia wording from `src/phil/ui/mascot.py`'s docstring, the 0.1.0 line in
`CHANGELOG.md`, and the README's opening ("Named after *philia*…", and "Friendly" in the banner's
alt text). Earlier specs stay as they were written; they're history.

## Constraints

- The site builds (`npm run build` in `site/`) and its existing tests pass.
- Phil's own test suite still passes.

## Left to the builder

The exact copy within the rules above, how the line-by-line reveal is done, and whether the
diagram is HTML or inline SVG.
