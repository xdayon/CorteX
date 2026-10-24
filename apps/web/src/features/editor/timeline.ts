import type { CaptionCorrection, EditPlanSegment, OverlayPage, RenderInsert, SceneSuggestion, SceneSuggestions } from "../../api";
import { isDoubtful } from "../../editor/captions";

export type Trecho = { label: string; start: number; end: number; teaser: boolean };

/** Plan segments grouped by selection piece (the teaser on its own), placed on the timeline, plus the junctions between groups. */
export function buildTrechos(segments: EditPlanSegment[], total: number): { blocks: Trecho[]; joins: number[] } {
  const ordered = [...segments].sort((a, b) => a.timeline_order - b.timeline_order);
  const length = (segment: EditPlanSegment) => Math.max(0, (segment.video_end ?? segment.end) - (segment.video_start ?? segment.start));
  const sum = ordered.reduce((all, segment) => all + length(segment), 0);
  if (!sum) return { blocks: [], joins: [] };
  const scale = total > 0 ? total / sum : 1;
  const blocks: Trecho[] = [];
  let cursor = 0, previous = "", body = 0;
  for (const segment of ordered) {
    const group = segment.teaser ? "teaser" : `piece:${segment.piece_index ?? 0}`;
    const end = cursor + length(segment) * scale;
    if (group === previous) blocks[blocks.length - 1].end = end;
    else blocks.push({ label: segment.teaser ? "Teaser" : `Trecho ${++body}`, start: cursor, end, teaser: !!segment.teaser });
    previous = group; cursor = end;
  }
  return { blocks, joins: blocks.slice(1).map(block => block.start) };
}

export type CaptionBlock = { start: number; end: number; doubtful: boolean; current: boolean };

export function captionBlocks(pages: OverlayPage[], time: number, corrections: CaptionCorrection[]): CaptionBlock[] {
  return pages.map(page => ({
    start: page.start, end: page.end, current: time >= page.start && time < page.end,
    doubtful: page.lines.flat().some(word => isDoubtful(word, corrections)),
  }));
}

/** Timeline second under a pointer: `clientX` against the lane's box, clamped to the clip. */
export function seekFromPointer(clientX: number, left: number, width: number, total: number) {
  if (width <= 0 || total <= 0) return 0;
  return Math.min(Math.max((clientX - left) / width, 0), 1) * total;
}

const STEPS = [1, 2, 5, 10, 15, 30, 60, 120];

export function rulerTicks(total: number) {
  if (total <= 0) return [];
  const step = STEPS.find(candidate => total / candidate <= 9) ?? 300;
  const ticks: number[] = [];
  for (let second = 0; second <= total + 1e-6; second += step) ticks.push(second);
  return ticks;
}

export const tickLabel = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;

export type SceneBlock = { id: string; kind: SceneSuggestion["kind"]; start: number; duration: number; state: "accepted" | "pending" };

/** Scene lane: accepted inserts as solid blocks, undecided suggestions as dashed ones, rejected ones hidden. */
export function sceneBlocks(data: SceneSuggestions | null, inserts: RenderInsert[]): SceneBlock[] {
  if (!data || data.status !== "ok") return [];
  const accepted = new Set(inserts.map(insert => insert.suggestion_id));
  const blocks: SceneBlock[] = [];
  for (const item of data.suggestions) {
    const state = accepted.has(item.id) ? "accepted" : data.decisions[item.id] === "rejected" ? null : "pending";
    if (state) blocks.push({ id: item.id, kind: item.kind, start: item.output_start, duration: item.duration, state });
  }
  return blocks;
}
