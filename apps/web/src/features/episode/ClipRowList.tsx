import "../clips/clips.css";
import { useEffect, useRef } from "react";
import type { ClipRow } from "../../api";
import { clipDuration } from "../../editor/clips";
import { Chip, Icon } from "../../ui";
import { headlineOf, rowId, shortDate, STATUS_LABELS, STATUS_TONES } from "../clips/clipModel";

const scoreLabel = (value: number | null) => value == null ? "–" : Number.isInteger(value) ? String(value) : value.toFixed(1);

export function ClipRowList({ rows, activeId, selected, onActivate, onToggle, showRunDate, dimDiscarded, topScore }: {
  rows: ClipRow[]; topScore: (row: ClipRow) => boolean; activeId: string; selected: string[]; onActivate: (id: string) => void; onToggle: (id: string) => void;
  showRunDate: boolean; dimDiscarded: boolean;
}) {
  const active = useRef<HTMLLIElement>(null);
  useEffect(() => { active.current?.scrollIntoView?.({ block: "nearest" }); }, [activeId]);
  return <ul className="rows" aria-label="Lista de cortes">
    {rows.map(row => {
      const id = rowId(row);
      const on = selected.includes(id);
      const isActive = id === activeId;
      const headline = headlineOf(row);
      const meta = [clipDuration(row.duration), row.piece_count > 1 ? `${row.piece_count} trechos` : "", showRunDate ? shortDate(row.run_created_at) : ""].filter(Boolean);
      return <li key={id} ref={isActive ? active : undefined} className={`row${isActive ? " act" : ""}${dimDiscarded && row.status === "discarded" ? " dim" : ""}`} aria-current={isActive ? "true" : undefined}>
        <button type="button" role="checkbox" aria-checked={on} aria-label={`Selecionar ${headline}`} className={`ck${on ? " on" : ""}`} onClick={() => onToggle(id)}>
          <span className="ckb"><Icon name="check" small /></span>
        </button>
        <button type="button" className="rmain" onClick={() => onActivate(id)}>
          <span className={`score mono${topScore(row) ? " hi" : ""}`} title="Nota da IA">{scoreLabel(row.score)}</span>
          <span className="rb"><span className="rt">{headline}</span><span className="rm">{meta.map((part, index) => <span key={index} className={index === 0 ? "mono" : undefined}>{index ? `· ${part}` : part}</span>)}</span></span>
          {row.status !== "new" && <Chip tone={STATUS_TONES[row.status]}>{STATUS_LABELS[row.status]}</Chip>}
        </button>
      </li>;
    })}
  </ul>;
}
