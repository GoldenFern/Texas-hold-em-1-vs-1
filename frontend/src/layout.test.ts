import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

function read(relative: string): string {
  return readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf-8");
}

describe("responsive layout regressions", () => {
  it("gives the felt a definite height on narrow screens", () => {
    const source = read("./components/TableView.vue");
    expect(source).toContain(".felt { height: 430px; min-height: 430px; aspect-ratio: auto;");
  });

  it("wraps the raise panel before the table gets too narrow", () => {
    const source = read("./components/ActionBar.vue");
    expect(source).toContain("@media (max-width: 1520px)");
    expect(source).toMatch(/@media \(max-width: 1520px\)[\s\S]*?\.raise-panel \{\s+flex: 1 1 100%;/);
  });
});
