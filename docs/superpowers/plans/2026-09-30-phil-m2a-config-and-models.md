# Phil M2a — Layered Config, Model Tiers and Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phil's settings can be configured once globally and overridden per repo or per command. Users choose a strong (`high`) and a light (`low`) model instead of one model per role. Any provider works, including Ollama and custom OpenAI-compatible endpoints. `phil models check` proves each model can return structured output.

**Architecture:**
- `phil.config` gains a layered loader: defaults, then `~/.phil/config.toml`, then repo `phil.toml`, then `--set`. It tracks where each value came from, and tier-aware model resolution.
- A new `phil.agents.providers` module resolves `provider:model` strings to a `ProviderSpec` (built-in or `[providers.x]`) and builds the LangChain chat model per kind.
- The CLI gains `phil config`, `phil models check` and `--set`.
- Runs record their `--set` overrides so resumes keep them.

**Tech Stack:** Python 3.14, uv, pydantic, typer, rich, langchain 1.4 (`init_chat_model`), and langchain-openrouter, langchain-openai, langchain-anthropic and langchain-google-genai.

**Spec:** `docs/superpowers/specs/2026-09-30-phil-m2a-config-and-models-design.md` (roadmap M2, part a)

## Global Constraints

- **Layer order:** built-in defaults → `phil_home() / "config.toml"` (global) → `<repo>/phil.toml` → command `--set`. Tables merge by key. Scalars and lists replace.
- **Default tiers:** `high` = architect, critic, reviewer. `low` = orchestrator, implementer, tester. The `classifier` tier has no roles and falls back to `low`.
- **Model resolution for a role:** the role's own `[models]` key, then its tier's model, then (for `classifier` only) `low`, then unset. A six-role legacy `phil.toml` keeps working unchanged.
- **Built-in providers:**
  - `openrouter` (openrouter, `OPENROUTER_API_KEY`)
  - `openai` (openai, `OPENAI_API_KEY`)
  - `anthropic` (anthropic, `ANTHROPIC_API_KEY`)
  - `google`, with alias `google_genai` (google, `GOOGLE_API_KEY`)
  - `ollama` (openai, `base_url` `http://localhost:11434/v1`, no key, prices 0.0)
- **Provider kinds:** `openai` (OpenAI-compatible), `anthropic`, `google`, `openrouter`.
- **Provider SDK settings:** the timeout goes in the provider's own units (OpenRouter takes milliseconds, the others seconds), and `max_retries=0` always. Exception: Google kind uses max_retries=1 (its SDK treats 0 as 'use default retries'). OpenRouter's SDK client also gets an explicit no-retry `retry_config`.
- **Messages (exact):**
  - missing model: `No model for <role> (tier <tier>). Set models.<tier> in ~/.phil/config.toml or phil.toml.`
  - unknown provider: `Unknown provider "<name>" in <role/tier> model "<string>". Add [providers.<name>] to ~/.phil/config.toml or phil.toml.`
  - missing key: `<provider> needs <ENV_VAR> (used by <roles>).`
- **API keys are never written to, or read from, config files.** They come only from environment variables in M2a.
- **Rejected output:** when no structured output is returned, the rejected-output record keeps the last AI message's text (at most 2,000 characters) as `raw`.
- **Import rule:** `phil.cli.main`, `phil.chat.*`, `phil.config`, `phil.agents.invoke` and `phil.agents.providers` must not import langchain, langgraph or deepagents at module level. Import provider packages lazily, only when a model of that kind is built.
- **Tests never touch the network.** `phil models check` is tested with fake factories only.
- **Commit trailer:** every commit message ends with a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

