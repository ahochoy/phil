# Site Hero Busts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the site's centred hero with a two-column hero. The copy sits on the left. On the right, a pinned canvas plays a WebP frame sequence of orbiting marble busts as the visitor scrolls. The hero's lower edge fades into the cream page.

**Architecture:**
- **Layout:** a tall `.hero-scroll` section wraps a sticky `.hero-stage`, and plain CSS does the pinning.
- **Player:** a small TypeScript module maps scroll progress to a frame index and draws preloaded `ImageBitmap`s onto a canvas.
- **Math:** all the arithmetic lives in a pure module (`hero-math.ts`) with vitest unit tests.
- **Frames:** bash scripts (ffmpeg plus cwebp) turn a video clip into the frames. A placeholder clip, made from the reference still, ships first.

**Tech Stack:** Astro 7 (static output), Tailwind 4 via `@tailwindcss/vite`, TypeScript (Astro strict tsconfig), vitest (new dev dependency), ffmpeg 9 + ffprobe + cwebp (Homebrew), Playwright MCP for the browser check.

**Spec:** `docs/superpowers/specs/2026-10-18-site-hero-busts-design.md`

## Global Constraints

**Scope**
- All site work happens under `site/`. Run npm commands from `/Users/hochoy/Code/phil/site`.
- No runtime dependencies. New dev dependencies: `vitest`, plus `@astrojs/check` and `typescript` if `astro check` asks for them.

**Layout values**
- Scroll budget: `250vh` on `md` and up, applied only under `html.js-scrub`.
- Composition B:
  - the media covers the right `68%` of the stage;
  - desktop mask: `linear-gradient(to right, transparent 0%, #000 38%)`;
  - mobile mask: `linear-gradient(to bottom, #000 70%, transparent)`.
- The orbit ends at p = `0.85`, and the fade runs from p = `0.85` to `1`.
- The fade's height is `calc(15% + 85% * var(--fade, 0))`.
- Header height is `4.5rem` (`h-18`), which is the stage's sticky `top`.

**Colours**
- Hero `#352F2A`, page cream `#FAF8F5`. The fade is a cream alpha ramp with eased stops.

**Copy**
- Order: badge, h1, [View on GitHub] [How it started ↓], paragraph, install pill. The wording is unchanged from the current hero.

**Frames**
- Directory layout: `site/public/hero/frames/{960,1600}/NNNN.webp` (1-based, 4 digits), `site/public/hero/poster.webp`, and `site/public/hero/frames.json` = `{"count": N, "widths": [960, 1600], "aspect": W/H, "pad": 4}`.
- Default count `120`. Quality `q 72` at 1600 and `q 70` at 960. Warn, but don't fail, when the 1600 set is over 8 MB.
- The reference still is `/Users/hochoy/Downloads/media-1792346042653.png`. Never commit it, or any source clip. Only the generated frames are committed.

**Player behaviour**
- In production the player logs nothing to the console. Every failure leaves the poster visible.
- The canvas and poster carry `aria-hidden="true"`.

**Repo rules**
- Edit files only with the Edit and Write tools. No `sed`, heredocs, `printf >` or python to write files.
- Commit by writing the message with Write to `/Users/hochoy/Code/phil/.git/phil-commit-msg.txt`, then `git commit -F .git/phil-commit-msg.txt` from the repo root.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Stage only the files the task names.

## Review Focus

1. **Resizing across 768px mid-scroll** (desktop to mobile and back). The player switches progress mode and redraws without a blank canvas, and the section height follows the media query. *Pinned in Task 6, step 5.*
2. **Short laptop viewport (1280×600).** All the copy, including the install pill, stays inside the pinned stage and both CTAs can be clicked. *Pinned in Task 4 (`min-height`), checked in Task 6, step 6.*
3. **Jumping by anchor or the End key** (e.g. clicking "How it started ↓", or pressing End while frames are still loading). The canvas shows the nearest loaded frame, never a blank or stale wrong-end frame, and the fade ends fully cream. *Pinned in Task 6, step 7.*
4. **Missing or broken `frames.json`** (frames not generated, or a 404). The poster stays and the page stays usable, with no uncaught exception. *Pinned in Task 6, step 8.*
5. **Frames still loading on a slow network.** Scrubbing shows the nearest loaded frame. *Pinned by the `nearestLoaded` unit tests in Task 1, and checked in Task 6, step 7 under throttling.*

---

## File Structure

| File | Responsibility |
| --- | --- |
| `site/src/scripts/hero-math.ts` (create) | Pure math: progress, frame index, fade easing, load order, nearest loaded frame, cover-crop. |
| `site/src/scripts/hero-math.test.ts` (create) | vitest unit tests for the above. |
| `site/src/scripts/hero-frames.ts` (create) | DOM player: manifest fetch, loading, canvas sizing, rAF drawing, fade variable. |
| `site/src/scripts/copy-command.ts` (create) | Copy-to-clipboard buttons (`[data-copy]`). |
| `site/scripts/placeholder-clip.sh` (create) | Still image → 6 s 1600×900 push-in clip (ffmpeg `zoompan`). |
| `site/scripts/frames.sh` (create) | Clip → WebP frames at 960/1600, poster, manifest, size report. |
| `site/public/hero/**` (generated, committed) | Placeholder frames, poster, manifest. |
| `site/src/components/HeroMetrics.astro` (create) | The 4-metric strip, moved out of the hero, on cream. |
| `site/src/components/Hero.astro` (rewrite) | Two-column pinned hero markup and styles, plus the script that boots the player and copy button. |
| `site/src/layouts/Layout.astro` (modify `<head>`) | Inline script that adds `js-scrub` to `<html>` before first paint. |
| `site/src/pages/index.astro` (modify) | Render `<HeroMetrics />` after `<Hero />`. |
| `site/package.json` (modify) | `vitest` dev dependency and the `test` script. |
| `site/README.md` (modify) | "Hero frames" section: how to regenerate the frames from a clip. |

---

### Task 1: Pure hero math with unit tests

**Files:**
- Modify: `site/package.json` (dev dependency and script)
- Create: `site/src/scripts/hero-math.ts`
- Test: `site/src/scripts/hero-math.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces (exact exports of `site/src/scripts/hero-math.ts`):
  - `clamp01(x: number): number`
  - `progress(scrollY: number, sectionTop: number, sectionHeight: number, stageHeight: number): number`
  - `viewportProgress(rectTop: number, rectHeight: number, viewportHeight: number): number`
  - `frameIndex(p: number, count: number, end?: number): number` (default `end = 0.85`)
  - `fadeOpacity(p: number, start?: number): number` (default `start = 0.85`)
  - `loadOrder(count: number): number[]`
  - `nearestLoaded(i: number, loaded: ArrayLike<unknown>): number`
  - `interface Crop { sx: number; sy: number; sw: number; sh: number }`
  - `coverRect(srcW: number, srcH: number, dstW: number, dstH: number, focusX?: number): Crop` (default `focusX = 0.6`)

- [ ] **Step 1: Add vitest**

Run (in `site/`): `npm install --save-dev vitest`

Then use Edit on `site/package.json` to add the test script to `"scripts"`:

```json
    "deploy": "astro build && npx wrangler deploy",
    "test": "vitest run"
