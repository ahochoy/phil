# Phil Roadmap

**Updated:** 2026-09-30. **Inputs:** the v1 design (`specs/2026-09-23-phil-v1-design.md`), the plan followups files, and the live-testing feedback (`../feedback/2026-09-29-live-testing-feedback.md`).

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

- A classifier chooses a task class (question, small operation, simple change, diagnosis, focused fix, feature, refactor, design, broad project). The class sets the workflow depth and the model tier separately.
- Small classes get one worker, a focused change, and the relevant check. Large classes get investigation, planning, review, and verification.
- Lighter agents for small work (no sub-agent or summarization), review findings patched directly instead of becoming new TDD tasks, and tighter retries.
- Designed from M1's benchmark numbers.

### M4 — Permissions

- Approval scopes: once, session, repository or folder, all repositories, persistent.
- Targets: an exact command, a prefix, a tool, the read-only class, or a directory.
- A policy file separate from config and memory.
- The exact scope is shown before confirming. Read-only is never confused with mutating (for example `gh pr view` versus `gh pr merge`).

### M5 — Terminal experience and visibility

- A live event stream from the worker: tool calls and results, file changes, retries, and failures.
- An activity feed: conversation plus execution, with summaries first, expandable details, and raw output on demand.
- A welcome banner, a persistent input, a status bar, and a sub-agent bar that can later select one agent's feed.
- Bordered question and approval callouts with keyboard choices and explicit scope.
- Failure categories: provider, auth or quota, network, tool, command, permission, config, orchestration, internal. Each shows what changed, whether Phil will retry, and whether the user must act.
- Full-screen app versus a richer inline UI is decided at M5, from mockups.

### M6 — Extensibility and learning

Each item is its own design: file explorer and diff views, diagrams (ASCII and Mermaid), skills, plugins, UI extensions, snippets, memory and self-learning (formerly "spec #2"), and Herdr agent support.

Core capabilities, built-in optional modules, plugins, UI extensions, and skills are kept distinct. Configuration, permissions, and learned memory stay separate on disk.

## Launch gate

A public release needs at least M1–M4, plus keeping internal diagnostics out of end users' terminals (see the 04a followups). M5 is what makes Phil feel finished.

## Open decisions (carried from the feedback)

1. Whether per-agent model selection stays available as an advanced option (leaning yes) — M2.
2. Whether the classifier is explicit or inferred from the configured models — M2/M3.
3. The task classes and their workflows — M3.
4. How much reasoning appears in the feed, which status is permanent, and whether sub-agent feeds are live or snapshots — M5.
5. The scope of approval rules, without granting broad mutation rights by accident — M4.
6. Whether the file explorer and diffs are core or extensions — M5/M6.
7. The on-disk separation of config, policies, and memory — M2, M4, M6.
8. Mouse interaction, and which rich visualizations work across terminals — M5/M6.
