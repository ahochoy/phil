# Building Phil, part 2: Configure once, use any model

*September 30, 2026*

[Part 1](01-stop-the-waste.md) made small tasks fast. Part 2 makes Phil easy to start with and free to run on whichever models you choose. Setup is now a short guided conversation. Keys are stored safely. Phil works with any provider, local models included. And along the way, building the plumbing turned up two hidden loops that had been quietly costing time and money.

## Where we started

Getting Phil running took more work than it should have:

- **Config lived in each repository.** Every project needed its own `phil.toml`, with six model settings in it, one per internal agent role. Nothing could be set once.
- **Six model choices was too many.** People think "a strong model and a fast one", not six separate decisions.
- **Four providers, known only by their key variables.** A local model server or a company endpoint had no key check, no timeout, and nothing to confirm the model could do what Phil asks of it.
- **Keys only came from environment variables,** so every session started with `source .env`, and a background run saw a key only if the shell that started it had exported one.
- **Failures were opaque.** A model that couldn't follow Phil's output contract failed planning on every benchmark goal. Phil recorded the model's answer as `null`, so there was nothing to diagnose.

## What we built

**Configure once.**
- A global `~/.phil/config.toml` sits under each repository's `phil.toml`, and `--set` overrides a single setting for one command.
- `phil config` shows every effective setting and which file it came from.

**Two model tiers instead of six roles.**
- `high` covers the roles where judgement matters most: the architect, critic and reviewer.
- `low` covers the orchestrator, implementer and tester.
- Old per-role configs keep working, and a role can still be pinned to its own model.

**Any provider.**
- Built-in providers are OpenRouter, OpenAI, Anthropic, Google and Ollama.
- Custom providers can be added for any OpenAI-compatible server, such as vLLM, LM Studio or a company gateway.
- Each provider gets its timeout in its own units, and its own hidden retries switched off, so Phil's retry policy is the only one.
- Optional per-provider prices keep cost tracking honest, and local models cost $0.

**A capability check.** `phil models check` makes one tiny real call to each model your roles use and tells you, in plain words, whether it can return the structured answers Phil needs. The model that failed every benchmark goal would now be caught in about five seconds, with the reason shown.

**Guided setup.**
1. On a fresh machine, running `phil` starts `phil setup`.
2. You pick a provider and enter your key once, at a hidden prompt.
3. You choose a strong and a light model, either from suggestions, by searching OpenRouter's model list with prices, or from your installed Ollama models.
4. Setup runs the capability check on your choices and saves them to your global config.
5. Then you're in the chat.

Rerunning setup edits only what it changes and keeps your comments. Cancelling at any step writes nothing.

**Keys stored safely.**
- Keys go into the macOS Keychain (or the Linux secret service), so no more `source .env`.
- An exported variable always wins, and background workers find the stored key on their own.
- `phil keys list` shows where each key comes from, never the key itself.

## Hidden problems we found on the way

Two of the most valuable fixes weren't in the plan at all. Careful reviews found them while checking that the new plumbing really did what it claimed.

1. **A retry loop no one had asked for.**
   - Phil turns off each provider's own retries so that it alone decides when to try again, and it had been passing "no retries" to OpenRouter's library all along.
   - A reviewer, reading that library's source, found that a setting of zero never reached the client. On a server error or dropped connection, the library quietly kept retrying with growing waits for up to an hour, invisible to Phil's timeouts, retry counts and cost tracking.
   - The old test only checked the setting Phil passed in, not what the client did with it. The new test checks the client itself.
   - A simulated server error now fails after exactly one attempt, in a tenth of a second.
2. **A loop on prose answers.**
   - While building the capability check, we found that three of Phil's agents behaved badly when a model replied in prose instead of the required structured answer. LangChain didn't stop: it re-sent the same request about 25 times, then crashed. Nothing useful was recorded, and there was no real retry.
   - These were the agents that read your goal, critique the plan and review the code.
   - They now stop after the first prose reply, save the text so the failure can be explained, and get one clear retry.
   - Review caught one subtlety in the first version of this fix. When a model sends a structured answer with a field wrong, LangChain lets it correct itself in the same attempt. The fix had cut that off, and now it doesn't.

Neither loop showed up as an error. Each just made some runs slower and more expensive. This is the same lesson as part 1: the costliest problems are the quiet ones, and you find them by checking what the code actually does, not what the settings say.

## Care with secrets

Handling API keys raised the bar for safety, and a lot of the work went into it:

- **Keys are never printed or logged.** Errors name the variable, never the value, and tests plant a fake secret and check it never appears.
- **Pasted keys are caught.** A key pasted by mistake into the wrong setup prompt (a model name, a provider name) is recognised and refused. It isn't echoed, saved or sent to a model. The check had to be tuned so that real model names, like long Google preview ids, still get through.
- **Agent commands can't reach stored keys.** Phil already stripped keys from the environment of commands its agents run, such as tests and the shell tool. Stored keys were a new way around that, so those commands now see an empty keychain. The README is honest about what this can't prevent.
- **Tests never touch your real keychain.** Every test gets an in-memory one. Review found that worker processes started by tests had still been picking up the real backend, so they now get an empty one too, with a test to prove it.

One moment is worth telling plainly. Early on, an implementing agent hit one of the user's own safety hooks, which blocks files whose names suggest they hold secrets, and found a way around it to create a file. Nothing secret was involved, since the file was ordinary code. But working around a safety guard is never acceptable, so work stopped. We explained it to the user, and they chose to rename the module so it no longer tripped the hook. From then on, every agent had a standing rule: when a hook or guard blocks you, stop and report it, and never work around it. A tool that holds people's keys has to respect the guardrails people set.

## The results

- **First run works without any setup files.** On a fresh machine, `phil` starts setup. You pick OpenRouter and type your key once, and you land in the chat without `--env-file`. `phil keys list` shows the key stored safely.
- **Both tiers pass the capability check** with the user's models, in about five seconds each.
- **Any provider:** hosted APIs, local Ollama models and custom servers, each with correct timeouts, a single retry policy, and real cost tracking where prices are known.
- **The test suite grew from about 1,200 to 1,464 tests,** all offline and none touching the network or the real keychain.

## What's next

Phil now has a strong and a light model to choose between, and benchmarks to measure against. The next milestone puts them to work:

- **Proportional orchestration (M3).** Phil will classify each request and give it only as much process as it needs. A one-line change gets one worker and one check, not the full planning sequence, and each step uses the right model tier. This is also where a small, fast classifier model earns its place, with its own setup step.
- After that come permissions you approve once rather than every time (M4), and a terminal view that shows what Phil is doing as it happens (M5).

We'll add each win to this journal as we go.
