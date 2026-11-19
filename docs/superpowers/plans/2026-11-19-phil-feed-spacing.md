# Feed Spacing and Diffs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phil's feed gets blank lines between blocks, wrapped (not cut) design text, a `? asked  answer` settled line, and up to 8 changed lines per file under each burst summary, the same in the full-screen frame, the inline chat and `phil attach`.

**Architecture:** A new `ui/diff_view.py` turns an edit's recorded detail into coloured lines. `FeedRenderer.end_burst` returns the summary plus those diff lines as `FeedItem`s, flagging the summary as starting a block. A new `ui/blocks.py` `BlockConsole` wraps the chat's console: every `print` starts a block (one blank line first, never two, none at the top) unless it runs inside `group()`. The controller, `attach` and the frame's task blocks honour it.

**Tech Stack:** Python 3.12, Rich, Textual `>=8.2.8,<8.3`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-phil-feed-spacing-design.md`

## Global Constraints

- No new dependencies.
- Agent and repo text reaches the screen only through `one_line()` / `printable()` (`phil.ui.bursts.one_line`, `phil.text.printable`).
- Detail indent is `INDENT` (4 spaces). Diff file headers are indented 8 spaces, diff lines 10.
- Diff caps: 8 lines per file (`DIFF_LINES = 8`), 3 files per burst (`DIFF_FILES = 3`).
- Removed-line marker is `−` (U+2212), added is `+`. Styles: `phil.diff.add` (green) and `phil.diff.del` (red), foreground colours only.
- No two blank lines in a row; no blank line at the very top of the conversation.
- Existing tests that pin old feed output are updated to the new layout, not kept.
- `/more #n` keeps working unchanged.
- Repo files are edited only with the Edit/Write tools (no sed, heredocs, python scripts or printf). Commit with `git commit -F .git/phil-commit-msg.txt`; messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Stage only your own files.
- Test command: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q <paths>`. Never run `-m live` or `-m bench`.

## Review Focus

1. A very narrow window (width ≤ 12): diff headers and lines must still render without errors or negative widths, cut with `…`.
2. A detail file that's missing, unreadable or empty (the activity log disabled itself): the file header still shows, with no diff lines and no crash.
3. Wide characters (CJK, emoji) and tabs in a diff line: no line exceeds the width, and a tab shows as `?`.
4. `/feed <agent>` filtering on: a hidden burst hides its summary, headers, diff lines and its blank line together.
5. The frame folds a task block that held blank lines and diffs: the folded block is one line, with no stray blank item left behind.

Each has a test in the task that owns the code (marked "Review Focus n").

---

### Task 1: Diff lines from an edit's detail

**Files:**
- Create: `src/phil/ui/diff_view.py`
- Modify: `src/phil/ui/theme.py` (add `"phil.diff.add": "green"`, `"phil.diff.del": "red"`)
- Test: `tests/ui/test_diff_view.py`

**Interfaces:**
- Produces:
  - `changed_lines(tool: str, detail: str) -> tuple[list[tuple[str, str]], bool]`. It returns `(lines, truncated)`. Each line is `("+" | "−", text)`, and `truncated` is True when the detail ended with the `MAX_DETAIL_CHARS` marker.
  - `file_blocks(calls: list[dict], details: Callable[[int], str | None], width: int, *, more_hint: bool, number: int | None) -> list[Text]`. It returns every diff line for one burst: for up to `DIFF_FILES` files, a header followed by that file's lines, then the `+N more files` line.
  - Constants `DIFF_LINES = 8` and `DIFF_FILES = 3`.

- [ ] **Step 1: Write the failing tests** in `tests/ui/test_diff_view.py`:

```python
from rich.cells import cell_len
from phil.ui.diff_view import DIFF_FILES, DIFF_LINES, changed_lines, file_blocks

EDIT = "--- before\n+++ after\n@@ -1,3 +1,3 @@\n keep\n-old\n-- dashes\n+new\n keep2"