```

(Replace the existing `"deploy": …` line with these two lines. Mind the comma.)

- [ ] **Step 2: Write the failing tests**

Create `site/src/scripts/hero-math.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  clamp01,
  coverRect,
  fadeOpacity,
  frameIndex,
  loadOrder,
  nearestLoaded,
  progress,
  viewportProgress,
} from "./hero-math";

describe("clamp01", () => {
  it("keeps values inside 0..1", () => {
    expect(clamp01(-0.5)).toBe(0);
    expect(clamp01(0.25)).toBe(0.25);
    expect(clamp01(3)).toBe(1);
  });

  it("treats NaN as 0", () => {
    expect(clamp01(Number.NaN)).toBe(0);
  });
});

describe("progress", () => {
  // Section 2500px tall, stage 800px: 1700px of pinned scrolling.
  it("is 0 before the section reaches the stage top", () => {
    expect(progress(0, 100, 2500, 800)).toBe(0);
  });

  it("runs linearly across the pinned distance", () => {
    expect(progress(100 + 850, 100, 2500, 800)).toBeCloseTo(0.5);
  });

  it("is 1 at and after the end of the pinned distance", () => {
    expect(progress(100 + 1700, 100, 2500, 800)).toBe(1);
    expect(progress(99999, 100, 2500, 800)).toBe(1);
  });

  it("is 0 when the section is no taller than the stage", () => {
    expect(progress(500, 0, 800, 800)).toBe(0);
    expect(progress(500, 0, 600, 800)).toBe(0);
  });
});

describe("viewportProgress", () => {
  it("is 0 when the box top meets the viewport bottom", () => {
    expect(viewportProgress(900, 500, 900)).toBe(0);
  });

  it("is 1 when the box bottom meets the viewport top", () => {
    expect(viewportProgress(-500, 500, 900)).toBe(1);
  });

  it("is halfway when the box is centred in its travel", () => {
    expect(viewportProgress(200, 500, 900)).toBeCloseTo(0.5);
  });

  it("clamps outside the travel and survives a zero-size box and viewport", () => {
    expect(viewportProgress(5000, 500, 900)).toBe(0);
    expect(viewportProgress(-5000, 500, 900)).toBe(1);
    expect(viewportProgress(0, 0, 0)).toBe(0);
  });
});

describe("frameIndex", () => {
  it("starts on the first frame", () => {
    expect(frameIndex(0, 120)).toBe(0);
  });

  it("reaches the last frame at p = 0.85 and holds it", () => {
    expect(frameIndex(0.85, 120)).toBe(119);
    expect(frameIndex(1, 120)).toBe(119);
    expect(frameIndex(1.7, 120)).toBe(119);
  });

  it("is midway at half the orbit", () => {
    expect(frameIndex(0.425, 121)).toBe(60);
  });

  it("never goes negative", () => {
    expect(frameIndex(-1, 120)).toBe(0);
  });

  it("handles one and two frames", () => {
    expect(frameIndex(0.5, 1)).toBe(0);
    expect(frameIndex(0, 2)).toBe(0);
    expect(frameIndex(1, 2)).toBe(1);
  });

  it("can use the whole range", () => {
    expect(frameIndex(0.5, 101, 1)).toBe(50);
  });
});

describe("fadeOpacity", () => {
  it("is 0 up to the start of the outro", () => {
    expect(fadeOpacity(0)).toBe(0);
    expect(fadeOpacity(0.85)).toBe(0);
  });

  it("is 1 at the end and beyond", () => {
    expect(fadeOpacity(1)).toBe(1);
    expect(fadeOpacity(1.4)).toBe(1);
  });

  it("is eased: half at the midpoint, never decreasing", () => {
    expect(fadeOpacity(0.925)).toBeCloseTo(0.5);
    let previous = -1;
    for (let p = 0.8; p <= 1.0001; p += 0.01) {
      const value = fadeOpacity(p);
      expect(value).toBeGreaterThanOrEqual(previous);
      previous = value;
    }
  });
});

describe("loadOrder", () => {
  const isPermutation = (order: number[], n: number) =>
    order.length === n && [...order].sort((a, b) => a - b).every((v, k) => v === k);

  it.each([1, 2, 17, 120])("lists every frame exactly once for %i frames", (n) => {
    expect(isPermutation(loadOrder(n), n)).toBe(true);
  });

  it("starts with the first frame", () => {
    expect(loadOrder(120)[0]).toBe(0);
  });

  it("loads the last frame in the first (coarsest) pass", () => {
    const order = loadOrder(120);
    const firstPass = Math.ceil(120 / 16) + 1; // 0, 16, …, 112 and then 119
    expect(order.slice(0, firstPass)).toContain(119);
  });

  it("coarse frames come before fine ones", () => {
    const order = loadOrder(120);
    expect(order.indexOf(64)).toBeLessThan(order.indexOf(8));
    expect(order.indexOf(8)).toBeLessThan(order.indexOf(4));
    expect(order.indexOf(4)).toBeLessThan(order.indexOf(1));
  });

  it("is empty for no frames", () => {
    expect(loadOrder(0)).toEqual([]);
  });
});

describe("nearestLoaded", () => {
  it("is -1 when nothing is loaded", () => {
    expect(nearestLoaded(5, [])).toBe(-1);
    expect(nearestLoaded(5, new Array(10))).toBe(-1);
  });

  it("returns the frame itself when it is loaded", () => {
    const loaded = new Array(10);
    loaded[5] = "x";
    expect(nearestLoaded(5, loaded)).toBe(5);
  });

  it("finds the closest loaded frame", () => {
    const loaded = new Array(10);
    loaded[0] = "x";
    loaded[8] = "x";
    expect(nearestLoaded(6, loaded)).toBe(8);
    expect(nearestLoaded(3, loaded)).toBe(0);
  });

  it("prefers the lower frame on a tie", () => {
    const loaded = new Array(10);
    loaded[2] = "x";
    loaded[6] = "x";
    expect(nearestLoaded(4, loaded)).toBe(2);
  });

  it("clamps an out-of-range index", () => {
    const loaded = new Array(10);
    loaded[9] = "x";
    expect(nearestLoaded(50, loaded)).toBe(9);
    expect(nearestLoaded(-3, loaded)).toBe(9);
  });
});

