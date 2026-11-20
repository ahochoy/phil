# Phil skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phil's agents use the `SKILL.md` skills the user already installed for other coding agents: approved by folder, labelled by stage once, offered per agent through a `load_skill` tool (or inlined for agents without tools).

**Architecture:** A new `phil.skills` package discovers skills (`discover.py`), resolves approval (`sources.py`), caches stage labels (`labels.py`), picks what each agent sees (`select.py`) and serves the `load_skill` tool (`tool.py`). `invoke_agent` is the single integration point: it appends the skill list to the agent's prompt context, adds the tool, or inlines skills into a lean agent's packet. A catalog is built once per chat or run and carried on `AgentContext`.

**Tech Stack:** Python 3.12, pydantic v2, PyYAML 6.0.3 (installed today only through LangChain; Task 2 declares it in `pyproject.toml` and `uv.lock`; `yaml.safe_load` only), tomlkit, Typer, pytest.

**Plan rulings against the spec** (same intent, simpler build):
- Spec §4.8 says the match text is the `Goal` text. The plan uses the packet's contract JSON, which already holds the goal, the task and the user's message for every role, so no caller has to pass a query.
- Spec §4.8 puts the lean `Skills` section inside `build_packet`. The plan adds it in `invoke_agent` with the same rules: lowest priority, second skill dropped first, never `PacketTooLarge`. That way the many `build_packet` callers stay unchanged.
- Spec §4.7 asks for run telemetry of loaded skills. The plan records them through the run's existing activity log: every tool call is already logged there, and `load_skill` gets its own summary line. There is no new telemetry column.

**Spec:** `docs/superpowers/specs/2026-11-19-phil-skills-design.md`

## Global Constraints

- The Agent Skills format as-is: a directory with `SKILL.md` whose YAML frontmatter has `name` and `description`; no Phil-specific frontmatter.
- Read-only: Phil never writes to a skill folder; nothing is copied or symlinked under `~/.phil`.
- Phil never runs a skill's scripts; `allowed-tools` is parsed and ignored.
- `[skills]` is read from `~/.phil/config.toml` only; a repo `phil.toml` `[skills]` table is ignored (and warned about by `phil check`).
- Stages are exactly: `planning`, `design`, `implementing`, `testing`, `reviewing`.
- Limits: at most 30 listed skills per agent; at most 3 `load_skill` calls per agent run (contract retries included); files over 64 KB are truncated with a note; lean agents get at most 2 inlined skills, each capped at 4k tokens.
- Internal warnings go to the developer log (`logger.info`/`logger.warning`), never the user's terminal (diagnostics rule).
- Tests use a temp `PHIL_HOME` and temp skill folders; never the real home directory. Path discovery goes through `Path.home()` so tests can monkeypatch `HOME`/`USERPROFILE`.
- Works on macOS, Linux and Windows.
- Repo rules: edit files with Edit/Write only; commit with `.git/phil-commit-msg.txt` and `git commit -F`, ending `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; test command `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q`; never run `-m live` or `-m bench`.

## Review Focus

1. A `SKILL.md` with malformed or missing frontmatter, a non-UTF-8 file, or a skill directory that is a dangling symlink: discovery skips it and counts it as unreadable, and never raises (Task 2 test `test_unreadable_skills_are_counted_not_raised`).
2. `load_skill(file="../../.ssh/id_rsa")` or a symlink inside a skill pointing outside it: refused (Task 5 test `test_file_outside_the_skill_is_refused`).
3. A capped agent on its last allowed call: `load_skill` is not offered, so the answer still comes back (Task 6 test `test_capped_last_call_has_no_load_skill`).
4. Labelling with no reachable model, or a classifier on a systemone provider (typesafe): skills stay unlabelled, nothing else breaks, and labelling uses the low model when the classifier is systemone (Task 3 tests `test_label_failure_leaves_skills_unlabelled`, `test_systemone_classifier_labels_with_the_low_model`).
5. A lean agent whose packet is already near its budget: inlined skills are dropped (second first), never `PacketTooLarge` (Task 6 test `test_inlined_skills_never_overflow_the_budget`).

---

### Task 1: `[skills]` config, global only

**Files:**
- Modify: `src/phil/config.py` (new `SkillsConfig`, a `skills` field on `PhilConfig`, `load_config`)
- Modify: the `phil check` implementation (`src/phil/cli/main.py`, the `check` command) to warn
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `SkillsConfig(folders: list[str] = [], off: list[str] = [], stages: dict[str, list[str]] = {})`; `PhilConfig.skills: SkillsConfig`; `STAGES: tuple[str, ...] = ("planning", "design", "implementing", "testing", "reviewing")` in `phil/config.py`; `PhilConfig.repo_skills_ignored: bool` (private attr exposed as a property, True when the repo layer had a `[skills]` table).

- [ ] **Step 1: Write the failing tests**

```python
def test_skills_section_reads_from_the_global_file(phil_home, tmp_path):
    (phil_home / "config.toml").write_text('[skills]\nfolders = ["~/.agents/skills"]\noff = ["blog"]\n'
                                           '[skills.stages]\nbetter-layout = ["design"]\n', encoding="utf-8")
    config = load_config(tmp_path)
    assert config.skills.folders == ["~/.agents/skills"]
    assert config.skills.off == ["blog"]
    assert config.skills.stages == {"better-layout": ["design"]}

