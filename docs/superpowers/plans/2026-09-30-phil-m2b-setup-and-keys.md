# Phil M2b — Guided Setup and Stored Credentials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new user runs `phil`, answers a few questions, and gets a working global config. Provider keys are stored in the OS keychain, so no `.env` sourcing is needed. `phil keys` manages the stored keys.

**Architecture:** Three new pieces:
- `phil.key_store`: env-first, then keychain, via `keyring`, exposed as a read-only `key_lookup()` mapping. It is used everywhere a key is read today.
- `phil keys`: CLI commands built on top of it.
- A `phil.setup` package: a step-by-step flow over a small `SetupIO` interface (terminal and scripted implementations), provider-specific model suggestions and pickers, a `models check` step, and an in-place `tomlkit` edit of `~/.phil/config.toml`.

Bare `phil` starts setup when no role has a model and a terminal is attached.

**Tech Stack:** Python 3.14, uv, typer, prompt_toolkit, keyring (new), tomlkit (new), httpx (already present, for the Ollama tags call).

**Spec:** `docs/superpowers/specs/2026-09-30-phil-m2b-setup-and-keys-design.md` (roadmap M2, part b; builds on M2a)

## Global Constraints

- **Key lookup order:** the environment variable wins, then the keychain entry (service `"phil"`, username = the variable name, e.g. `OPENROUTER_API_KEY`).
- **Never show or store a key value:** never print, log, write to config, or store as an artifact. Only a key's source (`env` | `keychain` | missing) is ever shown.
- **Tests never touch the real keychain:** an autouse fixture installs an in-memory keyring backend for every test (live and bench included). Tests never touch the network either. The Ollama tags call and the OpenRouter catalog are injected.
- **Exact messages:**
  - `Saved <VAR> for <provider> in the keychain.`
  - The same, plus ` (the environment value still wins while <VAR> is exported)` when the variable is also set.
  - `Removed <VAR> from the keychain.` / `No <VAR> in the keychain.` / `<provider> doesn't use a key.`
  - `No keychain is available here; export <VAR> instead.`
  - `Phil isn't set up yet. Let's choose your models (about a minute).`
  - `Run phil setup to choose your models.`
- **Scope of setup writes:** setup edits only the global file (`phil.config.global_config_path()`), in place with `tomlkit`. It changes only `models.high`, `models.low`, and `providers.<name>.*` for the chosen provider, and keeps every other key and comment. Cancelling (Ctrl-C or EOF) at any step writes nothing.
- **Classifier:** setup does not ask about `classifier` (deferred to M3).
- **Import rule:** `phil.cli.main`, `phil.chat.*`, `phil.config`, `phil.key_store` and `phil.setup.*` must not import langchain/langgraph/deepagents at module level. Import `keyring` lazily inside `phil.key_store` functions.
- **Commit trailer:** every commit message ends with a blank line then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The user's shell guard blocks any command line containing the word "keychain", so write commit messages to a file and use `git commit -F`.

## File Structure

- **Create:**
  - `src/phil/key_store.py`
  - `src/phil/setup/__init__.py`
  - `src/phil/setup/flow.py` (the steps)
  - `src/phil/setup/io.py` (`SetupIO`, `TerminalSetupIO`, `ScriptedSetupIO`)
  - `src/phil/setup/suggestions.py`
  - `src/phil/setup/catalog.py` (OpenRouter search, Ollama tags)
  - `src/phil/setup/write.py` (the `tomlkit` edit)
- **Modify:**
  - `src/phil/config.py` (`missing_keys` default lookup)
  - `src/phil/agents/providers.py` (default `environ`)
  - `src/phil/agents/check.py` (key pre-check)
  - `src/phil/cli/main.py` (`keys` sub-app, `setup` command, auto-start, key check)
  - `src/phil/agents/pricing.py` (expose the catalog)
  - `tests/conftest.py` (keyring fixture)
  - `pyproject.toml` and `uv.lock`
  - `README.md`, `docs/superpowers/roadmap.md`
- **Create (docs):** `docs/superpowers/plans/2026-09-30-phil-m2b-followups.md`

---

### Task 1: Credentials

**Files:**
- Create: `src/phil/key_store.py`
- Modify: `src/phil/config.py`, `src/phil/agents/providers.py`, `src/phil/agents/check.py`, `src/phil/cli/main.py` (~109, the key check), `tests/conftest.py`, `pyproject.toml`
- Test: `tests/test_key_store.py` (create), plus the existing provider, check and CLI tests

