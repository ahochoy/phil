# Welcome Banner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `phil` opens with a boxed card. The card holds a swappable mascot on the left and the facts on the right: identity, repo, models and budget. Below it go "pick up where you left off" and short tips. Every line of the box is exactly the same display width.

**Architecture:**
- A pure renderer, `render_banner(facts, width) -> list[Text]`, plus `banner_plain(facts) -> list[str]` for when there's no TTY, in `src/phil/ui/banner.py`.
- The mascot art lives in `src/phil/ui/mascot.py`.
- `cli/main.py` gathers `BannerFacts` with guarded lookups and prints the banner in place of today's header, uncommitted-files, parked and `/help` prints.

**Tech Stack:** Python 3.14, Rich `Text` and `cell_len`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-phil-welcome-banner-design.md`

**Base:** branch `plan-banner`, stacked on `agent-bar` (PR #33). Rebase it onto `main` once #33 merges.

## Global Constraints

- **Editing and committing:**
  - Edit files only with the Edit and Write tools. Never edit through python, perl, sed, heredocs (including empty ones) or printf in Bash.
  - To commit, write the message to a file with Write, then run `git commit -F <file>`.
  - Every commit message ends with a blank line, then exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, whatever model you are.
- **Safety:**
  - Keep the words "keychain" and "credentials" out of Bash command lines.
  - Never work around a hook or guard. Report BLOCKED instead.
  - Never read or print `.env` files.
- **Tests:**
  - Never run `-m live` or `-m bench`.
  - Iterate with `uv run pytest <paths> -q -n 0`. Run the full `uv run pytest -q` once at the end of each task, and wait for it before committing.
  - Tests must be portable, and test output must stay clean.
- **Rendering:**
  - Text is built as Rich `Text`, never as markup.
  - Widths are measured with `rich.cells.cell_len`.
  - The banner never stops the chat from starting.
- **Constants:**

| Name | Value |
|---|---|
| `NARROW_MASCOT` | `60`. Below this width, no mascot. |
| `NARROW_BOX` | `40`. Below this width, no box. |
| `GAP` | `4`. Cells between the mascot and the facts. |

- **Copy, verbatim:**
  - `Phil v{version} · deterministic orchestration, token-efficient`
  - `budget ${x:.2f} a run`
  - `no run budget`
  - `⏸ {run} is waiting for you ({summary}) · /answer`
  - `{run} failed · /resume`
  - `{run} was stopped · /resume`
  - `{run} is running · phil attach {run}`
  - `{n} other open chat · phil --resume {id}` (`chats` when n > 1)
  - `Type a goal to start · /btw ask while it works · /feed filter the feed · /help everything else`
  - `Describe what you want built. Phil plans it, asks before it runs, and works on its own branch. · /help`

## Review Focus

1. **A repo name with CJK characters and a branch name with an emoji.** The right border stays straight. Tested in Task 1.
2. **A mascot with uneven row widths, or one wider than the placeholder.** The facts and the border stay aligned. Tested in Task 1.
3. **A terminal exactly 60 or 40 columns wide.** These are the tier edges: 60 still shows the mascot, and 40 still shows the box. Tested in Task 1.
4. **The run database being unreadable at start-up.** The banner leaves "pick up" out, and the chat starts. Tested in Task 2.
5. **Piped stdout.** The output has no box characters and no ANSI escape codes. Tested in Task 2.

---

### Task 1: The mascot and the banner renderer

**Files:**
- Create: `src/phil/ui/mascot.py`, `src/phil/ui/banner.py`, `tests/ui/test_banner.py`

**Interfaces:**
- Produces:
  - `MASCOT: tuple[tuple[tuple[str, str], ...], ...]`
  - `BannerFacts`, a frozen dataclass with the spec §3.5 fields:

| Field | Type |
|---|---|
| `version` | `str` |
| `repo` | `str` |
| `branch` | `str` |
| `sha` | `str` |
| `base` | `str \| None` |
| `dirty` | `int` |
| `parked` | `int` |
| `models` | `tuple[tuple[str, str], ...]` |
| `budget_usd` | `float` |
| `run` | `tuple[str, str, str \| None] \| None` |
| `other_chats` | `tuple[str, ...]` |
| `first_time` | `bool` |

  - `render_banner(facts: BannerFacts, width: int, mascot=MASCOT) -> list[Text]`
  - `banner_plain(facts: BannerFacts) -> list[str]`
  - The constants `NARROW_MASCOT`, `NARROW_BOX` and `GAP`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/ui/test_banner.py
import pytest
from rich.cells import cell_len

from phil.ui.banner import NARROW_BOX, NARROW_MASCOT, BannerFacts, banner_plain, render_banner

FACTS = BannerFacts(
    version="0.9.0", repo="calc", branch="main", sha="a1b2c3d", base=None, dirty=2, parked=3,
    models=(("high", "claude-sonnet-5"), ("low", "gemini-3.8-flash"), ("classifier", "jev-1.13")),
    budget_usd=1.0, run=("r-4f2a", "escalated", "CALC-002 needs approval"), other_chats=("7c1e",),
    first_time=False,
)


def box_lines(lines):
    return [line.plain for line in lines if line.plain[:1] in "╭│╰"]


@pytest.mark.parametrize("width", [60, 80, 120])
@pytest.mark.parametrize("facts", [
    FACTS,
    BannerFacts(**{**FACTS.__dict__, "repo": "計算器プロジェクト", "branch": "feat/🚀-launch"}),
    BannerFacts(**{**FACTS.__dict__, "models": (("high", "an-extremely-long-model-name-" * 4),)}),
])
def test_every_box_line_has_the_same_width(width, facts):
    lines = box_lines(render_banner(facts, width))
    assert lines and len({cell_len(line) for line in lines}) == 1
    assert all(cell_len(line) <= width - 1 for line in lines)


def test_uneven_and_wider_mascots_still_line_up():
    mascot = ((("", "  ^^  "),), (("", "(o_o)  wide one"),), (("", "/"),))
    lines = box_lines(render_banner(FACTS, 100, mascot=mascot))
    assert len({cell_len(line) for line in lines}) == 1
    first_fact_col = {line.index("Phil") for line in lines if "Phil v" in line}
    assert first_fact_col  # identity row is present


def test_tier_edges():
    assert any("◠" in t.plain or "╭─────╮" in t.plain for t in render_banner(FACTS, NARROW_MASCOT))
    assert not any("◠" in t.plain for t in render_banner(FACTS, NARROW_MASCOT - 1))
    assert box_lines(render_banner(FACTS, NARROW_BOX))
    plain = render_banner(FACTS, NARROW_BOX - 1)
    assert not box_lines(plain) and all(cell_len(t.plain) <= NARROW_BOX - 2 for t in plain)


def test_facts_content():
    text = "\n".join(t.plain for t in render_banner(FACTS, 140))
    assert "Phil v0.9.0 · deterministic orchestration, token-efficient" in text
    assert "calc @ main a1b2c3d · ⚠ 2 uncommitted · 3 parked" in text
    assert "high claude-sonnet-5 · low gemini-3.8-flash · classifier jev-1.13" in text
    assert "budget $1.00 a run" in text


def test_base_no_classifier_no_budget():
    facts = BannerFacts(**{**FACTS.__dict__, "base": "release", "models": (("high", "a"), ("low", "b")),
                           "budget_usd": 0.0})
    text = "\n".join(t.plain for t in render_banner(facts, 140))
    assert "calc · base release a1b2c3d" in text and "uncommitted" not in text
    assert "classifier" not in text and "no run budget" in text


@pytest.mark.parametrize("run,expected", [
    (("r-1", "escalated", "CALC-002 needs approval"), "⏸ r-1 is waiting for you (CALC-002 needs approval) · /answer"),
    (("r-1", "failed", None), "r-1 failed · /resume"),
    (("r-1", "stopped", None), "r-1 was stopped · /resume"),
    (("r-1", "running", None), "r-1 is running · phil attach r-1"),
    (("r-1", "pending", None), "r-1 is running · phil attach r-1"),
])
def test_pick_up_states(run, expected):
    facts = BannerFacts(**{**FACTS.__dict__, "run": run})
    assert expected in [t.plain for t in render_banner(facts, 140)]


def test_other_chats_and_nothing_to_pick_up():
    two = BannerFacts(**{**FACTS.__dict__, "other_chats": ("7c1e", "9a0b"), "run": None})
    assert "2 other open chats · phil --resume 7c1e" in [t.plain for t in render_banner(two, 140)]
    none = BannerFacts(**{**FACTS.__dict__, "other_chats": (), "run": None})
    text = "\n".join(t.plain for t in render_banner(none, 140))
    assert "/answer" not in text and "other open chat" not in text


def test_tips_first_time_and_returning():
    returning = [t.plain for t in render_banner(FACTS, 140)]
    assert "Type a goal to start · /btw ask while it works · /feed filter the feed · /help everything else" in returning
    first = BannerFacts(**{**FACTS.__dict__, "first_time": True})
    assert any(t.plain.startswith("Describe what you want built.") for t in render_banner(first, 140))


def test_plain_has_no_box_and_no_escape_codes():
    lines = banner_plain(FACTS)
    assert not any(ch in "".join(lines) for ch in "╭╮╰╯│") and "\x1b" not in "".join(lines)
    assert any("calc @ main a1b2c3d" in line for line in lines)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/ui/test_banner.py -q -n 0`.

