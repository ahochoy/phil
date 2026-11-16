# Intake as a conversation — design

Status: approved in conversation, 2026-11-10. First of two specs on intake and planning (the
second, larger-work planning, comes after this one is tried live). Goals worked toward in a loop
(e.g. "bring the Lighthouse score over 90") come after launch, with tools and skills.

## 1. Goal

Phil talks with the user before it works. When a request is clear, it says in a line or two what
it understood and the steps it will take, then starts. When it isn't, it asks, in plain prose
with optional numbered choices, remembering everything said so far, and looking at the code when
that makes its questions specific. The user always sees a short plan before anything runs.
Today's intake stays available behind a setting so the two can be compared.

## 2. Where things stand

- **Routing.** A message typed at an idle chat is classified first (Jev, or the `route` spec on
  the classifier model; `src/phil/routing/`). Plain code (`routing/policy.py` `decide`) maps the
  class to a depth: answer, quick, full, or "intake decides". Answers go to the read-only
  answerer.
- **Intake is one structured call.** The `intake` spec (orchestrator role, lean harness, no
  tools) turns the message, the file list and a README excerpt into a `Goal`
  (`contracts/interface.py`): objective, constraints, non-goals, up to 3 `open_questions` with
  0 or 2–4 options, `approach_open`, a depth and, for quick work, one `QuickTask`.
- **Questions are mechanical.** The controller asks them one at a time (`_ask_next`), with no
  model call between them, for at most 2 rounds (`MAX_QUESTION_ROUNDS`), then calls intake again
  with `Q: A` lines.
- **No agent sees the chat.** The controller keeps 8 recent turns (`_recent`, not saved); the
  router and the answerer see 4 of them. Intake, designer, architect and critic see only the
  structured `Goal`.
- **Quick work starts unseen.** On the quick path the plan is built in code from `goal.task` and
  the run starts after one line; the user never sees what Phil intends to do.
- **Full work** goes designer (when the approach is open) → architect → critic (one revision at
  most) → approval (`y` / `edit` / `n`).

## 3. Decisions (user, 2026-11-10)

- Two specs, intake first; larger-work planning (architecture write-up, self-review, feedback,
  task breakdown) is the second.
- A clear request: Phil states its understanding and steps, then starts. No confirmation step.
- The router stays and runs first: it still sends questions to the answerer, and its guess for
  work requests is a hint the conversation may overrule.
- Questions are prose plus optional numbered choices; free text always works.
- The conversation may read and search the repo, read-only.
- Built as one structured call per user message (approach A, over a long-lived agent that pauses
  to ask, or a free chat agent plus an extractor).
- The conversation is the new default; `[chat] intake = "classic"` keeps today's flow.

## 4. Design

### 4.1 Flow

1. A message at an idle chat is routed as today. `answer` goes to the answerer, unchanged, as do
   the forced paths (`/ask`, `/quick`, `/full`, `/full!`), which skip the conversation.
2. Anything else opens an **intake conversation**: chat stage `conversation`, which replaces
   `questions` when `[chat] intake = "conversation"`. The opening message is its first turn.
3. Each user message in that stage is one **turn**: one call to the new `converse` agent spec
   (§4.2), run as a chat job like intake is today.
4. A turn that isn't ready prints Phil's reply and its choices; the stage stays `conversation`.
5. A turn that is ready with a quick goal prints the reply and a **Plan** block (§4.4), then
   launches the quick run at once, through the same launch checks the quick path uses today
   (`launch_problems`; a quick task that fails them falls back to full planning, as now). The
   user objects the usual way: stop the run, and say what they want instead.
6. A turn that is ready with a full goal prints the reply and the Plan block, then hands over to
   today's full path: designer when `approach_open`, architect, critic, approval. The
   conversation goes with it (§4.3).
7. The conversation sets the size, not the router: a request routed quick may come out full,
   and the other way round.

### 4.2 The `converse` spec and its contract

```
"converse": AgentSpec(
    "converse", "orchestrator", ConverseInput, IntakeTurn, tools=("shell",), harness="light",
    read_only_shell=True, max_model_calls=CONVERSE_MAX_MODEL_CALLS, cap_message=REPLY_NOW,
    repo_context=True,
)
```

- `CONVERSE_MAX_MODEL_CALLS = 6` per turn. `REPLY_NOW` tells the agent its reading is over and
  it must answer with what it has, like `ANSWER_NOW` does for the answerer.
- Prompt: `prompts/converse.md`. It carries over the "when to ask" rules of `prompts/intake.md`
  (ask only what the user alone can supply; never ask what the repo answers), and adds: be brief
  and direct, no filler; ask at most one or two things a turn; offer choices when the answer is
  one of a few alternatives; when the request is clear, be ready on the first turn.
