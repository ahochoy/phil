# Mockups at the approach stage — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At the approach stage, the user can type `show` / `show N [note]` (or pick "Show as mockups") to see the approaches as an HTML mockup in the browser, or as a terminal sketch without one, and the picked approach's mockup goes on to the architect and implementers.

**Architecture:** A new read-only `mockup` agent returns one drawing per requested approach (HTML fragment or text sketch). A pure module, `phil.chat.mockup_page`, cleans the fragments, assembles one page per chat from a fixed template with a strict CSP, writes it under the project folder in `~/.phil`, and decides whether a browser is available. The chat controller parses `show`, runs the mockup as a background job, shows the result, keeps the latest drawing per approach, and passes the picked one to the planner. The planner stamps it on the `Plan` (as it does `critic_notes`) so the run engine hands it to implementers.

**Tech Stack:** Python 3, pydantic contracts, stdlib `webbrowser` / `html` / `re`, existing agent registry and fake agent, pytest.

**Spec:** `docs/superpowers/specs/2026-11-20-phil-mockups-design.md`

## Global Constraints

- No mockup call unless the user asks (`show`, `show N`, or the menu item). Nothing else changes in the approach stage.
- The mockup agent is read-only (`read_only_shell=True`, like `design`); Phil writes every file.
- Page CSP, exactly: `default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:`. Phil also strips `<script>` elements, `on*=` attributes and `javascript:` URLs from fragments.
- Sketch: at most 40 lines × 100 columns; wider than the terminal is clipped, not wrapped.
- No browser means: `SSH_CONNECTION` or `SSH_TTY` is set, or the platform is Linux with neither `DISPLAY` nor `WAYLAND_DISPLAY` set. Decided before the call; only one format is generated.
- One page per chat, `mockups/<chat id>.html` under the project folder, rewritten each `show`, plus `mockups/<chat id>-v<n>.html` copies. The browser opens only on a chat's first page; later ones print `Mockup updated: reload the tab`.
- `Approaches.visual` defaults to false.
- Copy, as the spec gives it:
  - The hint: `Say "show" to see these as mockups.`
  - The menu item: `Show as mockups`.
  - The page's closing line: `Pick in Phil: Enter or a number to choose, \`show N <note>\` to redraw.`
  - The failure line: `Couldn't draw the mockup: …`.