- [ ] **Step 3: Implement**

```python
# src/phil/ui/mascot.py
"""Phil's mascot for the welcome banner (spec 2026-10-08 §3.3). A placeholder until the final
mascot is chosen: swap it by editing MASCOT only. Each row is (style, text) fragments; rows may
differ in width — the banner pads them to the widest."""

MASCOT: tuple[tuple[tuple[str, str], ...], ...] = (
    (("phil.brand", "╭─────╮"),),
    (("phil.brand", "│ "), ("phil.warn", "◠ ◠"), ("phil.brand", " │")),
    (("phil.brand", "│  "), ("phil.warn", "◡"), ("phil.brand", "  │")),
    (("phil.brand", "╰──┬──╯"),),
)
```

```python
# src/phil/ui/banner.py
"""The chat's welcome banner (spec 2026-10-08): a boxed card — mascot left, facts right — with
"pick up where you left off" and tips below. Every box line has the same display width."""

from dataclasses import dataclass

from rich.cells import cell_len
from rich.text import Text

from phil.ui.mascot import MASCOT

NARROW_MASCOT = 60
NARROW_BOX = 40
GAP = 4
TIPS = "Type a goal to start · /btw ask while it works · /feed filter the feed · /help everything else"
FIRST_TIPS = "Describe what you want built. Phil plans it, asks before it runs, and works on its own branch. · /help"


@dataclass(frozen=True)
class BannerFacts:
    version: str
    repo: str
    branch: str
    sha: str
    base: str | None
    dirty: int
    parked: int
    models: tuple[tuple[str, str], ...]
    budget_usd: float
    run: tuple[str, str, str | None] | None
    other_chats: tuple[str, ...]
    first_time: bool


def _fit(text: Text, cells: int) -> Text:
    out = text.copy()
    if out.cell_len > cells:
        out.truncate(max(cells, 0), overflow="ellipsis")
    return out


def _pad(text: Text, cells: int) -> Text:
    out = _fit(text, cells)
    out.append(" " * max(cells - out.cell_len, 0))
    return out


def _facts(f: BannerFacts) -> list[Text]:
    ident = Text()
    ident.append("Phil", "phil.brand")
    ident.append(f" v{f.version} · deterministic orchestration, token-efficient", "phil.muted")
    where = Text()
    where.append(f.repo, "bold")
    if f.base:
        where.append(f" · base {f.base} {f.sha}", "phil.muted")
    else:
        where.append(" @ ", "phil.muted")
        where.append(f.branch)
        where.append(f" {f.sha}", "phil.muted")
        if f.dirty:
            where.append(" · ", "phil.muted")
            where.append(f"⚠ {f.dirty} uncommitted", "phil.warn")
    if f.parked:
        where.append(f" · {f.parked} parked", "phil.muted")
    models = Text()
    for i, (label, name) in enumerate(f.models):
        if i:
            models.append(" · ", "phil.muted")
        models.append(f"{label} ", "phil.muted")
        models.append(name)
    budget = Text(f"budget ${f.budget_usd:.2f} a run" if f.budget_usd > 0 else "no run budget", "phil.muted")
    return [ident, Text(), where, models, budget]


def _below(f: BannerFacts) -> list[Text]:
    lines: list[Text] = []
    if f.run:
        run_id, state, summary = f.run
        if state == "escalated":
            shown = (summary or "")[:60]
            lines.append(Text(f"⏸ {run_id} is waiting for you ({shown}) · /answer", "phil.warn"))
        elif state == "failed":
            lines.append(Text(f"{run_id} failed · /resume"))
        elif state == "stopped":
            lines.append(Text(f"{run_id} was stopped · /resume"))
        elif state in ("running", "pending"):
            lines.append(Text(f"{run_id} is running · phil attach {run_id}"))
    if f.other_chats:
        n = len(f.other_chats)
        lines.append(Text(f"{n} other open chat{'s' if n != 1 else ''} · phil --resume {f.other_chats[0]}", "phil.muted"))
    lines.append(Text(FIRST_TIPS if f.first_time else TIPS, "phil.muted"))
    return lines


def _mascot_rows(mascot) -> tuple[list[Text], int]:
    rows = [Text.assemble(*[(text, style) for style, text in row]) for row in mascot]
    width = max((r.cell_len for r in rows), default=0)
    return [_pad(r, width) for r in rows], width


def render_banner(facts: BannerFacts, width: int, mascot=MASCOT) -> list[Text]:
    facts_rows, below = _facts(facts), _below(facts)
    if width < NARROW_BOX:
        return [_fit(t, width - 1) for t in [*facts_rows, *below] if t.plain]
    art, art_w = _mascot_rows(mascot) if width >= NARROW_MASCOT else ([], 0)
    lead = art_w + GAP if art else 0
    inner_cap = width - 1 - 4
    inner = min(max([lead + t.cell_len for t in facts_rows] + [art_w]), inner_cap)
    height = max(len(art), len(facts_rows))
    art_top, fact_top = (height - len(art)) // 2, (height - len(facts_rows)) // 2
    border = "─" * (inner + 2)
    out = [Text(f"╭{border}╮", "phil.muted")]
    for i in range(height):
        row = Text()
        if art:
            a = i - art_top
            row.append_text(art[a] if 0 <= a < len(art) else Text(" " * art_w))
            row.append(" " * GAP)
        f = i - fact_top
        fact = facts_rows[f] if 0 <= f < len(facts_rows) else Text()
        row.append_text(_fit(fact, inner - lead))
        line = Text("│ ", "phil.muted")
        line.append_text(_pad(row, inner))
        line.append(" │", "phil.muted")
        out.append(line)
    out.append(Text(f"╰{border}╯", "phil.muted"))
    return out + [_fit(t, width - 1) for t in below]


def banner_plain(facts: BannerFacts) -> list[str]:
    return [t.plain for t in [*_facts(facts), *_below(facts)] if t.plain]
```

