import { useEffect, useState } from "react";
import { api, type ClipCounts, type EpisodeEntry } from "../../api";
import { plural } from "../../app/humanize";
import { EmptyState, Icon, Segmented, Skeleton, useToast } from "../../ui";
import { EpisodeCard } from "./EpisodeCard";
import { NewEpisodeComposer } from "./NewEpisodeComposer";
import { CamerasDialog } from "./CamerasDialog";
import { VoiceDialog } from "./VoiceDialog";
import { episodeStatus, exportedCount } from "./episodeStatus";
import { defaultRunOptions } from "./runOptions";
import { useCreateEpisodeRun } from "./useCreateEpisodeRun";
import { useEpisodeActions } from "./useEpisodeActions";
import { useEpisodeCatalog } from "./useEpisodeCatalog";
import "./episodes.css";

type Filter = "all" | "cuts" | "undownloaded" | "archived";

const hasCuts = (entry: EpisodeEntry) => episodeStatus(entry).readyRuns > 0 || entry.renders.length > 0;

export function EpisodesPage() {
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const catalog = useEpisodeCatalog(filter === "archived");
  const actions = useEpisodeActions(catalog.refresh, catalog.invalidate);
  const create = useCreateEpisodeRun();
  const [summary, setSummary] = useState<Record<string, ClipCounts>>({});
  const signature = catalog.entries.map(entry => `${entry.id}:${entry.runs.map(run => run.status).join("")}:${entry.renders.length}`).join("|");
  useEffect(() => {
    const controller = new AbortController();
    Promise.resolve(api.clipsSummary(controller.signal)).then(result => { if (!controller.signal.aborted && result?.episodes) setSummary(result.episodes); }).catch(() => undefined);
    return () => controller.abort();
  }, [signature]);

  async function prepare(entry: EpisodeEntry) {
    const failure = await create.start({ kind: "episode", entry }, { ...defaultRunOptions(), primarySubject: entry.primary_subject || "Dayon" });
    if (failure) toast(failure, "bad");
  }

  const live = catalog.entries.filter(entry => !entry.archived);
  const scope = filter === "archived" ? catalog.entries.filter(entry => entry.archived) : live;
  const byFilter = scope.filter(entry => filter === "cuts" ? hasCuts(entry) : filter === "undownloaded" ? !entry.source : true);
  const search = query.trim().toLocaleLowerCase("pt-BR");
  const shown = byFilter.filter(entry => `${entry.title} ${entry.channel_name || ""} ${entry.primary_subject}`.toLocaleLowerCase("pt-BR").includes(search));
  const videos = scope.reduce((sum, entry) => sum + (summary[entry.id] ? exportedCount(summary[entry.id]) : entry.renders.length), 0);
  const counts = filter === "archived" ? {} : { all: live.length, cuts: live.filter(hasCuts).length, undownloaded: live.filter(entry => !entry.source).length };

  return <div className="wrap">
    <NewEpisodeComposer />
    <section className="library" aria-label="Biblioteca de episódios" aria-busy={catalog.loading}>
      <div className="lib-head">
        <h1>Episódios</h1>
        <span className="lib-sub">{catalog.loading ? "Carregando…" : `${plural(scope.length, "episódio", "episódios")} · ${plural(videos, "vídeo gerado", "vídeos gerados")}`}</span>
        <div className="grow" />
        <label className="search"><Icon name="search" small /><input type="search" value={query} placeholder="Buscar título ou canal" aria-label="Buscar episódio" onChange={event => setQuery(event.target.value)} /></label>
        <Segmented label="Filtrar episódios" value={filter} onChange={setFilter} options={[
          { value: "all", label: "Todos", count: counts.all }, { value: "cuts", label: "Com cortes", count: counts.cuts },
          { value: "undownloaded", label: "Não baixados", count: counts.undownloaded }, { value: "archived", label: "Arquivados" },
        ]} />
      </div>
      {catalog.error && <div className="notice bad" role="alert"><p>Não foi possível carregar os episódios: {catalog.error}</p></div>}
      {catalog.loading && !catalog.entries.length && <div className="grid" role="status" aria-label="Carregando episódios">
        {[0, 1, 2].map(index => <Skeleton key={index} height={250} style={{ borderRadius: 14 }} />)}
      </div>}
      {!catalog.loading && !catalog.error && !scope.length && <EmptyState title={filter === "archived" ? "Nenhum episódio arquivado" : "Nenhum episódio ainda"}>
        {filter === "archived" ? "Episódios arquivados aparecem aqui." : "Cole o link de um episódio acima. Ele fica salvo aqui, com todas as seleções de cortes."}
      </EmptyState>}
      {!catalog.loading && !!scope.length && !shown.length && <EmptyState title="Nenhum episódio encontrado">Ajuste a busca ou o filtro.</EmptyState>}
      <div className="grid">
        {shown.map(entry => <EpisodeCard key={entry.id} entry={entry} counts={summary[entry.id]} pending={actions.pending[entry.id]} busy={create.busy}
          onAction={action => void actions.act(entry, action)} onVoice={() => actions.setVoiceFor(entry)} onCameras={() => actions.setCamerasFor(entry)} onPrepare={() => void prepare(entry)} />)}
      </div>
    </section>
    {actions.voiceFor && <VoiceDialog entry={actions.voiceFor} onClose={() => actions.setVoiceFor(null)} />}
    {actions.camerasFor && <CamerasDialog entry={actions.camerasFor} onClose={() => actions.setCamerasFor(null)} />}
  </div>;
}