- Tests use a temp `PHIL_HOME` and the fake agent. Never run `-m live` or `-m bench`.
- Edit repo files only with Edit/Write. Commit through `.git/phil-commit-msg.txt` and `git commit -F`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Test command: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q -n auto -p no:cacheprovider`.

## Review Focus

1. A described approach that starts with "show", such as `show prices in red`, must still be planned as the user's own approach and must not trigger a mockup. *(Task 3 test.)*
2. A late mockup result that arrives after the user picked, described an approach or cancelled must be dropped silently, without printing or opening the browser. *(Task 4 test.)*
3. A model fragment containing `<script>`, `onload=` or `href="javascript:…"` must never reach the written page. *(Task 2 test.)*
4. A reopened chat whose mockup file was deleted must carry no mockup, and must not raise. *(Task 5 test.)*
5. When `webbrowser.open` returns False, Phil must print the link and say the browser didn't open. It must not claim the browser opened. *(Task 4 test.)*

---

### Task 1: Contracts, mockup agent spec and prompts

**Files:**
- Modify: `src/phil/contracts/design.py`, `src/phil/contracts/__init__.py` (exports)
- Modify: `src/phil/agents/registry.py` (new spec and budget constant)
- Modify: `src/phil/skills/select.py` (stage map)
- Create: `src/phil/prompts/mockup.md`
- Modify: `src/phil/prompts/design.md` (the `visual` field)
- Test: `tests/contracts/test_design.py` (create if missing), `tests/agents/test_registry.py`, `tests/skills/test_select.py`

**Interfaces:**
- Produces:
  - `Approaches.visual: bool = False`.
  - `MockupInput(Contract)`: `goal: Goal`, `approaches: list[Approach]`, `repo_overview: str = ""`, `note: str = ""`, `format: Literal["html", "sketch"]`, `conversation: list[ConverseTurn] | None = None`.
  - `MockupDrawing(Part)`: `name: str`, `html: str = ""`, `sketch: str = ""`, `note: str = ""`.
  - `Mockup(Contract)`: `drawings: list[MockupDrawing]` (min 1).
  - `get_spec("mockup")`: role `"designer"`, `MockupInput` → `Mockup`, `tools=("shell",)`, `harness="light"`, `shared_prompt=False`, `read_only_shell=True`, `max_model_calls=MOCKUP_MAX_MODEL_CALLS` (8), `repo_context=True`, `cap_message` asking it to draw now from what it has read.
  - In `select.py`, `"mockup": ("design",)`.

- [ ] **Step 1: Write the failing tests**
  - `test_approaches_visual_defaults_false`: `Approaches(options=[a, b]).visual is False`, and `Approaches.model_validate({...without visual...}).visual is False`.
  - `test_mockup_sketch_is_clipped_to_40_by_100`: a `MockupDrawing` validator clips `sketch` to 40 lines and each line to 100 characters.
  - `test_mockup_spec_is_read_only_designer`: `get_spec("mockup").role == "designer"`, `read_only_shell is True`, `writes_files is False`, and `max_model_calls == 8`.
  - `test_mockup_agent_sees_design_skills`: the select mapping for `"mockup"` is `("design",)`.
- [ ] **Step 2: Run them and watch them fail** (`pytest tests/contracts tests/agents/test_registry.py tests/skills/test_select.py -q`). They should fail on missing names.
- [ ] **Step 3: Implement the contracts, spec and stage mapping as in Interfaces.** Field descriptions are the agent's instructions: the `html` description names the fragment rules (inline CSS only, no script, no external URLs, images as inline SVG or `data:`), and the `sketch` description names the 40×100 limit.
- [ ] **Step 4: Write `prompts/mockup.md`** (match `design.md`'s structure).
  - The job: rough "this is what we're thinking" mockups of the given approaches, one drawing per approach, named exactly as given.
  - Look at the repo's pages and styles first, and use its colours, fonts and real copy.
  - Where nothing fits, use the wireframe classes `mock-nav`, `mock-sidebar`, `mock-content`, `mock-button`, `mock-input`, `placeholder` and `split`.
  - Use placeholders, not photos.
  - Match fidelity to the difference between the approaches.
  - Follow the user's `note` when one is given.
  - Fill `html` or `sketch` as `format` says, never both.
- [ ] **Step 5: Add `visual` to `design.md`'s Approaches section.** Set it to true only when the approaches differ in how something looks (layout, placement, styling, what's on screen). Touching UI code is not enough. Add one example of each.
- [ ] **Step 6: Run the tests and watch them pass**, then run the full suite.
- [ ] **Step 7: Commit** with the message `Mockups: contracts, agent spec and prompts`.

### Task 2: The mockup page

**Files:**
- Create: `src/phil/chat/mockup_page.py`
- Test: `tests/chat/test_mockup_page.py`

**Interfaces:**
- Produces:
  - `CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"`
  - `clean_fragment(html: str) -> str`: removes `<script>…</script>` (including unclosed and mixed case), `on\w+=` attributes (quoted or not) and `javascript:` URLs.
  - `build_page(objective: str, comparing: str, drawings: list[tuple[int, str, str]]) -> str`: each drawing is `(number, name, cleaned_html)`. The page is a full document with the CSP `<meta http-equiv="Content-Security-Policy">` first in `<head>`. It contains:
    - The wireframe classes' CSS.
    - The objective (HTML-escaped) as `<h1>`.
    - The `comparing` line.
    - The drawings in a side-by-side grid that stacks below 800px, each headed `N. name` (escaped).
    - The closing line from Global Constraints.
  - `comparing_line(count: int, objective: str) -> str`: e.g. `3 approaches to <objective>: compare placement and content.`, or `1 approach to …` for one.
  - `browser_available(env: Mapping[str, str], platform: str) -> bool`: the rule in Global Constraints. `platform` is `sys.platform`.
  - `write_page(folder: Path, chat_id: str, page: str) -> tuple[Path, bool]`: writes `folder/<chat_id>.html` and the next `folder/<chat_id>-v<n>.html`. It creates the folder, and returns `(current path, first)`, where `first` is True when no versioned copy existed before.