- **Modify:**
  - `src/phil/config.py`: layers, sources, `--set` parsing, tiers, providers table.
  - `src/phil/agents/factory.py`: build models through `providers.build_chat_model`.
  - `src/phil/agents/invoke.py`: provider-aware model building, provider prices, and rejected `raw` text.
  - `src/phil/agents/pricing.py`: optional price override.
  - `src/phil/cli/main.py`: `--set` on the root command and `run`; the `config` and `models check` commands; tier-aware checks.
  - `src/phil/run/worker.py` and `src/phil/run/launch.py`: launch overrides.
  - `src/phil/store/db.py` and `src/phil/store/runs.py`: a `config_overrides` column.
  - `src/phil/chat/controller.py`: reload with overrides.
  - `tests/live/bench/harness.py`: `load_config` signature.
  - `pyproject.toml` and `uv.lock`: dependencies.
  - `README.md`.
- **Create:**
  - `src/phil/agents/providers.py`: `ProviderSpec`, `resolve_provider`, `build_chat_model`.
  - `src/phil/agents/check.py`: `check_models`, `CheckResult`.
  - `docs/superpowers/plans/2026-09-30-phil-m2a-followups.md`.

---

### Task 1: Layered config with sources and `--set`

**Files:**
- Modify: `src/phil/config.py`, every caller of `load_config` (`src/phil/cli/main.py`, `src/phil/run/worker.py`, `src/phil/chat/controller.py`, `tests/live/bench/harness.py`).
- Test: `tests/test_config.py` (extend).

**Interfaces:**
- Produces:
  - `global_config_path() -> Path`, returning `phil_home() / "config.toml"` (import `phil_home` from `phil.store.paths`; `phil.store.paths` must not import `phil.config`).
  - `parse_override(text: str) -> tuple[list[str], object]`: splits on the first `=`, splits the path on `.`, and parses the value with `tomllib.loads("v = " + value)["v"]`. On a `TOMLDecodeError`, the raw string is the value. It raises `ConfigError("--set expects key.path=value: <text>")` when there is no `=` or the path is empty.
  - `load_config(repo_root: Path, *, overrides: Sequence[str] = ()) -> PhilConfig`.
  - `PhilConfig.sources: dict[str, str]`: a dotted leaf path mapped to `"default"`, the global file path (as `str`), `"phil.toml"` or `"--set"`. Store it with `PrivateAttr`, so the model's schema is unchanged.
  - `effective_toml(config: PhilConfig) -> str`: the merged settings as TOML with trailing `# from <source>` comments on each leaf line. `default` leaves are included.
- **Merge rule:** a `dict` merges recursively. Anything else replaces the lower layer's value.
- **Validation:**
  - Each file layer is parsed as TOML on its own. A TOML syntax error gives `ConfigError(f"Invalid {path}: {exc}")`.
  - The merged dict is validated once. A `ValidationError` names the source of the first failing key, from the sources map, e.g. `Invalid ~/.phil/config.toml: run.max_cost_usd: Input should be a valid number`.
- **`--set` on an unknown path:** it fails validation, because `extra="forbid"`, and the message names `--set`.

- [ ] **Step 1: Write the failing tests** in `tests/test_config.py`. `PHIL_HOME` is already isolated per test by the autouse `phil_home` fixture, so write the global file at `phil_home() / "config.toml"`.
  - `test_global_values_apply_when_the_repo_has_no_phil_toml`: `[run] max_cost_usd = 7.0` globally gives `7.0` and source = the global path.
  - `test_repo_overrides_global_key_by_key`: global `[run] max_cost_usd = 7.0` plus `warn_at = 0.5`, and repo `[run] max_cost_usd = 3.0`, gives `3.0` from `phil.toml` and `0.5` from global.
  - `test_lists_replace`: global `[shell] allow = ["a"]` and repo `["b"]` gives `["b"]`.
  - `test_set_wins_over_every_file`: `overrides=["run.max_cost_usd=5"]` gives `5.0`, with source `--set`.
  - `test_set_parses_toml_values_and_bare_strings`: `models.high=openrouter:x/y` gives a string, `run.max_tokens=100` gives an int, and `shell.allow=["ls"]` gives a list.
  - `test_set_without_equals_is_a_config_error`.
  - `test_set_unknown_path_names_set_in_the_error`.
  - `test_invalid_global_file_names_the_file`: the global file contains `[run] max_cost_usd = "x"`, and the error message contains the global path.
  - `test_effective_toml_marks_sources`: the output contains `max_cost_usd = 3.0  # from phil.toml`.
