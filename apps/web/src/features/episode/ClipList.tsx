import type { SuggestedClip } from "../../api";
import { formatClock } from "../../app/humanize";
import { clipDuration, clipKey } from "../../editor/clips";
import { Icon, Segmented } from "../../ui";

export type ClipSort = "score" | "order";

const scoreOf = (clip: SuggestedClip) => clip.scores?.total ?? 0;
const scoreLabel = (value: number) => (Number.isInteger(value) ? String(value) : value.toFixed(1));

export function sortClips(clips: SuggestedClip[], sort: ClipSort) {
  return [...clips].sort(sort === "score"
    ? (a, b) => scoreOf(b) - scoreOf(a) || a.rank - b.rank
    : (a, b) => a.start_second - b.start_second);
}

function extrasOf(clip: SuggestedClip) {
  const parts: string[] = [];
  if (clip.pieces && clip.pieces.length > 1) parts.push(`${clip.pieces.length} trechos`);
  if (clip.lead_trimmed) parts.push("começo limpo");
  return parts;
}

export function ClipList({ clips, notes, selected, onToggle, sort, onSort }: {
  clips: SuggestedClip[]; notes?: string; selected: string[];
  onToggle: (key: string) => void; sort: ClipSort; onSort: (sort: ClipSort) => void;
}) {
  return <section className="clips" aria-label="Cortes sugeridos">
    <div className="clips-tools">
      <span className="clips-count">{clips.length === 1 ? "1 corte sugerido" : `${clips.length} cortes sugeridos`}</span>
      <div className="grow" />
      <Segmented label="Ordenar cortes" value={sort} onChange={onSort} options={[{ value: "score", label: "Nota" }, { value: "order", label: "Ordem no episódio" }]} />
    </div>
    {notes && <details className="ainote"><summary><Icon name="spark" small />Notas da IA sobre esta seleção</summary><p>{notes}</p></details>}
    <ul className="rows">
      {sortClips(clips, sort).map(clip => {
        const key = clipKey(clip);
        const on = selected.includes(key);
        const headline = clip.headline || clip.title;
        const score = scoreOf(clip);
        return <li key={key} className={`row${on ? " act" : ""}`}>
          <button type="button" role="checkbox" aria-checked={on} aria-label={`Selecionar ${headline}`} className={`ck${on ? " on" : ""}`} onClick={() => onToggle(key)}>
            <span className="ckb"><Icon name="check" small /></span>
          </button>
          <div className="rmain" onClick={() => onToggle(key)}>
            <span className={`score mono${score >= 9 ? " hi" : ""}`} title="Nota da IA">{scoreLabel(score)}</span>
            <div className="rb"><span className="rt">{headline}</span><span className="rm"><span className="mono">{formatClock(clip.start_second)}</span>{extrasOf(clip).map(part => <span key={part}>{`· ${part}`}</span>)}</span></div>
            <span className="dur mono">{clipDuration(clip.estimated_duration)}</span>
          </div>
        </li>;
      })}
    </ul>
  </section>;
}
