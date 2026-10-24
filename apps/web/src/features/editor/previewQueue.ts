import type { ApiJob, ClipRow, RefineStatus, RenderSettings } from "../../api";
import { api } from "../../api";

export type PreviewPhase = "queued" | "planning" | "rendering" | "ready" | "stale" | "error";

/** What the queue knows about one clip; `renderedKey` and `planTeaser` record the inputs the artifacts were built from. */
export type ClipPreview = {
  planId: string | null; planTeaser: string; timeline: number | null; refine: RefineStatus | null;
  belowMinimum: boolean; teaserDropped: string | null; questionDropped: string | null; videoUrl: string | null; renderedKey: string;
  running: "planning" | "rendering" | null; progress: number; error: string; errorKey: string;
};
export type PreviewView = ClipPreview & { phase: PreviewPhase };

export const EMPTY_PREVIEW: ClipPreview = {
  planId: null, planTeaser: "", timeline: null, refine: null, belowMinimum: false, teaserDropped: null, questionDropped: null,
  videoUrl: null, renderedKey: "", running: null, progress: 0, error: "", errorKey: "",
};

/** The inputs a clip's preview is built from right now; `teaserKey` covers everything that re-plans the clip (teaser and opening question). */
export type Target = { teaserKey: string; renderKey: string; errorKey: string };

export function targetOf(row: ClipRow, settings: RenderSettings): Target {
  const teaser = settings.transitions.teaser && row.clip.teaser ? row.clip.teaser : null;
  const question = settings.transitions.question;
  const teaserKey = (teaser ? `${teaser.start}-${teaser.end}` : "") + (question ? `|q${question.start}-${question.end}` : "");
  const renderKey = JSON.stringify([settings.framing, settings.transitions.piece_junction, settings.inserts ?? []]);
  return { teaserKey, renderKey, errorKey: `${teaserKey}|${renderKey}` };
}

/**
 * Whether the queue should spend a job on this clip. A clip with no preview yet is always prepared; one whose
 * preview is out of date (framing or teaser changed) is refreshed only while focused, so a global framing change
 * never fans out into a render per clip.
 */
export function needsWork(state: ClipPreview | undefined, target: Target, focused: boolean) {
  const current = state ?? EMPTY_PREVIEW;
  if (current.error && current.errorKey === target.errorKey) return false;
  if (current.planId === null) return true;
  if (current.planTeaser !== target.teaserKey) return focused;
  if (current.renderedKey === target.renderKey) return false;
  return focused || !current.videoUrl;
}

/** Focused clip first, then the rest in list order. */
export function pickNext(keys: string[], focused: string, wants: (key: string) => boolean) {
  if (keys.includes(focused) && wants(focused)) return focused;
  return keys.find(key => key !== focused && wants(key)) ?? null;
}

export function phaseOf(state: ClipPreview | undefined, target: Target, focused: boolean): PreviewPhase {
  const current = state ?? EMPTY_PREVIEW;
  if (current.running) return current.running;
  if (current.error && current.errorKey === target.errorKey) return "error";
  if (current.videoUrl && current.renderedKey !== target.renderKey) return "stale";
  if (needsWork(state, target, focused)) return "queued";
  return current.videoUrl ? "ready" : "queued";
}

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

export async function finishJob(job: ApiJob, signal: AbortSignal, onUpdate: (job: ApiJob) => void) {
  if (TERMINAL.has(job.status)) return job;
  return api.watchJob(job.id, update => { if (!signal.aborted) onUpdate(update); }, signal);
}

export function cancelQuietly(jobId: string | null) {
  if (!jobId) return;
  try { void Promise.resolve(api.cancelJob(jobId)).catch(() => undefined); } catch { /* best effort */ }
}

export const reasonText = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));
