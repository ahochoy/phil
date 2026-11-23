/** When each of `count` lines appears, in ms after the run comes into view. */
export function lineDelays(count: number, stepMs: number): number[] {
  return Array.from({ length: Math.max(count, 0) }, (_, i) => i * stepMs);
}

const STEP_MS = 450;

/**
 * Plays the example run once, line by line, the first time it scrolls into view. Lines are
 * visible in the HTML; only here, with motion allowed, are they hidden until their turn, so the
 * run reads in full without JavaScript or with reduced motion.
 */
export function startRunReveal(root: HTMLElement): void {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  if (!("IntersectionObserver" in window)) return;
  const lines = Array.from(root.querySelectorAll<HTMLElement>("[data-run-line]"));
  for (const line of lines) line.classList.add("is-pending");
  const observer = new IntersectionObserver(
    (entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      lineDelays(lines.length, STEP_MS).forEach((delay, i) => {
        setTimeout(() => lines[i].classList.remove("is-pending"), delay);
      });
    },
    { threshold: 0.3 },
  );
  observer.observe(root);
}
