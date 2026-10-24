import { useEffect, useRef, useState } from "react";
import { dismissTask, useEpisodeTask } from "../../app/tasks";
import { usePageTitle } from "../../app/usePageTitle";
import { Link } from "../../router";
import { Button, Chip, EmptyState, Icon, Skeleton, useToast } from "../../ui";
import { EpisodeMenu } from "../episodes/EpisodeMenu";
import { EpisodeThumb } from "../episodes/EpisodeThumb";
import { RunOptionsDialog } from "../episodes/RunOptionsDialog";
import { CamerasDialog } from "../episodes/CamerasDialog";
import { VoiceDialog } from "../episodes/VoiceDialog";
import { episodeStatus } from "../episodes/episodeStatus";
import { defaultRunOptions, optionsFromRun, type RunOptions } from "../episodes/runOptions";
import { useCreateEpisodeRun } from "../episodes/useCreateEpisodeRun";
import { useEpisodeActions } from "../episodes/useEpisodeActions";
import { useEpisodeCatalog } from "../episodes/useEpisodeCatalog";
import { EpisodeClips } from "./EpisodeClips";
import { EpisodeSteps } from "./EpisodeSteps";
import { ProcessingPanel } from "./ProcessingPanel";
import "./episode.css";

export function EpisodePage({ id }: { id: string }) {
  const toast = useToast();
  const catalog = useEpisodeCatalog(true, 3000);
  const actions = useEpisodeActions(catalog.refresh, catalog.invalidate);
  const create = useCreateEpisodeRun();
  const task = useEpisodeTask(id);
  const entry = catalog.entries.find(item => item.id === id);
  const run = entry?.runs[0];
  const [dialog, setDialog] = useState<RunOptions | null>(null);
  usePageTitle(entry ? `${entry.title} · CorteX` : "Episódio · CorteX");
  const taskStatus = task?.status;
  const refreshed = useRef(taskStatus);
  useEffect(() => {
    if (taskStatus === refreshed.current) return;
    refreshed.current = taskStatus;
    void catalog.refresh();
  }, [taskStatus, catalog]);

  if (!entry) {
    if (catalog.loading) return <div className="ep-page"><Skeleton height={54} /></div>;
    return <div className="ep-page"><EmptyState title={catalog.error ? "Não foi possível carregar o episódio" : "Episódio não encontrado"}
      action={<Link to="/" className="btn">Voltar aos episódios</Link>}>{catalog.error || "Ele pode ter sido removido da biblioteca."}</EmptyState></div>;
  }

  const status = episodeStatus(entry);
  const taskRunning = task?.status === "running";
  const processing = status.kind === "processing" || taskRunning;
  const refreshKey = `${entry.runs.map(item => `${item.id}:${item.status}`).join(",")}|${taskStatus ?? ""}`;

  function openOptions() {
    setDialog({ ...defaultRunOptions(), ...optionsFromRun(run, entry!.primary_subject) });
  }
  async function generate(options: RunOptions) {
    setDialog(null);
    const failure = await create.start({ kind: "episode", entry: entry! }, options);
    if (failure) toast(failure, "bad");
  }

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
        <EpisodeMenu entry={entry} pending={!!actions.pending[entry.id]} onAction={action => void actions.act(entry, action)} onVoice={() => actions.setVoiceFor(entry)} onCameras={() => actions.setCamerasFor(entry)} />
      </div>
    </header>

    <EpisodeSteps entry={entry} processing={processing} onDownload={() => void actions.act(entry, "download")} onGenerate={openOptions}
      onVoice={() => actions.setVoiceFor(entry)} onCameras={() => actions.setCamerasFor(entry)} reloadKey={`${!!actions.voiceFor}${!!actions.camerasFor}${entry.source?.id ?? ""}`} />

    {create.busy && <div className="notice" role="status"><p>{create.message}</p></div>}
    {catalog.error && <div className="notice bad" role="alert"><p>{catalog.error}</p></div>}

    {processing && <ProcessingPanel run={run && status.kind === "processing" ? run : undefined} task={taskRunning ? task : undefined} onCancelled={() => void catalog.refresh()} />}
    {task?.status === "failed" && <div className="notice bad" role="alert"><p>{task.error}</p><Button size="sm" variant="ghost" onClick={() => dismissTask(task.id)}>Dispensar</Button></div>}
    {!processing && run?.status === "failed" && <div className="notice bad" role="alert"><p>{status.error ?? "A última seleção não terminou."} Use “Gerar nova seleção” para tentar de novo.</p></div>}
    {!processing && run?.status === "cancelled" && <div className="notice" role="status"><p>A última seleção foi cancelada.</p></div>}
    {!run && !create.busy && !processing && <EmptyState title="Nenhuma seleção ainda" action={<Button variant="primary" icon="spark" disabled={!!entry.archived} onClick={openOptions}>Gerar seleção de cortes</Button>}>
      A IA lê a conversa e sugere os melhores momentos para virar cortes.
    </EmptyState>}

    <EpisodeClips episodeId={entry.id} refreshKey={refreshKey} />
    {dialog && <RunOptionsDialog title="Nova seleção de cortes" confirmLabel="Gerar seleção" options={dialog} onClose={() => setDialog(null)} onSave={next => void generate(next)} />}
    {actions.voiceFor && <VoiceDialog entry={actions.voiceFor} onClose={() => actions.setVoiceFor(null)} />}
    {actions.camerasFor && <CamerasDialog entry={actions.camerasFor} onClose={() => actions.setCamerasFor(null)} />}
  </div>;
}
