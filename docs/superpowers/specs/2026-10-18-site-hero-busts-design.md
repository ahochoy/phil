# Site hero: scroll-scrubbed marble busts

Date: 2026-10-18
Status: approved in conversation, awaiting written-spec review
Scope: `site/` (Astro 7, Tailwind 4, static output on Cloudflare)

## Goal

Redesign the hero of the Phil website. The copy moves to the left, and a scroll-scrubbed animation fills the right side. The animation shows several marble busts on pedestals, joined by glowing lines, while a camera arcs slowly around them. Each bust stands for one of Phil's subagents. The bottom of the hero fades smoothly into the cream page below it.

Success means the following:

- On desktop, scrolling through the pinned hero plays the orbit forward and backward, frame by frame, with no visible stutter.
- When the pin releases, the page continues without a visible seam.
- The copy and the CTAs stay readable and clickable throughout.
- Mobile, reduced-motion and no-JS visitors all get a good static hero.

## Decisions (from the brainstorm)

| Topic | Decision |
| --- | --- |
| Frame source | AI image-to-video, seeded with the reference still. The user generates the clip, and a script turns it into frames. |
| Scroll mode | Pinned scrub. A tall section with a sticky stage. |
| Scroll budget | `250vh` section height on `md` and up, held in one constant so it can be tuned later. |
| Composition | **B, right-side bleed.** The footage spans the right 68% edge to edge and fades out leftward into the taupe behind the copy. |
| Existing hero content | The "philia (φιλία)" badge stays as an eyebrow. The 4-metric strip moves just below the hero. |
| Install pill | A copyable `uv tool install …` command with a copy button. |
| Left column order | Badge, headline, buttons, paragraph, install pill (the sketch order). |
| Player | Canvas plus a WebP frame sequence. Not `<video>` scrubbing, and not a CSS sprite sheet. |

## 1. Layout, pin and fade

### Structure

```
<section class="hero-scroll">        250vh on md+; sets how long the scrub lasts
  <div class="hero-stage">           position: sticky; top: header height; height: 100svh − header
    <img class="hero-poster">        frame 1; hidden once the canvas has drawn
    <canvas class="hero-canvas">     right 68%; mask: linear-gradient(to right, transparent 0%, #000 38%)
    <div class="hero-copy">          left ~42% inside the max-w-6xl container, vertically centred
      badge · h1 · [View on GitHub] [How it started ↓] · paragraph · install pill + copy button
    <div class="hero-fade">          bottom of the stage, taupe → cream
</section>
<HeroMetrics />                      the existing 4-metric strip, now on cream
```

- `HeroMetrics.astro` is a new component that renders `SITE_CONFIG.metrics`. The markup moves out of `Hero.astro` unchanged, apart from recolouring for the cream background.
- `index.astro` renders `<Hero />` and then `<HeroMetrics />`. The other sections, including `TerminalDemo`, stay unchanged.
- The header in `Layout.astro` is unchanged. Its height is fixed at `h-18` (4.5rem), so the stage's sticky `top` and height use that value.
- The hero background stays `#352F2A`.

### Scroll timeline (md and up)

The scroll progress `p` runs from 0 to 1 across the pinned distance.

- **0 to 0.85:** the orbit runs from the first frame to the last.
- **0.85 to 1:** the hero holds, still pinned, on the last frame. This is a short stick (about 200px of scrolling at 900px tall).
- **On release:** the hero scrolls out like any other section, with its copy and footage fully visible. Only the resting bottom fade blends its lower edge into the cream metrics strip.

(Revised 2026-10-18 after review on the page. The first version raised a cream wash over the whole stage during the outro, and the user preferred stick-then-scroll.)

### Fade

- **The gradient:** an eased gradient of about 8 stops, from `#352F2A` to `#FAF8F5`. The extra stops prevent banding across the large change in brightness.
- **At rest:** it covers the bottom 15% of the stage. That is the "fades smoothly" edge visible on load.
- **Size:** it stays at this size throughout, so nothing rises during the outro. It extends 2px past the stage edge, where the stage's overflow clips it. At a fractional height the last row would otherwise be only partly painted, leaving a 1px grey line where the hero meets the page.

### Copy

- **Wording:** unchanged from the current hero, apart from the reordering above.
- **Headline size:** it shrinks to fit the half-width column, at a maximum of about 52px (`lg:text-[52px]`).
- **Movement:** the copy does not move or animate. Only the canvas and the fade respond to scrolling.

### Below `md` (768px)

