# Classifier Setup with Decision Models — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `phil setup` recommend Jev as the classifier and offer it through OpenRouter (no new key) or TypeSafe, with `d1` as an experimental option.

**Architecture:**
- **The OpenRouter endpoint:** a built-in provider, `openrouter_decisions` (kind `systemone`), points the existing Jev adapter at OpenRouter's TypeSafe-compatible System One endpoint.
- **The adapter** also records the cost OpenRouter reports.
- **The setup classifier step:**
  - offers four sources;
  - pre-selects Jev through OpenRouter or through TypeSafe, depending on the main provider;
  - reuses the OpenRouter key;
  - checks the classifier actually chosen.

**Tech Stack:** Python 3.14, httpx (MockTransport in tests), pytest, offline only.

**Spec:** `docs/superpowers/specs/2026-10-06-phil-classifier-setup-design.md`

## Global Constraints

- **Editing files:** use only the Edit or Write tools. Never edit through python, perl, sed, heredocs or printf in Bash.
- **Commits:** write the message to a file with Write, then `git commit -F <file>`. The message ends with a blank line and then exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Bash command lines:** keep the words "keychain" and "credentials" out of them.
- **Hooks and guards:** never work around one. If you are blocked, stop and report BLOCKED.
- **Secrets:** never read or print `.env` files or key values. Never send a real request; tests use `httpx.MockTransport`.
- **Tests:**
  - never run `-m live` or `-m bench`;
  - iterate with `uv run pytest <paths> -q -n 0`;
  - run the full `uv run pytest -q` once, at the end of each task.
- **The provider** (spec §3.1): `ProviderSpec("openrouter_decisions", SYSTEMONE, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", None, None)`.
- **Model ids** (spec §3.1), as module constants in `phil/setup/flow.py`:

  | Constant | Value |
  |---|---|
  | `JEV_OPENROUTER` | `"openrouter_decisions:typesafe/jev-1.13"` |
  | `JEV_TYPESAFE` | `"typesafe:jev-latest"` (the existing `CLASSIFIER_MODEL`, renamed) |
  | `D1_OPENROUTER` | `"openrouter_decisions:liquid/d1"` |

- **Option labels in the classifier step**, verbatim (spec §3.2):
  1. `Keep <current>`, only when the global file sets a classifier;
  2. `Jev through OpenRouter (recommended; uses your OpenRouter key)`;
  3. `Jev through TypeSafe (recommended without OpenRouter; needs TYPESAFE_API_KEY)`;
  4. `d1 through OpenRouter (experimental: routing thresholds were tuned for Jev)`;
  5. `Your low model`.
- **Pre-selection:** `Keep <current>` if there is one; otherwise Jev through OpenRouter when the main provider is `openrouter`; otherwise Jev through TypeSafe.
- **Check lines:** `✓ classifier  <model>`, or `✗ classifier  <model>  <reason>`. On failure, the choice prompt is `<model> failed the check.`, with the options `Use your low model instead` (the default) and `Keep <model> anyway`.

## Review Focus

1. **A main provider other than OpenRouter whose user picks Jev through OpenRouter:** setup asks for `OPENROUTER_API_KEY` once, and the key is saved only at the end. Tested in Task 2.
2. **OpenRouter as the main provider, with the key coming from the environment:** no key prompt at the classifier step. Tested in Task 2.
3. **A rerun with a current `openrouter_decisions` classifier:** `Keep <current>` is pre-selected, so Enter changes nothing. Tested in Task 2.
4. **An OpenRouter response with no `usage.cost`:** the cost is unknown, never estimated from a missing price. Tested in Task 1.
5. **A refused OpenRouter decision call in `phil models check`:** the message names OpenRouter and `OPENROUTER_API_KEY`, not TypeSafe. Tested in Task 1.

## Rulings made while planning

