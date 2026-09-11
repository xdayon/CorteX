import { JobProgress, type JobActivity } from "./JobProgress";
import { EpisodeLibrary } from "./EpisodeLibrary";
import { TranscriptEditor } from "./TranscriptEditor";
import type { CaptionCorrection } from "./api";
import { CaptionControls, LivePreview } from "./CaptionEditor";
import { FramingReview } from "./FramingReview";
import type { CameraScene } from "./api";
import { restoreEditorDraft, type EditorDraft, type VisualArtifacts } from "./editorDraft";
import { useEffect, useRef, useState, type ChangeEvent, type CSSProperties, type ReactNode } from "react";
import { api, type ApiJob, type IdentityIndexDocument, type RenderSettings, type SuggestedClip, type SuggestionSelection, type WorkflowRun } from "./api";

type IconName = "check" | "chevron" | "link" | "play" | "spark" | "upload" | "user";

const paths: Record<IconName, ReactNode> = {
  spark: <><path d="m12 3 1.2 4.2L17 9l-3.8 1.8L12 15l-1.2-4.2L7 9l3.8-1.8L12 3Z"/><path d="m19 15 .7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7L19 15ZM5 14l.8 2.2L8 17l-2.2.8L5 20l-.8-2.2L2 17l2.2-.8L5 14Z"/></>,
  upload: <><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5"/><path d="M5 14v5h14v-5"/></>,
  link: <><path d="m9 15 6-6"/><path d="M7.5 17.5h-1a4 4 0 0 1 0-8H10m4 5h3.5a4 4 0 0 0 0-8H14"/></>,
  play: <path d="m8 5 11 7-11 7V5Z"/>,
  check: <path d="m5 12 4 4L19 6"/>,
  chevron: <path d="m9 18 6-6-6-6"/>,
  user: <><circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/></>,
};

function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

