import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

const layout = readFileSync("src/layout.html", "utf8");
const content = readFileSync("src/index.html", "utf8").trim();
mkdirSync("dist", { recursive: true });
writeFileSync("dist/index.html", layout.replace("{{content}}", content));
console.log("built dist/index.html");