- **R1, model ids:** Jev uses `typesafe/jev-1.13`, the id OpenRouter's Decisions docs use. OpenRouter's System One curl example uses the bare `jev-1.13`. The live check after the plan confirms which works: if `typesafe/jev-1.13` fails with an unknown-model error, change `JEV_OPENROUTER` to `openrouter_decisions:jev-1.13`. The same goes for `d1`.
- **R2, cost only in the benchmark:** chat routing doesn't record a Jev call's cost today. The adapter now carries a reported cost on `Usage.cost_usd`. The classifier benchmark prefers it over an estimate. Recording routing cost in the chat stays out of scope.
- **R3, fixing the existing setup tests:** many existing setup tests end on Enter at the classifier step, which used to mean "your low model".
  - The test helper `setup()` now defaults `classifier_check` to a stub that passes.
  - Each test that asserts no classifier gets an explicit `Your low model` answer.
  - Each test that only cared about other steps keeps its Enter, so it now writes Jev through OpenRouter (the new default). Where such a test asserts the exact `models` dict, add the classifier key.

---

### Task 1: The `openrouter_decisions` provider, reported cost, and the check's wording

**Files:**
- Modify: `src/phil/agents/providers.py` (the `BUILTIN_PROVIDERS` entry).
- Modify: `src/phil/routing/jev.py` (`parse_response` reads `usage.cost`).
- Modify: `src/phil/agents/check.py:116-121` (`_jev_detail` names the provider).
- Modify: `tests/live/bench/classify/run.py`, in `_run_jev`: a reported cost takes precedence over an estimate.
- Create: `tests/routing/fixtures/jev_openrouter_ok.json`.
- Test: `tests/routing/test_jev.py`, `tests/agents/test_check.py`, `tests/agents/test_providers.py`, `tests/live/bench/classify/test_run_offline.py`.

**Interfaces:**
- Produces:
  - `BUILTIN_PROVIDERS["openrouter_decisions"]`;
  - `Usage.cost_usd` filled from `usage.cost` when present;
  - `PROVIDER_LABELS: dict[str, str]` in `check.py`, mapping `"typesafe"` to `"TypeSafe"` and `"openrouter_decisions"` to `"OpenRouter"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/routing/fixtures/jev_openrouter_ok.json`. It's the TypeSafe fixture's answers in OpenRouter's documented System One envelope, which adds `id`, `provider` and `usage.cost`:

```json
{
  "id": "gen-dec-1789738314-X5e5eKGQdvR9rblyX250",
  "model": "typesafe/jev-1.13-20260917",
  "provider": "TypeSafe",
  "answers": {
    "task_class": {
      "type": "choice",
      "choice": "simple_change",
      "probabilities": {"question": 0.02, "diagnosis": 0.01, "small_operation": 0.05, "simple_change": 0.86,
                        "focused_fix": 0.03, "feature": 0.01, "refactor": 0.005, "design": 0.005,
                        "broad_project": 0.005, "other": 0.005},
      "confidence": 0.81
    },
    "needs_detail": {"type": "noul", "noul": 0.07}
  },
  "usage": {"input_tokens": 412, "output_tokens": 3, "cost": 0.0000173}
}
```

Append to `tests/routing/test_jev.py`:

```python
OPENROUTER_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "jev_openrouter_ok.json").read_text())
OPENROUTER_SPEC = BUILTIN_PROVIDERS["openrouter_decisions"]
OPENROUTER_ENV = {"OPENROUTER_API_KEY": "sk-or-TESTSECRET0123456789"}


def test_openrouter_decisions_posts_to_openrouters_systemone_endpoint_and_reads_its_cost():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json=OPENROUTER_FIXTURE)

    judgement = judge_jev(
        OPENROUTER_SPEC, "typesafe/jev-1.13", STATE, timeout_s=5, environ=OPENROUTER_ENV, transport=transport(handler)
    )
    assert seen["url"] == "https://openrouter.ai/api/v1/systemone"
    assert seen["auth"] == "Bearer sk-or-TESTSECRET0123456789"
    assert judgement.task_class == "simple_change" and judgement.source == "jev"
    assert judgement.usage.cost_usd == pytest.approx(0.0000173)


def test_a_typesafe_response_without_a_cost_leaves_it_unknown():
    judgement = judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV,
                          transport=transport(lambda r: httpx.Response(200, json=FIXTURE)))
    assert judgement.usage.cost_usd is None


def test_a_malformed_reported_cost_is_malformed():
    body = {**OPENROUTER_FIXTURE, "usage": {"input_tokens": 1, "output_tokens": 1, "cost": "lots"}}
    with pytest.raises(JevError, match="malformed"):
        judge_jev(OPENROUTER_SPEC, "typesafe/jev-1.13", STATE, timeout_s=5, environ=OPENROUTER_ENV,
                  transport=transport(lambda r: httpx.Response(200, json=body)))
```