- [ ] **Step 1: Write the failing tests**
  - `test_scripts_handlers_and_js_urls_are_removed`: the input contains `<SCRIPT>x</script>`, `<script src=a>` with no closing tag, `<img onload="x" onerror=y>` and `<a href="javascript:alert(1)">`. The output contains none of `script`, `onload`, `onerror` or `javascript:` (case-insensitive), and keeps `<img` and `<a`.
  - `test_page_carries_the_csp_first`: in `build_page(...)`, the CSP meta comes before any `<style>`, and `CSP` appears verbatim.
  - `test_page_escapes_names_and_objective`: given an objective `<b>x</b>` and a name `a&b`, both appear escaped.
  - `test_page_ends_with_how_to_respond`: the closing line is present.
  - `test_browser_rule`: no SSH and darwin gives True; `SSH_TTY` set gives False; linux without `DISPLAY` or `WAYLAND_DISPLAY` gives False; linux with `WAYLAND_DISPLAY` gives True; win32 gives True.
  - `test_write_page_versions_and_first_flag`: the first write returns `first=True` and creates `-v1`. The second returns `first=False`, creates `-v2`, and the current file holds the second page.
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement.** Use regex with `re.IGNORECASE | re.DOTALL` for cleaning. The CSP is the real guard, so the cleaner aims at obvious cases, not completeness. Write files with `encoding="utf-8"`.
- [ ] **Step 4: Run the tests and watch them pass**, then run the full suite.
- [ ] **Step 5: Commit** with the message `Mockups: the page, its CSP and where it is written`.

### Task 3: Parsing `show` and drawing

**Files:**
- Create: `src/phil/chat/mockup.py`
- Test: `tests/chat/test_mockup.py`

**Interfaces:**
- Consumes: Task 1 contracts and `get_spec("mockup")`.
- Produces:
  - `ShowRequest` (frozen dataclass): `number: int | None` (None means all) and `note: str`.
  - `parse_show(text: str) -> ShowRequest | None`. The first word must be `show` (case-insensitive). After it: nothing gives `(None, "")`; a number gives `(N, "")`; a number then text gives `(N, text)`. Anything else gives None, so the text is the user's own approach. A number followed by `.` or `)` counts as a number. The range isn't checked here.
  - `draw_mockup(ctx: AgentContext, goal: Goal, approaches: Sequence[Approach], *, tree: Path, overview: str, note: str, fmt: Literal["html", "sketch"], call: int, conversation: Sequence[ConverseTurn] | None = None, images: Sequence[Path] = ()) -> Mockup`. It mirrors `propose_approaches` in `chat/design.py`: the same budget role (`designer`), the same fallback to the architect's model when the designer model is missing, and `workdir=tree`, `node="mockup"`.

- [ ] **Step 1: Write the failing tests**
  - `test_show_forms`: `parse_show` maps each input as follows.

    | Input | Result |
    |---|---|
    | `show` | `(None, "")` |
    | `Show` | `(None, "")` |
    | `show 2` | `(2, "")` |
    | `show 2.` | `(2, "")` |
    | `show 2 above the footer` | `(2, "above the footer")` |
    | `show prices in red` | None |
    | `showcase it` | None |
    | `2` | None |
    | `` (empty) | None |

  - `test_draw_mockup_sends_the_requested_format_and_note`: the fake `mockup` agent sees a packet whose contract has `format == "sketch"`, the note, and only the approaches passed.
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and watch them pass**, then run the full suite.
- [ ] **Step 5: Commit** with the message `Mockups: parse show and draw`.

### Task 4: The approach stage in the chat

