import { describe, expect, it } from "vitest";
import { lineDelays } from "./run-reveal";

describe("lineDelays", () => {
  it("spaces the lines one step apart, starting at once", () => {
    expect(lineDelays(3, 450)).toEqual([0, 450, 900]);
  });

  it("is empty with no lines", () => {
    expect(lineDelays(0, 450)).toEqual([]);
    expect(lineDelays(-1, 450)).toEqual([]);
  });
});
