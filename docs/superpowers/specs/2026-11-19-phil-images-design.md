# Phil: images in, and models that can see them

Date: 2026-11-19. Status: draft for review.

## Why

The user wants to show Phil what to change, not only tell it. Examples are a screenshot of their
site marked up with boxes, arrows and notes, or a new design to build. The result is always code
changes in the repo. Phil never sends an image back.

Two failures from a live test on 2026-11-19 (repo `hochoy-biz`, every agent on
`openrouter:inclusionai/ling-3.0-flash`, a text-only model served through DeepInfra) show what's
missing:

1. `NotFoundResponseError: No endpoints found that support image input`. An agent opened a PNG in
   the repo with `read_file`. deepagents hands an image file to the model as a picture, and a
   text-only model can't take one. Phil never checks what a model accepts.
2. `BadRequestResponseError: Upstream error from DeepInfra: Assistant tool call function.arguments
   must be valid JSON`. The model had written a tool call with malformed JSON arguments. Phil kept
   it in the history and resent it, and the provider rejected the whole request.

This is the first of three related pieces. The other two come later:
- **Skills.** Phil uses `SKILL.md` skills installed on the host, from approved folders. This is
  the roadmap's after-launch item 1, and the 2026-10-08 spike settled the mechanism.
- **Mockups.** Phil shows an HTML or Markdown mockup of a visual change, or asks the user to
  confirm, before it implements. This builds on skills.

## Goals

- The user drags an image file onto the input bar (the frame or the inline chat). Phil recognises
  the path the terminal pastes, attaches the image to the message, and the agents that work out
  the change see the picture.
- A turn that carries an image, whether attached by the user or opened by an agent, reaches a
  model that can see it. That's the agent's own model when it can, and otherwise the configured
  `vision` model.
- With no model available that can see, Phil says so plainly and early, and says what to set.
- Malformed tool-call arguments in the history never get resent as they are.

## Non-goals

- Mockups and confirm-before-implementing (a later piece).
- Skills (a later piece).
- Pasting raw image data from the clipboard. Terminals paste paths, not image bytes.
- Phil producing images.
- Other capability gaps: models without tool support, context-length limits, audio, video or PDF
  input.

## Anti-goals

- A text-only setup that silently gets worse. When an image can't be seen, the user hears about
  it.
- Images costing a vision model's price on agent runs that never carried an image.
  An agent run that carries one pays the vision rate throughout. That's the user's choice, made
  so the image stays visible.
- The long dragged path cluttering the conversation.

## Approaches

**When an image meets a model that can't see it**

1. **Run that turn on a vision model (chosen).** A new `vision` model setting. Any agent turn that
   carries an image runs on the `vision` model when the agent's own model can't take images. The
   agent works from the real pixels, which matters most for "build this design". The cost is that
   those turns are priced at the vision model's rate.
2. **Describe the image in words first.** The vision model writes a description, and the agent's
   own model works from that. Cheaper, but layout, spacing and colour are lost in translation.
3. **Refuse with a clear message.** Kept as the fallback when no `vision` model is set.

For an agent run that carries an image, two ways were weighed: keep the image for the whole run,
or use it once and then replace it with a text note. The user chose to keep it. A design
implemented from memory of one look is weak for spacing and colour.

