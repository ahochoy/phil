# M4 Permissions: followups

These items came up in the reviews of the M4 branch and were deliberately set aside. Each entry says why it was parked and what the fix would be. The rulings referred to below (R1 to R18) were made during implementation, and their effects are recorded in the spec: `specs/2026-10-13-phil-permissions-design.md`.

## Security, theoretical, or "same verb" reach

- **Same-verb reach.** An open rule covers whatever its verb can do. For example:
  - `npm install *` also installs from a git URL or a tarball;
  - `kubectl get *` covers `get secrets`;
  - `docker cp` can write to the host;
  - `gh repo edit` and `gh workflow run` act on the remote.

  Remove verbs are saved exactly (R8). A possible next step is a short list of verbs that are always saved exactly, chosen per family.
- **Aliases and abbreviations of dangerous subcommands.** Examples are `npm c` (config), `docker buildx b` (build) and `npm un` / `npm r` (uninstall). They show in the rule the user approves, but the danger lists don't recognise them.
- **Launchers aren't unwrapped for deny rules.** `env git push -f` and `nice …` ask rather than being denied. The user still sees the full command.
- **Engine commands with shell operators skip deny rules.** A plan test command such as `npm ci && npm test` is classed as forbidden before deny rules are read, so a deny on `npm test` isn't applied to it. A fix would split on `&&` and `;`, then deny-check each part.
- **Glob paths in trusted repo patterns.** `rm build/*` matches `rm build/../../etc`. Your own config patterns have the same behaviour (R17).
- **Agent-run code (R18).** Code that agents write and your test command runs has your full rights. Sandboxing (macOS seatbelt, Linux bubblewrap) is planned for after launch. Today Phil only reports when a policy file changed during a run without Phil writing it, and it doesn't switch the changed rules off.
- **Case in the shell policy.** `FIND . -exec …` escalates, where `find . -exec …` is forbidden.
- **Windows.** File locking (`msvcrt` across handles) and rule normalisation for path-qualified programs are only covered by simulated tests. Confirm them on a real Windows CI run.

## Behaviour and UX

- **`phil permissions check --run <id>`** would include a run's approvals and plan commands. It was skipped because it needs the run's saved state loaded outside the worker.
- **The race when the watcher starts.** A worker notice written before the chat's watcher starts isn't shown in the chat. Budget warnings work the same way. A fresh run could skip seeding from the log.
- **The policy-change notice** also fires for rules the user saves from another terminal while a run is paused. That errs on the safe side.
- **The callout at narrow widths.** At width 60 the overflow line takes two rows, so about two commands fit. `/more 1` lists them all.
- **Coarse labels.** A known program used in a way that isn't a read (such as `docker ps`) is labelled `changes things`. A repo pattern run by path (`./tool *`) is labelled `may change things`.
- **Quick-path trust fallback.** If saving the answer fails, the user can be asked twice in one goal.
- **Ctrl-C at the trust prompt** drops a finished draft, while Ctrl-C at the approval prompt keeps it.
- **The notice is repeated** after a crash that happens before the first checkpoint.
- **The ask message** still shows up to 10 rule texts to the agent.
- **A problem line for a bad rule** has no line number when the rule contains an escaped control character.

## Code health

- **`chat/controller.py`** is now about 2500 lines. Split it at the next restructure; the trust section is a natural seam.
- **The policy files are re-read** on every engine command (setup, test and check). It costs milliseconds, and it lets a trust change take effect mid-run.
- **Atomic writes.** A failed `os.replace` leaves a stale `<path>.tmp`, which the next write overwrites, and there's no `fsync` before the replace.