Append to `tests/agents/test_providers.py`:

```python
def test_openrouter_decisions_is_a_builtin_systemone_provider_sharing_the_openrouter_key():
    from phil.agents.providers import SYSTEMONE

    spec = BUILTIN_PROVIDERS["openrouter_decisions"]
    assert (spec.kind, spec.base_url, spec.api_key_env) == (SYSTEMONE, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY")
    assert (spec.input_per_mtok, spec.output_per_mtok) == (None, None)  # cost comes from the response


def test_openrouter_decisions_is_allowed_only_as_the_classifier():
    from phil.config import PhilConfig

    assert PhilConfig(models={"classifier": "openrouter_decisions:typesafe/jev-1.13"}).is_systemone("classifier")
    with pytest.raises(ValueError):
        PhilConfig(models={"low": "openrouter_decisions:typesafe/jev-1.13"})
```

`test_builtins_resolve` pins the set of built-in providers, so add `"openrouter_decisions"` to its expected set.

Append to `tests/agents/test_check.py`:

```python
def test_check_pings_an_openrouter_decisions_classifier_at_openrouter(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    seen = {}
    ok = {"answers": {"ping": {"type": "choice", "choice": "yes", "probabilities": {"yes": 1.0}, "confidence": 1.0}},
          "usage": {"input_tokens": 1, "output_tokens": 1, "cost": 0.0}}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=ok)

    config = PhilConfig(models={"classifier": "openrouter_decisions:typesafe/jev-1.13"})
    results = check_models(config, repo_root=tmp_path, jev_transport=httpx.MockTransport(handler))
    [result] = [r for r in results if r.model == "openrouter_decisions:typesafe/jev-1.13"]
    assert result.ok and seen["url"] == "https://openrouter.ai/api/v1/systemone"


def test_a_refused_openrouter_decision_call_names_openrouter_and_its_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    config = PhilConfig(models={"classifier": "openrouter_decisions:typesafe/jev-1.13"})
    results = check_models(config, repo_root=tmp_path,
                           jev_transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    [result] = [r for r in results if r.model == "openrouter_decisions:typesafe/jev-1.13"]
    assert result.detail == "OpenRouter refused the request (http 401): check OPENROUTER_API_KEY."
```

Append to `tests/live/bench/classify/test_run_offline.py`. Mirror the existing jev-backend test that monkeypatches `_jev_transport` and builds `PhilConfig(models={"classifier": "typesafe:jev-latest"})`. Then:

```python
def test_the_jev_backend_prefers_a_reported_cost(monkeypatch):
    from phil.config import PhilConfig

    body = {**JEV_FIXTURE, "usage": {"input_tokens": 412, "output_tokens": 3, "cost": 0.000123}}
    _jev_transport(monkeypatch, body)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-TESTSECRET0123456789")
    monkeypatch.setattr(run, "phil_sha", lambda: "test")
    config = PhilConfig(models={"classifier": "openrouter_decisions:typesafe/jev-1.13"})

    [record] = run.run_backend("jev", [CASE], config)

    assert record["cost_usd"] == pytest.approx(0.000123)
```

Use the existing `_jev_transport` helper and `CASE` constant as they are in that file. If `_jev_transport` patches a TypeSafe-specific URL, read it and adapt the test accordingly.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/routing/test_jev.py tests/agents/test_providers.py tests/agents/test_check.py tests/live/bench/classify/test_run_offline.py -q -n 0`
Expected: FAIL. `openrouter_decisions` doesn't exist and the cost isn't read.

- [ ] **Step 3: Implement**

In `src/phil/agents/providers.py` `BUILTIN_PROVIDERS`, after the `typesafe` entry:

```python
    # OpenRouter's TypeSafe-compatible System One endpoint: Jev and other decision models with the
    # OpenRouter key. No built-in price: the response reports each call's cost.
    "openrouter_decisions": ProviderSpec(
        "openrouter_decisions", SYSTEMONE, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", None, None
    ),