- **`ConverseInput`:** `turns` (the conversation so far: each `{speaker: "user" | "phil", text,
  choices}`), `route_depth` and `route_class` (the router's guess, or None), `repo_overview`,
  `detected_test_cmd`, `must_decide` (§4.6).
- **`IntakeTurn`:**
  - `reply: str` — the message shown, a few sentences at most.
  - `choices: list[str]` — 0 or 2–4 short options (the same rule `Question.options` has).
  - `ready: bool`.
  - `goal: Goal | None` — required when ready, absent otherwise. Its depth is `quick` or
    `full` (never `answer` or None); a quick goal carries its `task`, as today's intake output
    does. Its `open_questions` is empty: a turn with questions is not ready.
  - `steps: list[str]` — when ready, 2–7 one-line steps in the user's terms; empty otherwise.
- A validator enforces those rules. An output that breaks them is invalid and gets the usual
  structured-output retry.

### 4.3 What later agents see

The full path today sees only the `Goal`. `ArchitectInput` and `DesignInput` gain an optional
`conversation: list[ConverseTurn]`, filled from the intake conversation, including after a
fallback to the classic intake (§4.7); None only when the goal never had a conversation. The architect and designer prompts say: the goal is the summary; the conversation is the
user's own words, which win where they differ. The quick implementer needs nothing new: the
quick task already carries what it must do.

### 4.4 What the user sees

- **While a turn runs:** the usual live status: "Reading the repo" while the snapshot is
  exported, then "Thinking". (Showing each file the agent reads would need an activity feed for
  chat jobs, which no chat agent has today; not in this spec.)
- **A reply** prints as ordinary chat text. Choices print under it, numbered, with "Plan with what
  you know" always last. A typed number picks a choice; anything else is the user's own answer.
- **The Plan block,** on a ready turn: a short titled list of the steps, then "Starting (quick)…"
  or "Planning in detail…".
- **`/go`** at any point in the stage means "plan with what you know" (§4.6).

### 4.5 State

- The conversation's turns are saved in the chat's `state.json` with the stage, so a reopened
  chat resumes mid-conversation. The transcript logs every turn as it does every message today.
- A message typed while a turn is running is held and becomes part of the next turn (both
  messages, in order), rather than cancelling the turn.
- The conversation ends when the goal launches, hands over to planning, or is cancelled; a new
  goal starts a new conversation.

### 4.6 Limits

- **Deciding on request.** "Plan with what you know" and `/go` send a turn with
  `must_decide=True`: the agent must be ready, with its best goal and steps.
- **Turn cap.** After 4 user replies (`MAX_CONVERSATION_TURNS`) without a ready turn, the next
  turn is sent with `must_decide=True`.
- **Budget.** Turns count against the goal's planning budget, `[chat] max_cost_usd`. It is checked
  before each turn; at `[run] warn_at` Phil says so once; when it is spent the next turn is sent
  with `must_decide=True` and planning goes on under today's budget rules.
- **A turn returned not ready under `must_decide`** is a failure (§4.7).

### 4.7 Failures

- A turn that fails (provider error, or still-invalid output after the retries) shows the
  existing failure message, then the whole conversation (its user messages, joined) goes once to
  the classic intake (`planning.intake`), and the goal carries on from its `Goal` as on the
  classic path. If that fails too, the goal stops with the usual error.
- A cancelled goal cancels a running turn like any chat job.

### 4.8 The classic switch

`[chat] intake = "conversation" | "classic"`, default `"conversation"`. `classic` runs today's
code path unchanged (intake, question menus, quick start without a Plan block). `phil config`
validates it; the guide documents both.

## 5. Tests

- **Contract:** `IntakeTurn` accepts a not-ready turn with choices; rejects ready without goal or
  steps, a goal on a not-ready turn, a ready goal with open questions or depth `answer`, 1 or 5
  choices, 1 or 8 steps.
- **Chat flow** (scripted fake turns through the existing fake factory):
  - a clear request: one ready quick turn, the Plan block, then the quick run launches;
  - not ready, an answer, then ready: two turns, the second sees both user messages and Phil's
    first reply;
  - a typed number picks a choice; free text is passed as written;
  - ready full: the architect's input carries the conversation; designer too when the approach
    is open;
  - a request routed quick that the conversation makes full, and the reverse;
  - "Plan with what you know", `/go`, and the turn cap each send `must_decide=True`;
  - a message typed during a turn joins the next turn;
  - a reopened chat resumes the conversation from `state.json`;
  - the budget spent: the next turn must decide;
  - a failed turn falls back to classic intake with the joined messages;
  - not ready under `must_decide` counts as a failure;
  - a quick goal that fails the launch checks falls back to full planning;
  - forced paths and `answer` skip the conversation;
  - `intake = "classic"`: today's flow, unchanged (the existing intake tests run under it).
- **Config:** the setting's default, both values, and a bad value rejected.
- **Live (user-run):** a clear small request starts after one turn with a Plan block; a vague one
  gets a question with choices, then plans; a large one hands over to the architect.

## 6. Out of scope

- Larger-work planning: the architecture write-up, self-review, user feedback on it, and the
  task breakdown (the second spec). Its planning artifacts are Markdown files: Phil writes a
  spec, gets it approved, writes an implementation plan from it, then starts the run, after the
  spec → plan → work flow of the Superpowers skills.
- Goals worked toward in a loop, with tools to measure them (after launch, with skills).
- Changing the router, the answerer or the forced paths.
- Removing the classic intake (once the conversation has been tried).
