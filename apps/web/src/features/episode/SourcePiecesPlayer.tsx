import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { formatClock } from "../../app/humanize";
import { Icon } from "../../ui";

export type Piece = { start: number; end: number };
export type PlayerHandle = { toggle: () => void };

const EPSILON = 0.04;

/** What to do when the media clock reaches `time` while playing piece `index`. */
export function stepPieces(pieces: Piece[], index: number, time: number) {
  const piece = pieces[index];
  if (time < piece.end - EPSILON) return { index, seek: null as number | null, ended: false };
  const next = pieces[index + 1];
  if (!next) return { index, seek: null as number | null, ended: true };
  return { index: index + 1, seek: Math.abs(next.start - time) < 0.05 ? null : next.start, ended: false };
}

/** Piece containing a virtual (concatenated) time, with the matching media time. */
export function locate(pieces: Piece[], virtual: number) {
  let offset = 0;
  for (let index = 0; index < pieces.length; index++) {
    const length = pieces[index].end - pieces[index].start;
    if (virtual < offset + length || index === pieces.length - 1) {
      const within = Math.min(Math.max(virtual - offset, 0), length);
      return { index, media: pieces[index].start + within };
    }
    offset += length;
  }
  return { index: 0, media: pieces[0].start };
}

/** Plays a clip's pieces straight from the source video, jumping piece to piece without a render. */
export const SourcePiecesPlayer = forwardRef<PlayerHandle, { src: string; pieces: Piece[]; label?: string; poster?: string; portrait?: boolean; onMetadata?: (duration: number) => void }>(function SourcePiecesPlayer({ src, pieces, label = "Prévia do corte original", poster, portrait, onMetadata }, handle) {
  const video = useRef<HTMLVideoElement>(null);
  const index = useRef(0);
  const frame = useRef(0);
  const [playing, setPlaying] = useState(false);
  const [virtual, setVirtual] = useState(0);
  const offsets = useMemo(() => pieces.reduce<number[]>((all, piece, i) => [...all, i ? all[i - 1] + pieces[i - 1].end - pieces[i - 1].start : 0], []), [pieces]);
  const total = pieces.length ? offsets[pieces.length - 1] + pieces[pieces.length - 1].end - pieces[pieces.length - 1].start : 0;

  const sync = useCallback((media: number) => {
    const piece = pieces[index.current];
    setVirtual(offsets[index.current] + Math.min(Math.max(media - piece.start, 0), piece.end - piece.start));
  }, [pieces, offsets]);

  const stop = useCallback(() => { cancelAnimationFrame(frame.current); frame.current = 0; }, []);

  const tick = useCallback(() => {
    const element = video.current;
    if (!element) return;
    const result = stepPieces(pieces, index.current, element.currentTime);
    index.current = result.index;
    if (result.seek !== null) element.currentTime = result.seek;
    if (result.ended) {
      element.pause(); setPlaying(false); setVirtual(total); return;
    }
    sync(result.seek ?? element.currentTime);
    frame.current = requestAnimationFrame(tick);
  }, [pieces, sync, total]);

  const play = useCallback(() => {
    const element = video.current;
    if (!element || !pieces.length) return;
    const piece = pieces[index.current];
    if (element.currentTime < piece.start - 0.1 || element.currentTime >= piece.end - EPSILON) {
      if (index.current === pieces.length - 1 && element.currentTime >= piece.end - EPSILON) index.current = 0;
      element.currentTime = pieces[index.current].start;
    }
    void Promise.resolve(element.play()).catch(() => setPlaying(false));
    setPlaying(true);
    stop(); frame.current = requestAnimationFrame(tick);
  }, [pieces, stop, tick]);

  const pause = useCallback(() => { video.current?.pause(); stop(); setPlaying(false); }, [stop]);
  const toggle = useCallback(() => (playing ? pause() : play()), [playing, pause, play]);
  useImperativeHandle(handle, () => ({ toggle }), [toggle]);
  useEffect(() => stop, [stop]);

  const seekVirtual = useCallback((time: number) => {
    const element = video.current;
    if (!element || !pieces.length) return;
    const target = locate(pieces, Math.min(Math.max(time, 0), total));
    index.current = target.index;
    element.currentTime = target.media;
    setVirtual(offsets[target.index] + target.media - pieces[target.index].start);
  }, [pieces, offsets, total]);

  function onBarClick(event: MouseEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    if (box.width > 0) seekVirtual(((event.clientX - box.left) / box.width) * total);
  }
  function onBarKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowRight") { event.preventDefault(); seekVirtual(virtual + 5); }
    if (event.key === "ArrowLeft") { event.preventDefault(); seekVirtual(virtual - 5); }
  }

  function onLoaded() {
    if (video.current && pieces.length) video.current.currentTime = pieces[0].start;
    if (video.current && Number.isFinite(video.current.duration)) onMetadata?.(video.current.duration);
  }

  return <div className={portrait ? "spp spp-portrait" : "spp"}>
    <div className="spp-stage" onClick={toggle}>
      <video ref={video} src={src} poster={poster} preload="metadata" playsInline aria-label={label} onLoadedMetadata={onLoaded} onEnded={pause} />
      {!playing && <button type="button" className="spp-play" aria-label="Reproduzir" onClick={event => { event.stopPropagation(); play(); }}><Icon name="play" /></button>}
    </div>
    <div className="spp-controls">
      <button type="button" className="ibtn sm" aria-label={playing ? "Pausar" : "Reproduzir"} onClick={toggle}>
        {playing ? <svg className="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14M16 5v14" /></svg> : <Icon name="play" />}
      </button>
      <div className="spp-bar" role="slider" tabIndex={0} aria-label="Posição no corte" aria-valuemin={0} aria-valuemax={Math.round(total)} aria-valuenow={Math.round(virtual)}
        aria-valuetext={`${formatClock(virtual)} de ${formatClock(total)}`} onClick={onBarClick} onKeyDown={onBarKey}>
        {pieces.map((piece, i) => {
          const length = piece.end - piece.start;
          const fill = Math.min(Math.max((virtual - offsets[i]) / length, 0), 1) * 100;
          return <span key={i} className="spp-seg" data-testid="segment" style={{ flexGrow: length }}><i style={{ width: `${fill}%` }} /></span>;
        })}
      </div>
      <span className="spp-time mono">{formatClock(virtual)} / {formatClock(total)}</span>
    </div>
  </div>;
});