```

In `src/phil/routing/jev.py` `parse_response`, extend the usage block to read an optional cost:

```python
    if isinstance(raw_usage, dict):
        input_tokens = _token_count(raw_usage.get("input_tokens", 0))
        output_tokens = _token_count(raw_usage.get("output_tokens", 0))
        cost = raw_usage.get("cost")
        if cost is not None and (isinstance(cost, bool) or not isinstance(cost, int | float) or cost < 0):
            raise JevError("malformed")
        usage = Usage(input_tokens, output_tokens, float(cost) if cost is not None else None)
```

This replaces the existing `try/except JevError: raise` block, which only re-raised.

In `src/phil/agents/check.py`:

```python
# How a decision-model provider is named in a check's failure message.
PROVIDER_LABELS = {"typesafe": "TypeSafe", "openrouter_decisions": "OpenRouter"}


def _jev_detail(provider: ProviderSpec, exc: JevError) -> str:
    label = PROVIDER_LABELS.get(provider.name, provider.name)
    if exc.reason == "missing key":
        return missing_key_message(provider.name, provider.api_key_env or "", ["classifier"])
    if exc.reason in ("http 401", "http 403"):
        return f"{label} refused the request ({exc.reason}): check {provider.api_key_env}."
    return f"{label} didn't answer ({exc.reason})."
```

In `tests/live/bench/classify/run.py` `_run_jev`, where `cost_usd = estimate_cost(spec, input_tokens, output_tokens)`, use:

```python
                cost_usd = (
                    judgement.usage.cost_usd
                    if judgement.usage.cost_usd is not None
                    else estimate_cost(spec, input_tokens, output_tokens)
                )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run the command from Step 2.
Expected: PASS. The existing TypeSafe tests, including the "TypeSafe refused…" wording, still pass.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`. Commit message: `Add openrouter_decisions: Jev and other decision models through OpenRouter's System One endpoint`, plus the trailer.

---

### Task 2: The setup classifier step and the README

**Files:**
- Modify: `src/phil/setup/flow.py`. Add the constants. Update `_default_classifier_check`, the `classifier_check` signature, the `run_setup` call, and `_classifier_step`.
- Modify: `tests/setup/test_flow.py`. Update the helper and the existing classifier tests, and add new tests.
- Modify: `README.md`. Update the classifier section and the providers table, and remove "The `classifier` tier isn't part of setup yet".

**Interfaces:**
- Consumes: `BUILTIN_PROVIDERS["openrouter_decisions"]` (Task 1).
- Produces:
  - `run_setup(..., classifier_check: Callable[[str], str | None] | None = None)`, where the check takes the chosen classifier model string and returns `None` or a reason;
  - the constants `JEV_OPENROUTER`, `JEV_TYPESAFE` and `D1_OPENROUTER`.

- [ ] **Step 1: Write the failing tests**

In `tests/setup/test_flow.py`:

1. **Change the `setup()` helper.** When the caller doesn't pass `classifier_check`, it uses a stub that passes:

```python
def setup(answers, tmp_path, *, check=None, ollama=None, config=None, catalog=None, **kwargs):
    io = ScriptedSetupIO(answers)
    check = check or FakeCheck()
    kwargs.setdefault("classifier_check", lambda model: None)  # never a real call
    wrote = run_setup(
        io,
        config=config or load_config(tmp_path),
        check=check,
        catalog=catalog or (lambda: CATALOG),
        ollama=ollama or FakeOllama(),
        **kwargs,
    )
    return wrote, io, check
```

