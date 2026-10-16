import { useRef, type PointerEvent } from "react";
import { rulerTicks, seekFromPointer, tickLabel, type CaptionBlock, type Trecho } from "./timeline";

const pct = (seconds: number, total: number) => `${total > 0 ? Math.min(Math.max(seconds / total, 0), 1) * 100 : 0}%`;

export function Timeline({ total, time, onSeek, trechos, joins, captions, headline }: {
  total: number; time: number; onSeek: (seconds: number) => void; trechos: Trecho[]; joins: number[]; captions: CaptionBlock[];
  headline: { on: boolean; seconds: number | null };
}) {
  const track = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const seekAt = (event: PointerEvent) => {
    const box = track.current?.getBoundingClientRect();
    if (box) onSeek(seekFromPointer(event.clientX, box.left, box.width, total));
  };
  const headlineEnd = headline.seconds === null ? total : Math.min(headline.seconds, total);

  return <div className="tl" aria-label="Linha do tempo do corte">
    <div className="tl-n"><span>Trechos</span><span>Legenda</span><span>Manchete</span></div>
    <div ref={track} className="tl-m" role="slider" tabIndex={0} aria-label="Posição na linha do tempo" aria-valuemin={0} aria-valuemax={Math.round(total)}
      aria-valuenow={Math.round(time)} aria-valuetext={`${tickLabel(time)} de ${tickLabel(total)}`}
      onPointerDown={event => { dragging.current = true; try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* synthetic pointer */ } seekAt(event); }}
      onPointerMove={event => { if (dragging.current) seekAt(event); }}
      onPointerUp={event => { dragging.current = false; try { event.currentTarget.releasePointerCapture(event.pointerId); } catch { /* not captured */ } }}
      onPointerCancel={() => { dragging.current = false; }}>
      <div className="ruler mono" aria-hidden="true">
        {rulerTicks(total).map(tick => <span key={tick} style={{ left: pct(tick, total) }}>{tickLabel(tick)}</span>)}
      </div>
      <div className="lane" data-lane="trechos">
        {trechos.map(block => <span key={block.start} className={`tseg${block.teaser ? " tz" : ""}`} data-testid="trecho"
          style={{ left: pct(block.start, total), width: `${((block.end - block.start) / Math.max(total, 1e-6)) * 100}%` }}>{block.label}</span>)}
        {joins.map(join => <span key={join} className="join" title="Junção entre trechos" style={{ left: pct(join, total) }} />)}
      </div>
      <div className="lane" data-lane="legenda">
        {captions.map((block, index) => <span key={index} data-testid="caption-block" className={`cb${block.current ? " cur" : block.doubtful ? " warn" : ""}`}
          style={{ left: pct(block.start, total), width: `${((block.end - block.start) / Math.max(total, 1e-6)) * 100}%` }} />)}
      </div>
      <div className="lane" data-lane="manchete">
        {headline.on && total > 0 && <span className="tseg hb" style={{ left: 0, width: pct(headlineEnd, total) }}>{headline.seconds === null ? "Corte todo" : `${Math.round(headlineEnd)} s`}</span>}
      </div>
      <div className="ph" data-testid="playhead" style={{ left: pct(time, total) }} />
    </div>
  </div>;
}