- **No pinning:** the section height is `auto`.
- **Order:** the footage sits above the copy at a 16:10 aspect ratio, and the copy flows below it.
- **Mask:** the footage fades at the bottom only (`linear-gradient(to bottom, #000 70%, transparent)`), not from the left.
- **Scrub:** the footage still scrubs. Its progress is how far the media box has travelled through the viewport (see section 3).

### Reduced motion and no JS

- **Reduced motion** (`@media (prefers-reduced-motion: reduce)`):
  - the section height is `auto`;
  - only the poster shows;
  - the fade stays at its resting size.
- **No JS:** the canvas never draws, so the poster stays visible, and the section is only as tall as the stage.
- **How the height is gated:** the 250vh height comes from a `.js-scrub` class on `<html>`. An inline `<script>` in the `<head>` adds it before first paint, and only when reduced motion is off. Without JS, or with reduced motion on, the class is never added, so the extra height never exists.

## 2. Asset pipeline

### The clip (the user's step)

Generate it with an image-to-video model seeded with the reference still (`media-1792346042653.png`).

**Prompt guide:**

- **Camera:** a slow, smooth camera arc of about 60–90° around the group of busts. Not a full 360°, which would expose the room behind the camera and makes the faces drift.
- **Framing:** keep the busts in the **right two-thirds** of the frame throughout, so they never sit under the copy.
- **Links:** thin glowing amber and teal lines between the pedestals, pulsing gently.
- **Look:**
  - a steady camera, with no handheld shake;
  - no people;
  - warm late-afternoon light.
- **Output:** 16:9, at least 1080p, 5–8 seconds.

**Storage:** keep the source clip out of git. Only the frames generated from it are committed.

### The frame script

`site/scripts/frames.sh <clip> [count=120]` is a bash script that needs `ffmpeg` and `cwebp` (both are installed via Homebrew).

```
ffmpeg evenly samples <count> frames from the clip into a temporary directory as PNG
cwebp writes:
  site/public/hero/frames/1600/0001.webp … NNNN.webp   width 1600, q 72
  site/public/hero/frames/960/0001.webp  … NNNN.webp   width 960,  q 70
  site/public/hero/poster.webp                          frame 1 at 1600
  site/public/hero/frames.json   {"count": N, "widths": [960, 1600], "aspect": W/H, "pad": 4}
```

- **Sampling:** it uses the `fps` filter, computed from the clip's duration (read with `ffprobe`), so the output has exactly `count` frames.
- **Cleanup:** before writing, it clears the old `site/public/hero/frames/` so that stale frames from an earlier run can't linger.
- **Size report:** it prints the total size of each width set. It warns, but does not fail, if the 1600 set is over 8 MB.
- **Failure:** it exits non-zero if `ffmpeg`, `ffprobe` or `cwebp` is missing, or if the clip doesn't exist.
- **Budget:** expect about 35–55 KB per frame, so about 4–6.5 MB at 1600 and 2–3 MB at 960.

### Placeholder

`site/scripts/placeholder-clip.sh <still>` uses ffmpeg's `zoompan` filter to turn the reference still into a 6-second, 1600×900 clip with a slow push-in and pan. It writes the clip to the scratch directory, and `frames.sh` then processes it.

- **Why:** the placeholder frames are committed so the whole page works before the real clip exists.
- **Swapping in the real clip later:** re-run `frames.sh`. No code changes.
- **The still:** it is not committed under `site/public` except as the frames generated from it.

## 3. Player

There are two modules, both bundled by Astro from a `<script>` in `Hero.astro`. No runtime dependencies are added.

### `site/src/scripts/hero-math.ts`: pure functions with no DOM access

| Function | Contract |
| --- | --- |
| `progress(scrollY, sectionTop, sectionHeight, stageHeight): number` | `(scrollY − sectionTop) / (sectionHeight − stageHeight)`, clamped to 0..1. Returns 0 when the denominator is ≤ 0. |
| `viewportProgress(rectTop, rectHeight, viewportHeight): number` | Mobile. 0 when the box's top meets the viewport bottom, and 1 when its bottom meets the viewport top. Clamped. |
| `frameIndex(p, count, end = 0.85): number` | `round(min(p / end, 1) × (count − 1))`. Returns 0 when `count ≤ 1`. |
| `loadOrder(count): number[]` | Every index in `0..count−1` exactly once, in passes with strides 16, 8, 4, 2, 1. Each pass skips indices already listed. Index 0 comes first and `count − 1` is in the first pass. |
| `nearestLoaded(i, loaded: ArrayLike<unknown>): number` | The closest index whose slot is filled, preferring the lower index on ties. Returns −1 when nothing is loaded. |
| `coverRect(srcW, srcH, dstW, dstH, focusX = 0.6)` | The source crop `{sx, sy, sw, sh}` that covers the destination at its aspect ratio. Any horizontal excess is cropped around `focusX`, and vertical excess around the centre. |