2. **Update every existing `classifier_check=lambda: ...` to take the model**, for example `lambda model: None` and `lambda model: "http 401"`. In the classifier-step tests, replace the answer `"TypeSafe"` with `"Jev through TypeSafe"`. Replace `"Keep TypeSafe"` with `"Keep typesafe:jev-latest"`. Replace expected lines naming `TypeSafe Jev failed the check.` with `typesafe:jev-latest failed the check.`.

3. **Apply ruling R3 to tests that end with Enter at the classifier step.** Read each one. If it asserts no classifier, or asserts the exact `models` dict without one, either append a `"Your low model"` answer or add `"classifier": JEV_OPENROUTER` to the expected dict, whichever matches the test's intent. Tests about other steps should keep their meaning.

4. **Append the new tests:**

```python
from phil.setup.flow import D1_OPENROUTER, JEV_OPENROUTER, JEV_TYPESAFE


def classifier_options(io):
    start = io.lines.index("Route requests with a fast classifier?")
    return [line for line in io.lines[start + 1:start + 6] if line.startswith("  ")]


def test_on_openrouter_the_classifier_step_lists_the_sources_and_preselects_jev_through_openrouter(tmp_path, memory_keyring):
    seen = []
    wrote, io, _ = setup(["", SECRET, "", "", ""], tmp_path, classifier_check=lambda model: seen.append(model))
    assert wrote is True
    assert classifier_options(io) == [
        "  1. Jev through OpenRouter (recommended; uses your OpenRouter key)",
        "  2. Jev through TypeSafe (recommended without OpenRouter; needs TYPESAFE_API_KEY)",
        "  3. d1 through OpenRouter (experimental: routing thresholds were tuned for Jev)",
        "  4. Your low model",
    ]
    assert seen == [JEV_OPENROUTER]  # Enter took the pre-selected option, and the check used it
    assert f"✓ classifier  {JEV_OPENROUTER}" in io.lines
    assert written(tmp_path).models["classifier"] == JEV_OPENROUTER
    assert memory_keyring.store == {(SERVICE, "OPENROUTER_API_KEY"): SECRET}  # one key, asked once
    assert sum(1 for kind, prompt in io.prompts if kind == "secret") == 1


def test_off_openrouter_jev_through_typesafe_is_preselected(tmp_path, memory_keyring):
    # Anthropic as the main provider: Enter at the classifier step takes Jev through TypeSafe.
    wrote, io, _ = setup(["Anthropic", SECRET, "", "", "", TYPESAFE_SECRET], tmp_path)
    assert wrote is True
    assert written(tmp_path).models["classifier"] == JEV_TYPESAFE
    assert memory_keyring.store[(SERVICE, "TYPESAFE_API_KEY")] == TYPESAFE_SECRET


def test_off_openrouter_choosing_jev_through_openrouter_asks_for_the_openrouter_key_once(tmp_path, memory_keyring):
    or_secret = "sk-or-TESTSECRET-classifier"
    wrote, io, _ = setup(["Anthropic", SECRET, "", "", "Jev through OpenRouter", or_secret], tmp_path)
    assert wrote is True
    assert written(tmp_path).models["classifier"] == JEV_OPENROUTER
    assert memory_keyring.store[(SERVICE, "OPENROUTER_API_KEY")] == or_secret
    assert all(or_secret not in line for line in io.lines)


def test_an_openrouter_key_from_the_environment_is_not_asked_for(tmp_path, monkeypatch, memory_keyring):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    wrote, io, _ = setup(["", "", "", ""], tmp_path)
    assert wrote is True
    assert written(tmp_path).models["classifier"] == JEV_OPENROUTER
    assert not any(kind == "secret" for kind, prompt in io.prompts)


def test_d1_can_be_chosen_and_is_checked(tmp_path, memory_keyring):
    seen = []
    wrote, io, _ = setup(["", SECRET, "", "", "d1"], tmp_path, classifier_check=lambda model: seen.append(model))
    assert wrote is True and seen == [D1_OPENROUTER]
    assert written(tmp_path).models["classifier"] == D1_OPENROUTER


def test_a_failed_openrouter_check_falls_back_to_the_low_model_by_default(tmp_path, memory_keyring):
    wrote, io, _ = setup(["", SECRET, "", "", "", ""], tmp_path, classifier_check=lambda model: "http 401")
    assert wrote is True
    assert f"✗ classifier  {JEV_OPENROUTER}  http 401" in io.lines
    assert f"{JEV_OPENROUTER} failed the check." in io.lines
    assert "classifier" not in written(tmp_path).models


def test_a_rerun_with_an_openrouter_classifier_keeps_it_on_enter(tmp_path, memory_keyring):
    memory_keyring.store[(SERVICE, "OPENROUTER_API_KEY")] = SECRET
    global_config_path().parent.mkdir(parents=True)
    global_config_path().write_text(
        '[models]\nhigh = "openrouter:openai/gpt-6-sol"\nlow = "openrouter:openai/gpt-6-luna"\n'
        f'classifier = "{JEV_OPENROUTER}"\n'
    )
    seen = []
    wrote, io, _ = setup(["", "", "", "", ""], tmp_path, classifier_check=lambda model: seen.append(model))
    assert wrote is True
    assert classifier_options(io)[0] == f"  1. Keep {JEV_OPENROUTER}"
    assert seen == []  # keeping the current classifier needs no new check
    assert written(tmp_path).models["classifier"] == JEV_OPENROUTER


def test_the_default_classifier_check_pings_the_chosen_provider(monkeypatch):
    from phil.config import PhilConfig
    from phil.setup import flow

    seen = {}

    def fake_ping(spec, model_name, *, timeout_s):
        seen["call"] = (spec.name, model_name)

    monkeypatch.setattr(flow, "ping_jev", fake_ping)
    assert flow._default_classifier_check(PhilConfig(), JEV_OPENROUTER) is None
    assert seen["call"] == ("openrouter_decisions", "typesafe/jev-1.13")
```

