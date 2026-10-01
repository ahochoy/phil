# Phil M2b: Guided Setup and Keychain Credentials

**Status:** approved in conversation, 2026-09-30.

**Scope:** roadmap M2, part b, built on M2a (layered config, tiers, providers, `phil models check`). It adds:
- a guided setup, `phil setup`, which also starts on its own when nothing is configured;
- API keys stored in the OS keychain, with environment variables still taking precedence;
- `phil keys` commands for managing those keys.

**Deferred to M3:** choosing a `classifier` model in setup. It is only meaningful once the classifier exists and can be benchmarked. It can already be set by hand.

## 1. Problem

- **First run is hard.** A new user has to hand-write `~/.phil/config.toml` and know the provider and model IDs, with nothing to guide them.
- **Keys don't persist.** They only come from environment variables, so every session needs `source .env` or `--env-file`. Background workers see a key only if the launching shell exported it.
- **Keys can only be managed through the environment.** Phil has no way to store one, check where one comes from, or remove one.

## 2. Decisions (user, 2026-09-30)

| Topic | Decision |
|---|---|
| Model choice in setup | Suggested `high`/`low` per provider, editable. OpenRouter adds type-to-search over its cached model list, with prices. Ollama lists the installed models. Other providers take a typed ID. |
| Auto-start | Bare `phil` in a terminal starts setup when no role has a usable model. Other commands, and runs without a terminal, print a hint. |
| Key management | `phil keys set <provider>`, `phil keys list`, `phil keys remove <provider>`. Setup uses the same code. |
| Rerunning setup | Setup edits the global file in place with `tomlkit`: it changes only the `[models]` and `[providers]` keys it sets, and keeps other settings and comments. |
| Classifier | Deferred to M3. |

## 3. Design

### 3.1 Credentials (`phil.key_store`)

