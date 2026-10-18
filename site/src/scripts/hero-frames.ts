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