Check the test file's answer layout before relying on these sequences. Read `test_a_fresh_openrouter_setup_stores_the_key_writes_the_tiers_and_checks_once` (answers `["", SECRET, "", "", ""]`) and a non-OpenRouter test such as the Anthropic or OpenAI path. The five steps are:
1. provider;
2. key;
3. high model;
4. low model;
5. classifier.

Adjust the scripted answers in the new tests to the real prompt order for each path, especially for providers whose model step differs. Keep every assertion. `ScriptedSetupIO.prompts` records `(kind, prompt)`; secrets come through `secret`.

Update `test_the_default_classifier_check_reports_an_unsendable_key_without_it` to call `_default_classifier_check(PhilConfig(), JEV_TYPESAFE)`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/setup -q -n 0`
Expected: FAIL. There are no constants yet, and the labels and the check signature are the old ones.

- [ ] **Step 3: Implement**

In `src/phil/setup/flow.py`:

- **Constants:** replace `CLASSIFIER_MODEL = "typesafe:jev-latest"` with:

```python
JEV_TYPESAFE = "typesafe:jev-latest"
JEV_OPENROUTER = "openrouter_decisions:typesafe/jev-1.13"
D1_OPENROUTER = "openrouter_decisions:liquid/d1"
# The classifier step's sources (spec 2026-10-06 §3.2): (label, model, key var, key provider name).
CLASSIFIER_SOURCES: list[tuple[str, str, str, str]] = [
    ("Jev through OpenRouter (recommended; uses your OpenRouter key)", JEV_OPENROUTER, "OPENROUTER_API_KEY", "openrouter"),
    ("Jev through TypeSafe (recommended without OpenRouter; needs TYPESAFE_API_KEY)", JEV_TYPESAFE, "TYPESAFE_API_KEY", "typesafe"),
    ("d1 through OpenRouter (experimental: routing thresholds were tuned for Jev)", D1_OPENROUTER, "OPENROUTER_API_KEY", "openrouter"),
]
LOW_MODEL = "Your low model"
```

  Update any other `CLASSIFIER_MODEL` references in `src/` and `tests/` to `JEV_TYPESAFE`. Find them with `grep -rn CLASSIFIER_MODEL src tests`.

- **`_default_classifier_check`:**

```python
def _default_classifier_check(config: PhilConfig, model: str) -> str | None:
    """Pings the chosen decision model through its own provider with `ping_jev`; `None` if it
    answered, else the reason."""
    provider = resolve_provider(config, split_model(model)[0])
    try:
        ping_jev(provider, split_model(model)[1], timeout_s=config.routing.jev_timeout_s)
    except JevError as exc:
        return exc.reason
    return None
