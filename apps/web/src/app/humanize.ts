import type { ApiJob, EpisodeEntry, WorkflowRun } from "../api";

const JOB_LABELS: Record<string, string> = {
  ingest_youtube: "Baixando",
  transcription: "Transcrevendo",
  analysis: "Analisando",
  suggestion: "Escolhendo cortes",
  edit_plan: "Preparando corte",
  render: "Renderizando",
};

export const jobLabel = (type?: string) => (type && JOB_LABELS[type]) || "Processando";

export const isActiveStatus = (status?: string) => status === "queued" || status === "running";

export const isRunReady = (run?: WorkflowRun) => !!run && ["ready_for_review", "rendering", "complete"].includes(run.status);

export function activeRunOf(entry: EpisodeEntry) {
  const run = entry.runs[0];
  return run && isActiveStatus(run.status) ? run : undefined;
}

export function activeJobOf(entry: EpisodeEntry) {
  return entry.jobs.find(job => isActiveStatus(job.status));
}

export function newestActive(jobs: ApiJob[]) {
  const active = jobs.filter(job => isActiveStatus(job.status));
  const byDate = (a: ApiJob, b: ApiJob) => Date.parse(b.created_at ?? "") - Date.parse(a.created_at ?? "") || 0;
  const running = active.filter(job => job.status === "running").sort(byDate);
  return { active, lead: running[0] ?? [...active].sort(byDate)[0] };
}

/** One short, readable line from a technical error message. */
export function shortError(text?: string | null, fallback = "A última tentativa não terminou.") {
  const first = (text ?? "").split("\n")[0].trim();
  if (!first) return fallback;
  return first.length > 140 ? `${first.slice(0, 137)}…` : first;
}

export const errorText = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

export function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

const STAGES = ["Baixar", "Transcrever", "Analisar", "Escolher cortes"] as const;
export const PROCESSING_STAGES = STAGES;

/** Index of the stage a run is in, within PROCESSING_STAGES. */
export function stageIndex(run: WorkflowRun) {
  if (run.stage === "ingest") return 0;
  if (run.stage === "analyze") return 2;
  if (["suggest", "edit", "render", "quality", "complete"].includes(run.stage)) return 3;
  return 1;
}

export const STAGE_MESSAGES = [
  "Baixando o vídeo", "Transcrevendo a conversa", "Analisando o episódio", "Escolhendo os melhores cortes",
];

/** m:ss, or h:mm:ss from one hour on. */
export function formatClock(seconds: number) {
  const value = Math.max(0, Math.round(seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const rest = String(value % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}
