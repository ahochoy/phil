# Plan 5 (Pull requests and cleanup): Follow-ups for Later Plans

Deferred items from plan 5's task reviews, plus what the plan itself pushed out. Earlier
follow-ups: `2026-09-23-phil-01-followups.md`'s "MVP lifecycle" section (done in plan 5 except
the two items below), `2026-09-28-phil-04c-followups.md`.

## Deferred by the plan

- **Full project memory.** Spec #2's roadmap/epic/story memory is still out of scope; plan 5
  only adds the flat, append-only `~/.phil/projects/<slug>/learnings.md`.
- **Non-GitHub hosts.** `Publisher` (`phil.publish.publisher`) is a `gh`-only protocol; a
  GitLab/Bitbucket/self-hosted implementation needs its own client, and — per the
  provider-agnostic-models principle — should not assume a hosted API is reachable the way
  `gh auth status` does.
- **PR template filling.** `find_pr_template`/`render_pr_body` (`phil/publish/pr_body.py`)
  append the target repo's template verbatim under a `## Template` heading; they never parse or
  fill in its placeholders.
- **Squash option.** Whether a merged run's worktree branch should be squashed before/at merge
  (spec §1's "deciding the fate of the run's commit history") is still open; Phil neither offers
  nor requires it.
- **Auto-publish config switch.** Opening a PR is always asked (or explicit via `phil pr`);
  there's no `phil.toml` setting to skip the question and publish automatically after a
  completed run.
- **`learnings.md` in-repo option.** The file lives under `~/.phil/projects/<slug>/`, not in the
  target repo, so it isn't versioned or shareable with a team; an opt-in `docs/phil/learnings.md`
  location was considered and deferred.
- **Nothing reads `learnings.md` yet.** It's a plain per-project log for the human; no agent
  (architect, critic, or otherwise) consults it when planning a new run. That's the natural next
  step once project memory (above) is designed.

- **Per-task outcomes in the PR body.** Spec §3.3 item 3 asks for each task's gates, tester
  and reviewer outcomes under "How it was verified"; the body only has one tests line, the
  tester report count and the newest reviewer verdict. Deferred from the final review.

## Final review (deferred)

- **Downgrade hazard.** Plan 5 migrates `phil.db` (the `runs` table gains `pr_*`/`base_branch`
  columns) and `get_run`/`list_runs` build `RunRecord(**row)`, so an older Phil opening a
  migrated `phil.db` fails on the unknown columns. Either tolerate unknown columns when reading
  rows or record a schema version that older builds refuse with a clear message.
- **Reopened PRs aren't re-checked.** Once a sweep records `pr_state = "closed"` the run is never
  looked at again, so a PR reopened (and maybe merged) later is missed; `phil clean <run>` is
  the only way out. Re-check closed PRs occasionally, or offer `phil pr <run> --recheck`.
- **Fix the cross-imported test fixtures early in the next plan.** The `calc_repo` re-exports
  below cause an order-dependent `fixture 'calc_repo' not found` under some collection orders;
  do the hoisting as the first task of the next plan, before it grows more tests on them.

- **Head-mismatch runs are re-reported every sweep.** A merged PR whose head isn't the run's
  branch stays `pr_state = "merged"`, `cleanup_failed`, so every sweep calls `gh` again and the
  chat monitor re-reports it every 5 minutes. Give it a one-time notice or a terminal state.
- **`find_pr` swallows gh errors.** `GhPublisher.find_pr` returns None on any gh or parse failure
  with no trace; log the failure at info (under `phil.publish`) before returning None.
- **An adopted PR isn't verified as ours.** When `create_pr` says the PR already exists,
  `publish_run` records whatever `find_pr(branch)` returns; compare its `headRefOid` with the
  local branch tip before adopting it.

## Review minors

- **PR jobs share the chat's 3 job slots.** `phil.chat.terminal.MAX_JOBS = 3`
  (`src/phil/chat/terminal.py:20`) gates every submitted job — intake, architect/critic cycles,
  and `_open_pr`'s job alike — through one `threading.Semaphore(MAX_JOBS)`
  (`terminal.py:65`). A slow or hung `gh pr create` call (up to `GH_TIMEOUT_S = 60`s,
  `src/phil/publish/publisher.py:12`) can occupy a slot and delay the chat's own
  intake/planning jobs for another goal typed in the meantime.
- **`calc_repo`/`finished_run`/`failed_run` fixtures are duplicated and cross-imported.**
  `calc_repo` is defined once in `tests/run/conftest.py:26` and re-exported by
  `from tests.run.conftest import calc_repo` in `tests/cli/conftest.py:1`,
  `tests/publish/conftest.py:1`, and `tests/ui/conftest.py:1`; one collection ordering surfaced
  `fixture 'calc_repo' not found` instead. `finished_run` is independently redefined in
  `tests/ui/test_show_view.py:12`, `tests/cli/test_show_command.py:14`, and
  `tests/cli/test_diff_clean.py:23` (plus `failed_run` at `tests/cli/test_diff_clean.py:31`).
  Hoist `calc_repo`, `finished_run`, and `failed_run` into one shared conftest (e.g.
  `tests/run/conftest.py` promoted to a root-level or `tests/conftest.py` fixture module) instead
  of the current import-and-redefine pattern.
- **`phil clean --merged` calls `Publisher.available()` twice.** The CLI's `_clean_merged`
  (`src/phil/cli/main.py:623`) checks `publisher.available()` itself before calling `sweep_prs`,
  which then checks it again as its own early-exit guard (`src/phil/publish/service.py:148`).
  Harmless (both checks are cheap and idempotent) but redundant; `sweep_prs` could take the
  already-known-available publisher, or the CLI could skip its own check and read the reason
  off an empty/absent-changes result.
- **The PR body's Tester line has no test.** `render_pr_body` only appends `- Tester: N
  report(s)` when `output_count(run_dir, "tester")` is truthy (`src/phil/publish/pr_body.py:143-146`);
  no test in `tests/publish/` exercises a run with tester output to check that line's text or
  its absence when there is none.
