/** One character of the token field: a glyph and its tone ("" dim, "w" white, or a primary). */
export type Cell = { ch: string; tone: "" | "w" | "r" | "y" | "b" };

const CHARS = Array.from("φλΣ{}→01/·∂");

/** mulberry32: a small seeded PRNG, so the field is the same on every build. */
function random(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * A field of token glyphs, densest on the left and thinning to the right (a run that spends
 * fewer tokens). A few glyphs are white or in a primary colour.
 */
export function tokenField(rows: number, cols: number, seed: number): Cell[][] {
  const next = random(seed);
  return Array.from({ length: rows }, () =>
    Array.from({ length: cols }, (_, col): Cell => {
      if (next() > 0.7 - (0.55 * col) / cols) return { ch: " ", tone: "" };
      const ch = CHARS[Math.floor(next() * CHARS.length)];
      const roll = next();
      const tone = roll < 0.01 ? "r" : roll < 0.02 ? "y" : roll < 0.03 ? "b" : roll < 0.07 ? "w" : "";
      return { ch, tone };
    }),
  );
}
