# Phil: Classifier Setup with Decision Models

**Status:** approved in conversation, 2026-10-06.

## 1. Problem

The 2026-10-06 benchmark rerun made Jev Phil's recommended classifier (journal part 5). `phil setup` already has a classifier step, but it falls short in three ways:

- It defaults to "your low model".
- It offers Jev only through TypeSafe, which means a second API key.
- It checks the classifier against the `typesafe` provider no matter which one was chosen.

Decision models are becoming a category of their own:
- **TypeSafe** offers Jev.
- **OpenRouter** serves Jev (`typesafe/jev-1.13`) and Liquid's `d1` through a TypeSafe-compatible System One endpoint, `POST https://openrouter.ai/api/v1/systemone`. It also has an alpha Decisions API, `POST /api/alpha/decisions`.
- **OpenAI** announced a Decisions API on 2026-09-29. It's in limited preview, with no public docs or pricing yet.

## 2. Decisions (user, 2026-10-06)

| Topic | Decision |
|---|---|
| Sources this round | TypeSafe direct and OpenRouter (Jev and `d1`). OpenAI comes once its API is public. |
| Default in setup | Recommend Jev. Through OpenRouter when that's the user's main provider (no new key); through TypeSafe otherwise. |

## 3. Design

### 3.1 A built-in provider: `openrouter_decisions`

- **The `BUILTIN_PROVIDERS` entry:** `ProviderSpec("openrouter_decisions", SYSTEMONE, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", None, None)`.
- **Models:**
  - `openrouter_decisions:typesafe/jev-1.13`
  - `openrouter_decisions:liquid/d1`
- **Like `typesafe`:** it's kind `systemone`, so it's allowed only as the classifier (the existing check in `PhilConfig`).
- **Adapter:** the Jev adapter (`phil/routing/jev.py`) already posts to `{base_url}/systemone` with a bearer key, so it needs no new code if the response matches TypeSafe's.
  - **First implementation step:** record an OpenRouter System One response, either from the docs or from one live call made by the user, as a test fixture.
  - If its shape differs (fields, usage, errors), adapt `parse_response`. Both shapes must keep parsing.
- **Cost:**
  - If OpenRouter's response reports a cost, Phil records it as `reported`.
  - Otherwise the cost is `unknown`. Phil doesn't guess, because `openrouter_decisions` has no built-in price.
  - TypeSafe direct keeps its built-in price.
- **Keys:** `phil keys` lists the provider under the OpenRouter key it shares. One key serves both, and storing it once is enough.

### 3.2 The classifier step in `phil setup`

The options are listed in this order, labels as shown:

1. `Keep <current>`, only when the global file already sets a classifier.
2. `Jev through OpenRouter (recommended; uses your OpenRouter key)`.
3. `Jev through TypeSafe (recommended without OpenRouter; needs TYPESAFE_API_KEY)`.
4. `d1 through OpenRouter (experimental: routing thresholds were tuned for Jev)`.
5. `Your low model`.

**Pre-selection:**
- with a current classifier, `Keep <current>`;
- otherwise, Jev through OpenRouter when the main provider chosen in setup is `openrouter`;
- otherwise, Jev through TypeSafe.

The cursor starts on the pre-selected option. The text marks Jev as recommended.

**Keys:**
- OpenRouter choices reuse the OpenRouter key from the main provider step: either the one entered and held in memory, or the one already stored or exported. Only when neither exists does setup ask for it, using the existing key step.
- TypeSafe asks for `TYPESAFE_API_KEY`, as today.
- Keys stay in memory and are saved only when setup finishes, as today.

**Check:** one ping (`ping_jev`) against the **chosen** classifier's provider and model, not `typesafe` hard-coded. On failure, setup offers `Use your low model instead` (the default) or `Keep <choice> anyway`, as today.

**Written:** `models.classifier = "<provider>:<model>"`. Choosing the low model writes nothing, and leaves an existing classifier in place with today's note.

### 3.3 Elsewhere

- **`phil models check`:** pings the classifier through its own provider. That already works for any `systemone` provider; a test pins it for `openrouter_decisions`.
- **Routing and the classifier benchmark:** unchanged. They go by provider kind, so `d1` can be benchmarked by pointing `PHIL_BENCH_CONFIG`'s classifier at it.
  - Known limit: Phil labels every decision model's judgement `jev`, so `routing.jev_detail_threshold` applies to `d1` too.
  - That's why `d1` is labelled experimental. Per-model thresholds wait for a `d1` benchmark.
- **README:**
  - the classifier section lists the three sources (TypeSafe, OpenRouter, low model), the recommendation, and the experimental `d1`;
  - it shows the `openrouter_decisions` provider in the providers table;
  - it removes "The `classifier` tier isn't part of setup yet".

## 4. Out of scope

- OpenAI's Decisions API, until it has public docs.
- OpenRouter's `/api/alpha/decisions` endpoint (the System One endpoint is stable).
- Per-model detail thresholds.
- Any change to routing policy or defaults.

## 5. Testing (offline)

- **The provider:**
  - `openrouter_decisions` resolves with the spec above;
  - a classifier on it passes the config's systemone check;
  - a non-classifier role on it is rejected.
- **The adapter:** parses the recorded OpenRouter response (and still parses TypeSafe's), and records a reported cost when present, or unknown cost otherwise.
- **Setup:**
  - the options, in the order above;
  - pre-selection for an `openrouter` main provider, for any other main provider, and with a current classifier;
  - the OpenRouter key is reused without a second prompt, and asked for when absent;
  - the check targets the chosen provider and model;
  - a failed check falls back to the low model;
  - cancelling saves nothing.
- **`phil models check`:** reaches `https://openrouter.ai/api/v1/systemone` for an `openrouter_decisions` classifier.

**Live (user):** run `phil models check` with `classifier = "openrouter_decisions:typesafe/jev-1.13"`. It should show `✓ classifier`.
