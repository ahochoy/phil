# Answers that work on every provider — design

Status: approved in conversation, 2026-10-22. Release work item "confirm the bring-your-own-provider
path (M2a)" in `docs/superpowers/roadmap.md`. This is the first of two specs from the provider
spike; the second covers prices and budgets.

## 1. Goal

A user who brings their own provider (Anthropic, OpenAI, Google, Ollama, or a custom
OpenAI-compatible endpoint) gets agents that return their answers, current models included, and
errors that tell them what to do. Nothing that works on OpenRouter today gets worse.

## 2. What the spike found (2026-10-21/22)

Live checks with the user's own keys, a throwaway `PHIL_HOME`, `phil models check` and small
probe scripts:

1. **Current Claude models reject a forced tool call.** `claude-opus-5-5`, `claude-sonnet-5-5` and
   `claude-fable-5-1` answer every agent call with `400 tool_choice: type "tool" and "any" are not
   supported for this model`, on Anthropic directly and through OpenRouter (Bedrock route).
   `claude-haiku-5-5`, `claude-opus-5` and `claude-haiku-4-5` pass. Unforced, `claude-opus-5-5`
   called the tool.
2. **The forcing comes from LangChain.** `create_agent` binds the structured-output tool with
   `tool_choice="any"` whenever a `ToolStrategy` is used (`langchain/agents/factory.py`, 1.4.3), with
   no option to turn it off. Phil always uses `ToolStrategy` (`src/phil/agents/factory.py`,
   `_tool_strategy`).
3. **Ollama models answer in text.** `phil models check` on `qwen3:8b` passed 1 of 2 runs and
   `gemma4:12b` 0 of 2: both wrote the right JSON as plain text, sometimes in a ```json fence.
   Ollama ignores the forced tool choice.
4. **`gpt-5.6-terra`, setup's suggested OpenAI `low`, fails its first call:** `Function tools with
   reasoning_effort are not supported for gpt-5.6-terra in /v1/chat/completions. To use function
   tools, use /v1/responses or set reasoning_effort to 'none'.` With `use_responses_api=True` the
   tool call works.
5. **Error messages.** A bad Google key is reported as `refused` with raw JSON as the headline
   (`GoogleInvalidRequestError`, `API_KEY_INVALID`). Ollama not running is reported as `network`,
   "Check your connection". The wrong-key hint after an auth failure is built from the model's
   prefix (`failures.py` `_KEY_ENV`), so it is wrong for custom providers, overridden
   `api_key_env`, and Ollama (which takes no key). A quota error with no HTTP status (Google's
   rate-limit class) is classified `busy` before the quota phrases are checked.
6. **Fine as is:** images in tool results (OpenAI, Anthropic, Google, Ollama); all 11 output
   contracts accepted as tool schemas by Gemini and OpenAI; Ollama runs each model at its full
   context window; OpenAI and Anthropic auth errors carry status 401.

## 3. Decisions (user, 2026-10-22)

- Two specs: this one (answers, OpenAI, setup suggestions, error messages), then prices and budgets.
- Never force a tool call; when a model answers in text, accept valid JSON or remind it.
- Live acceptance: `phil models check` across the spike's models plus Ollama, and one small
  quick-path run per provider. The user runs the OpenRouter benchmark.

## 4. Design

### 4.1 No forced tool call

`build_chat_model` (`src/phil/agents/providers.py`) returns models whose `bind_tools` turns a
forced choice (`"any"`, `"required"`, or a named tool) into an unforced one (`"auto"`), for every
provider kind, the existing `PhilChatOpenRouter` included. Any other `tool_choice`, and every
other argument, passes through unchanged. The rule lives at model construction, so every harness
(lean, light, deep, sub-agents) gets it without each one remembering.

### 4.2 A text answer gets a second chance

A new middleware, installed on every agent Phil builds (lean, light and deep), runs after each
model call. When the call ended with an AI message that has no tool calls and the state has no
structured response:

1. **Text that is the answer.** If the message's text holds one JSON object (bare, or inside one
   ```` ```json ```` / ```` ``` ```` fence) that validates as the agent's output contract, Phil uses
   it as the structured response and ends the loop. `invoke_agent`'s own validation and evidence
   checks then run exactly as for a tool-call answer.
2. **A reminder.** Otherwise Phil appends one human message — `Return your answer by calling the
   <ToolName> tool.` (the structured-output tool's name) — and goes back to the model. At most
   **2** reminders per agent attempt.
3. **No answer.** After the second reminder, the loop ends with no structured response, and
   `invoke_agent` treats it as today: an `invalid` outcome, the answer's text kept in
   `<name>.rejected.json`, and the whole-attempt retry.

Reminders are model calls: they count toward the agent's model-call cap (`CallBudget`), so a
reminder never takes an agent past it, and the cap's hard stop still applies. Telemetry records
how many reminders an attempt used (a new `reminders` column on `telemetry`, default 0).

This replaces `end_on_text`: the flag and `_end_on_text_middleware` are removed, and the agents
that set it (intake, critic, reviewer, route, memory, `model_check`) get the reminder behaviour
like every other agent.

### 4.3 `phil models check`

The check uses the same path real agents use, so a model passes only if Phil can actually get an
answer from it. A pass that needed a reminder is shown as `✓ <label>  <model>  <secs>s (needed a
reminder)`: usable, but a hint that the model is a weak fit. A failure after two reminders keeps
today's "returned text instead of the required structured output" detail.

### 4.4 OpenAI's Responses API

An `openai`-kind provider whose `base_url` is unset or has host `api.openai.com` is built with
`use_responses_api=True`. Every other `openai`-kind provider (Ollama, custom endpoints, vLLM,
llama.cpp) keeps the Chat Completions API, which is what OpenAI-compatible servers serve.
Timeouts and `max_retries=0` are unchanged. Phil's usage and cost collection reads
`usage_metadata` and `response_metadata`, which LangChain fills for both APIs; a test pins that.

### 4.5 Setup

- **Suggestions** (`src/phil/setup/suggestions.py`), each confirmed live in acceptance:
  - Anthropic: `high = anthropic:claude-opus-5-5`, `low = anthropic:claude-haiku-5-5`.
  - Google (new): `high = google:gemini-4-argon`, `low = google:gemini-3.8-flash`.
  - OpenAI and OpenRouter: unchanged.
- **Ollama models** (`src/phil/setup/catalog.py`) carry their `capabilities` from `/api/tags`.
  Setup marks one without `tools` as "can't call tools — won't work with Phil" and doesn't offer
  it as a default. An Ollama too old to report capabilities shows its models unmarked.

### 4.6 Error messages (`src/phil/agents/failures.py`, `retry.py`)

- **Wrong key.** The hint names the provider's real `api_key_env`, from `resolve_provider` with the
  caller's config, and drops `_KEY_ENV`. A provider with no `api_key_env` gets no
  `phil keys set` hint. Both the chat and the run worker pass the provider name through.
- **Google's bad key.** A 400 whose body says `API_KEY_INVALID` (or "API key not valid") is an
  `auth` failure.
- **A local server that isn't running.** A connection refused by a provider whose `base_url` host
  is `localhost`, `127.0.0.1` or `::1` is not retried, and says `Couldn't reach <provider> at
  <base_url>.` with the action `Is \`ollama serve\` running?` for Ollama and `Is the server at
  <base_url> running?` otherwise.
