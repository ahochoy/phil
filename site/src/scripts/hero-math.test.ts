import { describe, expect, it } from "vitest";
import {
  clamp01,
  coverRect,
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
