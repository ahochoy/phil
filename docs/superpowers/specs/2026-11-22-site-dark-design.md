# Site visual pass: dark, φ as geometry (short design)

**Status:** approved by the user, 2026-11-22, from the full-page mockup. It follows the content
reframe (`2026-11-22-site-reframe-design.md`, PR #67), which fixed the copy and section order.

## Intent

The site moves away from the Greek philosophy look (marble busts, warm cream) to a STEM one: the
φ as a mathematical symbol. The look is dark and plain, borrowing griffin.com's treatment: a
near-black page, a large light serif heading, uppercase mono labels, pill buttons and hairline
rules. Bauhaus primaries are the only accents.

## Goals

- A near-black page with white and off-white text, and red, yellow and blue as accents.
- A golden-ratio spiral, drawn in code, as the hero visual.
- A token field band near the footer.
- A social card and README banner that match.

## Non-goals

- No change to the copy, the section order, or what the page says (all settled in PR #67).
- No new sections and no new interactivity beyond the spiral's draw-in.

## Anti-goals

- Colour used as decoration everywhere. The primaries stay small: dots, the spiral's squares, a
  few highlights.
- Any return of the busts or the philia look.
- A heavy hero. The spiral is inline SVG; no frames, video or canvas.

## Approaches

1. **Restyle the existing Astro components, with colour tokens in `global.css` (picked).** The
   content, tests and deploy stay; only styling and the hero visual change.
2. A new site. Nothing gained over restyling.

## Design

**Tokens.** Page `#0C0C0B`; body text `#D4CFC6`; headings `#FFFFFF`; muted `#8C877F`; hairlines
`#2A2927`. Accents: red `#E2402F`, yellow `#F4C430`, blue `#2F5BD3`. Each path keeps one colour
wherever it appears: answer blue, quick yellow, full red.

**Type.** Headings in Newsreader Light (300) with tight tracking; body in Inter (300/400); the
nav, labels, buttons and small captions in JetBrains Mono, uppercase, with wide tracking. Plus
Jakarta Sans is dropped.

**Shapes.** Pill buttons: solid (off-white `#E6E1D9` on dark text) and outlined. Sections are
separated by hairline rules; cards are cells divided by hairlines, not shaded boxes. Terminal
panels are darker than the page (`#080808`) with a hairline border.

**Hero.** The copy keeps its order. The visual is the golden spiral as inline SVG: a 377×233
golden rectangle split into Fibonacci squares (233, 144, 89, 55, 34, 21) in hairlines; the 89,
55 and 21 squares filled yellow, blue and red; a white quarter-circle spiral through them; a
small mono caption "φ = 1.618…". On load the spiral draws itself once and the three squares fade
in after it; with reduced motion it appears drawn. The scroll-scrubbed frame player is removed:
`hero-frames.ts`, `hero-math.ts` and its test, `public/hero/`, `scripts/frames.sh`,
`scripts/placeholder-clip.sh`, the `js-scrub` head script and its README section.

**Token field.** A band just above the footer, about ten rows of monospace characters drawn from
`φ λ Σ { } → 0 1 / · ∂`. It is densest on the left and thins to the right, and a few characters
are white or in the primaries. It is generated when the site is built, from a fixed seed, so it
is the same on every load and needs no JavaScript. It is decorative and hidden from screen
readers.

**Social card and README banner.** A 1200×630 image of the new hero, captured from the built
page, replaces `banner.jpg` as `og:image` and as the README banner. The old banner images are
deleted.

## Constraints

- `cd site && npm run build && npm test` passes; the page-content tests from PR #67 still pass.
- No sideways scroll at 375 px; the hero fits at 1280×720.
- Text contrast meets WCAG AA on the dark background (muted text included).

## Left to the builder

How the draw-in is done (CSS stroke-dashoffset or similar), the token field's exact density
curve and seed, and how the social card is captured.
