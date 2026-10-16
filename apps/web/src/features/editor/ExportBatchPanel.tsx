import type { ExportBatch, ExportItem } from "../../api";
import { api } from "../../api";
import { shortError } from "../../app/humanize";
import { hookOf } from "../../editor/clips";
import type { SuggestedClip } from "../../api";
import { JobProgress } from "../../JobProgress";
import { PublicationPanel, usePublications } from "../../PublicationPanel";
import { Link, episodePath } from "../../router";
import { Button, buttonClass, Chip, type ChipTone } from "../../ui";

const STATUS: Record<ExportItem["status"], [string, ChipTone]> = {
  queued: ["Na fila", "neutral"], planning: ["Montando", "ac"], camera_planning: ["Montando", "ac"], rendering: ["Exportando", "ac"],
  succeeded: ["Pronto", "ok"], failed: ["Falhou", "bad"], cancelled: ["Cancelado", "neutral"],
};

function downloadName(title: string) {
  const safe = title.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-");
  return `${safe || "corte"}.mp4`;
}

/** Baixa os MP4 em sequência; o navegador pode pedir uma vez para permitir vários downloads. */
async function downloadAll(items: Array<{ title: string; url: string }>) {
  for (const item of items) {
    const link = document.createElement("a");
    link.href = item.url; link.download = downloadName(item.title);
    document.body.appendChild(link); link.click(); link.remove();
    await new Promise(resolve => setTimeout(resolve, 600));
  }
}

export function ExportBatchPanel({ batch, clips, episodeId, busy, progress, onCancel, onRetry }: {
  batch: ExportBatch; clips: SuggestedClip[]; episodeId: string; busy: boolean; progress: number; onCancel: () => void; onRetry: () => void;
}) {
  const publications = usePublications();
  const open = ["queued", "running"].includes(batch.status);
  const done = batch.items.filter(item => item.status === "succeeded" && item.render_artifact_id);
  const outputs = done.map(item => ({ title: item.title, url: api.renderMediaUrl(batch.project_id, item.render_artifact_id!) }));
  return <section className="batch card" aria-label="Exportação">
    <div className="batch-head">
      <h2>Exportação</h2>
      <span className="batch-count">{done.length} de {batch.items.length} prontos</span>
      <div className="grow" />
      {open && <Button size="sm" disabled={busy} onClick={onCancel}>Cancelar exportação</Button>}
      {batch.items.some(item => item.status === "failed") && <Button size="sm" disabled={busy} onClick={onRetry}>Repetir falhas</Button>}
      {outputs.length > 1 && <Button size="sm" icon="download" onClick={() => void downloadAll(outputs)}>Baixar todos ({outputs.length} MP4)</Button>}
      <Link to={episodePath(episodeId)} className={buttonClass("ghost", "sm")}>Voltar ao episódio</Link>
    </div>
    {(busy || open) && <JobProgress job={null} history={[]} progress={progress} busy={busy} label={busy ? "Exportando os cortes" : "Exportação em andamento"} />}
    <ul className="batch-items">
      {batch.items.map(item => {
        const [label, tone] = STATUS[item.status] ?? [item.status, "neutral" as ChipTone];
        const url = item.render_artifact_id ? api.renderMediaUrl(batch.project_id, item.render_artifact_id) : "";
        return <li key={item.id} className="batch-item">
          <div className="batch-row">
            <span className="batch-title">{item.title}</span>
            <Chip tone={tone}>{label}</Chip>
            {item.status === "succeeded" && item.render_artifact_id && <>
              <a className={buttonClass("secondary", "sm")} href={url} download={downloadName(item.title)}>Baixar MP4</a>
              <a className={buttonClass("ghost", "sm")} href={api.renderSubtitlesUrl(batch.project_id, item.render_artifact_id)} download>Baixar SRT</a>
            </>}
          </div>
          {item.status === "failed" && <p className="batch-error">{shortError(item.error, "Esta exportação falhou.")}</p>}
          {item.status === "succeeded" && item.render_artifact_id && <PublicationPanel projectId={batch.project_id} artifactId={item.render_artifact_id}
            publication={publications.items[item.render_artifact_id]} hookText={hookOf(clips, item.clip_key)} onSaved={publications.save} onDeleted={publications.remove} />}
        </li>;
      })}
    </ul>
  </section>;
}