- **Quota first.** The quota phrases are checked before the `busy` class-name match and regardless
  of HTTP status, so an exhausted quota is `quota`, not retried.

## 5. Tests

Offline, each written to fail first:

- Every provider kind's model sends `tool_choice="auto"` when bound with `"any"`, `"required"` or a
  tool name, and passes other values through (`tests/agents/test_providers.py`).
- Middleware, with a fake model: a fenced-JSON answer is accepted; a bare JSON answer is accepted;
  JSON that fails the contract leads to a reminder; a reminder then a tool call succeeds; two
  reminders then text ends with no answer; reminders count toward the model-call cap; the
  `reminders` count reaches telemetry.
- A deep agent and a light agent each recover from a text answer through the middleware.
- `phil models check` prints "(needed a reminder)".
- Responses API on for no `base_url` and for `https://api.openai.com/v1`, off for Ollama and a
  custom `base_url`; usage and cost collected from a Responses-API message.
- Each error case in 4.6, from exceptions built the way the SDKs raise them.
- Setup: the new suggestions; an Ollama model without `tools` is marked and not defaulted.

Live acceptance, run by Claude with the user's stored keys and a throwaway `PHIL_HOME` (never
`pytest -m live` or `-m bench`):

- `phil models check` passes for `openai:gpt-5.6-sol`, `openai:gpt-5.6-terra`,
  `anthropic:claude-opus-5-5`, `anthropic:claude-sonnet-5-5`, `anthropic:claude-haiku-5-5`,
  `anthropic:claude-opus-5`, `google:gemini-4-argon`, `google:gemini-3.8-flash`,
  `openrouter:anthropic/claude-sonnet-5-5`, `ollama:qwen3:8b` and `ollama:gemma4:12b` (a reminder
  allowed).
- One small quick-path run (a one-line change in a scratch repo) completes on Anthropic, OpenAI,
  Google and Ollama, using setup's suggested models (Ollama: `qwen3:8b`).
- The user runs the OpenRouter benchmark; pass rates and cost per task stay within the last
  baseline's noise.

## 6. Out of scope

- Prices, cost budgets, and prompt caching for direct Anthropic: the second spec.
- Provider-native JSON output modes.
- README provider documentation (polish pass) and setup's other gaps (custom providers always
  written as `kind = "openai"`; the classifier step defaulting to TypeSafe).
- Gemini ignoring `additionalProperties`: the contract validation already rejects extra fields.

## 7. Docs

`docs/guide.md`: the providers section notes that OpenAI uses the Responses API, that local models
need tool calling (with the setup marker), and what "(needed a reminder)" means in `phil models
check`. The roadmap's release item records this spec.