**How the user gives an image:** dragging the file onto the input bar (the user's choice). Terminals
paste a dropped file as its path, so Phil detects image paths in what was typed.

## The design

### Attaching an image

- **Detection.** When a message is submitted, Phil scans it for image paths, in every form a
  terminal pastes a dropped file:
  - a bare absolute or `~` path
  - backslash-escaped spaces (`Screenshot\ 2026-11-19\ at\ 9.07.45\ AM.png`)
  - single- or double-quoted paths
  - `file://` URIs (percent-decoded)

  macOS screenshot names contain U+202F (narrow no-break space) before `AM`/`PM`, and the path must
  still match. A path counts only if the file exists and its extension is `.png`, `.jpg`, `.jpeg`,
  `.webp` or `.gif`, case-insensitive. Anything else stays plain text.
- **Chips.** Each detected path is replaced in the message text by `[image N: <file name>]`,
  numbered from 1 per message. Long names are cut with `…` to fit. That replaced text is what the
  conversation shows and what is saved.
  - In the full-screen frame, the chip appears in the input box as soon as the paste lands (the
    frame sees a paste event). The box shows the chip, not the path.
  - In the inline chat, the chip shows on the echoed line after Enter. The prompt keeps the raw
    path while the user types.
- **Limits.** At most 5 images per message and 5 MB per image (Anthropic's API refuses larger
  images, and an attachment rides every later call of its goal). For each image skipped, one notice
  says why (`Skipped big.png: over 5 MB`) and the message still goes.
- **Storage.** Each attached image is copied into the chat's folder, at
  `~/.phil/projects/<slug>/chats/<chat id>/images/<n>-<file name>`. The attachment keeps the copy's
  path, so it survives `phil --resume` and the original being deleted. The chat's saved transcript
  records each attachment's path and file name.
- **Safety.** Shown file names go through `one_line()`. Phil reads only the paths the user pasted.
  It follows no symlinks out of them and copies only regular files.

### Who sees the image

- **Intake.** The image travels with the user's message to the intake conversation (the `converse`
  agent), as image parts next to the text.
- **Answering.** A question answered directly (`/ask`, or routed to the answer path) carries the
  goal's attachments to the answerer, as image parts in its packet.
- **Planning.** When planning starts, the goal carries the chat's attachments to the designer, the
  architect and the critic, as image parts in their packets.
- **Building.** The implementer's task packets carry the images too, because it is building the
  design. The tester and the reviewer get the paths in text, without the pictures.
- **Packets.** A packet gains `images: list[Path]`. `render()` stays text. A new
  `render_content()` returns the text, then for each image a short text label, `Image: <original
  file name>` (the copy's name without its `<n>-` prefix), and the image part (base64 with its
  MIME type). The label lets the agent match a chip or the user's words ("after.png") to the
  right picture. `invoke_agent` sends that content when `images` is non-empty. The packet's
  `.retry.json` artifact names each image by its path in place of the base64.
- **Image paths in text.** Agents that get text only see the paths in a short section of their
  packet, `Images the user attached (for reference; you can't open these):`, then one `- <path>`
  line each. The reviewer has no tools and the tester's file tools are rooted in the worktree, so
  the paths are for reference only.
- **Which images, when (design call).** Attachments belong to the current goal. They build up
  across every message from the goal's first message on, answers to clarifying questions
  included. Each `converse` call carries all of the goal's attachments so far, because its earlier
  turns are kept as text only. Planning and the run get the goal's full list. A new or replaced
  goal starts with none, and `/chat` switching keeps each chat's own list. The saved chat state
  records the list, so a reopened chat still has it.
- **Budget (design call).** Each image counts as a flat 1,600 tokens toward a packet's budget and
  the pre-call cost estimate. The real cost comes from the provider's reported usage, as today.

### Reaching a model that can see

- **The setting.** `vision` is a new optional model setting, under `[models]` in `phil.toml` or the
  global `config.toml`, using the same `provider:model` strings. `phil setup` offers it and
  `phil models check` checks it. It is not a role, and no agent runs on it by default.
- **Can this model see?** A function answers `True`, `False` or `None` (unknown).
  - **OpenRouter models.** From the model list Phil already downloads and caches for prices
    (`PriceBook`, `agents/pricing.py`, `https://openrouter.ai/api/v1/models`). An entry's
    `architecture.input_modalities` containing `"image"` means True; present without it means
    False.
  - **Other providers, or a model missing from the list.** Unknown. Treated as able to see, with
    the safety net below as the backstop.
