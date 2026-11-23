import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// The built page, as a visitor's browser receives it. Run `npm run build` first.
const built = fileURLToPath(new URL("../dist/client/index.html", import.meta.url));
if (!existsSync(built)) throw new Error("dist/client/index.html is missing: run npm run build first");
const page = readFileSync(built, "utf-8");

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

  it("describes itself as an experiment", () => {
    expect(page).toMatch(/<meta name="description" content="[^"]*experiment[^"]*"/);
  });
});

export { count };