def test_edit_detail_keeps_only_changed_lines_and_reads_markers_by_first_char():
    assert changed_lines("edit_file", EDIT) == ([("−", "old"), ("−", "- dashes"), ("+", "new")], False)

def test_write_detail_is_all_added_and_never_parsed_as_diff():
    assert changed_lines("write_file", "+x\n--- y\n") == ([("+", "+x"), ("+", "--- y")], False)

def test_truncation_marker_is_dropped_and_reported():
    lines, cut = changed_lines("write_file", "a\n… (12 more characters not shown)")
    assert lines == [("+", "a")] and cut

def _edit(seq, path, result="+1 −1", ok=True):
    return {"seq": seq, "tool": "edit_file", "summary": f"edit {path}", "result": result,
            "ok": ok, "phase": "end", "detail": f"{seq}.txt"}

def test_header_sums_a_files_edits_and_lines_are_indented_and_styled():
    calls = [_edit(1, "a.py", "+2 −1"), _edit(2, "a.py", "+1 −0")]
    details = {1: EDIT, 2: "--- before\n+++ after\n@@ -0,0 +1 @@\n+more"}.get
    out = file_blocks(calls, details, 80, more_hint=True, number=2)
    assert out[0].plain == "        a.py  +3 −1"
    assert out[1].plain == "          − old"
    assert out[1].spans[0].style == "phil.diff.del" or out[1].style == "phil.diff.del"
    assert [t.plain.strip() for t in out[1:]] == ["− old", "− - dashes", "+ new", "+ more"]

def test_more_than_eight_lines_ends_with_a_count():
    body = "\n".join(f"line {n}" for n in range(12))
    call = {"seq": 3, "tool": "write_file", "summary": "write b.py", "result": "+12", "ok": True, "phase": "end", "detail": "3.txt"}
    out = file_blocks([call], {3: body}.get, 80, more_hint=True, number=3)
    assert out[0].plain == "        b.py  +12 −0"
    assert len(out) == 1 + DIFF_LINES + 1 and out[-1].plain == "          … 4 more lines"

def test_a_truncated_detail_points_at_more():
    call = {"seq": 4, "tool": "write_file", "summary": "write c.py", "result": "+1", "ok": True, "phase": "end", "detail": "4.txt"}
    out = file_blocks([call], {4: "a\n… (9 more characters not shown)"}.get, 80, more_hint=True, number=4)
    assert out[-1].plain == "          … more in /more #4"
    out = file_blocks([call], {4: "a\n… (9 more characters not shown)"}.get, 80, more_hint=False, number=4)
    assert out[-1].plain == "          … more"

def test_more_than_three_files_ends_with_a_files_line():
    calls = [_edit(n, f"f{n}.py") for n in range(1, 6)]
    out = file_blocks(calls, lambda seq: None, 80, more_hint=True, number=5)
    assert [t.plain for t in out if not t.plain.startswith("          ")][-1] == "        +2 more files · /more #5"
    assert file_blocks(calls, lambda seq: None, 80, more_hint=False, number=5)[-1].plain == "        +2 more files"

def test_failed_edits_and_non_edits_are_left_out():
    calls = [_edit(1, "a.py", ok=False), {"seq": 2, "tool": "read_file", "summary": "read a.py", "phase": "end"}]
    assert file_blocks(calls, {1: EDIT}.get, 80, more_hint=True, number=2) == []

def test_missing_detail_keeps_the_header():  # Review Focus 2
    out = file_blocks([_edit(1, "a.py")], lambda seq: None, 80, more_hint=True, number=1)
    assert [t.plain for t in out] == ["        a.py  +1 −1"]

def test_narrow_width_cuts_every_line():  # Review Focus 1
    out = file_blocks([_edit(1, "a_long_file_name.py")], {1: EDIT}.get, 12, more_hint=True, number=1)
    assert out and all(cell_len(t.plain) <= 11 for t in out)

