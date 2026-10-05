# Phil: Ask Before Acting

**Status:** approved in conversation, 2026-10-03.

## 1. Problem

In a live run, the user asked for "a CTA section at the bottom of the page… featuring the AI systems audit and the AI readiness guide". It went wrong at every step:

- **Routing:** the request went to the quick path. Jev rated it 0.54 for needs-detail, below the 0.6 threshold.
- **Intake:** intake asked nothing. It didn't ask about placement, link targets, copy, images or visual style.
- **The run:** the run built nothing.

The user wants Phil to behave like a good collaborator:

- ask the right questions, with choices;
- talk through the design when the approach is open;
- show visual ideas for visual changes (later, under M5).

They also asked to revisit the Jev decision rule after M3.

## 2. Decisions (user, 2026-10-03)

| Topic | Decision |
|---|---|
| Question style | Multiple choice, **one question at a time**. Each question has numbered options plus "something else", and the user answers with a number or free text. |
| Missing content | Intake asks before planning or writing a quick task whenever the change depends on content only the user can supply. |
| Design discussion | On the **full path**, when the approach is open and no design exists yet, a designer proposes 2–3 approaches. The user picks one, and the architect plans it. |
| Visuals | Out of scope here; they stay under M5. The designer is where a mockup would plug in later. |
| Jev revisit | Revise the decision rule in the spec before rerunning anything (Plan B). |

## 3. Plan A: asking before acting

### 3.1 Contracts

- **`Question` (Part):**

  | Field | Type | Meaning |
  |---|---|---|
  | `text` | `str` | the question |
  | `options` | `list[str]` | 0 or 2–4 short choices; 0 means a free-text question |
  | `why` | `str` | one line on what the answer changes, optional (default `""`) |

  The chat always adds "Something else (type it)" as the last choice. It isn't stored.
- **`Goal.open_questions: list[Question]`.** A validator turns a plain `str` into `Question(text=s, options=[])`, so saved chats and older outputs still load.
- **`Goal.approach_open: bool = False`:** true when there are several reasonable ways to build the request and the user hasn't picked one.
- **`Approach` (Part):**

  | Field | Type | Meaning |
  |---|---|---|
  | `name` | `str` | short |
  | `summary` | `str` | 1–2 sentences |
  | `tradeoffs` | `list[str]` | 1–3 items |

- **`Approaches` (Contract):**

  | Field | Type | Meaning |
  |---|---|---|
  | `options` | `list[Approach]` | 2–3 approaches |
  | `recommended` | `int` | index into `options` |
  | `reason` | `str` | one sentence |

- **`DesignInput` (Contract):** `goal: Goal`, `repo_overview: str`.
- **`ArchitectInput.chosen_approach: Approach | None = None`**, plus `approach_note: str = ""`, which holds free text the user added.

### 3.2 Intake prompt

- **When to ask:** intake must ask (`open_questions`, at most 3, most important first) whenever the change depends on content only the user can supply:
  - link targets or URLs;
  - copy, product or offer names and their details;
  - images or other assets;
  - which page or section;
  - placement on the page;
  - visual style, when the change is visual.

  This applies on every path, quick included. While questions are open, `task` stays null.
- **Options:** each question should offer 2–4 sensible options drawn from the repo and the request (for example, placements that exist on the page, or "placeholder links for now"). Use options=[] only when no sensible options exist.
- **`approach_open`:** true when several reasonable designs exist (layout, data model, library choice) and the user hasn't stated one. False for a clear, single-way change.

### 3.3 Chat: questions one at a time

- **The `questions` stage** asks one question at a time:

  ```
  {n}. {text}
       1. {option}
       …
       k. Something else (type it)
  ```

  `why` is shown dimmed under the question when it's set.
- **Answers:**
  - a number picks that option;
  - picking "Something else", or typing any text that isn't a number, records the text as the answer;
  - for a free-text question (options=[]), the reply is the answer;
  - `go` stops asking and plans with what's known, as today.
