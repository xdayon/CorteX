import { api, type ExportBatch, type ExportItem } from "../../api";
import { shortError } from "../../app/humanize";
import { JobProgress } from "../../JobProgress";
import { Button, Chip, type ChipTone } from "../../ui";
import { downloadAll, downloadName } from "../clips/clipModel";
import { isOpen } from "./useExportBatches";

const STATUS: Record<ExportItem["status"], [string, ChipTone]> = {
  queued: ["Na fila", "neutral"], planning: ["Montando", "ac"], camera_planning: ["Montando", "ac"], rendering: ["Exportando", "ac"],
  succeeded: ["Pronto", "ok"], failed: ["Falhou", "bad"], cancelled: ["Cancelado", "neutral"],
};

/** Progress and downloads of an export: one batch per run, listed together. */
export function ExportBatchPanel({ batches, busy, progress, onCancel, onRetry }: {
  batches: ExportBatch[]; busy: boolean; progress: number; onCancel: () => void; onRetry: () => void;
}) {
  const open = batches.some(isOpen);
  const items = batches.flatMap(batch => batch.items.map(item => ({ batch, item })));
  const done = items.filter(({ item }) => item.status === "succeeded" && item.render_artifact_id);
  const outputs = done.map(({ batch, item }) => ({ title: item.title, url: api.renderMediaUrl(batch.project_id, item.render_artifact_id!) }));
  return <section className="batch" aria-label="Exportação">
    <div className="batch-head">
      <span className="batch-count">{done.length} de {items.length} prontos</span>
      <div className="grow" />
      {open && <Button size="sm" disabled={busy} onClick={onCancel}>Cancelar exportação</Button>}
      {items.some(({ item }) => item.status === "failed") && <Button size="sm" disabled={busy} onClick={onRetry}>Repetir falhas</Button>}
      {outputs.length > 1 && <Button size="sm" icon="download" onClick={() => void downloadAll(outputs)}>Baixar todos ({outputs.length} MP4)</Button>}
    </div>
    {(busy || open) && <JobProgress job={null} history={[]} progress={progress} busy={busy} label={busy ? "Exportando os cortes" : "Exportação em andamento"} />}
    <ul className="batch-items">
      {items.map(({ batch, item }) => {
        const [label, tone] = STATUS[item.status] ?? [item.status, "neutral" as ChipTone];
        const url = item.render_artifact_id ? api.renderMediaUrl(batch.project_id, item.render_artifact_id) : "";
        return <li key={item.id} className="batch-item">
          <div className="batch-row">
            <span className="batch-title">{item.title}</span>
            <Chip tone={tone}>{label}</Chip>
            {item.status === "succeeded" && item.render_artifact_id && <>
              <a className="btn sm" href={url} download={downloadName(item.title)}>Baixar MP4</a>
              <a className="btn ghost sm" href={api.renderSubtitlesUrl(batch.project_id, item.render_artifact_id)} download>Baixar SRT</a>
            </>}
          </div>
          {item.status === "failed" && <p className="batch-error">{shortError(item.error, "Esta exportação falhou.")}</p>}
        </li>;
      })}
    </ul>
  </section>;
}