- **Choosing per turn.** Phil checks each model call (the agent's existing model-call middleware is
  the natural place). If the request carries any image part, from a packet or from a tool result
  such as `read_file` on a PNG, and the call's model is known not to see:
  - **With a `vision` model set:** that call runs on the `vision` model. An image stays in the
    agent's conversation once it's there (the user's choice), so in practice every later call of
    that agent run also carries it and also runs on the `vision` model. An implementer building a
    design keeps seeing the design. Agent runs that never carry an image stay on their own model.
    The feed shows one line per agent run the first time it happens:
    `↪ image: <agent> runs on <vision model>`.
  - **With no `vision` model set:** the call fails with a clear refusal (below).
- **The safety net.** If a provider still refuses a call because of the image, Phil retries that
  call once on the `vision` model, when one is set and differs from the model that failed.
  Otherwise the refusal below applies.
  - (Design call.) A refusal counts as an image refusal when the request carried an image and the
    provider's error text matches a short list in `failures.py`, case-insensitive:
    `support image input`, `does not support image`, `image input is not supported`,
    `vision is not supported`, `multimodal`.
  - Non-OpenRouter text-only models count as "unknown", so they hit one refusal and then the
    retry. That's accepted.
- **Every harness (design call).** The switch lives at the chat-model level, as a model that
  chooses per call between the agent's model and the `vision` model. It's not agent middleware, so
  it covers the light, lean and deep harnesses and deep sub-agents alike.
  - The `vision` chat model is built once per agent, the same way the agent's own model is
    (`chat_model(...)`: timeout, key, `used_by`), resolving its own provider through
    `provider_for_model`.
  - Each call's usage is priced against the model and provider that actually served it.
    `ModelCall` carries the provider, so a `vision` model on another provider gets its own rates.
- **The refusal.** A `Failure("refused", …)` whose headline is
  `<model> can't read images.` and whose action is
  `Set a vision model: [models] vision = "provider:model"`. Phil doesn't retry it.
- **Early warning.** When the user attaches an image, the chat checks every model that will get
  the picture: the orchestrator's (converse), and the designer's, architect's, critic's and
  implementer's. If one is known not to see and no `vision` model is
  set, Phil shows the same message at once, as a notice under the user's line. The message still
  goes, and the user decides.
- **Cost.** A call moved to the `vision` model is priced and recorded at that model's rates in the
  run's usage, under the agent that made it.

### Broken tool calls in the history

- **When.** Before each model call, Phil checks every earlier assistant tool call in the request.
  (Design call.) That covers both a message's `tool_calls` and its `invalid_tool_calls`, where
  LangChain keeps a call whose raw arguments didn't parse. A repaired invalid call becomes an
  ordinary tool call. The paired tool result keeps its content, which is usually the error the
  agent was shown. The call the model has just made is left to LangChain's existing handling, and
  its repair happens before the next send.
- **Repair.** If a call's `arguments` aren't valid JSON, Phil tries a small repair: strip trailing
  commas before `}`/`]`, and close unbalanced brackets and quotes at the end.
- **Fallback.** If the result still isn't a JSON object, the arguments become `{}`. The tool call
  and its paired tool result both stay, because providers require every tool call to keep its
  result.
- **Scope.** Only the outgoing request changes. The activity log and the transcript keep the
  original. A debug log line records each repair.
- **Location.** Repair code goes with the existing tool-argument fixes (`agents/tool_args.py`).

## Constraints

- No new dependencies. Image MIME types come from the extension, and the size limit from
  `stat()`.
- Images are sent as LangChain image content blocks. OpenRouter tool results already convert
  through `PhilChatOpenRouter` (`agents/openrouter_model.py`).
- Works in the full-screen frame, the inline chat, and piped (`LineIO`) mode, where paths are
  detected but no chip appears while typing.
- Every user-visible string that includes a file name goes through `one_line()` / `printable()`.

## Left to the builder

- Exactly where the per-call model switch lives (model-call middleware, or wrapping the chat
  model), as long as it applies to every agent and to tool-result images.
- How the frame swaps a pasted path for a chip while keeping the real path for submission.
- The path-detection implementation, within the forms listed.
- How the goal or contract carries attachments from the chat to the run's agents.
