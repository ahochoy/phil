# Spike: a codebase digest to cut the architect's cost

**Date:** 2026-10-05. Told as a story in [part 4](../04-paying-for-what-you-use.md). **Outcome:** concluded; no repo map. Instead, cap the architect at 10 calls and tell it that budget (PR #16 and PR #17).

## The problem

In a live chat on `roi-calculator`, a small Next.js repo of 73 tracked files, a single architect call for "add a pricing section with animations" cost **$3.17**:

- 46 model calls;
- 1.56M input tokens;
- 44 `read_file`, 23 `grep`, 5 `ls` and 2 `glob` calls.

It ended without a plan, when OpenRouter refused for lack of credits. Nothing bounded it: only the light agents had a call cap, and the cost budget covered runs, not the chat's own planning.

A stopgap merged first, in PR #16. It added a 20-call cap for the architect and a per-goal planning budget (`[chat] max_cost_usd`, default $1.00).

## The hypothesis

The architect starts from `repo_overview`, which is a list of file names plus the start of the README (0.8k tokens). So it has to discover the repo's structure by reading, and every model call resends everything read so far. Giving it a compact digest of the structure up front should cut the reading, and with it the calls, the tokens and the cost, without worse plans.

## What we tried

### Offline: three candidates on roi-calculator

| Candidate | Output | Size | Gives the page structure? |
|---|---|---|---|
| Today's `repo_overview` | file names, start of the README | 0.8k tokens | no |
| [codebase-digest](https://github.com/kamilstanuch/codebase-digest) (`cdigest`, Python) | every file concatenated, with a tree and statistics | 68k tokens (lockfile and images excluded) | yes, by including everything |
| [agentic-codebase](https://crates.io/crates/agentic-codebase) (`acb` v0.1.4, Rust) | a binary graph you query (symbols, dependencies, impact) | 196 KB graph | no |
| Phil prototype repo map (throwaway, regex-based) | per file: exports, local imports, rendered JSX tags in order, CSS variables, Markdown headings, `package.json` scripts and dependencies | 2.2k tokens | yes, e.g. `… LeverGrid ResultsSummary LeadCaptureForm footer …` |

For scale: all of `src/` is 41k tokens. **The $3.17 call spent 38 times the whole source tree**, so the cost came from resending, not from the size of the repo.

**codebase-digest** is a full dump. Its `--no-content` flag gave identical output, and it hangs on an interactive clipboard prompt unless stdin is closed. It adds nothing that Phil's packets can't already do.

**agentic-codebase:**
- **Build:** it compiled the repo in 0.12s (173 units, 95 edges).
- **TSX:** it misses JSX usage. `page.Home` shows 0 dependencies, although it renders ten components.
- **Version:** the prebuilt binary is v0.1.4; the crate is at 0.3.0, which would need cargo.
- **Use:** it's useful as a symbol finder, not as a picture of how a page is put together.

### Live: the prototype map against the plain overview

The prototype was switched on with a throwaway switch on a local branch. Both arms used the same goal ("an animated 3-tier pricing section near the bottom of `src/app/page.tsx`, lightweight CSS transitions only"), the same architect model (`anthropic/claude-sonnet-5` through OpenRouter), and `n` at approval.

**The first attempt found a bug instead.** Both arms failed with "no structured output", at about $1.80 each:

- At the cap, Sonnet kept calling `read_file` and `ls`, although Phil offered only `Plan`, with `tool_choice: required`. An offline check confirmed that the request itself was correct.
- The tool executor still ran those calls, so the architect explored until the hard stop, and the retry repeated it.
- It also wasted calls on guessed `/repo/...` paths.

This was fixed in PR #16's second commit:
- capped calls see the history as plain text;
- tool calls made past the cap aren't run;
- the prompts say paths start at `/`.

A live probe with a cap of 3 then returned a valid plan in 5 calls, about $0.15.

**Results:**

| Run | Architect calls | Architect cost | Goal total |
|---|---|---|---|
| Original (no cap) | 46 | $3.17 (no plan) | – |
| Cap 20, without map | 21 | $0.91 | ~$0.94 |
| Cap 20, with map | 20, then 20 for a critic revision | $2.29 | ~$2.36 |
| Cap 10 with the budget in the prompt, without map | 11 | $0.38 | ~$0.41 |
| Cap 10 with the budget in the prompt, with map | 11, then 11 for a critic revision | $1.05 | ~$1.09 |

All the plans were usable, and they put the section in the same place (after `LeadCaptureForm`, before the footer). One 20-call plan with the map added three test dependencies nobody asked for.

## What we learned

- **The model reads until it's stopped.** Sonnet used its whole call budget every time: with the map or without it, and even when its prompt stated the budget. It read 13–25 files a run. Knowing the structure up front didn't make it stop early.
- **Cost follows the call count.** Each call resends the history, so the cap is what sets the cost: about $0.90 at 20 calls, about $0.40 at 10, about $0.15 at 3, with comparable plans.
- **The map never reduced the reading.** It also adds about 2k tokens to every call. Both map runs drew a critic revision, which doubles the cost. With one run per arm, that may be chance, but the map showed no saving anywhere.
- **Capping only works if it's enforced on Phil's side.** A provider can ignore the tool restriction, so a capped agent needs a history it can't continue as tool calls, and refused tool calls.

**Limits of the evidence:**
- One run per arm.
- One small Next.js repo.
- One architect model, Sonnet 5.
- The arms of each comparison ran at the same time. That doesn't affect the per-chat numbers.

## Decision

1. **No repo map for now.** Delete the throwaway branch. Revisit only if a model or harness actually stops exploring when it has enough. The prototype's approach is in this document if we do.
2. **Cap the architect at 10 calls and state the budget in its prompt** (PR #17). Planning this goal went from $3.17 (and no plan) to about $0.41.
3. **Rejected: codebase-digest.** It's a full dump that adds nothing.
4. **Deferred: agentic-codebase.** It may be worth it later as a query tool for very large repos (for example, "what does this change affect?" for the reviewer), on a version newer than v0.1.4.
5. **Next ways to cut cost, before the Jev revisit:**
   - prompt caching, since every call still resends 15–25k tokens;
   - cheaper critic revisions, since today a revision is a full architect pass and could run with a smaller cap.