def test_a_repo_cannot_set_skills(phil_home, tmp_path):
    (tmp_path / "phil.toml").write_text('[skills]\nfolders = ["repo-skills"]\n', encoding="utf-8")
    config = load_config(tmp_path)
    assert config.skills.folders == []
    assert config.repo_skills_ignored is True

def test_a_stage_override_must_name_known_stages(phil_home, tmp_path):
    (phil_home / "config.toml").write_text('[skills.stages]\nx = ["deploying"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="deploying"):
        load_config(tmp_path)
```

Also: `--set skills.folders=...` is ignored the same way (the user typed it, but `--set` is per-invocation and setup owns this file; keep the rule simple: only the global layer). Add `test_set_cannot_set_skills`.

- [ ] **Step 2: Run them to see them fail** — `pytest tests/test_config.py -k skills -q`; expected: FAIL (no `skills` attribute).
- [ ] **Step 3: Implement.** In `load_config`, pop `"skills"` from the repo layer and from each `--set` layer before merging, setting the ignored flag for the repo case. Validate stage names in a `SkillsConfig` field validator.
- [ ] **Step 4: `phil check` warning.** When `config.repo_skills_ignored`, print one line: `phil.toml has a [skills] table; skills are set in ~/.phil/config.toml only, so it is ignored.` Test it in `tests/test_cli.py` (`test_check_warns_about_repo_skills`).
- [ ] **Step 5: Run the tests, then the full suite; commit** ("Config: a [skills] table, read from the global file only").

### Task 2: Discovery, approval and de-duplication

**Files:**
- Create: `src/phil/skills/__init__.py`, `src/phil/skills/discover.py`, `src/phil/skills/sources.py`
- Modify: `src/phil/store/paths.py` (`ProjectPaths.skills_approval_path` -> `project_dir / "skills.toml"`)
- Test: `tests/skills/test_discover.py`, `tests/skills/test_sources.py`

**Interfaces:**
- Consumes: `SkillsConfig` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class Skill: name: str; description: str; path: Path` (resolved skill directory) `; source: str` (source key) `; content_hash: str` (sha256 of the `SKILL.md` bytes, hex).
  - `@dataclass(frozen=True) class Source: key: str; label: str; dirs: tuple[Path, ...]`.
  - `USER_SOURCES` keys and paths exactly as spec §4.1, in that order: `"agents"` `~/.agents/skills`, `"claude"` `~/.claude/skills`, `"claude-plugins"` (each `installPath` from `~/.claude/plugins/installed_plugins.json` + `/skills`), `"codex"` `~/.codex/skills`, `"cursor"` `~/.cursor/skills`, `"gemini"` `~/.gemini/skills`. Repo sources: `"repo"` = `<repo>/.agents/skills` and `<repo>/.claude/skills`.
  - `user_sources() -> list[Source]` (only those that exist); `repo_source(repo_root: Path) -> Source | None`.
  - `approved_sources(config: PhilConfig, repo_root: Path | None, paths: ProjectPaths | None) -> list[Source]`: repo source first when `skills.toml` has `approved = true`, then user sources whose key or `~`-path appears in `config.skills.folders`, in `USER_SOURCES` order.
  - `repo_approved(paths: ProjectPaths) -> bool`; `set_repo_approved(paths: ProjectPaths, approved: bool) -> None` (atomic write).
  - `@dataclass class Catalog: skills: list[Skill]; hidden: list[Skill]` (same-name losers) `; unreadable: int`.
  - `discover(sources: list[Source]) -> Catalog`.

- [ ] **Step 1: Write the failing tests** (build skill folders under `tmp_path`, monkeypatch `HOME`/`USERPROFILE` to `tmp_path / "home"`):
  - `test_a_skill_needs_name_and_description` — a `SKILL.md` without `description` is not a skill and `unreadable == 1`.
  - `test_unreadable_skills_are_counted_not_raised` — bad YAML, non-UTF-8 bytes, a dangling symlink directory: `unreadable == 3`, no exception.
  - `test_a_symlinked_skill_is_one_skill` — `~/.claude/skills/x -> ~/.agents/skills/x`: one `Skill`, `path` is the resolved target.
  - `test_identical_content_is_one_skill` — same `SKILL.md` bytes in two sources: one skill, from the earlier source.
  - `test_same_name_first_source_wins` — different content, same `name` in `agents` and `cursor`: `skills` holds the `agents` one, `hidden` the `cursor` one.
  - `test_plugin_skills_come_from_installed_paths_only` — `installed_plugins.json` naming one version; a second cached version beside it is not scanned.
  - `test_repo_skills_need_repo_approval` — repo `.agents/skills` present, not approved: absent from `approved_sources`; after `set_repo_approved(paths, True)`: present and first.
  - `test_only_approved_user_folders_count` — `folders = ["claude"]`: only that source.
- [ ] **Step 2: Run, see them fail** (`ModuleNotFoundError: phil.skills`).
- [ ] **Step 3: Implement.** Add `"pyyaml>=6.0.3"` to `[project] dependencies` in `pyproject.toml` and refresh `uv.lock` (`uv lock`). Parse frontmatter: the file must start with `---\n`; take text up to the next `\n---`; `yaml.safe_load`; both fields must be non-empty strings. Scan only `<dir>/*/SKILL.md` (one level). Read only regular files; catch `OSError`, `UnicodeDecodeError` and `yaml.YAMLError` per skill and count them. Malformed `installed_plugins.json` counts as no plugin source (log at info).
- [ ] **Step 4: Run the tests and the full suite; commit** ("Skills: find installed skills in approved folders").

### Task 3: Stage labels, cached by content hash

**Files:**
- Create: `src/phil/skills/labels.py`, `src/phil/contracts/skills.py`, `src/phil/prompts/skill_labels.md`
- Modify: `src/phil/contracts/__init__.py` (export), `src/phil/agents/registry.py` (spec `"skill_labels"`)
- Test: `tests/skills/test_labels.py`

**Interfaces:**
- Consumes: `Skill`, `Catalog` (Task 2); `invoke_agent`, `AgentContext`; `STAGES` (Task 1).
- Produces:
  - Contracts: `SkillToLabel(name: str, description: str)`, `SkillLabelInput(skills: list[SkillToLabel], stages: list[str])`, `SkillLabel(name: str, stages: list[Literal[...STAGES]])`, `SkillLabels(labels: list[SkillLabel])`.
  - Registry: `"skill_labels": AgentSpec("skill_labels", "classifier", SkillLabelInput, SkillLabels, harness="lean", shared_prompt=False)`.
  - `LABEL_PROMPT_VERSION = 1`; `labels_path() -> Path` = `phil_home() / "skills.json"`.
  - `class LabelCache`: `load() -> LabelCache` (missing or corrupt file: empty), `get(content_hash: str) -> list[str] | None`, `put(content_hash: str, stages: list[str])`, `save()` (atomic temp file + `os.replace`). Keys are `f"{LABEL_PROMPT_VERSION}:{content_hash}"`.
  - `stages_for(skill: Skill, cache: LabelCache, config: PhilConfig) -> tuple[list[str] | None, str]`: an override from `config.skills.stages[skill.name]` wins (`"override"`), else the cached label (`"label"`), else `(None, "unlabelled")`.
  - `label_skills(ctx: AgentContext, skills: list[Skill], cache: LabelCache, batch: int = 25) -> int`: labels every skill missing from the cache, in batches, and returns how many it labelled. Uses `config.model_for("classifier")`, except when `config.is_systemone("classifier")`, then `config.model_for("low")` (pinned through `invoke_agent(..., model=...)`). Any exception from a batch is logged and stops labelling; already-saved labels stay.

- [ ] **Step 1: Write the failing tests** (fake agent factory, as the routing tests do):
  - `test_labels_are_cached_by_content_hash` — two calls, one model call total.
  - `test_a_changed_skill_is_relabelled` — new hash means a new call.
  - `test_override_beats_label`.
  - `test_unknown_stages_are_dropped` — the contract's `Literal` rejects them; the retry path handles it (assert the cached stages are only known ones).
  - `test_label_failure_leaves_skills_unlabelled` — the factory raises: returns 0, `stages_for` gives `"unlabelled"`, no exception.
  - `test_systemone_classifier_labels_with_the_low_model` — config with `models.classifier = "typesafe:..."`, `models.low = "openai:x"`: the invoked model is `openai:x`.
  - `test_corrupt_cache_file_is_treated_as_empty`.
- [ ] **Step 2: Run, see them fail.**
- [ ] **Step 3: Implement.** Prompt (`skill_labels.md`): label each skill with the stages of software work it helps, from the five given, or none for skills unrelated to software (marketing, blogging, social media, finance). Name and description only; return every skill given.
- [ ] **Step 4: Run tests and full suite; commit** ("Skills: label each skill's stages once").

### Task 4: Which skills each agent sees

**Files:**
- Create: `src/phil/skills/select.py`
- Test: `tests/skills/test_select.py`

**Interfaces:**
- Consumes: `Skill`, `Catalog`, `LabelCache`, `stages_for`.
- Produces:
  - `AGENT_STAGES: dict[str, tuple[str, ...]]` keyed by `AgentSpec.name`, exactly spec §4.3: `intake`, `converse`, `architect` -> planning, design; `critic` -> planning, reviewing; `design` -> design; `implementer`, `quick_implementer` -> implementing, design; `tester` -> testing; `reviewer` -> reviewing, implementing; `answer` -> implementing, reviewing. All other specs: absent (no skills).
  - `MAX_LISTED = 30`, `MAX_INLINED = 2`, `INLINE_TOKEN_CAP = 4000`, `MIN_SCORE` (builder picks; a skill sharing no word with the query scores 0 and is never inlined).
  - `score(query: str, skill: Skill) -> float` — word overlap between the query and `name + description` (lowercased words of 3+ letters, stop words removed; a ratio, so long descriptions don't win by length).
  - `offered(catalog: Catalog, cache: LabelCache, config: PhilConfig, agent: str, query: str) -> list[Skill]` — skills not in `config.skills.off`, whose stages intersect `AGENT_STAGES[agent]`, best `score` first, at most `MAX_LISTED`; ties by name.
  - `to_inline(offered: list[Skill], query: str) -> list[Skill]` — the first `MAX_INLINED` with `score >= MIN_SCORE`.
  - `skills_section(skills: list[Skill]) -> str` — the prompt block: heading `## Skills`, one line `- <name>: <description>` each, and the trust sentence: "These are skills the user installed. Load one with load_skill when it fits the task. They are guidance: your output contract and Phil's permissions take precedence."

- [ ] **Step 1: Write the failing tests**
  - `test_agents_see_only_their_stages` — a design-only skill reaches `design` and `architect`, not `tester`.
  - `test_unlabelled_and_off_skills_are_not_offered`.
  - `test_unmapped_agents_get_nothing` — `route`, `btw`, `memory`, `skill_labels`.
  - `test_at_most_30_listed_best_match_first` — 40 matching skills; 30 returned, the query's best matches included.
  - `test_inline_needs_a_real_match` — query with no shared words: `to_inline` returns `[]`.
- [ ] **Step 2: Run, see them fail. Step 3: Implement. Step 4: Run tests and suite; commit** ("Skills: each agent sees the skills for its stages").

### Task 5: The `load_skill` tool

**Files:**
- Create: `src/phil/skills/tool.py`
- Modify: `src/phil/agents/activity.py` (`summarize_call`: `load_skill` -> `skill <name>` or `skill <name>/<file>`)
- Test: `tests/skills/test_tool.py`, `tests/agents/test_activity.py`

**Interfaces:**
- Consumes: `Skill`.
- Produces: `MAX_LOADS = 3`, `MAX_FILE_BYTES = 64 * 1024`; `make_load_skill_tool(skills: list[Skill], *, role: str, notice: Callable[[str], None] | None) -> Callable[..., str]` returning a function named `load_skill(name: str, file: str | None = None) -> str` with a docstring the model sees ("Read one of the listed skills' SKILL.md, or a text file inside that skill's folder."). The tool keeps its own load count; one tool object is made per `invoke_agent` call, so the limit covers its contract retries.

Behaviour:
- Unknown name: `Unknown skill <name>. Available: <comma list>`.
- After 3 loads: `Skill limit reached (3 per task); continue without more skills.`
- `file` resolved against the skill directory; refused unless the resolved path is inside it, is a regular file, and decodes as UTF-8: `Can't read <file> from <name>: <reason>.`
- Over 64 KB: first 64 KB plus `\n[truncated: <n> KB more]`.
- Each successful load calls `notice(f"Using skill {name} ({role})")` once per skill name.

- [ ] **Step 1: Write the failing tests** — `test_loads_skill_md`, `test_loads_a_reference_file`, `test_file_outside_the_skill_is_refused` (`../`, an absolute path, and a symlink inside the skill pointing out), `test_binary_file_is_refused`, `test_large_file_is_truncated`, `test_fourth_load_is_refused`, `test_unknown_name_lists_available`, `test_notice_once_per_skill`, and `test_load_skill_activity_line` in the activity tests.
- [ ] **Step 2: Run, fail. Step 3: Implement. Step 4: Run tests and suite; commit** ("Skills: a load_skill tool that reads only approved skills").

### Task 6: Wire skills into `invoke_agent`

**Files:**
- Modify: `src/phil/agents/invoke.py` (`AgentContext.skills`, `invoke_agent`)
- Modify: `src/phil/chat/controller.py` and `src/phil/run/engine.py` (build the catalog once and pass it on `AgentContext`)
- Create: `src/phil/skills/runtime.py`
- Test: `tests/agents/test_invoke_skills.py`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces:
  - `@dataclass class SkillRuntime: catalog: Catalog; cache: LabelCache` with `load_runtime(config: PhilConfig, repo_root: Path | None, paths: ProjectPaths | None) -> SkillRuntime | None` (None when no source is approved). It lives in `src/phil/skills/runtime.py`.
  - `AgentContext.skills: SkillRuntime | None = None`.
  - `MIN_CAP_FOR_SKILLS = 5`: a spec whose `max_model_calls` is below this gets no `load_skill` (covers the architect's revise cap of 4).

`invoke_agent` changes, after the repo-context block. The query is `packet.contract_json`: it carries the goal, the task and the user's message for every role, so no caller changes (plan ruling against spec §4.8's "goal text", same intent):
- Tool harnesses (spec in `AGENT_STAGES`, harness `light` or `deep`, cap not below `MIN_CAP_FOR_SKILLS`): `offered(...)`; if any, append `skills_section(...)` to `spec.context` (with a blank line between it and the repo context) and add `make_load_skill_tool(offered, role=spec.name, notice=ctx.notice)` to `tools`.
- Lean harness (spec in `AGENT_STAGES`): `to_inline(offered(...), query)`; each skill's `SKILL.md` text, cut to `INLINE_TOKEN_CAP` tokens by `estimate_tokens`, under a heading `## Skill: <name>`, appended to `packet.extra_text` with `packet.model_copy`, only while `packet.tokens + cost <= ctx.config.budget_for(spec.role).max_input_tokens`; the second is dropped first. Dropped skills: `logger.info`. Each inlined skill sends `notice(f"Using skill {name} ({spec.name})")`. The new packet's `tokens` is recomputed.
- `ctx.skills is None`: nothing changes (all existing tests keep passing untouched).

- [ ] **Step 1: Write the failing tests** (fake factory recording the `spec.context` and the tool names it was given):
  - `test_tool_agent_gets_list_and_tool`
  - `test_no_skills_no_change` — the context and tools are identical to today's.
  - `test_revise_cap_gets_no_load_skill` — `replace(get_spec("architect"), max_model_calls=4)`.
  - `test_capped_last_call_has_no_load_skill` — real `_call_budget_middleware` on a capped light agent: on the final allowed call the request's tools are empty (an existing cap test, extended with `load_skill` in the tool list).
  - `test_lean_agent_gets_inlined_skill`
  - `test_inlined_skills_never_overflow_the_budget` — a budget that leaves room for one: one inlined; room for none: zero; no `PacketTooLarge`.
  - `test_feed_notice_for_inlined_skill`
- [ ] **Step 2: Run, fail. Step 3: Implement**, including `load_runtime` called once in `ChatController.__init__` and in the run engine's setup (and reused by `_context`); a failure in `load_runtime` is logged and leaves `skills=None`.
- [ ] **Step 4: Run tests and the full suite; commit** ("Agents: the skills for each agent's stages, by tool or inlined").

### Task 7: Labelling triggers

**Files:**
- Modify: `src/phil/chat/controller.py` (background labelling at chat start)
- Modify: `src/phil/cli/main.py` (`_run_setup`: label after a successful setup)
- Test: `tests/chat/test_skills_labelling.py`, `tests/setup/test_flow.py`

**Interfaces:**
- Consumes: `label_skills`, `SkillRuntime`.
- Produces: `ChatController._label_in_background()`: when `self.ctx.skills` has skills missing a label, a daemon thread (named `phil-skill-labels`) opens its own db connection (as jobs do), runs `label_skills` with a copy of the context, and saves the cache. It never touches the feed. Exceptions are logged.

- [ ] **Step 1: Write the failing tests** — `test_chat_start_labels_unlabelled_skills` (wait for the thread; the cache file has the labels), `test_labelling_failure_is_silent` (the factory raises; the chat starts and no error reaches the feed), `test_setup_labels_after_writing` (a fake labeller is called after the config is written), `test_setup_label_failure_warns_and_completes` (the summary has one warning line: `Couldn't label skills yet; Phil will try again when a chat starts.`, and setup returns True).
- [ ] **Step 2: Run, fail. Step 3: Implement. Step 4: Run tests and suite; commit** ("Skills: label new skills at setup and at chat start").

### Task 8: Setup step for skill folders

**Files:**
- Modify: `src/phil/setup/flow.py` (`_skills_step` after `_vision_step`), `src/phil/setup/write.py` (`write_global_config(..., skill_folders: list[str] | None = None)`, which writes `[skills] folders`)
- Test: `tests/setup/test_flow.py`, `tests/setup/test_write.py`

**Interfaces:**
- Consumes: `user_sources()`, `discover()`.
- Produces: `_skills_step(io, config, target) -> list[str] | None` — None leaves `[skills] folders` untouched.

Behaviour: if no user source exists, say nothing and return None. Otherwise say `Skills from other coding agents can be used by Phil's agents. Found:` and one line per source, `  <n>. <label> (<path>): <count> skills`, then ask `use which? (numbers separated by spaces, Enter for none)`. The default is the folders already approved in the global file. Answers outside the list are asked again once, then treated as Enter. When `TerminalSetupIO` is not interactive, the step is skipped (None).

- [ ] **Step 1: Write the failing tests** — `test_skills_step_lists_sources_with_counts`, `test_skills_step_writes_chosen_folders`, `test_enter_keeps_current_folders`, `test_no_sources_no_step`, `test_write_preserves_other_tables` (write.py).
- [ ] **Step 2: Run, fail. Step 3: Implement. Step 4: Run tests and suite; commit** ("Setup: choose which installed skill folders Phil uses").

### Task 9: `phil skills`, `/skills`, `/permissions`, and docs

**Files:**
- Create: `src/phil/cli/skills.py` (`skills_app`, registered in `main.py` as `skills`), `src/phil/skills/listing.py`
- Modify: `src/phil/chat/controller.py` (`/skills` in the command list and dispatch; the `/permissions` listing), `src/phil/cli/permissions.py` (listing), `docs/guide.md` and `CHANGELOG.md`
- Test: `tests/cli/test_skills_command.py`, `tests/chat/test_skills_command.py`, `tests/skills/test_listing.py`

**Interfaces:**
- Consumes: everything above; `write_global_config`'s tomlkit pattern for edits.
- Produces:
  - `skill_lines(runtime: SkillRuntime | None, config: PhilConfig, repo_has_skills: bool, repo_approved: bool) -> list[str]` — shared by both commands: the approved sources; then each offered-capable skill as `<name>  <stages> (<label|override>)`; then `Off: …`, `Hidden duplicates: …`, `Not labelled yet: <n>`, `Couldn't read: <n>`. With nothing approved: `No skill folders are approved. Run phil setup to choose them.` When the repo ships skills that aren't approved, a last line: `This repo has its own skills; turn them on with: phil skills repo on`.
  - `phil skills` (list), `phil skills off <name>`, `phil skills on <name>`, `phil skills stages <name> <s1,s2>` (validates stage names; `stages <name> -` clears the override), `phil skills repo on|off`. Edits to `~/.phil/config.toml` use tomlkit and keep the rest of the file.
  - `/skills` in chat prints `skill_lines`, read-only, and points to `phil skills` for changes.
  - `/permissions` and `phil permissions` add a section `Skill folders Phil's agents can read:` with one line per approved source path.
  - The chat mentions once per repo, the first time a chat opens there, `This repo ships its own skills; Phil won't use them until you run phil skills repo on.` Remembered in the project's `skills.toml` (`mentioned = true`).

- [ ] **Step 1: Write the failing tests** for each command's output and file edits, the `/skills` listing, the `/permissions` section, and the once-only repo mention.
- [ ] **Step 2: Run, fail. Step 3: Implement.**
- [ ] **Step 4: Docs.** Add a "Skills" section to the user guide: where Phil looks, approving folders, stages, overrides, the repo rule, the limits, and that scripts never run. Add a changelog entry.
- [ ] **Step 5: Run tests and the full suite; commit** ("Skills: phil skills, /skills, and the guide").
