import { describe, expect, it } from "vitest";
import { tokenField } from "./token-field";

describe("tokenField", () => {
  const a = tokenField(10, 190, 1618);

  it("is the same for the same seed, and different for another", () => {
    expect(tokenField(10, 190, 1618)).toEqual(a);
    expect(tokenField(10, 190, 7)).not.toEqual(a);
  });

  it("has the asked-for shape", () => {
    expect(a).toHaveLength(10);
    expect(a[0]).toHaveLength(190);
  });

  it("thins out from left to right", () => {
    const filled = (from: number, to: number) =>
      a.flatMap((row) => row.slice(from, to)).filter((cell) => cell.ch !== " ").length;
    expect(filled(0, 40)).toBeGreaterThan(filled(150, 190) * 2);
  });

  it("uses only the token characters", () => {
    expect(a.flat().every((cell) => cell.ch === " " || "φλΣ{}→01/·∂".includes(cell.ch))).toBe(true);
  });
});
