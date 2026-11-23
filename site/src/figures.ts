// Line figures for the five decisions (DeepDives.astro), drawn on an isometric grid: thin strokes,
// dotted edges for what is optional, cached or out of reach, and colour only where it means a path.

export type Point = [number, number];
export type Accent = "blue" | "yellow" | "red";
export interface Shape {
  kind: "poly" | "line" | "circle";
  points: Point[]; // projected; a circle's single point is its centre
  r?: number;
  fill?: boolean; // a solid face, in the page colour, hides what is behind it
  dotted?: boolean;
  faint?: boolean; // the rule colour instead of the muted one
  accent?: Accent;
}
type P3 = [number, number, number];

const COS30 = Math.cos(Math.PI / 6);

/** Project grid coordinates: x runs to the lower right, y to the lower left, z straight up. */
export function iso(x: number, y: number, z: number): Point {
  return [(x - y) * COS30, (x + y) * 0.5 - z];
}

const project = (points: P3[]): Point[] => points.map(([x, y, z]) => iso(x, y, z));
const poly = (points: P3[], style: Partial<Shape> = {}): Shape => ({ kind: "poly", points: project(points), ...style });
const line = (points: P3[], style: Partial<Shape> = {}): Shape => ({ kind: "line", points: project(points), ...style });

/** A solid box's three visible faces: its top, and the sides facing +x and +y. */
function box(x: number, y: number, z: number, w: number, d: number, h: number, style: Partial<Shape> = {}): Shape[] {
  const t = z + h;
  const s = { fill: true, ...style };
  return [
    poly([[x + w, y, z], [x + w, y + d, z], [x + w, y + d, t], [x + w, y, t]], s),
    poly([[x, y + d, z], [x + w, y + d, z], [x + w, y + d, t], [x, y + d, t]], s),
    poly([[x, y, t], [x + w, y, t], [x + w, y + d, t], [x, y + d, t]], s),
  ];
}

/** Horizontal hatching on a box's +x face, `gap` apart. */
function hatch(x: number, y: number, z: number, d: number, h: number, gap: number): Shape[] {
  const out: Shape[] = [];
  for (let k = z + gap; k < z + h - gap / 2; k += gap) out.push(line([[x, y, k], [x, y + d, k]], { faint: true }));
  return out;
}

/** A circle lying flat on the floor, as a polygon. */
function floorRing(cx: number, cy: number, r: number, style: Partial<Shape> = {}): Shape {
  const pts: P3[] = [];
  for (let a = 0; a < 32; a++) pts.push([cx + r * Math.cos((a / 32) * 2 * Math.PI), cy + r * Math.sin((a / 32) * 2 * Math.PI), 0]);
  return poly(pts, style);
}

/** 0.1 Routing: a stream of requests passes one narrow gate and leaves on three lanes. */
function routing(): Shape[] {
  const out: Shape[] = [];
  for (let i = 0; i < 6; i++) out.push(...box(i * 0.9, 1.2, 0, 0.22, 2.6, 2.2, { faint: i < 2 }));
  out.push(...box(6.2, 0, 0, 0.7, 0.9, 3.4));
  out.push(...box(6.2, 4.1, 0, 0.7, 0.9, 3.4));
  out.push(...box(6.2, 0, 3.4, 0.7, 5, 0.45));
  // One trunk out of the gate, then three lanes fanning out and running on in the stream's direction.
  const split: P3 = [8.6, 2.5, 0];
  out.push(line([[6.9, 2.5, 0], split]));
  const ends: [P3, Accent][] = [[[14, -0.5, 0], "blue"], [[14, 2.5, 0], "yellow"], [[14, 5.5, 0], "red"]];
  for (const [end] of ends) out.push(line([split, [10.6, end[1], 0], end]));
  for (const [end, accent] of ends) out.push({ kind: "circle", points: [iso(...end)], r: 0.32, accent });
  return out;
}

