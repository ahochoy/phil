# Plan 4b (Live chat): Follow-ups for Later Plans

Deferred findings from plan 4b's task reviews and final review. Earlier follow-ups: `2026-09-24-phil-04a-followups.md` (its "Plan 4c — carried items", "Before a public release" and "Dependency policy" sections still apply).

## Design rules learned in 4b (apply to every later plan)

- **Nothing thread-bound crosses threads.** Chat jobs run on daemon threads; each job opens its own SQLite connection (`ChatController._job`), and only the main thread prints or writes the transcript/state. SQLite connections are thread-bound (`check_same_thread`), so any new background work opens its own.
- **Test with real threads.** Inline or deferred-on-main-thread job runners hid a bug that broke every model call in the live chat. Every concurrency feature needs at least one test that runs its jobs on real threads.
- **A live check before merge.** Scripted agents can't drive the real terminal; run `phil` in a scratch repo (toolbar, a pause answered in the chat, `/btw`, reopen) before merging chat changes.

## Reopening and state

- A reopened chat with any unreadable saved part (goal, plan, run, counters) starts idle and drops the run link, even when the run itself is valid; it should keep following a valid `run_id` (or at least print `Run <id> continues; phil attach <id>`), and a bad counter should not discard everything else.
- `done_seen` is read with `bool(...)`, so a hand-edited `"false"` counts as true.
- The chat lock is advisory (check-then-replace, not `O_EXCL`); two windows opening the same chat at the same instant could both take it, and a reused pid looks alive.
- Chats saved before counters were persisted restart artifact numbering on reopen; `_answer_sent` is not persisted.

## Run watching

- `connect()` runs migrations on every poll (a short write lock each second); open one connection per watcher thread instead.
- `events.latest()` reads the whole event log on each poll; track an offset.
- A row that is `escalated` without an escalation event gets no pause or lost-worker notice (`phil resume` still works).
- A hung-but-alive worker never triggers `worker_lost`; a reused pid keeps `worker_starting` true; `worker_starting` uses `os.WNOHANG` (POSIX-only, like the rest of the worker code).
- The rearm/poll race can print a false "worker exited" (a second answer is refused safely); a re-escalation within one poll shows "worker exited" instead of a new question — compare escalation timestamps.

## Chat and toolbar

- One OS thread per job: the semaphore bounds concurrency, not creation; rapid `/btw` creates parked threads.
- Each goal generation and each `/btw` exports its own snapshot, kept until the chat ends; reuse the goal's tree for `/btw`.
- No plan re-render after recovering to `approval` from a failed handler.
- A user-requested revision's first architect pass is labelled "Architect drafting".
- `list_open_chats` reads every chat's `state.json` per call; ids with `-10` sort before `-9`.
- `/runs` lists every run in the repo, not the chat's own first; there is no in-chat `/attach` (the chat follows its own run).
