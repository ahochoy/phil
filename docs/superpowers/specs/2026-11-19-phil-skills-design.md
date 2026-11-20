# Phil skills: use the SKILL.md skills already installed

Date: 2026-11-19. Status: draft for approval.

## 1. Intent

The user has installed many skills for other coding agents (Claude Code, Codex, Cursor, the shared `~/.agents/skills` folder): planning and PRD skills, web and design best practices, architecture and engineering practices, across the whole development cycle. Phil should use them too, from where they are already installed, without the user re-installing or curating them one by one.

Phil's thesis is cost efficiency, so skills must not make every run noticeably dearer. The standard's own answer is progressive disclosure (name and description up front, full text only when used); Phil adds a second filter: each agent sees only the skills that fit its stage of the work.

This is the "basic version" from the roadmap (after-launch item 1, pulled forward by the user on 2026-11-19). It is groundwork for the later mockups and confirm-before-implement work (D).

## 2. Goals, non-goals, anti-goals

**Goals**
- Discover `SKILL.md` skills in the standard install locations (§4.1) and in the repo.
- The user approves folders, not individual skills; per-skill on/off and stage overrides exist for when Phil's choice is wrong (the user's decision: approach 3 with 1 as an override).
- Each agent sees only the skills labelled for its stage (§4.3).
- Agents with tools load a skill's full text on demand through Phil's own `load_skill` tool; agents without tools get up to 2 matching skills' full text in their input (approach A, user's decision).
- The user can see which skills Phil used, in the feed and in `/skills`.

**Non-goals (this version)**
- Running a skill's scripts. Phil reads a skill's instructions and its reference files only. Running bundled scripts comes later, through the existing shell permissions.
- MCP servers, hooks, plugins as a whole, AGENTS.md changes.
- Installing, writing or editing skills.
- Antigravity's internal store and claude.ai skills (no readable folder of `SKILL.md` files).
- `allowed-tools` from the skill frontmatter (parsed but not enforced or granted).

**Anti-goals** (failures even if it technically works)
- Every agent call carries a list of hundreds of skills.
- A skill from a cloned repo reaches an agent without the user having approved it.
- The same skill appears two or three times because it is symlinked or cached in several places.
- A skill load silently eats a capped agent's last model call, so it fails to answer.

## 3. Approaches considered

How skills reach an agent (decided: **A**):
- **A. Phil's own `load_skill` tool** plus the stage-filtered list; lean agents get up to 2 skills inlined. One model call per loaded skill; works for every harness; no sandbox mounts. *Chosen:* keeps progressive disclosure and covers the lean critic and reviewer, where engineering skills help most.
- **B. Phil inlines 2–3 full skills before each agent runs.** No extra calls, but no mid-work discovery, and thousands of tokens per skill whether needed or not.
- **C. deepagents' `SkillsMiddleware`** with approved folders mounted read-only. Closest to the standard, but lean agents get nothing and light agents spend capped calls reading files.

How Phil narrows the list (decided: **3 with 1 as override**):
1. The user ticks individual skills. Cheapest, but a chore at ~200 skills.
2. The user picks folders and every skill is listed. Simple; 5–10k tokens on every call.
3. The user picks folders and each agent sees the skills that fit its stage. *Chosen.*

How a skill's stage is decided (my call, presented with A): a cheap model labels each skill once from its name and description, cached by content hash. Keyword rules were rejected as too often wrong.

## 4. Design

### 4.1 Where skills come from

User-level sources, scanned when they exist:

| Source | Path |
|---|---|
| Shared (`npx skills`) | `~/.agents/skills/*/SKILL.md` |
| Claude Code | `~/.claude/skills/*/SKILL.md` |
| Claude Code plugins | each `installPath` in `~/.claude/plugins/installed_plugins.json`, then `<installPath>/skills/*/SKILL.md` (only installed versions, never the whole cache) |
| Codex | `~/.codex/skills/*/SKILL.md` |
| Cursor | `~/.cursor/skills/*/SKILL.md` |
| Gemini | `~/.gemini/skills/*/SKILL.md` |

Repo-level sources: `<repo>/.agents/skills`, `<repo>/.claude/skills`.

A skill is a directory holding `SKILL.md` with YAML frontmatter `name` and `description` (the Agent Skills format). A file without both, or that fails to parse, is skipped and counted as a skill Phil couldn't read; the count shows in `/skills`, the reasons in the developer log only (see the diagnostics rule).

Symlinks: `~/.claude/skills` entries are often symlinks into `~/.agents/skills`. Phil follows a symlinked skill directory, reads only regular files, and identifies the skill by its resolved path, so a link and its target are one skill.

### 4.2 Approval

- **User-level folders** are approved once, globally, in `~/.phil/config.toml`:
  ```toml
  [skills]
  folders = ["~/.agents/skills", "~/.claude/skills", "claude-plugins"]
  ```
  `claude-plugins` names the plugin source in §4.1, which is not a single folder.
- **Repo folders** are approved per repo, in that project's store (next to its policy), because a cloned repo is untrusted. Until approved, Phil mentions once per repo that the repo ships skills and how to turn them on.
- **Setup** gets a skills step after the vision step: it lists each source found with its skill count and asks which to approve (none preselected). `phil setup` can be re-run to change it.
- **Overrides** live in the same `[skills]` table:
  ```toml
  off = ["blog-write"]                      # never offered
  [skills.stages]
  better-layout = ["design", "implementing"]  # replaces the label
  ```
- Turning a folder off removes its skills immediately; nothing is copied into `~/.phil`.

### 4.3 Stages and which agents see what

Stages: `planning`, `design`, `implementing`, `testing`, `reviewing`. A skill may have several or none.