/** 0.2 Models: a small, dense high tier, a wide, plain low tier, and an optional lens. */
function models(): Shape[] {
  return [
    ...box(0, 0, 0, 2.2, 2.2, 5.2),
    ...hatch(2.2, 0, 0, 2.2, 5.2, 0.35),
    ...box(3.6, -5, 0, 5.4, 4.4, 1.7),
    ...hatch(9, -5, 0, 4.4, 1.7, 0.85),
    { kind: "circle", points: [iso(4.2, -6.2, 5)], r: 0.95, dotted: true },
    { kind: "circle", points: [iso(4.2, -6.2, 5)], r: 0.35, dotted: true, faint: true },
  ];
}

/** 0.3 Structure: fixed rails between steps, one rail looping back, one step checkpointed. */
function structure(): Shape[] {
  const c = 1.5;
  const cubes: [number, number][] = [[0.5, 0.5], [4.5, 0.5], [8.5, 0.5], [8.5, 4.5], [4.5, 4.5]];
  const h = c / 2; // rails run at half a cube's height, drawn last, in the gaps between cubes
  const rail = ([ax, ay]: [number, number], [bx, by]: [number, number], style: Partial<Shape> = {}): Shape =>
    ay === by
      ? line([[Math.min(ax, bx) + c, ay + h, h], [Math.max(ax, bx), ay + h, h]], style)
      : line([[ax + h, Math.min(ay, by) + c, h], [ax + h, Math.max(ay, by), h]], style);
  const out: Shape[] = [poly([[-0.5, -0.5, 0], [11, -0.5, 0], [11, 7, 0], [-0.5, 7, 0]], { dotted: true, faint: true })];
  for (const [x, y] of cubes) out.push(...box(x, y, 0, c, c, c));
  for (let i = 0; i < cubes.length - 1; i++) out.push(rail(cubes[i], cubes[i + 1]));
  out.push(rail(cubes[4], cubes[1], { dotted: true })); // the loop back
  const [nx, ny] = cubes[2];
  out.push(poly([[nx + 0.4, ny + 0.4, c], [nx + 1.1, ny + 0.4, c], [nx + 1.1, ny + 1.1, c], [nx + 0.4, ny + 1.1, c]]));
  return out;
}

/** 0.4 Context: a tall stack sent again and again, against a short one mostly read from cache. */
function context(): Shape[] {
  const out: Shape[] = [];
  for (let i = 0; i < 13; i++) out.push(...box(0, 0, i * 0.38, 4, 4, 0.12));
  for (let i = 0; i < 5; i++) out.push(...box(7.5, 1, i * 0.38, 4, 4, 0.12, { dotted: i < 4, faint: i < 4 }));
  out.push(line([[4.8, 2, 0], [6.8, 3, 0]], { faint: true }));
  return out;
}

/** 0.5 Security: the agent's open shell, a wall, and a key on the far side, unconnected. */
function security(): Shape[] {
  const out: Shape[] = [...box(0, 0, 0, 4, 4, 2.4)];
  out.push(poly([[0.5, 0.5, 2.4], [3.5, 0.5, 2.4], [3.5, 3.5, 2.4], [0.5, 3.5, 2.4]], { faint: true }));
  out.push(...box(7, -1.5, 0, 0.18, 7.5, 3.8, { dotted: true }));
  out.push(floorRing(10.6, 1.8, 0.9));
  out.push(line([[11.5, 1.8, 0], [14.8, 1.8, 0]]));
  out.push(line([[13.8, 1.8, 0], [13.8, 2.5, 0]]));
  out.push(line([[14.6, 1.8, 0], [14.6, 2.6, 0]]));
  return out;
}

export const FIGURES: Shape[][] = [routing(), models(), structure(), context(), security()];

/** A viewBox around every shape, with `margin` to spare on each side. */
export function viewBox(shapes: Shape[], margin = 0.6): string {
  let [minX, minY, maxX, maxY] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const s of shapes) {
    const r = s.kind === "circle" ? (s.r ?? 0) : 0;
    for (const [x, y] of s.points) {
      minX = Math.min(minX, x - r);
      minY = Math.min(minY, y - r);
      maxX = Math.max(maxX, x + r);
      maxY = Math.max(maxY, y + r);
    }
  }
  const f = (n: number) => Math.round(n * 100) / 100;
  return [f(minX - margin), f(minY - margin), f(maxX - minX + 2 * margin), f(maxY - minY + 2 * margin)].join(" ");
}

/** An SVG points attribute. */
export const pointsAttr = (points: Point[]) => points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