def test_wide_chars_and_escapes_stay_inside_the_width():  # Review Focus 3
    detail = "--- before\n+++ after\n@@ -1 +1 @@\n+" + "漢字" * 40 + "\t\x1b[2J"
    out = file_blocks([_edit(1, "a.py")], {1: detail}.get, 40, more_hint=True, number=1)
    assert all(cell_len(t.plain) <= 39 for t in out) and "\x1b" not in out[1].plain and "\t" not in out[1].plain
```

- [ ] **Step 2: Run them; expect `ModuleNotFoundError: phil.ui.diff_view`.**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/ui/test_diff_view.py`

- [ ] **Step 3: Implement `src/phil/ui/diff_view.py` and the two theme styles.**
  - **Parsing an edit.** For `edit_file`, skip the first two lines, then skip every line starting `@@`. For each remaining line, its first character decides: `+` is added, `-` is removed (shown as `−`), and a space is context, which is dropped.
  - **Parsing a write.** For `write_file`, every line is added.
  - **Truncation.** A last line matching `… (N more characters not shown)` is dropped and sets `truncated`. Match it on its shape (`startswith("… (") and endswith(" more characters not shown)")`).
  - **Grouping by file.** A file is the path from an edit's summary, as `bursts.edited_files` reads it. Only successful `EDITS` calls count, and files keep first-touched order.
  - **Header totals.** `+A −R` sums each call's `result`, parsed as `+(\d+)` and optionally `−(\d+)`.
  - **Text and width.** Every text goes through `one_line()`, and every line is cut to `width - 1` cells with `…`. Reuse `feed_view._cut`, moving it to `bursts.py` if that avoids a circular import.
  - **Styles.** Headers use `phil.muted`, diff lines `phil.diff.add` or `phil.diff.del`, and the `… N more lines`, `… more` and `+N more files` lines `phil.muted`.

- [ ] **Step 4: Run the tests; expect them all to pass.** Same command.

- [ ] **Step 5: Commit** `src/phil/ui/diff_view.py src/phil/ui/theme.py tests/ui/test_diff_view.py`, with the message "Feed: diff lines from an edit's detail".

---

### Task 2: Burst summaries carry their diffs

**Files:**
- Modify: `src/phil/ui/bursts.py`:
  - `FeedItem` gains `starts_block: bool = False`.
  - `burst_text(burst, *, counted: bool = False)`: with `counted`, the edit part reads `edited N file(s)` instead of the names.
- Modify: `src/phil/ui/feed_view.py`:
  - `FeedRenderer.__init__(self, *, more_hint: bool = True, details: Callable[[int], str | None] | None = None)`.
  - `end_burst` emits the summary item with `starts_block=True`, followed by one `FeedItem` per `file_blocks` line, each carrying the burst's records.
  - Milestone bands are unchanged here; Task 3 spaces them.
- Modify: `src/phil/chat/controller.py`, at both `FeedRenderer()` constructions (around lines 393 and 2094). Pass a `details` that reads `activity_log(ProjectPaths(self.info.slug), run_id).detail_for(seq)` for the run the chat follows (`self._chat_run()`), as `_more_step` does. Return the file text, or None on any error.
- Modify: `src/phil/cli/attach.py`: `FeedRenderer(more_hint=False, details=...)` reading through the `ActivityLog` it already opens.
- Test: `tests/ui/test_feed_view.py`, `tests/chat/test_controller_feed.py`, `tests/cli/test_attach.py`.

**Interfaces:**
- Consumes: `file_blocks`, from Task 1.
- Produces:
  - `FeedItem.starts_block`.
  - `FeedRenderer(details=...)`.
  - `end_burst` returns `[summary(starts_block=True), *diff_items]`. Engine-gate and in-burst failure items keep `starts_block=False`.

