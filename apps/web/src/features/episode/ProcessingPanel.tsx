import { useEffect, useRef, useState } from "react";
import { api, type ApiJob, type WorkflowRun } from "../../api";
import { errorText, PROCESSING_STAGES, STAGE_MESSAGES, stageIndex } from "../../app/humanize";
import { JobDetails, staleMessage, useJobStale, type JobActivity } from "../../JobProgress";
import type { Task } from "../../app/tasks";
import { Button, Icon, ProgressBar, useToast } from "../../ui";

export function ProcessingPanel({ run, task, onCancelled }: { run?: WorkflowRun; task?: Task; onCancelled: () => void }) {
  const toast = useToast();
  const [job, setJob] = useState<ApiJob | null>(null);
  const [history, setHistory] = useState<JobActivity[]>([]);
  const [cancelling, setCancelling] = useState(false);
  const jobId = run?.active_job_id ?? null;
  const last = useRef("");

  useEffect(() => {
    setJob(null); setHistory([]); last.current = "";
    if (!jobId) return;
    const controller = new AbortController();
    let timer = 0;
    async function poll() {
      try {
        const next = await api.job(jobId!, controller.signal);
        if (controller.signal.aborted || !next) return;
        setJob(next);
        const stamp = `${next.status}|${next.progress}|${next.message}`;
        if (stamp !== last.current) {
          last.current = stamp;
          setHistory(items => [...items.slice(-39), { job: next, receivedAt: Date.now() }]);
        }
      } catch { /* o painel mantém o último estado conhecido */ }
      if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), 2000);
    }
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [jobId]);

  const { age, stale } = useJobStale(job, history, true);
  const current = run ? stageIndex(run) : 0;
  const progress = run ? run.progress : task?.progress ?? 0;
  const percent = Math.round(progress);

  async function cancel() {
    if (!jobId) return;
    setCancelling(true);
    try { await api.cancelJob(jobId); toast("Processamento cancelado."); onCancelled(); }
    catch (reason) { toast(errorText(reason), "bad"); }
    finally { setCancelling(false); }
  }

  return <section className="proc card" aria-label="Processamento do episódio">
    <ol className="stages">
      {PROCESSING_STAGES.map((stage, index) => <li key={stage} className={index < current ? "done" : index === current ? "now" : ""} aria-current={index === current ? "step" : undefined}>
        {index < current && <Icon name="check" small />}{stage}
        {index < PROCESSING_STAGES.length - 1 && <Icon name="chevronRight" small />}
      </li>)}
    </ol>
    <div className="proc-line">
      <b>{run ? (run.status === "queued" ? "Na fila para começar" : STAGE_MESSAGES[current]) : task?.message}</b>
      <span className="mono">{percent}%</span>
    </div>
    <ProgressBar value={progress} label="Andamento do processamento" />
    <p className="proc-hint">O processamento continua mesmo se você fechar esta página.</p>
    {stale && <div className="notice" role="alert"><p>{staleMessage(age)}</p></div>}
    <div className="proc-actions">
      <details className="proc-details"><summary>Detalhes técnicos</summary>
        {run?.message && <p className="proc-msg">{run.message}</p>}
        <JobDetails job={job} history={history} age={age} stale={stale} />
      </details>
      <div className="grow" />
      {jobId && <Button size="sm" disabled={cancelling} onClick={() => void cancel()}>Cancelar</Button>}
    </div>
  </section>;
}
