import type { ClipCounts, EpisodeEntry } from "../../api";
import { Link, episodePath } from "../../router";
import { Button, Chip, ProgressBar } from "../../ui";
import { EpisodeMenu } from "./EpisodeMenu";
import { EpisodeThumb } from "./EpisodeThumb";
import { episodeStats, episodeStatus } from "./episodeStatus";
import type { EpisodeAction } from "./useEpisodeActions";

export function EpisodeCard({ entry, counts, pending, busy, onAction, onVoice, onPrepare }: {
  entry: EpisodeEntry; counts?: ClipCounts; pending?: EpisodeAction; busy: boolean;
  onAction: (action: EpisodeAction) => void; onVoice: () => void; onPrepare: () => void;
}) {
  const status = episodeStatus(entry);
  const href = episodePath(entry.id);
  const needsAction = !entry.archived && (status.kind === "attention" || (status.kind !== "processing" && status.readyRuns === 0));
  return <article className="ecard">
    <Link to={href} className="ecard-thumb" aria-label={`Abrir ${entry.title}`} tabIndex={-1}>
      <EpisodeThumb entry={entry} />
      <Chip tone={status.tone} className="ecard-chip">{entry.archived ? "Arquivado" : status.label}</Chip>
    </Link>
    <div className="ecard-body">
      <Link to={href} className="ecard-title">{entry.title}</Link>
      <span className="ecard-channel">{entry.channel_name || (entry.url ? "Canal não identificado" : "Arquivo local")}</span>
      {status.kind === "processing" && <div className="ecard-prog">
        <span className="ecard-stage">{status.stage}</span>
        <ProgressBar value={status.percent ?? 0} label={`Andamento de ${entry.title}`} />
      </div>}
      {status.kind === "attention" && <span className="ecard-error">{status.error}</span>}
      {entry.metadata_error && <span className="ecard-error">Dados do YouTube: {entry.metadata_error}</span>}
      <div className="ecard-foot">
        <span className="ecard-stats">{episodeStats(entry, status.readyRuns, counts)}</span>
        <div className="grow" />
        {needsAction && <Button size="sm" disabled={busy || !!pending} onClick={onPrepare}>{status.kind === "attention" ? "Tentar de novo" : "Preparar cortes"}</Button>}
        <EpisodeMenu entry={entry} pending={!!pending} onAction={onAction} onVoice={onVoice} />
      </div>
    </div>
  </article>;
}
