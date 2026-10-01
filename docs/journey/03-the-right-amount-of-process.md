# Building Phil, part 3: The right amount of process

*October 1, 2026*

[Part 2](02-configure-once-any-model.md) gave Phil a strong model and a fast one. Part 3 starts putting them to work. Phil now reads each message and decides how much process it deserves. A question gets an answer, not a project plan. And because we wanted to know whether a dedicated classifier was worth it, we measured it, wrote down how we would judge it before we saw the numbers, and kept to that.

## Where we started

Every message went down the same road. "What does this function do?" and "add a login system" both went through intake, an architect, a critic, a run of tasks, a tester and a review. In M1's benchmark, a one-line change still cost 25–30 model calls and about two minutes. A plain question had no shortcut at all: it got a plan.

## The idea that changed the design

Our first design had a classifier take over the step that turns your message into a goal. Then Andrew described the classifier he wanted to use: TypeSafe's **Jev**, a "System One" model that answers typed questions (pick one of these options; how likely is this?) quickly and cheaply. It doesn't generate text. So it can tell Phil what kind of request something is, but it can't write the follow-up question when a request is too vague.

That pushed the design somewhere better. Classification became a **decision primitive**, not a pipeline stage. For each message, Phil asks Jev two questions at once:
- **Which of nine kinds of work is this?** For example, a question, a diagnosis, a simple change or a feature, each with a description and examples.
- **Is it too vague to act on without asking you something first?**

Phil's own code then applies a fixed policy:
- If the request is vague, or Jev isn't confident, the generative intake step takes over. It can write the questions.
- Otherwise the kind of work picks the path:
  - **answer:** reply in chat, read-only, no run;
  - **quick:** coming in M3b;
  - **full:** today's pipeline.

If Jev isn't configured or fails, your fast model makes the same judgement. If that fails too, intake decides. Routing never blocks the chat.

You see one status line, such as `Question · answering` or `Simple change · planning`, and you can override it with `/ask`, `/quick` or `/full`.

## Answers without a run

Questions and "why is this broken?" now go to a new **answerer**: a lean, read-only agent that can list, read and search files, and run only read-only shell commands. It answers in chat, cites the files it used, and never starts a run. After a diagnosis it offers: *Fix it? [Enter = plan the fix / n]*.

## Problems the reviews caught

Every task was reviewed on its own, and the whole branch was reviewed at the end. Several of the most important fixes came from those reviews, not the plan:

- **A read-only agent that could write.** The agent library we build on quietly saves large tool results as files in its working folder, bypassing the write rules. For the answerer that folder was your repository. We turned that behaviour off and added a test that fails if it ever comes back.
- **A cap that wasn't a cap.** The answerer's 12-call limit asked the model to answer on its last call. But if the model kept giving a malformed answer, it could loop for thousands of paid calls. There is now a hard stop, and a test confirms that even a model that never gives a valid answer stops after exactly 28 calls.
- **Secrets in plain sight.** The answerer read your live working folder, so it could have read `.env`. It now reads a snapshot of your current files that leaves out everything git ignores. Your uncommitted edits are still visible; `.env` isn't.
- **A pasted smart quote in an API key** used to crash routing and show one character of the key in the error. Now any failure becomes a plain "request failed", and the fast model takes over.
- **Your answers to Phil's questions got lost** if the request then turned out to be a question for the answerer. They now reach the answerer.

## Being honest about what we did

Two smaller moments shaped how Phil talks to you.

**Cancelling setup.** Setup used to store a key in the keychain the moment you typed it, so cancelling later still left the key behind. The review caught that the cancel message then said "nothing was written". We fixed the wording first, but Andrew pushed further: *"We should be honest in our dealings."* Cancel should mean nothing was kept. Setup now holds keys in memory, lets the model checks use them, and stores them only once your config is written. Cancel at any step saves nothing, and says exactly that.

**An error that explained nothing.** The first benchmark run failed all 40 cases with a bare `ConfigError`. Phil's tests deliberately hid the real keychain, and the benchmark recorded only the error's type, to be safe. Now, live tests can read stored keys but never change them. Phil's own errors, which are written so they never contain a key, are recorded in full ("openrouter needs OPENROUTER_API_KEY"). Messages from other libraries, which could contain anything, are still reduced to their type.

## Did the dedicated classifier earn its place?

We built a benchmark of 40 labelled requests: questions, diagnoses, typos, fixes, features, refactors, designs, and six deliberately vague ones. We wrote the decision rule into the spec **before** running it. Jev would be recommended over the fast model only if it was within 2 points on accuracy, made no more of the worst error (treating a question as a change request), was at least 3× faster, and was cheaper.

| | Jev | Fast model (DeepSeek flash) |
|---|---|---|
| Correct path | 87.5% | 90% |
| Questions treated as changes | 0 | 0 |
| Features sent down the light path | 0 | 1 |
| Typical / slowest response time | 0.21 s / 0.33 s | 1.6 s / 2.8 s |
| Cost per 100 messages | less | about $0.03 |

Jev was about 8.5× faster, cheaper, and never made a risky mistake. Every miss it made was cautious: it handed a clear request to intake to double-check. The fast model's one miss was the riskier kind.

But the gap on accuracy was exactly one request out of 40, which is 2.5 points. So by the rule we'd written, **the verdict is no.** We kept it. Changing the rule after seeing the results would defeat the point of writing it first.

The data also shows why Jev hesitated. It scores "is this vague?" on a different scale from the fast model, and the single threshold we gave both was too cautious for Jev. Before the next run, we'll write a revised rule: deferring to intake will be counted separately from choosing the wrong path, the tolerance will be at least one case, and each classifier will get its own threshold. We expect Jev to win once it's tuned. When it does, it'll be by a rule we agreed on before the run.

## The results

- **Questions are answered in chat** without a run: one routing call and a short read-only answer, where before they got a full plan.
- **Every message is routed and recorded**, with its class, path, source and confidence.
- **Routing never blocks the chat:** Jev falls back to the fast model, which falls back to intake.
- **Setup is honest:** cancel saves nothing.
- **The test suite grew from 1,464 to 1,640 tests**, all offline, with no network access and no real keychain.

## What's next

M3a decides how much process a message needs. **M3b** makes the light path real. A small change will get:
- one task;
- no architect or critic;
- a lighter implementer;
- one review, with any findings fixed inside the same task.

If it struggles, Phil offers to move it up to the full plan and carries over what it learned. The target is under 10 model calls and under a minute for a one-line change, against the 25–30 calls and two minutes we started with.

After that, we'll come back to Jev with the revised rule.