- **Lookup order:** the environment variable first, then the keychain. Keychain entries are stored with service `phil` and the variable name as the username (e.g. `OPENROUTER_API_KEY`).
- **`key_lookup()`** returns a read-only `Mapping[str, str]` that checks `os.environ` first and then the keychain, one variable at a time, as needed. Every caller that reads a provider key today uses it instead of `os.environ`:
  - `PhilConfig.missing_keys` (the CLI's startup check);
  - `check.py`'s key pre-check;
  - `providers.build_chat_model`, which runs in the chat and in background workers. So a worker finds a stored key without the shell exporting it.
- **Keychain functions:**
  - `key_source(var) -> "env" | "keychain" | None`;
  - `set_key(var, value)`;
  - `delete_key(var) -> bool`;
  - `keychain_available() -> bool` (false when `keyring`'s active backend is the fail or null backend).
- **When the keychain is unavailable:** `set_key` raises `KeyStoreError` with the message `No keychain is available here; export <VAR> instead.`, and lookups use the environment only.
- **Values are never printed, logged, written to config or stored as artifacts.** Only a key's source is ever shown.
- **Dependency:** `keyring`, at its latest version.
- **Tests:** an autouse fixture installs an in-memory keyring backend for every test (live and bench tests included), so tests never touch the real keychain.

### 3.2 `phil keys`

- **`phil keys set <provider>`:**
  - The provider name is resolved through `resolve_provider`, so a built-in provider, an alias or a custom provider all work.
  - A provider with no `api_key_env`, such as `ollama`, is refused: `<provider> doesn't use a key.`
  - The key is read with a hidden prompt (`getpass`) and stored.
  - Output: `Saved <VAR> for <provider> in the keychain.`
  - If `<VAR>` is also set in the environment, it adds `(the environment value still wins while <VAR> is exported)`.
- **`phil keys list`** shows one line for each provider that is configured or used by a role, plus the built-ins that have a key: `<provider>  <VAR>  env | keychain | missing`. It never shows a value.
- **`phil keys remove <provider>`** deletes the keychain entry: `Removed <VAR> from the keychain.`, or `No <VAR> in the keychain.` if there wasn't one.

### 3.3 `phil setup`

The steps run on the same prompt_toolkit input the chat uses, with plain line input when there is no terminal. Ctrl-C at any step cancels without writing anything.

1. **Provider.** The choices are:
   - OpenRouter (recommended: one key for many models);
   - OpenAI, Anthropic, Google;
   - Ollama (local): checks `GET <base_url minus /v1>/api/tags`; if unreachable, says how to start Ollama and offers retry or back;
   - custom OpenAI-compatible: asks for a name, `base_url`, and an optional key variable (default `<NAME>_API_KEY`).
2. **Key** (skipped for keyless providers):
   - If `key_source(var)` is `env` or `keychain`, show `Using <VAR> from <source>.` and offer to replace a keychain value.
   - Otherwise, ask with a hidden prompt and `set_key`.
   - With no keychain, print the export instruction and continue. The check then fails unless the key is exported.
3. **Models.**
   - Suggested defaults come from a small table in `phil.setup.suggestions`, keyed by provider, giving `high` and `low`. The values are the current recommended IDs, maintained in code and confirmed with the benchmark.
   - Pressing Enter accepts a suggestion. Or you can:
     - OpenRouter: type to search the cached model list (id plus input/output price per million tokens, top 10 matches);
     - Ollama: pick from the installed models;
     - other providers: type an ID.
4. **Check.**
   - Runs `check_models` on the chosen models, one line per model.
   - On ✗, it shows the reason and offers to choose another model for that tier, or keep it anyway.
5. **Write and summarise.**
   - Edit `~/.phil/config.toml` with `tomlkit`: set `models.high`, `models.low`, and (for custom or overridden providers) `providers.<name>.*`. All other content, including comments, is kept. The file and `~/.phil` are created if missing.
   - Print what was written, the file path, and the next steps: `phil` to start a chat, `phil config`, and `phil keys list`.

When run again, setup pre-fills the current provider and models from the effective config. The rerun edits the global file only, never a repo's `phil.toml`.

### 3.4 Auto-start

- **Bare `phil`, with a terminal on stdin and stdout, and `missing_model_messages(CHAT_ROLES + RUN_ROLES)` not empty:** print `Phil isn't set up yet. Let's choose your models (about a minute).`, run setup, then continue into the chat with the reloaded config. If setup is cancelled, exit with the usual missing-model messages.
- **Otherwise** (`phil run`, `phil models check`, anything without a terminal): print the missing-model messages plus `Run phil setup to choose your models.`, and exit as today.

## 4. Testing

All tests run offline:

- Credentials:
  - the environment wins over the keychain;
  - the keychain is used when the variable is unset;
  - an unavailable keychain gives environment-only lookups and a clear `set_key` error;
  - `key_lookup` works as the `environ` for `build_chat_model` and `missing_keys`.
- A worker builds a model with a keychain-only key (fake keyring, scripted factory path).
- `phil keys`:
  - `set` (via a hidden-input stub);
  - `list` (sources shown, never a value — assert that a planted secret never appears in the output);
  - `remove`;
  - the keyless-provider refusal.
- Setup, driven with scripted input and fakes for the check, the Ollama tags endpoint and the OpenRouter catalog:
  - a fresh OpenRouter setup writes a file that loads, contains the models, and has no key;
  - the Ollama path;
  - the custom-provider path;
  - a failed check followed by a different choice;
  - a rerun keeps comments and unrelated keys;
  - cancelling writes nothing.
- Auto-start:
  - bare `phil` with a terminal and missing models runs setup and then the chat;
  - `phil run` prints the hint;
  - with no terminal, there is no prompt.

**Live (user):** with `PHIL_HOME` pointing at a fresh temporary directory, run `phil` and set up OpenRouter with a keychain key. The chat starts without `--env-file`, and `phil keys list` shows `keychain`.
