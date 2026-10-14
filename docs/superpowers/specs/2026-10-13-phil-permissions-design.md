# Phil: Permissions (M4)

**Status:** approved in conversation on 2026-10-13. M4 is the first item in the release sequence (see the roadmap) and the last milestone the public release depends on.

## 1. Problem

Today an agent's shell command is either on a short built-in read-only list, matched by `[shell] allow`, or one of the plan's test and check commands. Anything else is denied, and the run pauses with **Approve for this run / Deny / Abort**. The approval matches that exact command only, and it is forgotten when the run ends.

The live tests showed what this leads to:
- **Repeated prompts for safe commands.** `gh pr view` and `gh pr checks` ask again and again.
- **No middle ground.** You can't approve `gh pr checks` without approving all of `gh`.
- **No way to remember an approval.** There is nothing longer than one run.
- **Two safety gaps:**
  - On macOS and Linux, a risky program given by its full path (`/usr/bin/python3 -c …`, `/usr/bin/git -c …`) slips past the risky-flag check. Windows already compares by name.
  - A cloned repo's `phil.toml` can add `[shell] allow` entries, so someone else's repo can pre-approve commands for you.

## 2. Decisions (user, 2026-10-13)

| Topic | Decision |
|---|---|
| Approval choices | Allow for this run, always allow in this repo, always allow everywhere, or deny. There is no "this chat" scope. |
| What "always" saves | A pattern Phil suggests and shows before you confirm (`gh pr checks *`). It is never just `program *`. Interpreters and destructive commands are saved exactly. |
| Read-only list | A built-in list of known read-only subcommands, network reads included, that never asks. Mutating subcommands always ask and are labelled. |
| Repo rules | A repo's own `[shell] allow` needs your trust once per repo, and again whenever it changes. |
| Approach | A policy module in front of the shell check, with policy files under `~/.phil`, separate from config and memory. |
| Left out | Directory rules, whole-tool rules, an OS sandbox, and Phil's own publishing commands. |

## 3. Design

### 3.1 Rules and matching

**The form of a rule.** A rule is a command written as words, for example `gh pr checks *`.
- **The program.** The first word is compared by program name, on every platform: the basename, lowercased, with `.exe`, `.cmd` or `.bat` removed on Windows. So `/usr/bin/gh` and `gh` are the same program.
- **The other words** must match exactly.
- **A trailing `*`** matches zero or more further *plain arguments*. A plain argument is a word that doesn't start with `-` and doesn't contain `=`. So `gh pr checks *` covers `gh pr checks 43` but not `gh pr checks 43 --watch`, and a command with options or `NAME=value` words asks again. (User, 2026-10-13. Four security passes found dangerous options hiding behind a `*`; this closes that class of problem in one rule.) A `*` anywhere else, or quoted or escaped, makes the rule invalid.
- **A path-qualified program** (`./gh`, `/tmp/gh`) only matches a rule that names that exact path. A bare-name rule never covers a program run from a path, so an agent can't plant its own `gh` in the worktree.
- **Old patterns.** `[shell] allow` patterns from config keep today's matching (`fnmatchcase` on each word after the first), so existing setups behave the same.

**The order of checks.** The first match wins.

| # | Check | Result |
|---|---|---|
| 1 | Forbidden: today's character, parse, risky program and flag, and containment checks. The risky check now uses the program name, which closes the full-path bypass. | refused, and can't be approved |
| 2 | Deny rules: everywhere, then this repo. A deny rule matches broadly: the program by its name (any path, any letter case), the rule's words in order anywhere after the program, and a trailing `*` covering anything, including options. So `git push *` blocks `git -C x push -f`. | denied, and can't be approved this run |
| 3 | The built-in read-only list (§3.2) | allowed |
| 4 | Allow rules: everywhere, then this repo, then trusted repo rules, then `[shell] allow` from `~/.phil/config.toml`, then the plan's test and check commands | allowed |
| 5 | Approved earlier in this run | allowed |
| 6 | Anything else | ask |

**What every decision carries:**
- the outcome;
- a reason, for example `read-only (built-in)`, `rule "gh pr checks *" (calc)` or `deny rule "git push *" (everywhere)`;
- a label: `reads only`, `changes things` or `may change things`.