describe("coverRect", () => {
  const inside = (c: { sx: number; sy: number; sw: number; sh: number }, w: number, h: number) =>
    c.sx >= 0 && c.sy >= 0 && c.sx + c.sw <= w + 1e-9 && c.sy + c.sh <= h + 1e-9;

  it("crops the sides of a wider source around focusX", () => {
    const c = coverRect(1600, 900, 800, 900); // destination is narrower
    expect(c.sh).toBe(900);
    expect(c.sw).toBe(800);
    expect(c.sx).toBeCloseTo(0.6 * 1600 - 400);
    expect(c.sy).toBe(0);
  });

  it("crops the top and bottom of a taller source around the centre", () => {
    const c = coverRect(1000, 1000, 1600, 900);
    expect(c.sw).toBe(1000);
    expect(c.sh).toBeCloseTo(562.5);
    expect(c.sx).toBe(0);
    expect(c.sy).toBeCloseTo((1000 - 562.5) / 2);
  });

  it("uses the whole source when the aspects match", () => {
    const c = coverRect(1600, 900, 800, 450);
    expect(c.sx).toBeCloseTo(0);
    expect(c.sy).toBeCloseTo(0);
    expect(c.sw).toBeCloseTo(1600);
    expect(c.sh).toBeCloseTo(900);
  });

  it("keeps the crop inside the source at both extremes of focusX", () => {
    expect(inside(coverRect(1600, 900, 300, 900, 0), 1600, 900)).toBe(true);
    expect(inside(coverRect(1600, 900, 300, 900, 1), 1600, 900)).toBe(true);
    expect(coverRect(1600, 900, 300, 900, 0).sx).toBe(0);
    expect(coverRect(1600, 900, 300, 900, 1).sx).toBeCloseTo(1600 - 300);
  });

  it("returns the whole source for a zero-size destination", () => {
    expect(coverRect(1600, 900, 0, 0)).toEqual({ sx: 0, sy: 0, sw: 1600, sh: 900 });
  });
});
```

- [ ] **Step 3: Run the tests to check they fail**

Run: `npm test`
Expected: FAIL with `Failed to resolve import "./hero-math"`.

- [ ] **Step 4: Implement**

Create `site/src/scripts/hero-math.ts`:

```ts
// Pure arithmetic for the scroll-scrubbed hero. No DOM access, so it is unit-tested.

export interface Crop {
  sx: number;
  sy: number;
  sw: number;
  sh: number;
}

export function clamp01(x: number): number {
  if (Number.isNaN(x)) return 0;
  return Math.min(1, Math.max(0, x));
}

/** How far through the pinned distance the page has scrolled, 0..1. */
export function progress(
  scrollY: number,
  sectionTop: number,
  sectionHeight: number,
  stageHeight: number,
): number {
  const distance = sectionHeight - stageHeight;
  if (distance <= 0) return 0;
  return clamp01((scrollY - sectionTop) / distance);
}

/** Mobile: 0 when the box enters at the bottom of the viewport, 1 when it leaves at the top. */
export function viewportProgress(rectTop: number, rectHeight: number, viewportHeight: number): number {
  const travel = viewportHeight + rectHeight;
  if (travel <= 0) return 0;
  return clamp01((viewportHeight - rectTop) / travel);
}

/** The frame for progress p. The orbit finishes at p = end and holds the last frame after it. */
export function frameIndex(p: number, count: number, end = 0.85): number {
  if (count <= 1) return 0;
  const t = Math.min(clamp01(p) / end, 1);
  return Math.round(t * (count - 1));
}

/** Smoothstep from p = start to p = 1. */
export function fadeOpacity(p: number, start = 0.85): number {
  const t = clamp01((p - start) / (1 - start));
  return t * t * (3 - 2 * t);
}

/** Every frame once: every 16th frame (plus the last), then every 8th, 4th, 2nd, then the rest. */
export function loadOrder(count: number): number[] {
  const order: number[] = [];
  const seen = new Set<number>();
  const add = (i: number) => {
    if (!seen.has(i)) {
      seen.add(i);
      order.push(i);
    }
  };
  for (const stride of [16, 8, 4, 2, 1]) {
    for (let i = 0; i < count; i += stride) add(i);
    if (stride === 16 && count > 0) add(count - 1);
  }
  return order;
}

/** The closest index whose slot is filled (lower wins a tie), or -1 if none is. */
export function nearestLoaded(i: number, loaded: ArrayLike<unknown>): number {
  const n = loaded.length;
  if (n === 0) return -1;
  const at = Math.min(n - 1, Math.max(0, i));
  for (let d = 0; d < n; d++) {
    if (at - d >= 0 && loaded[at - d] != null) return at - d;
    if (at + d < n && loaded[at + d] != null) return at + d;
  }
  return -1;
}

/** The source crop that covers the destination's aspect: sides cropped around focusX, top and bottom around the centre. */
export function coverRect(srcW: number, srcH: number, dstW: number, dstH: number, focusX = 0.6): Crop {
  if (srcW <= 0 || srcH <= 0 || dstW <= 0 || dstH <= 0) return { sx: 0, sy: 0, sw: srcW, sh: srcH };
  const dstAspect = dstW / dstH;
  if (srcW / srcH > dstAspect) {
    const sw = srcH * dstAspect;
    const sx = Math.min(srcW - sw, Math.max(0, focusX * srcW - sw / 2));
    return { sx, sy: 0, sw, sh: srcH };
  }
  const sh = srcW / dstAspect;
  return { sx: 0, sy: (srcH - sh) / 2, sw: srcW, sh };
}
```

- [ ] **Step 5: Run the tests to check they pass**

Run: `npm test`
Expected: PASS, every `hero-math` test green.

- [ ] **Step 6: Check the types**

Run: `npx astro check` (if Astro asks to install `@astrojs/check` and `typescript`, accept and add them as dev dependencies; the plan allows these two as tooling).
Expected: 0 errors.

- [ ] **Step 7: Commit**

Write `/Users/hochoy/Code/phil/.git/phil-commit-msg.txt`:

```
Site: hero scroll math, with vitest

Pure functions for the scroll-scrubbed hero: progress through the pinned
section, mobile viewport progress, frame index (the orbit ends at
p = 0.85), the smoothstep fade, a coarse-to-fine load order, the
nearest loaded frame, and a cover-crop centred to the right.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

Run from the repo root: `git add site/package.json site/package-lock.json site/src/scripts/hero-math.ts site/src/scripts/hero-math.test.ts && git commit -F .git/phil-commit-msg.txt`

---

### Task 2: Frame pipeline and placeholder frames

**Files:**
- Create: `site/scripts/placeholder-clip.sh`
- Create: `site/scripts/frames.sh`
- Generate: `site/public/hero/frames/960/0001.webp … 0120.webp`, `site/public/hero/frames/1600/0001.webp … 0120.webp`, `site/public/hero/poster.webp`, `site/public/hero/frames.json`
- Modify: `site/README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `/hero/frames.json` with the shape `{"count": number, "widths": [960, 1600], "aspect": number, "pad": 4}`, frame URLs `/hero/frames/<width>/<zero-padded 1-based index>.webp`, and the poster at `/hero/poster.webp` (1600 wide).

- [ ] **Step 1: Write `placeholder-clip.sh`**

Create `site/scripts/placeholder-clip.sh`:

```bash
#!/usr/bin/env bash
# Make a stand-in hero clip from a still: a slow push-in with a gentle pan.
# Usage: placeholder-clip.sh <still> [out.mp4]
set -euo pipefail

