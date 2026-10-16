import { useState } from "react";
import { api, type ClipRow, type ExportItem } from "../../api";
import { clipDuration } from "../../editor/clips";
import { episodePath, Link } from "../../router";
import { buttonClass, Chip, Icon, Kbd, type ChipTone } from "../../ui";
import { headlineOf, newestExport, rowId } from "../clips/clipModel";
import type { PreviewView } from "./previewQueue";

const EXPORT_LABELS: Record<ExportItem["status"], [string, ChipTone]> = {
  queued: ["Exportando", "ac"], planning: ["Exportando", "ac"], camera_planning: ["Exportando", "ac"], rendering: ["Exportando", "ac"],
  succeeded: ["Exportado", "ok"], failed: ["Falhou", "bad"], cancelled: ["Cancelado", "neutral"],
};

/** The rail chip: the export status of the clip when it has one, else how far its draft preview is. */
export function readiness(preview: PreviewView | undefined, exported: ExportItem["status"] | undefined): [string, ChipTone] {
  if (exported) return EXPORT_LABELS[exported] ?? [exported, "neutral"];
  switch (preview?.phase) {
    case "planning": case "rendering": return [`Preparando prévia ${Math.round(preview.progress)}%`, "ac"];
    case "ready": return ["Prévia pronta", "ok"];
    case "stale": return ["Prévia desatualizada", "warn"];
    case "error": return ["Falhou", "bad"];
    default: return ["Na fila", "neutral"];
  }
}

function Poster({ row }: { row: ClipRow }) {
  const latest = newestExport(row);
  const [failed, setFailed] = useState(false);
  const src = latest && !failed ? api.posterUrl(row.project_id, latest.render_artifact_id) : api.sourcePreviewUrl(row.project_id, row.source_asset_id, row.start_second + 1);
  return <img className="ed-thumb" src={src} alt="" loading="lazy" onError={() => setFailed(true)} />;
}

export function ClipRail({ episodeId, rows, previews, focusedKey, onFocus, customized, exportStatus }: {
  episodeId: string; rows: ClipRow[]; previews: Record<string, PreviewView>; focusedKey: string; onFocus: (key: string) => void;
  customized: (key: string) => boolean; exportStatus: Record<string, ExportItem["status"]>;
}) {
  return <aside className="rail" aria-label="Cortes no editor">
    <div className="rail-h"><span className="lbl">No editor</span><span className="num">{rows.length}</span></div>
    <nav className="rail-list" aria-label="Cortes selecionados">
      {rows.map(row => {
        const key = rowId(row);
        const preview = previews[key];
        const [label, tone] = readiness(preview, exportStatus[key]);
        const seconds = preview?.timeline ?? row.clip.estimated_duration;
        return <button key={key} type="button" className={`clip${key === focusedKey ? " on" : ""}`} aria-pressed={key === focusedKey} onClick={() => onFocus(key)}>
          <Poster row={row} />
          <span className="clip-b">
            <span className="clip-t">{headlineOf(row)}</span>
            <span className="clip-m mono">{clipDuration(seconds)} · {row.piece_count <= 1 ? "1 trecho" : `${row.piece_count} trechos`}</span>
            <span className="clip-chips">
              <Chip tone={tone}>{label}</Chip>
              {customized(key) && <span className="custom" title="Este corte tem ajustes só dele"><i />Personalizado</span>}
            </span>
          </span>
        </button>;
      })}
    </nav>
    <Link to={episodePath(episodeId)} className={buttonClass("ghost", "md", "add")}><Icon name="plus" small />Adicionar cortes</Link>
    <div className="rail-foot">
      <span className="lbl">Atalhos</span>
      <span className="kr"><Kbd>Espaço</Kbd> reproduzir / pausar</span>
      <span className="kr"><Kbd>J</Kbd><Kbd>L</Kbd> voltar / avançar 5 s</span>
      <span className="kr"><Kbd>←</Kbd><Kbd>→</Kbd> ±1 s · <Kbd>Shift</Kbd> ±5 s</span>
      <span className="kr"><Kbd>↑</Kbd><Kbd>↓</Kbd> corte anterior / próximo</span>
      <span className="kr"><Kbd>Ctrl</Kbd><Kbd>Enter</Kbd> exportar</span>
    </div>
  </aside>;
}