- [ ] **Step 1: Write the failing tests** in `tests/ui/test_feed_view.py`:
  - `test_end_burst_puts_diffs_under_a_counted_summary`. A burst by `implementer` with two successful edits to `a.py` and `b.py`, and `details` returning a small unified diff for each. The items are:
    - `items[0].plain == "    implementer ran 2 tools · edited 2 files  (/more #2)"`, with `items[0].starts_block` True.
    - `items[1].line.plain == "        a.py  +1 −1"`.
    - Every item's `records` equals the burst's calls.
  - `test_end_burst_without_edits_is_one_item`. A burst of reads only returns one item, unchanged from today, with `starts_block` True.
  - `test_no_details_reader_shows_headers_only`. `FeedRenderer()` with no `details` shows a header per file and no diff lines.

  In `tests/cli/test_attach.py`, `test_attach_prints_a_bursts_diff`. Write a real `ActivityLog` with one `edit_file` end record and its detail, run attach to the terminal state, and assert the output contains `a.py  +1 −1` and `− old`, and does not contain `/more`.

- [ ] **Step 2: Run them; expect them to fail** (`TypeError: unexpected keyword 'details'` or assertion errors).

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/ui/test_feed_view.py tests/cli/test_attach.py`

- [ ] **Step 3: Implement the changes listed under Files.** The summary uses `burst_text(burst, counted=True)` when `file_blocks` returned anything, and `burst_text(burst)` otherwise.

- [ ] **Step 4: Run** `tests/ui tests/chat/test_controller_feed.py tests/cli/test_attach.py`. Fix any test that pinned the old summary text by updating it to the new text. Expect them all to pass.

- [ ] **Step 5: Commit**, with the message "Feed: a burst's summary carries its diffs".

---

### Task 3: Blank lines between blocks

**Files:**
- Create: `src/phil/ui/blocks.py`
- Modify: `src/phil/chat/controller.py`:
  - The controller wraps its console: `self.console = BlockConsole(console)`.
  - Group the multi-print sequences that form one block.
  - `_print_feed` honours `starts_block` on both paths.
- Modify: `src/phil/cli/attach.py`: wrap its console the same way. `_print_items` honours `starts_block`.
- Test: `tests/ui/test_blocks.py`, `tests/chat/test_controller_feed.py`, `tests/ui/screen/test_tasks.py`, `tests/cli/test_attach.py`.

**Interfaces:**
- Produces `class BlockConsole` in `phil/ui/blocks.py`:
  - `BlockConsole(console: Console)`, which delegates every other attribute to the wrapped console (`__getattr__`).
  - `print(*objects, **kwargs) -> None`: starts a block unless inside `group()` or `continued()`.
  - `group() -> ContextManager[None]`: the prints inside it form one block, with one blank line before the first.
  - `continued() -> ContextManager[None]`: the prints inside it start no block.
  - `blank() -> None`: one blank line unless the last line printed was blank, or nothing has been printed yet.
  - `mark_blank() -> None`: for a caller that printed a blank line some other way.

- [ ] **Step 1: Write the failing tests** in `tests/ui/test_blocks.py`, using `Console(file=io.StringIO(), width=80, color_system=None)`:

```python
def test_each_print_is_a_block_with_one_blank_between_and_none_on_top(): ...
    # print("a"); print("b")  -> "a\n\nb\n"
def test_group_prints_one_block(): ...
    # print("x"); with group(): print("a"); print("b")  -> "x\n\na\nb\n"
def test_continued_lines_join_the_block_above(): ...
    # print("band"); with continued(): print("gate")  -> "band\ngate\n"
def test_blank_never_doubles(): ...
    # print("a"); blank(); blank(); print("b")  -> "a\n\nb\n"
def test_other_console_attributes_pass_through(): ...
    # BlockConsole(c).width == 80
