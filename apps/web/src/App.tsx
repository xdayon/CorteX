import { useEffect, useRef, useState, type ChangeEvent, type CSSProperties, type ReactNode } from "react";
import { api, type AnalysisDocument, type ApiJob, type EditPlanDocument, type RenderDocument, type RenderSettings, type RenderSettingsPatch, type SceneIndexDocument, type SuggestedClip, type SuggestionBrief, type SuggestionSelection, type Telemetry, type TranscriptDocument, type TranscribeOverrides } from "./api";

type IconName = "spark" | "upload" | "link" | "wave" | "brain" | "cut" | "type" | "play" | "cpu" | "check" | "chevron" | "folder" | "settings" | "queue" | "save" | "film" | "clock" | "sliders" | "pause";

const paths: Record<IconName, ReactNode> = {
  spark: <><path d="m12 3 1.2 4.2L17 9l-3.8 1.8L12 15l-1.2-4.2L7 9l3.8-1.8L12 3Z"/><path d="m19 15 .7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7L19 15ZM5 14l.8 2.2L8 17l-2.2.8L5 20l-.8-2.2L2 17l2.2-.8L5 14Z"/></>,
  upload: <><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5"/><path d="M5 14v5h14v-5"/></>,
  link: <><path d="m9 15 6-6"/><path d="M7.5 17.5h-1a4 4 0 0 1 0-8H10m4 5h3.5a4 4 0 0 0 0-8H14"/></>,
  wave: <path d="M3 12h2l1.5-6 3 12 3-14 3 16 2.5-8H21"/>,
  brain: <><path d="M9.5 4.5A3 3 0 0 0 6 8a3 3 0 0 0-1 5.8A3.5 3.5 0 0 0 9.5 19"/><path d="M14.5 4.5A3 3 0 0 1 18 8a3 3 0 0 1 1 5.8 3.5 3.5 0 0 1-4.5 5.2M12 4v16M8 9h4m4 5h-4"/></>,
  cut: <><circle cx="6" cy="7" r="3"/><circle cx="6" cy="17" r="3"/><path d="m8.5 8.5 12 8.5M8.5 15.5 20.5 7"/></>,
  type: <><path d="M5 5h14M12 5v14M8 19h8"/></>,
  play: <path d="m8 5 11 7-11 7V5Z"/>,
  cpu: <><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 1v3m6-3v3M9 20v3m6-3v3M20 9h3m-3 6h3M1 9h3m-3 6h3M9 9h6v6H9z"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  chevron: <path d="m9 18 6-6-6-6"/>,
  folder: <path d="M3 6h7l2 2h9v11H3V6Z"/>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></>,
  queue: <><path d="M5 6h14M5 12h14M5 18h9"/><circle cx="3" cy="6" r=".5"/><circle cx="3" cy="12" r=".5"/><circle cx="3" cy="18" r=".5"/></>,
  save: <><path d="M5 3h12l2 2v16H5V3Z"/><path d="M8 3v6h8V3M8 21v-7h8v7"/></>,
  film: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16m10-16v16M3 9h4m10 0h4M3 15h4m10 0h4"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  sliders: <><path d="M4 6h10m4 0h2M4 12h3m4 0h9M4 18h8m4 0h4"/><circle cx="16" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="14" cy="18" r="2"/></>,
  pause: <path d="M8 5v14m8-14v14"/>,
};

function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

const steps = [
  { jp: "入力", label: "Fonte", sub: "Arquivo ou YouTube", icon: "upload" as IconName },
  { jp: "転写", label: "Transcrição", sub: "CUDA + word timing", icon: "wave" as IconName },
  { jp: "解析", label: "Brief da IA", sub: "Direção editorial", icon: "brain" as IconName },
  { jp: "選択", label: "Curadoria", sub: "Escolha os cortes", icon: "cut" as IconName },
  { jp: "編集", label: "Estúdio", sub: "Visual e ritmo", icon: "type" as IconName },
  { jp: "出力", label: "Render", sub: "Fila de produção", icon: "play" as IconName },
];

const mockTelemetry: Required<Telemetry> = {
  gpu_utilization: 0, gpu_memory_used_mb: 0, gpu_memory_total_mb: 0,
  gpu_temperature_c: 0, encoder_utilization: 0, decoder_utilization: 0,
  cpu_utilization: 0, ram_used_gb: 0, ram_total_gb: 0, device: "Hardware não conectado",
};

const defaultRenderSettings: RenderSettings = {
  schema_version: 1,
  encoder: "h264_nvenc",
  canvas: { width: 1080, height: 1920, fps: 30 },
  captions: {
    enabled: true, font_family: "Montserrat", font_size: 32, words_per_cue: 5,
    outline: true, shadow: true, karaoke: true,
    text_color: "#FFFFFF", karaoke_color: "#2CE4D7", outline_color: "#071012",
    shadow_color: "#000000B8", animation: { style: "fade", duration_seconds: 0.18 },
  },
  headline: {
    enabled: true, text: "", font_family: "Montserrat", font_size: 48, duration_seconds: 4,
    burst_color: "#11B9AD", strip_color: "#FFFFFFF5", text_color: "#071012",
    animation: { entrance: "fade", exit: "fade", duration_seconds: 0.24 },
  },
  subtitles: { sidecar_srt: true },
  template: { quote_burst_opacity: 1, quote_strip_opacity: 0.96, quote_padding: 14 },
};

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="field"><span className="field-head"><b>{label}</b>{hint && <small>{hint}</small>}</span>{children}</label>;
}

function OverrideStatus({ label, current, global }: { label: string; current?: string | number | boolean | null; global?: string | number | boolean | null }) {
  const diverged = current !== undefined && global !== undefined && current !== global;
  return <div className={`override-status ${diverged ? "diverged" : ""}`}><span>{label}</span><b>{current ?? "global"}</b>{diverged && <small>{String(global)}</small>}</div>;
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return <button type="button" className={`toggle-row ${checked ? "on" : ""}`} onClick={() => onChange(!checked)}><span>{label}</span><i><em /></i></button>;
}

function Range({ label, value, min, max, suffix = "", onChange }: { label: string; value: number; min: number; max: number; suffix?: string; onChange: (v: number) => void }) {
  const fill = `${((value - min) / (max - min)) * 100}%`;
  return <Field label={label}><div className="range-wrap"><input type="range" min={min} max={max} value={value} style={{ "--fill": fill } as CSSProperties} onChange={(e) => onChange(Number(e.target.value))}/><output>{value}{suffix}</output></div></Field>;
}

function Waveform({ analysis, compact = false, start = 0, end }: { analysis?: AnalysisDocument | null; compact?: boolean; start?: number; end?: number }) {
  if (!analysis?.waveform.length) return <div className={`waveform empty ${compact ? "compact" : ""}`}><small>Waveform aguardando AnalysisArtifact</small></div>;
  const resolution = analysis.waveform[0];
  const rangeEnd = Math.min(end ?? analysis.duration_seconds, analysis.duration_seconds);
  const first = Math.max(0, Math.floor((start / analysis.duration_seconds) * resolution.peaks.length));
  const last = Math.max(first + 1, Math.ceil((rangeEnd / analysis.duration_seconds) * resolution.peaks.length));
  const source = resolution.peaks.slice(first, last);
  const stride = Math.max(1, Math.ceil(source.length / (compact ? 72 : 144)));
  const points = source.filter((_, index) => index % stride === 0);
  return <div className={`waveform ${compact ? "compact" : ""}`} aria-label="Forma de onda e regiões de voz do áudio">{points.map((peak, i) => {
    const time = start + ((i * stride + 0.5) / Math.max(1, source.length)) * (rangeEnd - start);
    const speech = analysis.vad_intervals.some((interval) => time >= interval.start && time <= interval.end);
    return <i key={i} className={speech ? "speech" : "silence"} style={{ height: `${Math.max(3, peak * 100)}%`, animationDelay: `${i * 8}ms` }} />;
  })}<span className="wave-cursor" /></div>;
}

function Metric({ label, value, detail, tone = "cyan" }: { label: string; value: number; detail: string; tone?: "cyan" | "green" | "amber" }) {
  return <div className={`metric ${tone}`}><div className="metric-top"><span>{label}</span><b>{Math.round(value)}%</b></div><div className="metric-track"><i style={{ width: `${Math.min(100, value)}%` }} /></div><small>{detail}</small></div>;
}

function ComputeDeck({ telemetry, online, jobs }: { telemetry: Required<Telemetry>; online: boolean; jobs: ApiJob[] }) {
  const active = jobs.find((j) => j.status === "running" || j.status === "processing");
  const gpuAvailable = online && !telemetry.device.toLowerCase().includes("indisponível");
  return <aside className="compute-deck glass">
    <div className="deck-head"><div><span className="eyebrow">COMPUTE DECK <i>状態</i></span><h3>Recursos em tempo real</h3></div><span className={`live-dot ${online ? "" : "mock"}`}>{online ? "LIVE" : "STANDBY"}</span></div>
    <div className="device-line"><span className="chip-icon"><Icon name="cpu" /></span><div><b>{telemetry.device}</b><small>{active ? active.message || active.stage : gpuAvailable ? "Pronta para acelerar" : "Aguardando telemetria real"}</small></div><span className="cuda-badge">{gpuAvailable ? "CUDA" : "OFFLINE"}</span></div>
    <Metric label="GPU CORE" value={telemetry.gpu_utilization} detail={`${telemetry.gpu_temperature_c}°C · ${telemetry.gpu_memory_used_mb}/${telemetry.gpu_memory_total_mb} MB`} />
    <Metric label="NVENC" value={telemetry.encoder_utilization} detail="H.264 hardware encoder" tone="green" />
    <Metric label="NVDEC" value={telemetry.decoder_utilization} detail="Hardware decode" tone="green" />
    <div className="deck-mini"><div><span>CPU</span><b>{Math.round(telemetry.cpu_utilization)}%</b></div><div><span>RAM</span><b>{telemetry.ram_used_gb.toFixed(1)} GB</b></div></div>
    {active && <div className="active-job"><div><span>{active.stage || active.type || "PROCESSO"}</span><b>{Math.round(active.progress || 0)}%</b></div><div className="job-track"><i style={{ width: `${active.progress || 0}%` }} /></div></div>}
  </aside>;
}

