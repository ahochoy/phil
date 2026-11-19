# Changelog

All notable changes to Phil are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

The first public release, published to PyPI as `phil-agent`.

### Added

- `phil`, a chat in the terminal that plans a goal with you, then runs it in its own git worktree
  and leaves a tested, reviewed branch.
- Two model tiers, `high` and `low`. Providers: OpenRouter, OpenAI, Anthropic, Google, Ollama and
  any OpenAI-compatible endpoint.
- `phil setup` for choosing a provider, storing its key in the OS key store and checking each model.
- Request routing with the Jev classifier.
- A run budget that stops a run before it spends more than you allowed.
- `phil pr` to push a finished run and open its pull request, and `phil clean` to remove it.
- `phil show`, `phil diff` and `phil runs` for following and inspecting runs.
- Rules for the shell commands agents may run (`phil permissions`).
- Repo memory that Phil keeps between runs (`phil memory`).
- Native Windows support through Git Bash.
- A full-screen chat: `phil` opens a header, a scrolling conversation, a live line (agent, step,
  tasks, run time and cost, one line per sub-agent) and a boxed input. New commands `/chat`,
  `/status` and `/copy`; `phil chat` and `phil --continue` reopen chats; `[chat] screen = "inline"`
  keeps the previous chat, and small terminals, some Windows consoles and piped output fall back
  to it automatically.
- A calmer activity feed: a blank line before each block, design options that wrap instead of
  being cut off, picked answers and answers to Phil's clarifying questions kept as
  `? question  answer`, and a burst that edited files
  shows each file's changed lines (`+`/`−`, up to 8 lines and 3 files, `/more #n` for the rest).
- Images: drag an image (up to 5 MB, 5 per message) onto the chat input and the intake
  conversation, answerer, designer, architect, critic and implementer see it (the tester and
  reviewer are told its path). An optional
  `[models] vision` model, offered by `phil setup` and checked by `phil models check`, reads
  images when an agent's own model can't.

### Fixed

- A model's malformed tool-call arguments are repaired before Phil resends the history, so
  provider errors like `function.arguments must be valid JSON` no longer stop a run.