**Interfaces (all in `src/phil/key_store.py`):**
- `SERVICE = "phil"`
- `class KeyStoreError(Exception)`
- `keychain_available() -> bool`: False when `keyring.get_keyring()` is a `keyring.backends.fail.Keyring` or `keyring.backends.null.Keyring`, or when importing `keyring` fails.
- `key_source(var: str) -> Literal["env", "keychain"] | None`: `env` if `os.environ.get(var)` is non-empty. Otherwise `keychain` if `keyring.get_password(SERVICE, var)` is non-empty. Otherwise None. Any keyring exception counts as None and is logged at debug level without the value.
- `get_key(var) -> str | None`, following the same order.
- `set_key(var, value) -> None`: raises `KeyStoreError("No keychain is available here; export <VAR> instead.")` when unavailable.
- `delete_key(var) -> bool`: True if an entry was deleted.
- `class KeyLookup(Mapping[str, str])`: `__getitem__` returns `get_key(var)` or raises `KeyError`. `__iter__`/`__len__` cover only `os.environ` (enough for `.get`, which is all callers use).
- `key_lookup() -> KeyLookup`

**Wiring:**
- `providers.build_chat_model(..., environ: Mapping[str, str] | None = None)` uses `environ if environ is not None else key_lookup()`.
- `PhilConfig.missing_keys(roles, environ=None)` does the same. The CLI's key check stops passing `os.environ`.
- `check.py`'s key pre-check uses `key_lookup().get(...)`.
- The dependency is `uv add keyring`, at its latest version, with the floor raised to the locked version.

**Test fixture** (autouse, in `tests/conftest.py`): install a fresh in-memory backend for every test and restore the previous backend afterwards. Tests needing a keychain-less environment can set a fail backend.

```python
@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch):
    import keyring
    from keyring.backend import KeyringBackend

    class MemoryKeyring(KeyringBackend):
        priority = 1
        def __init__(self):
            super().__init__()
            self.store: dict[tuple[str, str], str] = {}
        def get_password(self, service, username):
            return self.store.get((service, username))
        def set_password(self, service, username, password):
            self.store[(service, username)] = password
        def delete_password(self, service, username):
            from keyring.errors import PasswordDeleteError
            if (service, username) not in self.store:
                raise PasswordDeleteError(username)
            del self.store[(service, username)]

    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)
```

**Tests:**
- The environment wins over the keychain.
- The keychain is used when the variable is unset.
- An empty environment value falls through to the keychain.
- Unset everywhere gives None.
- With a fail backend, `keychain_available()` is False and `set_key` raises the exact message.
- `delete_key` returns True, then False.
- A keychain-only key satisfies `missing_keys` and builds an openai model: its `api_key` secret value equals the stored key. Read it via the model's `SecretStr.get_secret_value()`, in memory only, and never print it.
- The CLI key check passes with a keychain-only key.
- A worker-path model build finds a keychain-only key. Check this through the scripted factory path, or directly through `build_chat_model` with no `environ`.

- [ ] Red → green → full suite → commit: `Look up provider keys in the environment, then the keychain`

---

### Task 2: `phil keys`

**Files:**
- Modify: `src/phil/cli/main.py` (a `keys` Typer sub-app registered with `app.add_typer(keys_app, name="keys")`)
- Test: `tests/cli/test_keys_command.py` (create)

**Behaviour** (exact messages in Global Constraints):
- **`phil keys set <provider>`:**
  - Resolve `<provider>` with `resolve_provider(config, name)` so aliases and custom providers work. An unknown provider prints the unknown-provider message and exits 1. A provider with no `api_key_env` prints `<provider> doesn't use a key.` and exits 1.
  - Read the value with `getpass.getpass(f"{VAR}: ")`. The call is injectable for tests. An empty value aborts without saving.
  - Then `set_key` and print the saved message (with the env suffix when that variable is set).
- **`phil keys list`:**
  - One line per provider that is used by a role, or configured under `[providers]`, or a built-in provider with a key present. Format: `<provider>  <VAR>  env|keychain|missing`.
  - Providers without a key are shown as `<provider>  (no key needed)`.
  - The output must never include a value.
- **`phil keys remove <provider>`:** prints `Removed …` or `No …`.
- The config is loaded with the root's `--set` overrides, as in `phil config`.

**Tests:**
- `set` stores the value (checked through the memory backend) and prints exactly the saved line.
- `set` with the env var also exported prints the suffix.
- `set ollama` is refused.
- `set nope` prints the unknown-provider message.
- `list` shows each source. Plant a distinctive secret such as `sk-TESTSECRET-123` in both the env and the keychain, and assert it is absent from the output.
- `remove` prints both messages.