The reason appears in the activity feed and in `phil show`.

### 3.2 The built-in read-only list

This list was amended during implementation (2026-10-13), after security review found entries in the original list that could write or run code. Today's list stays. The new entries are subcommands, and each entry carries its own list of safe flags.

| Program | Read-only (examples) | Always asks (examples) |
|---|---|---|
| `gh` | `pr view/list/checks/diff/status`, `issue view/list`, `repo view`, `run view/list`, `release view/list`, `auth status` | `pr merge/create/close/edit`, `api` (any method), `repo delete`, `release create`, `--web` |
| `git` | today's set, plus `remote -v`, `rev-parse`, `blame`, `tag -l`, `stash list` (exactly), `config --get` | `push`, `commit`, `reset`, `checkout`, `tag <name>`, `blame --contents` |
| `npm` | `ls`, `view`, `outdated`, `why`, `explain` | `install`, `run`, `publish`, `--registry` |
| `go` | `list`, `version`, `env` (reading only) | `build`, `install`, `get`, `env -w`, `list -toolexec` |
| `cargo` | `version` | everything else (`tree` and `metadata` write `Cargo.lock`) |

**Rules for the list:**
- **Safe flags only.** A command counts as a read only when everything after the subcommand is either a plain argument or one of that entry's safe flags. Any other option, or `--`, sends it on to your rules, or Phil asks. A flag that takes a value consumes the next word, so `git tag --format -l v1` isn't mistaken for listing.
- **No reads for `uv`, `yarn` or `pnpm`.** These run code from configuration files the agent can write in the worktree, such as `pyproject.toml`, `.yarnrc.yml` and `.pnpmfile.cjs`. Their commands always ask, or match your own rules.
- **`gh api`** always asks. It can make any request, so it can't be classed as read-only.
- **Programs run by path** (`./gh`, `/opt/homebrew/bin/gh`) are never reads.
- **Labels.** A known program used in a way that isn't a read is labelled `changes things`. A program Phil doesn't know is labelled `may change things`.

### 3.3 What "always allow" suggests

Patterns are only offered from a vetted list (amended during implementation, 2026-10-13, after security review). Every other command is saved exactly.

| The command | Suggested rule |
|---|---|
| A vetted family: `gh` (except `api`), `git`, `npm`, `pnpm`, `yarn`, `uv`, `cargo`, `go`, `docker`, `kubectl`, or the test and lint tools (`make`, `just`, `pytest`, `tox`, `nox`, `ruff`, `mypy`, `eslint`, `prettier`, `tsc`, `jest`, `vitest`), with only plain arguments after the subcommand | the program plus its subcommand words, then `*`. `gh pr checks 42` gives `gh pr checks *`, and `make test-unit` gives `make test-unit *` |
| The same, but with an option or a `NAME=value` word after the subcommand | the exact command |
| Anything outside the vetted families (`tar`, `ssh`, `terraform`…) | the exact command |
| A dangerous form inside a family: launchers (`uv run`, `npm exec`, `docker run`, `kubectl exec`…), commands that run or configure other commands (`git config`, `git send-email`, `gh api`, `npm config`…), and remove verbs (`delete`, `rm`, `prune`, `uninstall`, `clean`, `reset`…) | the exact command |
| An interpreter (any `python`, `node`, `bash`, `sh`, `ruby`, `perl`, … including versioned names), a destructive command (`rm`, `mv`, `chmod`, `chown`, `dd`, `kill`…), or a launcher (`env`, `sudo`, `xargs`…) | the exact command, and an open rule never covers it |

Phil never suggests `program *` on its own, and never suggests a pattern when any word contains `*`.

### 3.4 The approval callout

The callout replaces today's approval choices when the implementer was denied commands. There is still one callout per pause, and it lists every denied command with its label.

```
╭─ implementer wants to run ─────────────────────────────────╮
│ gh pr merge 42  (changes things)  → saves gh pr merge *    │
│ make test-unit --fast  (may change things)                 │
│   → saves make test-unit --fast                            │
│                                                            │
│ ❯ Allow for this run                                       │
│   Always allow in calc (2 rules)                           │
│   Always allow everywhere (2 rules)                        │
│   Deny                                                     │
│   Stop the run                                             │
╰────────────────────────────────────────────────────────────╯
```

