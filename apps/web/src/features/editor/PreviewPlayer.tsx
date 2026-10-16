import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { api, type OverlayPreview, type SafeZones } from "../../api";
import type { PreviewView } from "./previewQueue";

export type PlayerHandle = { seek: (seconds: number) => void; toggle: () => void; skip: (delta: number) => void };

const EMPTY_ASS = "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n";
const ZONE_LABELS: Record<string, string> = { top_bar: "Barra superior", right_actions: "Botões laterais", bottom_meta: "Legenda do post" };
const PLATFORM_LABELS: Record<string, string> = { instagram_reels: "Instagram", tiktok: "TikTok" };

type Engine = {
  ready: Promise<void>; renderer: { setTrack: (ass: string) => Promise<unknown> };
  resize: (forceRepaint?: boolean) => Promise<void>; destroy: () => Promise<void>;
};

const QUEUE_COPY: Record<string, string> = {
  queued: "Na fila para preparar", planning: "Montando o corte", rendering: "Gerando a prévia",
};

/** The 9:16 draft video with the backend `.ass` drawn over it by JASSUB, so captions and headline match the export. */
export function PreviewPlayer({ preview, overlay, muted, zonesOn, onTime, onPlaying, onDuration, onRetry, ref }: {
  preview: PreviewView; overlay: OverlayPreview | null; muted: boolean; zonesOn: boolean;
  onTime: (seconds: number) => void; onPlaying: (playing: boolean) => void; onDuration: (seconds: number) => void;
  onRetry: () => void; ref?: Ref<PlayerHandle>;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const engineRef = useRef<Engine | null>(null);
  const assRef = useRef(EMPTY_ASS);
  const frame = useRef(0);
  const [engine, setEngine] = useState<Engine | null>(null);
  const [zones, setZones] = useState<SafeZones | null>(null);
  const [zonesError, setZonesError] = useState("");
  const [engineError, setEngineError] = useState("");
  const fonts = overlay?.fonts ?? [];
  const fontKey = fonts.map(font => `${font.ass_name}=${font.url}`).join("|");
  const hasVideo = !!preview.videoUrl;
  if (overlay) assRef.current = overlay.ass_text;

  useImperativeHandle(ref, () => ({
    seek(seconds: number) {
      const video = videoRef.current;
      if (!video) return;
      video.currentTime = Math.max(0, seconds);
      onTime(video.currentTime);
    },
    toggle() {
      const video = videoRef.current;
      if (!video || !video.src) return;
      if (video.paused) void video.play()?.catch(() => undefined); else video.pause();
    },
    skip(delta: number) {
      const video = videoRef.current;
      if (!video) return;
      const limit = Number.isFinite(video.duration) ? video.duration : Infinity;
      video.currentTime = Math.min(Math.max(0, video.currentTime + delta), limit);
      onTime(video.currentTime);
    },
  }), [onTime]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !hasVideo || !fontKey) return;
    let cancelled = false;
    let created: Engine | null = null;
    const urls = fonts.map(font => new URL(font.url, window.location.href).href);
    const availableFonts = Object.fromEntries(fonts.map((font, index) => [font.ass_name, urls[index]]));
    setEngineError("");
    void import("jassub").then(({ default: JASSUB }) => {
      if (cancelled) return;
      // `fonts` are loaded up front and matched by the family names inside the files (exactly like libass with
      // fontsdir in the export); availableFonts alone is looked up by "family + weight" and misses "Montserrat Black".
      created = new JASSUB({ video, subContent: assRef.current, fonts: urls, availableFonts }) as unknown as Engine;
      engineRef.current = created;
      setEngine(created);
    }).catch(reason => {
      if (!cancelled) setEngineError(`Não foi possível iniciar a legenda da prévia: ${reason instanceof Error ? reason.message : reason}`);
    });
    return () => {
      cancelled = true;
      if (created) {
        void created.destroy().catch(() => undefined);
        if (engineRef.current === created) engineRef.current = null;
      }
      setEngine(null);
    };
    // Recreated only when the font set (or the video presence) changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fontKey, hasVideo]);

  useEffect(() => {
    if (!engine || !overlay) return;
    let cancelled = false;
    void engine.ready.then(() => cancelled ? undefined : engine.renderer.setTrack(overlay.ass_text))
      // A paused video produces no new frames, so repaint the current one.
      .then(() => { if (cancelled) return; nudgeFrame(); return engine.resize(true); })
      .catch(reason => { if (!cancelled) setEngineError(`Legenda da prévia indisponível: ${reason instanceof Error ? reason.message : reason}`); });
    return () => { cancelled = true; };
  }, [engine, overlay]);

  function repaint() { void engineRef.current?.resize(true).catch(() => undefined); }

  function nudgeFrame() {
    // JASSUB draws on video frame callbacks; a paused, freshly loaded video never presents a new frame, so re-seek to get one.
    const video = videoRef.current;
    if (video && video.paused && video.readyState >= 2) video.currentTime = video.currentTime;
  }

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  function follow() {
    cancelAnimationFrame(frame.current);
    const tick = () => {
      const video = videoRef.current;
      if (!video || video.paused) return;
      onTime(video.currentTime);
      frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
  }

  useEffect(() => {
    if (!zonesOn || zones) return;
    const controller = new AbortController();
    void api.safeZones(controller.signal).then(setZones)
      .catch(reason => { if (!controller.signal.aborted) setZonesError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [zonesOn, zones]);

  const waiting = !hasVideo && preview.phase !== "error";
  return <div className="vid">
    <video
      ref={videoRef} playsInline preload="auto" muted={muted} src={preview.videoUrl ?? undefined} aria-label="Prévia do corte"
      onPlay={() => { onPlaying(true); follow(); }} onPause={() => { onPlaying(false); cancelAnimationFrame(frame.current); }}
      onEmptied={() => { onPlaying(false); cancelAnimationFrame(frame.current); }}
      onLoadedMetadata={event => onDuration(event.currentTarget.duration || 0)}
      onLoadedData={() => { nudgeFrame(); repaint(); }} onSeeked={repaint}
      onTimeUpdate={event => onTime(event.currentTarget.currentTime)}
      onClick={() => { const video = videoRef.current; if (video?.paused) void video.play()?.catch(() => undefined); else video?.pause(); }}
    />
    {zonesOn && zones && <div className="safe-zones" aria-hidden="true">
      {Object.entries(zones.platforms).flatMap(([platform, rects]) => Object.entries(rects).map(([name, rect]) =>
        <div key={`${platform}-${name}`} className={`safe-zone ${platform}`}
          style={{ left: `${rect[0] * 100}%`, top: `${rect[1] * 100}%`, width: `${(rect[2] - rect[0]) * 100}%`, height: `${(rect[3] - rect[1]) * 100}%` }}>
          <span>{PLATFORM_LABELS[platform] ?? platform} · {ZONE_LABELS[name] ?? name}</span>
        </div>))}
    </div>}
    {waiting && <div className="preview-overlay" role="status">
      <b>{QUEUE_COPY[preview.phase] ?? "Preparando"}</b>
      {preview.phase !== "queued" && <progress max={100} value={preview.progress} aria-label="Progresso da prévia" />}
      <small>{preview.phase === "queued" ? "Outro corte está sendo preparado antes deste." : `${Math.round(preview.progress)}% · isso leva alguns segundos`}</small>
    </div>}
    {preview.phase === "error" && <div className="preview-overlay error" role="alert">
      <b>Não foi possível gerar a prévia</b><small>{preview.error}</small>
      <button type="button" className="btn" onClick={onRetry}>Tentar de novo</button>
    </div>}
    {preview.phase === "stale" || (hasVideo && (preview.phase === "planning" || preview.phase === "rendering")) ? <span className="stale-badge" role="status">Prévia desatualizada · atualizando</span> : null}
    {(zonesError || engineError) && <small className="vid-note" role="status">{zonesError ? `Não foi possível carregar as áreas: ${zonesError}` : engineError}</small>}
  </div>;
}