- **The tests are the contract.** If `_fit` with `Text.truncate` doesn't measure in cells on the installed Rich, implement the cut with `rich.cells.set_cell_size`, and keep the styled spans for the text that stays.
- **The tier test** checks for the placeholder's characters (`◠` and its frame). Keep the placeholder as given.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ui -q -n 0`.

- [ ] **Step 5: Run the full suite, then commit**

Commit message: `Welcome banner renderer with a swappable mascot`, plus the trailer.

---

### Task 2: The chat opens with the banner

**Files:**
- Modify:
  - `src/phil/cli/main.py`, in the chat start-up path around lines 229–266: gather `BannerFacts`, then print the banner in place of the header, uncommitted-files, parked and `HELP` prints.
  - `README.md`, the "Getting started" section: one sentence on what the chat shows when it opens.
- Test: the existing chat command tests, which you find with `grep -rln "uncommitted file" tests/cli`, plus a new `tests/cli/test_banner_startup.py`.

**Interfaces:**
- Consumes: `BannerFacts`, `render_banner` and `banner_plain` (Task 1); `short_model` (`phil.ui.toolbar`); `list_runs` and `get_run` (`phil.store.runs`); `run_events` (`phil.store.events`); `list_open_chats` (`phil.chat.session`); `list_parked` (`phil.store.parked`).
- Produces: `_banner_facts(info, config, conn, paths, base_label, header_sha, base, session) -> BannerFacts`, where every lookup is guarded.