- [ ] **Step 2:** Run the tests and confirm they fail.
- [ ] **Step 3:** Implement the tests' requirements. Update every caller of `load_config` to the new signature; the default `overrides=()` keeps existing calls valid.
- [ ] **Step 4:** Run the config tests, then the full suite.
- [ ] **Step 5: Commit:** `Layer the global config under phil.toml, with --set and value sources`

---

### Task 2: Model tiers

**Files:**
- Modify: `src/phil/config.py`, `src/phil/cli/main.py` (the check messages), `src/phil/chat/approval.py`.
- Test: `tests/test_config.py`.

**Interfaces:**
- Produces:
  - `TIERS = ("high", "low", "classifier")`.
  - `DEFAULT_TIERS: dict[str, str] = {"architect": "high", "critic": "high", "reviewer": "high", "orchestrator": "low", "implementer": "low", "tester": "low"}`.
  - `PhilConfig.tiers: dict[str, str]`: role to tier, validated so that roles are in `ROLES` and tiers are in `TIERS`.
  - `PhilConfig.tier_for(role) -> str`.
  - `PhilConfig.model_for(role) -> str`: resolved per the Global Constraints. If unset, it raises `ConfigError` with the exact missing-model message.
  - `PhilConfig.missing_models(roles) -> list[str]`, which resolves the same way.
  - `PhilConfig.tier_model(tier) -> str | None`, with the `classifier` → `low` fallback.
  - The `[models]` validator accepts `ROLES + TIERS` keys.
- Callers of `missing_models` print the exact missing-model message for each missing role, instead of the old wording. Find them with `grep -rn missing_models src`.

- [ ] **Step 1: Write the failing tests:**
  - `high` and `low` only: the architect gets `high` and the implementer gets `low`.
  - A role key beats its tier.
  - `[tiers] implementer = "high"` moves the implementer to `high`.
  - `classifier` falls back to `low`.
  - A legacy six-role config resolves each role to its own key.
  - With only `high` set, `missing_models(("implementer",))` returns `["implementer"]` and `model_for` raises with `(tier low)` in the message.
  - An unknown tier in `[tiers]` is rejected.
  - Update any existing test that expects the old wording.
- [ ] **Steps 2–4:** Red, then green, then the full suite.
- [ ] **Step 5: Commit:** `Resolve role models through high, low and classifier tiers`

---

### Task 3: Providers and model construction

**Files:**
- Create: `src/phil/agents/providers.py`.
- Modify: `src/phil/config.py` (the `providers` table and `missing_keys`), `src/phil/agents/factory.py`, `src/phil/agents/invoke.py` (build and prices), `src/phil/agents/pricing.py`, `src/phil/cli/main.py` (key check), `pyproject.toml` and `uv.lock`.
- Test: `tests/agents/test_providers.py` (create), `tests/test_config.py`, `tests/agents/test_factory.py`, `tests/agents/test_invoke*.py`.

**Interfaces:**
- **Config:**
  - `ProviderConfig(_Section)` with fields `kind: Literal["openai", "anthropic", "google", "openrouter"] | None = None`, `base_url: str | None = None`, `api_key_env: str | None = None`, `input_per_mtok: float | None = None`, `output_per_mtok: float | None = None`.
  - `PhilConfig.providers: dict[str, ProviderConfig]`.
