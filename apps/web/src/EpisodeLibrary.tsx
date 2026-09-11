import { VoiceSelector } from "./VoiceSelector";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type EpisodeEntry, type WorkflowRun } from "./api";

function EpisodeThumbnail({ entry }: { entry: EpisodeEntry }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [entry.thumbnail_url]);
  return entry.thumbnail_url && !failed
    ? <img className="episode-thumbnail" src={entry.thumbnail_url} alt={`Capa de ${entry.title}`} loading="lazy" decoding="async" width={480} height={270} onError={() => setFailed(true)} />
    : <div className="episode-thumbnail-placeholder">{entry.url ? "Capa ainda não disponível" : "Arquivo local"}</div>;
}

export function EpisodeLibrary({ busy, onOpen, onUse }: {
  busy: boolean;
  onOpen: (run: WorkflowRun) => void;
  onUse: (episode: EpisodeEntry) => void;
}) {
  const [entries, setEntries] = useState<EpisodeEntry[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [pending, setPending] = useState<Record<string, string>>({});
  const generation = useRef(0);
  const mounted = useRef(false);
  const fetching = useRef(false);
  const currentFilter = useRef(includeArchived);
  const refresh = useCallback(async () => {
    const request = ++generation.current;
    fetching.current = true;
    try {
      const result = await api.episodes(currentFilter.current);
      if (mounted.current && request === generation.current) {setEntries(result); setError("");}
    } catch (reason) {
      if (mounted.current && request === generation.current) setError(String(reason));
    } finally {
      if (mounted.current && request === generation.current) {fetching.current = false; setLoading(false);}
    }
  }, []);
  useEffect(() => {
    mounted.current = true; currentFilter.current = includeArchived; setLoading(true);
    void refresh();
    function resume() {if (!document.hidden && !fetching.current) void refresh();}
    window.addEventListener('focus', resume);
    document.addEventListener('visibilitychange', resume);
    return () => {mounted.current = false; generation.current++; window.removeEventListener('focus', resume); document.removeEventListener('visibilitychange', resume);};
  }, [includeArchived, refresh]);
  const hasActiveJobs = entries.some(entry => entry.jobs.some(job => ['queued','running'].includes(job.status)));
  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = window.setInterval(() => {if (!document.hidden && !fetching.current) void refresh();}, 5000);
    return () => window.clearInterval(timer);
  }, [hasActiveJobs, refresh]);
  async function act(entry: EpisodeEntry, action: "download" | "metadata" | "archive") {
    generation.current++;
    setPending(current => ({ ...current, [entry.id]: action }));
    setError("");
    try {
      if (action === "download") await api.downloadEpisode(entry.id);
      if (action === "metadata") await api.refreshEpisodeMetadata(entry.id);
      if (action === "archive") await api.archiveEpisode(entry.id, !entry.archived);
      if (mounted.current) await refresh();
    }
    catch (reason) {if (mounted.current) {fetching.current = false; setError(String(reason));}}
    finally {if (mounted.current) setPending(current => { const next = { ...current }; delete next[entry.id]; return next; });}
  }
  const search = query.trim().toLocaleLowerCase("pt-BR");
  const filtered = entries.filter(entry => `${entry.title} ${entry.channel_name || ""} ${entry.primary_subject}`.toLocaleLowerCase("pt-BR").includes(search));
  return <section className="simple-page enter" aria-busy={loading}>
    <div className="simple-title"><span>DAYON NEWS · BIBLIOTECA LOCAL</span><h1>Seus episódios. Seus cortes.</h1><p>Os vídeos e resultados ficam salvos neste computador. Abra uma seleção anterior ou aproveite novos trechos do mesmo episódio.</p></div>
    <div className="library-toolbar">
      <label className="library-search">Buscar episódio ou canal<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Título, canal ou participante" /></label>
      <label><input type="checkbox" checked={includeArchived} onChange={event => setIncludeArchived(event.target.checked)} /> Mostrar arquivados</label>
    </div>
    {error && <p role="alert">{error}</p>}
    {loading && <p role="status">Carregando episódios…</p>}
    {!loading && !entries.length && !error && <p>Adicione um episódio pelo botão Novo episódio. Ele ficará disponível aqui.</p>}
    {!loading && !!entries.length && !filtered.length && <p>Nenhum episódio corresponde à busca.</p>}
    <div className="library-grid">{filtered.map(entry => {
      const activeJob = entry.jobs.find(job => ["queued", "running"].includes(job.status));
      const disabled = busy || !!activeJob || !!pending[entry.id];
      return <article className="simple-card" key={entry.id}>
        <EpisodeThumbnail entry={entry} />
        {entry.archived && <small>Arquivado</small>}
        {entry.channel_name && <p className="episode-channel">{entry.channel_url ? <a href={entry.channel_url} target="_blank" rel="noreferrer">{entry.channel_name}</a> : entry.channel_name}</p>}
        <h3>{entry.title}</h3><p>Foco: {entry.primary_subject}{entry.participant_count ? ` · ${entry.participant_count} participantes` : ""}</p>
        <small>{entry.source ? `Vídeo salvo · ${(entry.source_bytes / 1e9).toFixed(2)} GB` : "Vídeo ainda não baixado"} · {entry.renders.length} vídeos gerados</small>
        {entry.metadata_error && <p role="alert">Dados do YouTube: {entry.metadata_error}</p>}
        {pending[entry.id] === "metadata" && <p role="status">Buscando título, canal e capa…</p>}
        {entry.url && <div className="library-metadata"><button className="btn secondary" disabled={!!pending[entry.id]} onClick={() => void act(entry, "metadata")}>{entry.metadata_updated_at ? "Atualizar dados do YouTube" : "Buscar dados do YouTube"}</button></div>}
        {activeJob && <p role="status">{activeJob.message} · {Math.round(activeJob.progress || 0)}%</p>}
        {!activeJob && entry.jobs[0]?.status === "failed" && <p role="alert">Última tentativa: {entry.jobs[0].error || entry.jobs[0].message}</p>}
        <div className="library-actions"><button className="btn primary" disabled={disabled || !!entry.archived} onClick={() => onUse(entry)}>{entry.source ? "Gerar novos cortes" : "Preparar cortes"}</button>
        {!entry.source && <button className="btn secondary" disabled={disabled || !!entry.archived} onClick={() => void act(entry, "download")}>{pending[entry.id] === "download" ? "Preparando download…" : "Baixar e guardar"}</button>}
        <button className="btn secondary" disabled={disabled} onClick={() => void act(entry, "archive")}>{pending[entry.id] === "archive" ? "Salvando…" : entry.archived ? "Restaurar episódio" : "Arquivar"}</button></div>
        {entry.source && <VoiceSelector entry={entry} disabled={disabled || !!entry.archived}/>}
        {!!entry.runs.length && <details><summary>Seleções e processamentos ({entry.runs.length})</summary>{entry.runs.map(run => <button className="btn secondary" key={run.id} disabled={busy} onClick={() => onOpen(run)}>{run.status === "ready_for_review" ? "Abrir cortes" : "Ver processamento"} · {new Date(run.created_at || "").toLocaleString("pt-BR")} · {run.message}</button>)}</details>}
        {!!entry.renders.length && <details><summary>Vídeos salvos ({entry.renders.length})</summary>{entry.renders.map(render => <p key={render.id}><a href={api.renderMediaUrl(render.project_id, render.id)} target="_blank" rel="noreferrer">{render.title} · {Math.round(render.duration || 0)}s</a></p>)}</details>}
      </article>;
    })}</div>
  </section>;
}
