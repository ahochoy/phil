# Phil M2a — Layered Configuration, Model Tiers and Providers

**Status:** approved in conversation on 2026-09-30.

**Scope:** roadmap M2, part a. It covers:
- a global config layered under each repo's config;
- two or three model tiers in place of six per-role models;
- any provider, including Ollama and custom endpoints;
- a check that each configured model can return structured output.

**Deferred to M2b:** guided first-run setup (`phil setup`, which also starts automatically on the first `phil`) and storing keys in the OS keychain. See `docs/superpowers/roadmap.md`.

## 1. Problem

- **Config is repo-only.** Every repository needs its own `phil.toml`, including the six per-role models. Nothing is set once.
- **Per-role models are too granular.** Users want a strong model and a light model, not six model decisions.
- **Providers are fixed.** Only four providers are known, and only by their key variables. A local server or a custom endpoint gets no key check and no timeout, and nothing checks that the model can do what Phil needs.
- **Capability failures show up late.** The benchmark with `openai/gpt-oss-120b` failed at planning in every case ("no structured output was returned"). Phil recorded `raw: null`, so there was nothing to diagnose.

## 2. Decisions (user, 2026-09-30)

| Topic | Decision |
|---|---|
| Split | M2a covers the engine (config, tiers, providers, model check). M2b covers guided setup and keychain credentials. |
| Tiers | `high`: architect, critic, reviewer. `low`: orchestrator (intake, chat, `/btw`), implementer, tester. An optional `classifier` tier falls back to `low` and is used from M3 onwards. |
| Overrides | Per-role model keys stay as an advanced override. `[tiers]` remaps a role to another tier. |
| Credentials | Environment variables in M2a. M2b adds the OS keychain, with env vars still taking precedence. Keys never go in config files. |

## 3. Design

### 3.1 Layers

Settings resolve in this order, each layer overriding the previous one key by key:
1. built-in defaults;
2. `~/.phil/config.toml`, the global file (under `PHIL_HOME` when that is set);
3. `<repo>/phil.toml`;
4. command-level overrides.

Merging rules:
- **Tables** (`[run]`, `[models]`, `[providers.x]`, …) merge by key.
- **Scalars and lists replace** the value from the layer before. For example, the repo's `[shell] allow` replaces the global one as a whole.
- **Every layer is validated.** An invalid global or repo file is a `ConfigError` that names the file, the key and the problem.

Command-level overrides:
- `phil` and `phil run` accept a repeatable `--set key.path=value`, for example `--set run.max_cost_usd=5` or `--set models.high=anthropic:claude-sonnet-5`.
- The value is parsed as a TOML value, with a bare word treated as a string. A path that doesn't exist in the schema is an error.

Where each value came from:
- The merged `PhilConfig` records the source of every leaf value: `default`, `global`, `repo` or `command`.
- `phil config` prints the effective settings as TOML, with a trailing `# from <source>` comment on each line, for example `# from ~/.phil/config.toml`.
- `phil config --path` prints both file locations and whether each exists.

Workers:
- A run's worker loads the same layers.
- The worker receives the `--set` overrides the run was started with, recorded in the run state at launch, so a resumed run keeps them.

### 3.2 Model tiers

`[models]` accepts tier keys (`high`, `low`, `classifier`) and role keys (`orchestrator`, `architect`, `critic`, `implementer`, `tester`, `reviewer`).

`[tiers]` maps a role to a tier. Defaults:

| Tier | Roles |
|---|---|
| `high` | architect, critic, reviewer |
| `low` | orchestrator, implementer, tester |

The `classifier` tier has no roles yet.

**Resolution for a role:**
1. the role's own model key, if set;
2. otherwise the model of the role's tier;
3. otherwise, for `classifier`, the `low` model;
4. otherwise the role is unset.

The same resolution backs `model_for(role)`, `missing_models(roles)` and `missing_keys(...)`. A missing model reports `No model for <role> (tier <tier>). Set models.<tier> in ~/.phil/config.toml or phil.toml.`

