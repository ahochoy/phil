# Phil: Welcome Banner

**Status:** approved in conversation on 2026-10-08. This is the last piece of roadmap M5. It builds on the status bar (PR #32) and the active-agents work (PR #33).

## 1. Problem

When the chat opens, it prints:
- one header line (`Phil · calc · base: main @ a1b2c3d`);
- a warning about uncommitted files;
- a count of parked items;
- the open-chat picker;
- the whole `/help` text as one long, dim paragraph.

It says nothing about which models are set up, the run budget, or a run waiting for you. It doesn't feel like a product either, and the help paragraph buries the few commands that matter.

## 2. Decisions (user, 2026-10-08)

| Topic | Decision |
|---|---|
| Purpose | All four: identity, orientation (repo and setup), getting started (short tips), and picking up where you left off. |
| Layout | Option A: a boxed card with a mascot on the left and the facts on the right. "Pick up" and the tips go below the card. |
| The right edge | Every row of the box ends in the same column, whatever the content (wide characters, colour codes, long values). |
| Mascot | Still to be chosen by the user. It lives in its own file so it can be swapped. The current robot face is a placeholder until then. |

The mockup is `.superpowers/brainstorm/` (not committed), `banner-layouts.html`. The ragged right edge in that HTML mockup was a mockup flaw, not part of the design.

## 3. Design

### 3.1 The card

```
╭──────────────────────────────────────────────────────────────────────────╮
│   ╭─────╮    Phil v0.9.0 · deterministic orchestration, token-efficient   │
│   │ ◠ ◠ │                                                              │
│   │  ◡  │    calc @ main a1b2c3d · ⚠ 2 uncommitted · 3 parked           │
│   ╰──┬──╯    high claude-sonnet-5 · low gemini-3.8-flash · classifier jev-1.13 │
│              budget $1.00 a run                                          │
╰──────────────────────────────────────────────────────────────────────────╯
```

This sketch shows content only. The real box pads every row so the right edges align.

**Left: the mascot** (§3.3). The mascot and the facts are vertically centred against each other, and the shorter one is padded with blank lines.

**Right: the facts, one per row**

1. **Identity:** `Phil v<version> · deterministic orchestration, token-efficient`. "Phil" is in `phil.brand`, the rest in `phil.muted`.
2. A blank row.
3. **Where:** `<repo> @ <branch> <short sha>`, then `⚠ N uncommitted` (in `phil.warn`) when there are uncommitted files and no `--base`, then `N parked` when there are parked items. With `--base`, it reads `<repo> · base <base> <short sha>` instead.
4. **Models:** `high <short> · low <short>`, then `· classifier <short>` when a classifier model is configured. Short names follow the status bar's `short_model`.
5. **Budget:** `budget $X.XX a run`, or `no run budget` when `max_cost_usd` is 0.

### 3.2 Below the card

**Pick up where you left off.** This is left out entirely when there's nothing to pick up.

- **The newest unfinished run in this repo** (state pending, running, escalated, failed or stopped), with its next step. The banner prints before you know which chat will open, and `/answer` and `/resume` act only on the chat's own run, so every hint is a CLI command that works from any chat:
  - escalated: `⏸ <run> is waiting for you (<summary>) · phil attach <run>`. The summary is the run's last escalation summary, with all whitespace (newlines included) collapsed to single spaces. Longer than 60 characters, it's cut to 59 and ends in `…`. Empty, the ` (<summary>)` part is left out: `⏸ <run> is waiting for you · phil attach <run>`.
  - failed: `<run> failed · phil resume <run>`.
  - stopped: `<run> was stopped · phil resume <run>`. A run still in `running` whose worker is dead (after a crash or a reboot) is shown as stopped.
  - running or pending: `<run> is running · phil attach <run>`.
- **Other open chats in this repo:** `N other open chat(s) · phil --resume <id>`, showing the newest one's id. The open-chat picker that runs after the banner is unchanged; this line is only a reminder.

**Tips:** one line in `phil.muted`, with the command names in normal weight.
- Normally: `Type a goal to start · /btw ask while it works · /feed filter the feed · /help everything else`.
- The first time Phil runs in a repo (no runs and no saved chats): `Describe what you want built. Phil plans it, asks before it runs, and works on its own branch. · /help`.

**Removed:** the start-up header line, the separate uncommitted and parked lines, and the full `/help` paragraph. `/help` still prints it.

### 3.3 The mascot

- **Where it lives:** `src/phil/ui/mascot.py` defines `MASCOT: tuple[tuple[tuple[str, str], ...], ...]`. That's one row per line of art, and each row is `(style, text)` fragments.
- **Its width:** the widest row, measured in display cells.
- **The placeholder:** the robot face from the mockup, a 7-cell-wide frame with eyes and a mouth, in `phil.brand` and `phil.warn`.
- **Swapping it:** edit only this file.
- **Rule:** every row of the art is padded to the mascot's width, so art with uneven rows can't push the facts out of line.

### 3.4 Width and alignment

- **The box's inner width:** the widest row (mascot, gap and facts), capped at `terminal width - 1 - 4`. That's 2 cells of border and 2 of padding.
- **Overflow:** a facts row that doesn't fit is cut with `…`, measured in display cells.
- **Padding:** every row is padded with spaces to exactly the inner width, measured with `rich.cells.cell_len`. Colour and style never count toward width, because rows are built as `Text`, never markup.
- **So:** every line of the box, including the top and bottom borders, has exactly the same display width.
- **Below 60 columns:** no mascot. The facts are boxed alone.
- **Below 40 columns:** no box. The facts, "pick up" and tips print as plain lines.

### 3.5 Where the facts come from

`render_banner(facts: BannerFacts, width: int) -> list[Text]` in `src/phil/ui/banner.py` is pure. `BannerFacts` holds:

| Field | Type |
|---|---|
| `version` | `str` |
| `repo` | `str` |
| `branch` | `str` |
| `sha` | `str` |
| `base` | `str \| None` |
| `dirty` | `int` |
| `parked` | `int` |
| `models` | `tuple[tuple[str, str], ...]`, as (label, short name) |
| `budget_usd` | `float` |
| `run` | `tuple[str, str, str \| None] \| None`, as (run id, state, summary) |
| `other_chats` | `tuple[str, ...]` |
| `first_time` | `bool` |

`main.py` gathers them:
- **The version:** `importlib.metadata.version("phil")`, or `dev` when it can't be read.
- **Repo, branch, sha and dirty files:** the existing `info`.
- **Base:** the `--base` handling that already exists.
- **Parked items:** `list_parked`.
- **Models:** `config.model_for` per tier.
- **Budget:** `config.run.max_cost_usd`.
- **The run:** the newest unfinished run for this repo, and `events.latest("escalation")` for its summary.
- **Other chats:** `list_open_chats`, minus the chat being reopened.
- **First time:** no runs and no saved chats for this repo.

Every lookup is guarded. A failure shows as `?` or leaves the fact out, and never stops the chat from starting.

**Piped or CI use (no TTY):** the same facts print as plain lines, with no box, no art and no colour.

### 3.6 Testing

- **The right edge:**
  - every line of the box has the same `cell_len` at widths 60, 80 and 120;
  - this holds with a CJK repo name, an emoji branch name, and a long model name that gets cut;
  - it holds with a mascot of a different width and with uneven art rows.
- **Width tiers:** at 59 columns there's no mascot. At 39 columns there's no box, and every plain line fits.
- **Content:**
  - every pick-up state: escalated with its summary, failed, stopped, running, other chats, and nothing;
  - `--base`;
  - no classifier;
  - no budget;
  - the dirty and parked parts;
  - the first-run tips versus the normal tips.
- **Robustness:**
  - piped output: no box characters and no ANSI codes;
  - an unreadable version gives `dev`;
  - a database error leaves "pick up" out.
- **The chat:** the long `/help` paragraph no longer prints at start-up, and the card does.

## 4. Out of scope

- Choosing the final mascot. That's the user's to decide, and it's a one-file swap.
- A "what's new" line.
- Animation.
- Banners for other commands (`phil run`, `phil setup`).

## 5. Done means

- `phil` opens with the card. The right edge lines up at every width, and the "pick up" and tips lines are below it.
- The mascot is swappable in one file.
- CI passes on Ubuntu, macOS and Windows.
- The user checks it live, and picks a mascot when ready.