```

  In `tests/chat/test_controller_feed.py`:
  - `test_feed_blocks_are_spaced_and_gate_lines_are_not`. Drive `task_started`, then activity with an engine gate record, then a burst, then `task_done`, into the inline console. Assert exactly one blank line before the band, before the burst summary and before the done band, and none between the band and the gate line.
  - `test_a_hidden_burst_hides_its_blank_line_too` (Review Focus 4). With `/feed tester` set, an `implementer` burst with diffs prints nothing at all, blank line included.

  In `tests/ui/screen/test_tasks.py`:
  - `test_a_task_block_spaces_its_burst_and_folds_to_one_line` (Review Focus 5). Through the frame's task output, the burst summary's `starts_block` adds one empty item before it inside the task block. After `task_done`, the block holds only the folded line.

- [ ] **Step 2: Run them; expect them to fail.**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/ui/test_blocks.py tests/chat/test_controller_feed.py tests/ui/screen/test_tasks.py`

- [ ] **Step 3: Implement `BlockConsole` and wire it in.**
  - **The blank line.** `BlockConsole` prints a blank line with the wrapped console's `print()` (an empty line). The frame's `PaneConsole` then makes it an empty item.
  - **Frame task path.** In `_print_feed`, when an item starts a block, emit `output.task_line(task, Text(""))` before it, unless the last line written to that task's block was blank. Keep that state per task in the controller, and reset it when a task starts.
  - **Inline path.** The controller calls `self.console.blank()`, then prints the item inside `continued()`.
  - **Items inside a burst.** Every item with `starts_block=False` prints inside `continued()`.
  - **Milestones.** `_on_milestone`'s inline path prints a band as a block. The frame path's `task_started` creates a new task block, so the block widget's own top margin must give one blank line: check `TaskBlock`'s CSS and add `margin-top: 1` if it has none. `task_done` folds, so nothing is added.
  - **Grouping multi-print sequences.** Audit `controller.py` for handlers that print one logical block over several `print` calls, and wrap each in `self.console.group()`. At minimum that covers:
    - `_on_design_ready`. The heading, the reason and each option's lines form one group per option, and the heading forms its own group.
    - The `/help`, `/status`, `/show` and `/feed` outputs.
    - The completion notice and its refs.
    - The failure box.
    - The user-line echo, together with a reply that follows it at once.

    List every handler you grouped in your report.
  - **`/more #n`.** It prints its tool lines and detail panel as one group.

- [ ] **Step 4: Run** `tests/ui tests/chat tests/cli`. Existing tests that compare exact console output gain blank lines. Update each expected value to the new spaced output; don't add `continued()` just to keep an old expectation. Expect them all to pass, then run the full suite once.

- [ ] **Step 5: Commit**, with the message "Feed: blank lines between blocks".

---

### Task 4: Wrapped design text and the `? asked  answer` line

**Files:**
- Modify: `src/phil/ui/blocks.py`: add `hanging(text: str, width: int, *, indent: str, lead: str = "", style: str = "") -> list[Text]`.
- Modify: `src/phil/chat/decision.py`:
  - `Decision` gains `asked: str = ""`.
  - Builders set `asked`:
    - `question_decision`: the question `text`, through `one_line`.
    - `conversation_decision`: `"Your answer"`.
    - `approach_decision`: `"Which approach?"`.
    - `chat_picker_decision`: `"Reopen which chat?"`.
  - `settled_line(decision, option) -> Text`.
- Modify: `src/phil/chat/controller.py`:
  - In `_on_design_ready`, print each option's summary, trade-offs and the reason through `hanging(...)`, with no `_clip`.
  - Print the settled `Text` as returned. It's no longer wrapped in `Text(settled, style="phil.muted")`.
- Test: `tests/ui/test_blocks.py`, `tests/chat/test_decision.py`, `tests/chat/test_controller_feed.py` (or the approaches test file that pins `_on_design_ready` output).

