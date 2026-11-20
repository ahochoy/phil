# Phil: record and salvage an agent's rejected answer

Date: 2026-11-19. Status: approved (short design).

## Why

In a live run on 2026-11-19 (repo `hochoy-biz`, every agent on `openrouter:inclusionai/ling-3.0-flash`),
the architect failed twice with `ContractViolation: architect returned invalid output: no structured
output was returned`. Converse failed the same way. Each attempt used its full call budget:
- 10 calls with tools,
- then 2 grace calls where only the answer tool was available,
- and still no usable answer.

The cost was about $0.003, so this was not a budget problem or a timeout. Phil records only
`{"raw": "", "problems": [...]}`, so nobody can tell whether the model wrote broken JSON, plain
text, or nothing at all.

The user's position: Phil's thesis is to be cost-efficient and deliberate, so escalating to a more
expensive model is a last resort, and never a default. First make the failure visible, and rescue
answers that are almost right.

## Goals

1. **Record.** A rejected answer's file shows what the model actually did at the end.
2. **Salvage.** When an agent ends without a usable answer, Phil recovers one that is nearly right
   before it declares failure. That covers broken JSON in the answer tool call, a rejected but
   parseable answer call, and JSON written as plain text.

## Non-goals

- Escalating to another model. If that comes later, it is opt-in only.
- Prose-then-convert output.
- Changing call budgets or prompts.

## Approaches

There is one sensible way. Both parts sit at the single place where `invoke_agent` decides an
answer is rejected (`agents/invoke.py`, after `_validate`).

Two alternatives were set aside for now, because the record from goal 1 should come first and show
whether they're needed:
- lenient contract validation
- the provider's JSON mode

## The design

### Recording (goal 1)

`outputs/<name>.rejected.json` gains three fields:
- **`last_messages`:** the agent's last 6 messages, oldest first. Each has `role` (`ai`, `tool`,
  `human`, `system`) and `text` (string or text parts, cut to 2,000 characters). AI messages also
  carry:
  - `tool_calls`: `[{name, args}]`, with args JSON-encoded and cut to 2,000 characters
  - `invalid_tool_calls`: `[{name, args, error}]`, with the raw args string cut to 2,000
    characters
  - `finish_reason`: from `response_metadata["finish_reason"]` (or `stop_reason`), when present
  - at most 10 tool calls and 10 invalid calls are kept per message; `tool_calls_omitted` holds the
    number cut, when any are
- **`model_calls`:** the number of model calls telemetry records for the attempt (`len(usage.calls)`).
- **`max_model_calls`:** the agent's cap, or `null`.

Image parts are written as `[image]`, never as base64. The file is local, and nothing is sent
anywhere.

### Salvage (goal 2)

When `result` has no `structured_response` and no parse problem was raised, Phil walks the AI
messages from newest to oldest, at most the last 6, to the first message that has any candidate: an
answer call, an invalid answer call, or extractable JSON in its text. **Only that newest message
counts.** If none of its candidates validates, salvage stops with the problems of the last candidate it
tried. It never falls back to an older message's draft. The 6-message window only bounds how far back
Phil looks for the newest message with a candidate. That message's candidates are tried in this order:

1. **Broken answer call.** An `invalid_tool_calls` entry named after the out contract
   (`spec.out_contract.__name__`, ToolStrategy's tool name). Phil repairs its args string with
   `tool_args.repair_arguments`, then validates the result against the out contract.
2. **Rejected answer call.** A `tool_calls` entry with that name. Phil validates its args again,
   directly.
3. **JSON in the text.** The message's text. Phil tries every fenced ` ```json ` (or plain ` ``` `)
   block in order (a fence with another language, such as ` ```bash `, is skipped whole), then every
   top-level span from a `{` to its matching `}`, counting braces outside strings. For each, Phil tries
   `json.loads`, then `repair_arguments`, then validates.

**No empty answers.** An empty object is rejected on every route. On the text route, a candidate that
sets none of the out contract's fields is also rejected (`invoke` passes `spec.out_contract.model_fields`
to `salvage`), so `{}` or an unrelated object never passes for a contract whose fields all have
defaults.

The first candidate that validates is the output. The same evidence checks then run (`check_evidence`),
exactly as for a normal answer. A salvaged output is recorded with telemetry outcome `salvaged`, which
is a new value in `TelemetryRow.outcome`. A debug log line says which route recovered it. The outputs
artifact is written as usual.

If nothing validates, behaviour is the same as today: the problems list, the single contract retry,
and the `ContractViolation`. The problems from the last candidate tried are added after the existing
problem (for example, `salvage: plan.tasks: Field required`), so the retry message tells the model
what was wrong with its closest attempt.

## Constraints

- No new dependencies.
- Salvage reads only the agent's own messages, and costs no model calls.
- Behaviour is unchanged when `structured_response` is present.