- [ ] Red → green → full suite → commit: `Add phil keys to store, list and remove provider keys`

---

### Task 3: `phil setup`

**Files:**
- Create: `src/phil/setup/` (`__init__.py`, `io.py`, `suggestions.py`, `catalog.py`, `write.py`, `flow.py`)
- Modify: `src/phil/agents/pricing.py`, `src/phil/cli/main.py` (the `setup` command), `pyproject.toml` (`uv add tomlkit`, latest version)
- Test: `tests/setup/` (create: `test_write.py`, `test_catalog.py`, `test_flow.py`)

**Interfaces:**
- **`io.py`:**
  - `class SetupCancelled(Exception)`
  - `class SetupIO(Protocol)` with four methods:
    - `say(text: str) -> None`
    - `ask(prompt: str, default: str | None = None) -> str`
    - `choose(prompt: str, options: list[str], default: int = 0) -> int`
    - `secret(prompt: str) -> str`

    Any of the input methods raises `SetupCancelled` on Ctrl-C or EOF.
  - `TerminalSetupIO(console)`:
    - uses a prompt_toolkit `PromptSession` for `ask`;
    - renders `choose` as a numbered list and reads a number;
    - uses `getpass` for `secret`;
    - uses plain `input()` when stdin is not a TTY;
    - escapes all printed text.
  - `ScriptedSetupIO(answers: list[str])` records every `say` in `.lines` and pops the next answer for each input call. When answers run out it raises `SetupCancelled`.
- **`suggestions.py`:**
  - `SUGGESTIONS: dict[str, dict[str, str]]` with `high`/`low` model IDs for `openrouter`, `openai`, `anthropic` and `google`. Use current IDs.
    - For `openrouter`, use `high = "openrouter:anthropic/claude-sonnet-5"` and `low = "openrouter:google/gemini-3.8-flash"`. The low model is the benchmark's model; the user's live check confirmed `google/gemini-flash-latest` works too.
    - For the others, use each provider's current flagship and light model, in `provider:model` form. Verify the IDs with ctx7 or the provider SDKs' docs; if unsure, note it in the report.
  - Ollama and custom providers have no suggestions.
- **`catalog.py`:**
  - `@dataclass(frozen=True) class CatalogModel(id: str, input_per_mtok: float | None, output_per_mtok: float | None)`
  - `search_openrouter(query: str, catalog: list[CatalogModel], limit: int = 10) -> list[CatalogModel]`: a case-insensitive substring match on the id, in catalog order.
  - `openrouter_catalog(book: PriceBook | None = None) -> list[CatalogModel]`, via a new `PriceBook.models()` that returns the parsed catalog entries (id plus prices per million tokens) and loads the book when needed.
  - `ollama_models(base_url: str, *, get=httpx.get, timeout=3.0) -> list[str] | None`: GET `<base_url without a trailing /v1>/api/tags`, returning `[m["name"] for m in json["models"]]`, or None on any error.
- **`write.py`:**
  - `write_global_config(path: Path, *, models: dict[str, str], provider_name: str | None, provider_fields: dict[str, object]) -> None`.
  - Load the file with `tomlkit` if it exists, else start a new document with a header comment `# Phil settings (written by phil setup; edit freely)`.
  - Set `models.high`/`models.low`, and `providers.<name>.<field>` for each given field.
  - Create parent directories and write atomically: a temp file, then `os.replace`.
  - The field names allowed are `kind`, `base_url`, `api_key_env`, `input_per_mtok` and `output_per_mtok`. Never a key.
