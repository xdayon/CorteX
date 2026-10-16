import "./clips.css";
import type { ClipRow, DiscardReason } from "../../api";
import { formatClock } from "../../app/humanize";
import { Button } from "../../ui";
import { DiscardMenu } from "./DiscardMenu";

/** Floating bar for the selected clips; shared by the episode page and the board. */
export function BulkBar({ rows, onApprove, onDiscard, onSave, onDownload, onEdit, onClear }: {
  rows: ClipRow[]; onApprove: () => void; onDiscard: (reason: DiscardReason) => void; onSave: () => void;
  onDownload: () => void; onEdit: () => void; onClear: () => void;
}) {
  if (!rows.length) return null;
  const exported = rows.filter(row => row.exports.length);
  const seconds = rows.reduce((sum, row) => sum + (row.duration || 0), 0);
  return <div className="selbar" role="region" aria-label="Cortes selecionados">
    <span className="count">{rows.length === 1 ? "1 selecionado" : `${rows.length} selecionados`}</span>
    <span className="sub mono">{formatClock(seconds)}</span>
    <Button size="sm" onClick={onApprove}>Aprovar</Button>
    <DiscardMenu up onPick={onDiscard} trigger={({ ref, ...props }) => <button ref={ref} type="button" className="btn sm" {...props}>Descartar</button>} />
    <Button size="sm" disabled={!exported.length} onClick={onSave}>Marcar salvos</Button>
    <Button size="sm" icon="download" disabled={!exported.length} onClick={onDownload}>Baixar ({exported.length} MP4)</Button>
    <Button variant="ghost" size="sm" onClick={onClear}>Limpar</Button>
    <Button variant="primary" onClick={onEdit}>Editar e exportar →</Button>
  </div>;
}
