# Building Phil, part 4: Paying for what you use

*October 5, 2026*

[Part 3](03-the-right-amount-of-process.md) taught Phil to match the process to the request. Part 4 is about cost. A single plan for a small website change cost $3.17 and never finished. Tracking down why led to a bug in how Phil stops an agent, a spike that disproved our first idea, and a fix that plans the same change for about $0.41.

## Since part 3

Four pieces of work landed between part 3 and this one:

- **The quick path (M3b).** A small, well-specified change gets one task, a lighter implementer and a single review. If that isn't enough, it moves up to a full plan.
- **Worktrees that can actually run.** Phil installs a run's dependencies before it starts. It stops with a clear message when a test command can't be found, instead of passing a check that never ran.
- **Honest outcomes.** An attempt that changes nothing fails. A run that finishes with blocking issues still open is `incomplete`, not `completed`. Phil won't offer a PR for an empty branch.
- **Asking before acting.** Phil's clarifying questions are now multiple choice, asked one at a time. Phil asks for content only you can supply (links, copy, placement, style). When there's more than one sensible design, a designer proposes two or three approaches for you to pick from.

All four came from live runs going wrong. So did this part.

## A $3.17 plan

Andrew asked Phil, in a chat on a small Next.js repo, to "add a pricing section with animations". The new questions worked: three multiple-choice answers settled the tiers, the placement and the animation style. Then the architect started planning on Claude Sonnet and didn't stop:

- **46 model calls** in about 90 seconds;
- **44 file reads, 23 searches**, in a repo with 73 tracked files;
- **1.56 million input tokens**, for **$3.17**.

It ended only when OpenRouter refused the next call, because the account had run out of credits. There was no plan.

Nothing had been designed to stop it. The cheap agents, like the answerer, had a cap on model calls, but the architect didn't. The cost budget applied to background runs, not to the planning that happens in the chat.

The number that explains it: the whole `src/` folder is about 41,000 tokens. The architect spent 38 times that. It wasn't reading a big codebase; it was rereading its own history. Every model call resends everything read so far, so cost grows with the square of the number of calls.

## A ceiling, then a cap that didn't hold

The first fix was a ceiling:
- **A call cap:** the architect could make at most 20 model calls.
- **A planning budget:** each goal's planning in the chat got a budget (`[chat] max_cost_usd`, $1.00 by default). Phil warns at 80%, and at the limit it skips the critic's revision and shows the plan it has.

The first live test of the cap failed in a new way: two plans, about $1.80 each, and no plan from either. When the architect reached its cap, Phil removed every tool except "return the plan" and required a tool call. An offline check showed that Phil's request was exactly that. Sonnet, through OpenRouter, kept calling the file tools anyway. Phil's tool executor still had them, so it ran them, and the architect kept exploring until the hard stop.

So the cap only worked if the model obeyed it. We now enforce it on Phil's side:

- **History becomes text.** From the cap on, the conversation is sent as text ("You called read_file…", then the result), not as a pattern of tool calls the model can continue.
- **Late calls are refused.** A tool called past the cap isn't run. It returns "your tool budget is used up; return your plan now".
- **Paths are spelled out.** The architect had also wasted calls guessing `/repo/...` paths. The prompts now say paths start at `/`.

A cheap live probe confirmed the fix: capped at 3 calls, Sonnet returned a valid plan in 5, for about $0.15.

## Would a map of the code help?

The obvious theory was that the architect reads so much because it starts almost blind: a list of file names and the start of the README. Give it a compact map of the code up front and it should read less.

We ran this as a spike, with the question and the probe agreed before any code was written. The full record is in [spikes/codebase-digest.md](spikes/codebase-digest.md). In short:

| Candidate | What it gives the architect | Size on this repo |
|---|---|---|
| Today's overview | file names, the start of the README | 0.8k tokens |
| codebase-digest | every file, concatenated | 68k tokens |
| agentic-codebase | a graph to query (missed how pages use components) | – |
| A prototype repo map | per file: exports, imports, the sections a page renders, styling, scripts | 2.2k tokens |

The prototype map looked right on paper. It showed the page's sections in order, which is exactly what you need to know where a pricing section goes. So we compared it live: the same goal and the same model, once with the map and once without, answering "no" at approval so only planning was measured.

| Run | Architect calls | Cost of planning the goal |
|---|---|---|
| Before any cap | 46 | $3.17, no plan |
| Cap of 20, no map | 21 | ~$0.94 |
| Cap of 20, with map | 20, plus 20 for a revision | ~$2.36 |
| Cap of 10, budget in the prompt, no map | 11 | **~$0.41** |
| Cap of 10, budget in the prompt, with map | 11, plus 11 for a revision | ~$1.09 |

**The map didn't help.** In every run the architect used its whole call budget, map or no map. That held even when its prompt said how many calls it had and told it to stop once it knew enough. It read 13–25 files whatever it already knew. The map only added tokens to every call, and both runs with it happened to draw a revision from the critic, which doubled their cost.

What did change the cost was the cap. Plans at 10 calls were as usable as plans at 20, and both put the section in the same place. So the decision was:

- **No repo map.**
- **Cap the architect at 10 calls,** and state that budget in its prompt.
- **Rejected:** codebase-digest. It adds nothing Phil can't already do.
- **Deferred:** agentic-codebase, to revisit for very large repositories.

The theory was reasonable, the evidence said no, and we kept what the evidence said. One run per arm on one repo isn't conclusive, and the record says so. But nothing in the data pointed the other way.

## Errors that explain themselves

One smaller fix came out of the same afternoon. Switching models, Andrew got `BadRequestResponseError: Provider returned error` and nothing more. OpenRouter had accepted the request and Anthropic had rejected it, but the reason never reached the screen. OpenRouter sends the provider's own explanation along with the error, and Phil had been throwing it away. Phil now shows it: `Provider returned error (Anthropic: <the reason>)`. It appears in `phil models check`, in the chat and in runs, and it never includes a key.

## The results

- **Planning this goal costs about $0.41**, down from $3.17. That was a run that never finished; plans now finish.
- **Every architect pass is bounded:** at most 10 model calls, plus 2 to fix a malformed plan. The cap holds even when the provider doesn't enforce it.
- **Each goal's planning has a budget** you can see and change.
- **A rejected model call says why.**
- **A theory we didn't build:** the spike record keeps the data that ruled out the repo map, so we don't spend on it again without new evidence.

## What's next

Two more ways to cut cost come before we return to the Jev classifier:

1. **Prompt caching.** Every architect call still resends 15–25k tokens of history. Caching that repeated part can make it far cheaper on providers that support it.
2. **Cheaper revisions.** When the critic asks for changes, the architect currently starts a full pass from scratch. It has already read the repo, so a revision shouldn't need a full budget.

After that, we'll come back to Jev with the revised rule from part 3.