### `site/src/scripts/hero-frames.ts`: the DOM wrapper

1. **Exit early** if reduced motion is on, if the elements are missing, or if `navigator.connection` reports `saveData` or an `effectiveType` in `slow-2g/2g/3g`. In each case the poster stays.
2. **Fetch `/hero/frames.json`.** On failure, exit and keep the poster.
3. **Pick the width:** 1600 if `canvas.clientWidth × min(devicePixelRatio, 2) > 1100`, otherwise 960.
4. **Size the backing store** to the canvas's CSS size × `min(DPR, 2)`. A `ResizeObserver` resizes it and marks the canvas for a redraw.
5. **Load the frames:**
   - load starts once an `IntersectionObserver` reports the hero within one viewport height (`rootMargin: "100% 0px"`);
   - frames load in `loadOrder(count)`, about 6 at a time;
   - each frame is an `Image`, then `img.decode()`, then `createImageBitmap(img)`, stored in `bitmaps[i]`;
   - a frame that fails is left empty;
   - if frame 0 fails, the player exits and the poster stays.
6. **Draw:**
   - `scroll` and `resize` events only mark the canvas for a redraw (passive listeners), and a single `requestAnimationFrame` callback does the work;
   - it computes `p` (with `progress` at `md` and up, or `viewportProgress` below), then `i = frameIndex(p, count)`, then `j = nearestLoaded(i, bitmaps)`;
   - it draws frame `j` with `coverRect` (frames are decoded `<img>` elements, not ImageBitmaps, so the browser can evict their pixels);
   - it skips the draw when `j` and the canvas size haven't changed.
7. **Hand-over from the poster:** after the first draw, it adds `.is-live` to the stage, and the CSS hides the poster.
8. **No console output** in production.

### Install pill copy button

- **Copy:** `navigator.clipboard.writeText(command)`. The label changes to "Copied" for 1.5 seconds.
- **Fallback:** if `navigator.clipboard` is missing, the button stays hidden; it is shown only once the script confirms support. The command keeps `select-all`.

### Accessibility

- **Media:** the canvas and poster have `aria-hidden="true"` and an empty `alt`.
- **Order:** the copy comes before the media in the source, so heading and focus order are unchanged.
- **Keyboard:** pinning uses only CSS `sticky`, so keyboard scrolling is unaffected.
- **Contrast:** the copy sits on solid `#352F2A` (the mask is transparent there), so the contrast is the same as today.

## 4. Testing and verification

### Unit tests

- **Setup:** add `vitest` as a dev dependency of the site, and an `npm test` script (`vitest run`).
- **Location:** `site/src/scripts/hero-math.test.ts`.
- **Cases:**
  - `progress` and `viewportProgress` at the boundaries, beyond them, and with a zero or negative denominator;
  - `frameIndex` at p = 0, 0.85 and 1, with p > 1, and with `count` = 1 and 2;
  - `loadOrder` for counts 1, 2, 17 and 120: a permutation of `0..n−1`, starting with 0, with `n − 1` in the first pass;
  - `nearestLoaded` with an empty list, with exactly the requested frame loaded, and with ties;
  - `coverRect` for a source wider than the destination, a taller one, an equal aspect ratio, and `focusX` at 0 and 1 (the crop stays inside the source).

### Script check

- Run `placeholder-clip.sh` and then `frames.sh` on the reference still.
- **Expect:**
  - 120 frames at each width;
  - the poster;
  - a valid `frames.json`;
  - a size report under the 8 MB warning.

### Build

`npm run build` succeeds, and `dist/hero/` contains the frames.

### Browser check

Use Playwright against `astro preview`.

- **Desktop, 1440×900:**
  - screenshots at p ≈ 0, 0.5, 0.9 and 1, showing different frames, the hold on the last frame, then the hero scrolling out normally with no seam at its bottom edge;
  - no console errors;
  - "View on GitHub" can be clicked while pinned.
- **Mobile, 390×844:** stacked, with no pinning and the footage above the copy.
- **Reduced motion emulated:** the poster only, and no section height beyond the stage.
- **JS disabled:** the poster shows and there is no extra height.

## Out of scope

- Producing the AI clip, which is the user's step.
- Changes to the copy.
- The nav and header.
- Other sections, except for moving the metrics strip.
- A CI job for the site's tests.
- Deploying.
