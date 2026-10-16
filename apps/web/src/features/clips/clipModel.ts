import type { ChipTone } from "../../ui";
import type { ClipExport, ClipRow, ClipState, ClipStatus, DiscardReason, Publication } from "../../api";

export const STATUS_LABELS: Record<ClipStatus, string> = {
  new: "Novo", approved: "Aprovado", exported: "Exportado", saved: "Salvo", posted: "Postado", discarded: "Descartado",
};
export const STATUS_TONES: Record<ClipStatus, ChipTone> = {
  new: "neutral", approved: "ac", exported: "ok", saved: "warn", posted: "ok", discarded: "bad",
};
export const REASON_LABELS: Record<DiscardReason, string> = {
  weak_hook: "Gancho fraco", off_topic: "Fora do tema", repeated: "Repetido", bad_edit: "Edição ruim", technical: "Problema técnico", other: "Outro",
};
export const REASONS = Object.keys(REASON_LABELS) as DiscardReason[];

/** Row identity across runs: `<runId>~<clipKey>`. */
export const rowId = (row: Pick<ClipRow, "run_id" | "clip_key">) => `${row.run_id}~${row.clip_key}`;

/** Same precedence as the backend, so an optimistic change shows the status the server will return. */
export function deriveStatus(state: ClipState | null, exports: ClipExport[], publication: Publication | null): ClipStatus {
  if (publication?.published) return "posted";
  if (state?.decision === "discarded") return "discarded";
  if (state?.saved) return "saved";
  if (exports.length) return "exported";
  if (state?.decision === "approved") return "approved";
  return "new";
}

export function withState<R extends ClipRow>(row: R, change: Partial<ClipState>): R {
  const base: ClipState = row.state ?? { decision: null, discard_reason: null, saved: false, saved_at: null, notes: "", updated_at: new Date().toISOString() };
  const state = { ...base, ...change };
  if (state.decision !== "discarded") state.discard_reason = null;
  return { ...row, state, status: deriveStatus(state, row.exports, row.publication) };
}

export function withPublication<R extends ClipRow>(row: R, publication: Publication | null): R {
  return { ...row, publication, status: deriveStatus(row.state, row.exports, publication) };
}

const MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
export function shortDate(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

export function downloadName(title: string) {
  const safe = title.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-");
  return `${safe || "corte"}.mp4`;
}

/** Sequential downloads; the browser may ask once to allow several files. */
export async function downloadAll(items: Array<{ title: string; url: string }>) {
  for (const item of items) {
    const link = document.createElement("a");
    link.href = item.url; link.download = downloadName(item.title);
    document.body.appendChild(link); link.click(); link.remove();
    await new Promise(resolve => setTimeout(resolve, 600));
  }
}

export const newestExport = (row: ClipRow) => row.exports[0] as ClipExport | undefined;
export const headlineOf = (row: ClipRow) => row.headline || row.title;

/**
 * Which clips earn the accented score badge. Scores are relative to the run's own scale: on a scale that runs
 * past 10 (v1 sums) the top 20% of the run are accented, otherwise a score of 9 or more.
 */
export function topScores(rows: ClipRow[]): (row: ClipRow) => boolean {
  const byRun = new Map<string, number[]>();
  for (const row of rows) if (row.score != null) byRun.set(row.run_id, [...(byRun.get(row.run_id) ?? []), row.score]);
  const cutoff = new Map<string, number>();
  for (const [run, scores] of byRun) {
    const sorted = [...scores].sort((a, b) => b - a);
    cutoff.set(run, sorted[0] > 10 ? sorted[Math.max(0, Math.ceil(sorted.length * 0.2) - 1)] : 9);
  }
  return row => row.score != null && row.score >= (cutoff.get(row.run_id) ?? 9);
}
