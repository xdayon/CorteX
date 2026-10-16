import { forwardRef, useState } from "react";
import { type ClipRow, type DiscardReason } from "../../api";
import { formatClock } from "../../app/humanize";
import { clipDuration } from "../../editor/clips";
import { Button, buttonClass, Chip } from "../../ui";
import { DiscardMenu } from "../clips/DiscardMenu";
import { SavedToggle } from "../clips/SavedToggle";
import { headlineOf, newestExport, REASON_LABELS, shortDate, STATUS_LABELS, STATUS_TONES } from "../clips/clipModel";
import { ExportPlayer } from "./ExportPlayer";
import { QuickPreviewPlayer } from "./QuickPreviewPlayer";
import type { PlayerHandle } from "./SourcePiecesPlayer";

type Handlers = {
  onApprove: () => void; onDiscard: (reason: DiscardReason) => void; onEdit: () => void;
  onToggleSaved: () => void; onPublish: () => void;
};

const jump = (seconds: number) => `junção · pula ${seconds.toFixed(1).replace(".", ",")} s`;

export const ClipDetail = forwardRef<PlayerHandle, { row: ClipRow; topScore: boolean; discardRef: React.Ref<{ open: () => void }> } & Handlers>(function ClipDetail({ row, topScore, discardRef, ...on }, player) {
  const latest = newestExport(row);
  const [view, setView] = useState<"export" | "source">(latest ? "export" : "source");
  const showExport = !!latest && view === "export";
  const clip = row.clip;
  const pieces = clip.pieces ?? [];
  const approved = row.state?.decision === "approved";
  const saved = !!row.state?.saved;
  const others = (row.headline_options ?? []).filter(option => option !== row.headline);
  const publication = row.publication;

  return <article className="cd">
    <div className="cd-player">
      {latest && <div className="seg cd-view" role="radiogroup" aria-label="Versão do vídeo">
        <button type="button" role="radio" aria-checked={showExport} onClick={() => setView("export")}>Exportado</button>
        <button type="button" role="radio" aria-checked={!showExport} onClick={() => setView("source")}>Original</button>
      </div>}
      {showExport
        ? <ExportPlayer ref={player} projectId={row.project_id} item={latest} />
        : <QuickPreviewPlayer ref={player} row={row} />}
    </div>
    <div className="cd-side">
      <h2 className="cd-title">{headlineOf(row)}</h2>
      <div className="cd-chips">
        {row.status !== "new" && <Chip tone={STATUS_TONES[row.status]}>{STATUS_LABELS[row.status]}</Chip>}
        {row.status === "discarded" && row.state?.discard_reason && <Chip>{REASON_LABELS[row.state.discard_reason]}</Chip>}
        {row.score != null && <Chip tone={topScore ? "ac" : "neutral"}>Nota <span className="mono">{row.score}</span></Chip>}
        <Chip><span className="mono">{clipDuration(row.duration)}</span></Chip>
        {row.piece_count > 1 && <Chip>{row.piece_count} trechos</Chip>}
        {clip.lead_trimmed && <Chip>começo limpo</Chip>}
        {clip.teaser && <Chip>teaser</Chip>}
      </div>
      <div className="cd-actions">
        <Button aria-pressed={approved} onClick={on.onApprove}>{approved ? "Aprovado" : "Aprovar"}</Button>
        <DiscardMenu ref={discardRef} onPick={on.onDiscard} trigger={({ ref, ...props }) => <button ref={ref} type="button" className="btn" {...props}>Descartar</button>} />
        <Button variant="primary" onClick={on.onEdit}>Editar e exportar</Button>
      </div>
      {latest && <div className="cd-actions">
        <a className={buttonClass("secondary", "sm")} href={latest.media_url} download>Baixar MP4</a>
        <a className={buttonClass("secondary", "sm")} href={latest.subtitles_url} download>Baixar SRT</a>
        <SavedToggle saved={saved} onClick={on.onToggleSaved} />
        <Button size="sm" onClick={on.onPublish}>{publication ? "Editar publicação" : "Registrar publicação"}</Button>
      </div>}
    </div>

    <div className="cd-body">
      {clip.reasoning && <section><h3>Por que funciona</h3><p>{clip.reasoning}</p></section>}
      {row.warnings.length > 0 && <section><h3>Avisos</h3><div className="notice"><div>{row.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div></div></section>}
      {others.length > 0 && <section><h3>Outras manchetes</h3><ul className="alts">{others.map(option => <li key={option}>{option}</li>)}</ul></section>}
      {clip.teaser && <section><h3>Teaser</h3><p className="teaser">“{clip.teaser.text}”</p></section>}
      {pieces.length > 0 && <section><h3>Texto do corte</h3><div className="txt">
        {pieces.map((piece, index) => <div key={index}>
          {index > 0 && <div className="junc">{jump(piece.start - pieces[index - 1].end)}</div>}
          <p><span className="mono txt-time">{formatClock(piece.start)}</span> {piece.text}</p>
        </div>)}
      </div></section>}
      <section><h3>Histórico</h3><ul className="hist">
        <li>Sugerido na seleção de {shortDate(row.run_created_at)}</li>
        {row.exports.map(item => <li key={item.render_artifact_id}>Exportado em {shortDate(item.exported_at)}</li>)}
        {row.state?.saved && row.state.saved_at && <li>Salvo em {shortDate(row.state.saved_at)}</li>}
        {publication?.published && <li>Postado{publication.published_on ? ` em ${shortDate(publication.published_on)}` : ""}
          {publication.platforms.length > 0 && ` · ${publication.platforms.map(name => name === "instagram" ? "Instagram" : "TikTok").join(", ")}`}
          {publication.url && <> · <a href={publication.url} target="_blank" rel="noreferrer">abrir publicação</a></>}</li>}
      </ul></section>
    </div>
    <p className="cd-hint">↑ ↓ navegar · Espaço reproduzir · X selecionar · A aprovar · D descartar · S salvo · E editar</p>
  </article>;
});
