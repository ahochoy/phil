# Full-screen chat — design

Status: approved in conversation, 2026-11-18 (mockups: `.superpowers/brainstorm/`, not committed:
`frame-layout-v2.html`, `subagents-working.html`). This reverses the 2026-10-08 "inline" decision
and pulls the full-screen mode forward from after launch. Next in the epic: the feed's order and
detail (its own spec), then Markdown spec/plan planning with skills.

## 1. Why

Next to Claude Code, Codex and Antigravity, Phil feels like a command-line utility for one-off
jobs: dense, with past chats, the feed, the status line and the input crowding each other, and no
flow. Entering a chat should feel like entering a space with Phil, one that can take substantial
asks and a real back-and-forth. Full screen is the first step; the look will be tuned through
feedback rounds after it lands, so the frame must make those rounds cheap.

**Success:** `phil` opens a calm, full-window chat; the conversation scrolls in its own pane; the
input and a live line stay put; leaving prints how to come back. Nothing the chat does today is lost.

**Anti-goals:** a frame that is busier than today; losing text that can't be copied; a terminal
left in a broken state after a crash; a rewrite of the controller.

## 2. Where things stand

- `phil` (the chat) prints the banner and, if any, the open-chats list with a "Reopen one?"
  prompt (`cli/main.py` `_chat`, `_choose_open_chat`), then runs `ChatController.run` under
  `TerminalIO.run` (`chat/terminal.py`).
- `TerminalIO` is a prompt_toolkit `PromptSession`: a bottom toolbar (`ui/toolbar.py`
  `render_toolbar`: repo, model, elapsed, cost, progress, pause), live rows above the input
  (`render_live_rows`: main agent, `└` sub-agents, goal step, `/btw` lines), and decision menus
  docked above the input (`choose`, drawing `ui/callout.py` boxes). Everything else is Rich output
  to the controller's `Console`, printed into scrollback under `patch_stdout`.
- The controller talks to the terminal only through `ChatIO` (`chat/controller.py`): `ask`
  (blocking; returns text, None on EOF, or `WAKE`), `choose`, `wake` (any thread), `submit`
  (jobs on daemon threads, at most 3), `prefill`, `spawn`. Its main loop runs on the thread that
  calls `ask` and is the only place that prints. `LineIO` serves non-TTY use and the tests.
- `phil attach <run>` follows a run with its own loop (`cli/attach.py`), printing the feed.

## 3. Decisions (user, 2026-11-18)

- Full-screen takeover is the default for the chat. Inline stays behind a setting.
- **Layout (classic frame):** a one-line header; the conversation fills the window and scrolls; a
  live line directly above a boxed input pinned at the bottom; nothing under the input.
- **Live line:** left, the active agent, what it's doing, and the task count; right, run time and
  cost. With sub-agents active it grows one `└` line per sub-agent (as the agent bar does today),
  then shrinks back.
- **Conversation:** questions, approvals and the Plan block appear in it much as today. A running
  task shows its short tool lines; a finished task folds to one line
  (`✓ Task 1 · Theme setting · 0:38 · 2 files`). Each sub-agent gets one line in its task saying
  what it was sent to do. The feed's order and detail stay as today (next spec).
- **First screen:** the banner and mascot only; no open-chats list. One quiet line,
  `2 open chats · /chat`, when there are open chats; none when there aren't.
- **On exit:** the frame closes, and Phil prints only how to come back:
  `To continue this chat:  phil --resume 3f2a`.
