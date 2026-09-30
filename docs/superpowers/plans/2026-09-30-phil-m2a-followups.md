# Plan M2a follow-ups

Deferred items from implementing plan M2a ("layered config and models"). Plan:
`2026-09-30-phil-m2a-config-and-models.md`. Spec:
`../specs/2026-09-30-phil-m2a-config-and-models-design.md`. Ledger:
`.superpowers/sdd/2026-09-30-phil-m2a-config-and-models/progress.md`.

## M2b (roadmap)

- **Guided `phil setup`.** An interactive command that writes a readable, editable
  `~/.phil/config.toml` (tiers, provider, key environment variable) instead of asking the
  user to hand-write TOML. It also runs automatically the first time `phil` is invoked with
  no usable global config, so a new user lands in setup rather than an opaque "no model
  configured" error.
- **Keychain-stored credentials.** M2a reads API keys only from environment variables
  (`[providers.<name>] api_key_env`, or a built-in's own default). M2b adds storing and
  reading keys from the OS keychain as an alternative, so `phil setup` can offer to save a
  key instead of telling the user to export one.

## Config layering and `phil.toml` (Task 1)

- No test covers a malformed repo `phil.toml` (as opposed to a malformed global config) going
  through `load_config`'s error path.
- `phil.tomlw`'s writer emits back-to-back section headers for empty parent tables (a table
  with only empty sub-tables under it produces adjacent `[x]`/`[x.y]` headers with nothing
  between them).
- `effective_toml` omits leaves whose value is `None` instead of printing them explicitly
  (e.g. `api_key_env` left unset on a custom provider).

## Model resolution and messages (Task 2)

- The missing-model message idiom (build the "no model for `<role>`..." text, then print it)
  is duplicated between `phil/chat/approval.py` and `phil/cli/main.py`; a shared
  `missing_model_messages`-style helper could own both call sites instead of each formatting
  its own wrapper around `PhilConfig.missing_model_messages`.
- The `[models]` unknown-key wording changed during M2a (roles vs. tiers now both listed); no
  follow-up test pins the exact new wording, so a future edit could drift silently.

## Providers (Task 3)

- `phil.agents.invoke.chat_model` (and any other direct caller that omits a `provider=`
  argument) falls back to a bare `PhilConfig()` rather than the caller's real config. Harmless
  today because production call sites always pass a provider/config explicitly, but a future
  direct caller could silently get built-in defaults instead of the user's settings.

## CLI and `phil models check` (Task 4)

- `phil models check`'s own pre-check for a missing API key (`_check_one` in
  `src/phil/agents/check.py`) duplicates the key rule already enforced inside
  `providers.build_chat_model`; consider having the check call through the one real code path
  instead of re-implementing the "is the key set" test.
- `--set` is read by the chat, `phil run`, `phil config` and `phil models check` only. Every
  other command (`phil runs`, `phil parked`, `phil resume`, `phil stop`, `phil attach`,
  `phil diff`, `phil show`, `phil clean`, `phil pr`) accepts `--set` on the root command
  without error (it's a global Typer option) but silently ignores it, since those commands
  never call `_load_config`. Worth an explicit "not supported here" message, or wiring it
  through, rather than a silent no-op.
