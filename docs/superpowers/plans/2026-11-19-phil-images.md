# Images In, and Models That Can See Them: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The user drags an image onto Phil's input bar. The picture reaches the agents that plan and build the change, on a model that can see it, with a clear refusal when no such model is set. Malformed tool-call arguments in the history are repaired before they're resent.

**Architecture:**
- **Attachments.** A pure `chat/attachments.py` finds image paths in a message, swaps them for `[image N: name]` chips and copies the files into the chat folder.
- **Carrying images.** Packets carry image paths, and `invoke_agent` sends them as image parts.
- **Vision switch.** A model-call middleware (`agents/vision.py`) moves any call that carries an image to the `vision` model when the agent's model can't see. It also retries once on an image refusal. Whether a model can see comes from OpenRouter's model list, which `PriceBook` already caches.
- **Plumbing.** The chat keeps the goal's attachments and hands them to converse, design and planning, and to the run through `attachments.json` in the run dir.

**Tech Stack:** Python 3.12, LangChain v1 agents (`ModelRequest.override`), Textual 8.2.8, prompt_toolkit, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-phil-images-design.md`

## Global Constraints

- No new dependencies. MIME type comes from the extension, size from `stat()`.
- Image extensions: `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, case-insensitive.
- Limits: at most 5 images per message (`MAX_IMAGES = 5`), at most 10 MB per image (`MAX_IMAGE_BYTES = 10 * 1024 * 1024`).
- Chip text: `[image N: <file name>]`, numbered from 1 per message. Names longer than 40 cells are cut with `…`.
- Image storage: `<chat dir>/images/<n>-<file name>`, where `n` counts per chat.
- Each image counts as a flat `IMAGE_TOKENS = 1600` tokens toward packet budgets and cost estimates.
- Settings: `[models] vision = "provider:model"`, optional. It's not a role.
- Refusal: headline `<model> can't read images.`, action `Set a vision model: [models] vision = "provider:model"`. No retry.
- Feed line: `↪ image: <agent> runs on <vision model>`, once per agent run.
- Image refusal phrases (case-insensitive): `support image input`, `does not support image`, `image input is not supported`, `vision is not supported`, `multimodal`.
- Who gets pictures: converse (orchestrator), designer, architect, critic, implementer. The tester and reviewer get paths in text only (`Images the user attached: …`).
- Every user-visible string that holds a file name goes through `one_line()` / `printable()`.
- Repo files are edited only with Edit/Write. Commit with `git commit -F .git/phil-commit-msg.txt`, with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Run tests as `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q <paths>`. Never run `-m live` or `-m bench`.

## Review Focus

1. **A dragged macOS screenshot path.** Spaces are backslash-escaped, and there's a U+202F before `AM`. It is detected as one image. The same path quoted, or as a `file://` URI, is also detected.
2. **A path-like word that isn't an image or doesn't exist** (`~/notes.txt`, `/tmp/gone.png`). It stays as plain text: no chip, no error.
3. **A chat reopened with `phil --resume`.** The goal's attachments are still there, and the next converse turn still carries the pictures.
4. **An agent whose model is text-only, with no `vision` set, that `read_file`s a PNG.** The run stops with the clear refusal. It doesn't fail with a raw provider error, and Phil doesn't loop retrying.
5. **A history holding an `invalid_tool_calls` entry with `arguments='{"path": "a.py",'`.** The next request goes out with a repaired, valid JSON call and its tool result still paired.

Each line has a test in the task that owns it, marked "Review Focus n".

---

### Task 1: Find and attach images in a message

**Files:**
- Create: `src/phil/chat/attachments.py`
- Test: `tests/chat/test_attachments.py`

