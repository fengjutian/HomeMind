import { describe, expect, it } from "vitest";
import {
  mergeExpertOrder,
  moveExpert,
  readExpertOrder,
  writeExpertOrder,
} from "./expertOrder";

describe("expertOrder", () => {
  it("keeps saved experts first and appends newly discovered experts", () => {
    expect(mergeExpertOrder(["b", "a", "removed"], ["a", "b", "c"])).toEqual([
      "b",
      "a",
      "c",
    ]);
  });

  it("moves a dragged expert before its drop target", () => {
    expect(moveExpert(["a", "b", "c"], "c", "a")).toEqual(["c", "a", "b"]);
    expect(moveExpert(["a", "b", "c"], "a", "c")).toEqual(["b", "a", "c"]);
  });

  it("round-trips local storage and ignores invalid data", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    } as Storage;

    writeExpertOrder(storage, ["a", "b"]);
    expect(readExpertOrder(storage)).toEqual(["a", "b"]);

    values.set("octop:chat-expert-order", "not-json");
    expect(readExpertOrder(storage)).toEqual([]);
  });
});
