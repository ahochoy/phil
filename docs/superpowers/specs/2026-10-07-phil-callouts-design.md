# Phil: Decision and Failure Callouts

**Status:** approved in conversation, 2026-10-07. This is the second slice of roadmap M5. It follows the live activity feed (PR #30).

## 1. Problem

Every decision in the chat is a typed prompt line:

- **Intake questions** are numbered lines, and you type a number or an answer.
- **Plan approval** reads `Approve? [y / edit / n] ›`.
- **A paused run** prints a yellow `⏸ r-4f2a needs you: …` line. You then type `/answer`, and the prompt becomes `… — retry / skip / abort ›`.
- **Confirmations** are `[y / n]` prompts.

Failures print one red line with an exception name, `Phil couldn't finish that: ContractViolation: …`, then a folder path.

Decisions don't stand out, answering them means knowing the right word, and approvals don't say exactly what they allow. Failures don't say what happened in plain words, whether Phil will retry, or what you should do. Roadmap M5 asks for bordered question and approval callouts with keyboard choices and an explicit scope, and for failure categories.

## 2. Decisions (user, 2026-10-07)

| Topic | Decision |
|---|---|
| Interaction | A bordered callout, with its options inside the box, navigated with ↑/↓. Enter or a number key picks. While the question is open, the callout is docked in the live prompt area. When it's answered, a settled copy goes into the scrollback. |
| Approval scope | Today's scope only: "for the rest of this run". The design leaves room for M4 to add more scopes as extra options. |
| Coverage | Every choice prompt: run pauses (all reasons), plan approval, intake questions, the approach picker, and the PR, fix and replace confirmations. Free-text answers stay typed, reached from a menu option. |
| Architecture | A decision mode in the chat's existing prompt_toolkit prompt (option 1). Not a separate dialog app, and not a full-screen layout. |
| Failures | A classifier maps an exception to a category, a plain sentence, whether Phil retries, and what you can do, shown as a red callout. |

The session's mockups are in `.superpowers/brainstorm/` (not committed): `callout-interaction.html` and `callout-combined.html`.

## 3. Design

### 3.1 The `Decision` value

`phil.chat.decision` holds two frozen dataclasses:

- **`Option`:**
  - `label`, the text shown;
  - `answer`, the text the chat receives, the same word or number its handlers already accept;
  - `typed: bool = False`. Picking a typed option switches to text input instead of submitting.
  - `detail: str = ""`, an optional second line (approach summaries).
- **`Decision`:**
  - `kind`: `"question" | "approval" | "pause" | "confirm"`. It sets the border colour: `phil.agent` for questions and confirmations, `phil.warn` for approvals and pauses.
  - `title`
  - `body: tuple[str, ...]`, capped at `BODY_LINES = 8`. When the body is longer, the last line reads `… see /more 1` and the full text becomes ref 1.
  - `options: tuple[Option, ...]`
  - `default: int`, the index highlighted when it opens.
  - `settled: Callable[[Option], str]`, the one-line record printed once it's answered (for example `✓ Approved for this run`).

### 3.2 The controller

- **`ChatController._decision() -> Decision | None`** builds the decision for the current stage, or returns None when the stage expects free text (`idle`, `hint`, `edit`, `running`, typing "Something else", describing an approach) or when no question is open.
- **The menu answers through the existing path.** A picked option's `answer` goes into `_input` as if you had typed it. The handlers (`_answers`, `_choose_approach`, `_approval`, `_confirm_replace`, `_confirm_pr`, `_confirm_fix`, `_pause_answer`) keep their logic. Before routing, the controller prints the settled copy for the option you picked.
- **A paused run's question opens at once.** `/answer` is no longer needed to start answering. `/answer` stays as the way to reopen a folded callout.

### 3.3 The terminal: decision mode

`TerminalIO.ask(prompt, decision=None)`:

- **Without a decision,** it behaves as today.
- **With a decision,** the prompt's message becomes the live row (if there is one) followed by the callout box. The box holds the title, the body, and the options with the highlighted one marked `›`, plus a key-hint line: `↑/↓ choose · Enter confirm · 1–N · Esc type a message`.
  - The text buffer is hidden.
  - Key bindings active only in decision mode: ↑/↓ (wrapping), Enter, digits 1–9, and Esc.
  - The toolbar, the live row and the wake mechanism are unchanged.
- **What `ask` returns:**
  - the picked option, as `answer`;
  - `TYPE`, a sentinel, when Esc is pressed or a `typed` option is picked;
  - `WAKE`, as today.
- **Keeping your place.** The highlighted index survives a wake, the same way typed text and the cursor survive today.
- **After `TYPE`:**
  - the controller re-asks without a decision;
  - a folded one-line reminder sits above the input: `⏸ r-4f2a needs you · /answer to choose`, or `Question 2 of 3 · /answer to choose` for questions;
  - picking a `typed` option shows its own prompt instead of the reminder (for example `Your answer ›` or `What should change? ›`).
- **`LineIO` (no TTY).** It prints the box once with numbered options and reads a typed line. A number picks that option, and any other text passes through unchanged. So pipes and tests behave as today.

### 3.4 Content

**Pauses.** Titles read `⏸ <run> needs you · <reason text>`:

| Reason | Title ends | Body |
|---|---|---|
| `attempts` | `<task> failed <n> attempts (<phase> phase)` | the first 3 problems, then `Details: /more` |
| `approval` | `approve a command` | the exact commands, each on its own line, then `Approving allows these exact commands for the rest of this run only.` |
| `cmd_not_found` | `a command isn't installed` | the command and the last line of its output |
| `setup_failed` | `setup failed` | the setup command and the last lines of its output |
| `no_test_cmd` | `no test command` | `Set [project] test_cmd in phil.toml, then retry.` |
| `commit_failed` | `the commit failed` | git's error, and the signing and hooks settings |
| `review_failed` | `the reviewer gave no valid review` | the problems |
| `budget` | `the budget is used up` | the tokens and cost used, against the limit |

**Option labels.** The `answer` is the engine word.

| Engine word | Label |
|---|---|
| `retry` | Retry the task. This opens the optional hint input; Enter skips it. |
| `skip` | Skip this task |
| `full` | Plan it fully instead |
| `approve` | Approve for this run |
| `deny` | Deny: the agent continues without it |
| `continue` | Keep going past the budget |
| `bypass` | Commit without signing or hooks |
| `finish` | Finish without a review |
| `abort` | Abort the run. Always listed last. |

**Default highlight:**
- **Retry:** for `attempts`, `cmd_not_found`, `setup_failed`, `commit_failed` and `review_failed`.
- **Approve:** for `approval`.
- **Keep going:** for `budget`.
- **The first option:** otherwise.

Abort is never the default.

**The other decisions:**
- **Questions:**
  - the title is `Question <i> of <n>: <text>`, and the body is the question's `why`;
  - the options are the question's own options, then `Something else (type it)` (typed), then `Plan with what you know` (`go`);
  - a question with no options isn't a menu: it's asked as free text.
- **Approach picker:**
  - each approach is an option with `detail` set to its summary;
  - the recommended approach is the default;
  - the last option is `Describe your own` (typed).
- **Plan approval:**
  - the title is `Approve this plan?`;
  - the options are `Approve and run` (`y`), `Edit the plan` (typed, which goes to the existing `edit` stage) and `Cancel` (`n`).
- **Confirmations:**
  - **Open a PR?** `Open the PR` / `Not now`. When the run finished with blocking issues: `Open the PR anyway` / `Not now`.
  - **Fix it?** `Quick fix` / `Plan it fully` / `Not now`.
  - **Replace the current goal?** `Replace it` / `Keep the current goal`.

**Settled copies** are one dim line in the box's colour. For example:
- `✓ Approved for this run · node build.mjs`
- `✓ Retrying CALC-001`
- `Answer: Postgres`
- `✗ Run aborted`

### 3.5 Failure callouts

`phil.agents.failures.classify_failure(exc) -> Failure(category, headline, retries, action)` builds on `agents/retry.py`: `is_transient`, the status-code helpers and `provider_detail`.

| Category | Recognised by | Headline | Action |
|---|---|---|---|
| `auth` | status 401 or 403, or a missing-key error | The provider rejected your API key. | `phil keys set <provider>`, or set `<ENV>` |
| `quota` | status 402, or credit or quota wording in the provider detail | Your <provider> account is out of credits. | Add credits, then try again |
| `busy` | 429 or 529 after Phil's retries | <provider> is busy. Phil retried <n> times. | Try again in a minute |
| `server` | status 500–599, after Phil's retries | <provider> had a server error. Phil retried. | Try again in a few minutes |
| `network` | the transient connection and timeout classes | Couldn't reach <provider>. Phil retried. | Check your connection |
| `refused` | any other 4xx, with the provider detail | <provider> refused the request: <detail>. | Check the model with `phil models check` |
| `output` | `ContractViolation` | The <role> didn't return a usable answer. | Try again, or use a stronger model for <role> |
| `config` | `ConfigError` | <the error's message> | Fix the setting it names |
| `internal` | anything else | Something went wrong inside Phil (<ExceptionClass>). | Try again. |

- **Where they appear.** `_failed` (a failed intake, plan, revision or `/btw` job) and a run ending as `failed` print a red `Decision`-styled box with no options. It shows the headline, whether Phil retries, the action, and `Details: /more 1`.
- **The raw exception** goes to the session's error log, and becomes `/more 1`. Exception class names never appear in a headline, except for `internal`.

### 3.6 Rendering the box

- **One renderer, two uses.** `phil.ui.callout` renders a `Decision` (or a `Failure`) as prompt_toolkit formatted text for the docked prompt, and as a Rich `Panel` for the scrollback and `LineIO`. The two share the line layout.
- **Wrapping.** Body lines wrap inside the box. Option labels are cut to the width with `…`.
- **Narrow terminals.** Below 40 columns, the box has no border and its lines are indented two spaces.
- **No markup injection.** All text comes from plans, agents and errors, so it's built as `Text` or plain formatted-text fragments, never as markup.

### 3.7 Edge cases

- **The run is answered elsewhere** (`run_resumed` while a pause callout is open). The callout closes, `r-4f2a was answered elsewhere.` prints, and the prompt returns to normal.
- **A wake while the callout is open.** The callout redraws, with the same option highlighted.
- **Ctrl-C** behaves as it does today in each stage.
- **Questions not answered yet.** Leaving the questions stage with some unanswered keeps the existing behaviour (`go`).

### 3.8 Testing

- **Decisions:** for every pause reason and every stage, `_decision()` gives the expected title, options, labels, answers and default.
- **Failures:** `classify_failure` for each category, with fake exceptions shaped like the real SDK errors (status codes on `status_code` or `response.status_code`; OpenRouter, Anthropic and httpx class names).
- **The box renderer:** at widths 30, 40, 80 and 120; a body capped at 8 lines; markup-like text rendered literally.
- **The terminal, with pipe input:**
  - ↑/↓ wrap;
  - Enter and the number keys pick;
  - Esc returns `TYPE`;
  - a typed option returns `TYPE`;
  - a wake keeps the highlight;
  - the docked box is not left in the scrollback (as with the live row).
- **`LineIO`:** a number picks, and any other text passes through.
- **End to end with a scripted run:** it pauses for approval, the chat shows the callout, Enter approves, the settled copy prints, and the run resumes.

## 4. Out of scope

- Approval scopes beyond "this run", and a policy file (M4).
- The welcome banner, a status-bar redesign, and a sub-agent bar.
- Mouse support.
- Visual proposals.
- Failure categories for `phil run` and `phil setup` outside the chat. They can reuse `classify_failure` later.

## 5. Done means

- Every choice prompt in the chat is a docked callout you answer with the arrow keys, and leaves a settled record behind.
- Approvals show exactly what they allow, and for how long.
- Failures show a plain headline, whether Phil retries, and what to do. The raw error stays one `/more` away.
- Piped input (`LineIO`) still works.
- CI passes on Ubuntu, macOS and Windows.
- A live check by the user feels clear.