- **`providers.py`:**
  - `ProviderSpec(name, kind, base_url, api_key_env, input_per_mtok, output_per_mtok)`, a frozen dataclass.
  - `BUILTIN_PROVIDERS: dict[str, ProviderSpec]` per the Global Constraints, plus `ALIASES = {"google_genai": "google"}`.
  - `resolve_provider(config: PhilConfig, name: str) -> ProviderSpec`:
    - aliases resolve first;
    - a user entry with a built-in name overrides only the fields it sets;
    - a user entry with a new name needs `kind`;
    - otherwise it raises `UnknownProvider(name)`, a `ConfigError` subclass.
  - `split_model(model: str) -> tuple[str, str]`: splits on the first `:`.
  - `build_chat_model(spec: ProviderSpec, model_name: str, timeout_s: int, environ: Mapping[str, str] = os.environ) -> BaseChatModel`. The imports inside it are lazy.
    - `openrouter`: keep today's `init_chat_model("openrouter:<name>", timeout=timeout_s*1000, max_retries=0)` path.
    - `openai`: `langchain_openai.ChatOpenAI(model=model_name, base_url=spec.base_url, api_key=environ.get(env) or "not-needed", timeout=timeout_s, max_retries=0)`. The placeholder key `"not-needed"` is only for keyless local servers, which ignore it.
    - `anthropic`: `langchain_anthropic.ChatAnthropic(model=..., base_url=..., api_key=..., timeout=timeout_s, max_retries=0)`.
    - `google`: `langchain_google_genai.ChatGoogleGenerativeAI(model=..., google_api_key=..., timeout=timeout_s, max_retries=0)`. Check the installed package's parameter names with ctx7, or by reading the source in `.venv`.