function SourceStep({ sourceMode, setSourceMode, fileName, setFile, youtube, setYoutube, onNext }: {
  sourceMode: "file" | "youtube"; setSourceMode: (v: "file" | "youtube") => void; fileName: string; setFile: (v: File | null) => void; youtube: string; setYoutube: (v: string) => void; onNext: () => void;
}) {
  function selectFile(event: ChangeEvent<HTMLInputElement>) { setFile(event.target.files?.[0] ?? null); }
  const ready = sourceMode === "file" ? !!fileName : /^https?:\/\//.test(youtube);
  return <section className="step-content enter">
    <div className="page-heading"><div><span className="eyebrow">01 / INPUT NODE <i>入力</i></span><h1>Escolha a matéria-prima</h1><p>O CorteX só começa quando você mandar. Configure a fonte primeiro, sem processos surpresa.</p></div><span className="phase-number">01</span></div>
    <div className="source-tabs"><button className={sourceMode === "file" ? "active" : ""} onClick={() => setSourceMode("file")}><Icon name="upload"/> Arquivo local</button><button className={sourceMode === "youtube" ? "active" : ""} onClick={() => setSourceMode("youtube")}><Icon name="link"/> Link do YouTube</button></div>
    {sourceMode === "file" ? <label className={`dropzone ${fileName ? "has-file" : ""}`}>
      <input type="file" accept="video/*,audio/*" onChange={selectFile}/><span className="drop-icon"><Icon name={fileName ? "check" : "upload"} size={28}/></span>
      <strong>{fileName || "Arraste o episódio completo aqui"}</strong><p>{fileName ? "Arquivo validado e aguardando configuração" : "ou clique para selecionar MP4, MOV, MKV, WebM, MP3 ou WAV"}</p><small>{fileName ? "READY // LOCAL SOURCE" : "Até 10 GB · nenhum upload inicia a transcrição"}</small>
    </label> : <div className="url-panel glass"><span className="url-icon"><Icon name="link"/></span><input aria-label="URL do YouTube" value={youtube} onChange={(e) => setYoutube(e.target.value)} placeholder="https://youtube.com/watch?v=..."/><button className="btn secondary">Validar link</button></div>}
    <div className="info-grid"><div><Icon name="film"/><span><b>Ingestão inteligente</b><small>Metadados e formato preservados</small></span></div><div><Icon name="save"/><span><b>Cache resumível</b><small>Continue mesmo após reiniciar</small></span></div><div><Icon name="cpu"/><span><b>Pipeline local</b><small>Seu vídeo não sai da máquina</small></span></div></div>
    <div className="step-actions"><span>{ready ? "Fonte pronta para configurar" : "Selecione uma fonte para continuar"}</span><button className="btn primary" disabled={!ready} onClick={onNext}>Configurar transcrição <Icon name="chevron"/></button></div>
  </section>;
}

function TranscriptionStep({ onStart, running, progress, message, error }: { onStart: (overrides: TranscribeOverrides) => void; running: boolean; progress: number; message: string; error: string | null }) {
  const [model, setModel] = useState("large-v3-turbo"); const [language, setLanguage] = useState("pt"); const [batch, setBatch] = useState(8); const [vad, setVad] = useState(true); const [fillers, setFillers] = useState(true);
  return <section className="step-content enter"><div className="page-heading"><div><span className="eyebrow">02 / SPEECH ENGINE <i>転写</i></span><h1>Transcrição de alta precisão</h1><p>Word timing, waveform e VAD preparados para proteger cada fonema na edição.</p></div><span className="phase-number">02</span></div>
    <div className="two-col"><div className="panel glass"><div className="panel-title"><span><Icon name="wave"/></span><div><h3>Motor de reconhecimento</h3><p>Configuração aplicada somente ao iniciar</p></div></div>
      <div className="form-grid"><Field label="Modelo Whisper" hint="recomendado"><select value={model} onChange={(e) => setModel(e.target.value)}><option value="large-v3-turbo">Large v3 Turbo</option><option value="large-v3">Large v3</option><option value="medium">Medium</option></select></Field><Field label="Idioma"><select value={language} onChange={(e) => setLanguage(e.target.value)}><option value="pt">Português (Brasil)</option><option value="auto">Detecção automática</option></select></Field><Range label="Batch CUDA" value={batch} min={1} max={16} onChange={setBatch}/><Field label="Compute type" hint="GTX 1060 · Pascal"><select defaultValue="int8"><option>int8</option><option>int8_float32</option><option>float32</option></select></Field></div>
      <div className="toggles"><Toggle checked={vad} onChange={setVad} label="Silero VAD · proteger regiões de voz"/><Toggle checked={fillers} onChange={setFillers} label="Marcar hesitações para revisão"/></div>
    </div><div className="panel glass neural-panel"><div className="neural-grid"/><div className="neural-core"><Icon name="brain" size={42}/></div><span className="engine-label">FASTER-WHISPER</span><h3>{model}</h3><div className="engine-chips"><span>CUDA</span><span>WORD LEVEL</span><span>VAD</span></div><p>O áudio será extraído em PCM 16 kHz e processado localmente na GPU configurada.</p></div></div>
    {running && <div className="process-strip"><div className="process-meta"><span><i/> {message || "Processando"}</span><b>{Math.round(progress)}%</b></div><div className="process-track"><i style={{width:`${progress}%`}}/></div></div>}
    {error && <div className="error-strip" role="alert"><b>Falha na transcrição</b><p>{error}</p></div>}
    <div className="step-actions"><span>{running ? message || "Processamento local em andamento" : "Nenhuma tarefa será iniciada automaticamente"}</span><button className="btn primary" disabled={running} onClick={() => onStart({ model, language: language === "auto" ? undefined : language, batch_size: batch, vad })}><Icon name={running ? "pause" : "play"}/> {running ? "Transcrevendo..." : "Iniciar transcrição"}</button></div>
  </section>;
}

function BriefStep({ onAnalyze, running, progress, message, error }: { onAnalyze: (brief: SuggestionBrief) => void; running: boolean; progress: number; message: string; error: string | null }) {
  const [prompt, setPrompt] = useState("Encontre falas fortes, autocontidas e compartilháveis. Priorize reflexões sobre espiritualidade, consciência e passagens bíblicas, sempre com um payoff claro."); const [count, setCount] = useState(15); const [min, setMin] = useState(40); const [max, setMax] = useState(90); const [tone, setTone] = useState("viral");
  return <section className="step-content enter"><div className="page-heading"><div><span className="eyebrow">03 / EDITORIAL INTELLIGENCE <i>解析</i></span><h1>Dê uma missão à inteligência</h1><p>Direcione o olhar editorial. A IA propõe; você continua como diretor.</p></div><span className="phase-number">03</span></div>
    <div className="brief-layout"><div className="panel glass"><div className="panel-title"><span><Icon name="brain"/></span><div><h3>Brief editorial</h3><p>Este texto será versionado junto ao projeto</p></div></div><textarea className="prompt-area" value={prompt} onChange={(e) => setPrompt(e.target.value)}/><div className="prompt-chips"><button onClick={() => setPrompt(prompt + " Foque em falas bíblicas.")}>+ Bíblia</button><button onClick={() => setPrompt(prompt + " Busque frases controversas.")}>+ Hot takes</button><button onClick={() => setPrompt(prompt + " Preserve pausas emocionais.")}>+ Profundo</button><button onClick={() => setPrompt(prompt + " Priorize histórias pessoais.")}>+ Histórias</button></div><Field label="Direção"><div className="segmented"><button className={tone === "viral" ? "active" : ""} onClick={() => setTone("viral")}>Viral</button><button className={tone === "authority" ? "active" : ""} onClick={() => setTone("authority")}>Autoridade</button><button className={tone === "deep" ? "active" : ""} onClick={() => setTone("deep")}>Profundo</button></div></Field></div>
      <div className="panel glass limits"><div className="panel-title"><span><Icon name="sliders"/></span><div><h3>Limites da busca</h3><p>Faixas usadas pela seleção</p></div></div><Range label="Quantidade de cortes" value={count} min={1} max={25} onChange={setCount}/><Range label="Duração mínima" value={min} min={15} max={90} suffix="s" onChange={(v) => { setMin(v); if (v >= max) setMax(Math.min(180, v + 15)); }}/><Range label="Duração máxima" value={max} min={30} max={180} suffix="s" onChange={(v) => setMax(Math.max(v, min + 1))}/><div className="estimate"><span><Icon name="clock"/></span><div><b>Janela editorial</b><p>{count} sugestões · {min}s a {max}s cada</p></div></div></div></div>
    {running && <div className="process-strip"><div className="process-meta"><span><i/> {message || "Selecionando cortes"}</span><b>{Math.round(progress)}%</b></div><div className="process-track"><i style={{width:`${progress}%`}}/></div></div>}
    {error && <div className="error-strip" role="alert"><b>Falha na seleção editorial</b><p>{error}</p></div>}
    <div className="step-actions"><span>Codex CLI · fallback Claude · sem API key</span><button className="btn primary" disabled={running} onClick={() => onAnalyze({ count, minimum_seconds: min, maximum_seconds: max, topic: tone, instructions: prompt })}><Icon name="spark"/> {running ? "Analisando..." : "Analisar e sugerir cortes"}</button></div>
  </section>;
}