still=${1:?usage: placeholder-clip.sh <still> [out.mp4]}
out=${2:-hero-placeholder.mp4}

command -v ffmpeg >/dev/null || { echo "placeholder-clip.sh: ffmpeg not found" >&2; exit 1; }
[ -f "$still" ] || { echo "placeholder-clip.sh: no such file: $still" >&2; exit 1; }

# 150 frames at 25 fps = 6 s. Upscaling first keeps zoompan from jittering.
ffmpeg -y -v error -i "$still" \
  -vf "scale=4800:-2,zoompan=z='1+0.18*on/150':x='(iw-iw/zoom)*(0.35+0.3*on/150)':y='(ih-ih/zoom)/2':d=150:s=1600x900:fps=25" \
  -frames:v 150 -c:v libx264 -pix_fmt yuv420p "$out"

echo "placeholder-clip.sh: wrote $out"
```

- [ ] **Step 2: Write `frames.sh`**

Create `site/scripts/frames.sh`:

```bash
#!/usr/bin/env bash
# Turn a hero clip into the WebP frame sequence the hero player reads.
# Usage: frames.sh <clip> [count=120]
set -euo pipefail

clip=${1:?usage: frames.sh <clip> [count=120]}
count=${2:-120}
widths=(960 1600)
pad=4
warn_kb=8192

for tool in ffmpeg ffprobe cwebp; do
  command -v "$tool" >/dev/null || { echo "frames.sh: $tool not found" >&2; exit 1; }
done
[ -f "$clip" ] || { echo "frames.sh: no such clip: $clip" >&2; exit 1; }
[[ "$count" =~ ^[1-9][0-9]*$ ]] || { echo "frames.sh: count must be a positive integer" >&2; exit 1; }

site=$(cd "$(dirname "$0")/.." && pwd)
out="$site/public/hero"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Decode every frame once, then pick `count` of them evenly by index.
ffmpeg -v error -i "$clip" "$tmp/all-%06d.png"
total=$(find "$tmp" -name 'all-*.png' | wc -l | tr -d ' ')
[ "$total" -ge "$count" ] || { echo "frames.sh: clip has $total frames, fewer than $count" >&2; exit 1; }

read -r w h < <(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0:s=' ' "$clip")
aspect=$(awk -v w="$w" -v h="$h" 'BEGIN { printf "%.6f", w / h }')

rm -rf "$out/frames"
for width in "${widths[@]}"; do mkdir -p "$out/frames/$width"; done

for ((k = 0; k < count; k++)); do
  if [ "$count" -eq 1 ]; then src_index=1; else src_index=$((1 + k * (total - 1) / (count - 1))); fi
  src=$(printf "%s/all-%06d.png" "$tmp" "$src_index")
  name=$(printf "%0${pad}d.webp" $((k + 1)))
  cwebp -quiet -q 70 -resize 960 0 "$src" -o "$out/frames/960/$name"
  cwebp -quiet -q 72 -resize 1600 0 "$src" -o "$out/frames/1600/$name"
done

cp "$out/frames/1600/$(printf "%0${pad}d" 1).webp" "$out/poster.webp"
printf '{"count": %d, "widths": [960, 1600], "aspect": %s, "pad": %d}\n' "$count" "$aspect" "$pad" > "$out/frames.json"

for width in "${widths[@]}"; do
  kb=$(du -sk "$out/frames/$width" | cut -f1)
  echo "frames.sh: $width px: $count frames, $((kb / 1024)).$(((kb % 1024) * 10 / 1024)) MB"
  if [ "$width" -eq 1600 ] && [ "$kb" -gt "$warn_kb" ]; then
    echo "frames.sh: warning: the 1600 set is over 8 MB; consider fewer frames or a lower quality" >&2
  fi
done
echo "frames.sh: wrote $out/frames.json"
```

(The `printf … > frames.json` line writes a generated file at run time, which is fine. The repo rule against shell writes applies to editing source files, not to what a script produces.)

- [ ] **Step 3: Make both scripts executable and check their failure paths**

Run (in `site/`):

```bash
chmod +x scripts/placeholder-clip.sh scripts/frames.sh
scripts/frames.sh /nonexistent.mp4; echo "exit=$?"
scripts/frames.sh; echo "exit=$?"
```

Expected:
- the first run prints `frames.sh: no such clip: /nonexistent.mp4` and `exit=1`;
- the second prints a usage error and `exit=1`.

- [ ] **Step 4: Generate the placeholder frames**

Run (in `site/`; the scratch path is the session scratchpad):

```bash
scripts/placeholder-clip.sh /Users/hochoy/Downloads/media-1792346042653.png /private/tmp/claude-501/-Users-hochoy-Code-phil/2e3edf0e-3bbf-4dc2-a337-ff6b22479293/scratchpad/hero-placeholder.mp4
scripts/frames.sh /private/tmp/claude-501/-Users-hochoy-Code-phil/2e3edf0e-3bbf-4dc2-a337-ff6b22479293/scratchpad/hero-placeholder.mp4
ls public/hero/frames/960 | wc -l; ls public/hero/frames/1600 | wc -l; cat public/hero/frames.json; ls -la public/hero/poster.webp
```

Expected:
- `120` and `120`;
- the JSON is `{"count": 120, "widths": [960, 1600], "aspect": 1.777778, "pad": 4}`;
- the poster exists;
- the size report shows the 1600 set under 8 MB, with no warning.

Then view `public/hero/frames/1600/0001.webp` and `public/hero/frames/1600/0120.webp` with the Read tool. The second should be visibly zoomed further in than the first.

- [ ] **Step 5: Document regenerating the frames**

Edit `site/README.md`. After the `## Build` section's code block, insert:

````markdown
## Hero frames

The hero scrubs through a WebP frame sequence in `public/hero/`. To replace it with a new clip (16:9, 1080p or larger, 5–8 s):

```bash
scripts/frames.sh path/to/clip.mp4        # 120 frames by default
scripts/frames.sh path/to/clip.mp4 90     # or choose the count
```

The script rewrites `public/hero/frames/{960,1600}/`, `poster.webp` and `frames.json`, then prints the size of each set. No code changes are needed. Keep the source clip out of git.

`scripts/placeholder-clip.sh <still> <out.mp4>` makes a stand-in clip (a slow push-in) from a single image.
````

- [ ] **Step 6: Commit**

Write `/Users/hochoy/Code/phil/.git/phil-commit-msg.txt`:

