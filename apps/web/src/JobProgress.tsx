import { useEffect, useState } from "react";
import type { ApiJob } from "./api";
import { ProgressBar } from "./ui";
import "./JobProgress.css";

export type JobActivity = { job: ApiJob; receivedAt: number };

const STATUS_LABELS: Record<string, string> = {
  succeeded: "Concluída", running: "Em andamento", queued: "Na fila", failed: "Falhou", cancelled: "Cancelada",
};

/** Seconds since the job last reported, and whether that silence looks like a stuck task. */
export function useJobStale(job: ApiJob | null, history: JobActivity[], busy: boolean) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [busy]);
  const last = history.at(-1);
  const serverUpdatedAt = job?.updated_at ? Date.parse(job.updated_at) : Number.NaN;
  const lastActivity = Number.isFinite(serverUpdatedAt) ? serverUpdatedAt : last?.receivedAt;
  const age = lastActivity ? Math.max(0, Math.floor((now - lastActivity) / 1000)) : 0;
  return { age, stale: busy && job?.status === "running" && age >= 30 };
}

export function staleMessage(age: number) {
  return `Sem atualização há ${age}s. A tarefa pode estar travada; o CorteX a interromperá após 5 minutos sem atividade.`;
}

export function JobDetails({ job, history, age, stale }: { job: ApiJob | null; history: JobActivity[]; age: number; stale: boolean }) {
  return <div className="job-details">
    {job ? <>
      <p><strong>{job.message || job.type}</strong> · <span className="mono">{Math.round(job.progress || 0)}%</span> desta tarefa</p>
      <dl>
        <dt>Estado</dt><dd>{STATUS_LABELS[job.status] || job.status}</dd>
        <dt>Etapa</dt><dd>{job.stage || job.type}</dd>
        <dt>Tarefa</dt><dd className="mono">{job.id}</dd>
        {job.worker_pid && <><dt>Processo local</dt><dd className="mono">{job.worker_pid}</dd></>}
        <dt>Última atualização</dt><dd className="mono">{age}s atrás</dd>
      </dl>
      {stale && <p>Sem atividade recente. Se chegar a 5 minutos, o worker encerra a tarefa como falha para ela não ficar presa em “processando”.</p>}
    </> : <p>Aguardando a primeira tarefa do servidor.</p>}
    <ol className="job-events">{history.map((event, index) => <li key={index}><time className="mono">{new Date(event.receivedAt).toLocaleTimeString("pt-BR")}</time> {event.job.message || event.job.type} · <span className="mono">{Math.round(event.job.progress || 0)}%</span></li>)}</ol>
    <small>Os percentuais vêm das tarefas locais. O avanço do lote é uma estimativa; cada etapa tem duração diferente.</small>
  </div>;
}

export function JobProgress({ job, history, progress, label, busy, completed = false }: {
  job: ApiJob | null; history: JobActivity[]; progress: number; label: string; busy: boolean; completed?: boolean;
}) {
  const { age, stale } = useJobStale(job, history, busy);
  return <section className="job-progress" aria-label="Progresso do processamento">
    <div className="job-head"><b>{label}</b><span className="mono">{Math.round(progress)}%</span></div>
    <ProgressBar value={progress} label="Lote de cortes" />
    {completed && <span className="sr-only">Concluído</span>}
    {job && <p className="job-status" role={stale ? "alert" : "status"}>{stale ? staleMessage(age) : `Etapa atual: ${job.message || job.type || "aguardando o servidor"} · atualização há ${age}s.`}</p>}
    <details><summary>Detalhes técnicos</summary><JobDetails job={job} history={history} age={age} stale={stale} /></details>
  </section>;
}
