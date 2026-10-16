import { useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from "react";
import { api, type CaptionCorrection, type ClipRow, type EditPlanSegment, type OverlayPreview, type RenderSettings } from "../../api";
import { clipDuration } from "../../editor/clips";
import { Chip, Icon, IconButton } from "../../ui";
import { PreviewPlayer, type PlayerHandle } from "./PreviewPlayer";
import type { PreviewView } from "./previewQueue";
import { Timeline } from "./Timeline";
import { buildTrechos, captionBlocks } from "./timeline";
import { formatBlockTime } from "./timecode";

const TEASER_DROP_REASONS: Record<string, string> = {
  max_duration: "o corte ficaria acima da duração máxima",
  teaser_removed_by_refine: "o enxugamento da IA removeu a frase do teaser",
  outside_source: "a frase está fora da mídia disponível",
  no_speech: "não há fala na frase escolhida",
  invalid_teaser: "o trecho do teaser é inválido",
};

const LIVE: Record<string, [string, string]> = {
  ready: ["Prévia atualizada · rascunho", "ok"], stale: ["Prévia desatualizada", "warn"], error: ["Falha ao gerar a prévia", "bad"],
};

export function Stage({ row, preview, overlay, overlayError, settings, corrections, time, onTime, onRetry, ref }: {
  row: ClipRow; preview: PreviewView; overlay: OverlayPreview | null; overlayError: string; settings: RenderSettings;
  corrections: CaptionCorrection[]; time: number; onTime: (seconds: number) => void; onRetry: () => void; ref?: Ref<PlayerHandle>;
}) {
  const player = useRef<PlayerHandle>(null);
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [muted, setMuted] = useState(false);
  const [zonesOn, setZonesOn] = useState(false);
  const [segments, setSegments] = useState<EditPlanSegment[]>([]);
  useImperativeHandle(ref, () => ({
    seek: seconds => player.current?.seek(seconds), toggle: () => player.current?.toggle(), skip: delta => player.current?.skip(delta),
  }), []);

  const planId = preview.planId;
  useEffect(() => {
    setSegments([]);
    if (!planId) return;
    const controller = new AbortController();
    void Promise.resolve(api.editPlan(row.project_id, planId, controller.signal))
      .then(plan => { if (!controller.signal.aborted && plan?.document?.segments) setSegments(plan.document.segments); })
      .catch(() => undefined);
    return () => controller.abort();
  }, [row.project_id, planId]);

  const total = overlay?.timeline_duration_seconds ?? (duration || preview.timeline || 0);
  const { blocks, joins } = useMemo(() => buildTrechos(segments, total), [segments, total]);
  const captions = useMemo(() => captionBlocks(overlay?.pages ?? [], time, corrections), [overlay, time, corrections]);
  const [liveText, liveTone] = LIVE[preview.phase] ?? (preview.videoUrl ? ["Atualizando prévia…", "ac"] : ["Preparando a prévia…", "ac"]);
  const refine = preview.refine;
  const reason = preview.teaserDropped ? (TEASER_DROP_REASONS[preview.teaserDropped] ?? preview.teaserDropped) : "";

  return <main className="stage">
    <div className="stage-bar">
      <Chip tone="ac"><Icon name="check" small />Prévia igual ao export</Chip>
      {refine?.status === "accepted" && preview.timeline != null
        ? <Chip><Icon name="spark" small />Refino da IA: {clipDuration(row.clip.estimated_duration)} → {clipDuration(preview.timeline)}</Chip>
        : refine && <span title={refine.reason}><Chip><Icon name="spark" small />Refino da IA não aplicado</Chip></span>}
      {preview.belowMinimum && <Chip tone="warn">Abaixo da duração mínima</Chip>}
      {reason && <Chip tone="warn">Teaser não aplicado: {reason}</Chip>}
      <div className="grow" />
      <IconButton icon="safeZones" aria-label="Mostrar áreas da interface do Reels e TikTok" aria-pressed={zonesOn} className={zonesOn ? "on" : ""} onClick={() => setZonesOn(value => !value)} />
    </div>
    <div className="canvas">
      <PreviewPlayer ref={player} preview={preview} overlay={overlay} muted={muted} zonesOn={zonesOn} onTime={onTime} onPlaying={setPlaying} onDuration={setDuration} onRetry={onRetry} />
    </div>
    <div className="transport">
      <IconButton icon="skipBack" aria-label="Voltar 5 segundos" onClick={() => player.current?.skip(-5)} />
      <button type="button" className="play" aria-label={playing ? "Pausar" : "Reproduzir"} disabled={!preview.videoUrl} onClick={() => player.current?.toggle()}>
        <Icon name={playing ? "pause" : "play"} />
      </button>
      <IconButton icon="skipForward" aria-label="Avançar 5 segundos" onClick={() => player.current?.skip(5)} />
      <span className="tc mono"><b>{formatBlockTime(time)}</b> / {formatBlockTime(total)}</span>
      <div className="grow" />
      <span className={`live ${liveTone}`} role="status"><i />{liveText}</span>
      {overlayError && <span className="live bad" role="status" title={overlayError}><i />Legenda da prévia indisponível</span>}
      <IconButton icon={muted ? "volumeOff" : "volume"} aria-label={muted ? "Ativar o som" : "Silenciar"} aria-pressed={muted} onClick={() => setMuted(value => !value)} />
    </div>
    <Timeline total={total} time={time} onSeek={seconds => player.current?.seek(seconds)} trechos={blocks} joins={joins} captions={captions}
      headline={{ on: settings.headline.enabled, seconds: settings.headline.persistent ? null : settings.headline.duration_seconds }} />
  </main>;
}