```
Site: hero frame pipeline, with placeholder frames

frames.sh samples a clip evenly into 120 WebP frames at 960 and 1600
px, plus a poster and a frames.json manifest, and reports the size of
each set. placeholder-clip.sh makes a slow push-in clip from the
reference still, so the hero works before the real orbit footage
exists. The frames committed here are that placeholder.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

Run from the repo root: `git add site/scripts/placeholder-clip.sh site/scripts/frames.sh site/public/hero site/README.md && git commit -F .git/phil-commit-msg.txt`

---

### Task 3: Move the metrics strip out of the hero

**Files:**
- Create: `site/src/components/HeroMetrics.astro`
- Modify: `site/src/pages/index.astro`
- Modify: `site/src/components/Hero.astro` (remove only the `<!-- Metrics -->` block)

**Interfaces:**
- Consumes: `SITE_CONFIG.metrics` from `site/src/config.ts` (`{label, value, sublabel}[]`).
- Produces: `<HeroMetrics />`, which takes no props.

- [ ] **Step 1: Create the component**

Create `site/src/components/HeroMetrics.astro`:

```astro
---
import { SITE_CONFIG } from "../config";

const dots = ["bg-[#F59E0B]", "bg-[#10B981]", "bg-[#A855F7]", "bg-[#F59E0B]"];
---

