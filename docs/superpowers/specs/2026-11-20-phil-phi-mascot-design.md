# Phil's φ mascot on the welcome card — short design

Date: 2026-11-20 · Status: draft for the user's approval · Roadmap: release work, "finalize the mascot".

## Intent

The welcome card's mascot is a placeholder: a boxed face (`src/phil/ui/mascot.py`). The user wants a simple lowercase phi (φ, for *philia*) in its place, drawn as text art, matching a reference picture they supplied. This is about the in-chat look only. The website and the wider branding are separate work.

## Goals, non-goals, anti-goals

- **Goal:** the card shows the φ on the left and the facts on the right, with only a line under the card instead of the full box.
- **Non-goals:** no change to the README banner images, the facts, the wording, the colours, or the plain (piped) output.
- **Anti-goal:** a mascot that breaks or crowds the card in narrow terminals or in the full-screen chat.

## Approaches

Sizes tried, rendered in the real card:
- **A. Block characters, 6 rows** (picked): closest to the reference; its stem goes below the loop like a lowercase φ.
- B. Block characters, 5 rows: the same height as the facts, but the stem is stubby.
- C. Box-drawing lines, 4 rows: lightest, but reads less like the reference.

Borders tried: the full box (today), a bottom line only (picked), no border. With a mascot this large, side borders are clutter; the bottom line still separates the welcome from the chat.

## Design

The mascot, in `phil.brand` (bold cyan), every row one fragment:

```
 ▄█▀ █▀▀█▄
██   █   ██
██   █   ██
 ▀█▄ █ ▄█▀
   ▀▀█▀▀
     █
```

The card (wide terminal):

```
 ▄█▀ █▀▀█▄     Phil v0.1.0 · deterministic orchestration, token-efficient
██   █   ██
██   █   ██    phil @ main f88a4e8
 ▀█▄ █ ▄█▀     plan claude-opus-5-5 · code claude-sonnet-5-5
   ▀▀█▀▀       budget $2.00 a run
     █
─────────────────────────────────────────────────────────────────────────
```

- No top line and no side borders. The bottom line is muted (`phil.muted`), as the box was.
- *(My call)* The bottom line is as wide as the card's content (the mascot, the gap and the widest fact), capped by the terminal width less one column, as the box was. A line across the whole terminal would look like a divider that belongs to the chat, not the card.
- *(My call)* Rows start at column 0, as in the mockup the user picked. The lines below the card (resume hints, first-time tips) already start there.
- Narrow terminals keep today's rules: under `NARROW_MASCOT` (60 columns) the mascot is left out; under `NARROW_BOX` (40 columns) there is no card, just the lines. Facts still shorten to fit.
- The full-screen chat draws the same card at the pane's width (`FittedBanner`), unchanged apart from the new look.
- The plain output (piped, or when drawing fails) is unchanged.
- `mascot.py`'s docstring no longer calls the mascot a placeholder.

## Left to the builder

- Renaming tests and constants that say "box" where that's now misleading.
- Updating the guide or the changelog if they describe the boxed card.
