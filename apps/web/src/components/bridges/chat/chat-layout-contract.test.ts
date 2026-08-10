import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const styles = readFileSync(
  path.join(process.cwd(), "src/components/bridges/chat/chat.module.css"),
  "utf8"
);

describe("conversation viewport layout", () => {
  it("keeps the thread as the only scroll container and docks the composer", () => {
    expect(styles).toMatch(/\.chatMain[\s\S]*?overflow:\s*hidden/);
    expect(styles).toMatch(/\.thread[\s\S]*?min-height:\s*0/);
    expect(styles).toMatch(/\.thread[\s\S]*?overflow-y:\s*auto/);
    expect(styles).toMatch(/\.thread[\s\S]*?overflow-x:\s*hidden/);
    expect(styles).toMatch(/\.composerWrap[\s\S]*?flex-shrink:\s*0/);
    expect(styles).not.toMatch(/position:\s*fixed/);
  });
});