<section class="max-w-5xl mx-auto px-6 pt-4 pb-10">
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 border-b border-[#E5DDD3] pb-8">
    {SITE_CONFIG.metrics.map((item, idx) => (
      <div class="flex flex-col items-center p-3 text-center">
        <div class="flex items-center gap-1.5">
          <span class={`w-1.5 h-1.5 rounded-full ${dots[idx % dots.length]}`}></span>
          <span class="font-serif text-2xl md:text-3xl font-medium text-[#26221E]">{item.value}</span>
        </div>
        <span class="text-xs font-semibold text-[#4A443D] mt-1 font-sans">{item.label}</span>
        <span class="text-[11px] text-[#7A7268] mt-0.5 max-w-[160px] leading-tight font-sans">{item.sublabel}</span>
      </div>
    ))}
  </div>
</section>
```

- [ ] **Step 2: Wire it into the page**

Edit `site/src/pages/index.astro`:
- after `import Hero from "../components/Hero.astro";`, add `import HeroMetrics from "../components/HeroMetrics.astro";`;
- after `<Hero />`, add the line `  <HeroMetrics />`.

- [ ] **Step 3: Remove the old strip from the hero**

Edit `site/src/components/Hero.astro`: delete everything from `<!-- Metrics -->` through the closing `</div>` of that grid. That's the block starting `<div class="grid grid-cols-2 md:grid-cols-4 gap-4 max-w-4xl mx-auto pt-6 border-t border-[#4D453E]">` and ending at its matching `</div>`, just before the hero's own two closing `</div>` and `</section>`.

- [ ] **Step 4: Build**

Run (in `site/`): `npm run build`
Expected: success. Then `grep -c "Faster routing" dist/client/index.html 2>/dev/null || grep -c "Faster routing" dist/index.html` prints `1`, so the metrics are rendered exactly once.

- [ ] **Step 5: Commit**

Write `/Users/hochoy/Code/phil/.git/phil-commit-msg.txt`:

```
Site: metrics strip moves below the hero

The four metrics move out of the hero into their own HeroMetrics
section, recoloured for the cream page. This clears the way for the
pinned two-column hero, whose fade will land on this strip.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

Run from the repo root: `git add site/src/components/HeroMetrics.astro site/src/pages/index.astro site/src/components/Hero.astro && git commit -F .git/phil-commit-msg.txt`

---

### Task 4: Two-column pinned hero (static: poster, layout, fade)

**Files:**
- Rewrite: `site/src/components/Hero.astro`
- Modify: `site/src/layouts/Layout.astro` (`<head>`)

**Interfaces:**
- Consumes: `/hero/poster.webp` (Task 2) and `SITE_CONFIG.links.github` (`site/src/config.ts`).
- Produces the DOM contract the player (Task 5) relies on:
  - `section.hero-scroll`, containing `div.hero-stage`;
  - inside `.hero-stage`:
    - `div.hero-media`, holding `img.hero-poster` and `canvas.hero-canvas`;
    - `div.hero-fade` (its height reads the CSS variable `--fade`);
    - `button[data-copy]`, carrying the `hidden` attribute and the command in `data-copy`;
  - the stage gets the class `.is-live` once the canvas has drawn, and that hides the poster;
  - `html.js-scrub` turns on the 250vh height.

- [ ] **Step 1: Add the `js-scrub` gate to the layout**

Edit `site/src/layouts/Layout.astro`. Directly after `<meta name="viewport" content="width=device-width, initial-scale=1.0" />`, insert:

```astro
    <script is:inline>
      // Only scripted pages with motion allowed get the tall, pinned hero (see Hero.astro).
      if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
        document.documentElement.classList.add("js-scrub");
      }
    </script>
```

- [ ] **Step 2: Rewrite the hero**

Replace the whole of `site/src/components/Hero.astro` with:

```astro
---
import { SITE_CONFIG } from "../config";

const installCommand = "uv tool install --editable git+https://github.com/ahochoy/phil.git";
---

<section class="hero-scroll text-[#FAF8F5]">
  <div class="hero-stage">
    <div class="hero-media" aria-hidden="true">
      <img class="hero-poster" src="/hero/poster.webp" alt="" width="1600" height="900" fetchpriority="high" decoding="async" />
      <canvas class="hero-canvas"></canvas>
    </div>

    <div class="hero-copy-wrap">
      <div class="hero-copy">
        <div class="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full border border-[#554C43] bg-[#433B34] text-[12px] font-mono font-medium text-[#D5CCC0] mb-7 shadow-xs">
          <span class="w-2 h-2 rounded-full bg-[#F59E0B] animate-pulse"></span>
          <span class="text-[#FAF8F5] font-serif italic text-sm">philia (φιλία)</span>
          <span class="text-[#8C8074]">·</span>
          <span class="tracking-wide uppercase text-[11px] text-[#D5CCC0]">a coding agent experiment</span>
        </div>

        <h1 class="font-serif text-4xl sm:text-5xl lg:text-[52px] font-normal tracking-tight text-[#FAF8F5] leading-[1.12] mb-7">
          I built a coding agent to find out where the <span class="italic font-serif text-[#F59E0B]">tokens go</span>.
        </h1>

        <div class="flex flex-col sm:flex-row gap-3 mb-7">
          <a
            href={SITE_CONFIG.links.github}
            target="_blank"
            rel="noopener noreferrer"
            class="w-full sm:w-auto inline-flex items-center justify-center gap-2.5 px-6 py-3.5 rounded-full bg-[#FAF8F5] text-[#26221E] text-sm font-medium hover:bg-[#EAE4DA] shadow-md hover:shadow-lg transition-all"
          >
            <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24" aria-hidden="true">
              <path fill-rule="evenodd" clip-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
            </svg>
            <span>View on GitHub</span>
          </a>

          <a
            href="#origin"
            class="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-full border border-[#554C43] bg-[#433B34] text-[#FAF8F5] text-sm font-medium hover:bg-[#4E443C] transition-all"
          >
            <span>How it started</span>
            <span class="text-[#B8ADA0]">↓</span>
          </a>
        </div>

        <p class="text-base md:text-lg text-[#D5CCC0] font-normal leading-relaxed mb-7">
          Phil is a terminal coding agent built on <span class="text-[#FAF8F5] font-medium">LangGraph</span> and <span class="text-[#FAF8F5] font-medium">DeepAgents</span>. It plans with you, works in its own git worktree, and stops at a budget you set. Every change I make to it runs against a live benchmark, and I write up what I learn.
        </p>

        <div class="flex items-center gap-3 pl-4 pr-1.5 py-1.5 rounded-full border border-[#4D453E] bg-[#2A2521] font-mono text-xs text-[#FAF8F5] shadow-inner">
          <span class="text-[#F59E0B] font-bold" aria-hidden="true">&gt;</span>
          <code class="select-all truncate min-w-0 flex-1">{installCommand}</code>
          <button
            type="button"
            hidden
            data-copy={installCommand}
            class="shrink-0 inline-flex items-center px-3 py-1.5 rounded-full border border-[#554C43] bg-[#433B34] text-[11px] text-[#D5CCC0] hover:text-[#FAF8F5] hover:border-[#6B6055] transition-colors"
          >Copy</button>
        </div>
      </div>
    </div>

    <div class="hero-fade" aria-hidden="true"></div>
  </div>
</section>

<style>
  .hero-scroll {
    position: relative;
    background: #352f2a;
  }

  .hero-stage {
    position: relative;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* Mobile: footage on top at 16:10, fading out at the bottom. */
  .hero-media {
    position: relative;
    order: -1;
    width: 100%;
    aspect-ratio: 16 / 10;
    -webkit-mask-image: linear-gradient(to bottom, #000 70%, transparent);
    mask-image: linear-gradient(to bottom, #000 70%, transparent);
  }

  .hero-poster,
  .hero-canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }

  .hero-poster {
    object-fit: cover;
    object-position: 60% center;
  }

  .hero-stage.is-live .hero-poster {
    visibility: hidden;
  }

  .hero-copy-wrap {
    position: relative;
    z-index: 10;
    width: 100%;
    max-width: 72rem;
    margin: 0 auto;
    padding: 2.5rem 1.5rem 6rem;
  }

  /* Eased cream ramp: composited over taupe and footage it lands on #FAF8F5 without banding. */
  .hero-fade {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 20;
    height: calc(15% + 85% * var(--fade, 0));
    pointer-events: none;
    background: linear-gradient(
      to bottom,
      rgb(250 248 245 / 0) 0%,
      rgb(250 248 245 / 0.013) 8.1%,
      rgb(250 248 245 / 0.049) 15.5%,
      rgb(250 248 245 / 0.104) 22.5%,
      rgb(250 248 245 / 0.175) 29%,
      rgb(250 248 245 / 0.259) 35.3%,
      rgb(250 248 245 / 0.352) 41.2%,
      rgb(250 248 245 / 0.45) 47.1%,
      rgb(250 248 245 / 0.55) 52.9%,
      rgb(250 248 245 / 0.648) 58.8%,
      rgb(250 248 245 / 0.741) 64.7%,
      rgb(250 248 245 / 0.825) 71%,
      rgb(250 248 245 / 0.896) 77.5%,
      rgb(250 248 245 / 0.951) 84.5%,
      rgb(250 248 245 / 0.987) 91.9%,
      rgb(250 248 245 / 1) 100%
    );
  }

  @media (min-width: 768px) {
    :global(html.js-scrub) .hero-scroll {
      height: 250vh;
    }

    .hero-stage {
      position: sticky;
      top: 4.5rem;
      height: calc(100svh - 4.5rem);
      min-height: 600px;
      justify-content: center;
    }

    /* Composition B: footage bleeds across the right 68% and dissolves leftward behind the copy. */
    .hero-media {
      position: absolute;
      top: 0;
      right: 0;
      bottom: 0;
      width: 68%;
      aspect-ratio: auto;
      -webkit-mask-image: linear-gradient(to right, transparent 0%, #000 38%);
      mask-image: linear-gradient(to right, transparent 0%, #000 38%);
    }

    .hero-copy-wrap {
      padding: 0 1.5rem;
    }

    .hero-copy {
      width: 46%;
    }
  }
</style>
```

- [ ] **Step 3: Build and check the static result**

Run (in `site/`): `npm run build && npm run preview`. Leave preview running in the background and note its URL (normally `http://localhost:4321`).

With Playwright MCP:
1. `browser_resize` to 1440×900.
2. `browser_navigate` to the preview URL.
3. Take a `browser_take_screenshot`.

Expect:
- the copy on the left;
- the poster bleeding in from the right, fading leftward into taupe;
- a cream fade at the bottom edge.

Then `browser_evaluate`:

```js
() => getComputedStyle(document.querySelector('.hero-scroll')).height
```

Expect `2250px` (250vh of 900).

- [ ] **Step 4: Commit**

Write `/Users/hochoy/Code/phil/.git/phil-commit-msg.txt`:

```
Site: two-column pinned hero with the busts poster

The hero becomes a 250vh section with a sticky stage. The copy sits on
the left: badge, headline, buttons, paragraph, then the install pill.
The busts footage bleeds in from the right 68% and dissolves leftward.
An eased cream fade at the bottom edge is ready to rise during the
outro (--fade). The extra height applies only under html.js-scrub, which
an inline head script adds when reduced motion is off, so no-JS and
reduced-motion visitors get a normal-height hero with the poster. Below
md the footage stacks above the copy, with no pinning.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

Run from the repo root: `git add site/src/components/Hero.astro site/src/layouts/Layout.astro && git commit -F .git/phil-commit-msg.txt`

---

### Task 5: Frame player and copy button

**Files:**
- Create: `site/src/scripts/hero-frames.ts`
- Create: `site/src/scripts/copy-command.ts`
- Modify: `site/src/components/Hero.astro` (add the boot `<script>` before `<style>`)

**Interfaces:**
- Consumes:
  - from `hero-math.ts` (Task 1): `progress`, `viewportProgress`, `frameIndex`, `fadeOpacity`, `loadOrder`, `nearestLoaded`, `coverRect`;
  - the DOM contract from Task 4;
  - the manifest and URLs from Task 2.
- Produces:
  - `startHero(section: HTMLElement): void` from `hero-frames.ts`;
  - `setupCopyButtons(root: ParentNode): void` from `copy-command.ts`.

- [ ] **Step 1: Write the copy button module**

Create `site/src/scripts/copy-command.ts`:

```ts
// Reveal and wire every [data-copy] button. Without the Clipboard API the buttons stay hidden
// and the command text stays select-all.
export function setupCopyButtons(root: ParentNode): void {
  if (!navigator.clipboard) return;
  for (const button of root.querySelectorAll<HTMLButtonElement>("button[data-copy]")) {
    const label = button.textContent ?? "Copy";
    let timer: number | undefined;
    button.hidden = false;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy ?? "");
      } catch {
        return;
      }
      button.textContent = "Copied";
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        button.textContent = label;
      }, 1500);
    });
  }
}
```

- [ ] **Step 2: Write the player**

Create `site/src/scripts/hero-frames.ts`:

```ts
// Scroll-scrubbed frame player for the hero. Every failure path returns early and leaves the poster.
import {
  coverRect,
  fadeOpacity,
  frameIndex,
  loadOrder,
  nearestLoaded,
  progress,
  viewportProgress,
} from "./hero-math";