- **Resuming:** `phil` always starts a fresh chat; `phil --resume <id>` reopens one (as today);
  `phil chat` opens the open-chats picker inside the frame; `phil --continue` reopens the most
  recent open chat in the repo; `/chat` in a chat opens the same picker. The picker is named
  "chat" because `/resume` (continue the chat's stopped run) and `phil resume <run>` already
  exist and keep their meaning (user, 2026-11-18).
- Built with **Textual** (approach A, §4).

## 4. Approaches

- **A. Textual (chosen).** From the makers of Rich, which Phil already draws everything with: Rich
  renderables display as they are and reflow on resize; scrolling, the mouse wheel, click-and-drag
  selection with copy, pinned layouts and a test pilot come with it; side panes and a details pane
  can follow later without another rebuild. Costs a new, fairly large dependency and a rewrite of
  the terminal layer; the controller is untouched.
- **B. prompt_toolkit full-screen.** No new dependency, and the key bindings and menus carry over,
  but the scrolling conversation (rendering, reflow on resize, mouse, selection, "new output"
  marker) would be ours to write and keep, which gets more expensive with every polish round.
- **C. Inline with a pinned bottom (Claude Code style).** Ruled out: it isn't a takeover.

## 5. Design

### 5.1 Shape

A new `phil.ui.screen` package holds the Textual app (only it imports Textual, as only
`chat/terminal.py` imports prompt_toolkit today; `cli/main.py` imports it lazily). A new
`ScreenIO` offers what the chat startup uses from `TerminalIO` (`chat_io`, `run`, `close`, `width`);
`LineIO` (no `width`) stays the non-TTY choice.

- **Threads.** The Textual app owns the main thread. `ScreenIO.run(fn)` starts the app and runs
  `fn` (the controller's `run`) on a worker thread; the app exits when `fn` returns.
- **`ask`** blocks the controller thread until the input submits a line (or EOF, or a wake);
  typed text is never lost across a wake (as `TerminalIO` carries it). `WAKE`, `TYPE`, None and
  `KeyboardInterrupt` keep their meanings exactly.
- **`choose`** shows the decision's callout docked above the input, with the same keys (↑/↓,
  Enter, 1–9, Esc for `TYPE`) and the same highlight carry-over on a wake.
- **`wake`, `submit`, `prefill`, `spawn`** behave as in `TerminalIO` (`submit` keeps the 3-job
  limit and daemon threads).
- **Output.** The controller keeps printing to a Rich `Console`. In the frame that console's
  output becomes conversation items (renderables posted to the app thread-safely), so every
  existing view (callouts, Plan block, feed lines, answers, bands) shows without being rewritten.
  Items reflow to the pane's width on resize.

### 5.2 The frame

- **Header** (one line): `◆ phil · <repo> · <branch>` left; chat id right.
- **Conversation pane:** scrolls with the mouse wheel, PgUp/PgDn and Shift+↑/↓; follows new output
  while at the bottom; when scrolled up, new output shows a small `↓ new` marker instead of moving
  the view; typing returns to the bottom.
- **Live line(s):** `render_live_rows`' content, in the new arrangement: the main agent line ends
  with the task count on the left and has run time and cost on the right; sub-agent and `/btw`
  lines as today; hidden when nothing is running (the right side alone when only a chat cost is
  worth showing is the builder's call). A decision menu collapses them to one line, as today.
- **Input:** a bordered box; Enter sends, Alt+Enter (and the keys read as it today) adds a line;
  multi-line input grows the box up to a few lines, then scrolls.
- **What the old toolbar showed** (model, repo, progress, pause): repo is in the header; progress,
  elapsed and cost are on the live line; the pause notice shows on the live line in its warning
  style; the model is left out of the always-on frame (shown by `/status`, which exists or is
  added). *Builder's call where the toolbar's remaining segments land, keeping the frame no busier
  than the mockup.*

### 5.3 Tasks in the conversation

The feed lines the chat prints today go into the conversation pane. Two changes only, because the
frame needs them: the running task's tool lines are one block that updates in place, and when the
task finishes the block folds to its one-line summary (the existing milestone band's text). A
sub-agent's tool lines are not shown in the block; the block shows one line per sub-agent
(`└ sub-agent  <its task description>`), and its live activity is on the live line.

*Builder's notes (calls made at review, 2026-11-18):*
- Today the controller prints feed lines one at a time (`_print_feed`, `_on_milestone`), with
  nothing saying which task a line belongs to. Small, additive changes to the controller and
  `FeedRenderer` so they hand the screen structured items (task id, line, milestone) are allowed;
  "the controller stays as it is" means its behaviour and its `ChatIO` contract, not a ban on
  touching it. Inline and non-TTY output stay exactly as today (no fold there).
- The folded line is the existing `task_done` band text with the task title added:
  `✓ Task 1 · Theme setting · done · 2 files · 0:38`.
- `/feed <agent>` filters the lines inside a running block the same way it filters the feed today.

### 5.4 Start, resume and exit

- **Start:** the banner card (`render_banner`, falling back to `banner_plain`) is the first
  conversation item; under it, when `list_open_chats` finds any, one muted line
  `N open chat(s) · /chat`. A failure listing chats hides the line (as today's lookups do).
- **`phil chat` / `/chat`:** a picker in the frame listing the open chats with today's
  `_open_chat_line` text; picking one reopens it (from `/chat`, the current chat is saved and
  closed first, as quitting would); Esc cancels. `phil chat` with no open chats starts a new chat
  and says so in one line. `/chat` is added to `HELP_COMMANDS`. `/resume`, `phil resume <run>`,
  `--resume <id>` and `--new` work as today; `--resume` still requires an id.
- **`--continue`:** reopens the most recently active open chat; with none, starts a new chat and
  says so in one line.
- **Exit** (EOF/Ctrl+D, `/quit` or however the chat ends today): the app restores the terminal,
  then prints one line to normal output: `To continue this chat:  phil --resume <id>`. A chat with
  no goal yet prints nothing.
- **Crash safety:** any exception restores the terminal before the traceback or error is shown;
  Ctrl+C keeps today's meaning (interrupt the current step, bump the generation) and never quits
  the app on its own.

*Builder's notes (calls made at review, 2026-11-18):*
- **Switching chats in the frame.** The app outlives one controller: the session locking,
  `diagnostics.redirect` and controller construction now in `_chat`/`_run_chat` move into a loop
  that the frame drives, and `ChatController.run` returns an outcome (quit, or switch to chat X)
  instead of only returning. Switching saves and unlocks the current chat, then locks and opens
  the chosen one, exactly as quitting and reopening would.
- **Ctrl+C.** Textual owns the main thread, so SIGINT no longer lands in the controller's loop.
  Ctrl+C in the frame raises `KeyboardInterrupt` from the blocked `ask`/`choose` at once; pressed
  while the controller is busy between prompts, it is held and raised from the next `ask` or
  `choose`. The controller's long waits already happen in `ask` (jobs run on threads), so this
  keeps today's meaning in practice; the builder confirms no synchronous step blocks for long.
- **Inline and the small-terminal fallback** keep today's start (the open-chats list and
  "Reopen one?") unchanged; `--continue` works in every mode.
- **`--new`** stays, documented as "start a new chat (the default)".
- **The exit line** prints when the chat had a goal and is still open (so reopening it means
  something); a chat with no goal, or one that has been closed, prints nothing.

### 5.5 Keys and copying

- Click-and-drag selects text in the conversation; Ctrl+C copies when there's a selection, and
  otherwise is today's interrupt. Terminals' own selection (Option-drag in iTerm, Shift-drag
  elsewhere) keeps working.
- `/copy` copies Phil's last reply.
- Esc, Ctrl+D, Tab, and the existing slash commands keep their meanings; Textual's own default
  bindings that clash (quit, command palette) are turned off.

### 5.6 Setting and fallbacks

- `[chat] screen = "full" | "inline"`, default `"full"`; `phil config` validates it and the guide
  documents it. `"inline"` runs today's `TerminalIO` unchanged.
- Non-TTY input or output uses `LineIO`, as today.
- A terminal smaller than 60 columns or 15 rows at start uses inline, saying so in one line.
- `phil attach <run>` stays as it is for now (call made at review: it has its own IO and no chat
  state to feed the live line, so it's a separate adapter; it follows once the frame has settled).

### 5.7 Windows

Textual runs in Windows Terminal; the chat must start and exit cleanly there (CI's Windows job
runs the screen tests). Legacy consoles that Textual can't drive fall back to inline.

## 6. Tests

- **ChatIO contract:** the existing controller tests keep using their fakes; `ScreenIO` gets its
  own tests, with Textual's pilot, for `ask`/`wake` (typed text survives), `choose` (keys,
  `TYPE`, highlight carry-over), `submit` (job limit), EOF and Ctrl+C.
- **Frame:** a chat goal through the pilot: the banner and the open-chats line; a reply, a Plan
  block and a running task in the conversation; the live line with one agent and with sub-agents;
  a task folding when done; scrolling up shows `↓ new` and holds the view; resize reflows.
- **Start/resume/exit:** `--continue`, the `phil chat` and `/chat` picker, `/resume` still
  continuing a stopped run, the exit line (and none for
  an empty chat), the terminal restored after an exception.
- **Setting:** default, both values, a bad value rejected; `"inline"` and non-TTY paths unchanged
  (today's terminal tests keep passing).
- **Live (user-run):** a real goal in the frame on macOS; resize; copy; quit and `--resume`.

## 7. Out of scope

- The feed's order, grouping and detail (next spec), beyond §5.3's fold.
- Side panes, a details pane, per-agent views, themes.
- Changing other commands (`phil runs`, `show`, `setup`, `config`), which stay plain output.
- Branding (the mascot itself), which comes after.

## 8. Left to the builder

How Textual widgets are composed and named; how the controller's console output becomes
conversation items; the exact shortcut for `/copy` if any; where the remaining toolbar segments go
(§5.2); and the pinned Textual version (current at the time, per keep-dependencies-current).