- **No model call between questions.** After the last question, the existing intake follow-up call runs once with all the answers, as `"{question}: {answer}"` lines.
- `MAX_QUESTION_ROUNDS = 2` stays the same, so a follow-up round can ask new questions.
- **Ctrl-C** cancels the goal, as today.

### 3.4 Designer step (full path)

- **When it runs:** the route or intake chose full, `goal.approach_open` is true, there are no open questions left, and the message didn't start with `/full!`. In that case the chat runs the designer before planning.
- **The designer:**
  - a new role **`designer`**, on the `high` tier by default;
  - spec `"design"`, on the light read-only harness: read tools and the read-only shell, `max_model_calls = 8`, no writes;
  - prompt `design.md`;
  - output `Approaches`;
  - it reads the same tree the architect reads (the chat's detection root).
- **The chat shows the approaches as one multiple-choice question.** The recommended approach comes first and is marked `(recommended)`. Each approach shows its summary and trade-offs, followed by a final option, "Something else (describe it)".
- **The pick goes to the architect** as `chosen_approach`. Free text goes in as `approach_note`, with `chosen_approach` left null. Revisions (`edit` at approval) keep the same choice.
- **Recording:** the transcript gets an `approaches` contract and an `approach_chosen` note.
- **Failure:** if the designer fails or errors, the chat prints `Couldn't propose designs; planning directly.` and plans as today.
- **`/full!`** forces the full path and skips the designer. The help text lists it.

### 3.5 Quick path

The quick path already waits for open questions. With 3.2, a CTA-style request is asked about before any quick task is written. No other change.

## 4. Plan B: the Jev revisit (after Plan A)

### 4.1 The decision rule replaces §5.1's in the M3 spec

Jev is worth recommending over the `llm` backend if all of these hold:

| # | Condition |
|---|---|
| 1 | **Wrong-path errors:** Jev's count is no more than the llm backend's + 1. A wrong path is a routed depth that differs from the expected depth, excluding intake. |
| 2 | **Unsafe errors:** no more `answer_as_change` or `full_as_quick` errors than the llm backend. |
| 3 | **Deferrals:** Jev's intake rate is no more than the llm backend's + 2 cases. |
| 4 | **Latency:** p95 is at most a third of the llm backend's. |
| 5 | **Cost:** cost per 100 requests is lower. Unknown cost means "undecided". |

### 4.2 Thresholds

- Each backend gets its own detail threshold: `routing.detail_threshold` stays as the default, and `routing.jev_detail_threshold` overrides it for Jev.
- The benchmark's sweep covers both thresholds (confidence and detail) for each backend.
- Thresholds are chosen on the existing 40 cases, then checked on the new cases before they become defaults.

### 4.3 Cases

- **f-01:** review its label, and record the reason in the case file.
- **About 10 new cases.** The user's CTA request is one, labelled ambiguous with expected depth full. The rest are content-missing requests that should come back needing detail, such as links, copy, a page not named, or "make it look better".

## 5. Testing (offline)

- **Contracts:**
  - a `str` is coerced into a `Question`;
  - options must number 0 or 2–4;
  - `recommended` is in range;
  - every field has a description.
- **Chat:**
  - questions are asked one at a time;
  - a number answer, a "something else" answer, a typed answer and a free-text question all work;
  - `go` stops asking;
  - intake gets a single follow-up call with all the answers;
  - Ctrl-C cancels;
  - a saved chat with string questions still reopens.
- **Designer:**
  - it runs only when it should;
  - picking an approach sends `chosen_approach` to the architect, and free text sends `approach_note`;
  - the choice survives an edit;
  - a designer failure falls back to planning directly;
  - `/full!` skips the designer;
  - the designer can't write anything.
- **CTA scenario** (scripted): a quick route, intake asks about placement and links, the user answers, then a quick task is written.

## 6. Out of scope

- Visual mockups (M5).
- Asking questions during a run.
- Learning the user's preferences across goals (M6 memory).