**Existing configs:** a `phil.toml` that sets all six roles keeps working unchanged.

### 3.3 Providers

A model string is `<provider>:<model>`.

**Built-in providers:**

| Name | Kind | Base URL | Key variable |
|---|---|---|---|
| `openrouter` | openrouter | default | `OPENROUTER_API_KEY` |
| `openai` | openai | default | `OPENAI_API_KEY` |
| `anthropic` | anthropic | default | `ANTHROPIC_API_KEY` |
| `google` (alias `google_genai`, kept for existing configs) | google | default | `GOOGLE_API_KEY` |
| `ollama` | openai | `http://localhost:11434/v1` | none |

**Custom providers** are defined as `[providers.<name>]` with:
- `kind`: `openai` (meaning OpenAI-compatible, which covers vLLM, LM Studio, llama.cpp servers and similar), `anthropic`, `google` or `openrouter`;
- `base_url`: optional;
- `api_key_env`: optional; with none, no key is sent or checked;
- `input_per_mtok` and `output_per_mtok`: optional prices in USD per million tokens.

A user entry with a built-in name overrides that provider's fields, for example to set an `ollama` `base_url` or prices.

**Model construction** (`phil.agents.providers`) builds the LangChain chat model for each kind, with the right model, base URL and key:
- The timeout is converted to the provider's own units. OpenRouter takes milliseconds; the others take seconds.
- The SDK's own retries are turned off, so Phil's retry policy is the only one. Google kind uses max_retries=1 (its SDK treats 0 as 'use default retries'). OpenRouter's SDK client also gets an explicit no-retry `retry_config`, because with `max_retries=0` it falls back to its own backoff.

`langchain-openai`, `langchain-anthropic` and `langchain-google-genai` become runtime dependencies, at their current versions. They are imported only when a model of that kind is built.

**Errors:**
- An unknown provider name gives `Unknown provider "<name>" in <role/tier> model "<string>". Add [providers.<name>] to ~/.phil/config.toml or phil.toml.`
- A missing key gives `<provider> needs <ENV_VAR> (used by <roles>).`

**Pricing:**
- For OpenRouter models, costs are estimated from the OpenRouter price list, as today.
- For any provider with prices set, costs are estimated from those prices.
- `ollama` defaults to 0.0.
- Otherwise the cost is unknown (`$?`).

### 3.4 Capability check

`phil models check` runs, for each configured tier (and each overridden role), one tiny real call through the same agent path Phil uses. It uses a lean agent and a small structured-output contract, for example `{"ok": true, "echo": "<word>"}`.

It reports one line per model:
- `✓ high  anthropic:claude-sonnet-5  1.8s`, or
- `✗ low  openai:gpt-oss-120b  returned text instead of the required structured output: "<first 120 chars>"`.

Any failure exits non-zero. The command:
- uses the configured timeout and no retries;
- is never run automatically in M2a (M2b's setup runs it);
- is skipped in tests except through fake factories.

### 3.5 Diagnostics for "no structured output"

When an agent returns no structured output, the rejected-output record (`outputs/<name>.rejected.json`) keeps the text of the last AI message (capped at 2,000 characters) as `raw`, instead of `null`. `phil show` and the chat's `/more` can then display what the model actually said.

## 4. Testing

All tests run offline:
- Layer merge and precedence: default, then global, then repo, then `--set`.
- Source tracking and the `phil config` output.
- Error messages that name the file and key.
- Tier resolution, including role overrides, `[tiers]` remaps and the classifier fallback.
- Legacy six-role configs keep working.
- Provider resolution for built-ins, aliases, custom providers and overrides of built-ins.
- Constructed model objects carry the right base URL, key, timeout and `max_retries=0`, with no network calls (skip the test for any kind whose package is missing).
- Missing-key and unknown-provider messages.
- Price estimates from provider prices.
- `phil models check` with a fake agent that succeeds, and with one that returns no structured output.
- A rejected record keeps `raw` text.
- A worker resumed with the launch `--set` overrides.

**Live (user):** `phil models check` against the user's real providers, then a benchmark run with tiers configured.
