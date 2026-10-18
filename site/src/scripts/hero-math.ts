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