| Agent | Stages it sees |
|---|---|
| intake, converse, architect | planning, design |
| critic | planning, reviewing |
| designer | design |
| implementer, quick implementer | implementing, design |
| tester | testing |
| reviewer | reviewing, implementing |
| answerer | implementing, reviewing |
| route, btw, memory | none |

A skill labelled with no stage (for example a blog or social-media skill) is offered to no agent unless the user gives it stages.

### 4.4 Labelling

- When discovery finds a skill whose content hash has no label, Phil asks the classifier-tier model (falling back to the low model, as routing does) for its stages, from its name and description only. Skills are labelled in batches (one call for many skills) to keep it cheap.
- Labels are cached in `~/.phil/skills.json`, keyed by content hash; a changed skill is relabelled, an unchanged one never is.
- Labelling runs at the end of setup and, for new or changed skills, at the start of a chat in the background. A skill without a label yet is not offered; it is never blocking.
- If labelling fails (no model, provider error), the skills stay unlabelled and `/skills` says so; nothing else breaks.

### 4.5 Duplicates

Skills are one entry when they share a resolved path or a content hash. Two different skills with the same `name`: the first in the source order of §4.1 wins (repo sources first, then user sources in table order); `/skills` lists the one hidden.

### 4.6 What an agent receives

**Agents with tools (light and deep harnesses):**
- A short system-prompt section listing the skills for its stages: name and description, at most 30 entries. If more match, Phil keeps the 30 whose name and description best match the goal text (simple word-overlap score).
- A `load_skill(name, file=None)` tool. Without `file` it returns `SKILL.md`; with `file`, a text file inside that skill's directory (its references). It refuses names not in the agent's list, paths that leave the skill directory, binary files, and files over 64 KB (returning a truncation note).
- At most 3 `load_skill` calls per agent run; later calls are refused with a message saying to continue without.
- Cap safety (applies to the capped agents: converse, architect, answerer, designer, quick implementer): on its last allowed model call, a capped agent already has every tool removed except the answer tool. `load_skill` is removed with them, so a skill load can never take the answering call. For the architect (cap 10) and the designer (cap 8), a skill load counts as one of those calls. That is accepted as the cost of using a skill.
- `load_skill` joins the tool list `invoke_agent` builds, which today has only the shell tool.

**Agents without tools (lean harness: intake, critic, reviewer):**
- Phil picks up to 2 skills for its stages whose name and description best match the goal (the same word-overlap score, above a minimum score so a weak match is not sent) and adds their `SKILL.md` text to the packet as a closing `Skills` section, each capped at 4k tokens. The packet's token budget counts them.

### 4.7 Visibility

- The feed shows a one-line notice when an agent loads or is given a skill: `Using skill better-layout (reviewer)`.
- `/skills` and `phil skills` list the approved sources, each skill's stages (and whether they came from the label or an override), skills switched off, hidden duplicates, unlabelled and unreadable counts.
- `phil skills off <name>`, `phil skills on <name>`, `phil skills stages <name> design,implementing` edit the overrides in `config.toml`.
- Run telemetry records which skills each agent loaded, for cost analysis.

### 4.8 Details settled after the builder check (my calls)

- **Config scope.** `[skills]` is read from `~/.phil/config.toml` only. A `[skills]` table in a repo's `phil.toml` is ignored, and `phil check` warns about it, so a cloned repo can't approve its own skills. Repo approval lives in the project store as `skills.toml` (`approved = true`), next to `policy.toml`.
- **Goal text for matching.** The run's goal as Phil understood it (the `Goal` text). The implementer and tester add their task's title. Agents that run before a goal exists (intake, converse) use the user's message.
- **Packet budget.** The lean agents' `Skills` section is added inside `build_packet` as the lowest-priority section. If it doesn't fit, the second skill is dropped first, then the first. It never raises `PacketTooLarge`, and a dropped skill is recorded in the developer log only.
- **Prompt placement.** The skill list is appended to the system prompt. It is fixed for the whole agent run, so prompt caching still works.
- **Trust.** Skill text is labelled in the prompt as guidance from the user's installed skills. The prompt also says the role's output contract and Phil's permissions take precedence over it. Approved folders are trusted the way the user's own `AGENTS.md` is.
- **Caps.** The architect's revise path (cap 4) gets no `load_skill` tool. The 3-load limit is per agent run, including its contract retries.
- **Cost attribution.** Labelling calls are charged to the `classifier` role. No new role is added.
- **Labels.** The model returns a strict list from the five stages. The cache key is content hash plus label-prompt version, so changing the prompt relabels everything. `skills.json` is written atomically (temp file and rename), and if two processes write it, the last one wins.
- **Setup.** The skills step is skipped in non-interactive setup. If labelling fails, the summary shows a warning, and setup still completes.
- **Reads.** `load_skill` is a plain Python tool that reads only inside approved skill directories. This is a separate permission from the workdir sandbox, and `/permissions` lists the approved skill folders.

## 5. Constraints

- The Agent Skills format as-is; no Phil-specific frontmatter.
- No symlinks or copies created under `~/.phil` (the October spike: the sandbox rejects symlinked paths outside its root).
- Read-only: Phil never writes to a skill folder.
- Internal warnings stay out of the user's terminal (diagnostics rule).
- Tests use a temp `PHIL_HOME` and temp skill folders, never the real home directory.
- Works on macOS, Linux and Windows (paths via `Path.home()`).

## 6. Left to the builder

- Module layout (suggested: `phil/skills/` with discovery, labels, selection and the tool).
- The exact word-overlap score and its minimum, the labelling prompt, and the batch size.
- How `/skills` lays out its list in the full-screen and inline chats.
