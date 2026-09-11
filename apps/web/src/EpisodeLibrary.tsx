import { VoiceSelector } from "./VoiceSelector";
import { useEffect, useState } from "react";
import { api, type EpisodeEntry, type WorkflowRun } from "./api";

export function EpisodeLibrary({ busy, onOpen, onUse }: {
  busy: boolean;
  onOpen: (run: WorkflowRun) => void;
  onUse: (episode: EpisodeEntry) => void;
}) {
  const [entries, setEntries] = useState<EpisodeEntry[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    async function refresh() {
      try { const result = await api.episodes(); if (active) { setEntries(result); setError(""); } }
      catch (reason) { if (active) setError(String(reason)); }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  async function download(entry: EpisodeEntry) {
    try { await api.downloadEpisode(entry.id); setEntries(await api.episodes()); }
    catch (reason) { setError(String(reason)); }
  }
  return <section className="simple-page enter">
    <div className="simple-title"><span>DAYON NEWS · BIBLIOTECA LOCAL</span><h1>Seus episódios. Seus cortes.</h1><p>Os vídeos e resultados ficam salvos neste computador. Abra uma seleção anterior ou aproveite novos trechos do mesmo episódio.</p></div>
    {error && <p role="alert">Não foi possível carregar a biblioteca: {error}</p>}
    {!entries.length && !error && <p>Adicione um episódio pelo botão Novo episódio. Ele ficará disponível aqui.</p>}
    <div className="library-grid">{entries.map(entry => {
      const activeJob = entry.jobs.find(job => ["queued", "running"].includes(job.status));
      return <article className="simple-card" key={entry.id}>
        <h3>{entry.title}</h3><p>Foco: {entry.primary_subject}{entry.participant_count ? ` · ${entry.participant_count} participantes` : ""}</p>
        <small>{entry.source ? `Vídeo salvo · ${(entry.source_bytes / 1e9).toFixed(2)} GB` : "Vídeo ainda não baixado"} · {entry.renders.length} vídeos gerados</small>
        {activeJob && <p role="status">{activeJob.message} · {Math.round(activeJob.progress || 0)}%</p>}
        {!activeJob && entry.jobs[0]?.status === "failed" && <p role="alert">Última tentativa: {entry.jobs[0].error || entry.jobs[0].message}</p>}
        <div className="library-actions"><button className="btn primary" disabled={busy || !!activeJob} onClick={() => onUse(entry)}>{entry.source ? "Gerar novos cortes" : "Preparar cortes"}</button>
        {!entry.source && <button className="btn secondary" disabled={busy || !!activeJob} onClick={() => void download(entry)}>Baixar e guardar</button>}</div>
        {entry.source && <VoiceSelector entry={entry} disabled={busy || !!activeJob}/>}
        {!!entry.runs.length && <details><summary>Seleções e processamentos ({entry.runs.length})</summary>{entry.runs.map(run => <button className="btn secondary" key={run.id} disabled={busy} onClick={() => onOpen(run)}>{run.status === "ready_for_review" ? "Abrir cortes" : "Ver processamento"} · {new Date(run.created_at || "").toLocaleString("pt-BR")} · {run.message}</button>)}</details>}
        {!!entry.renders.length && <details><summary>Vídeos salvos ({entry.renders.length})</summary>{entry.renders.map(render => <p key={render.id}><a href={api.renderMediaUrl(render.project_id, render.id)} target="_blank" rel="noreferrer">{render.title} · {Math.round(render.duration || 0)}s</a></p>)}</details>}
      </article>;
    })}</div>
  </section>;
}
