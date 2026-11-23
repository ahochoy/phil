import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// The built page, as a visitor's browser receives it. Run `npm run build` first.
const built = fileURLToPath(new URL("../dist/client/index.html", import.meta.url));
if (!existsSync(built)) throw new Error("dist/client/index.html is missing: run npm run build first");
const page = readFileSync(built, "utf-8");
// Every built stylesheet, joined.
const cssDir = fileURLToPath(new URL("../dist/client/_astro/", import.meta.url));
const css = readdirSync(cssDir)
  .filter((name) => name.endsWith(".css"))
  .map((name) => readFileSync(join(cssDir, name), "utf-8"))
  .join("\n");

/** How many elements carry the `[data-…]` attribute `name`. */
function count(name: string): number {
  return page.match(new RegExp(`\\s${name}(?=[\\s=>])`, "g"))?.length ?? 0;
}

describe("the page", () => {
  it("says what Phil is, plainly", () => {
    expect(page).toMatch(/<h1[^>]*>\s*Phil\s*<\/h1>/);
    expect(page).toContain("a coding agent experiment");
    expect(page).toContain("uv tool install phil-agent");
    expect(page).toContain("pipx install phil-agent");
  });

  it("drops what was cut", () => {
    for (const gone of [/philia/i, /φιλία/, /devlog/i, /cal\.com/, /--editable/, /Schedule a chat/])
      expect(page).not.toMatch(gone);
  });

  it("links the nav and the about-the-dev footer", () => {
    for (const href of ['href="#how-it-works"', 'href="#decisions"', 'href="#install"'])
      expect(page).toContain(href);
    expect(page).toContain("About the dev");
    for (const url of ["andrewhochoy.com", "linkedin.com/in/andrewhochoy", 'github.com/ahochoy"', "docs/guide.md"])
      expect(page).toContain(url);
  });

  it("plays one full-path run, visible without JavaScript", () => {
    expect(page).toContain('id="run"');
    expect(count("data-run-line")).toBeGreaterThanOrEqual(10);
    const lineTags = page.match(/<[a-z]+\s[^>]*\bdata-run-line\b[^>]*>/g) ?? [];
    expect(lineTags.length).toBe(count("data-run-line"));
    for (const tag of lineTags) expect(tag).not.toMatch(/\bhidden\b|is-pending/);
    expect(page).toMatch(/illustrative/i);
    expect(page).not.toContain("data-tab=");
  });

  it("shows the three paths and what it's built with", () => {
    expect(page).toContain('id="how-it-works"');
    expect(count("data-path")).toBe(3);
    for (const p of ['data-path="answer"', 'data-path="quick"', 'data-path="full"']) expect(page).toContain(p);
    for (const t of ["Python", "LangGraph", "LangChain", "DeepAgents", "Textual", "Rich", "Typer", "SQLite"])
      expect(page).toContain(t);
  });

  it("has five decisions and no budget card", () => {
    expect(page).toContain('id="decisions"');
    expect(count("data-decision")).toBe(5);
    expect(page).not.toContain("04 · PROCESS");
  });

  it("claims no more about keys and caching than the guide does", () => {
    expect(page).not.toMatch(/can(?:'|&#39;|’)t read the OS key store/);
    expect(page).toContain("through keyring");
    expect(page).toMatch(/OS keychain directly/);
    expect(page).not.toContain("Repeated history is prompt-cached.");
    expect(page).toContain("on Anthropic models");
  });

  it("is dark, in the new type", () => {
    expect(css.toLowerCase()).toContain("#0c0c0b");
    expect(page).toMatch(/fonts\.googleapis\.com[^"]*Inter/);
    expect(page).not.toContain("Plus+Jakarta");
    expect(page).toMatch(/<body[^>]*class="[^"]*bg-phi-ink/);
  });

  it("draws the golden spiral, not the busts", () => {
    expect(page).toMatch(/<svg[^>]*data-spiral[^>]*aria-hidden="true"/);
    expect(count("data-square")).toBe(3);
    expect(page).toContain("data-spiral-path");
    expect(page).toContain("φ = 1.618");
    for (const gone of ["hero-canvas", "/hero/", "poster.webp", "js-scrub"]) expect(page).not.toContain(gone);
    expect(css).toMatch(/prefers-reduced-motion:\s*reduce/);
  });

  it("leaves no trace of the cream palette, and colours each path", () => {
    for (const old of ["#FAF8F5", "#F4EFEA", "#E5DDD3", "#26221E", "#352f2a"])
      expect(page.toLowerCase()).not.toContain(old.toLowerCase());
    expect(page).toMatch(/data-path="answer"[^>]*>[\s\S]{0,400}?text-phi-blue/);
    expect(page).toMatch(/data-path="quick"[^>]*>[\s\S]{0,400}?text-phi-yellow/);
    expect(page).toMatch(/data-path="full"[^>]*>[\s\S]{0,400}?text-phi-red/);
  });

  it("ends on a token field, hidden from screen readers", () => {
    expect(page).toMatch(/<div[^>]*data-token-field[^>]*aria-hidden="true"/);
    expect(page.indexOf("data-token-field")).toBeLessThan(page.indexOf("<footer"));
  });

  it("shares a card of the new hero", () => {
    expect(page).toContain('<meta property="og:image" content="/social.png"');
    const png = readFileSync(fileURLToPath(new URL("../public/social.png", import.meta.url)));
    expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([1200, 630]);
  });

  it("describes itself as an experiment", () => {
    expect(page).toMatch(/<meta name="description" content="[^"]*experiment[^"]*"/);
  });
});

export { count };
