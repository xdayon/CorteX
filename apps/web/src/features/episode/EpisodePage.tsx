import { useEffect, useState } from "react";
import { isRunReady } from "../../app/humanize";
import { usePageTitle } from "../../app/usePageTitle";
import { clipKey } from "../../editor/clips";
import { editorPath, Link, navigate } from "../../router";
import { Button, Chip, EmptyState, Icon, Skeleton, useToast } from "../../ui";
import { EpisodeMenu } from "../episodes/EpisodeMenu";
import { EpisodeThumb } from "../episodes/EpisodeThumb";
import { RunOptionsDialog } from "../episodes/RunOptionsDialog";
import { VoiceDialog } from "../episodes/VoiceDialog";
import { episodeStatus } from "../episodes/episodeStatus";
import { defaultRunOptions, optionsFromRun, type RunOptions } from "../episodes/runOptions";
import { useCreateEpisodeRun } from "../episodes/useCreateEpisodeRun";
import { useEpisodeActions } from "../episodes/useEpisodeActions";
import { useEpisodeCatalog } from "../episodes/useEpisodeCatalog";
import { ClipList, type ClipSort } from "./ClipList";
import { ProcessingPanel } from "./ProcessingPanel";
import { SelectionBar } from "./SelectionBar";
import { useSuggestion } from "./useSuggestion";
import "./episode.css";

export function EpisodePage({ id }: { id: string }) {
  const toast = useToast();
  const catalog = useEpisodeCatalog(true, 3000);
  const actions = useEpisodeActions(catalog.refresh, catalog.invalidate);
  const create = useCreateEpisodeRun(() => { void catalog.refresh(); });
  const entry = catalog.entries.find(item => item.id === id);
  const run = entry?.runs[0];
  const readyRun = entry?.runs.find(isRunReady);
  const suggestion = useSuggestion(readyRun);
  const [selected, setSelected] = useState<string[]>([]);
  const [sort, setSort] = useState<ClipSort>("score");
  const [dialog, setDialog] = useState<RunOptions | null>(null);
  usePageTitle(entry ? `${entry.title} · CorteX` : "Episódio · CorteX");
  useEffect(() => setSelected([]), [readyRun?.id]);

  if (!entry) {
    if (catalog.loading) return <div className="ep-page"><Skeleton height={54} /></div>;
    return <div className="ep-page"><EmptyState title={catalog.error ? "Não foi possível carregar o episódio" : "Episódio não encontrado"}
      action={<Link to="/" className="btn">Voltar aos episódios</Link>}>{catalog.error || "Ele pode ter sido removido da biblioteca."}</EmptyState></div>;
  }

  const status = episodeStatus(entry);
  const clips = suggestion.selection?.clips ?? [];
  const chosen = clips.filter(clip => selected.includes(clipKey(clip)));
  const totalSeconds = chosen.reduce((sum, clip) => sum + clip.estimated_duration, 0);
  const processing = status.kind === "processing";

  function openOptions() {
    setDialog({ ...defaultRunOptions(), ...optionsFromRun(run, entry!.primary_subject) });
  }
  async function generate(options: RunOptions) {
    setDialog(null);
    const failure = await create.start({ kind: "episode", entry: entry! }, options);
    if (failure) toast(failure, "bad");
  }
  const toggle = (key: string) => setSelected(current => current.includes(key) ? current.filter(item => item !== key) : [...current, key]);

  return <div className="ep-page">
    <Link to="/" className="back"><Icon name="arrowLeft" small />Episódios</Link>
    <header className="ep-head">
      <EpisodeThumb entry={entry} className="ep-thumb" />
      <div className="ep-info">
        <h1>{entry.title}</h1>
        <div className="ep-meta">
          {entry.channel_name && <span>{entry.channel_name}</span>}
          <Chip tone={entry.archived ? "neutral" : status.tone}>{entry.archived ? "Arquivado" : status.label}</Chip>
          {entry.subject_reference?.speaker && <Chip tone="ok">Voz confirmada</Chip>}
          {status.readyRuns > 0 && <Chip>{status.readyRuns === 1 ? "1 seleção" : `${status.readyRuns} seleções`}</Chip>}
        </div>
      </div>
      <div className="ep-actions">
        <Button icon="spark" disabled={create.busy || processing || !!entry.archived} onClick={openOptions}>Gerar nova seleção</Button>
        <EpisodeMenu entry={entry} pending={!!actions.pending[entry.id]} onAction={action => void actions.act(entry, action)} onVoice={() => actions.setVoiceFor(entry)} />
      </div>
    </header>

    {create.busy && <div className="notice" role="status"><p>{create.message}</p></div>}
    {catalog.error && <div className="notice bad" role="alert"><p>{catalog.error}</p></div>}

    {run && processing && <ProcessingPanel run={run} onCancelled={() => void catalog.refresh()} />}
    {!processing && run?.status === "failed" && <div className="notice bad" role="alert"><p>{status.error ?? "A última seleção não terminou."} Use “Gerar nova seleção” para tentar de novo.</p></div>}
    {!processing && run?.status === "cancelled" && <div className="notice" role="status"><p>A última seleção foi cancelada.</p></div>}
    {!run && !create.busy && <EmptyState title="Nenhuma seleção ainda" action={<Button variant="primary" icon="spark" disabled={!!entry.archived} onClick={openOptions}>Gerar seleção de cortes</Button>}>
      A IA lê a conversa e sugere os melhores momentos para virar cortes.
    </EmptyState>}

    {readyRun && <>
      {suggestion.loading && <div className="clips-loading" role="status" aria-label="Carregando cortes">{[0, 1, 2, 3].map(index => <Skeleton key={index} height={52} />)}</div>}
      {suggestion.error && <div className="notice bad" role="alert"><p>Não foi possível carregar os cortes: {suggestion.error}</p></div>}
      {suggestion.selection && <ClipList clips={clips} notes={suggestion.selection.selection_notes} selected={selected} onToggle={toggle} sort={sort} onSort={setSort} />}
    </>}

    <SelectionBar count={chosen.length} totalSeconds={totalSeconds} onClear={() => setSelected([])}
      onEdit={() => readyRun && navigate(editorPath(entry.id, readyRun.id, chosen.map(clipKey)))} />
    {dialog && <RunOptionsDialog title="Nova seleção de cortes" confirmLabel="Gerar seleção" options={dialog} onClose={() => setDialog(null)} onSave={next => void generate(next)} />}
    {actions.voiceFor && <VoiceDialog entry={actions.voiceFor} onClose={() => actions.setVoiceFor(null)} />}
  </div>;
}
