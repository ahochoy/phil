# Building Phil, part 1: Stop the waste

*September 30, 2026*

Phil is a coding agent that works like a careful teammate. You describe a goal in a chat. Phil plans it with you, runs the work in its own git worktree test-first, reviews the result, opens the pull request, and cleans up after the merge. By the end of September, all of that worked end to end.

Then we gave it a trivial job.

## The one-line change that took fifty minutes

The job was to add a hidden `<meta name="easter-egg" content="hello world">` tag to a one-page Astro site. It's a developer Easter egg. Nothing renders, so there's nothing to test. Any frontier coding agent does this in seconds.

Phil worked on it for about 50 minutes. It made 438 model calls and read more than 3 million input tokens while writing about 80 thousand. It split the change into three tasks. It finished two of them, then failed the third three times in a row, and we stopped the run.

We were using a cheaper model on purpose. The tempting explanation was "the model is weak", and that explanation was wrong. A model can be slow, but it should still be capable, and a well-built harness should get a small job done even with a modest model. So we treated the run as evidence and went through its records call by call.

## What the records showed

The model wasn't the main problem. Phil's own guardrails were tripping it into loops:

| What happened | What it cost |
|---|---|
| The agent explored with `ls`, `grep`, `cat` and `git status`. None of these were on the shell allow list, so every one paused the run for human approval. | 13 pauses. After each one the agent started over and explored the whole repository again: 186 `ls` calls and 166 file reads. |
| The model cited file-tool calls ("I read `index.astro`") as evidence. Phil only accepted shell commands as evidence. | 26 correct answers rejected on a technicality, each one followed by a retry. |
| Each retry, approval or resume started a brand-new agent with no memory of the last attempt. | Nearly all of the 3 million input tokens: context sent again and again, not new work. |
| The test command was wrong for the repository, and once a run started, its test command could never change. | Every test step failed, even after the config was fixed. |
| The planner split one tag into three tasks, including a new JSON data file "to match conventions" and a task whose only job was to verify. | A verification-only task can't start from a failing test, so it could never pass Phil's test-first rule. |

None of these was exotic. Each came from a reasonable safety or quality rule, applied without thinking about what it would cost on small work.

## What we changed

We wrote a design and a plan, then built it in eight reviewed steps:

- **A benchmark first.** Before touching anything, we built a live benchmark. It has two tiny sample repositories (a Python package with tests, and a static site with only a build step) and three goals: add a function, add the hidden meta tag, and fix a typo. It runs the real planner and worker from start to finish and records time, model calls, tokens, cost, and pass or fail. That gave us an honest before-and-after.
- **Read-only exploration without asking.** Agents can now list, read and search the repository without pausing for approval. The test and check commands from a plan you approved are allowed too. Anything that would reach outside the run's worktree is still refused. This part got the most scrutiny: four rounds of security review, each tested against the real `grep`, `rg` and `git`, closing escape routes through symlinks, values attached to flags, and options that follow links or run programs. An agent can now look around freely, and it still can't touch anything it shouldn't.
- **Evidence that matches how agents work.** A claim can now cite the file tool that produced it.
- **Check tasks.** Not everything is testable. Copy, markup and config changes can now be *check* tasks: make the change, then prove it with a command such as the site's build. They skip the failing-test step. The planner is also told never to create a task that only verifies.
- **Memory between attempts.** Each attempt leaves a short work summary, and the next attempt gets that summary and the current diff instead of starting from zero. If a human approves a command mid-task, the agent carries on from where it stopped.
- **A test command that fits the repository.** Phil detects the test command from the repository. If you fix it in the config while a run is paused, the run picks up the change.
- **Leaner prompts.** Every agent now gets the same short rules: explore with the file tools, don't re-read what you already have, don't build more than the task needs, and stop when the goal is met.

## The results

We ran the benchmark before and after the changes, with the same model for every role (`gemini-3.8-flash`):

| Goal | Before | After |
|---|---|---|
| Add a Python function (test-first) | ✅ 3.0 min · 32 model calls · $0.15 | ✅ **2.0 min** · 31 calls · **$0.13** |
| Add the hidden meta tag | ⏸ stuck waiting for approval after 4.2 min · $0.16 | ✅ **1.8 min** · 30 calls · **$0.11** |
| Fix a typo | ⏸ stuck waiting for approval after 3.8 min · $0.18 | ✅ **1.8 min** · 24 calls · **$0.09** |

- **Every goal now finishes.** Before, both web-page goals stalled, waiting for someone to approve running the site's own build.
- **Each goal is planned as one task of the right kind:** a check task for the page changes, a test-first task for the function.
- **Small tasks finish in under two minutes** from goal to reviewed, committed change: roughly a third faster on the one goal that finished before, with about half the output tokens and lower cost.
- **Stalled runs don't burn tokens.** The "before" costs for the stalled goals are only what they spent before stopping. Finishing them would have cost more.

## Why this matters

The fixes that mattered most weren't clever. They came from reading the run's records carefully and asking, for every rule Phil enforces, what it costs when the work is small. Being safe doesn't have to mean being slow. Phil now lets agents explore freely inside a strict sandbox, and approving a command means exactly that command, nothing wider. That is safer in the places that matter, and much faster.

The process mattered too. Every change started from written evidence, and every step had an independent review. The security boundary got as many rounds as it needed. The benchmark shipped first, so the "before" numbers were real and not remembered.

## What's next

Two minutes and about 25–30 model calls is still a lot for a one-line change. The next milestones go after it:

- **Configuration and models:** set your preferences once, globally. Choose a strong and a light model tier instead of one model per agent. Bring any provider, including local models.
- **Proportional orchestration:** Phil will classify each request and use only as much process as it deserves, so a one-line fix gets one worker and one check, not the full planning ceremony.
- **Visibility:** a terminal view that shows what Phil is doing as it happens, including costs, tool calls and failures, explained plainly.

We'll add each win to this journal as we go.
