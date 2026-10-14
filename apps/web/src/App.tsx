import { JobProgress, type JobActivity } from "./JobProgress";
import { EpisodeLibrary } from "./EpisodeLibrary";
import type { CaptionCorrection } from "./api";
import { restoreEditorDraft, type EditorDraft, type VisualArtifacts } from "./editorDraft";
import { ExportEditor } from "./editor/ExportEditor";
import { Field, Range, Toggle } from "./editor/controls";
import { clipDuration, clipKey, hookOf } from "./editor/clips";
import { PublicationPanel, usePublications } from "./PublicationPanel";
import { parseHotwords } from "./editor/hotwords";
import { defaultRenderSettings, LEGACY_FRAMING_NOTICE, normalizeSettings } from "./editor/settings";
import { useEffect, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import { api, type ApiJob, type ExportBatch, type RenderSettings, type SuggestionSelection, type WorkflowRun } from "./api";

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

// o último texto usado fica salvo no navegador.
const INSTRUCTIONS_KEY = "cortex-editorial-instructions";
const DEFAULT_INSTRUCTIONS = [
  "Público: seguidores do @dayon.news, interessados em espiritualidade, consciência, ETs e ufologia, mistérios antigos, saúde e crítica ao sistema.",
  "Priorize, nesta ordem: 1) afirmações fortes ou revelações que desafiam o senso comum; 2) histórias pessoais com emoção ou virada; 3) explicações de um conceito complexo em poucas frases; 4) frases de efeito que dá vontade de compartilhar.",
  "Evite: apresentações e bastidores do programa, piadas internas, trechos que dependem de algo dito antes, e momentos em que o entrevistador fala mais que eu.",
  "Entre dois trechos parecidos, escolha o de gancho mais forte e o de final mais marcante.",
].join("\n");

function downloadName(title: string) {
  const safe = title.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-");
  return `${safe || "corte"}.mp4`;
}

/** Baixa os MP4 em sequência; o navegador pode pedir uma vez para permitir vários downloads. */
async function downloadAll(items: Array<{title: string; url: string}>) {
  for (const item of items) {
    const link = document.createElement("a");
    link.href = item.url; link.download = downloadName(item.title);
    document.body.appendChild(link); link.click(); link.remove();
    await new Promise(resolve => setTimeout(resolve, 600));
  }
}

function timestamp(seconds: number) { const value = Math.max(0, Math.round(seconds)); const h = Math.floor(value / 3600); const m = Math.floor((value % 3600) / 60); const s = value % 60; return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`; }

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

type RecentEpisode = {projectId: string; runId: string; label: string};
function recentEpisodes(): RecentEpisode[] {
  try { const value = JSON.parse(localStorage.getItem("cortex-recent-episodes") || "[]");
    return Array.isArray(value) ? value.filter(v => typeof v?.projectId === "string" && typeof v?.runId === "string" && typeof v?.label === "string").slice(0,10) : [];
  } catch { return []; }
}

export default function App() {
  const [captionEdits, setCaptionEdits] = useState<Record<string,CaptionCorrection[]>>({});
  const [headlines, setHeadlines] = useState<Record<string, string>>({});
  const [emphasisEdits, setEmphasisEdits] = useState<Record<string, number[]>>({});
  const [hotwordsText, setHotwordsText] = useState("");
  const [notice, setNotice] = useState("");
  const [encoderError, setEncoderError] = useState("");
  const serverEncoder = useRef<RenderSettings["encoder"] | null>(null);
  const [draftRunKey, setDraftRunKey] = useState<string | null>(null);
  const [draftError, setDraftError] = useState("");
  const [focusedKey, setFocusedKey] = useState("");
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
  const [instructions, setInstructionsState] = useState(() => {
    try { return localStorage.getItem(INSTRUCTIONS_KEY) || DEFAULT_INSTRUCTIONS; } catch { return DEFAULT_INSTRUCTIONS; }
  });
  const setInstructions = (value: string) => {
    setInstructionsState(value);
    try { localStorage.setItem(INSTRUCTIONS_KEY, value); } catch { /* storage indisponível: só perde a lembrança */ }
  };
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
  const [rendered, setRendered] = useState<EditorDraft["rendered"]>([]);
  const [visualArtifacts, setVisualArtifacts] = useState<VisualArtifacts | null>(null);
  const [, setRevisionId] = useState("");
  const [activeBatch, setActiveBatch] = useState<ExportBatch | null>(null);
  const publications = usePublications();

  useEffect(() => {
    const controller = new AbortController();
    void api.renderDefaults(controller.signal).then(defaults => {
      if (controller.signal.aborted) return;
      serverEncoder.current = defaults.encoder;
      setSettings(value => value.encoder === defaults.encoder ? value : { ...value, encoder: defaults.encoder });
    }).catch(reason => {
      if (!controller.signal.aborted) setEncoderError(`Não foi possível ler o codificador do servidor: ${errorMessage(reason)}`);
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (busy || !run || !activeBatch || !["queued","running"].includes(activeBatch.status)) return;
    const timer = window.setTimeout(() => {
      void api.exportBatch(run.project_id,activeBatch.id).then(batch=>{
        setActiveBatch(batch);
        const finished = batch.items.filter(item=>["succeeded","failed","cancelled"].includes(item.status)).length;
        setRenderProgress((finished/batch.items.length)*100);
      }).catch(reason=>setWarnings(items=>[...items,`Não foi possível atualizar o lote: ${errorMessage(reason)}`]));
    },1200);
    return () => window.clearTimeout(timer);
  },[busy,run,activeBatch]);

  useEffect(() => {
    if (!run || !selection || draftRunKey !== `${run.project_id}:${run.id}`) return;
    const draft: EditorDraft = {schema_version: 1, settings, selectedKeys, captionEdits, headlines, emphasisEdits, focusedKey, rendered, visualArtifacts};
    try {
      localStorage.setItem(`cortex-draft:${draftRunKey}`, JSON.stringify(draft));
      setDraftError("");
    } catch { setDraftError("Não foi possível salvar os ajustes neste navegador. Libere espaço antes de fechar a página."); }
  }, [run, selection, draftRunKey, settings, selectedKeys, captionEdits, headlines, emphasisEdits, focusedKey, rendered, visualArtifacts]);

  useEffect(() => {
    if (!run || !selection || draftRunKey !== `${run.project_id}:${run.id}`) return;
    if (!selectedKeys.length || !run.artifacts.suggestion) return;
    const timer = window.setTimeout(() => {
      const selected = selection.clips.filter(clip => selectedKeys.includes(clipKey(clip)))
        .map(clip => ({...clip,key:clipKey(clip)}));
      void api.saveRevision(run.project_id,run.id,{
        selection_artifact_id:run.artifacts.suggestion,selected_clips:selected,
        render_settings:settings,caption_edits:captionEdits,headlines,emphasis:emphasisEdits,
        visual_artifacts:visualArtifacts || {},
      }).then(saved => { if (saved) { setRevisionId(saved.id); setDraftError(""); } })
        .catch(reason => setDraftError(`Ajustes salvos neste navegador, mas não no servidor: ${errorMessage(reason)}`));
    },500);
    return () => window.clearTimeout(timer);
  }, [run, selection, draftRunKey, settings, selectedKeys, captionEdits, headlines, emphasisEdits, visualArtifacts]);

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
    setRun(null); setSelection(null); setSelectedKeys([]); setCaptionEdits({}); setHeadlines({}); setEmphasisEdits({}); setNotice("");
    setFocusedKey(""); setRendered([]);
    setError(null); setWarnings([]); setUploadProgress(0); setRenderProgress(0);
    setSettings(normalizeSettings(defaultRenderSettings, defaultRenderSettings, serverEncoder.current).settings); setVisualArtifacts(null);
    setRevisionId(""); setActiveBatch(null);
  }

  function resetEpisode() {
    if (busy) return;
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
    if (busy) return;
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
    if (!["ready_for_review","rendering","complete"].includes(next.status)) return;
    const artifactId = next.artifacts.suggestion;
    if (!artifactId) throw new Error("Processamento terminou sem sugestões persistidas");
    const envelope = await api.suggestion(next.project_id, artifactId);
    signal.throwIfAborted();
    const result = envelope.document.selection;
    const runKey = `${next.project_id}:${next.id}`;
    const draft = restoreEditorDraft(localStorage, runKey, result.clips.map(clipKey), defaultRenderSettings, serverEncoder.current);
    setSelection({ ...result, provenance: envelope.document.provenance });
    setSettings(draft.settings); setSelectedKeys(draft.selectedKeys); setCaptionEdits(draft.captionEdits);
    setHeadlines(draft.headlines); setEmphasisEdits(draft.emphasisEdits); setFocusedKey(draft.focusedKey); setRendered(draft.rendered);
    let legacyFraming = draft.legacyFraming;

    let serverVisual = draft.visualArtifacts;
    try {
      const saved = await api.latestRevision(next.project_id,next.id);
      signal.throwIfAborted();
      const validKeys = new Set(result.clips.map(clipKey));
      const savedKeys = saved.selected_clips.map(clip=>clip.key).filter(key=>validKeys.has(key));
      const restored = normalizeSettings(saved.render_settings, defaultRenderSettings, serverEncoder.current);
      legacyFraming = restored.legacyFraming;
      setRevisionId(saved.id); setSettings(restored.settings); setSelectedKeys(savedKeys);
      setCaptionEdits(saved.caption_edits); setHeadlines(saved.headlines);
      setEmphasisEdits(Object.fromEntries(Object.entries(saved.emphasis ?? {}).filter(([key]) => validKeys.has(key))));
      serverVisual = saved.visual_artifacts.scene ? {scene: saved.visual_artifacts.scene} : serverVisual;
    } catch { /* primeira revisão: mantém o draft local compatível */ }

    try {
      const batches = await api.exportBatches(next.project_id,next.id);
      signal.throwIfAborted();
      const latest = batches[0];
      if (latest) {
        setActiveBatch(latest);
        const outputs = latest.items.filter(item=>item.status==="succeeded"&&item.render_artifact_id);
        setRendered(outputs.map(item=>({title:item.title,
          url:api.renderMediaUrl(next.project_id,item.render_artifact_id!),
          subtitles:api.renderSubtitlesUrl(next.project_id,item.render_artifact_id!),
          projectId:next.project_id,artifactId:item.render_artifact_id!,
          hookText:hookOf(result.clips,item.clip_key)})));
      }
    } catch { /* lote ainda inexistente */ }

    setNotice(legacyFraming ? LEGACY_FRAMING_NOTICE : "");
    setScreen("review");
    const persistedScene = next.artifacts.scene_index;
    const visual = persistedScene ? {scene: persistedScene} : serverVisual;
    if (visual) setVisualArtifacts(visual);
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
    if (busy) return;
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
        count, minimum_seconds: minimum, maximum_seconds: maximum, instructions, primary_subject: primarySubject, new_clips_only: newClipsOnly, hotwords: parseHotwords(hotwordsText),
      });
      signal.throwIfAborted();
      window.localStorage.setItem("cortex-active-run", `${project.id}:${created.id}`);
      await followRun(created, signal);
    } catch (reason) {
      if (!signal.aborted) setError(errorMessage(reason));
    } finally { if (!signal.aborted) setBusy(false); }
  }

  async function renderSelected() {
    if (!run || !selection || busy || !episode.current) return;
    const signal = episode.current.signal;
    const clips = selection.clips.filter((clip) => selectedKeys.includes(clipKey(clip)));
    if (!clips.length) { setError("Selecione pelo menos um corte"); return; }
    setBusy(true); setJobHistory([]); setCurrentJob(null); setError(null); setRendered([]); setRenderProgress(0);
    try {
      const selected = clips.map(clip => ({...clip,key:clipKey(clip)}));
      const revision = await api.saveRevision(run.project_id,run.id,{
        selection_artifact_id:run.artifacts.suggestion,selected_clips:selected,
        render_settings:settings,caption_edits:captionEdits,headlines,emphasis:emphasisEdits,
        visual_artifacts:visualArtifacts || {},
      });
      if (!revision) throw new Error("Não foi possível salvar a revisão antes de exportar");
      setRevisionId(revision.id);
      let batch = await api.createExportBatch(
        run.project_id,run.id,revision.id,`${run.id}:${crypto.randomUUID()}`,
      );
      setActiveBatch(batch);
      while (["queued","running"].includes(batch.status)) {
        const finished = batch.items.filter(item => ["succeeded","failed","cancelled"].includes(item.status)).length;
        setRenderProgress((finished / batch.items.length) * 100);
        await waitForPoll(signal);
        batch = await api.exportBatch(run.project_id,batch.id,signal);
        setActiveBatch(batch);
      }
      const completed = batch.items.filter(item => item.status === "succeeded" && item.render_artifact_id);
      setRendered(completed.map(item => ({
        title:item.title,url:api.renderMediaUrl(run.project_id,item.render_artifact_id!),
        subtitles:api.renderSubtitlesUrl(run.project_id,item.render_artifact_id!),
        projectId:run.project_id,artifactId:item.render_artifact_id!,
        hookText:hookOf(selection.clips,item.clip_key),
      })));
      const failures = batch.items.filter(item => item.status === "failed");
      if (failures.length) setWarnings(items => [...items,...failures.map(item => `“${item.title}”: ${item.error || "falha na exportação"}`)]);
      if (!completed.length) throw new Error(batch.error || "Nenhum corte foi exportado");
      setRenderProgress(100);
    } catch (reason) {
      if (!signal.aborted) setError(errorMessage(reason));
    } finally { if (!signal.aborted) setBusy(false); }
  }

  const activeClips = selection?.clips.filter((clip) => selectedKeys.includes(clipKey(clip))) ?? [];
  const allSelected = !!selection?.clips.length && selection.clips.every((clip) => selectedKeys.includes(clipKey(clip)));
  const focusedClip = activeClips.find(c => clipKey(c) === focusedKey) || activeClips[0];
  return <div className="simple-app">
    <header className="simple-header"><div className="brand"><span className="brand-mark">CX<i/></span><div><b>Corte<span>X</span></b><small>PODCAST CLIPPER</small></div></div><nav aria-label="Jornada do episódio">{(["new", "review", "export"] as SimpleScreen[]).map((item, index) => <button key={item} className={screen === item ? "active" : ""} disabled={(item === "new" && (busy)) || ((item === "review" || item === "export") && !selection)} onClick={() => item === "new" ? resetEpisode() : setScreen(item)}><span>{index + 1}</span>{item === "new" ? "Novo episódio" : item === "review" ? "Escolher cortes" : "Exportar"}</button>)}</nav><button className={`library-nav ${screen === "library" ? "active" : ""}`} onClick={() => setScreen("library")}>Biblioteca</button><span className={`simple-status ${busy ? "working" : ""}`}>{busy ? "PROCESSANDO" : "LOCAL"}</span></header>
    <main className="simple-main">

      {(screen === "new" || screen === "library") && !run && recent.length > 0 && <div className="episode-history"><b>Retomar episódio</b>{recent.map(item => <button key={item.runId} className="btn secondary" disabled={busy} onClick={() => void resumeEpisode(item)}>{item.label}</button>)}</div>}

      {screen === "library" && <EpisodeLibrary busy={busy} onOpen={saved => void resumeEpisode({projectId:saved.project_id, runId:saved.id, label:saved.message})} onUse={entry => {
        resetEpisode(); setYoutube(entry.url || ""); setPrimarySubject(entry.primary_subject); setNewClipsOnly(true); setScreen("new");
        if (!entry.url && entry.source) { void (async () => {
          const signal = beginEpisode(); setBusy(true);
          try { const created = await api.createWorkflowRun(entry.source!.project_id, entry.source!.id, {count, minimum_seconds:minimum, maximum_seconds:maximum, instructions, primary_subject:entry.primary_subject, new_clips_only:true, hotwords:parseHotwords(hotwordsText)});
            localStorage.setItem("cortex-active-run", `${created.project_id}:${created.id}`); await followRun(created,signal);
          } catch(reason) { if(!signal.aborted) setError(errorMessage(reason)); } finally { if(!signal.aborted) setBusy(false); }
        })(); }
      }}/>}
      {screen === "new" && <section className="simple-page enter"><div className="simple-title"><span>01 · NOVO EPISÓDIO</span><h1>Do episódio aos cortes, em um clique.</h1><p>Envie o vídeo, escolha a duração e deixe o CorteX transcrever, analisar e encontrar os melhores momentos.</p></div><div className="simple-grid"><div className="simple-card source-card"><div className="source-tabs"><button className={sourceMode === "file" ? "active" : ""} onClick={() => setSourceMode("file")}><Icon name="upload"/> Arquivo</button><button className={sourceMode === "youtube" ? "active" : ""} onClick={() => setSourceMode("youtube")}><Icon name="link"/> YouTube</button></div>{sourceMode === "file" ? <label className={`dropzone ${file ? "has-file" : ""}`}><input type="file" accept="video/*,audio/*" onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] || null)}/><Icon name={file ? "check" : "upload"} size={30}/><strong>{file?.name || "Solte o episódio aqui"}</strong><small>MP4, MOV, MKV, WebM, MP3 ou WAV</small></label> : <div className="simple-url"><Icon name="link"/><input value={youtube} onChange={(event) => setYoutube(event.target.value)} placeholder="https://youtube.com/watch?v=..."/></div>}</div><div className="simple-card settings-card"><h3>O que você quer receber</h3><Range label="Quantidade" value={count} min={1} max={25} onChange={setCount}/><Range label="Mínimo" value={minimum} min={15} max={180} suffix="s" onChange={(value) => { setMinimum(value); if (value > maximum) setMaximum(value); }}/><Range label="Máximo" value={maximum} min={15} max={180} suffix="s" onChange={(value) => { setMaximum(value); if (value < minimum) setMinimum(value); }}/><Field label="Protagonista dos cortes" hint="Orienta a IA; não reconhece a voz pelo nome"><input value={primarySubject} onChange={event => setPrimarySubject(event.target.value)}/></Field><Field label="Nomes e termos do episódio" hint="Opcional · separe por vírgula"><input value={hotwordsText} onChange={event => setHotwordsText(event.target.value)} placeholder="Ex.: nome do convidado, termos técnicos"/></Field><p className="field-help">Ajuda a transcrição a escrever certo nomes próprios e termos difíceis.</p><p className="field-help">Para confirmar sua voz, use “Identificar a voz do Dayon” no episódio salvo na Biblioteca.</p><Toggle checked={newClipsOnly} onChange={setNewClipsOnly} label="Evitar trechos já sugeridos"/><Field label="Direção editorial" hint="Este pedido é enviado à IA para escolher os trechos"><textarea placeholder="Ex.: priorize minhas explicações sobre consciência; inclua a pergunta quando ajudar a entender; preserve o contexto e evite frases cortadas." value={instructions} onChange={(event) => setInstructions(event.target.value)}/></Field></div></div>{run && busy && <div className="simple-progress"><div><b>{run.message}</b><span>{Math.round(run.progress)}%</span></div><i><em style={{ width: `${run.progress}%` }}/></i><small>{uploadProgress > 0 && uploadProgress < 1 ? `Enviando · ${Math.round(uploadProgress * 100)}%` : "O processamento continua mesmo se você fechar esta tela."}</small></div>}{busy && currentJob && <><JobProgress job={currentJob} history={jobHistory} progress={currentJob.progress ?? uploadProgress * 100} completed={false} label={currentJob.message || "Preparando episódio"} busy/><button className="btn secondary" onClick={() => void api.cancelJob(currentJob.id).then(trackJob).catch(reason => setError(errorMessage(reason)))}>Cancelar tarefa</button></>}<button className="btn primary simple-primary" disabled={busy || (sourceMode === "file" ? !file : !youtube)} onClick={() => void processEpisode()}><Icon name="spark"/> {busy ? "Processando episódio..." : "Processar episódio"}</button></section>}
      {screen === "review" && selection && run && <section className="simple-page enter"><div className="simple-title row"><div><span>02 · ESCOLHER CORTES</span><h1>{selection.clips.length} momentos encontrados.</h1><p>{selection.selection_notes}</p></div><div className="review-actions"><button className="btn secondary" onClick={() => setSelectedKeys(allSelected ? [] : selection.clips.map(clipKey))}>{allSelected ? "Limpar seleção" : "Selecionar todos"}</button><button className="btn primary" disabled={!activeClips.length} onClick={() => setScreen("export")}>Editar {activeClips.length} selecionado{activeClips.length === 1 ? "" : "s"} <Icon name="chevron"/></button></div></div><div className="clip-grid">{selection.clips.map((clip) => { const key = clipKey(clip); const selected = selectedKeys.includes(key); return <article key={key} className={`simple-clip ${selected ? "selected" : ""}`} onClick={() => setSelectedKeys((items) => selected ? items.filter((item) => item !== key) : [...items, key])}><div className="clip-frame"><img src={api.sourcePreviewUrl(run.project_id, run.source_asset_id, clip.start_second + 1)} alt="Preview do corte"/><span>{timestamp(clip.estimated_duration)}</span><i>{selected ? <Icon name="check"/> : null}</i></div><div><span className="clip-rank">#{clip.rank} · {clip.primary_speaker} · {clipDuration(clip.estimated_duration)}{clip.pieces && clip.pieces.length > 1 ? <b className="clip-pieces-badge">montado de {clip.pieces.length} trechos</b> : null}</span><h3>{clip.headline || clip.title}</h3>{clip.headline && clip.headline !== clip.title ? <small className="clip-muted">{clip.title}</small> : null}<p>{clip.reasoning}</p>{clip.lead_trimmed ? <small className="clip-note">Começo limpo: “{clip.lead_trimmed}” removido</small> : null}{clip.headline_options && clip.headline_options.length > 1 ? <small className="clip-muted">Outras manchetes: {clip.headline_options.slice(1).join(" · ")}</small> : null}<strong>{timestamp(clip.start_second)} — {timestamp(clip.end_second)}</strong>{clip.pieces && clip.pieces.length > 0 ? <details className="clip-text" onClick={(event) => event.stopPropagation()}><summary>Ver texto do corte</summary>{clip.pieces.map((piece) => <p key={piece.from_id}><small>{timestamp(piece.start)} — {timestamp(piece.end)}</small> {piece.text}</p>)}</details> : null}</div></article>; })}</div></section>}
      {screen === "export" && selection && run && <section className="simple-page enter export-page"><div className="simple-title"><span>03 · EXPORTAR</span><h1>Seu corte, do seu jeito.</h1><p>{activeClips.length} cortes selecionados · 1080 × 1920. A prévia é o vídeo real da edição com a legenda exata da exportação. Depois que o lote for enviado, o processamento continua no servidor mesmo se você fechar esta tela.</p></div>
        {notice && <p className="simple-notice" role="status">{notice}</p>}
        <ExportEditor run={run} clips={activeClips} focusedKey={focusedKey} onFocus={setFocusedKey} settings={settings} update={setSettings}
          captionEdits={captionEdits} onCaptionEdits={(key, edits) => setCaptionEdits(current => ({...current, [key]: edits}))} onAllCaptionEdits={setCaptionEdits}
          headlines={headlines} onHeadline={(key, text) => setHeadlines(current => { const next = {...current}; if (text) next[key] = text; else delete next[key]; return next; })}
          emphasisEdits={emphasisEdits} onEmphasis={(key, indices) => setEmphasisEdits(current => ({...current, [key]: indices}))}
          sceneIndexArtifactId={visualArtifacts?.scene} disabled={busy}
          itemStatus={Object.fromEntries((activeBatch?.items ?? []).map(item => [item.clip_key, item.status]))}/>
        {activeBatch && <div className="field-help"><p>Lote no servidor: {activeBatch.items.filter(item=>item.status==="succeeded").length}/{activeBatch.items.length} concluídos · {activeBatch.status}</p>{["queued","running"].includes(activeBatch.status)&&<button className="btn secondary" disabled={busy} onClick={()=>void api.cancelExportBatch(activeBatch.project_id,activeBatch.id).then(setActiveBatch).catch(reason=>setError(errorMessage(reason)))}>Cancelar lote</button>}{activeBatch.items.some(item=>item.status==="failed")&&<button className="btn secondary" disabled={busy} onClick={()=>void api.retryExportBatch(activeBatch.project_id,activeBatch.id).then(setActiveBatch).catch(reason=>setError(errorMessage(reason)))}>Repetir falhas</button>}</div>}
        {(busy || currentJob) && <JobProgress job={currentJob} history={jobHistory} progress={renderProgress} completed={renderProgress === 100} label={busy ? currentJob?.message || "Preparando exportação" : renderProgress === 100 ? "Exportação concluída" : "Exportação interrompida"} busy={busy}/>}
        <div className="clip-preview-actions"><div className="export-actionbar"><button className="btn primary simple-primary" disabled={busy || !activeClips.length} onClick={() => void renderSelected()}><Icon name="play"/> {busy ? "Renderizando..." : `Renderizar ${activeClips.length} corte${activeClips.length === 1 ? "" : "s"}`}</button></div></div>
        {rendered.length > 1 && <div className="export-actionbar"><button className="btn primary" onClick={() => void downloadAll(rendered)}><Icon name="check"/> Baixar todos ({rendered.length} MP4)</button></div>}
        {rendered.length > 0 && <div className="rendered-grid">{rendered.map((item) => <article key={item.url}><video controls src={item.url}/><h3>{item.title}</h3><div><a className="btn secondary" href={item.url} download={downloadName(item.title)}>Baixar MP4</a><a href={item.subtitles} download>Baixar SRT</a></div>{item.artifactId && item.projectId && <PublicationPanel projectId={item.projectId} artifactId={item.artifactId} publication={publications.items[item.artifactId]} hookText={item.hookText} onSaved={publications.save} onDeleted={publications.remove}/>}</article>)}</div>}</section>}
      {screen === "export" && warnings.length > 0 && <aside className="simple-warnings" role="status" aria-label="Avisos da exportação"><b>Avisos da exportação</b>{warnings.map((warning, index) => <p key={index}>{warning}</p>)}</aside>}
      {draftError && <p role="alert">{draftError}</p>}
      {encoderError && <p role="alert">{encoderError}</p>}
      {error && <div className="simple-error" role="alert"><b>Não deu certo ainda</b><p>{error}</p><button onClick={() => setError(null)}>Fechar</button></div>}
    </main>
  </div>;
}