interface Manifest {
  count: number;
  widths: number[];
  aspect: number;
  pad: number;
}

const MANIFEST_URL = "/hero/frames.json";
const CONCURRENCY = 6;
const DESKTOP = "(min-width: 768px)";
const LARGE_FRAME_THRESHOLD = 1100;

type NetworkInformation = { saveData?: boolean; effectiveType?: string };

function slowConnection(): boolean {
  const connection = (navigator as Navigator & { connection?: NetworkInformation }).connection;
  if (!connection) return false;
  return connection.saveData === true || ["slow-2g", "2g", "3g"].includes(connection.effectiveType ?? "");
}

function isManifest(value: unknown): value is Manifest {
  const m = value as Manifest;
  return (
    typeof m === "object" && m !== null &&
    Number.isInteger(m.count) && m.count >= 1 &&
    Array.isArray(m.widths) && m.widths.length > 0 && m.widths.every((w) => Number.isInteger(w) && w > 0) &&
    Number.isInteger(m.pad) && m.pad >= 1
  );
}

async function loadBitmap(url: string): Promise<ImageBitmap> {
  const img = new Image();
  img.src = url;
  await img.decode();
  return createImageBitmap(img);
}

export function startHero(section: HTMLElement): void {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches || slowConnection()) return;
  const stage = section.querySelector<HTMLElement>(".hero-stage");
  const media = section.querySelector<HTMLElement>(".hero-media");
  const canvas = section.querySelector<HTMLCanvasElement>(".hero-canvas");
  const fade = section.querySelector<HTMLElement>(".hero-fade");
  if (!stage || !media || !canvas || !fade) return;

  // Start fetching once the hero is within a viewport of the screen (in practice: immediately).
  const observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        observer.disconnect();
        void run(section, stage, media, canvas, fade);
      }
    },
    { rootMargin: "100% 0px" },
  );
  observer.observe(section);
}

async function run(
  section: HTMLElement,
  stage: HTMLElement,
  media: HTMLElement,
  canvas: HTMLCanvasElement,
  fade: HTMLElement,
): Promise<void> {
  let manifest: Manifest;
  try {
    const response = await fetch(MANIFEST_URL);
    if (!response.ok) return;
    const body: unknown = await response.json();
    if (!isManifest(body)) return;
    manifest = body;
  } catch {
    return;
  }

  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width =
    canvas.clientWidth * dpr > LARGE_FRAME_THRESHOLD ? Math.max(...manifest.widths) : Math.min(...manifest.widths);
  const url = (i: number) => `/hero/frames/${width}/${String(i + 1).padStart(manifest.pad, "0")}.webp`;
  const bitmaps: (ImageBitmap | undefined)[] = new Array(manifest.count);
  const desktop = matchMedia(DESKTOP);

  try {
    bitmaps[0] = await loadBitmap(url(0));
  } catch {
    return;
  }

  let queued = false;
  let lastFrame = -1;
  let lastWidth = 0;
  let lastHeight = 0;

  const currentProgress = (): number => {
    if (desktop.matches) {
      const rect = section.getBoundingClientRect();
      const stageTop = parseFloat(getComputedStyle(stage).top) || 0;
      return progress(0, rect.top - stageTop, rect.height, stage.offsetHeight);
    }
    const rect = media.getBoundingClientRect();
    return viewportProgress(rect.top, rect.height, window.innerHeight);
  };

  const draw = () => {
    queued = false;
    const p = currentProgress();
    fade.style.setProperty("--fade", desktop.matches ? String(fadeOpacity(p)) : "0");
    const frame = nearestLoaded(frameIndex(p, manifest.count, desktop.matches ? 0.85 : 1), bitmaps);
    if (frame < 0) return;
    if (frame === lastFrame && canvas.width === lastWidth && canvas.height === lastHeight) return;
    const bitmap = bitmaps[frame]!;
    const crop = coverRect(bitmap.width, bitmap.height, canvas.width, canvas.height);
    ctx.drawImage(bitmap, crop.sx, crop.sy, crop.sw, crop.sh, 0, 0, canvas.width, canvas.height);
    lastFrame = frame;
    lastWidth = canvas.width;
    lastHeight = canvas.height;
    stage.classList.add("is-live");
  };

  const schedule = () => {
    if (!queued) {
      queued = true;
      requestAnimationFrame(draw);
    }
  };

  const resize = () => {
    const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
    const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (w !== canvas.width || h !== canvas.height) {
      canvas.width = w;
      canvas.height = h;
    }
    schedule();
  };

  new ResizeObserver(resize).observe(canvas);
  window.addEventListener("scroll", schedule, { passive: true });
  desktop.addEventListener("change", schedule);
  resize();

  // Coarse-to-fine: frame 0 is already in, the rest fill gaps, about six at a time.
  const order = loadOrder(manifest.count).filter((i) => i !== 0);
  let next = 0;
  const worker = async () => {
    while (next < order.length) {
      const i = order[next++];
      try {
        bitmaps[i] = await loadBitmap(url(i));
        schedule();
      } catch {
        // A missing frame is covered by its neighbours via nearestLoaded.
      }
    }
  };
  await Promise.all(Array.from({ length: CONCURRENCY }, worker));
}
```

- [ ] **Step 3: Boot both from the hero**

Edit `site/src/components/Hero.astro`. Directly before the `<style>` line, insert:

```astro
<script>
  import { startHero } from "../scripts/hero-frames";
  import { setupCopyButtons } from "../scripts/copy-command";

  const section = document.querySelector<HTMLElement>(".hero-scroll");
  if (section) {
    setupCopyButtons(section);
    startHero(section);
  }