- [ ] **Step 1: Write the failing tests**

```python
# tests/cli/test_banner_startup.py — use the existing chat-command test harness (CliRunner with the chat
# patched to exit immediately); read tests/cli/test_chat_command.py for how it's driven.
def test_chat_opens_with_the_card_and_no_help_paragraph(...):
    """On a TTY-like console (force the tty path the way existing tests do): the output contains
    'Phil v' and the repo name inside box lines ('│'), the tips line, and does NOT contain the long HELP
    paragraph's distinctive text (e.g. 'Commands: /runs')."""


def test_piped_start_prints_plain_facts(...):
    """Not a TTY: output contains '<repo> @ <branch>' and the tips line, and no '╭', '│' or '\\x1b'."""


def test_a_paused_run_shows_in_pick_up(...):
    """A run for this repo in state escalated with an escalation event summary 'CALC-002 needs approval':
    the banner contains '⏸ <run> is waiting for you (CALC-002 needs approval) · /answer'."""


def test_a_broken_database_leaves_pick_up_out_and_the_chat_starts(...):
    """Make list_runs raise (monkeypatch): the banner prints without a pick-up run line and the chat
    still starts (the harness's first prompt is reached)."""


def test_first_time_tips(...):
    """A repo with no runs and no saved chats: the first-time tips line appears."""


def test_version_falls_back_to_dev(monkeypatch, ...):
    """importlib.metadata.version raising PackageNotFoundError: the identity row shows 'Phil vdev'."""
```

