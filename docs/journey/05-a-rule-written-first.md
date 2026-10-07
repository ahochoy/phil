# Building Phil, part 5: A rule written first

*October 6, 2026*

In [part 3](03-the-right-amount-of-process.md), we benchmarked Jev, TypeSafe's fast classifier, against an ordinary language model for deciding how much process each chat message needs. Jev was 8.5× faster and 6× cheaper, but it missed the decision rule we'd written in advance by one request out of 40. We kept the "no", and promised to come back with a revised rule written before the next run. This is that run.

## Why the first rule was the wrong shape

The first rule judged accuracy as one number: did the message take the right path? But "wrong" covered two very different outcomes:

- **Choosing the wrong path.** For example, treating a question as a change. That is a real mistake.
- **Deferring to intake.** The classifier says "not sure", and the step that can ask you a question decides instead. That's slower, but it's safe.

Most of Jev's misses were the second kind. The rule counted them like the first.

The data also showed that Jev scores "is this request too vague?" on a different scale from the language model. We had given both the same threshold.

## The revised rule, written down before the run

Before anything ran, the spec set out what Jev would have to show to be recommended. All five conditions must hold:

1. **Wrong paths:** no more than the language model's, plus one.
2. **Unsafe errors:** no more than the language model's. These are a question treated as a change, or a big feature sent down the quick path.
3. **Deferrals to intake:** no more than the language model's, plus two.
4. **Latency:** at most a third of the language model's (p95).
5. **Cost:** lower.

Each classifier now gets its own thresholds, chosen on the original 40 requests: the **tune** set. Those thresholds are then shown on 10 new requests nobody had tuned anything on: the **check** set. The check set includes the two live requests behind the recent changes: "add a CTA section at the bottom of the page featuring the AI systems audit and the AI readiness guide", and "add a pricing section with animations". Seven of the ten are vague in the same way. They name the kind of change but leave out the content: which links, what copy, which logo.

We also fixed one label. Part 3 had labelled "add a multiply function to calc with tests" as a feature, which needs a full plan, and called it debatable. Our own end-to-end benchmark expects the twin of that request to take the quick path, so we relabelled it and recorded why in the case file.

## What the review caught before we spent anything

The code was reviewed task by task, then as a whole branch. The whole-branch review found four problems that would have undermined the run:

- **The live benchmark would always have failed.** It still checked for exactly 40 requests, and there were now 50.
- **The threshold chooser could win by deferring everything.** It put "fewest wrong paths" above "fewest deferrals" with no limit. The reviewer built a set where removing one wrong path meant deferring all 40 requests, and the chooser took that trade. That contradicts the rule's own tolerances. The chooser now stays within a deferral budget: the number of vague requests, plus two.
- **The two classifiers could be compared on different requests.** If one run was cut short, the comparison would have been 50 requests against 40. The report now compares only the requests both classifiers answered, and says how many.
- **The report would give a verdict before the check set had run.** Replaying the old results already printed "yes". Now it calls the verdict provisional until the check set has run.

None of these was about the classifier. Each was about whether the benchmark could be trusted.

## The result

| Over all 50 requests, at each classifier's chosen thresholds | Jev | Language model |
|---|---|---|
| Wrong paths | 0 | 1 |
| Unsafe errors | 0 | 1 (a full feature sent down the quick path) |
| Deferrals to intake | 8 | 11 |
| Response time (p95) | about 0.3 s | about 3.7 s |
| Cost per 100 messages | $0.0055 | $0.023 |

All five conditions hold. **Jev is now Phil's recommended classifier.**

## What the verdict doesn't say

The numbers are clean, but one detail matters. The chooser picked a very high "too vague" threshold for Jev (0.9). Anything lower made Jev defer far too many clear requests in the tune set. At 0.9, Jev's vagueness score hardly ever triggers on its own. Vague requests reach intake mostly when Jev is unsure what kind of work they are.

That held up on the tune set: 5 of 6 vague requests deferred. It didn't on the check set. Jev confidently routed 5 of the 7 content-missing requests, including the pricing request, straight to a path. The language model missed 3 of the 7. The pattern is reasonable. "Add our new logo to the header" is unmistakably a simple change. What's missing is the logo, not the kind of work.

That gap no longer costs much. Since the work in part 4, intake asks for content only you can supply (links, copy, placement, style) on every path, including the quick one. A vague request that the classifier waves through is still asked about before anything is planned. The classifier decides how much process a message needs; intake makes sure it isn't missing anything.

The check set held some surprises too. On these website requests, Jev's vagueness score did separate vague from clear: 0.42 and up for vague, 0.24 and below for clear. On the older, code-heavy requests, it didn't separate them nearly as well. One benchmark on one kind of repo can't settle that.

## The new defaults

- **Confidence threshold:** 0.5 → **0.7**. Both classifiers use it. Replayed at 0.7, the language model fallback also did better on the new requests, with no wrong paths or unsafe errors.
- **Jev's own vagueness threshold:** **0.9**, new.
- **The language model's vagueness threshold:** stays at 0.6.

## What's next

`phil setup` doesn't ask for a classifier yet. Today you add one by hand under `[models]`. Decision models like Jev are becoming a category of their own, and more providers are building them. So the next step is to teach setup to offer a classifier, with Jev as the recommended choice. Setup should explain what a classifier does, and fall back gracefully when you don't have one.
