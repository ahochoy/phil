import { describe, expect, it } from "vitest";
import { FIGURES, iso, viewBox } from "./figures";

describe("the decision figures", () => {
  it("project x to the lower right, y to the lower left and z straight up", () => {
    const [x1, y1] = iso(1, 0, 0);
    const [x2, y2] = iso(0, 1, 0);
    const [x3, y3] = iso(0, 0, 1);
    expect(x1).toBeGreaterThan(0);
    expect(y1).toBeGreaterThan(0);
    expect(x2).toBeLessThan(0);
    expect(y2).toBeGreaterThan(0);
    expect([x3, y3]).toEqual([0, -1]);
  });

  it("frame every shape with a margin", () => {
    const box = viewBox([{ kind: "line", points: [[0, 0], [10, 4]] }], 2).split(" ").map(Number);
    expect(box).toEqual([-2, -2, 14, 8]);
  });

  it("draw five figures, each with something to see", () => {
    expect(FIGURES.length).toBe(5);
    for (const shapes of FIGURES) expect(shapes.length).toBeGreaterThan(3);
  });

  it("colour only the routing figure, one dot per path", () => {
    const accents = FIGURES.map((shapes) => shapes.filter((s) => s.accent).map((s) => s.accent));
    expect(accents[0]).toEqual(["blue", "yellow", "red"]);
    for (const other of accents.slice(1)) expect(other).toEqual([]);
  });
});