**Interfaces:**
- Produces:
  - `IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})`, `MAX_IMAGES = 5`, `MAX_IMAGE_BYTES = 10 * 1024 * 1024`.
  - `@dataclass(frozen=True) class Found: raw: str; path: Path`. `raw` is the exact substring in the text, and `path` is resolved to absolute.
  - `find_images(text: str) -> list[Found]`. Returns existing image files only, in text order, without duplicates.
  - `@dataclass(frozen=True) class Attached: text: str; images: tuple[Path, ...]; skipped: tuple[str, ...]`. Here `images` are the copies, and `skipped` holds one-line reasons.
  - `attach(text: str, images_dir: Path, *, first_number: int) -> Attached`. It replaces each found `raw` with a chip and copies each image to `images_dir/<n>-<name>`, where `n` starts at `first_number`. Chips number from 1 within the message.
  - `chip(index: int, name: str) -> str`.
  - `mime_type(path: Path) -> str`, giving `image/png`, `image/jpeg`, `image/webp` or `image/gif`.

- [ ] **Step 1: Write failing tests** in `tests/chat/test_attachments.py`. Use `tmp_path` image files made with `write_bytes(b"\x89PNG...")`.
  - `test_bare_and_tilde_paths` (monkeypatch `HOME`).
  - `test_backslash_escaped_macos_screenshot` (Review Focus 1). The file is `Screenshot 2026-11-19 at 9.07.45 AM.png`. The text is `look at ` plus the path with `\ ` before each ASCII space, then ` please`. It gives one `Found` whose path exists.
  - `test_quoted_and_file_uri`. A single-quoted path, a double-quoted path, and `file:///…/a%20b.png` all resolve.
  - `test_non_images_and_missing_stay_text` (Review Focus 2). `~/notes.txt` and `/tmp/gone.png` give `[]`.
  - `test_attach_replaces_with_chips_and_copies`. `attach("see /x/a.png and /x/b.png", d, first_number=3)` gives `.text == "see [image 1: a.png] and [image 2: b.png]"`, and `[p.name for p in .images] == ["3-a.png", "4-b.png"]`. The copies exist.
  - `test_limits`. Six images give 5 attached, plus `skipped == ("Skipped f.png: only 5 images per message",)`. A sparse 11 MB file (`truncate`) is skipped with `"Skipped big.png: over 10 MB"`, and its path stays as text.
  - `test_long_name_chip_is_cut`. A 60-character name gives a chip of at most 40 cells inside the brackets, ending in `…`.
  - `test_symlink_and_directory_rejected`. A symlinked `.png` and a directory named `x.png` are not attached.
  - `test_escapes_in_names_are_printable`. A name containing `\x1b` shows `?` in the chip.
- [ ] **Step 2: Run them.** Expect `ModuleNotFoundError`.
- [ ] **Step 3: Implement.**
  - Tokenise candidates with one regex pass:
    - quoted strings
    - `file://\S+`
    - runs of `(?:\\ |[^\s'"])+` that start with `/` or `~/`

    Normalise each: drop quotes, percent-decode URIs, and replace `\ ` with a space.
  - Accept a candidate only if `Path.expanduser()` is a regular file (not a symlink), and its extension is in `IMAGE_EXTS`.
  - Names in chips and in skip reasons go through `one_line()`.
- [ ] **Step 4: Run them.** Expect PASS.
- [ ] **Step 5: Commit** with "Chat: find and attach images in a message".

---

### Task 2: Know which models can see, and the `vision` setting

**Files:**
- Modify:
  - `src/phil/config.py`: allow `vision` in `[models]` (`_validate_model_roles`), and add `PhilConfig.vision_model() -> str | None`.
  - `src/phil/agents/pricing.py`: `PriceBook` also parses `architecture.input_modalities` for every entry, priced or not, into `self._modalities: dict[str, frozenset[str]]`. Add `PriceBook.modalities(ids: Sequence[str]) -> frozenset[str] | None`, which returns the first id found or None.
  - `src/phil/agents/check.py`: `phil models check` also checks `vision` when it's set, labelled `vision`.
  - `src/phil/setup/flow.py`: add an optional vision step after the classifier step, modelled on `_classifier_step`. Enter skips it.
- Create: `src/phil/agents/vision.py` with `sees_images(model: str, provider: ProviderSpec | None, book: PriceBook) -> bool | None`. The result is True or False from OpenRouter modalities, and None when unknown (a non-OpenRouter model, or one missing from the list). Use `pricing.book_ids` to find the ids.
- Test: `tests/agents/test_pricing.py`, `tests/agents/test_vision.py`, `tests/test_config.py` (or wherever `[models]` validation is tested), `tests/agents/test_check.py`, `tests/setup/test_flow.py`.