function timestamp(seconds: number) { const value = Math.max(0, Math.round(seconds)); const h = Math.floor(value / 3600); const m = Math.floor((value % 3600) / 60); const s = value % 60; return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`; }

function clipKey(clip: SuggestedClip) { return `${clip.rank}:${clip.start_second}:${clip.end_second}:${clip.pacing}`; }

function EditPlanTimeline({ clip, plan }: { clip: SuggestedClip; plan: EditPlanDocument }) {
  const sourceDuration = Math.max(0.001, clip.end_second - clip.start_second);
  return <div className="edl-block"><div className="edl-head"><span>EDL RESOLVIDA</span><b>{plan.segments.length} segmento{plan.segments.length === 1 ? "" : "s"} · {plan.timeline_duration_seconds.toFixed(2)}s</b></div><div className="edl-track" aria-label="Segmentos resolvidos do plano de edição">{plan.segments.map((segment, index) => {
    const left = Math.max(0, ((segment.start - clip.start_second) / sourceDuration) * 100);
    const width = Math.max(0.8, ((segment.end - segment.start) / sourceDuration) * 100);
    return <span key={`${segment.timeline_order}-${segment.start}`} className="edl-segment" style={{ left: `${left}%`, width: `${Math.min(100 - left, width)}%` }} title={`${timestamp(segment.start)} a ${timestamp(segment.end)}`}><i>{index + 1}</i>{segment.transition && <em>{Math.round(segment.transition.duration * 1000)}ms</em>}</span>;
  })}</div><div className="edl-stats"><span><b>{plan.diagnostics.saved_seconds.toFixed(2)}s</b> removidos</span><span><b>{plan.diagnostics.cuts}</b> jump cuts</span><span><b>{Math.round(plan.diagnostics.crossfade * 1000)}ms</b> crossfade</span></div></div>;
}

function SceneMarkers({ sceneIndex, start, end }: { sceneIndex: SceneIndexDocument | null; start: number; end: number }) {
  if (!sceneIndex?.cuts.length) return null;
  const span = Math.max(0.001, end - start);
  const cuts = sceneIndex.cuts.filter((cut) => cut.time >= start && cut.time <= end);
  if (!cuts.length) return null;
  return <div className="scene-markers" aria-label="Cortes de câmera detectados (dados reais)">{cuts.map((cut) => <i key={cut.time} className="scene-marker" style={{ left: `${((cut.time - start) / span) * 100}%` }} title={`Corte de cena em ${cut.time.toFixed(2)}s · score ${cut.score.toFixed(1)}`} />)}</div>;
}

function CurateStep({ clips, notes, transcript, analysis, plans, planning, planProgress, planMessage, planError, onPlan, onNext, sceneIndex, sceneIndexArtifactId, detectingScenes, sceneProgress, sceneMessage, sceneError, onDetectScenes }: { clips: SuggestedClip[]; notes: string; transcript: TranscriptDocument | null; analysis: AnalysisDocument | null; plans: Record<string, EditPlanDocument>; planning: boolean; planProgress: number; planMessage: string; planError: string | null; onPlan: (clips: SuggestedClip[]) => Promise<boolean>; onNext: (clips: SuggestedClip[]) => Promise<void>; sceneIndex: SceneIndexDocument | null; sceneIndexArtifactId: string | null; detectingScenes: boolean; sceneProgress: number; sceneMessage: string; sceneError: string | null; onDetectScenes: () => void }) {
  const [selected, setSelected] = useState(clips.map(() => true)); const [active, setActive] = useState(0); const activeClip = clips[active];
  useEffect(() => { setSelected(clips.map(() => true)); setActive(0); }, [clips]);
  const activeWords = activeClip ? transcript?.segments.flatMap((segment) => segment.words).filter((word) => word.end >= activeClip.start_second && word.start <= activeClip.end_second) ?? [] : [];
  const activePauses = activeClip ? analysis?.pauses.filter((pause) => pause.end >= activeClip.start_second && pause.start <= activeClip.end_second) ?? [] : [];
  const activePlan = activeClip ? plans[clipKey(activeClip)] : undefined;
  const selectedClips = clips.filter((_, index) => selected[index]);
  return <section className="step-content wide enter"><div className="page-heading"><div><span className="eyebrow">04 / HUMAN CURATION <i>選択</i></span><h1>A decisão final é sua</h1><p>Compare argumento, ritmo e fronteiras antes de levar um corte ao estúdio.</p></div><span className="result-count">{selected.filter(Boolean).length}<small>selecionados</small></span></div>
    {clips.length === 0 ? <div className="panel glass"><h3>Nenhum corte recomendado</h3><p>{notes}</p></div> : <div className="curate-layout"><div className="clip-list">{clips.map((clip, i) => <article key={`${clip.rank}-${clip.title}`} className={`clip-card ${active === i ? "active" : ""} ${selected[i] ? "selected" : ""}`} onClick={() => setActive(i)}><button className="check-btn" onClick={(e) => { e.stopPropagation(); setSelected(selected.map((v, index) => index === i ? !v : v)); }}><Icon name="check"/></button><div className="clip-main"><div className="clip-tags"><span>{(clip.topic || clip.pacing).toUpperCase()}</span><small>{timestamp(clip.start_second)} — {timestamp(clip.end_second)} · {timestamp(clip.estimated_duration).slice(3)}</small></div><h3>{clip.title}</h3><p>{clip.reasoning}</p><div className="micro-structure"><i className="hook">HOOK</i><i className="context">CONTEXTO</i><i className="payoff">PAYOFF</i></div></div><div className="score"><b>{Math.round((clip.scores.total / 35) * 100)}</b><small>EDITORIAL<br/>SCORE</small></div></article>)}</div>
      <div className="timeline-panel glass"><div className="panel-title"><span><Icon name="cut"/></span><div><h3>Cirurgia do corte</h3><p>PCM real · Silero VAD em verde · pausas sincronizadas</p></div></div><div className="waveform-stack"><Waveform analysis={analysis} start={activeClip.start_second} end={activeClip.end_second}/><SceneMarkers sceneIndex={sceneIndex} start={activeClip.start_second} end={activeClip.end_second}/></div><div className="timeline-ruler"><span>{timestamp(activeClip.start_second)}</span><span>{timestamp((activeClip.start_second + activeClip.end_second) / 2)}</span><span>{timestamp(activeClip.end_second)}</span></div>
      <div className="scene-index-row"><button className="btn secondary" disabled={detectingScenes} onClick={onDetectScenes}><Icon name="film"/> {detectingScenes ? "Detectando cenas..." : sceneIndex ? "Redetectar cortes de câmera" : "Detectar cenas"}</button>{sceneIndex && <span className="scene-index-summary">{sceneIndex.cut_count} corte{sceneIndex.cut_count === 1 ? "" : "s"} de câmera reais · scdet threshold {sceneIndex.engine.threshold_effective}{sceneIndexArtifactId ? " · aplicado ao snapping da EDL" : ""}</span>}</div>
      {detectingScenes && <div className="process-strip compact-process"><div className="process-meta"><span><i/> {sceneMessage}</span><b>{Math.round(sceneProgress)}%</b></div><div className="process-track"><i style={{width:`${sceneProgress}%`}}/></div></div>}
      {sceneError && <div className="error-strip" role="alert"><b>Falha na detecção de cenas</b><p>{sceneError}</p></div>}
      {activePlan ? <EditPlanTimeline clip={activeClip} plan={activePlan}/> : <button className="btn secondary plan-btn" disabled={planning} onClick={() => void onPlan([activeClip])}><Icon name="cut"/> {planning ? "Resolvendo fronteiras..." : "Resolver EDL deste corte"}</button>}{planning && <div className="process-strip compact-process"><div className="process-meta"><span><i/> {planMessage}</span><b>{Math.round(planProgress)}%</b></div><div className="process-track"><i style={{width:`${planProgress}%`}}/></div></div>}{planError && <div className="error-strip" role="alert"><b>Falha no plano de edição</b><p>{planError}</p></div>}<div className="transcript-focus"><p>{activeWords.length ? activeWords.map((word, index) => <span className="spoken-word" key={`${word.start}-${index}`}>{word.word} </span>) : "Transcript indisponível para esta região."}</p><div className="boundary"><i/> {activePauses.length} PAUSAS · {Math.round((analysis?.overall_speech_ratio ?? 0) * 100)}% VOZ NO EPISÓDIO</div></div>{activePlan && <div className={`quality-report ${activePlan.quality.degraded ? "degraded" : "passed"}`}><div><Icon name={activePlan.quality.degraded ? "sliders" : "check"}/><b>{activePlan.quality.degraded ? "Plano preservado integralmente" : "Quality gate aprovado"}</b></div>{activePlan.quality.issues.length === 0 ? <p>Nenhum aviso de fronteira.</p> : activePlan.quality.issues.map((issue, index) => <p key={`${issue.code}-${index}`}><span>{issue.code}</span>{issue.snapped_from != null && issue.snapped_to != null ? ` ${issue.snapped_from.toFixed(3)}s → ${issue.snapped_to.toFixed(3)}s` : issue.word ? ` · ${issue.word}` : ""}</p>)}</div>}<div className="editor-notes"><div><span>Ritmo {activePlan ? "efetivo" : "sugerido"}</span><b>{activePlan?.profile ?? activeClip.pacing}</b></div><div><span>Loudness análise</span><b>{analysis?.loudness.integrated_lufs ?? "—"} LUFS</b></div><div><span>True peak</span><b>{analysis?.loudness.true_peak_dbfs ?? "—"} dBFS</b></div></div></div></div>}
    <div className="step-actions"><span>{selectedClips.length} cortes · {selectedClips.filter((clip) => plans[clipKey(clip)]).length} EDLs prontas</span><button className="btn primary" disabled={!selectedClips.length || planning} onClick={() => void onNext(selectedClips)}>{planning ? "Preparando planos..." : "Preparar e abrir estúdio"} <Icon name="chevron"/></button></div>
  </section>;
}

type SocialIconName = "heart" | "comment" | "send" | "dots" | "camera" | "music" | "home" | "search" | "create" | "reels" | "bookmark" | "shareArrow" | "friends" | "inbox" | "profile";

const socialPaths: Record<SocialIconName, ReactNode> = {
  heart: <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l8.8 8.7 8.8-8.7a5.5 5.5 0 0 0 0-7.8Z"/>,
  comment: <path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5 8.4 8.4 0 0 1-3.9-.9L3 21l1.9-5.6A8.5 8.5 0 1 1 21 11.5Z"/>,
  send: <><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></>,
  dots: <><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></>,
  camera: <><path d="M4 7h3l2-2.5h6L17 7h3v13H4V7Z"/><circle cx="12" cy="13" r="3.6"/></>,
  music: <><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></>,
  home: <path d="m3 10.5 9-7.5 9 7.5V21h-6v-6h-6v6H3V10.5Z"/>,
  search: <><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></>,
  create: <><rect x="3" y="3" width="18" height="18" rx="5"/><path d="M12 8v8M8 12h8"/></>,
  reels: <><rect x="3" y="3" width="18" height="18" rx="4.5"/><path d="M3 8.5h18M9.5 3l3.2 5.5M15.8 3 19 8.5"/><path d="m11 12.5 4.2 2.6-4.2 2.6v-5.2Z"/></>,
  bookmark: <path d="M6 3h12v18l-6-4.2L6 21V3Z"/>,
  shareArrow: <path d="M13.5 5 21 12l-7.5 7v-4.2C8 14.8 4.7 16.7 2.5 19.8 3.4 13.6 7.2 10 13.5 9.2V5Z"/>,
  friends: <><circle cx="9" cy="8" r="3.5"/><path d="M3 20c.8-3.4 3.2-5 6-5s5.2 1.6 6 5"/><circle cx="17.5" cy="9.5" r="2.8"/><path d="M16 15.3c2.6.2 4.4 1.7 5 4.7"/></>,
  inbox: <path d="M4 4h16v13H9l-5 4V4Z"/>,
  profile: <><circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/></>,
};

function SocialIcon({ name, size = 22, filled = false }: { name: SocialIconName; size?: number; filled?: boolean }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke={filled ? "none" : "currentColor"} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{socialPaths[name]}</svg>;
}

function ReelsOverlay({ description }: { description: string }) {
  return <div className="social-overlay ig" aria-label="Wireframe do Instagram Reels">
    <div className="so-top ig-top"><b>Reels</b><SocialIcon name="camera" size={22}/></div>
    <div className="so-rail">
      <div className="so-action"><SocialIcon name="heart" size={24}/><span>124K</span></div>
      <div className="so-action"><SocialIcon name="comment" size={24}/><span>1.032</span></div>
      <div className="so-action"><SocialIcon name="send" size={23}/><span>18,4K</span></div>
      <div className="so-action"><SocialIcon name="dots" size={20}/></div>
      <div className="ig-audio-cover"/>
    </div>
    <div className="so-bottom">
      <div className="ig-profile"><i className="ig-avatar"/><b>dayon</b><button type="button" tabIndex={-1}>Seguir</button></div>
      <p>{description} <span>... mais</span></p>
      <div className="so-music"><SocialIcon name="music" size={11}/><span>dayon · Áudio original</span></div>
    </div>
    <div className="so-nav ig-nav">
      <span><SocialIcon name="home" size={21}/></span>
      <span><SocialIcon name="search" size={21}/></span>
      <span><SocialIcon name="create" size={21}/></span>
      <span className="active"><SocialIcon name="reels" size={21}/></span>
      <span className="ig-nav-avatar"/>
    </div>
  </div>;
}

function TikTokOverlay({ description }: { description: string }) {
  return <div className="social-overlay tt" aria-label="Wireframe do TikTok">
    <div className="so-top tt-top"><span>Following</span><span className="active">For You</span></div>
    <span className="tt-search"><SocialIcon name="search" size={19}/></span>
    <div className="so-rail">
      <div className="tt-avatar"><i/><em>+</em></div>
      <div className="so-action"><SocialIcon name="heart" size={26} filled/><span>324,7K</span></div>
      <div className="so-action"><SocialIcon name="comment" size={25} filled/><span>2.463</span></div>
      <div className="so-action"><SocialIcon name="bookmark" size={23} filled/><span>60,5K</span></div>
      <div className="so-action"><SocialIcon name="shareArrow" size={25} filled/><span>12,1K</span></div>
      <div className="tt-disc"><i/></div>
    </div>
    <div className="so-bottom">
      <b>@dayon</b>
      <p>{description} <span>mais</span></p>
      <div className="so-music"><SocialIcon name="music" size={11}/><span>Som original — dayon</span></div>
    </div>
    <div className="so-nav tt-nav">
      <span className="active"><SocialIcon name="home" size={19} filled/><small>Home</small></span>
      <span><SocialIcon name="friends" size={19}/><small>Friends</small></span>
      <span className="tt-plus"><em>+</em></span>
      <span><SocialIcon name="inbox" size={19} filled/><small>Inbox</small></span>
      <span><SocialIcon name="profile" size={19}/><small>Profile</small></span>
    </div>
  </div>;
}

function PhonePreview({ platform, headline, headlineEnabled, captionsEnabled, captionSize, wireframe }: { platform: string; headline: string; headlineEnabled: boolean; captionsEnabled: boolean; captionSize: number; wireframe: boolean }) {
  const description = "Uma conversa sobre fé e consciência";
  return <div className="phone-stage"><div className={`phone ${platform !== "clean" ? "with-overlay" : ""}`}><div className="video-noise"/><div className="speaker speaker-a"><span/></div><div className="speaker speaker-b"><span/></div>{headlineEnabled && <div className="headline-preview"><i>“</i>{headline.split("\n").map((line, index) => <b key={`${index}-${line}`}>{line}</b>)}</div>}{captionsEnabled && <div className="caption-preview" style={{fontSize: `${captionSize}px`}}>A FÉ COMEÇA ONDE O <strong>CONTROLE</strong> TERMINA</div>}{platform === "reels" && <ReelsOverlay description={description}/>}{platform === "tiktok" && <TikTokOverlay description={description}/>}{wireframe && <div className="safe-guides"><span>SAFE TITLE</span><i>SAFE CAPTION</i></div>}</div></div>;
}

function StudioStep({ settings, onChange, onNext }: { settings: RenderSettings; onChange: (settings: RenderSettings) => void; onNext: () => void }) {
  const [tab, setTab] = useState<"caption" | "headline" | "output">("caption"); const [platform, setPlatform] = useState("reels"); const [wireframe, setWireframe] = useState(true);
  const canvasValue = `${settings.canvas.width}x${settings.canvas.height}`;
  const setCaptions = (captions: Partial<RenderSettings["captions"]>) => onChange({ ...settings, captions: { ...settings.captions, ...captions } });
  const setHeadline = (headline: Partial<RenderSettings["headline"]>) => onChange({ ...settings, headline: { ...settings.headline, ...headline } });
  return <section className="step-content studio-wide enter"><div className="studio-toolbar"><div><span className="eyebrow">05 / VISUAL LAB <i>編集</i></span><h1>Estúdio visual</h1></div><div className="preview-switch"><button className={platform === "clean" ? "active" : ""} onClick={() => setPlatform("clean")}>Limpo</button><button className={platform === "tiktok" ? "active" : ""} onClick={() => setPlatform("tiktok")}>TikTok</button><button className={platform === "reels" ? "active" : ""} onClick={() => setPlatform("reels")}>Reels</button><button className={wireframe ? "safe active" : "safe"} onClick={() => setWireframe(!wireframe)}>Safe zones</button></div></div>
    <div className="studio-layout"><div className="inspector glass"><div className="inspector-tabs"><button className={tab === "caption" ? "active" : ""} onClick={() => setTab("caption")}>Legenda</button><button className={tab === "headline" ? "active" : ""} onClick={() => setTab("headline")}>Headline</button><button className={tab === "output" ? "active" : ""} onClick={() => setTab("output")}>Saída</button></div>
      {tab === "caption" && <div className="inspector-body"><Toggle checked={settings.captions.enabled} onChange={(enabled) => setCaptions({ enabled })} label="Queimar legendas no vídeo"/><Toggle checked={settings.captions.karaoke} onChange={(karaoke) => setCaptions({ karaoke })} label="Karaoke palavra por palavra"/><Field label="Fonte instalada"><select value={settings.captions.font_family} onChange={(event) => setCaptions({ font_family: event.target.value })}><option>Montserrat</option><option>Liberation Sans</option><option>Noto Sans</option></select></Field><Field label="Cor do texto"><input type="text" value={settings.captions.text_color} onChange={(event) => setCaptions({ text_color: event.target.value })} /></Field><Field label="Cor do karaoke"><input type="text" value={settings.captions.karaoke_color} onChange={(event) => setCaptions({ karaoke_color: event.target.value })} /></Field><Field label="Cor da borda"><input type="text" value={settings.captions.outline_color} onChange={(event) => setCaptions({ outline_color: event.target.value })} /></Field><Field label="Cor da sombra"><input type="text" value={settings.captions.shadow_color} onChange={(event) => setCaptions({ shadow_color: event.target.value })} /></Field><Field label="Animação"><select value={settings.captions.animation.style} onChange={(event) => setCaptions({ animation: { ...settings.captions.animation, style: event.target.value as RenderSettings["captions"]["animation"]["style"] } })}><option value="none">none</option><option value="fade">fade</option><option value="pop">pop</option></select></Field><Range label="Duração da animação" value={settings.captions.animation.duration_seconds} min={0.05} max={1.5} suffix="s" onChange={(duration_seconds) => setCaptions({ animation: { ...settings.captions.animation, duration_seconds } })}/><Range label="Tamanho" value={settings.captions.font_size} min={22} max={52} suffix="px" onChange={(font_size) => setCaptions({ font_size })}/><Range label="Palavras por legenda" value={settings.captions.words_per_cue} min={2} max={9} onChange={(words_per_cue) => setCaptions({ words_per_cue })}/><Toggle checked={settings.captions.outline} onChange={(outline) => setCaptions({ outline })} label="Borda nos caracteres"/><Toggle checked={settings.captions.shadow} onChange={(shadow) => setCaptions({ shadow })} label="Sombra tipográfica"/></div>}
      {tab === "headline" && <div className="inspector-body"><Toggle checked={settings.headline.enabled} onChange={(enabled) => setHeadline({ enabled })} label="Renderizar headline"/><Field label="Texto da headline" hint="vazio usa o título de cada corte"><textarea value={settings.headline.text} maxLength={120} onChange={(event) => setHeadline({ text: event.target.value })} rows={4}/></Field><Field label="Fonte instalada"><select value={settings.headline.font_family} onChange={(event) => setHeadline({ font_family: event.target.value })}><option>Montserrat</option><option>Liberation Sans</option><option>Noto Sans</option></select></Field><Field label="Cor da faixa"><input type="text" value={settings.headline.strip_color} onChange={(event) => setHeadline({ strip_color: event.target.value })} /></Field><Field label="Cor do burst"><input type="text" value={settings.headline.burst_color} onChange={(event) => setHeadline({ burst_color: event.target.value })} /></Field><Field label="Cor do texto"><input type="text" value={settings.headline.text_color} onChange={(event) => setHeadline({ text_color: event.target.value })} /></Field><Field label="Animação de entrada"><select value={settings.headline.animation.entrance} onChange={(event) => setHeadline({ animation: { ...settings.headline.animation, entrance: event.target.value as RenderSettings["headline"]["animation"]["entrance"] } })}><option value="none">none</option><option value="fade">fade</option><option value="slide">slide</option></select></Field><Field label="Animação de saída"><select value={settings.headline.animation.exit} onChange={(event) => setHeadline({ animation: { ...settings.headline.animation, exit: event.target.value as RenderSettings["headline"]["animation"]["exit"] } })}><option value="none">none</option><option value="fade">fade</option><option value="slide">slide</option></select></Field><Range label="Duração da animação" value={settings.headline.animation.duration_seconds} min={0.05} max={1.5} suffix="s" onChange={(duration_seconds) => setHeadline({ animation: { ...settings.headline.animation, duration_seconds } })}/><Range label="Tamanho" value={settings.headline.font_size} min={28} max={72} suffix="px" onChange={(font_size) => setHeadline({ font_size })}/><Range label="Duração" value={settings.headline.duration_seconds} min={2} max={8} suffix="s" onChange={(duration_seconds) => setHeadline({ duration_seconds })}/></div>}
      {tab === "output" && <div className="inspector-body"><Field label="Canvas"><select value={canvasValue} onChange={(event) => { const [width, height] = event.target.value.split("x").map(Number); onChange({ ...settings, canvas: { width: width as 1080 | 1920, height: height as 1080 | 1920, fps: 30 } }); }}><option value="1080x1920">1080 × 1920 · Vertical</option><option value="1080x1080">1080 × 1080 · Quadrado</option><option value="1920x1080">1920 × 1080 · Horizontal</option></select></Field><Field label="Encoder"><select value={settings.encoder} onChange={(event) => onChange({ ...settings, encoder: event.target.value as RenderSettings["encoder"] })}><option value="h264_nvenc">H.264 NVENC</option><option value="libx264">libx264 CPU</option></select></Field><Toggle checked={settings.subtitles.sidecar_srt} onChange={(sidecar_srt) => onChange({ ...settings, subtitles: { sidecar_srt } })} label="Gerar SRT separado"/><p>Áudio é normalizado automaticamente para -14 LUFS.</p></div>}
    </div><PhonePreview platform={platform} headline={settings.headline.text || "Título do corte"} headlineEnabled={settings.headline.enabled} captionsEnabled={settings.captions.enabled} captionSize={settings.captions.font_size} wireframe={wireframe}/><div className="quick-stack glass"><span className="eyebrow">PROPRIEDADES EFETIVAS</span><div><span>Canvas</span><b>{settings.canvas.width} × {settings.canvas.height}</b></div><div><span>Preview-only</span><b>{platform === "clean" ? "Limpo" : platform === "tiktok" ? `TikTok${wireframe ? " + safe zones" : ""}` : `Reels${wireframe ? " + safe zones" : ""}`}</b></div><div><span>Codec</span><b>{settings.encoder === "h264_nvenc" ? "H.264 NVENC" : "libx264 CPU"}</b></div><div><span>Legendas</span><b>{settings.captions.enabled ? "Queimadas" : "Desativadas"}</b></div></div></div>
    <div className="step-actions"><span>Plataforma e safe zones afetam somente a prévia</span><button className="btn primary" onClick={onNext}>Revisar render <Icon name="chevron"/></button></div>
  </section>;
}

type RenderResult = { artifactId: string; document: RenderDocument };
type RenderItemStatus = { state: "queued" | "rendering" | "ready" | "failed"; progress: number; error?: string };

function RenderStep({ onStart, running, progress, clips, error, projectId, editPlanArtifactIds, results, statuses, settings, overrides, setOverrides }: { onStart: () => void; running: boolean; progress: number; clips: SuggestedClip[]; error: string | null; projectId?: string; editPlanArtifactIds: Record<string, string>; results: Record<string, RenderResult>; statuses: Record<string, RenderItemStatus>; settings: RenderSettings; overrides: Record<string, RenderSettingsPatch>; setOverrides: (value: Record<string, RenderSettingsPatch>) => void }) {
  const duration = clips.reduce((total, clip) => total + clip.estimated_duration, 0);
  const currentArtifactIds = new Set(clips.map((clip) => editPlanArtifactIds[clipKey(clip)]).filter(Boolean));
  const completed = Object.entries(results).filter(([editPlanArtifactId]) => currentArtifactIds.has(editPlanArtifactId)).map(([, result]) => result);
  const allPassed = completed.length > 0 && completed.every(({ document }) => document.quality.passed);
  const publicationLabel = (code: string) => ({
    black_frame_detected: "quadros pretos detectados",
    frozen_frame_detected: "trechos congelados detectados",
    duration_mismatch: "duração final divergente do plano",
    loudness_out_of_tolerance: "loudness fora da tolerância",
    true_peak_exceeded: "true peak acima do limite",
    subtitles_missing: "legendas sidecar ausentes",
    caption_outside_safe_zone: "legenda fora da safe zone",
    headline_outside_safe_zone: "headline fora da safe zone",
    safe_zone_missing: "safe zone inexistente para este canvas",
    audio_stream_missing: "stream de áudio ausente",
    video_stream_missing: "stream de vídeo ausente",
  }[code] ?? code);

  return (
    <section className="step-content enter">
      <div className="page-heading">
        <div><span className="eyebrow">06 / PRODUCTION QUEUE <i>出力</i></span><h1>Pronto para produzir</h1><p>Confira o plano e dispare a fila. Cada corte é validado antes de ser entregue.</p></div>
        <span className="phase-number">06</span>
      </div>
      <div className="render-grid">
        <div className="panel glass render-summary">
          <div className="summary-head"><span><Icon name="film"/></span><div><h3>Fila do projeto atual</h3><p>{clips.length} cortes · EDL persistida</p></div><b>{timestamp(duration)}</b></div>
          {clips.map((clip, i) => {
            const editPlanArtifactId = editPlanArtifactIds[clipKey(clip)];
            const result = results[editPlanArtifactId];
            const status = statuses[editPlanArtifactId] ?? { state: "queued", progress: 0 };
            const quality = result?.document.quality;
            const publication = result?.document.publication;
            const clipId = clipKey(clip);
            const clipOverride = overrides[clipId] ?? {};
            const updateOverride = (patch: RenderSettingsPatch) =>
              setOverrides({ ...overrides, [clipId]: patch });
            const hasOverride = Boolean(
              clipOverride.headline?.text ||
              clipOverride.captions?.text_color ||
              clipOverride.captions?.animation?.style ||
              clipOverride.headline?.animation?.entrance,
            );
            return (
              <div className={`render-item ${status.state}`} key={clipId}>
                <span className="render-thumb">{String(i + 1).padStart(2, "0")}</span>
                <div className="render-item-copy">
                  <b>{clip.title}</b>
                  <small>
                    {result ? `${timestamp(result.document.quality.actual_duration_seconds).slice(3)} · ${result.document.engine.width} × ${result.document.engine.height} · ${result.document.engine.effective_encoder}` : `${timestamp(clip.estimated_duration).slice(3)} · aguardando manifesto`}
                  </small>
                  {quality && (
                    <small className={quality.passed ? "quality-ok" : "quality-failed"}>
                      {quality.passed ? `Quality gate aprovado · ${quality.caption_cue_count} legendas` : `Quality gate reprovado: ${quality.issues.join(", ") || "sem detalhe"}`}
                    </small>
                  )}
                  {publication && (
                    <div className={`publication-report ${publication.publish_ready ? "passed" : "failed"}`}>
                      <strong>{publication.publish_ready ? "Publish-ready" : "Publish blocked"}</strong>
                      {publication.reasons.length === 0
                        ? <small>Sem bloqueios de publicação.</small>
                        : publication.reasons.map((reason) => <small key={reason}>{publicationLabel(reason)}</small>)}
                    </div>
                  )}
                  {status.error && <small className="quality-failed">{status.error}</small>}
                </div>
                <div className="render-overrides">
                  <button
                    className="btn secondary"
                    onClick={() => updateOverride({
                      ...clipOverride,
                      headline: { ...(clipOverride.headline ?? {}), text: clipOverride.headline?.text ?? clip.title },
                    })}
                  >
                    customizar este corte
                  </button>
                  <Field label="Headline">
                    <textarea
                      value={clipOverride.headline?.text ?? ""}
                      placeholder={clip.title}
                      rows={2}
                      onChange={(event) => updateOverride({
                        ...clipOverride,
                        headline: { ...(clipOverride.headline ?? {}), text: event.target.value },
                      })}
                    />
                  </Field>
                  <Field label="Cor da legenda">
                    <input
                      type="text"
                      value={clipOverride.captions?.text_color ?? ""}
                      placeholder={settings.captions.text_color}
                      onChange={(event) => updateOverride({
                        ...clipOverride,
                        captions: { ...(clipOverride.captions ?? {}), text_color: event.target.value },
                      })}
                    />
                  </Field>
                  <Field label="Animação legenda">
                    <select
                      value={clipOverride.captions?.animation?.style ?? ""}
                      onChange={(event) => updateOverride({
                        ...clipOverride,
                        captions: {
                          ...(clipOverride.captions ?? {}),
                          animation: {
                            ...(clipOverride.captions?.animation ?? { duration_seconds: settings.captions.animation.duration_seconds }),
                            style: event.target.value as RenderSettings["captions"]["animation"]["style"],
                          },
                        },
                      })}
                    >
                      <option value="">global</option>
                      <option value="none">none</option>
                      <option value="fade">fade</option>
                      <option value="pop">pop</option>
                    </select>
                  </Field>
                  <Field label="Animação headline">
                    <select
                      value={clipOverride.headline?.animation?.entrance ?? ""}
                      onChange={(event) => updateOverride({
                        ...clipOverride,
                        headline: {
                          ...(clipOverride.headline ?? {}),
                          animation: {
                            ...(clipOverride.headline?.animation ?? { duration_seconds: settings.headline.animation.duration_seconds, exit: settings.headline.animation.exit }),
                            entrance: event.target.value as RenderSettings["headline"]["animation"]["entrance"],
                          },
                        },
                      })}
                    >
                      <option value="">global</option>
                      <option value="none">none</option>
                      <option value="fade">fade</option>
                      <option value="slide">slide</option>
                    </select>
                  </Field>
                  <OverrideStatus label="headline" current={clipOverride.headline?.text} global={clip.title}/>
                  <OverrideStatus label="caption color" current={clipOverride.captions?.text_color} global={settings.captions.text_color}/>
                  <OverrideStatus label="caption anim" current={clipOverride.captions?.animation?.style} global={settings.captions.animation.style}/>
                  <OverrideStatus label="headline anim" current={clipOverride.headline?.animation?.entrance} global={settings.headline.animation.entrance}/>
                  <small>{hasOverride ? "override ativo" : "usa global"}</small>
                </div>
                <span className={`render-state ${status.state === "rendering" ? "working" : status.state}`}>
                  {status.state === "rendering" ? `${Math.round(status.progress)}%` : status.state === "ready" ? (quality?.passed ? "PRONTO" : "REVISAR") : status.state === "failed" ? "FALHOU" : "EDL OK"}
                </span>
                {result && projectId && (
                  <div className="render-media">
                    <video controls preload="metadata" src={api.renderMediaUrl(projectId, result.artifactId)} aria-label={`Prévia de ${clip.title}`}/>
                    <a className="btn secondary render-download" href={api.renderMediaUrl(projectId, result.artifactId)} download>Baixar MP4</a>
                    {result.document.subtitles_path && <a className="btn secondary render-download" href={api.renderSubtitlesUrl(projectId, result.artifactId)} download>Baixar SRT</a>}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="panel glass gates">
          <div className="panel-title">
            <span><Icon name={allPassed ? "check" : "sliders"}/></span>
            <div><h3>Quality gate do render</h3><p>{completed.length ? `${completed.length} manifesto(s) persistido(s)` : "Aguardando outputs reais"}</p></div>
          </div>
          {completed.length === 0 ? (
            <div className="gate pending"><Icon name="clock"/><span>Nenhuma validação de mídia executada</span><b>PENDENTE</b></div>
          ) : completed.map(({ artifactId, document }) => (
            <div className={`render-gate-group ${document.quality.passed ? "" : "failed"}`} key={artifactId}>
              <div className="gate"><Icon name={document.quality.passed ? "check" : "sliders"}/><span>Áudio {document.quality.has_audio ? "presente" : "ausente"} · vídeo {document.quality.has_video ? "presente" : "ausente"}</span><b>{document.quality.passed ? "PASS" : "FAIL"}</b></div>
              <small>{document.segment_count} segmento(s) · delta {document.quality.duration_delta_seconds.toFixed(3)}s · {document.engine.requested_encoder} → {document.engine.effective_encoder}</small>
              {document.quality.integrated_loudness_lufs != null && <small>{document.quality.integrated_loudness_lufs.toFixed(1)} LUFS · true peak {document.quality.true_peak_dbfs?.toFixed(1) ?? "—"} dBFS</small>}
              {document.quality.visual_analysis_performed && <small>Visual · {document.quality.black_interval_count ?? 0} trecho(s) preto(s) · {document.quality.freeze_interval_count ?? 0} congelamento(s)</small>}
              {document.quality.issues.map((issue) => <small className="quality-failed" key={issue}>{issue}</small>)}
            </div>
          ))}
        </div>
      </div>
      {running && <div className="render-progress glass"><div className="render-orbit"><span>{Math.round(progress)}%</span><i/></div><div><span className="eyebrow">RENDERING // {settings.encoder === "h264_nvenc" ? "NVENC" : "CPU"}</span><h3>Aplicando trims e crossfades da EDL</h3><p>{clips.length} cortes na fila persistente</p><div className="process-track"><i style={{width:`${progress}%`}}/></div></div></div>}
      {error && <div className="error-strip" role="alert"><b>Falha na renderização</b><p>{error}</p></div>}
      <div className="step-actions"><span>{running ? "Você pode sair; a fila continuará em segundo plano" : `${clips.length} EDLs · ${timestamp(duration)} de timeline`}</span><button className="btn primary render-btn" disabled={running || !clips.length} onClick={onStart}><Icon name="play"/> {running ? "Produzindo cortes..." : "Iniciar renderização"}</button></div>
    </section>
  );
}

export default function App() {
  const [step, setStep] = useState(0); const [sourceMode, setSourceMode] = useState<"file" | "youtube">("file"); const [file, setFile] = useState<File | null>(null); const [youtube, setYoutube] = useState("");
  const [health, setHealth] = useState(false); const [telemetry, setTelemetry] = useState<Required<Telemetry>>(mockTelemetry); const [jobs, setJobs] = useState<ApiJob[]>([]); const [mobileNav, setMobileNav] = useState(false);
  const [transcribing, setTranscribing] = useState(false); const [transcriptionProgress, setTranscriptionProgress] = useState(0); const [transcriptionMessage, setTranscriptionMessage] = useState(""); const [transcriptionError, setTranscriptionError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptDocument | null>(null); const [analysis, setAnalysis] = useState<AnalysisDocument | null>(null);
  const [suggesting, setSuggesting] = useState(false); const [suggestionProgress, setSuggestionProgress] = useState(0); const [suggestionMessage, setSuggestionMessage] = useState(""); const [suggestionError, setSuggestionError] = useState<string | null>(null); const [selection, setSelection] = useState<SuggestionSelection | null>(null);
  const [editPlans, setEditPlans] = useState<Record<string, EditPlanDocument>>({}); const [editPlanArtifactIds, setEditPlanArtifactIds] = useState<Record<string, string>>({}); const [preparedClipKeys, setPreparedClipKeys] = useState<string[]>([]); const [planning, setPlanning] = useState(false); const [planProgress, setPlanProgress] = useState(0); const [planMessage, setPlanMessage] = useState(""); const [planError, setPlanError] = useState<string | null>(null);
  const [sceneIndex, setSceneIndex] = useState<SceneIndexDocument | null>(null); const [sceneIndexArtifactId, setSceneIndexArtifactId] = useState<string | null>(null); const [detectingScenes, setDetectingScenes] = useState(false); const [sceneProgress, setSceneProgress] = useState(0); const [sceneMessage, setSceneMessage] = useState(""); const [sceneError, setSceneError] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false); const [renderProgress, setRenderProgress] = useState(0); const [renderError, setRenderError] = useState<string | null>(null);
  const [renderResults, setRenderResults] = useState<Record<string, RenderResult>>({}); const [renderStatuses, setRenderStatuses] = useState<Record<string, RenderItemStatus>>({});
  const [renderOverrides, setRenderOverrides] = useState<Record<string, RenderSettingsPatch>>({});
  const [renderSettings, setRenderSettings] = useState<RenderSettings>(defaultRenderSettings);
  const projectRef = useRef<{ projectId: string; sourceAssetId: string; transcriptArtifactId?: string; analysisArtifactId?: string } | null>(null);
  const fileName = file?.name ?? "";

  useEffect(() => {
    let alive = true;
    async function refresh() {
      const results = await Promise.allSettled([api.health(), api.hardware(), api.jobs()]);
      if (!alive) return;
      setHealth(results[0].status === "fulfilled");
      if (results[1].status === "fulfilled") {
        const hardware = results[1].value;
        const gpu = hardware.gpus[0];
        setTelemetry({
          ...mockTelemetry,
          cpu_utilization: hardware.cpu.usage_percent,
          ram_used_gb: (hardware.cpu.memory_used_percent / 100) * mockTelemetry.ram_total_gb,
          device: gpu?.name || (gpu?.state === "available" ? "GPU detectada" : "GPU indisponível"),
          gpu_utilization: gpu?.usage_percent ?? 0,
          gpu_memory_used_mb: gpu?.memory_used_mb ?? 0,
          gpu_memory_total_mb: gpu?.memory_total_mb ?? mockTelemetry.gpu_memory_total_mb,
          gpu_temperature_c: gpu?.temperature_c ?? 0,
          encoder_utilization: gpu?.encoder_percent ?? 0,
          decoder_utilization: gpu?.decoder_percent ?? 0,
        });
      }
      if (results[2].status === "fulfilled") { const data = results[2].value; setJobs(Array.isArray(data) ? data : data.jobs || []); }
    }
    refresh(); const timer = window.setInterval(refresh, 2500); return () => { alive = false; window.clearInterval(timer); };
  }, []);

  async function ensureSourceAsset(): Promise<{ projectId: string; sourceAssetId: string }> {
    if (projectRef.current) return projectRef.current;
    const projectName = sourceMode === "file" ? fileName.replace(/\.[^.]+$/, "") || "Novo episódio" : youtube;
    const project = await api.createProject(projectName);

    let sourceAssetId: string;
    if (sourceMode === "file") {
      if (!file) throw new Error("Selecione um arquivo na etapa Fonte");
      setTranscriptionMessage("Enviando arquivo para o projeto");
      const asset = await api.uploadSource(project.id, file, (fraction) => {
        setTranscriptionProgress(fraction * 100);
        setTranscriptionMessage(`Enviando arquivo · ${Math.round(fraction * 100)}%`);
      });
      sourceAssetId = asset.id;
    } else {
      setTranscriptionMessage("Baixando vídeo do YouTube");
      const ingestJob = await api.createYoutubeSource(project.id, youtube);
      setJobs((current) => [ingestJob, ...current]);
      const finished = await api.watchJob(ingestJob.id, (job) => {
        setTranscriptionProgress(job.progress ?? 0);
        setTranscriptionMessage(job.message || "Baixando vídeo do YouTube");
      });
      if (finished.status !== "succeeded") throw new Error(finished.error || "Falha na ingestão do YouTube");
      sourceAssetId = String(finished.result?.source_asset_id ?? "");
      if (!sourceAssetId) throw new Error("Ingestão terminou sem source_asset_id");
    }
    projectRef.current = { projectId: project.id, sourceAssetId };
    return projectRef.current;
  }

  async function startTranscription(overrides: TranscribeOverrides) {
    if (!health || transcribing) return;
    setTranscribing(true); setTranscriptionError(null); setTranscriptionProgress(0);
    try {
      const { projectId, sourceAssetId } = await ensureSourceAsset();
      setTranscriptionProgress(0);
      setTranscriptionMessage("Transcrição na fila");
      const job = await api.startTranscription(projectId, sourceAssetId, overrides);
      setJobs((current) => [job, ...current]);
      const finished = await api.watchJob(job.id, (update) => {
        setTranscriptionProgress(update.progress ?? 0);
        setTranscriptionMessage(update.message || "Transcrevendo");
        setJobs((current) => current.map((j) => (j.id === update.id ? update : j)));
      });
      if (finished.status !== "succeeded") throw new Error(finished.error || `Transcrição terminou como ${finished.status}`);
      const transcriptArtifactId = String(finished.result?.transcript_artifact_id ?? "");
      if (!transcriptArtifactId) throw new Error("Transcrição terminou sem transcript_artifact_id");
      if (projectRef.current) projectRef.current.transcriptArtifactId = transcriptArtifactId;
      const transcriptEnvelope = await api.transcript(projectId, transcriptArtifactId);
      setTranscript(transcriptEnvelope.document);
      setTranscriptionProgress(0); setTranscriptionMessage("Análise local na fila");
      const analysisJob = await api.startAnalysis(projectId, transcriptArtifactId);
      setJobs((current) => [analysisJob, ...current]);
      const analyzed = await api.watchJob(analysisJob.id, (update) => {
        setTranscriptionProgress(update.progress ?? 0);
        setTranscriptionMessage(update.message || "Analisando áudio localmente");
        setJobs((current) => current.map((item) => item.id === update.id ? update : item));
      });
      if (analyzed.status !== "succeeded") throw new Error(analyzed.error || `Análise terminou como ${analyzed.status}`);
      const analysisArtifactId = String(analyzed.result?.analysis_artifact_id ?? "");
      if (!analysisArtifactId) throw new Error("Análise terminou sem analysis_artifact_id");
      if (projectRef.current) projectRef.current.analysisArtifactId = analysisArtifactId;
      const analysisEnvelope = await api.analysis(projectId, analysisArtifactId);
      setAnalysis(analysisEnvelope.document); setTranscriptionProgress(100);
      window.setTimeout(() => setStep(2), 400);
    } catch (error) {
      setTranscriptionError(error instanceof Error ? error.message : String(error));
    } finally {
      setTranscribing(false);
    }
  }

  async function startSuggestion(brief: SuggestionBrief) {
    const current = projectRef.current;
    if (!health || suggesting || !current?.transcriptArtifactId || !current.analysisArtifactId) {
      setSuggestionError("Conclua a transcrição e a análise local antes de solicitar sugestões");
      return;
    }
    setSuggesting(true); setSuggestionError(null); setSuggestionProgress(0); setSelection(null); setEditPlans({}); setEditPlanArtifactIds({}); setPreparedClipKeys([]); setRenderResults({}); setRenderStatuses({});
    try {
      const job = await api.startSuggestion(current.projectId, current.transcriptArtifactId, current.analysisArtifactId, brief);
      setJobs((items) => [job, ...items]);
      const finished = await api.watchJob(job.id, (update) => {
        setSuggestionProgress(update.progress ?? 0);
        setSuggestionMessage(update.message || "Selecionando cortes");
        setJobs((items) => items.map((item) => item.id === update.id ? update : item));
      });
      if (finished.status !== "succeeded") throw new Error(finished.error || `Seleção terminou como ${finished.status}`);
      const result = finished.result?.selection as SuggestionSelection | undefined;
      if (!result?.clips) throw new Error("Seleção terminou sem resultado estruturado");
      setSelection(result); setSuggestionProgress(100); setStep(3);
    } catch (error) {
      setSuggestionError(error instanceof Error ? error.message : String(error));
    } finally {
      setSuggesting(false);
    }
  }

  async function detectScenes() {
    const current = projectRef.current;
    if (!health || detectingScenes || !current?.sourceAssetId) {
      setSceneError("Conclua a ingestão da fonte antes de detectar cenas");
      return;
    }
    setDetectingScenes(true); setSceneError(null); setSceneProgress(0); setSceneMessage("Detecção de cenas na fila");
    try {
      const job = await api.startSceneIndex(current.projectId, current.sourceAssetId);
      setJobs((items) => [job, ...items]);
      const finished = await api.watchJob(job.id, (update) => {
        setSceneProgress(update.progress ?? 0);
        setSceneMessage(update.message || "Executando ffmpeg scdet");
        setJobs((items) => items.map((item) => item.id === update.id ? update : item));
      });
      if (finished.status !== "succeeded") throw new Error(finished.error || `Detecção de cenas terminou como ${finished.status}`);
      const artifactId = String(finished.result?.scene_index_artifact_id ?? "");
      if (!artifactId) throw new Error("Job terminou sem scene_index_artifact_id");
      const envelope = await api.sceneIndex(current.projectId, artifactId);
      setSceneIndex(envelope.document); setSceneIndexArtifactId(envelope.artifact.id); setSceneProgress(100);
    } catch (error) {
      setSceneError(error instanceof Error ? error.message : String(error));
    } finally {
      setDetectingScenes(false);
    }
  }

  async function prepareEditPlans(clips: SuggestedClip[]): Promise<boolean> {
    const current = projectRef.current;
    if (!health || planning || !current?.transcriptArtifactId || !current.analysisArtifactId) {
      setPlanError("Conclua a transcrição e a análise antes de resolver a EDL");
      return false;
    }
    const pending = clips.filter((clip) => !editPlans[clipKey(clip)]);
    if (!pending.length) return true;
    setPlanning(true); setPlanError(null); setPlanProgress(0);
    try {
      for (let index = 0; index < pending.length; index += 1) {
        const clip = pending[index];
        setPlanMessage(`Resolvendo ${index + 1} de ${pending.length} · ${clip.title}`);
        const job = await api.startEditPlan(current.projectId, current.transcriptArtifactId, current.analysisArtifactId, clip.start_second, clip.end_second, clip.pacing, sceneIndexArtifactId ?? undefined);
        setJobs((items) => [job, ...items]);
        const finished = await api.watchJob(job.id, (update) => {
          const overall = ((index + (update.progress ?? 0) / 100) / pending.length) * 100;
          setPlanProgress(overall);
          setPlanMessage(update.message || `Resolvendo ${index + 1} de ${pending.length}`);
          setJobs((items) => items.map((item) => item.id === update.id ? update : item));
        });
        if (finished.status !== "succeeded") throw new Error(finished.error || `Plano terminou como ${finished.status}`);
        const artifactId = String(finished.result?.edit_plan_artifact_id ?? "");
        if (!artifactId) throw new Error("Job terminou sem edit_plan_artifact_id");
        const envelope = await api.editPlan(current.projectId, artifactId);
        setEditPlans((plans) => ({ ...plans, [clipKey(clip)]: envelope.document }));
        setEditPlanArtifactIds((ids) => ({ ...ids, [clipKey(clip)]: envelope.artifact.id }));
      }
      setPlanProgress(100); setPlanMessage("EDL persistida e validada");
      return true;
    } catch (error) {
      setPlanError(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setPlanning(false);
    }
  }

  async function prepareAndOpenStudio(clips: SuggestedClip[]) {
    if (await prepareEditPlans(clips)) { setPreparedClipKeys(clips.map(clipKey)); setStep(4); }
  }

  async function startRender() {
    if (!health || rendering) return;
    const current = projectRef.current;
    const artifactIds = preparedClipKeys.map((key) => editPlanArtifactIds[key]).filter(Boolean);
    if (!current || artifactIds.length !== preparedClipKeys.length) { setRenderError("Há EDLs sem artifact persistido"); return; }
    setRendering(true); setRenderProgress(0); setRenderError(null);
    let activeEditPlanArtifactId = "";
    try {
      for (let index = 0; index < artifactIds.length; index += 1) {
        const editPlanArtifactId = artifactIds[index];
        activeEditPlanArtifactId = editPlanArtifactId;
        setRenderStatuses((items) => ({ ...items, [editPlanArtifactId]: { state: "rendering", progress: 0 } }));
        const clip = (selection?.clips ?? []).find((item) => clipKey(item) === preparedClipKeys[index]);
        const effectiveSettings = renderSettings.headline.enabled && !renderSettings.headline.text.trim()
          ? { ...renderSettings, headline: { ...renderSettings.headline, text: clip?.title ?? "" } }
          : renderSettings;
        const job = await api.startRender(current.projectId, editPlanArtifactId, effectiveSettings, renderOverrides[editPlanArtifactId]);
        setJobs((items) => [job, ...items]);
        const finished = await api.watchJob(job.id, (update) => {
          setRenderProgress(((index + (update.progress ?? 0) / 100) / artifactIds.length) * 100);
          setRenderStatuses((items) => ({ ...items, [editPlanArtifactId]: { state: "rendering", progress: update.progress ?? 0 } }));
          setJobs((items) => items.map((item) => item.id === update.id ? update : item));
        });
        if (finished.status !== "succeeded") throw new Error(finished.error || `Render terminou como ${finished.status}`);
        const artifactId = String(finished.result?.render_artifact_id ?? "");
        if (!artifactId) throw new Error("Render terminou sem render_artifact_id");
        const envelope = await api.render(current.projectId, artifactId);
        setRenderResults((items) => ({ ...items, [editPlanArtifactId]: { artifactId: envelope.artifact.id, document: envelope.document } }));
        setRenderStatuses((items) => ({ ...items, [editPlanArtifactId]: { state: "ready", progress: 100 } }));
      }
      setRenderProgress(100);
    } catch (error) { const message = error instanceof Error ? error.message : String(error); if (activeEditPlanArtifactId) setRenderStatuses((items) => ({ ...items, [activeEditPlanArtifactId]: { state: "failed", progress: items[activeEditPlanArtifactId]?.progress ?? 0, error: message } })); setRenderError(message); } finally {
      setRendering(false);
    }
  }

  const displayJobs = jobs;

  return <div className="app-shell">
    <header className="topbar"><button className="mobile-menu" onClick={() => setMobileNav(!mobileNav)} aria-label="Abrir navegação"><span/><span/><span/></button><div className="brand"><span className="brand-mark">CX<i/></span><div><b>Corte<span>X</span></b><small>AN HITECHX SYSTEM</small></div></div><div className="project-pill"><Icon name="folder"/><span><small>PROJETO ATUAL</small><b>{fileName ? fileName.replace(/\.[^.]+$/, "") : "Novo episódio"}</b></span><Icon name="chevron" size={14}/></div><div className="system-status"><span className={health ? "online" : "standby"}/><div><small>SYSTEM</small><b>{health ? "ONLINE" : "LOCAL MODE"}</b></div></div><button className="icon-button" aria-label="Configurações"><Icon name="settings"/></button></header>
    <aside className={`step-rail ${mobileNav ? "open" : ""}`}><div className="rail-label">PIPELINE // ワークフロー</div>{steps.map((item, index) => <button key={item.label} className={`${step === index ? "active" : ""} ${step > index ? "done" : ""}`} onClick={() => { setStep(index); setMobileNav(false); }}><span className="step-index">{step > index ? <Icon name="check" size={15}/> : String(index + 1).padStart(2, "0")}</span><span className="step-copy"><i>{item.jp}</i><b>{item.label}</b><small>{item.sub}</small></span><Icon name={item.icon}/></button>)}<div className="rail-foot"><span>CORE BUILD</span><b>v0.1.0-alpha</b><small>GPU-FIRST PIPELINE</small></div></aside>
    <main className="workspace">
      {step === 0 && <SourceStep sourceMode={sourceMode} setSourceMode={setSourceMode} fileName={fileName} setFile={(f) => { setFile(f); projectRef.current = null; setTranscript(null); setAnalysis(null); setSelection(null); setEditPlans({}); setEditPlanArtifactIds({}); setPreparedClipKeys([]); setRenderResults({}); setRenderStatuses({}); setRenderOverrides({}); setTranscriptionError(null); setSceneIndex(null); setSceneIndexArtifactId(null); setSceneError(null); }} youtube={youtube} setYoutube={(v) => { setYoutube(v); projectRef.current = null; setTranscript(null); setAnalysis(null); setSelection(null); setEditPlans({}); setEditPlanArtifactIds({}); setPreparedClipKeys([]); setRenderResults({}); setRenderStatuses({}); setRenderOverrides({}); setSceneIndex(null); setSceneIndexArtifactId(null); setSceneError(null); }} onNext={() => setStep(1)}/>}
      {step === 1 && <TranscriptionStep onStart={startTranscription} running={transcribing} progress={transcriptionProgress} message={transcriptionMessage} error={transcriptionError}/>}
      {step === 2 && <BriefStep onAnalyze={startSuggestion} running={suggesting} progress={suggestionProgress} message={suggestionMessage} error={suggestionError}/>} 
      {step === 3 && <CurateStep clips={selection?.clips ?? []} notes={selection?.selection_notes ?? "Execute a seleção editorial na etapa anterior."} transcript={transcript} analysis={analysis} plans={editPlans} planning={planning} planProgress={planProgress} planMessage={planMessage} planError={planError} onPlan={prepareEditPlans} onNext={prepareAndOpenStudio} sceneIndex={sceneIndex} sceneIndexArtifactId={sceneIndexArtifactId} detectingScenes={detectingScenes} sceneProgress={sceneProgress} sceneMessage={sceneMessage} sceneError={sceneError} onDetectScenes={() => void detectScenes()}/>}
      {step === 4 && <StudioStep settings={renderSettings} onChange={setRenderSettings} onNext={() => setStep(5)}/>}
      {step === 5 && <RenderStep onStart={startRender} running={rendering} progress={renderProgress} clips={(selection?.clips ?? []).filter((clip) => preparedClipKeys.includes(clipKey(clip)))} error={renderError} projectId={projectRef.current?.projectId} editPlanArtifactIds={editPlanArtifactIds} results={renderResults} statuses={renderStatuses} settings={renderSettings} overrides={renderOverrides} setOverrides={setRenderOverrides}/>}
    </main>
    <ComputeDeck telemetry={telemetry} online={health} jobs={displayJobs}/>
  </div>;
}
