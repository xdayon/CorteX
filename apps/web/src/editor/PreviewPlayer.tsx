import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { api, type OverlayPreview, type SafeZones } from "../api";
import type { DraftPreview } from "./useDraftPreview";

export type PlayerHandle = { seek: (seconds: number) => void };

const EMPTY_ASS = "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n";
const ZONE_LABELS: Record<string, string> = {
  top_bar: "Barra superior", right_actions: "Botões laterais", bottom_meta: "Legenda do post",
};
const PLATFORM_LABELS: Record<string, string> = { instagram_reels: "Instagram", tiktok: "TikTok" };

type Engine = {
  ready: Promise<void>; renderer: { setTrack: (ass: string) => Promise<unknown> };
  resize: (forceRepaint?: boolean) => Promise<void>; destroy: () => Promise<void>;
};

function clock(seconds: number) {
  const value = Math.max(0, seconds);
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}

function refineLabel(preview: DraftPreview) {
  const refine = preview.refine;
  if (!refine) return "";
  if (refine.status === "accepted") return "Enxugado pela IA";
  const label = refine.status === "skipped" ? "Enxugamento da IA não aplicado" : "Enxugamento da IA recusado";
  return refine.reason ? `${label}: ${refine.reason}` : label;
}

const TEASER_DROP_REASONS: Record<string, string> = {
  max_duration: "o corte ficaria acima da duração máxima",
  teaser_removed_by_refine: "o enxugamento da IA removeu a frase do teaser",
  outside_source: "a frase está fora da mídia disponível",
  no_speech: "não há fala na frase escolhida",
  invalid_teaser: "o trecho do teaser é inválido",
};

function teaserLabel(preview: DraftPreview) {
  const reason = preview.teaserDropped;
  if (!reason) return "";
  return `Teaser de abertura não aplicado: ${TEASER_DROP_REASONS[reason] ?? reason}`;
}

export function PreviewPlayer({ preview, overlay, onTime, ref }: {
  preview: DraftPreview; overlay: OverlayPreview | null; onTime?: (seconds: number) => void; ref?: Ref<PlayerHandle>;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const engineRef = useRef<Engine | null>(null);
  const assRef = useRef(EMPTY_ASS);
  const [engine, setEngine] = useState<Engine | null>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [zonesOn, setZonesOn] = useState(false);
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
      setTime(video.currentTime); onTime?.(video.currentTime);
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
      // `fonts` are loaded up front and matched by the family names inside the
      // files (exactly like libass with fontsdir in the export). availableFonts
      // alone is looked up by "family + weight" keys and misses "Montserrat Black".
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
      .then(() => {
        if (cancelled) return;
        nudgeFrame();
        return engine.resize(true);
      })
      .catch(reason => { if (!cancelled) setEngineError(`Legenda da prévia indisponível: ${reason instanceof Error ? reason.message : reason}`); });
    return () => { cancelled = true; };
  }, [engine, overlay]);

  function repaint() {
    void engineRef.current?.resize(true).catch(() => undefined);
  }

  function nudgeFrame() {
    // JASSUB draws on video frame callbacks; a paused, freshly loaded video never
    // presents a new frame, so re-seek to the same time to get one.
    const video = videoRef.current;
    if (video && video.paused && video.readyState >= 2) video.currentTime = video.currentTime;
  }

  useEffect(() => {
    if (!zonesOn || zones) return;
    const controller = new AbortController();
    void api.safeZones(controller.signal).then(setZones)
      .catch(reason => { if (!controller.signal.aborted) setZonesError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [zonesOn, zones]);

  function toggle() {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) void video.play()?.catch(() => undefined); else video.pause();
  }
  function scrub(value: number) {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = value; setTime(value); onTime?.(value);
  }

  const total = overlay?.timeline_duration_seconds ?? (duration || preview.timelineSeconds || 0);
  const busy = preview.phase === "planning" || preview.phase === "rendering";
  const stale = preview.phase === "rendering" && hasVideo;
  const refineText = [refineLabel(preview), teaserLabel(preview)].filter(Boolean).join(" · ");
  return <div className="preview-player">
    <div className="phone-stage">
      <div className="phone-screen">
        <video
          ref={videoRef} playsInline preload="auto" muted={muted} src={preview.videoUrl ?? undefined}
          aria-label="Prévia do corte"
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
          onLoadedMetadata={event => setDuration(event.currentTarget.duration || 0)}
          onLoadedData={() => { nudgeFrame(); repaint(); }} onSeeked={repaint}
          onTimeUpdate={event => { setTime(event.currentTarget.currentTime); onTime?.(event.currentTarget.currentTime); }}
          onClick={toggle}
        />
        {zonesOn && zones && <div className="safe-zones" aria-hidden="true">
          {Object.entries(zones.platforms).flatMap(([platform, rects]) => Object.entries(rects).map(([name, rect]) =>
            <div key={`${platform}-${name}`} className={`safe-zone ${platform}`}
              style={{ left: `${rect[0] * 100}%`, top: `${rect[1] * 100}%`, width: `${(rect[2] - rect[0]) * 100}%`, height: `${(rect[3] - rect[1]) * 100}%` }}>
              <span>{PLATFORM_LABELS[platform] ?? platform} · {ZONE_LABELS[name] ?? name}</span>
            </div>))}
        </div>}
        {busy && !hasVideo && <div className="preview-overlay" role="status">
          <b>{preview.phase === "planning" ? "Montando o corte" : "Gerando a prévia"}</b>
          <progress max={100} value={preview.progress} aria-label="Progresso da prévia"/>
          <small>{Math.round(preview.progress)}% · isso leva alguns segundos</small>
        </div>}
        {preview.phase === "error" && <div className="preview-overlay error" role="alert">
          <b>Não foi possível gerar a prévia</b><small>{preview.error}</small>
          <button type="button" className="btn secondary" onClick={preview.retry}>Tentar de novo</button>
        </div>}
        {stale && <span className="stale-badge" role="status">Prévia desatualizada · atualizando</span>}
      </div>
    </div>
    <div className="player-controls">
      <button type="button" className="btn secondary" onClick={toggle} disabled={!hasVideo}>{playing ? "Pausar" : "Reproduzir"}</button>
      <div className="scrubber">
        <input type="range" min={0} max={total || 1} step={0.05} value={Math.min(time, total || 1)} disabled={!hasVideo}
          aria-label="Posição no vídeo" onChange={event => scrub(Number(event.target.value))}/>
        {total > 0 && overlay?.junctions.map(junction => <i key={junction} className="junction-tick" style={{ left: `${(junction / total) * 100}%` }}/>)}
      </div>
      <output className="player-clock">{clock(time)} / {clock(total)}</output>
      <button type="button" className="btn secondary" aria-pressed={muted} onClick={() => setMuted(value => !value)}>{muted ? "Som desligado" : "Som ligado"}</button>
    </div>
    <button type="button" className="btn secondary zones-toggle" aria-pressed={zonesOn} onClick={() => setZonesOn(value => !value)}>Áreas do Instagram/TikTok</button>
    {zonesError && <p className="preview-note warn" role="status">Não foi possível carregar as áreas: {zonesError}</p>}
    {engineError && <p className="preview-note warn" role="status">{engineError}</p>}
    {preview.belowMinimum && <p className="preview-note warn" role="status">Este corte ficou abaixo da duração mínima pedida{preview.timelineSeconds ? ` (${preview.timelineSeconds.toFixed(0)} s)` : ""}.</p>}
    {refineText && <p className="preview-note" role="status">{refineText}</p>}
  </div>;
}
