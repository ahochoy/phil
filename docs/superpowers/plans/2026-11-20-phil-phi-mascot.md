# Phi Mascot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the welcome card's placeholder face with a 6-row φ and replace the card's full box with a single muted line underneath.

**Architecture:** `src/phil/ui/mascot.py` holds the art as data (`MASCOT`); `src/phil/ui/banner.py::render_banner` lays out art, gap and facts. Only the layout's border changes: rows lose `│ `/` │` and the top line, and the bottom line becomes a plain `─` rule as wide as the content.

**Tech Stack:** Python 3.14, rich `Text`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-20-phil-phi-mascot-design.md`

## Global Constraints

- Mascot rows, exactly, each one `("phil.brand", row)` fragment:
  `" ▄█▀ █▀▀█▄"`, `"██   █   ██"`, `"██   █   ██"`, `" ▀█▄ █ ▄█▀"`, `"   ▀▀█▀▀"`, `"     █"`.
- No top line, no side borders; the bottom line is `─` repeated, style `phil.muted`.
- The bottom line is as wide as the card's content (mascot, `GAP`, widest fact), capped at the terminal width less one column.
- Card rows start at column 0.
- Unchanged: `NARROW_MASCOT` (60) drops the mascot below it, `NARROW_BOX` (40) drops the card below it; `banner_plain`; `FittedBanner`'s fallback; the facts and their wording.
- Edit repo files only with Edit/Write. Test command: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q -n auto -p no:cacheprovider`.

## Review Focus

1. A long fact at width 60–80 (e.g. a very long model name): every card line, rule included, stays within `width - 1` cells, so nothing wraps. Pinned by the reworked width test in Task 1.
2. Wide characters in the repo or branch name (CJK, emoji): the rule still matches the rows' width in cells, not characters. Pinned by the same parametrized test.
3. Width exactly `NARROW_MASCOT - 1`: the card still has a rule but no φ, and the rule is only as wide as the facts. Pinned in `test_tier_edges`.
4. The full-screen pane resizing narrower than 40: `FittedBanner` drops to plain lines with no stray rule. Covered by the existing `NARROW_BOX - 1` assertion, updated to also check for no rule.
5. A hand-edited mascot that raises: the CLI still falls back to plain lines. Existing `broken` test in `tests/cli/test_banner_startup.py`, updated to look for the rule instead of `│`.

---

### Task 1: The φ and the bottom-line card

**Files:**
- Modify: `src/phil/ui/mascot.py` (the art and the docstring)
- Modify: `src/phil/ui/banner.py` (`render_banner`, module docstring)
- Test: `tests/ui/test_banner.py`, `tests/cli/test_banner_startup.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `render_banner(facts, width, mascot=MASCOT) -> list[Text]`, same signature. For `width >= NARROW_BOX` it returns the card rows, then one rule line, then the lines below. Card rows and the rule all have the same cell width (`inner`).

- [ ] **Step 1: Update the tests to the new card**

In `tests/ui/test_banner.py`:
- Replace `box_lines` with `card_lines(lines)`: the plain lines up to and including the first line made only of `─` (empty list if there is none). Add `rule(lines)`: that last line, or `None`.
- `test_every_box_line_has_the_same_width` → `test_every_card_line_has_the_same_width`: same parametrization; assert `card_lines` is non-empty, all have one cell width, all `<= width - 1`, and no line contains `╭`, `╰` or starts with `│`.
- New `test_mascot_is_the_phi`: `[ "".join(t for _, t in row) for row in MASCOT ]` equals the six rows in Global Constraints, and every fragment's style is `"phil.brand"`.
- New `test_card_has_only_a_bottom_line`: at width 120, the first line starts with `" ▄█▀ █▀▀█▄"` and contains `"Phil v0.9.0"`; `rule(...)` is not `None`, its `Text` style is `"phil.muted"`, and its width equals the widest card row.
- `test_uneven_and_wider_mascots_still_line_up`: use `card_lines`, drop the rule before collecting starts, and expect `set(starts.values()) == {art_w + GAP}`.
- `test_tier_edges`: check `"█" in t.plain` for the mascot at `NARROW_MASCOT` and its absence at `NARROW_MASCOT - 1` (where `rule(...)` is still not `None`); at `NARROW_BOX` a rule exists; at `NARROW_BOX - 1` there is no rule and every line is `<= NARROW_BOX - 2` cells.

In `tests/cli/test_banner_startup.py`:
- `test_chat_opens_with_the_card_and_no_help_paragraph`: the line containing `"Phil v"` and a line containing `"calc"` both come before a line made only of `─`.
- `test_piped_start_prints_plain_facts`: also assert no line is made only of `─`.
- The broken-renderer test: replace the `│` check with "no line is made only of `─`".

- [ ] **Step 2: Run them and watch them fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q -p no:cacheprovider tests/ui/test_banner.py tests/cli/test_banner_startup.py`
Expected: FAIL in `test_mascot_is_the_phi`, `test_card_has_only_a_bottom_line`, the width test (it finds `╭`), the uneven-mascot test (off by 2) and `test_chat_opens_with_the_card…` (no rule line). The piped and broken tests pass already.

- [ ] **Step 3: Replace `MASCOT` in `src/phil/ui/mascot.py`**

Six rows from Global Constraints, one `("phil.brand", row)` fragment each. Docstring: Phil's mascot, a lowercase φ for *philia* (spec 2026-11-20); still swapped by editing `MASCOT` only; rows may differ in width.

- [ ] **Step 4: Change the border in `render_banner`**

`inner_cap = width - 1` (no borders to leave room for). Drop the top line and the `│ `/` │` wrapping; each row is the padded `row` itself. After the rows, append `Text("─" * inner, MUTED)`. Everything else (tiers, centring, `below`) stays. Update the module docstring: "mascot left, facts right, a line underneath".

- [ ] **Step 5: Run the banner tests**

Run: same command as Step 2.
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q -n auto -p no:cacheprovider`
Expected: all pass. Any other test that looked for `╭`/`│` in the banner gets the same rule-line treatment.

- [ ] **Step 7: Changelog and commit**

Add a line under `## [0.1.0] - Unreleased` → `### Added` in `CHANGELOG.md` (0.1.0 isn't published yet, so it belongs there): the welcome card shows Phil's φ mascot, with a line under it instead of a box.

```bash
git add src/phil/ui/mascot.py src/phil/ui/banner.py tests/ui/test_banner.py tests/cli/test_banner_startup.py CHANGELOG.md
git commit -F .git/phil-commit-msg.txt   # "Welcome card: the phi mascot and a bottom line"
```