**Ruling:** these are specified by docstring. Write them with the existing CLI chat harness, and make every assertion stated. Existing tests that assert the old header line (`Phil · <repo> · base: …`) or the old "uncommitted file … not included in runs" line must change to assert the banner's equivalent facts, keeping their intent:
- the repo and its base or branch;
- that uncommitted files are flagged, and stay out of runs.

The "not included in runs" wording isn't in the card. If a test depends on that warning, ruling R1 applies: the card's "where" row keeps `⚠ N uncommitted`, and the plain output adds ` (not included in runs)` after it. Make that change in `banner_plain` only, and note it in the report.

- [ ] **Step 2: Run them to verify they fail**

- [ ] **Step 3: Implement**

In `main.py`, replace the header print, the uncommitted-files block, the parked print and `out.print(... HELP ...)` with:

```python
    facts = _banner_facts(info, config, conn, paths, base_label, header_sha, base, session)
    if tty:
        for line in render_banner(facts, out.width):
            out.print(line, soft_wrap=True)
    else:
        for line in banner_plain(facts):
            out.print(line, soft_wrap=True, markup=False, highlight=False)
```

The open-chat picker block stays where it is, after the banner.

`_banner_facts` gathers each field with a guard. Each failure leaves a safe default (`"?"`, `0`, `None`, `()` or `False`):

- **`version`:** `importlib.metadata.version("phil")`, or `"dev"` if that raises.
- **`repo`, `branch` and `sha`:** `info.root.name`, `info.branch or "detached"`, and `header_sha[:7]`.
- **`base`:** `base_label` when `--base` was given, otherwise None.
- **`dirty` and `parked`:** `len(info.dirty_files)` when there's no base, and `len(list_parked(conn))`.
- **`models`:**
  - `("high", short_model(config.model_for("architect")))`, the high tier's role;
  - `("low", short_model(config.model_for("implementer")))`;
  - `("classifier", short_model(config.models["classifier"]))`, only if `"classifier" in config.models`.
  - Read `config.py` for the exact way to get a tier's model; `tier_model(tier)` exists, so prefer `tier_model("high")` and `tier_model("low")`.
- **`budget_usd`:** `config.run.max_cost_usd`.
- **`run`:** the first `list_runs(conn)` record whose `repo` and project match this repo and whose state is in (pending, running, escalated, failed, stopped). Check how runs are scoped: the db is per project. For an escalated run, the summary comes from `run_events(paths, run.run_id).latest("escalation")["escalation"]["summary"]`, guarded.
- **`other_chats`:** the ids from `list_open_chats(paths, conn)`, excluding `session.id` when a session is being reopened.
- **`first_time`:** no runs in `list_runs(conn)` and no chats directory with saved chats. Guarded, defaulting to False.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/cli tests/ui -q -n 0`.

- [ ] **Step 5: Update the README, run the full suite, then commit**

Commit message: `The chat opens with the welcome banner`, plus the trailer.
