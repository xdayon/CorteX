import {useEffect, useState} from 'react';
import type {ApiJob} from './api';
export type JobActivity = {job: ApiJob; receivedAt: number};
export function JobProgress({job, history, progress, label, busy, completed = false}: {
  job: ApiJob | null; history: JobActivity[]; progress: number; label: string; busy: boolean; completed?: boolean;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { if (!busy) return; const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, [busy]);
  const last = history.at(-1);
  const serverUpdatedAt = job?.updated_at ? Date.parse(job.updated_at) : Number.NaN;
  const lastActivity = Number.isFinite(serverUpdatedAt) ? serverUpdatedAt : last?.receivedAt;
  const age = lastActivity ? Math.max(0, Math.floor((now-lastActivity)/1000)) : 0;
  const stale = busy && job?.status === "running" && age >= 30;
  return <section className="simple-progress job-progress" aria-label="Progresso do processamento">
    <div><b>{completed ? "✓ " : ""}{label}</b><span>{Math.round(progress)}%</span></div>
    <progress max={100} value={progress} aria-label="Lote de cortes"/>
    {job && <p role={stale ? "alert" : "status"}>{stale ? `Sem atualização há ${age}s. A tarefa pode estar travada; o CorteX a interromperá após 5 minutos sem atividade.` : `Etapa atual: ${job.message || job.type || "aguardando o servidor"} · atualização há ${age}s.`}</p>}
    <details><summary>Exibir detalhes do processamento</summary>
      {job ? <><p><strong>{job.message || job.type}</strong> · {Math.round(job.progress || 0)}% desta tarefa</p>
        <dl><dt>Estado</dt><dd>{{succeeded:'Concluída',running:'Em andamento',queued:'Na fila',failed:'Falhou',cancelled:'Cancelada'}[job.status] || job.status}</dd><dt>Etapa</dt><dd>{job.stage || job.type}</dd>
          <dt>Tarefa</dt><dd>{job.id}</dd>{job.worker_pid && <><dt>Processo local</dt><dd>{job.worker_pid}</dd></>}
          <dt>Última atualização recebida</dt><dd>{age}s atrás</dd></dl>
        {stale && <p>Sem atividade recente. Se chegar a 5 minutos, o worker encerra a tarefa como falha para ela não ficar presa em “processando”.</p>}
      </> : <p>Aguardando a primeira tarefa do servidor.</p>}
      <ol className="job-events">{history.map((event, i) => <li key={i}><time>{new Date(event.receivedAt).toLocaleTimeString('pt-BR')}</time> {event.job.message || event.job.type} · {Math.round(event.job.progress || 0)}%</li>)}</ol>
      <small>Percentuais recebidos das tarefas locais. O avanço do lote é uma estimativa; cada etapa tem duração diferente.</small>
    </details>
  </section>;
}
