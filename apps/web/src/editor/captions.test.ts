import { expect, test } from "vitest";
import type { CaptionCorrection, SuggestedClip } from "../api";
import { emphasisFor, findReplace, hideWord, restoreWord, setWordText, toggleEmphasis, type IndexedWord } from "./captions";
import { clipKey } from "./clips";

const words: IndexedWord[] = [
  { index: 0, word: "O", start: 0, end: 1 },
  { index: 1, word: "Cortecs,", start: 1, end: 2 },
  { index: 2, word: "cortecs", start: 2, end: 3 },
  { index: 3, word: "cortecs", start: 50, end: 51 },
];
const clip = (rank: number, start: number, end: number, extra: Partial<SuggestedClip> = {}) =>
  ({ rank, title: `c${rank}`, start_second: start, end_second: end, pacing: "balanced", ...extra }) as SuggestedClip;

test("editing stores {word_index, original, text}; equal to the original is a no-op", () => {
  const edited = setWordText([], 1, "Cortecs", "CorteX");
  expect(edited).toEqual([{ word_index: 1, original: "Cortecs", text: "CorteX" }]);
  expect(setWordText(edited, 1, "Cortecs", "Cortecs")).toEqual([]);
  expect(setWordText([], 1, "Cortecs", "Cortecs")).toEqual([]);
  expect(setWordText(edited, 0, "O", "Ó").map(item => item.word_index)).toEqual([0, 1]);
});

test("hide stores empty text and restore removes only that word", () => {
  const hidden = hideWord([{ word_index: 5, original: "x", text: "y" }], 2, "cortecs");
  expect(hidden).toContainEqual({ word_index: 2, original: "cortecs", text: "" });
  expect(restoreWord(hidden, 2)).toEqual([{ word_index: 5, original: "x", text: "y" }]);
});

test("emphasis defaults to the clip's AI choice until the first edit, then toggles", () => {
  const c = clip(1, 0, 10, { emphasis_word_indices: [4, 9] });
  expect(emphasisFor(c, {})).toEqual([4, 9]);
  expect(emphasisFor(clip(2, 0, 10), {})).toEqual([]);
  expect(emphasisFor(c, { [clipKey(c)]: [] })).toEqual([]);
  expect(toggleEmphasis([4, 9], 6)).toEqual([4, 6, 9]);
  expect(toggleEmphasis([4, 6, 9], 6)).toEqual([4, 9]);
});

test("find and replace is whole-word, case-insensitive, scoped to each clip's pieces", () => {
  const a = clip(1, 0, 5);
  const b = clip(2, 45, 60);
  const outside = clip(3, 70, 80);
  const all = findReplace(words, [a, b, outside], {}, "CORTECS", "CorteX");
  expect(all.count).toBe(3);
  expect(all.edits[clipKey(a)]).toEqual([
    { word_index: 1, original: "Cortecs,", text: "CorteX," },
    { word_index: 2, original: "cortecs", text: "CorteX" },
  ]);
  expect(all.edits[clipKey(b)]).toEqual([{ word_index: 3, original: "cortecs", text: "CorteX" }]);
  expect(all.edits[clipKey(outside)]).toBeUndefined();
  expect(findReplace(words, [a], {}, "cort", "x").count).toBe(0);
  const onlyThis = findReplace(words, [a], {}, "cortecs", "CorteX");
  expect(onlyThis.count).toBe(2);
  expect(onlyThis.edits[clipKey(b)]).toBeUndefined();
});

test("find and replace uses pieces, keeps earlier corrections and can revert to the original", () => {
  const pieces = clip(1, 0, 60, { pieces: [{ from_id: 0, to_id: 1, start: 49, end: 52, text: "" }] });
  const existing: Record<string, CaptionCorrection[]> = { [clipKey(pieces)]: [{ word_index: 0, original: "O", text: "Ó" }] };
  const result = findReplace(words, [pieces], existing, "cortecs", "cortecs");
  expect(result.count).toBe(1);
  expect(result.edits[clipKey(pieces)]).toEqual([{ word_index: 0, original: "O", text: "Ó" }]);
});