```

- **`run_setup`:**
  - Its `classifier_check` parameter becomes `Callable[[str], str | None] | None = None`.
  - The default stays `partial(_default_classifier_check, config)`; it now takes the model as its remaining argument.
  - The call becomes `_classifier_step(io, config, target, provider, run_classifier_check, pending, to_save)`. The main `provider` is the new argument.

- **`_classifier_step`:**

```python
def _classifier_step(
    io: SetupIO,
    config: PhilConfig,
    target: Path,
    provider: _Provider,
    classifier_check: Callable[[str], str | None],
    pending: dict[str, str],
    to_save: list[tuple[str, str]],
) -> dict[str, str]:
    """Offer a decision model for routing (spec 2026-10-06 §3.2). Jev is recommended: through
    OpenRouter when that's the main provider (its key is reused), else through TypeSafe. Empty
    when the user keeps routing with the low model; `{"classifier": <model>}` when one is chosen
    and kept. A key entered here is held in memory like every other key, saved only at the end."""
    current = config.models.get("classifier") if config.sources.get("models.classifier") == str(target) else None
    labels = ([f"Keep {current}"] if current else []) + [label for label, *_ in CLASSIFIER_SOURCES] + [LOW_MODEL]
    offset = 1 if current else 0
    default = 0 if current else (0 if provider.name == "openrouter" else 1)
    index = io.choose("Route requests with a fast classifier?", labels, default=default)
    choice = labels[index]
    if current and index == 0:
        return {}
    if choice == LOW_MODEL:
        if current:
            io.say(f"models.classifier = {current} stays in {target}; remove it there to route with your low model.")
        return {}
    _, model, var, key_provider = CLASSIFIER_SOURCES[index - offset]
    # The main provider's key step already covered this key (entered, stored or exported).
    if not (var == provider.api_key_env and (var in pending or key_source(var) is not None)):
        key = _key_step(io, _Provider(key_provider, var))
        if key is not None:
            pending[var] = key
            to_save.append((var, key_provider))
    with pending_keys(pending):
        reason = classifier_check(model)
    if reason is not None:
        io.say(f"✗ classifier  {model}  {reason}")
        if io.choose(f"{model} failed the check.", ["Use your low model instead", f"Keep {model} anyway"]) == 0:
            return {}
    else:
        io.say(f"✓ classifier  {model}")
    return {"classifier": model}
```

  Note on the "Keep current" path: today, choosing "Keep" returns `{}`, and the existing file's value stays because `write_global_config` keeps other keys. Confirm that against `write.py` and the existing rerun test, and keep that behaviour.

In `README.md`:
- Remove the sentence "The `classifier` tier isn't part of setup yet and has no suggestion; set it by hand under `[models]` until it's wired up in M3."
- In the providers table, add the row ``| `openrouter_decisions` | systemone | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |``.
- In the Jev/classifier section, next to the "Jev is the recommended classifier" paragraph, add: "`phil setup` offers it: through OpenRouter (`openrouter_decisions:typesafe/jev-1.13`, using your OpenRouter key), or through TypeSafe (`typesafe:jev-latest`, with a `TYPESAFE_API_KEY`). It also offers Liquid's `d1` through OpenRouter as an experimental option: the routing thresholds were tuned for Jev."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/setup tests/cli -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`. Commit message: `Setup recommends Jev as the classifier, through OpenRouter or TypeSafe, with d1 as an option`, plus the trailer.

---

## After the plan: the live check (the user)

Point the classifier at `openrouter_decisions:typesafe/jev-1.13` by running `phil setup` and choosing Jev through OpenRouter. Then run `phil models check`. It should print `✓ classifier openrouter_decisions:typesafe/jev-1.13`.

If it fails with an unknown-model error, apply ruling R1: switch to `jev-1.13`.