**Files:**
- Modify: `src/phil/chat/controller.py` (`_on_design_ready`, `_choose_approach`, the stage prompt at line ~208, new `_mockup_job` / `_on_mockup_ready` / `_on_mockup_failed`, and event dispatch)
- Modify: `src/phil/chat/decision.py` (`approach_decision`)
- Test: `tests/chat/test_controller_mockups.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `approach_decision(approaches, visual: bool = False)`: when `visual` is True, it adds `Option("Show as mockups", "show")` before "Describe your own".
  - On the controller, `self._mockups: dict[str, Drawn]` keyed by approach name. `Drawn` is a small dataclass with `kind: Literal["html", "sketch"]`, `text: str` (the cleaned fragment or the sketch) and `path: Path | None` (the per-approach file, set in Task 5).
  - `self._mockup_seq: int`.
  - `self._approaches_visual: bool`, set in `_on_design_ready` from `approaches.visual`. It is cleared with `_approach_order` on a new goal.
  - The controller imports the module (`from phil.chat import mockup_page`) and calls `mockup_page.browser_available(...)`, so tests patch `phil.chat.mockup_page.browser_available`. It calls `webbrowser.open` through `import webbrowser` in the controller.

Behaviour:
- `_on_design_ready` prints `Say "show" to see these as mockups.` (muted) when `approaches.visual` is True.
- The `choose_approach` prompt becomes `Pick an approach [Enter = 1 / number / show / describe your own] › `.
- In `_choose_approach`, before the free-text fallback, run `parse_show(text)`:
  - A number out of range prints the existing "Pick 1–N…" message, extended with `or show N`.
  - Otherwise, start `_mockup_job(request)`.
  - While describing (`_describing`), `show` is ordinary text.
- `_mockup_job` follows `_design_job`'s pattern:
  - It runs a snapshot, then `draw_mockup`, then posts `mockup_ready`; on failure it posts `mockup_failed`.
  - It increments `_mockup_seq` and captures the seq.
  - It picks `fmt` with `browser_available(os.environ, sys.platform)` before the call.
  - It prints `Drawing the mockup…` (muted) and keeps the stage at `choose_approach`.
- `_on_mockup_ready` ignores the event unless the stage is still `choose_approach`, `not self._describing`, and the seq matches.
  - HTML: build and write the page under `ProjectPaths(slug).project_dir / "mockups"`.
  - If `first`, call `webbrowser.open(path.as_uri())`. When it returns False, print `Couldn't open the browser; open the mockup here:`.
  - If not `first`, print `Mockup updated: reload the tab`.
  - Always print the link, using `phil.ui.links.link`.
  - Sketch: print each drawing headed `N. name`, clipped to the console width.
  - Update `_mockups` for each drawing whose name matches an approach in `_approach_order`. Drawings with unknown names are ignored.
- `_on_mockup_failed` prints `Couldn't draw the mockup: <error>` (muted, one line) and stays at `choose_approach`.
- Picking an approach, describing one, `/cancel` and a new goal all increment `_mockup_seq`, so a late result is dropped.

- [ ] **Step 1: Write the failing tests** (use `run_chat` with fakes for `{"design": [...], "mockup": [...]}`, monkeypatch `phil.chat.mockup_page.browser_available` / `phil.chat.controller.webbrowser.open`)
  - `test_visual_approaches_offer_the_hint`: the hint is printed when `visual=True`; with `visual=False` it is absent, and no `mockup` call is made.
  - `test_show_opens_the_browser_once_then_asks_for_a_reload`: inputs are `show`, `show 2 less copy`, then EOF.
    - `webbrowser.open` is called once.
    - The output contains `Mockup updated: reload the tab`.
    - The second mockup call's contract has `note == "less copy"` and one approach.
    - The current page file contains the second drawing's text.
  - `test_without_a_browser_the_sketch_prints`: when `browser_available` returns False, the contract format is `sketch`, the sketch lines are printed, and `webbrowser.open` is not called.
  - `test_browser_that_wont_open_still_prints_the_link`: when `webbrowser.open` returns False, `Couldn't open the browser` and the file path appear in the output. *(Review Focus 5)*
  - `test_show_followed_by_words_is_an_approach`: `show prices in red` makes no `mockup` call; the architect's contract has `approach_note == "show prices in red"`. *(Review Focus 1)*
  - `test_a_late_mockup_after_a_pick_is_dropped`: drive the controller directly, the way `test_controller_feed.py` drives events. Start a mockup, pick `1`, then deliver `mockup_ready` with the old seq. Nothing is printed for it and `webbrowser.open` is not called. *(Review Focus 2)*
  - `test_mockup_failure_stays_at_the_approach_stage`: the fake raises; the output has `Couldn't draw the mockup:`, and the next input `1` plans approach 1.
  - `test_menu_offers_show_as_mockups_when_visual`: `approach_decision([...], visual=True)` has an option with answer `"show"` before the typed option; with `visual=False` there's none.
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement as described.** Pass `visual=bool(self._approaches_visual)` into `approach_decision` at the `choose_approach` branch around line 761.
- [ ] **Step 4: Run the tests and watch them pass**, then run the full suite.
- [ ] **Step 5: Commit** with the message `Mockups: show at the approach stage`.

