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