The layout was amended during implementation (2026-10-13):
- **Every rule is visible in full** before you choose "Always". The body lists each command with its label and the exact rule it would save, and lines wrap rather than being cut off. When the body overflows, its last line says how many commands and rules are hidden and points to `/more 1`, which lists everything.
- **The options are short.** The "Always" options show the rule count when there's more than one.
- **What's shown is what's saved.** The rule shown is the normalised form that gets saved. A command whose rule can't be saved shows `→ can't be saved`, and then both "Always" options are left out.
- **Hidden characters** (control, formatting and reordering characters) display as `?`. A command containing one can't be saved as a rule; it can still be allowed for this run.
- **No "everywhere" for dangerous forms.** If any listed command is an interpreter, a destructive command, a launcher, or a dangerous form inside a family, "Always allow everywhere" is left out for that pause.
- **Each answer belongs to its pause.** Every pause carries an id, and an answer meant for an earlier pause changes nothing; Phil asks again.
- **`phil resume`** shows the commands and rules, but it won't save rules. Use the chat or `phil attach` for that.
- **Choosing "always":**
  1. Phil writes the rules to the policy file.
  2. It adds the commands to the run's approvals.
  3. The run resumes.
  4. The feed shows `Saved rule "gh pr checks *" for calc · /permissions to review`. With several rules, they are listed together.
- **"Deny" and "Stop the run"** behave as `deny` and `abort` do today.
- **`phil attach`** offers the same choices as a plain prompt.

**Unchanged:**
- The tester still gets no approvals; a denied command becomes a minor issue.
- Read-only agents (the answerer and the designer) still get only the built-in read-only list. Saved rules, `[shell] allow` and approvals don't apply to them.

### 3.5 Trusting a repo's rules

**When Phil asks.** The question comes when both are true:
- the repo's `phil.toml` sets `[shell] allow`, `[shell] pass_env` or `[shell] bash`. All three need trust, because a repo could otherwise pass your secrets into agent commands (`pass_env`) or choose the shell every command runs through (`bash`, on Windows). This was amended during implementation;
- this repo's policy file has no decision for the current hash of those settings.

The recorded hash is always the hash of exactly what you were shown. If `phil.toml` changes while you're deciding, nothing is recorded and Phil asks again.

It comes before a plan is approved in the chat, so it joins a decision you're already making. It also comes before a quick run starts.

```
╭─ calc's phil.toml allows these commands ─╮
│ make test *                 may change things │
│ docker compose run --rm app pytest *          │
│                                               │
│ ❯ Trust these for calc                        │
│   Ignore them                                 │
╰───────────────────────────────────────────────╯
```

- **Recording the answer.** The decision is saved in the repo's policy file together with a sha256 of the list. If the list changes, Phil asks again.
- **With no one to ask** (a worker started directly with `phil run`): repo rules you haven't trusted are ignored, with a one-line notice in the run log.
- **The rest of `phil.toml`** (settings such as models and test commands) is unaffected. Only `[shell] allow`, `pass_env` and `bash` from the repo need trust.

### 3.6 Files on disk

```
~/.phil/
  config.toml                 settings (unchanged)
  policy.toml                 permissions that apply everywhere
  projects/<slug>/
    policy.toml               this repo's permissions and its repo-rules decision
    memory/                   reserved for repo memory (next milestone); not created by M4
    phil.db, runs/, worktrees/, chats   (unchanged)
```

`PHIL_HOME` moves all of this, as it does today.

**The file format** is TOML. Phil writes it, and you can edit it by hand.

```toml
# Phil permissions — everywhere. `phil permissions` shows what's in effect.

[[allow]]
rule = "gh pr checks *"
added = 2026-10-13
from = "approval in calc (r-4f2a)"