**Interfaces:**
- Produces: `PhilConfig.vision_model()`, `PriceBook.modalities(ids)`, `sees_images(model, provider, book) -> bool | None`.

- [ ] **Step 1: Write failing tests.**
  - `test_vision_key_accepted_and_read`: `PhilConfig(models={"high": "x:y", "vision": "openrouter:a/b"}).vision_model() == "openrouter:a/b"`. `None` when unset.
  - `test_modalities_parsed_for_unpriced_entries_too`.
  - `test_sees_images`: OpenRouter `a/b` with `["text","image"]` gives True, `["text"]` gives False, an unlisted model gives None, and an `ollama:` model gives None.
  - `test_models_check_includes_vision_when_set`.
  - `test_setup_offers_an_optional_vision_model`: Enter skips it, and an entered model is written under `[models] vision`.
- [ ] **Step 2: Run them.** Expect FAIL.
- [ ] **Step 3: Implement** as listed under Files.
- [ ] **Step 4: Run** `tests/agents tests/setup tests/test_config.py`. Expect PASS.
- [ ] **Step 5: Commit** with "Models: a vision setting, and which models can see".

---

### Task 3: Repair malformed tool calls before resending

**Files:**
- Modify:
  - `src/phil/agents/tool_args.py`: add `repair_arguments(raw: str) -> dict`, `repaired_history(messages: list) -> list | None` (None when nothing changed), and `ToolCallRepair`, a middleware whose `wrap_model_call` sends `request.override(messages=…)` when the history changed.
  - `src/phil/agents/factory.py`: add `ToolCallRepair()` to the middleware of the light (`~:233`), lean (`~:276`), deep (`~:298`) and deep sub-agent (`~:327`) agents.
- Test: `tests/agents/test_tool_args.py`.

**Interfaces:**
- Produces: `repair_arguments`, `repaired_history`, `ToolCallRepair`.

- [ ] **Step 1: Write failing tests.**
  - `repair_arguments('{"a": 1,}') == {"a": 1}`.
  - `repair_arguments('{"path": "a.py",') == {"path": "a.py"}`.
  - `repair_arguments('{"x": "unterminated') == {"x": "unterminated"}`.
  - `repair_arguments("not json") == {}`.
  - `repair_arguments('[1,2]') == {}`, because the result must be a dict.
  - `test_invalid_tool_call_becomes_valid_and_keeps_its_result` (Review Focus 5). An `AIMessage` with `invalid_tool_calls=[{"id": "c1", "name": "read_file", "args": '{"path": "a.py",', "type": "invalid_tool_call"}]`, followed by a `ToolMessage(tool_call_id="c1")`. The repaired history has an `AIMessage` with `tool_calls[0]["args"] == {"path": "a.py"}`, no `invalid_tool_calls`, and the same `ToolMessage` after it.
  - `test_valid_history_is_left_alone`: the result is `None`.
  - `test_middleware_sends_the_repaired_history`, using a fake handler.
- [ ] **Step 2: Run them.** Expect FAIL.
- [ ] **Step 3: Implement.** Repair only:
  - strip a trailing comma before `}` or `]`, or at the end
  - close an open string
  - close unbalanced `{` and `[`

  Then `json.loads`. If that fails or isn't a dict, use `{}`. Log each repair at debug level on the `phil.agents.tool_args` logger. Never mutate the input messages; copy them.
- [ ] **Step 4: Run** `tests/agents`. Expect PASS.
- [ ] **Step 5: Commit** with "Agents: repair malformed tool calls before resending".

---

### Task 4: Packets carry images; agents send them as pictures