- **`flow.py`:** `run_setup(io: SetupIO, *, config: PhilConfig, check=check_models, catalog=openrouter_catalog, ollama=ollama_models, write=write_global_config, path: Path | None = None) -> bool`. Returns True if it wrote the file, False if cancelled. The steps follow spec §3.3:
  1. **Provider.** Choose from OpenRouter, OpenAI, Anthropic, Google, Ollama, or Custom OpenAI-compatible. The default is the current `models.high` provider if one is set, else OpenRouter.
     - Ollama: call `ollama(...)`. On None, say how to start Ollama (`ollama serve`) and offer retry or back.
     - Custom: ask for a name matching `[a-z][a-z0-9_-]*` (and not a built-in name), a `base_url`, and a key variable (default `<NAME>_API_KEY`, or blank for none).
  2. **Key.** Skip this step when the provider has no key. If `key_source(var)` is set, say `Using <VAR> from <source>.`. If the source is `keychain`, offer to replace it.
     Otherwise ask with `io.secret` and `set_key`. On `KeyStoreError`, say the message and continue.
  3. **Models.** For each of `high` and `low`:
     - Show the suggestion (or the current value on a rerun) as the default.
     - OpenRouter: a typed answer that isn't an exact existing id is treated as a search query, showing up to 10 `id  $in/$out per M` choices, plus "type again".
     - Ollama: choose from the installed models.
     - Others: accept a typed id and prefix `<provider>:` if it's missing.
  4. **Check.**
     - Build a candidate config: the current config with `models.high`/`models.low` and the provider entry overridden, and with role keys under `[models]` removed for the check, since setup targets the tiers.
     - Run `check(candidate)` and say each line.
     - On a failure, offer to choose a different model for that tier or keep it anyway.
  5. **Write.** Call `write(...)` to the global path, then say what was written, the path, and the next steps: `phil`, `phil config`, `phil keys list`.
  - A `SetupCancelled` anywhere returns False without writing.
- **CLI:** `phil setup` runs `run_setup(TerminalSetupIO(console), config=load_config(root, overrides=...))`. Exit 0 if it wrote the file, 1 if cancelled.

**Tests** (scripted IO; the fakes for check, catalog and ollama return fixed data):
- `test_write.py`:
  - a new file loads with `load_config`;
  - an existing file with comments, `[run]` and `[shell]` keeps them all;
  - no field ever holds a key.
- `test_catalog.py`: search ordering and limit; the `ollama_models` success and failure paths with a fake `get`; `PriceBook.models()` from a fixture catalog.
- `test_flow.py`:
  - a fresh OpenRouter setup with a key stores the key, writes high and low, and runs the check once;
  - an existing env key is not asked for again;
  - the Ollama path picks installed models;
  - unreachable Ollama, then retry;
  - the custom provider writes a `providers.<name>` table;
  - a failed check, then a different choice, then the check passes;
  - a cancel at each step writes nothing and leaves the keychain untouched after the key step (cancel before `set_key`);
  - a rerun pre-fills the current models.

- [ ] Red → green → full suite → commit: `Add phil setup, a guided first-run setup`

---

### Task 4: Auto-start

**Files:**
- Modify: `src/phil/cli/main.py`
- Test: `tests/cli/test_setup_autostart.py` (create)

**Behaviour** (spec §3.4):
- In `_chat` (bare `phil`), before today's missing-model refusal: if `sys.stdin.isatty() and sys.stdout.isatty()` and `config.missing_model_messages(CHAT_ROLES + RUN_ROLES)` is non-empty:
  - print the "isn't set up yet" line and run setup with the terminal IO;
  - if setup returns True, reload the config (same overrides) and continue into the chat;
  - if it returns False, print the missing-model messages and exit 1 as today.
- Put the TTY check in a small `_interactive()` helper so tests can monkeypatch it.
- Everywhere else a missing model stops a command (`phil run`, `phil models check`, or bare `phil` without a TTY), print the missing-model messages, then `Run phil setup to choose your models.`, and exit as today.

**Tests:**
- Bare `phil` with `_interactive` True, no models, and a scripted setup (monkeypatch `run_setup` to write a config and return True) enters the chat. A `_run_chat` stub records the call.
- The same with cancel exits 1 with the messages.
- `phil run` with no models prints the hint and never runs setup.
- With `_interactive` False, bare `phil` prints the hint and doesn't prompt.

- [ ] Red → green → full suite → commit: `Start setup automatically when no models are configured`

---

### Task 5: Docs and follow-ups

**Files:** `README.md` (a "Getting started" section, and a key-storage section under Configuration), `docs/superpowers/roadmap.md` (mark M2 done; move M2a/M2b to Done with links), `docs/superpowers/plans/2026-09-30-phil-m2b-followups.md`.

**README:**
- first run: `phil`, then setup starts, then the chat;
- `phil setup` to change providers or models;
- `phil keys set|list|remove`;
- the env-first rule;
- no keychain means env only;
- classifier arrives in M3.

Every claim must match the code; check `phil setup --help` and `phil keys --help`.

**Follow-ups:** the classifier step (M3), and deferred review minors.

- [ ] Write the docs, run the full suite, commit: `Document setup and stored keys`

**Live check (user):** `PHIL_HOME=$(mktemp -d) phil` in a repo. Set up OpenRouter with the key typed into setup. The chat should start without `--env-file`, and `phil keys list` should show `keychain`. Afterwards, remove the test entry with `phil keys remove openrouter` if it was only for the test.
