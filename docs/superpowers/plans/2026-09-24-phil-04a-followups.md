# Plan 4a (Chat): Follow-ups for Later Plans

Findings from plan 4a's task reviews and final review that were deliberately deferred. Earlier follow-ups: `2026-09-23-phil-01-followups.md`, `2026-09-23-phil-02-followups.md`, `2026-09-24-phil-03a-followups.md`, `2026-09-24-phil-03b-followups.md` (its "Plan 4" section lists the 4b work).

## Design rules learned in 4a (apply to every later plan)

- **Chat agents never read the working tree.** The architect reads a `git archive` snapshot of the base commit's tracked files (`phil.chat.snapshot.export_tree`), so untracked secrets and uncommitted changes never reach a model provider. Any new chat agent with file access gets the same snapshot.
- **One launch gate.** `phil.chat.approval.launch_problems` (run-role models + test command equal to `[project] test_cmd` or allowed by `[shell] allow`) guards every path that starts a run: the chat's approval and `phil run`. The worker runs the test command without the allowlist, so a new launch path must call it too.
- **Escape TOML section names in rich markup.** Rich treats `[models]` as a style tag and drops it; wrap literal section names in `escape(...)`.

## Plan 4b

- Everything in the 03b follow-ups "Plan 4" section not done in 4a: usage callback and cost reconciliation, tool-call telemetry, OpenRouter SDK timeout, 200-with-error as transient, `Brief`/`present()` for free-form replies, `phil show` / `/more`, `/park`, `open_issues` dedup and summary cleanup.
- Consider a lean read-only architect: now that it reads a snapshot, the deep harness mostly adds tokens.

## Before a public release

- **Provider-agnostic models (user direction, 2026-09-28).** Phil must not depend solely on OpenRouter. Support bring-your-own provider: OpenRouter, OpenAI/ChatGPT/Codex, Anthropic/Claude, Google/Gemini, a local model (e.g. Ollama), and a **custom endpoint** (a model served on the user's own server, configured with a base URL, API style and key variable). Today `[models]` strings go to LangChain's `init_chat_model` ("provider:model"), and `phil.config.PROVIDER_KEYS` only knows the key variables for openrouter, openai, anthropic and google_genai; unknown providers are not key-checked. This needs its own design: a `[providers.<name>]` section (base_url, api_key_env, kind), per-provider capability checks (tool calling, structured output), and live evals per provider.
- **Developer vs end-user diagnostics (user direction, 2026-09-28).** Library warnings about Phil's internals (e.g. a dependency's deprecation or Python-compatibility warning) matter to Phil's developers, not its users, who can't act on them. Keep them visible in development (tests, a `--debug`/dev mode) and out of end users' terminals; if something needs a user's attention, say it plainly ("this version of Phil …"), never a raw library warning.

## Dependency policy

- **Keep dependencies current (user direction, 2026-09-28).** Upgrade everything regularly (`uv lock --upgrade`), raise the `pyproject.toml` floors to the locked versions, run the full suite and a live check. Record any held-back package here with its reason.
- Held back: `openrouter` stays at 0.10.x because 0.11 caps `pydantic<2.13`, and pydantic 2.12 prints a Python 3.14 compatibility warning on import (via `langchain_core`). `pyproject.toml` requires `pydantic>=2.13`; lift the hold when `openrouter` allows it.

## Later

- The snapshot follows `git archive` rules: `.gitattributes` `export-ignore`/`export-subst` apply, submodule contents are absent, LFS files arrive as pointers. It can differ slightly from the run's worktree; document it or export with `git worktree add --detach` instead.
- `export_tree` holds the whole tar in memory; stream with `tarfile.open(fileobj=proc.stdout, mode="r|")` via `Popen` for very large repos.
- Committed symlinks that point outside the repo are skipped in the snapshot (not followed); the architect sees them as missing.
- `ChatSession.create` checks then `mkdir`s: two chats started in the same second can race (`FileExistsError`); retry on `mkdir(exist_ok=False)`.
- `repo_overview` reads the whole README before slicing, and `git ls-files` C-quotes non-ASCII names (use `-z`).
- Blank lines at the idle prompt are not written to the transcript (everything else is stored raw).
- The architect and critic keep using the snapshot of the base resolved at plan time; if HEAD moves before approval, the run starts from the newer HEAD (the start message shows the commit).