**Files:**
- Modify:
  - `src/phil/packets/packet.py`:
    - `Packet.images: list[str] = []`, as absolute paths.
    - `build_packet(..., images: Sequence[Path] = ())` counts `IMAGE_TOKENS` per image in `used` and in `tokens`.
    - `Packet.render_content() -> str | list[dict]` returns `render()` when there are no images. Otherwise it returns `[{"type": "text", "text": render()}, {"type": "image", "base64": …, "mime_type": …}, …]`, reading each file and using `attachments.mime_type`.
    - A missing image file is left out and noted in the text: `(image <name> is no longer available)`.
    - `image_paths_section(paths) -> str` builds the text-only section `Images the user attached:\n- <path>…`.
  - `src/phil/agents/invoke.py:~377`: the first user message's content is `packet.render_content()`.
- Test: `tests/packets/test_packet.py`, `tests/agents/test_invoke.py`.

**Interfaces:**
- Consumes: `mime_type` (Task 1).
- Produces: `Packet.images`, `build_packet(images=)`, `Packet.render_content()`, `image_paths_section()`, `IMAGE_TOKENS = 1600` (in `packet.py`).

- [ ] **Step 1: Write failing tests.**
  - `test_images_add_tokens_and_can_overflow_the_budget`.
  - `test_render_content_has_text_then_image_parts` (asserts the mime type and base64 round-trip).
  - `test_missing_image_noted_not_crashing`.
  - `test_invoke_agent_sends_image_parts`. Using the fake agent factory, capture the messages and assert the content is a list whose second part has `type == "image"`.
- [ ] **Step 2: Run them.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/packets tests/agents`. Expect PASS.
- [ ] **Step 5: Commit** with "Packets: images travel as picture parts".

---

### Task 5: The vision switch, the safety net, and the refusal

**Files:**
- Modify:
  - `src/phil/agents/vision.py`. Add:
    - `has_image(messages) -> bool`, true for any content part with `type` `image`/`image_url`, in any message, including tool results.
    - `ImageRefusal(Exception)`, carrying the model.
    - `is_image_refusal(exc) -> bool`, which checks the status, the provider detail, and the phrases in Global Constraints.
    - `VisionSwitch(own_model: str, sees: bool | None, vision: tuple[str, Any] | None, notice: Callable[[str], None] | None)`, a middleware. Its `wrap_model_call` does this:
      1. If `has_image` is true and `sees is False`: with `vision` set, call `handler(request.override(model=vision_chat_model))`, emit the notice once, and record the served model. Without `vision`, raise `ImageRefusal(own_model)`.
      2. Otherwise call `handler(request)`. If that raises and `is_image_refusal`, and `vision` is set and differs from the model that failed, retry once on the vision model. Otherwise re-raise, as `ImageRefusal` when it's an image refusal and no vision model is set.
  - `src/phil/agents/factory.py`: `build_agent` takes `vision: tuple[str, Any] | None` (the model string and its built chat model) and `sees: bool | None`, and adds `VisionSwitch` as the first middleware for every harness, deep sub-agents included.
  - `src/phil/agents/invoke.py`:
    - Resolve `vision = ctx.config.vision_model()`. Build its chat model with `chat_model(vision, timeout_s, provider_for_model(...), used_by=("vision",))`.
    - Compute `sees_images(model, provider, price_book_for(ctx))`, and pass both into the factory.
    - The `AgentContext` gains `notice: Callable[[str], None] | None = None`, passed to `VisionSwitch` with the agent's display name.
  - Usage pricing. `ModelCall` (`agents/collector.py`) gains `provider: str | None = None`. Calls served by the vision model are recorded with its model and provider, so `_usage`/`_estimate` price them at the vision model's rates. Use `ModelCall.model` and `provider` when set, falling back to the configured model and provider.
  - `src/phil/agents/failures.py`: `_classify` maps `ImageRefusal` to `Failure("refused", f"{model} can't read images.", "Phil won't retry this.", 'Set a vision model: [models] vision = "provider:model"')`.
- Test: `tests/agents/test_vision.py`, `tests/agents/test_failures.py`, `tests/agents/test_invoke.py`.

**Interfaces:**
- Consumes:
  - `sees_images`, `PhilConfig.vision_model()` (Task 2)
  - `render_content` (Task 4)
- Produces:
  - `has_image`, `is_image_refusal`, `ImageRefusal`, `VisionSwitch`
  - `AgentContext.notice`
  - `ModelCall.provider`

- [ ] **Step 1: Write failing tests**, using fake handlers and fake chat models.
  - `test_image_call_moves_to_vision_when_model_cant_see`. Assert the overridden model and one notice `↪ image: implementer runs on openrouter:anthropic/claude-sonnet-5`, then a second call emits no new notice.
  - `test_text_only_call_stays_on_own_model`.
  - `test_blind_model_without_vision_raises_image_refusal` (Review Focus 4). A history holding a `read_file` tool result with an image part raises `ImageRefusal`. `classify` gives the refusal headline `openrouter:inclusionai/ling-3.0-flash can't read images.`, with no retry.
  - `test_unknown_model_refused_then_retried_on_vision`. The handler raises an error whose text is `No endpoints found that support image input` on the first model, and the second call succeeds on vision.
  - `test_refusal_not_retried_twice`.
  - `test_vision_calls_priced_at_vision_rates`. Usage for a moved call uses the vision model's price.