**Interfaces:**
- Consumes: `BlockConsole.group()`, from Task 3.
- Produces:
  - `hanging(text, width, *, indent, lead="", style="") -> list[Text]`. The first line is `indent + lead + text`, and the following lines are indented by `indent` plus spaces the width of `lead`. Every line is ≤ `width - 1` cells, and the text passes through `one_line()`.
  - `settled_line -> Text`.

- [ ] **Step 1: Write the failing tests:**

```python
# tests/ui/test_blocks.py
def test_hanging_wraps_under_its_lead():
    out = hanging("alpha beta gamma delta", 20, indent="    ", lead="– ")
    assert [t.plain for t in out] == ["    – alpha beta", "      gamma delta"]

# tests/chat/test_decision.py
def test_settled_question_shows_asked_and_answer():
    d = approach_decision([("Astro endpoints", "s"), ("Script", "t")])
    line = settled_line(d, d.options[0])
    assert line.plain == "? Which approach?  Astro endpoints"   # "(recommended)" dropped

def test_intake_question_asked_drops_the_counter():
    d = question_decision(2, 3, "Which pages?", "", ["All", "Devlog"])
    assert d.asked == "Which pages?"
    assert settled_line(d, d.options[1]).plain == "? Which pages?  Devlog"

def test_non_question_settled_lines_keep_their_prefix():
    # an approval's settled line still starts with "✓ "
    ...
```

  In `tests/chat/test_controller_feed.py` (or wherever `_on_design_ready` is pinned), add `test_design_options_wrap_instead_of_cutting`. Use a 60-column console and an approach whose trade-off is 150 characters. Assert that the full trade-off text appears across the wrapped lines, that no line ends in `…`, and that a blank line separates the options.

- [ ] **Step 2: Run them; expect them to fail.**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/ui/test_blocks.py tests/chat/test_decision.py tests/chat/test_controller_feed.py`

- [ ] **Step 3: Implement.**
  - **`settled_line` for a question.** `Text("? ", style="phil.callout.title.question")` + `asked` in the same style, then two spaces, then the label without a trailing ` (recommended)`, styled `phil.user`. With an empty `asked`, it falls back to the old `Answer: <label>` text.
  - **`settled_line` for other kinds.** Today's string as `Text(style="phil.muted")`.
  - **Typed answers.** A typed answer's line, where the controller prints the typed text, goes through `one_line()`.
  - **Design text.** Option names stay `phil.agent`. Summaries and trade-offs use `hanging` at `self.console.width`, with indent 4. Summaries have no lead; trade-offs have the lead `"– "` and the style `phil.muted`.

- [ ] **Step 4: Run** `tests/chat tests/ui`, then the full suite. Update pinned expectations of the `Answer:` text and the clipped trade-offs. Expect everything to pass.

- [ ] **Step 5: Commit**, with the message "Feed: design text wraps; settled questions show what was asked".

---

### Task 5: Guide and changelog

**Files:**
- Modify: `docs/guide.md`, the feed section: blank lines between blocks, diffs under a burst (8 lines per file, 3 files, `/more #n` for the rest), and the `? asked  answer` line.
- Modify: `CHANGELOG.md`, the Unreleased section: one entry.
- Test: `tests/release` and `tests/test_contract_descriptions.py`, which hold the doc guards.

- [ ] **Step 1: Edit both docs.** Write in the guide's existing voice and keep the edit short.
- [ ] **Step 2: Run** `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/release tests/test_contract_descriptions.py`. Expect them to pass.
- [ ] **Step 3: Commit**, with the message "Guide: feed spacing and diffs".

### Task 6 (user): live check

Hand the user these steps: `uv tool install --editable . --force`, then `cd <a test repo> && phil`. Then:

- Give it a goal that needs design options. They wrap, with blank lines between them, and the answer shows as `? Which approach?  …`.
- A task that edits files shows `edited N files` with diffs under it.
- Run `phil --set chat.screen=inline` and check that the inline chat looks the same.
- Run `phil attach <run>` on a finished run.

Record what they report. Fixes go in follow-up commits on this branch.
