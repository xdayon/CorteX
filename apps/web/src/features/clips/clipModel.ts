import { KEY_SEPARATOR } from "../../router";
import type { ChipTone } from "../../ui";
import type { ClipExport, ClipRow, ClipState, ClipStatus, DiscardReason } from "../../api";

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
export const rowId = (row: Pick<ClipRow, "run_id" | "clip_key">) => `${row.run_id}${KEY_SEPARATOR}${row.clip_key}`;

/** Same precedence as the backend, so an optimistic change shows the status the server will return. */
export function deriveStatus(state: ClipState | null, exports: ClipExport[]): ClipStatus {
  if (state?.posted) return "posted";
  if (state?.decision === "discarded") return "discarded";
  if (state?.saved) return "saved";
  if (exports.length) return "exported";
  if (state?.decision === "approved") return "approved";
  return "new";
}

export function withState<R extends ClipRow>(row: R, change: Partial<ClipState>): R {
  const base: ClipState = row.state ?? { decision: null, discard_reason: null, saved: false, saved_at: null, posted: false, posted_at: null, notes: "", updated_at: new Date().toISOString() };
  const state = { ...base, ...change };
  if (state.decision !== "discarded") state.discard_reason = null;
  return { ...row, state, status: deriveStatus(state, row.exports) };
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

/** Which clips earn the accented score badge: a score of 9 or more. */
export const isTopScore = (row: ClipRow) => row.score != null && row.score >= 9;

/** Scores with one decimal only when they are not whole, pt-BR comma ("9,7"). */
export const formatScore = (score: number) => (Number.isInteger(score) ? String(score) : score.toFixed(1).replace(".", ","));