- **`PhilConfig.missing_keys(roles, environ)`:** reports, for each provider whose `api_key_env` is set but missing from `environ`, the exact missing-key message, grouping the roles that use it. An unknown provider surfaces as its own message, e.g. `phil` refuses with the unknown-provider message. Update the CLI caller to print these lines.
- **Factory:** `build_agent(spec, model, ..., timeout_s)` gains a keyword `provider: ProviderSpec`. `chat_model(model, timeout_s)` becomes a thin wrapper: `invoke_agent` resolves the provider via `resolve_provider(ctx.config, split_model(model)[0])` and passes it through. Remove `_PROVIDER_TIMEOUT_KWARGS` once `build_chat_model` covers it.
- **Prices:** `PriceBook.estimate` is unchanged. In `invoke`'s cost resolution, when a call has no reported cost and the provider has both `input_per_mtok` and `output_per_mtok` set, estimate `in/1e6*input_per_mtok + out/1e6*output_per_mtok` with source `estimated`. OpenRouter keeps using the price book. `ollama` estimates $0.
- **Dependencies:** run `uv add langchain-openai langchain-anthropic langchain-google-genai` at their latest versions, and raise the `pyproject.toml` floors to the locked versions (the user's standing rule: keep dependencies current). If a package conflicts with the pydantic floor, report NEEDS_CONTEXT instead of pinning it down.

- [ ] **Step 1: Write the failing tests** in `tests/agents/test_providers.py`:
  - built-ins resolve;
  - `google_genai` resolves as an alias of `google`;
  - `[providers.ollama] base_url = "http://box:11434/v1"` overrides only `base_url`;
  - a custom `lab` provider needs `kind` (a validation error without it);
  - an unknown name raises `UnknownProvider` with the exact message;
  - `build_chat_model` for `openai`, `anthropic`, `google` and `ollama` returns objects whose attributes show the right model, base URL, a timeout in seconds, and `max_retries == 0`. Use a fake environ with a dummy key. Skip a kind with `pytest.importorskip` if its package is missing;
  - openrouter's timeout is `timeout_s * 1000`.

  In `tests/test_config.py`:
  - `missing_keys` groups roles by provider;
  - `ollama` needs no key;
  - an unknown provider is reported.

  In the invoke tests:
  - a provider with prices produces an `estimated` cost with the expected value.
- [ ] **Steps 2–4:** Red, then green, then the full suite. The network guard must still pass: no test builds a model that calls out.
- [ ] **Step 5: Commit:** `Resolve any provider, including Ollama and custom endpoints`

---

### Task 4: `phil config`, `phil models check`, rejected raw text, `--set` on runs

**Files:**
- Create: `src/phil/agents/check.py`.
- Modify: `src/phil/cli/main.py`, `src/phil/agents/invoke.py` (rejected `raw`), `src/phil/run/launch.py`, `src/phil/run/worker.py`, `src/phil/store/db.py`, `src/phil/store/runs.py`, `src/phil/chat/controller.py`.
- Test: `tests/test_cli.py` or `tests/cli/test_config_command.py` (create), `tests/agents/test_check.py` (create), `tests/agents/test_invoke*.py`, `tests/run/test_worker.py`.

**Interfaces:**
- **`phil config`** prints `effective_toml(config)`. **`phil config --path`** prints `global: <path> (exists|missing)` and `repo: <path> (exists|missing)`.
- **`--set` on commands:**
  - The root command and `run` gain `--set` (repeatable, `list[str]`), which is passed to `load_config(..., overrides=...)`.
  - The chat controller keeps the overrides and passes them when it reloads config (`controller.py` around line 613).
  - `prepare_run(..., overrides: Sequence[str] = ())` stores them as JSON in a new `runs.config_overrides TEXT` column: add a migration, a `RunRecord` field defaulting to `None`, and allow updating it.
  - The worker loads the config with the run's stored overrides on every mode.
- **Checks** (`check.py`):
  - `CheckResult(label: str, model: str, ok: bool, seconds: float, detail: str)`.
  - `check_models(config, *, factory: AgentFactory | None = None, repo_root: Path) -> list[CheckResult]`. It makes one call per distinct target: each tier that has a model set, plus each role key that overrides its tier (label `role:<name>`).
  - Each call uses a lean agent spec with a tiny contract, `ModelCheck(ok: bool, echo: str)`, and a prompt asking it to return `ok=true` and echo a given word. It goes through the normal `invoke_agent` path, with `max_attempts` 1 and no retries, in a temporary `AgentContext`.
  - The detail is `returned text instead of the required structured output: "<first 120 chars>"`, or the error line.
- **`phil models check`:**
  - prints one line per result, in the form `✓ high  <model>  1.8s` or `✗ low  <model>  <detail>`;
  - exits 1 if any result failed;
  - prints a hint when no models are configured.
- **Rejected raw text:** in `invoke.py`, when validation fails with "no structured output was returned", set `raw` to the text content of the last AI message in `result["messages"]`, capped at 2,000 characters, instead of `None`.

- [ ] **Step 1: Write the failing tests:**
  - `phil config` shows sources;
  - `phil config --path` shows both files, with existence;
  - `phil --set run.max_cost_usd=5 config` shows `5.0  # from --set`;
  - `phil run plan.json --set run.max_cost_usd=5` stores the override on the run record, and a resumed worker loads it (use the scripted-worker test helpers);
  - `check_models` with a scripted factory reports success for `high` and `low` (one call each when they differ, one call when they are the same model);
  - a fake agent that returns no structured output gives ✗ with the text excerpt;
  - `phil models check` exits 1 on a failure;
  - a rejected-output record contains the last AI text as `raw`.
- [ ] **Steps 2–4:** Red, then green, then the full suite.
- [ ] **Step 5: Commit:** `Add phil config, phil models check and --set on runs`

---

### Task 5: Docs and follow-ups

**Files:**
- Modify: `README.md` (a "Configuration" section), `docs/superpowers/roadmap.md` (mark M2a implemented).
- Create: `docs/superpowers/plans/2026-09-30-phil-m2a-followups.md`.

**README:**
- The layer order, and where `~/.phil/config.toml` lives.
- A minimal global file (`[models] high`/`low`, and a key env var).
- A repo `phil.toml` that holds only project settings.
- `--set`.
- `phil config` and `phil config --path`.
- Tiers and the default mapping, plus the `[tiers]` and role overrides.
- Providers: the built-ins table, and a custom provider example for Ollama and vLLM.
- Prices.
- `phil models check`.
- Every claim must match the code.

**Follow-ups:** the M2b items (guided setup, keychain), plus any deferred review minors.

- [ ] Write the docs and run the full suite.
- [ ] **Commit:** `Document layered config, tiers and providers`

**Live check (user):**
1. Move `[models]` into `~/.phil/config.toml` using `high`/`low`, then run `phil config`.
2. Run `phil models check`.
3. Run the benchmark again with tiers.
