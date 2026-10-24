import { expect, test } from "vitest";
import type { EditPlanSegment, OverlayPage, SceneSuggestions } from "../../api";
import { buildTrechos, captionBlocks, rulerTicks, sceneBlocks, seekFromPointer, tickLabel } from "./timeline";

const seg = (order: number, start: number, end: number, extra: Partial<EditPlanSegment> = {}): EditPlanSegment => ({ start, end, timeline_order: order, ...extra });

test("a pointer maps linearly onto the timeline and clamps at both ends", () => {
  expect(seekFromPointer(150, 100, 200, 60)).toBe(15);
  expect(seekFromPointer(100, 100, 200, 60)).toBe(0);
  expect(seekFromPointer(300, 100, 200, 60)).toBe(60);
  expect(seekFromPointer(50, 100, 200, 60)).toBe(0);
  expect(seekFromPointer(900, 100, 200, 60)).toBe(60);
  expect(seekFromPointer(150, 100, 0, 60)).toBe(0);
  expect(seekFromPointer(150, 100, 200, 0)).toBe(0);
});

test("plan segments are grouped by selection piece, scaled to the timeline, with a junction between groups", () => {
  const { blocks, joins } = buildTrechos([
    seg(2, 300, 310, { piece_index: 1 }), seg(0, 100, 110, { piece_index: 0 }), seg(1, 115, 125, { piece_index: 0 }),
  ], 60);
  expect(blocks).toEqual([
    { label: "Trecho 1", start: 0, end: 40, teaser: false },
    { label: "Trecho 2", start: 40, end: 60, teaser: false },
  ]);
  expect(joins).toEqual([40]);
});

test("the opening teaser is its own block and does not count as a piece", () => {
  const { blocks, joins } = buildTrechos([seg(0, 50, 54, { teaser: true }), seg(1, 40, 60, { piece_index: 0 })], 24);
  expect(blocks.map(block => [block.label, block.teaser])).toEqual([["Teaser", true], ["Trecho 1", false]]);
  expect(joins).toEqual([4]);
  expect(buildTrechos([], 30)).toEqual({ blocks: [], joins: [] });
});

test("caption blocks flag the current page and doubtful words that were not corrected", () => {
  const word = (index: number, probability: number | null) => ({ index, text: "X", start: 0, end: 1, emphasis: false, original: "x", probability });
  const pages: OverlayPage[] = [{ start: 0, end: 1, lines: [[word(0, 0.9)]] }, { start: 1, end: 2, lines: [[word(1, 0.2)]] }, { start: 2, end: 3, lines: [[word(2, 0.2)]] }];
  const blocks = captionBlocks(pages, 1.5, [{ word_index: 2, original: "x", text: "y" }]);
  expect(blocks.map(block => [block.current, block.doubtful])).toEqual([[false, false], [true, true], [false, false]]);
});

test("the ruler picks a readable step and labels minutes", () => {
  expect(rulerTicks(71)).toEqual([0, 10, 20, 30, 40, 50, 60, 70]);
  expect(rulerTicks(0)).toEqual([]);
  expect(rulerTicks(300).length).toBeLessThanOrEqual(10);
  expect(tickLabel(70)).toBe("1:10");
  expect(tickLabel(5)).toBe("0:05");
});

test("scene lane: accepted inserts are solid, undecided suggestions pending, rejected ones hidden", () => {
  const item = (id: string, at: number) => ({ id, kind: "reaction" as const, anchor: at, duration: 2, output_start: at, source_start: 100, group: 1, score: 1, reason: "", alternatives: [] });
  const data = {
    status: "ok", suggestions: [item("a", 5), item("b", 10), item("c", 15)], decisions: { c: "rejected" },
  } as unknown as SceneSuggestions;
  expect(sceneBlocks(data, [{ kind: "reaction", anchor: 5, duration: 2, source_start: 100, suggestion_id: "a" }])).toEqual([
    { id: "a", kind: "reaction", start: 5, duration: 2, state: "accepted" }, { id: "b", kind: "reaction", start: 10, duration: 2, state: "pending" },
  ]);
  expect(sceneBlocks(null, [])).toEqual([]);
  expect(sceneBlocks({ ...data, status: "needs_voice" }, [])).toEqual([]);
});
