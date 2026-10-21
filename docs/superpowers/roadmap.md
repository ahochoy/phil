# Phil Roadmap

**Updated:** 2026-10-13. **Inputs:** the v1 design (`specs/2026-09-23-phil-v1-design.md`), the plan followups files, and the live-testing feedback (`../feedback/2026-09-29-live-testing-feedback.md`).

Phil is a development partner. It plans with you, does the work in its own worktree, shows what it is doing, and closes the loop through a PR and cleanup. The live tests showed that it works end to end, but it is disproportionate: a one-tag change took about 50 minutes and more than 3M tokens. The next milestones make it proportional, configurable, and visible.

Each milestone gets its own spec and plan when it starts. The notes below give direction, not detail.

## Done

| Plan | Delivered |
|---|---|
| 1 · 2 · 3a · 3b | Foundation, contracts and agents, the TDD run engine, the background worker and CLI |
| 4a · 4b · 4c | Chat planning, the live chat (toolbar, `/btw`, following a run), observability (usage and cost, `phil show`, budgets, timeouts) |
| 5 | Phil raises the PR, notices the merge, cleans up, and writes `learnings.md` |
| 6a (M1, merged) | Stop the waste: a live benchmark, safe read-only shell commands by default, evidence checks, `check`-mode tasks, current test-command detection, tighter prompts. [Plan](plans/2026-09-29-phil-06a-stop-the-waste.md) · [spec](specs/2026-09-29-phil-06a-stop-the-waste-design.md) · [benchmark results](../journey/01-stop-the-waste.md) |
| M2a | Layered config (built-in → `~/.phil` → repository → session), model tiers (high/low, per-role overrides), and providers (OpenRouter, Anthropic, OpenAI, Google, Ollama, custom endpoints). [Plan](plans/2026-09-30-phil-m2a-config-and-models.md) · [spec](specs/2026-09-30-phil-m2a-config-and-models-design.md) · [followups](plans/2026-09-30-phil-m2a-followups.md) |
| M2b | A guided `phil setup` (also started automatically from a bare `phil` when no models are configured), provider API keys stored in the OS keychain through `keyring` with environment variables taking precedence, and `phil keys set/list/remove`. [Plan](plans/2026-09-30-phil-m2b-setup-and-keys.md) · [spec](specs/2026-09-30-phil-m2b-setup-and-keys-design.md) · [followups](plans/2026-09-30-phil-m2b-followups.md) |

## Principles (from live testing)

- Simple tasks stay simple. Process depth and model power match the task, and they are routed separately.
- Configure common preferences once, globally. Advanced customization stays possible without dominating the defaults.
- Agent-to-agent communication is concise and structured. Prompts favor accuracy, repository conventions, and clear stopping conditions.
- Activity, errors, costs, and sub-agent work are visible. The terminal feels structured, not like a raw stream.
- Permissions are safe but not repetitive, and the approved scope is always explicit.
- Extensibility (skills, plugins, snippets, diagrams, memory) is part of Phil's long-term identity. Each piece gets its own design.

## Milestones

### M3 — Proportional orchestration

Split into two plans: M3a routes every message to a path; M3b makes the quick path actually
quick.

- **M3a — Routing (done, PR #11).** A classifier (TypeSafe's Jev, or the low model as a
  fallback) sorts each message into a task class (question, diagnosis, a small operation, a
  simple change, a focused fix, a feature, a refactor, design work, a broad project), which
  sets an answer/quick/full depth. `/ask`, `/quick` and `/full` force a path. A read-only
  answerer handles questions and diagnoses with no run. A classifier benchmark (spec §5.1)
  compares Jev against the low model on class/depth accuracy, latency, and cost, with a
  written decision rule for recommending Jev. Quick-depth goals still run the full pipeline
  until M3b lands. [Spec](specs/2026-09-30-phil-m3-proportional-orchestration-design.md) ·
  [followups](plans/2026-09-30-phil-m3a-followups.md).