### Task 5: Carrying the mockup forward and keeping it across reopen

**Files:**
- Modify: `src/phil/contracts/inputs.py` (`ArchitectInput.mockup`, `ImplementInput.mockup`)
- Modify: `src/phil/contracts/planning.py` (`Plan.mockup`)
- Modify: `src/phil/chat/planning.py` (`draft` / `revise` / `_cycle` / `_architect` take `mockup: str = ""`, then stamp it on the plan)
- Modify: `src/phil/run/engine.py:759` (pass `plan.mockup`)
- Modify: `src/phil/chat/controller.py` (`_chose`, `_plan`, the revise call site, `save_state` around line 852, and restore around line 3339)
- Test: `tests/chat/test_controller_mockups.py`, `tests/chat/test_planning.py`, `tests/run/test_engine_mockup.py` (create)

**Interfaces:**
- Consumes: `self._mockups` and `Drawn` from Task 4.
- Produces:
  - `ArchitectInput.mockup: str = ""` and `ImplementInput.mockup: str = ""`. The description, verbatim: `"When set, a rough picture the user saw and accepted: match its layout, placement and content, not its exact pixels."`
  - `Plan.mockup: str = Field(default="", description="Leave empty; filled by Phil.")`.
  - The planner overwrites `plan.mockup` with the value passed in, empty included, after every architect call. It uses `model_copy`, the way `_with_notes` handles `critic_notes`.
  - The controller keeps `self._chosen_mockup: str`. `_chose` sets it to `_mockups[approach.name].text` when the picked approach has a drawing, and to `""` otherwise or for a described approach. It goes to `planner.draft(..., mockup=...)` and to revisions.
  - Each drawing is also written to `mockups/<chat id>-<slug of name>.html` or `.txt`, and `Drawn.path` is set.
  - `save_state` gains `"mockups": {name: str(path)}` and `"chosen_mockup_path": str | None`.
  - Restore re-reads each file. A missing or unreadable file drops that entry silently.

- [ ] **Step 1: Write the failing tests**
  - `test_picked_approach_mockup_reaches_the_architect`: `show`, then `1`. The architect contract's `mockup` equals the cleaned fragment for approach 1. Picking `2` instead, when only approach 1 has a drawing, gives `mockup == ""`.
  - `test_planner_stamps_the_mockup_over_the_architects_value`: the fake architect returns a plan with `mockup="junk"`; `draft(..., mockup="M")` yields `plan.mockup == "M"`; and `mockup=""` yields `""`.
  - `test_engine_hands_the_plan_mockup_to_implementers`: a plan with `mockup="M"` gives an implement packet whose contract has `mockup == "M"` (follow `tests/run/test_engine_images.py`'s setup).
  - `test_mockups_survive_reopen`: `show`, then EOF; reopen, then `1`. The architect receives the same mockup.
  - `test_a_deleted_mockup_file_means_no_mockup`: same flow, but delete the per-approach file before reopening. The architect gets `mockup == ""` and nothing raises. *(Review Focus 4)*
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement.** The slug is lowercase alphanumerics and hyphens, capped at 40 characters, and falls back to `approach-<index>`.
- [ ] **Step 4: Run the tests and watch them pass**, then run the full suite.
- [ ] **Step 5: Commit** with the message `Mockups: carry the picked one into planning and the run`.

### Task 6: Docs

**Files:**
- Modify: `docs/guide.md` (the approach-stage section), `CHANGELOG.md` (Unreleased), and `docs/superpowers/roadmap.md` ("Visual proposals for UI work": mark it built, link the spec and plan).

- [ ] **Step 1: Write the guide section.**
  - The `show` / `show N <note>` commands and the menu item.
  - Browser vs sketch, and the no-browser rule.
  - One tab per chat with reload.
  - Where the files live.
  - That the picked mockup guides the build.
  - That nothing is drawn unless asked.
- [ ] **Step 2: Add the CHANGELOG entry and the roadmap note.**
- [ ] **Step 3: Run the full suite** (the docs tests check links, if present).
- [ ] **Step 4: Commit** with the message `Docs: mockups at the approach stage`.
