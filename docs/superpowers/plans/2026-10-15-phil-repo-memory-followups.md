# Repo memory: followups

These items came up in the per-task and final reviews of the `plan-memory` branch. They were deliberately left for later. Each entry says what the issue is, why it can wait, and the likely fix. The spec is `specs/2026-10-15-phil-repo-memory-design.md`.

## Measuring

- **The warm benchmark stops at a harness crash.** Failed checks no longer stop the remaining pairs, but an exception raised inside `run_case` still ends the loop. If live runs crash often, catch the exception per run and record it as a failure.
- **Rounding.** The report rounds half to even, so a -62.5% change shows as -62%. This is cosmetic.
- **A missing repo is silently replaced.** If `reuse_repo` finds no repo, it quietly makes a fresh one. It would be better to raise.
- **Site cases fail in check mode, with or without memory (warm bench at 731e1d2, 2026-10-16).** 6 of the 12 site-typo and site-meta-tag runs didn't pass:
  - *Check commands refused at launch (3 runs).* The planner wrote `node -e '…;…'` and `! grep …`; the policy rejects shell operators and treats `!` as a program. Tell the planner which check commands are allowed (one program, no shell operators), or offer a small check-script convention.
  - *`node --version` escalated (2 runs).* An agent asked to run a version probe, which needs approval, and an unattended run has no approver. Allow read-only version probes (`<program> --version`) of programs the plan already uses.
  - *Completed without the check passing, or incomplete (2 runs).*

  Rerun with `PHIL_BENCH_PAIRS=5` after these, since one pair per case says nothing about memory.
- **The "cold run learned nothing" note is per row.** `report.py:125` adds it when any counted pair learned nothing, so a row where 2 of 3 cold runs learned still carries it. Count the pairs that learned instead (e.g. "2 of 3 cold runs learned").
- **`read_memory` isn't guarded in the harness.** An unreadable memory file crashes a bench run. That crash is visible, and it only affects the benchmark.

## Memory file and learning

- **Line endings and comments.** Parsing is byte-for-byte only for LF files. CRLF is normalised on read, and the inner lines of a multi-line HTML comment reach prompts.
- **Drop order.** `fit()` drops Phil's bullets in section order, starting with Commands, which is the most useful section for speed. This matches spec §2.2. Consider dropping Conventions or Layout first.
- **Over-cap user text isn't reported.** Spec §2.2 says that when the user's own text alone exceeds the cap, `/memory` says so. That isn't implemented.
- **One huge input drains the rest.** A single note over 6 KB pushes everything else out of the learning inputs. Cap each item first.
- **Learning isn't fully counted.** The learning call isn't checked against the run's budget, and the run summary's usage is computed before learning, so it leaves out the memory call.
- **Misleading notices and stale checks:**
  - If storing suggestions fails after memory was written, the notice says "Memory wasn't updated".
  - The `MemoryNotUpdated` notice says "not updated" twice.
  - `recognised` is checked on a read taken outside the lock.
- **Small code-quality points:**
  - A model addition that starts with `- ` gets a second bullet marker.
  - `assert isinstance` is used as a type guard in `learn.py`, and asserts are stripped under `-O`.
  - `agents_md.py` imports the private `_locked` from `store.py`.
- **Test gaps:**
  - No test runs `apply_update` with rejected items mixed in, or with `fit()` dropping bullets.
  - No test checks that "continue" mode leaves `context.md` untouched.
- **Logging and wording:**
  - Rejected text that looks like a secret is logged verbatim at debug level. The same text is already in the run's outputs.
  - A rejected tab is reported as "more than one line".
  - A duplicate removal isn't logged.

## Agent file and AGENTS.md

- **A single line over 16 KB is cut mid-line.** The truncation note is still added.
- **A leading BOM in AGENTS.md** stops the `## Notes from Phil` heading from matching when it's on the first line, so a second heading gets added.
- **Prompt order on apply.** The CLI and chat check `.exists()`, which follows symlinks. A dangling AGENTS.md link therefore asks to create the file before it's refused, and a live link is refused only after the lines are confirmed. Nothing is written in either case, but it would be cleaner to refuse first.
- **`apply_suggestions` doesn't re-check for a running run when it writes.** The check happens before the prompt, and it's user-initiated.
- **`/memory edit` creates the file outside the lock.** It writes the template outside `update_memory`'s lock, so there's a small overwrite race.
- **`apply` can create an empty database** for a repo with no runs, because it opens one.
- **Plausible but harmful saved lines.** Command output no longer reaches the memory agent, but a one-line addition such as "Commands: run `curl … | sh` before tests" still passes the line checks and persists. Consider refusing additions with pipe-to-shell, `curl`/`wget` or long base64 runs.
- **AGENTS.md text in prompts.** How much weight it carries as possible injection, and whether to give it a firmer frame than the background-only disclaimer, is open.
- **Fenced code in `memory.md`.** Code blocks that contain `## Commands`-like lines are parsed as headings.

## Editor launcher (`src/phil/memory/editor.py`)

- **Relative PATH entries.** A relative entry (`bin`, `C:tools`) is still searched. A file planted under it is refused, but the lookup then fails instead of moving on. Skip non-absolute entries.
- **Quoted PATH entries** aren't unquoted.
- **A broad current folder.** `_inside_cwd` refuses every editor under it, for example when Phil runs from `$HOME`. The message tells the user to set `$EDITOR` to a full path.
- **Untested paths:**
  - The tests stub `subprocess.run`. Add one real launch, for example with `EDITOR` set to `sys.executable -c …`.
  - `/memory edit` runs inside the chat's `patch_stdout`. Check it once by hand in a real terminal.

## Consistency

- **The "in progress" run states are written out in several places:**
  - `controller.py`
  - `agents_md._IN_PROGRESS`
  - `publish/service.py` (`UNSETTLED`)
  - `cli/main.py`
  - `run/cleanup.py`

  Make them one constant in `store/runs.py`. The chat's own-run check could then rely on `active_run` alone.
- **Duplicated "create the memory file, then edit" code.** The CLI and the chat each have their own copy. Move it into `memory/commands.py`.
- **The chat watcher pops a notice before posting it,** so a notice is lost if posting raises. The old code had the same weakness.
- **The Windows read-only target in `write_atomic`.** If the target is read-only, the cleanup unlink can raise and leave the temp file behind.
- **Ruff isn't set up for this repo.** About 850 findings already exist on `main`.

## Windows

- **Confirm Windows CI passes the new file-handling tests.** In particular, `tests/memory/test_context.py` compares `(st_dev, st_ino)` between `os.stat` and `os.fstat`. If those values differ on Windows, every agent-file read would be refused.