- [ ] **Step 2: Run them.** Expect FAIL.
- [ ] **Step 3: Implement** as listed under Files.
- [ ] **Step 4: Run** `tests/agents`, then the full suite. Expect PASS.
- [ ] **Step 5: Commit** with "Agents: a turn with an image runs on a model that can see".

---

### Task 6: The chat attaches images and sends them to intake and planning

**Files:**
- Modify:
  - `src/phil/chat/controller.py`:
    - **Attaching.** In `_input`, before `session.user(...)`, run `attach(raw, <chat dir>/images, first_number=len(self._attachments)+1)` on every input that starts or continues a goal. Commands starting with `/` are skipped.
    - **Saving the message.** Record and dispatch the chipped text. Extend `self._attachments: list[Path]`, the goal's attachments.
    - **Notices.** Print each `skipped` reason as a `phil.warn` notice.
    - **Inline echo.** prompt_toolkit already echoed the raw path, so when the IO is not the frame, print the chips as one muted line under it: `attached [image 1: a.png], [image 2: b.png]`.
    - **Early warning.** When attachments were added, `vision_model()` is None, and any of the converse, designer, architect, critic or implementer models is `sees_images(...) is False`, print the refusal text once as a notice.
    - **Resetting.** `_attachments` resets when a goal is replaced or a new goal begins, and is saved and restored in `state.json` as path strings.
    - **Handing images on.** Pass `images=self._attachments` to `converse(...)`, the designer call and `plan(...)`/critic.
    - **Notices from agents.** Set `ctx.notice` so `↪ image:` lines print as muted feed lines.
  - `src/phil/chat/planning.py`, `src/phil/chat/design.py`: `converse`, `design`, architect and critic take `images: Sequence[Path] = ()` and pass it to `build_packet(images=...)`.
  - `src/phil/chat/session.py`: the transcript `user` record gains `attachments: list[str]` when there are any.
  - `src/phil/ui/screen/widgets.py`:
    - `PromptBox` handles `events.Paste`. If `find_images(text)` finds anything, it inserts the text with each `raw` replaced by its chip and remembers `chip -> raw` in `self._chips`.
    - On Enter, it expands the chips back to raw paths before posting `Submitted`, so the controller's `attach` sees real paths. The echo keeps showing the chip.
    - `ScreenIO`'s echo shows the chipped text. Have `ScreenIO.ask` echo after `attach`, or echo the box text before expansion. Choose one, and state which in the report.
- Test:
  - `tests/chat/test_controller_images.py`
  - `tests/ui/screen/test_paste.py`
  - `tests/chat/test_planning.py` / `test_design.py` (images passed into packets)

**Interfaces:**
- Consumes:
  - `attach`, `find_images`, `chip` (Task 1)
  - `sees_images`, `vision_model` (Task 2)
  - `build_packet(images=)` (Task 4)
  - `AgentContext.notice` (Task 5)
- Produces:
  - `ChatController._attachments`
  - the `images=` parameter on `converse`, `design`, `plan` and critic