- **M3b — Quick path (done, PR #12).** The quick depth gets its own lighter run: one task, no architect or
  critic round, no sub-agent or summarization, a lean one-pass review that patches findings
  directly instead of becoming new TDD tasks, and tighter retries, with escalation to a full
  run when the quick attempt doesn't clear the gate. [Plan](plans/2026-10-01-phil-m3b-quick-path.md) ·
  [followups](plans/2026-10-01-phil-m3b-followups.md).
- Designed from M1's benchmark numbers.
- **M3c — Ask before acting (planned).** Prompted by a live CTA run that asked nothing and built
  nothing (user request, 2026-10-03):
  - Intake's clarifying questions become multiple choice, asked one at a time.
  - Intake must ask about content only the user can supply (links, copy, placement, style), on every path.
  - On the full path, when the approach is open and no design exists, a designer proposes 2–3
    approaches for the user to pick from before the architect plans.
  - Then the Jev benchmark is revisited under a revised decision rule.
  [Spec](specs/2026-10-03-phil-ask-before-acting-design.md).

### M4 — Permissions (done)

Saved allow and deny rules, for one repo or everywhere, in policy files separate from config
(`~/.phil/policy.toml` and `~/.phil/projects/<slug>/policy.toml`). A rule's trailing `*` covers
plain arguments only. Built-in read-only subcommands run without asking, and every decision
carries a reason and a label (`reads only`, `changes things`, `may change things`), so `gh pr view`
is never confused with `gh pr merge`. The approval callout shows each command and the exact rule it
would save, for this run, this repo or everywhere. A repo's `phil.toml` shell settings apply only
once trusted. `phil permissions` lists, checks, adds and removes rules, and `/permissions` lists
them in the chat. [Plan](plans/2026-10-13-phil-permissions.md) ·
[spec](specs/2026-10-13-phil-permissions-design.md).

### M5 — Terminal experience and visibility

- **A live event stream from the worker (done).** Tool calls and results, file changes, retries,
  and failures, each tagged with the task and role, recorded to `activity.jsonl` and streamed to
  the chat and `phil attach`. [Plan](plans/2026-10-07-phil-activity-feed.md) ·
  [spec](specs/2026-10-07-phil-activity-feed-design.md).
- **An activity feed (done).** Conversation plus execution, with summaries first (compact tool
  lines under highlighted milestone bands), expandable details on demand (`/more #n` in the chat,
  `phil show <run> --step N` outside it), and a live row for the step in progress.
  [Plan](plans/2026-10-07-phil-activity-feed.md) ·
  [spec](specs/2026-10-07-phil-activity-feed-design.md).
- **A welcome banner, a status bar, and active agents in the live row with a feed filter (done).**
  The banner is a boxed card with a swappable mascot (`src/phil/ui/mascot.py`; the final mascot is
  still to be chosen). [Banner spec](specs/2026-10-08-phil-welcome-banner-design.md) ·
  [status bar spec](specs/2026-10-07-phil-status-bar-design.md) ·
  [agent bar spec](specs/2026-10-08-phil-agent-bar-design.md).
- **Bordered question and approval callouts (done).** Arrow-key choices, with the approval's
  scope stated ("for the rest of this run"). [Plan](plans/2026-10-07-phil-callouts.md) ·
  [spec](specs/2026-10-07-phil-callouts-design.md).
- **Failure categories (done).** Auth, quota, busy, server, network, refused, output, config and
  internal. Each callout says what happened, whether Phil retries, and what to do.
  [Plan](plans/2026-10-07-phil-callouts.md) · [spec](specs/2026-10-07-phil-callouts-design.md).
- **Full-screen app versus a richer inline UI: decided, inline** (user, 2026-10-08, from mockups).
  The chat stays part of the terminal's scrollback, keeping its own scroll, search and copy. A
  full-screen mode (fixed header, switchable sub-agent feeds, a details pane, mouse support) can
  come later as an option such as `phil --full-screen`, if there's usage or interest.

### M6 — Extensibility and learning

Each item is its own design: file explorer and diff views, diagrams (ASCII and Mermaid), skills, plugins, UI extensions, snippets, memory and self-learning (formerly "spec #2"), and Herdr agent support.

- **Visual proposals for UI work** (user request, 2026-10-02; moved from M5 on 2026-10-07, because it
  builds on M6's diagrams and UI extensions):
  - For changes with a visual element (a new page section, a CTA, a layout), Phil shows what it intends before building it: a quick mockup, a sketch, or references.
  - The user confirms or redirects at that point.
  - This isn't a full design agent. It's a way to say "this is what we're thinking".
  - Prompted by a live run that added a CTA section with no shared picture of placement, content or visuals.

Core capabilities, built-in optional modules, plugins, UI extensions, and skills are kept distinct. Configuration, permissions, and learned memory stay separate on disk.

## Launch gate

A public release needs at least M1–M4, plus keeping internal diagnostics out of end users' terminals (see the 04a followups). M5 is what makes Phil feel finished.

## Release sequence (user, 2026-10-13)

The goal is to reach a publishable state first.

**First public release, in this order:**
1. ✓ M4 permissions (done, 2026-10-13). This also settles where config, policies and memory live on disk.
2. ✓ Memory at the repository level: facts learned in a repo that later runs reuse, visible and editable (done, 2026-10-15; [plan](plans/2026-10-15-phil-repo-memory.md), [spec](specs/2026-10-15-phil-repo-memory-design.md)).
3. ~~Skills~~ — moved to after launch (user, 2026-10-20).
4. Release work, in this order (user, 2026-10-20):
   - ✓ keep internal diagnostics out of users' terminals ([spec](specs/2026-10-20-phil-diagnostics-design.md), [plan](plans/2026-10-21-phil-diagnostics.md));
   - confirm the bring-your-own-provider path (M2a);
   - finalize the mascot;
   - the TestPyPI rehearsal, then publish `phil-agent` by tagging `v0.1.0` (packaging: PR #50).
5. A polish pass before release: clean up the status bar, the chat and the agent display, so that what's on screen is clear and relevant rather than competing for attention (UI polish, PR #41, merged).

**After launch, in this order:**
1. Skills, a basic version: Phil and its sub-agents use the `SKILL.md` skills the user already has installed, from folders the user approves.
2. Design workflows in the browser: a local page that shows the reasoning and the visual options, where the user's choice is sent back to the terminal. This replaces "visual proposals" in M6.
3. A full-screen mode, as an option. Other coding agents take over the terminal once a conversation starts, and Phil has its own command set and persistent on-screen elements, so this fits. It comes after launch because it means rebuilding how the whole chat is drawn.
4. Memory across repositories: insights about how the user works and best practices not already in the agent files.
5. Other local customizations: hooks, MCP servers and plugins.

## Open decisions (carried from the feedback)

1. Whether per-agent model selection stays available as an advanced option (leaning yes) — M2.
2. Whether the classifier is explicit or inferred from the configured models — M2/M3.
3. The task classes and their workflows — M3.
4. How much reasoning appears in the feed, which status is permanent, and whether sub-agent feeds are live or snapshots — M5.
5. The scope of approval rules, without granting broad mutation rights by accident — M4.
6. Whether the file explorer and diffs are core or extensions — M5/M6.
7. The on-disk separation of config, policies, and memory — M2, M4, M6.
8. Mouse interaction, and which rich visualizations work across terminals — M5/M6.