const defaultRenderSettings: RenderSettings = {
  schema_version: 1,
  encoder: "h264_nvenc",
  canvas: { width: 1080, height: 1920, fps: 30 },
  framing: { schema_version: 1, mode: "blurred_background", punch_in: { enabled: false, scale: 1.15, anchor: "center", alternate_on_jump_cuts: true } },
  captions: {
    enabled: true, font_family: "Montserrat", font_size: 56, words_per_cue: 5,
    font_weight: 900, uppercase: true, position_y: 0.78,
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

function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const pickerValue = /^#[0-9a-fA-F]{6}/.test(value) ? value.slice(0, 7) : "#FFFFFF";
  function pick(rgb: string) { onChange(value.length === 9 ? `${rgb}${value.slice(7)}` : rgb); }
  return <Field label={label}><div className="color-control"><input type="color" value={pickerValue} onChange={(event) => pick(event.target.value.toUpperCase())}/><input type="text" value={value} maxLength={9} spellCheck={false} onChange={(event) => onChange(event.target.value.toUpperCase())}/></div></Field>;
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return <button type="button" className={`toggle-row ${checked ? "on" : ""}`} onClick={() => onChange(!checked)}><span>{label}</span><i><em /></i></button>;
}

function Range({ label, value, min, max, step = 1, suffix = "", onChange }: { label: string; value: number; min: number; max: number; step?: number; suffix?: string; onChange: (v: number) => void }) {
  const fill = `${((value - min) / (max - min)) * 100}%`;
  return <Field label={label}><div className="range-wrap"><input type="range" min={min} max={max} step={step} value={value} style={{ "--fill": fill } as CSSProperties} onChange={(e) => onChange(Number(e.target.value))}/><output>{value}{suffix}</output></div></Field>;
}

function timestamp(seconds: number) { const value = Math.max(0, Math.round(seconds)); const h = Math.floor(value / 3600); const m = Math.floor((value % 3600) / 60); const s = value % 60; return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`; }

function clipKey(clip: SuggestedClip) { return `${clip.rank}:${clip.start_second}:${clip.end_second}:${clip.pacing}`; }

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason);
}

function waitForPoll(signal: AbortSignal) {
  signal.throwIfAborted();
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, 1200);
    function abort() {
      window.clearTimeout(timer);
      reject(signal.reason);
    }
    signal.addEventListener("abort", abort, { once: true });
  });
}

async function finishJob(job: ApiJob, signal: AbortSignal, onUpdate?: (job: ApiJob) => void) {
  signal.throwIfAborted();
  const done = await api.watchJob(job.id, onUpdate, signal);
  signal.throwIfAborted();
  if (done.status !== "succeeded") throw new Error(done.error || `Job ${done.status}: ${done.message || job.type}`);
  return done;
}

function resultArtifact(job: ApiJob, key: string) {
  const value = job.result?.[key];
  if (typeof value !== "string" || !value) throw new Error(`Job terminou sem artifact ${key}`);
  return value;
}

type SimpleScreen = "library" | "new" | "review" | "export";

function CameraDynamicsPanel({
  projectId, artifactId, identityIndex, interviewerIdentityId, busy, message, onPrepare, onSelect,
}: {
  projectId: string;
  artifactId?: string;
  identityIndex: IdentityIndexDocument | null;
  interviewerIdentityId: string;
  busy: boolean;
  message: string;
  onPrepare: () => void;
  onSelect: (identityId: string) => void;
}) {
  const identities = identityIndex?.identities.filter((identity) => identity.status === "confirmed") ?? [];
  return <section className="camera-dynamics simple-card">
    <div><span className="eyebrow">OPCIONAL · DINÂMICA DE PODCAST</span><h3>Reações do entrevistador</h3><p>O CorteX procura os rostos e planos existentes no episódio. Se houver uma reação segura do entrevistador, ela pode entrar sem alterar o áudio editorial.</p></div>
    {!identityIndex && <button className="btn secondary" disabled={busy} onClick={onPrepare}><Icon name="user"/> {busy ? message || "Analisando pessoas..." : "Analisar pessoas e câmeras"}</button>}
    {identityIndex && identities.length === 0 && <small>{identityIndex.identities.length > 0 ? (new Set(identityIndex.identities.flatMap(i => i.layout_ids)).size < 2 ? "Foram encontrados rostos, mas os trechos analisados têm apenas um ângulo. Ainda não foi possível confirmar o entrevistador para reutilizar reações." : "Foram encontrados rostos, mas a correspondência entre ângulos ainda é incerta. Não foi possível confirmar o entrevistador para reutilizar reações.") : "Nenhum rosto pôde ser agrupado com segurança nos trechos analisados."} O automático continua disponível e preserva as cenas abertas.</small>}
    {identityIndex && identities.length > 0 && <div className="identity-choice"><strong>Quem é o entrevistador?</strong><div>{identities.map((identity) => <button key={identity.identity_id} disabled={busy} aria-label={`Selecionar entrevistador ${identity.identity_id}`} aria-pressed={interviewerIdentityId === identity.identity_id} className={interviewerIdentityId === identity.identity_id ? "selected" : ""} onClick={() => onSelect(identity.identity_id)}>{artifactId && <img src={api.identityPreviewUrl(projectId, artifactId, identity.identity_id)} alt="Rosto detectado"/>}<span>{interviewerIdentityId === identity.identity_id ? "ENTREVISTADOR" : "Selecionar"}</span></button>)}</div><small>{interviewerIdentityId ? "Entrevistador confirmado. Você pode habilitar o reaproveitamento de reações nas opções de enquadramento." : "Selecione explicitamente o entrevistador. Nenhum papel é inferido a partir de outro rosto."}</small></div>}
  </section>;
}

type RecentEpisode = {projectId: string; runId: string; label: string};
function recentEpisodes(): RecentEpisode[] {
  try { const value = JSON.parse(localStorage.getItem("cortex-recent-episodes") || "[]");
    return Array.isArray(value) ? value.filter(v => typeof v?.projectId === "string" && typeof v?.runId === "string" && typeof v?.label === "string").slice(0,10) : [];
  } catch { return []; }
}

export default function App() {
  const [captionEdits, setCaptionEdits] = useState<Record<string,CaptionCorrection[]>>({});
  const [headlines, setHeadlines] = useState<Record<string, string>>({});
  const [draftRunKey, setDraftRunKey] = useState<string | null>(null);
  const [draftError, setDraftError] = useState("");
  const [focusedKey, setFocusedKey] = useState("");
  const [previewVideo, setPreviewVideo] = useState<{url:string;title:string}|null>(null);
  const [recent, setRecent] = useState(recentEpisodes);
  const [screen, setScreen] = useState<SimpleScreen>("new");
  const [file, setFile] = useState<File | null>(null);
  const [youtube, setYoutube] = useState("");
  const [sourceMode, setSourceMode] = useState<"file" | "youtube">("youtube");
  const [count, setCount] = useState(10);
  const [minimum, setMinimum] = useState(40);
  const [maximum, setMaximum] = useState(120);
  const [newClipsOnly, setNewClipsOnly] = useState(true);
  const [primarySubject, setPrimarySubject] = useState("Dayon");
  const [instructions, setInstructions] = useState("Priorize trechos autocontidos, interessantes e com payoff forte.");
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [selection, setSelection] = useState<SuggestionSelection | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const episode = useRef<AbortController | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [settings, setSettings] = useState<RenderSettings>(defaultRenderSettings);
  const [currentJob, setCurrentJob] = useState<ApiJob | null>(null);
  const [jobHistory, setJobHistory] = useState<JobActivity[]>([]);
  const [renderProgress, setRenderProgress] = useState(0);
  const [rendered, setRendered] = useState<Array<{ title: string; url: string; subtitles: string }>>([]);
  const [visualBusy, setVisualBusy] = useState(false);
  const [visualProgress, setVisualProgress] = useState(0);
  const [visualMessage, setVisualMessage] = useState("");
  const [cameraScenes, setCameraScenes] = useState<CameraScene[]>([]);
  const [identityIndex, setIdentityIndex] = useState<IdentityIndexDocument | null>(null);
  const [interviewerIdentityId, setInterviewerIdentityId] = useState("");
  const [reuseReactions, setReuseReactions] = useState(false);
  const [visualArtifacts, setVisualArtifacts] = useState<VisualArtifacts | null>(null);

  useEffect(() => { setPreviewVideo(null); }, [settings, captionEdits, headlines, focusedKey]);

  useEffect(() => {
    if (!run || !selection || draftRunKey !== `${run.project_id}:${run.id}`) return;
    const draft: EditorDraft = {schema_version: 1, settings, selectedKeys, captionEdits, headlines, focusedKey, rendered, reuseReactions, visualArtifacts};
    try {
      localStorage.setItem(`cortex-draft:${draftRunKey}`, JSON.stringify(draft));
      setDraftError("");
    } catch { setDraftError("Não foi possível salvar os ajustes neste navegador. Libere espaço antes de fechar a página."); }
  }, [run, selection, draftRunKey, settings, selectedKeys, captionEdits, headlines, focusedKey, rendered, reuseReactions, visualArtifacts]);

  function trackJob(job: ApiJob) {
    setCurrentJob(job);
    setJobHistory(items => {
      const last = items.at(-1)?.job;
      if (last?.id === job.id && last.status === job.status && last.progress === job.progress && last.message === job.message) return items;
      return [...items.slice(-39), {job, receivedAt: Date.now()}];
    });
  }
  async function completeJob(job: ApiJob, signal: AbortSignal, onUpdate?: (job: ApiJob) => void) {
    trackJob(job);
    const done = await finishJob(job, signal, update => { if (!signal.aborted) {trackJob(update); onUpdate?.(update);} });
    trackJob(done);
    return done;
  }

  function beginEpisode() {
    episode.current?.abort();
    const controller = new AbortController();
    episode.current = controller;
    return controller.signal;
  }

  function clearEpisodeState() {
    setCurrentJob(null); setJobHistory([]);
    setDraftRunKey(null); setDraftError("");
    setRun(null); setSelection(null); setSelectedKeys([]); setCaptionEdits({}); setHeadlines({});
    setFocusedKey(""); setPreviewVideo(null); setRendered([]);
    setError(null); setWarnings([]); setUploadProgress(0); setRenderProgress(0);
    setSettings(defaultRenderSettings); setIdentityIndex(null); setVisualArtifacts(null);
    setInterviewerIdentityId(""); setReuseReactions(false); setVisualProgress(0); setVisualMessage(""); setCameraScenes([]);
  }

  function resetEpisode() {
    if (busy || visualBusy) return;
    if (run) {
      const entry = {projectId:run.project_id,runId:run.id,label:selection?.clips[0]?.title || "Episódio anterior"};
      const next = [entry,...recent.filter(r => r.runId !== run.id)].slice(0,10);
      localStorage.setItem("cortex-recent-episodes", JSON.stringify(next)); setRecent(next);
    }
    beginEpisode(); localStorage.removeItem("cortex-active-run");
    clearEpisodeState();
    setScreen("new"); setFile(null); setYoutube(""); setSourceMode("youtube");
  }

  async function resumeEpisode(item: RecentEpisode) {
    if (busy || visualBusy) return;
    const signal = beginEpisode(); clearEpisodeState(); setBusy(true); setScreen("new");
    try {
      localStorage.setItem("cortex-active-run", `${item.projectId}:${item.runId}`);
      await followRun(await api.workflowRun(item.projectId,item.runId,signal),signal);
    } catch (reason) { if (!signal.aborted) setError(errorMessage(reason)); }
    finally { if (!signal.aborted) setBusy(false); }
  }

  async function loadReadyRun(next: WorkflowRun, signal: AbortSignal) {
    signal.throwIfAborted();
    setRun(next);
    if (next.status === "failed") throw new Error(next.error || "O processamento falhou");
    if (next.status === "cancelled") throw new Error("O processamento foi cancelado");
    if (next.status !== "ready_for_review") return;
    const artifactId = next.artifacts.suggestion;
    if (!artifactId) throw new Error("Processamento terminou sem sugestões persistidas");
    const envelope = await api.suggestion(next.project_id, artifactId);
    signal.throwIfAborted();
    const result = envelope.document.selection;
    const runKey = `${next.project_id}:${next.id}`;
    const draft = restoreEditorDraft(localStorage, runKey, result.clips.map(clipKey), defaultRenderSettings);
    setSelection({ ...result, provenance: envelope.document.provenance });
    setSettings(draft.settings); setSelectedKeys(draft.selectedKeys); setCaptionEdits(draft.captionEdits);
    setHeadlines(draft.headlines); setFocusedKey(draft.focusedKey); setRendered(draft.rendered);
    setReuseReactions(draft.reuseReactions);

    setScreen("review");
    const persistedVisual = {
      scene: next.artifacts.scene_index,
      face: next.artifacts.face_index,
      speaker: next.artifacts.speaker_timeline,
      camera: next.artifacts.camera_timeline,
      quality: next.artifacts.visual_quality,
      identity: next.artifacts.identity_index,
    };
    const visual = Object.values(persistedVisual).every(Boolean) ? persistedVisual : draft.visualArtifacts;
    if (visual) {
      try {
        const identities = await api.identityIndex(next.project_id, visual.identity);
        signal.throwIfAborted();
        setVisualArtifacts(visual);
        setIdentityIndex(identities.document);
        setInterviewerIdentityId(next.interviewer_identity_id || "");
        const cameras = await api.cameraTimeline(next.project_id, visual.camera);
        signal.throwIfAborted();
        setCameraScenes(cameras.document.scenes);
      } catch (reason) {
        signal.throwIfAborted();
        setWarnings((items) => [...items, `Não foi possível restaurar as câmeras. Os cortes normais continuam disponíveis. ${errorMessage(reason)}`]);
      }
    }
    signal.throwIfAborted();
    setDraftRunKey(runKey);
  }

  async function followRun(initial: WorkflowRun, signal: AbortSignal) {
    let current = initial;
    signal.throwIfAborted();
    setRun(current);
    while (current.status === "queued" || current.status === "running") {
      await waitForPoll(signal);
      current = await api.workflowRun(current.project_id, current.id, signal);
      signal.throwIfAborted();
      setRun(current);
    }
    await loadReadyRun(current, signal);
  }

  useEffect(() => {
    beginEpisode();
    const saved = window.localStorage.getItem("cortex-active-run");
    if (saved) {
      const [projectId, runId] = saved.split(":");
      if (projectId && runId) setRecent(items => items.some(item => item.runId === runId) ? items : [{projectId,runId,label:"Retomar último episódio"},...items]);
    }
    return () => { episode.current?.abort(); };
  }, []);

  async function processEpisode() {
    if (busy || visualBusy) return;
    const signal = beginEpisode();
    clearEpisodeState(); setBusy(true); setScreen("new");
    window.localStorage.removeItem("cortex-active-run");
    try {
      if (sourceMode === "file" && !file) throw new Error("Selecione o arquivo do episódio");
      if (sourceMode === "youtube" && !/^https?:\/\//.test(youtube)) throw new Error("Cole um link válido do YouTube");
      const name = sourceMode === "file"
        ? (file?.name.replace(/\.[^.]+$/, "") || "Novo episódio")
        : youtube;
      let project = sourceMode === "file" ? await api.createProject(name) : {id:""};
      signal.throwIfAborted();
      let sourceAssetId = "";
      if (sourceMode === "file") {
        const source = await api.uploadSource(project.id, file!, (progress) => {
          if (!signal.aborted) setUploadProgress(progress);
        });
        signal.throwIfAborted();
        sourceAssetId = source.id;
      } else {
        const entry = await api.registerEpisode(youtube);
        signal.throwIfAborted();
        if (entry.archived) await api.archiveEpisode(entry.id, false);
        if (!entry.metadata_updated_at) await api.refreshEpisodeMetadata(entry.id);
        signal.throwIfAborted();
        const download = await api.downloadEpisode(entry.id);
        if (download.source) {
          project = {id: download.source.project_id}; sourceAssetId = download.source.id;
        } else if (download.job) {
          project = {id: entry.project_ids[0]};
          const finished = await completeJob(download.job, signal, job => setUploadProgress(job.progress || 0));
          sourceAssetId = resultArtifact(finished, "source_asset_id");
        } else throw new Error("Download sem fonte nem tarefa");
      }
      const created = await api.createWorkflowRun(project.id, sourceAssetId, {
        count, minimum_seconds: minimum, maximum_seconds: maximum, instructions, primary_subject: primarySubject, new_clips_only: newClipsOnly,
      });
      signal.throwIfAborted();
      window.localStorage.setItem("cortex-active-run", `${project.id}:${created.id}`);
      await followRun(created, signal);
    } catch (reason) {
      if (!signal.aborted) setError(errorMessage(reason));
    } finally { if (!signal.aborted) setBusy(false); }
  }

  async function prepareCameraDynamics() {
    if (!run || visualBusy || busy || !episode.current) return;
    const signal = episode.current.signal;
    setVisualBusy(true); setError(null); setVisualMessage("Detectando mudanças de câmera");
    try {
      async function finish(job: ApiJob, label: string, key: string) {
        signal.throwIfAborted();
        setVisualMessage(label); setVisualProgress(0);
        const done = await completeJob(job, signal, (update) => {
          if (!signal.aborted) { setVisualMessage(update.message || label); setVisualProgress(update.progress || 0); }
        });
        return resultArtifact(done, key);
      }
      const sceneId = await finish(await api.startSceneIndex(run.project_id, run.source_asset_id, activeClips.map(c => ({start:Math.max(0,c.start_second-3),end:c.end_second+3}))), "Detectando cenas", "scene_index_artifact_id");
      const faceId = await finish(await api.startFaceIndex(run.project_id, run.source_asset_id, sceneId), "Encontrando rostos", "face_index_artifact_id");
      const speakerId = await finish(await api.startSpeakerTimeline(run.project_id, run.source_asset_id, sceneId, faceId, run.artifacts.analysis), "Relacionando fala e imagem", "speaker_timeline_artifact_id");
      const cameraId = await finish(await api.startCameraTimeline(run.project_id, sceneId, faceId, speakerId), "Entendendo os planos do podcast", "camera_timeline_artifact_id");
      const qualityId = await finish(await api.startVisualQuality(run.project_id, run.source_asset_id, sceneId, faceId), "Validando qualidade visual", "visual_quality_artifact_id");
      const identityId = await finish(await api.startIdentityIndex(run.project_id, faceId, cameraId), "Agrupando as pessoas", "identity_index_artifact_id");
      const envelope = await api.identityIndex(run.project_id, identityId);
      signal.throwIfAborted();
      setIdentityIndex(envelope.document);
      const cameras = await api.cameraTimeline(run.project_id, cameraId);
      signal.throwIfAborted();
      setCameraScenes(cameras.document.scenes);
      const prepared = { scene: sceneId, face: faceId, speaker: speakerId, camera: cameraId, quality: qualityId, identity: identityId };
      setVisualArtifacts(prepared);
      if (visualArtifacts && visualArtifacts.scene !== sceneId && settings.framing.scene_overrides?.length) {
        setSettings(v=>({...v,framing:{...v.framing,scene_overrides:[]}}));
        throw new Error("Os trechos analisados mudaram. Revise os ajustes manuais de enquadramento antes de renderizar.");
      }
      if (identityId !== visualArtifacts?.identity) setInterviewerIdentityId("");
      setVisualMessage("Enquadramento pronto. Identificar o entrevistador é opcional para reutilizar reações.");
      return prepared;
    } catch (reason) {
      if (!signal.aborted) setWarnings((items) => [...items, `A dinâmica de câmera não ficou pronta, mas os cortes normais continuam disponíveis. ${errorMessage(reason)}`]);
    } finally { if (!signal.aborted) setVisualBusy(false); }
  }

  async function selectInterviewer(identityId: string) {
    if (!run || !visualArtifacts || visualBusy || busy || !episode.current) return;
    const signal = episode.current.signal;
    setVisualBusy(true); setError(null);
    try {
      const updated = await api.selectWorkflowIdentity(run.project_id, run.id, identityId, visualArtifacts);
      signal.throwIfAborted();
      setRun(updated);
      setInterviewerIdentityId(updated.interviewer_identity_id || "");
    } catch (reason) {
      if (!signal.aborted) setError(errorMessage(reason));
    } finally { if (!signal.aborted) setVisualBusy(false); }
  }

  async function renderSelected(preview = false) {
    if (!run || !selection || busy || visualBusy || !episode.current) return;
    const signal = episode.current.signal;
    const clips = preview ? (focusedClip ? [focusedClip] : []) : selection.clips.filter((clip) => selectedKeys.includes(clipKey(clip)));
    if (!clips.length) { setError("Selecione pelo menos um corte"); return; }
    setBusy(true); setJobHistory([]); setCurrentJob(null); setError(null); if (!preview) setRendered([]); else setPreviewVideo(null); setRenderProgress(0);
    try {
      const visual = settings.framing.mode === "speaker_auto"
        ? await prepareCameraDynamics() : visualArtifacts;
      signal.throwIfAborted();
      if (settings.framing.mode === "speaker_auto" && !visual) {
        throw new Error("A análise de câmeras não terminou. Tente novamente ou escolha Quadro inteiro com fundo desfocado.");
      }
      if (reuseReactions && visual && visual.identity !== visualArtifacts?.identity) {
        throw new Error("A análise de pessoas foi atualizada. Confirme o entrevistador antes de reaproveitar reações.");
      }
      let reactionArtifactId: string | undefined;
      if (visual && interviewerIdentityId && reuseReactions) {
        try {
          const reactionJob = await api.startReactionCandidates(
            run.project_id, visual.speaker, visual.camera,
            visual.identity, visual.quality, interviewerIdentityId,
          );
          reactionArtifactId = resultArtifact(await completeJob(reactionJob, signal), "reaction_candidate_artifact_id");
        } catch (reason) {
          signal.throwIfAborted();
          setWarnings((items) => [...items, `Render continuará sem reações: ${errorMessage(reason)}`]);
        }
      }
      for (let index = 0; index < clips.length; index += 1) {
        signal.throwIfAborted();
        const clip = clips[index];
        const planJob = await api.startEditPlan(
          run.project_id, run.artifacts.transcript, run.artifacts.analysis,
          clip.start_second, preview ? Math.min(clip.end_second,clip.start_second+10) : clip.end_second, clip.pacing,
        );
        const planDone = await completeJob(planJob, signal, (job) => {
          if (!signal.aborted) setRenderProgress(((index + (job.progress || 0) / 200) / clips.length) * 100);
        });
        const planId = resultArtifact(planDone, "edit_plan_artifact_id");
        let cameraPlanId: string | undefined;
        if (visual && (settings.framing.mode === "speaker_auto" || reuseReactions)) {
          try {
            const cameraPlanJob = await api.startCameraPlan(
              run.project_id, planId, visual.camera, visual.identity,
              visual.quality, reactionArtifactId,
            );
            cameraPlanId = resultArtifact(await completeJob(cameraPlanJob, signal), "camera_edit_plan_artifact_id");
          } catch (reason) {
            signal.throwIfAborted();
            if (settings.framing.mode === "speaker_auto") throw reason;
            setWarnings((items) => [...items, `“${clip.title}” seguirá sem plano de câmeras: ${errorMessage(reason)}`]);
          }
        }
        const withCorrections = {...settings,captions:{...settings.captions,corrections:captionEdits[clipKey(clip)] || []}};
        const effective = {...withCorrections, headline: {...settings.headline,
          text: headlines[clipKey(clip)]?.trim() || clip.headline || clip.title,
        }};
        const renderJob = await api.startRender(run.project_id, planId, effective, cameraPlanId);
        const renderDone = await completeJob(renderJob, signal, (job) => {
          if (!signal.aborted) setRenderProgress(((index + 0.5 + (job.progress || 0) / 200) / clips.length) * 100);
        });
        const artifactId = resultArtifact(renderDone, "render_artifact_id");
        try {
          const manifest = await api.renderArtifact(run.project_id, artifactId);
          signal.throwIfAborted();
          const contextShots = manifest.document.auto_framing?.filter((span) => span.mode === "blurred_background" && span.reason !== "manual_full_frame").length || 0;
          if (contextShots) setWarnings((items) => [...items, `“${clip.title}”: ${contextShots} planos mantiveram o quadro inteiro porque a cena mostrava o contexto da conversa ou não permitia um recorte seguro.`]);
        } catch (reason) {
          signal.throwIfAborted();
          setWarnings((items) => [...items, `Vídeo pronto; não foi possível ler os detalhes do enquadramento: ${errorMessage(reason)}`]);
        }
        if (preview) setPreviewVideo({title:clip.title,url:api.renderMediaUrl(run.project_id,artifactId)});
        else setRendered((items) => [...items, {
          title: clip.title,
          url: api.renderMediaUrl(run.project_id, artifactId),
          subtitles: api.renderSubtitlesUrl(run.project_id, artifactId),
        }]);
      }
      setRenderProgress(100);
    } catch (reason) {
      if (!signal.aborted) setError(errorMessage(reason));
    } finally { if (!signal.aborted) setBusy(false); }
  }

  const activeClips = selection?.clips.filter((clip) => selectedKeys.includes(clipKey(clip))) ?? [];
  const focusedClip = activeClips.find(c => clipKey(c) === focusedKey) || activeClips[0];
  return <div className="simple-app">
    <header className="simple-header"><div className="brand"><span className="brand-mark">CX<i/></span><div><b>Corte<span>X</span></b><small>PODCAST CLIPPER</small></div></div><nav aria-label="Jornada do episódio">{(["new", "review", "export"] as SimpleScreen[]).map((item, index) => <button key={item} className={screen === item ? "active" : ""} disabled={(item === "new" && (busy || visualBusy)) || ((item === "review" || item === "export") && !selection)} onClick={() => item === "new" ? resetEpisode() : setScreen(item)}><span>{index + 1}</span>{item === "new" ? "Novo episódio" : item === "review" ? "Escolher cortes" : "Exportar"}</button>)}</nav><button className={`library-nav ${screen === "library" ? "active" : ""}`} onClick={() => setScreen("library")}>Biblioteca</button><span className={`simple-status ${busy ? "working" : ""}`}>{busy ? "PROCESSANDO" : "LOCAL"}</span></header>
    <main className="simple-main">

      {(screen === "new" || screen === "library") && !run && recent.length > 0 && <div className="episode-history"><b>Retomar episódio</b>{recent.map(item => <button key={item.runId} className="btn secondary" disabled={busy || visualBusy} onClick={() => void resumeEpisode(item)}>{item.label}</button>)}</div>}

      {screen === "library" && <EpisodeLibrary busy={busy || visualBusy} onOpen={saved => void resumeEpisode({projectId:saved.project_id, runId:saved.id, label:saved.message})} onUse={entry => {
        resetEpisode(); setYoutube(entry.url || ""); setPrimarySubject(entry.primary_subject); setNewClipsOnly(true); setScreen("new");
        if (!entry.url && entry.source) { void (async () => {
          const signal = beginEpisode(); setBusy(true);
          try { const created = await api.createWorkflowRun(entry.source!.project_id, entry.source!.id, {count, minimum_seconds:minimum, maximum_seconds:maximum, instructions, primary_subject:entry.primary_subject, new_clips_only:true});
            localStorage.setItem("cortex-active-run", `${created.project_id}:${created.id}`); await followRun(created,signal);
          } catch(reason) { if(!signal.aborted) setError(errorMessage(reason)); } finally { if(!signal.aborted) setBusy(false); }
        })(); }
      }}/>}
      {screen === "new" && <section className="simple-page enter"><div className="simple-title"><span>01 · NOVO EPISÓDIO</span><h1>Do episódio aos cortes, em um clique.</h1><p>Envie o vídeo, escolha a duração e deixe o CorteX transcrever, analisar e encontrar os melhores momentos.</p></div><div className="simple-grid"><div className="simple-card source-card"><div className="source-tabs"><button className={sourceMode === "file" ? "active" : ""} onClick={() => setSourceMode("file")}><Icon name="upload"/> Arquivo</button><button className={sourceMode === "youtube" ? "active" : ""} onClick={() => setSourceMode("youtube")}><Icon name="link"/> YouTube</button></div>{sourceMode === "file" ? <label className={`dropzone ${file ? "has-file" : ""}`}><input type="file" accept="video/*,audio/*" onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] || null)}/><Icon name={file ? "check" : "upload"} size={30}/><strong>{file?.name || "Solte o episódio aqui"}</strong><small>MP4, MOV, MKV, WebM, MP3 ou WAV</small></label> : <div className="simple-url"><Icon name="link"/><input value={youtube} onChange={(event) => setYoutube(event.target.value)} placeholder="https://youtube.com/watch?v=..."/></div>}</div><div className="simple-card settings-card"><h3>O que você quer receber</h3><Range label="Quantidade" value={count} min={1} max={25} onChange={setCount}/><Range label="Mínimo" value={minimum} min={15} max={180} suffix="s" onChange={(value) => { setMinimum(value); if (value > maximum) setMaximum(value); }}/><Range label="Máximo" value={maximum} min={15} max={180} suffix="s" onChange={(value) => { setMaximum(value); if (value < minimum) setMinimum(value); }}/><Field label="Protagonista dos cortes" hint="Orienta a IA; não reconhece a voz pelo nome"><input value={primarySubject} onChange={event => setPrimarySubject(event.target.value)}/></Field><p className="field-help">Para confirmar sua voz, use “Identificar a voz do Dayon” no episódio salvo na Biblioteca.</p><Toggle checked={newClipsOnly} onChange={setNewClipsOnly} label="Evitar trechos já sugeridos"/><Field label="Direção editorial" hint="Este pedido é enviado à IA para escolher os trechos"><textarea placeholder="Ex.: priorize minhas explicações sobre consciência; inclua a pergunta quando ajudar a entender; preserve o contexto e evite frases cortadas." value={instructions} onChange={(event) => setInstructions(event.target.value)}/></Field></div></div>{run && busy && <div className="simple-progress"><div><b>{run.message}</b><span>{Math.round(run.progress)}%</span></div><i><em style={{ width: `${run.progress}%` }}/></i><small>{uploadProgress > 0 && uploadProgress < 1 ? `Enviando · ${Math.round(uploadProgress * 100)}%` : "O processamento continua mesmo se você fechar esta tela."}</small></div>}<button className="btn primary simple-primary" disabled={busy || visualBusy || (sourceMode === "file" ? !file : !youtube)} onClick={() => void processEpisode()}><Icon name="spark"/> {busy ? "Processando episódio..." : "Processar episódio"}</button></section>}
      {screen === "review" && selection && run && <section className="simple-page enter"><div className="simple-title row"><div><span>02 · ESCOLHER CORTES</span><h1>{selection.clips.length} momentos encontrados.</h1><p>{selection.selection_notes}</p></div><button className="btn primary" disabled={!activeClips.length} onClick={() => setScreen("export")}>Editar {activeClips.length} selecionado{activeClips.length === 1 ? "" : "s"} <Icon name="chevron"/></button></div><div className="clip-grid">{selection.clips.map((clip) => { const key = clipKey(clip); const selected = selectedKeys.includes(key); return <article key={key} className={`simple-clip ${selected ? "selected" : ""}`} onClick={() => setSelectedKeys((items) => selected ? items.filter((item) => item !== key) : [...items, key])}><div className="clip-frame"><img src={api.sourcePreviewUrl(run.project_id, run.source_asset_id, clip.start_second + 1)} alt="Preview do corte"/><span>{timestamp(clip.estimated_duration)}</span><i>{selected ? <Icon name="check"/> : null}</i></div><div><span className="clip-rank">#{clip.rank} · {clip.primary_speaker}</span><h3>{clip.title}</h3><p>{clip.reasoning}</p><strong>{timestamp(clip.start_second)} — {timestamp(clip.end_second)}</strong></div></article>; })}</div></section>}
      {screen === "export" && selection && run && <section className="simple-page enter"><div className="simple-title"><span>03 · EXPORTAR</span><h1>Seu corte, do seu jeito.</h1><p>{activeClips.length} cortes selecionados · 1080 × 1920. Estilo aplicado ao lote; headline e correções de legenda são individuais.</p><p>Mantenha esta tela aberta para enviar todos os cortes à fila. Cada render enviado continua no servidor.</p></div><div className="export-layout"><fieldset disabled={busy || visualBusy} className="export-controls simple-card"><legend className="sr-only">Ajustes da exportação</legend><h3>Enquadramento</h3><Field label="Composição"><select value={settings.framing.mode} disabled={busy || visualBusy} onChange={(event) => setSettings((value) => ({ ...value, framing: { ...value.framing, mode: event.target.value as RenderSettings["framing"]["mode"] } }))}><option value="speaker_auto">Automático por pessoa e câmera</option><option value="blurred_background">Quadro inteiro com fundo desfocado</option><option value="vertical_crop">Recorte central</option></select></Field><p>{settings.framing.mode === "blurred_background" ? "Mantém o vídeo inteiro sobre um fundo com Gaussian blur. Ideal para mostrar a mesa e todos os participantes." : settings.framing.mode === "speaker_auto" ? "Preserva cenas abertas e acompanha os closes existentes. Analisa somente os cortes selecionados com margem de 3 segundos e reutiliza o cache." : "Preenche a tela vertical pelo centro do vídeo. As laterais ficam fora do quadro."}</p>{settings.framing.mode === "blurred_background" && <Range label="Posição vertical do vídeo" value={Math.round((settings.framing.position_y ?? .5)*100)} min={0} max={100} suffix="%" onChange={value => setSettings(s => ({...s, framing:{...s.framing,position_y:value/100}}))}/>}<div className="toggles"><Toggle checked={settings.framing.punch_in.enabled} onChange={(enabled) => setSettings((value) => ({ ...value, framing: { ...value.framing, punch_in: { ...value.framing.punch_in, enabled } } }))} label="Zoom discreto alternado (1,15×)"/></div><p className="field-help">O zoom alterna uma ampliação fixa entre trechos separados por cortes de pausa. Não é um movimento contínuo; um trecho único pode ficar sem zoom. Planos abertos são preservados no automático.</p>{reuseReactions && <p>{interviewerIdentityId ? "Usa apenas candidatos validados do entrevistador, preservando o áudio da sua fala." : "Analise as pessoas e selecione o entrevistador abaixo para habilitar o reaproveitamento."}</p>}{focusedClip && <><Field label="Corte em revisão"><select disabled={busy || visualBusy} value={clipKey(focusedClip)} onChange={e=>{setFocusedKey(e.target.value);setPreviewVideo(null);}}>{activeClips.map(c=><option key={clipKey(c)} value={clipKey(c)}>{c.title}</option>)}</select></Field><TranscriptEditor projectId={run.project_id} artifactId={run.artifacts.transcript} clip={focusedClip} corrections={captionEdits[clipKey(focusedClip)] || []} disabled={busy || visualBusy} onChange={edits=>{
 const next={...captionEdits,[clipKey(focusedClip)]:edits}; setCaptionEdits(next); setPreviewVideo(null);
}}/></>}      {screen === "export" && run && focusedClip && <section className="headline-editor">
        <h3>Headline deste corte</h3><Toggle checked={settings.headline.enabled} onChange={(enabled) => setSettings(value => ({...value, headline:{...value.headline,enabled}}))} label="Mostrar headline"/>
        <Field label="Headline manual" hint="Campo vazio usa a sugestão da IA">
          <textarea maxLength={300} disabled={busy || visualBusy} value={headlines[clipKey(focusedClip)] || ""}
            placeholder={focusedClip.headline || focusedClip.title}
            onChange={event => setHeadlines(current => ({...current, [clipKey(focusedClip)]: event.target.value}))}/>
        </Field>
        <p>Sugestão da IA: {focusedClip.headline || focusedClip.title}</p>
        <details><summary>Estilo da headline</summary>
          <Field label="Fonte da headline"><select value={settings.headline.font_family} onChange={e => setSettings(s => ({...s,headline:{...s.headline,font_family:e.target.value}}))}>{['Montserrat','Lato','DejaVu Sans'].map(f => <option key={f}>{f}</option>)}</select></Field>
          <Range label="Escala da headline" value={settings.headline.font_size} min={24} max={96} onChange={font_size => setSettings(s => ({...s,headline:{...s.headline,font_size}}))}/>
          <Range label="Tempo da headline" value={settings.headline.duration_seconds} min={1} max={10} suffix="s" onChange={duration_seconds => setSettings(s => ({...s,headline:{...s.headline,duration_seconds}}))}/>
          <Field label="Entrada da headline"><select value={settings.headline.animation.entrance} onChange={e => setSettings(s => ({...s,headline:{...s.headline,animation:{...s.headline.animation,entrance:e.target.value as RenderSettings['headline']['animation']['entrance']}}}))}><option value="none">Sem animação</option><option value="fade">Aparecer suave</option><option value="slide">Deslizar</option></select></Field>
        </details>
        <p>{settings.headline.enabled ? `Texto para exportação: ${headlines[clipKey(focusedClip)]?.trim() || focusedClip.headline || focusedClip.title}` : "Headline desativada."}</p>
        <button className="btn secondary" disabled={busy || visualBusy || !headlines[clipKey(focusedClip)]}
          onClick={() => setHeadlines(current => { const next = {...current}; delete next[clipKey(focusedClip)]; return next; })}>Usar sugestão da IA</button>
        <p>Revise a acentuação e os nomes próprios. O texto aparece na prévia ao vivo e na exportação. Os ajustes são salvos automaticamente neste navegador.</p>
      </section>}
<CaptionControls controls={<> <Range label="Tamanho" value={settings.captions.font_size} min={16} max={96} onChange={(font_size) => setSettings((value) => ({ ...value, captions: { ...value.captions, font_size } }))}/><ColorField label="Texto" value={settings.captions.text_color} onChange={(text_color) => setSettings((value) => ({ ...value, captions: { ...value.captions, text_color } }))}/><ColorField label="Palavra ativa" value={settings.captions.karaoke_color} onChange={(karaoke_color) => setSettings((value) => ({ ...value, captions: { ...value.captions, karaoke_color } }))}/> </>} settings={settings} disabled={busy || visualBusy} onChange={(patch) => setSettings(value => ({...value, captions:{...value.captions,...patch}}))}/>
<details className="reactions-options"><summary>Reações e câmeras · opcional</summary><Toggle checked={reuseReactions} onChange={setReuseReactions} label="Reaproveitar reações de outro instante"/>      {screen === "export" && run && settings.framing.mode === "speaker_auto" && <FramingReview
        projectId={run.project_id} sourceId={run.source_asset_id} scenes={cameraScenes}
        clips={activeClips} disabled={busy || visualBusy} overrides={settings.framing.scene_overrides || []}
        onChange={(scene_overrides) => setSettings((value) => ({ ...value, framing: { ...value.framing, scene_overrides } }))}
      />}
      {screen === "export" && run && <CameraDynamicsPanel projectId={run.project_id} artifactId={visualArtifacts?.identity} identityIndex={identityIndex} interviewerIdentityId={interviewerIdentityId} busy={visualBusy || busy} message={visualMessage} onPrepare={() => void prepareCameraDynamics()} onSelect={(identityId) => void selectInterviewer(identityId)}/>}
</details>
</fieldset><aside className="export-preview"><LivePreview settings={{...settings,headline:{...settings.headline,text:focusedClip ? headlines[clipKey(focusedClip)]?.trim() || focusedClip.headline || focusedClip.title : ""}}} image={focusedClip ? api.sourcePreviewUrl(run.project_id,run.source_asset_id,focusedClip.start_second+1) : undefined}/></aside></div>{(busy || currentJob) && <JobProgress job={currentJob} history={jobHistory} progress={visualBusy ? visualProgress : renderProgress} label={busy ? visualBusy ? visualMessage : currentJob?.message || "Preparando exportação" : "Último processamento"} busy={busy || visualBusy}/>}<div className="clip-preview-actions"><div className="export-actionbar"><button className="btn primary simple-primary" disabled={busy || visualBusy || !activeClips.length} onClick={() => void renderSelected()}><Icon name="play"/> {busy ? "Renderizando..." : `Renderizar ${activeClips.length} corte${activeClips.length === 1 ? "" : "s"}`}</button><button className="btn secondary" disabled={busy || visualBusy || !focusedClip} onClick={()=>void renderSelected(true)}>Gerar prévia curta (~10 s)</button></div><p>Duração aproximada para respeitar o fechamento da fala. Usa legenda corrigida, enquadramento e áudio da exportação. Mudanças nos ajustes exigem nova prévia.</p>{previewVideo && <div><h3>Prévia · {previewVideo.title}</h3><video controls src={previewVideo.url}/></div>}</div>{rendered.length > 0 && <div className="rendered-grid">{rendered.map((item) => <article key={item.url}><video controls src={item.url}/><h3>{item.title}</h3><div><a className="btn secondary" href={item.url} download>Baixar MP4</a><a href={item.subtitles} download>Baixar SRT</a></div></article>)}</div>}</section>}
      {screen === "export" && warnings.length > 0 && <aside className="simple-warnings" role="status" aria-label="Avisos de câmera"><b>Avisos de câmera</b>{warnings.map((warning, index) => <p key={index}>{warning}</p>)}</aside>}
      {draftError && <p role="alert">{draftError}</p>}
      {error && <div className="simple-error" role="alert"><b>Não deu certo ainda</b><p>{error}</p><button onClick={() => setError(null)}>Fechar</button></div>}
    </main>
  </div>;
}