- [ ] **Step 1: Write failing tests.**
  - `test_a_dragged_path_becomes_a_chip_and_is_copied`. The transcript text holds the chip, and the file sits in `chats/<id>/images/1-a.png`.
  - `test_attachments_reach_converse_and_planning_packets`. Capture the packets and assert their `images`.
  - `test_attachments_survive_resume` (Review Focus 3). Save, restore a new controller, and the next converse packet still has the image.
  - `test_new_goal_starts_without_attachments`.
  - `test_early_warning_when_no_model_can_see`. Use a fake price book with the model text-only and no vision. The warning appears once.
  - `test_skipped_image_notice`.
  - `test_inline_prints_attached_line`.
  - Frame (pilot): `test_paste_of_an_image_path_shows_a_chip_and_submits_the_path`. Paste the path, and the box text shows `[image 1: a.png]`. After Enter, `ask` returns the raw path, and the echo shows the chip.
- [ ] **Step 2: Run them.** Expect FAIL.
- [ ] **Step 3: Implement** as listed under Files.
- [ ] **Step 4: Run** `tests/chat tests/ui`, then the full suite. Expect PASS.
- [ ] **Step 5: Commit** with "Chat: drag an image in; intake and planning see it".

---

### Task 7: The run's agents get the images

**Files:**
- Modify:
  - `src/phil/run/launch.py`: `prepare_run(..., images: Sequence[Path] = ())` writes `<run dir>/attachments.json`, as `{"images": [<absolute path>, …]}`, when there are any.
  - The chat's call to `prepare_run` passes `self._attachments`.
  - `src/phil/run/engine.py`:
    - Read `attachments.json` once at engine start, with `[]` when it's missing or unreadable.
    - `_implement_packet` passes `images=`.
    - The tester (`~:1171`) and reviewer (`~:1260`) packets append `image_paths_section(paths)` to their text.
    - Set `AgentContext.notice` to append a `notice` event (`run_events(...).append("notice", message=…)`), so the chat's watcher shows `↪ image:` lines.
- Test: `tests/run/test_engine.py` (or the nearest engine test file), `tests/run/test_launch.py`.

**Interfaces:**
- Consumes:
  - `image_paths_section`, `build_packet(images=)` (Task 4)
  - `AgentContext.notice` (Task 5)
  - `_attachments` (Task 6)

- [ ] **Step 1: Write failing tests.**
  - `test_prepare_run_writes_attachments`.
  - `test_implementer_packet_carries_images`.
  - `test_tester_and_reviewer_get_paths_not_pictures`.
  - `test_missing_attachments_file_is_fine`.
  - `test_vision_notice_becomes_a_run_event`.
- [ ] **Step 2: Run them.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/run`, then the full suite. Expect PASS.
- [ ] **Step 5: Commit** with "Run: the implementer sees the user's images".

---

### Task 8: Guide, changelog, config template

**Files:**
- Modify:
  - `docs/guide.md`: a short "Showing Phil an image" section covering:
    - dragging an image in
    - chips
    - the limits
    - the `vision` setting and when it's used
    - the refusal
  - The guide's config reference gets `vision`.
  - `CHANGELOG.md`: one Unreleased entry for images, and one for repairing tool calls.
  - The `phil.toml` template written by `phil init`/setup, if it lists models: add a commented `# vision = "openrouter:…"   # reads images when an agent's model can't`.
- Test: `tests/release`, `tests/test_contract_descriptions.py`, and any template test.

- [ ] **Step 1: Edit the docs.**
- [ ] **Step 2: Run** the doc and release tests. Expect PASS.
- [ ] **Step 3: Commit** with "Guide: images and the vision model".

### Task 9 (user): live check

Install with `uv tool install --editable . --force`. Then, in `hochoy-biz` (all agents on `ling-3.0-flash`):

1. Drag a screenshot in with no `vision` set. Expect the early warning.
2. Set `[models] vision = "openrouter:anthropic/claude-sonnet-5"`, drag a marked-up screenshot of the site, and ask for the change. Expect:
   - the chip
   - the `↪ image:` lines
   - planning that reflects the picture
   - an implementer that builds it
3. Re-run a chat that edits files for a while. Expect no DeepInfra `function.arguments must be valid JSON` error.
