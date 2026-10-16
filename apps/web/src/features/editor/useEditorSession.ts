import { useCallback, useEffect, useRef, useState } from "react";
import { api, type CaptionCorrection, type ExportBatch, type RenderSettings, type SuggestionSelection, type WorkflowRun } from "../../api";
import { errorText, isRunReady } from "../../app/humanize";
import { restoreEditorDraft, type EditorDraft, type VisualArtifacts } from "../../editorDraft";
import { clipKey, hookOf } from "../../editor/clips";
import { defaultRenderSettings, LEGACY_FRAMING_NOTICE, normalizeSettings } from "../../editor/settings";

const FINISHED = ["succeeded", "failed", "cancelled"];
const isOpen = (batch: ExportBatch) => ["queued", "running"].includes(batch.status);
const finishedFraction = (batch: ExportBatch) => (batch.items.filter(item => FINISHED.includes(item.status)).length / Math.max(1, batch.items.length)) * 100;

function waitForPoll(signal: AbortSignal) {
  signal.throwIfAborted();
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 1200);
    function abort() { window.clearTimeout(timer); reject(signal.reason); }
    signal.addEventListener("abort", abort, { once: true });
  });
}

type Phase = { name: "loading" } | { name: "ready" } | { name: "error"; message: string };

/** Loads one run for editing and owns the editor state: draft, revision autosave and export batches. */
export function useEditorSession(episodeId: string, runId: string, urlKeys: string[]) {
  const [phase, setPhase] = useState<Phase>({ name: "loading" });
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [selection, setSelection] = useState<SuggestionSelection | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [settings, setSettings] = useState<RenderSettings>(defaultRenderSettings);
  const [captionEdits, setCaptionEdits] = useState<Record<string, CaptionCorrection[]>>({});
  const [headlines, setHeadlines] = useState<Record<string, string>>({});
  const [emphasisEdits, setEmphasisEdits] = useState<Record<string, number[]>>({});
  const [focusedKey, setFocusedKey] = useState("");
  const [visualArtifacts, setVisualArtifacts] = useState<VisualArtifacts | null>(null);
  const [activeBatch, setActiveBatch] = useState<ExportBatch | null>(null);
  const [renderProgress, setRenderProgress] = useState(0);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [notice, setNotice] = useState("");
  const [encoderError, setEncoderError] = useState("");
  const [draftError, setDraftError] = useState("");
  const [exportError, setExportError] = useState("");
  const [busy, setBusy] = useState(false);
  const [draftRunKey, setDraftRunKey] = useState<string | null>(null);
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    const { signal } = controller;
    setPhase({ name: "loading" }); setRun(null); setSelection(null); setDraftRunKey(null); setActiveBatch(null);
    setWarnings([]); setExportError(""); setEncoderError(""); setDraftError(""); setRenderProgress(0);

    async function load() {
      let encoder: RenderSettings["encoder"] | null = null;
      try {
        encoder = (await api.renderDefaults(signal)).encoder;
        signal.throwIfAborted();
      } catch (reason) {
        if (signal.aborted) return;
        setEncoderError(`Não foi possível ler o codificador do servidor: ${errorText(reason)}`);
      }
      if (!runId) throw new Error("Nenhuma seleção foi indicada para editar.");
      const entry = (await api.episodes(true)).find(item => item.id === episodeId);
      signal.throwIfAborted();
      const known = entry?.runs.find(item => item.id === runId);
      if (!known) throw new Error("Não encontramos essa seleção de cortes no episódio.");
      const next = await api.workflowRun(known.project_id, runId, signal);
      signal.throwIfAborted();
      if (!isRunReady(next) || !next.artifacts.suggestion) throw new Error("Esta seleção ainda não está pronta para editar.");
      const envelope = await api.suggestion(next.project_id, next.artifacts.suggestion);
      signal.throwIfAborted();
      const result = envelope.document.selection;
      const allKeys = result.clips.map(clipKey);
      const runKey = `${next.project_id}:${next.id}`;
      const draft = restoreEditorDraft(localStorage, runKey, allKeys, defaultRenderSettings, encoder);
      const chosen = urlKeys.filter(key => allKeys.includes(key));
      setRun(next);
      setSelection({ ...result, provenance: envelope.document.provenance });
      setSelectedKeys(chosen.length ? chosen : allKeys);
      setSettings(draft.settings); setCaptionEdits(draft.captionEdits); setHeadlines(draft.headlines);
      setEmphasisEdits(draft.emphasisEdits); setFocusedKey(draft.focusedKey);
      let legacyFraming = draft.legacyFraming;
      let serverVisual = draft.visualArtifacts;
      try {
        const saved = await api.latestRevision(next.project_id, next.id);
        signal.throwIfAborted();
        const valid = new Set(allKeys);
        const restored = normalizeSettings(saved.render_settings, defaultRenderSettings, encoder);
        legacyFraming = restored.legacyFraming;
        setSettings(restored.settings); setCaptionEdits(saved.caption_edits); setHeadlines(saved.headlines);
        setEmphasisEdits(Object.fromEntries(Object.entries(saved.emphasis ?? {}).filter(([key]) => valid.has(key))));
        serverVisual = saved.visual_artifacts.scene ? { scene: saved.visual_artifacts.scene } : serverVisual;
      } catch { if (signal.aborted) return; /* primeira revisão: mantém o draft local compatível */ }
      try {
        const latest = (await api.exportBatches(next.project_id, next.id))[0];
        signal.throwIfAborted();
        if (latest) { setActiveBatch(latest); setRenderProgress(finishedFraction(latest)); }
      } catch { if (signal.aborted) return; /* lote ainda inexistente */ }
      setNotice(legacyFraming ? LEGACY_FRAMING_NOTICE : "");
      const persistedScene = next.artifacts.scene_index;
      const visual = persistedScene ? { scene: persistedScene } : serverVisual;
      if (visual) setVisualArtifacts(visual);
      setDraftRunKey(runKey);
      setPhase({ name: "ready" });
    }
    load().catch(reason => { if (!signal.aborted) setPhase({ name: "error", message: errorText(reason) }); });
    return () => controller.abort();
  // As chaves da URL valem só na abertura: a sessão não recarrega quando a URL muda.
  }, [episodeId, runId]);

  const ready = phase.name === "ready" && !!run && !!selection && draftRunKey === `${run.project_id}:${run.id}`;

  useEffect(() => {
    if (!ready || !run) return;
    const rendered: EditorDraft["rendered"] = (activeBatch?.items ?? []).filter(item => item.status === "succeeded" && item.render_artifact_id).map(item => ({
      title: item.title, url: api.renderMediaUrl(run.project_id, item.render_artifact_id!), subtitles: api.renderSubtitlesUrl(run.project_id, item.render_artifact_id!),
      projectId: run.project_id, artifactId: item.render_artifact_id!, hookText: hookOf(selection!.clips, item.clip_key),
    }));
    const draft: EditorDraft = { schema_version: 1, settings, selectedKeys, captionEdits, headlines, emphasisEdits, focusedKey, rendered, visualArtifacts };
    try {
      localStorage.setItem(`cortex-draft:${draftRunKey}`, JSON.stringify(draft));
      setDraftError("");
    } catch { setDraftError("Não foi possível salvar os ajustes neste navegador. Libere espaço antes de fechar a página."); }
  }, [ready, run, selection, draftRunKey, settings, selectedKeys, captionEdits, headlines, emphasisEdits, focusedKey, visualArtifacts, activeBatch]);

  const revisionBody = useCallback(() => {
    const chosen = selection!.clips.filter(clip => selectedKeys.includes(clipKey(clip))).map(clip => ({ ...clip, key: clipKey(clip) }));
    return {
      selection_artifact_id: run!.artifacts.suggestion, selected_clips: chosen, render_settings: settings,
      caption_edits: captionEdits, headlines, emphasis: emphasisEdits, visual_artifacts: visualArtifacts || {},
    };
  }, [run, selection, selectedKeys, settings, captionEdits, headlines, emphasisEdits, visualArtifacts]);

  useEffect(() => {
    if (!ready || !run || !selectedKeys.length || !run.artifacts.suggestion) return;
    const timer = window.setTimeout(() => {
      void api.saveRevision(run.project_id, run.id, revisionBody())
        .then(() => setDraftError(""))
        .catch(reason => setDraftError(`Ajustes salvos neste navegador, mas não no servidor: ${errorText(reason)}`));
    }, 500);
    return () => window.clearTimeout(timer);
  }, [ready, run, selectedKeys, revisionBody]);

  useEffect(() => {
    if (busy || !run || !activeBatch || !isOpen(activeBatch)) return;
    const timer = window.setTimeout(() => {
      void api.exportBatch(run.project_id, activeBatch.id).then(batch => {
        setActiveBatch(batch); setRenderProgress(finishedFraction(batch));
      }).catch(reason => setWarnings(items => [...items, `Não foi possível atualizar o lote: ${errorText(reason)}`]));
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [busy, run, activeBatch]);

  /** Saves the revision and runs one durable export batch to completion. */
  const exportSelected = useCallback(async () => {
    const signal = lifetime.current?.signal;
    if (!run || !selection || busy || !signal) return;
    if (!selectedKeys.length) { setExportError("Selecione pelo menos um corte."); return; }
    setBusy(true); setExportError(""); setRenderProgress(0);
    try {
      const revision = await api.saveRevision(run.project_id, run.id, revisionBody());
      if (!revision) throw new Error("Não foi possível salvar a revisão antes de exportar.");
      let batch = await api.createExportBatch(run.project_id, run.id, revision.id, `${run.id}:${crypto.randomUUID()}`);
      setActiveBatch(batch);
      while (isOpen(batch)) {
        setRenderProgress(finishedFraction(batch));
        await waitForPoll(signal);
        batch = await api.exportBatch(run.project_id, batch.id, signal);
        setActiveBatch(batch);
      }
      const failures = batch.items.filter(item => item.status === "failed");
      if (failures.length) setWarnings(items => [...items, ...failures.map(item => `“${item.title}”: ${item.error || "falha na exportação"}`)]);
      if (!batch.items.some(item => item.status === "succeeded" && item.render_artifact_id)) throw new Error(batch.error || "Nenhum corte foi exportado.");
      setRenderProgress(100);
    } catch (reason) {
      if (!signal.aborted) setExportError(errorText(reason));
    } finally { if (!signal.aborted) setBusy(false); }
  }, [run, selection, busy, selectedKeys, revisionBody]);

  const patchBatch = useCallback((action: (projectId: string, batchId: string) => Promise<ExportBatch>) => {
    if (!activeBatch) return;
    void action(activeBatch.project_id, activeBatch.id).then(setActiveBatch).catch(reason => setExportError(errorText(reason)));
  }, [activeBatch]);

  return {
    phase, run, selection, selectedKeys, settings, setSettings, captionEdits, setCaptionEdits, headlines, setHeadlines,
    emphasisEdits, setEmphasisEdits, focusedKey, setFocusedKey, visualArtifacts, activeBatch, renderProgress, warnings,
    notice, encoderError, draftError, exportError, setExportError, busy, ready, exportSelected,
    cancelBatch: () => patchBatch(api.cancelExportBatch), retryBatch: () => patchBatch(api.retryExportBatch),
  };
}
