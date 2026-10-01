# Plan M2b follow-ups

Deferred items from implementing plan M2b ("setup and keys"). Plan:
`2026-09-30-phil-m2b-setup-and-keys.md`. Spec:
`../specs/2026-09-30-phil-m2b-setup-and-keys-design.md`. Ledger:
`.superpowers/sdd/2026-09-30-phil-m2b-setup-and-keys/progress.md`.

## M3 (roadmap)

- **The classifier's setup step.** `phil setup` has no step for the `classifier` tier; it is
  set by hand under `[models]` until M3 wires the classifier into the guided flow.

## Google suggestions (Task 3)

- `phil.setup.suggestions.SUGGESTIONS` has no entry for `google`, because the current Pro
  model id wasn't verified at the time (`src/phil/setup/suggestions.py`). Setup still offers
  `google` as a provider choice; it just asks for both model ids by hand instead of
  prefilling one. Add a `google` entry once a current Pro id is confirmed against Google's
  own docs.

## Key storage (Task 1)

- Each key lookup re-reads the keychain; there is no cache.

## `phil keys` (Task 2)

- No test covers `phil keys remove` on a keyless or unknown provider.
- Some internal helpers are missing return-type annotations.

## Setup (Task 3)

- When a repo's `phil.toml` overrides `models.high`, setup's rerun doesn't prefill that
  value; it falls back to the provider's suggestion instead of the overridden model.
- A custom provider's prefill reads the merged config rather than only the global file's own
  value (the same "only this rerun's own values" rule the tier prefill follows).
- The global config's directory isn't fsynced after the atomic replace (the file itself is).
- When setup writes through a broken symlink, the summary could name the real target (e.g.
  "via `<target>`") instead of just the symlink path.
- A Ctrl-C during `write_global_config`'s actual write shows Click's own "Aborted!" message
  rather than setup's own cancellation wording — safe, since the write is atomic (either the
  old file or the fully-written new one survives), but the message is inconsistent with every
  other cancellation path.