[[deny]]
rule = "git push *"
from = "manual"
```

The repo's file adds one table:

```toml
[repo_rules]
decision = "trust"            # or "ignore"
hash = "sha256:…"
```

**How the files are written:**
- Phil edits them with `tomlkit`, so comments and formatting are kept.
- It holds the lock from `platform.py` while writing, and writes to a temporary file in the same folder, then replaces the original.

**A broken file.** If a policy file can't be parsed, or a rule in it is invalid:
- its rules are not applied. Because a deny rule in it may have been meant to block an allow rule elsewhere, saved allow rules from every policy file are switched off too, so those commands ask instead (this fails safe);
- the error is shown once, with the file and line, in the chat and in the run log;
- `allow`, `deny` and saving from the callout refuse to write to that file until it's fixed.

Built-in read-only commands and the forbidden checks keep working.

**Existing setups.** `[shell] allow` in `~/.phil/config.toml` keeps working, at step 4 of §3.1. Nothing is moved automatically.

### 3.7 Commands

**CLI: `phil permissions`**

| Command | What it does |
|---|---|
| `phil permissions` | Lists every rule in effect, grouped as built-in (summarised), everywhere, this repo, trusted repo rules, and config. Each shows where it came from. |
| `phil permissions check "<command>"` | Shows the decision, its reason and its label, for example `asks · changes things · no rule matches`. |
| `phil permissions allow "<rule>" [--everywhere]` | Adds an allow rule to this repo's file, or to the everywhere file with `--everywhere`. |
| `phil permissions deny "<rule>" [--everywhere]` | Adds a deny rule the same way. |
| `phil permissions remove "<rule>" [--everywhere]` | Removes a rule. If no rule matches, it lists near matches. |
| `phil permissions trust` / `phil permissions ignore` | Records your decision on the repo's `phil.toml` rules. |

**Checks before `allow` saves anything:**
- the rule must be valid (§3.1);
- a bare `program *` gets a warning and needs `--yes`;
- a rule for something forbidden is refused, with the reason.

**Chat: `/permissions`** prints the same list as `phil permissions` and points to the CLI for changes.

## 4. Testing

- **Matching:**
  - words and the trailing `*`;
  - a `*` in the middle being invalid;
  - a program given by its full path, on POSIX and Windows (including the bypass regression);
  - old `[shell] allow` glob patterns still matching.
- **The order of checks:**
  - a deny rule beats the read-only list and every allow rule;
  - forbidden commands can never be approved;
  - every decision has a reason and a label.
- **The read-only list:** for each listed program, its read-only and its mutating subcommands, the flags that turn a read into a write, and `gh api` always asking.
- **Suggested rules:** every row of the §3.3 table.
- **The callout:**
  - its choices and the rules it shows;
  - "everywhere" left out for interpreters and destructive commands;
  - saving a rule, then resuming the run;
  - the feed line;
  - `phil attach` offering the same choices.
- **Repo trust:**
  - the first ask;
  - a hash change asking again;
  - an "ignore" decision holding;
  - an unattended worker ignoring rules you haven't trusted.
- **Policy files:**
  - comments kept when Phil writes;
  - two writers at once;
  - the temporary-file-then-replace write;
  - a broken file switching off its rules while built-in read-only commands keep working;
  - writes refused until a broken file is fixed.
- **Existing setups:** `[shell] allow` in config still applies.
- **Agents:** read-only agents gain no saved rules or approvals, and the tester still gets none.
- **CLI:** every command, including `check` explaining a deny, a forbidden command and a built-in read.
- **Portability:** every test passes on Ubuntu, macOS and Windows.

## 5. Out of scope

- Directory rules and whole-tool rules.
- An OS sandbox (macOS seatbelt or Linux bubblewrap). It is possible hardening after launch.
  Permissions control what agents run directly. Code that agents write and your test command runs still has your full rights; sandboxing that code is planned for after launch.
- A "this chat" scope.
- Phil's own `git push` and `gh pr create` when publishing. Those are confirmed in the chat's PR prompt.
- Memory. M4 only reserves `projects/<slug>/memory/`.

## 6. Done means

- A typical run asks fewer and clearer questions.
- Known read-only commands never ask.
- Anything you approve states exactly what it covers and for how long.
- Rules live in `~/.phil/policy.toml` and in `projects/<slug>/policy.toml`.
- A repo can't grant itself permissions.
- The full-path bypass is closed.
- CI passes on Ubuntu, macOS and Windows.
