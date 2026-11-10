import { useState } from "react";
import { api, type OtherExport } from "../../api";
import { clipDuration } from "../../editor/clips";
import { buttonClass, Icon } from "../../ui";
import { shortDate } from "../clips/clipModel";

function OldExport({ item }: { item: OtherExport }) {
  const [playing, setPlaying] = useState(false);
  return <li className="old-item">
    <div className="old-media">
      {playing
        ? <video src={item.media_url} controls autoPlay playsInline aria-label={`Vídeo ${item.title}`} />
        : <button type="button" className="old-poster" aria-label={`Reproduzir ${item.title}`} onClick={() => setPlaying(true)}>
            <img src={api.posterUrl(item.project_id, item.render_artifact_id)} alt="" loading="lazy" /><span className="spp-play"><Icon name="play" /></span>
          </button>}
    </div>
    <div className="old-info">
      <b>{item.title}</b>
      <span className="old-meta">{shortDate(item.exported_at)}{item.duration_seconds ? <> · <span className="mono">{clipDuration(item.duration_seconds)}</span></> : null}</span>
      <div className="cd-actions">
        <a className={buttonClass("secondary", "sm")} href={item.media_url} download>Baixar MP4</a>
        <a className={buttonClass("ghost", "sm")} href={item.subtitles_url} download>Baixar SRT</a>
      </div>
    </div>
  </li>;
}

export function OldExports({ items }: { items: OtherExport[] }) {
  return <details className="old card">
    <summary>Vídeos antigos ({items.length})</summary>
    <p className="field-help">Exportações que não pertencem a nenhuma seleção atual.</p>
    <ul className="old-list">{items.map(item => <OldExport key={item.render_artifact_id} item={item} />)}</ul>
  </details>;
}