</script>
```

- [ ] **Step 4: Type check, test, build**

Run (in `site/`): `npx astro check && npm test && npm run build`
Expected: 0 type errors, all tests pass, and the build succeeds.

- [ ] **Step 5: Quick check in the browser**

Restart `npm run preview` in the background.

With Playwright MCP:
1. `browser_resize` to 1440×900, then `browser_navigate` to the preview URL.
2. `browser_wait_for` 2 seconds.
3. `browser_evaluate`:

```js
() => ({
  live: document.querySelector('.hero-stage').classList.contains('is-live'),
  canvas: [document.querySelector('.hero-canvas').width, document.querySelector('.hero-canvas').height],
  copyShown: !document.querySelector('button[data-copy]').hidden,
})
```

Expect `live: true`, a canvas of about `[979, 828]` × the DPR (capped at 2), which is 68% of 1440 wide by 900 − 72 tall, and `copyShown: true`. `browser_console_messages` shows no errors.

- [ ] **Step 6: Commit**

Write `/Users/hochoy/Code/phil/.git/phil-commit-msg.txt`:

```
Site: hero frame player and install copy button

hero-frames.ts reads frames.json and picks 960 or 1600 px frames for
the canvas size. Frame 0 loads first. The rest load coarse-to-fine, six
at a time, decoded into ImageBitmaps. Scroll events only schedule one
rAF draw, which maps pinned progress (or viewport travel, below md) to a
frame, falls back to the nearest loaded one, and sets --fade for the
outro. Reduced motion, a slow connection, a missing manifest or a
broken first frame all leave the poster in place. The install pill's
Copy button appears only when the Clipboard API exists.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

Run from the repo root: `git add site/src/scripts/hero-frames.ts site/src/scripts/copy-command.ts site/src/components/Hero.astro && git commit -F .git/phil-commit-msg.txt`

---

### Task 6: Browser verification (spec section 4 and the Review Focus)

**Files:**
- Possibly modify: whatever a failed check points to (`Hero.astro`, `hero-frames.ts`), each fix in its own commit.
- Screenshots go to the session scratchpad, not the repo.

**Interfaces:**
- Consumes: the built site from Tasks 1–5, served by `npm run preview`.
- Produces: a pass/fail list for the PR test plan.

Use the Playwright MCP tools throughout. To scroll, use `browser_evaluate` with `() => window.scrollTo(0, Y)` and then `browser_wait_for` 0.5 s. Measure the pinned distance once with:

```js
() => { const s = document.querySelector('.hero-scroll'); const st = document.querySelector('.hero-stage');
        return { top: s.getBoundingClientRect().top + scrollY - 72, distance: s.offsetHeight - st.offsetHeight }; }
```

Then the scroll position for progress p is `top + p × distance`.

- [ ] **Step 1: Desktop scrub, 1440×900**

Screenshot at p = 0, 0.5, 0.9 and 1, plus one at `top + distance + 300`.

Expect:
- the frames visibly differ between 0 and 0.5;
- at 0.9 the fade covers about half the stage;
- at 1 the stage is cream;
- the final screenshot shows the metrics strip with no seam or colour step at the boundary.

`browser_console_messages`: no errors.

- [ ] **Step 2: The CTAs work while pinned**

At p = 0.5, `browser_click` on "View on GitHub" (a new tab opens, so close it with `browser_tabs`). Then `browser_click` "How it started ↓". Expect the page to scroll to `#origin`.

- [ ] **Step 3: Mobile, 390×844**

Expect:
- `browser_evaluate` `() => getComputedStyle(document.querySelector('.hero-stage')).position` returns `"relative"`;
- the screenshot shows the footage above the copy;
- scrolling 400px changes the drawn frame (compare `.hero-canvas` screenshots).

- [ ] **Step 4: Reduced motion and no JS**

1. `browser_emulate_media` with `reducedMotion: "reduce"`, then reload. Expect:
   - `document.documentElement.classList.contains('js-scrub') === false`;
   - `.hero-stage` does not have `is-live`;
   - the poster is visible;
   - the section height equals the stage height.
2. Reset the emulation.
3. For no JS, use `browser_run_code_unsafe` to open a new context with `javaScriptEnabled: false`, load the preview URL, and return the `.hero-scroll` and `.hero-stage` `offsetHeight`. Expect them equal and the poster `<img>` present.

- [ ] **Step 5: Review Focus 1, resizing across 768px**

At 1440×900 and p = 0.5:
1. `browser_resize` to 700×900 and screenshot. Expect a stacked layout with a frame drawn, not blank.
2. Resize back to 1440×900 and screenshot. Expect pinned again, with the frame matching the scroll position.

- [ ] **Step 6: Review Focus 2, short viewport, 1280×600**

At p = 0, screenshot. Expect:
- the badge through to the install pill all visible inside the stage (it has a 600px minimum height, so the stage may extend below the fold; the pill must not be clipped by `overflow: hidden`);
- `browser_click` "View on GitHub" succeeds.

If the pill is clipped, reduce the vertical spacing in `.hero-copy` (`mb-7` → `mb-5`), re-check, and commit as "Site: hero copy fits 600px-tall stages".

- [ ] **Step 7: Review Focus 3 and 5, jumping while frames load**

1. Throttle with `browser_run_code_unsafe`: `await page.route('**/hero/frames/**', async r => { await new Promise(f => setTimeout(f, 400)); await r.continue(); })`. Then reload.
2. Immediately `browser_press_key` End, and screenshot at once. Expect the canvas shows *some* frame (the poster or the nearest loaded frame), never blank.
3. Wait 10 s and screenshot again. Expect the fade fully cream at the bottom of the hero region.
4. `browser_press_key` Home. Expect the first frame and the fade back at rest.
5. Remove the route.

- [ ] **Step 8: Review Focus 4, missing manifest**

1. `browser_run_code_unsafe`: `await page.route('**/hero/frames.json', r => r.fulfill({ status: 404, body: '' }))`, then reload.
2. Expect:
   - the poster visible and `.hero-stage` without `is-live`;
   - scrolling still pins and then releases;
   - the console shows only the 404 network line, with no uncaught exception.
3. Remove the route.

- [ ] **Step 9: Record the results**

Write the pass/fail result of steps 1–8 into the PR test plan when the PR is opened. Stop the preview server.

---

## Self-review notes

- **Spec coverage:**
  - section 1 layout: Tasks 3 and 4;
  - fade: Task 4 CSS plus `--fade` in Task 5;
  - section 2 pipeline: Task 2;
  - section 3 math: Task 1;
  - player: Task 5;
  - copy button: Task 5;
  - accessibility: Task 4 (`aria-hidden`, source order);
  - section 4 tests: Task 1 (units), Task 2 step 4 (script), Task 5 step 4 (build), Task 6 (browser).
- **Spec deviation:** the spec says the canvas and poster have `aria-hidden`. The plan puts `aria-hidden` on their wrapper, `.hero-media`, which hides both, plus `alt=""` on the poster.
- **Spec deviation:** the spec sketches `startHero` exiting before fetching. The plan delays the fetch with an `IntersectionObserver`, as the spec's loading step requires.
