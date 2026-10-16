import type { ClipCounts, EpisodeEntry } from "../../api";
import type { ChipTone } from "../../ui";
import { activeJobOf, activeRunOf, isRunReady, jobLabel, plural, shortError, STAGE_MESSAGES, stageIndex } from "../../app/humanize";

export type EpisodeStatus = {
  kind: "processing" | "attention" | "ready" | "undownloaded" | "idle";
  label: string;
  tone: ChipTone;
  /** Active work: readable stage and 0-100 progress. */
  stage?: string;
  percent?: number;
  /** Short human reason when the last attempt failed. */
  error?: string;
  readyRuns: number;
};

export function episodeStatus(entry: EpisodeEntry): EpisodeStatus {
  const readyRuns = entry.runs.filter(isRunReady).length;
  const run = activeRunOf(entry);
  const job = activeJobOf(entry);
  if (run || job) {
    const percent = Math.round(run ? run.progress : job?.progress ?? 0);
    return {
      kind: "processing", label: `Processando ${percent}%`, tone: "ac", percent, readyRuns,
      stage: run ? STAGE_MESSAGES[stageIndex(run)] : jobLabel(job?.type),
    };
  }
  const latest = entry.runs[0];
  const failedJob = !entry.source ? entry.jobs.find(item => item.status === "failed") : undefined;
  if (latest?.status === "failed" || (!latest && failedJob)) {
    return { kind: "attention", label: "Precisa de atenção", tone: "bad", error: shortError(latest?.error ?? failedJob?.error ?? failedJob?.message), readyRuns };
  }
  if (readyRuns) return { kind: "ready", label: "Pronto", tone: "ok", readyRuns };
  if (!entry.source && entry.url) return { kind: "undownloaded", label: "Não baixado", tone: "neutral", readyRuns };
  return { kind: "idle", label: "Sem cortes", tone: "neutral", readyRuns };
}

export const exportedCount = (counts: ClipCounts) => counts.exported + counts.saved + counts.posted;

export function episodeStats(entry: EpisodeEntry, readyRuns: number, counts?: ClipCounts) {
  const parts: string[] = [];
  if (counts && counts.total > 0) {
    parts.push(plural(counts.total, "corte", "cortes"));
    if (exportedCount(counts)) parts.push(plural(exportedCount(counts), "exportado", "exportados"));
    if (counts.posted) parts.push(plural(counts.posted, "postado", "postados"));
    return parts.join(" · ");
  }
  if (entry.renders.length) parts.push(plural(entry.renders.length, "vídeo", "vídeos"));
  if (readyRuns) parts.push(plural(readyRuns, "seleção", "seleções"));
  if (parts.length) return parts.join(" · ");
  return entry.source ? "Nenhuma seleção ainda" : "Ainda não baixado";
}
