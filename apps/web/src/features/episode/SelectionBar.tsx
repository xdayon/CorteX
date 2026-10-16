import { formatClock } from "../../app/humanize";
import { Button } from "../../ui";

export function SelectionBar({ count, totalSeconds, onClear, onEdit }: { count: number; totalSeconds: number; onClear: () => void; onEdit: () => void }) {
  if (!count) return null;
  return <div className="selbar" role="region" aria-label="Cortes selecionados">
    <span className="count">{count === 1 ? "1 corte selecionado" : `${count} cortes selecionados`}</span>
    <span className="sub mono">{formatClock(totalSeconds)} no total</span>
    <Button variant="ghost" size="sm" onClick={onClear}>Limpar</Button>
    <Button variant="primary" onClick={onEdit}>Editar e exportar →</Button>
  </div>;
}
