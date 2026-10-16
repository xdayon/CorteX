import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type CaptionCorrection, type ClipRow, type ExportBatch, type RenderSettings, type SettingsPatch, type WorkflowRun } from "../../api";
import { errorText } from "../../app/humanize";
import { type EditorDraft, type VisualArtifacts } from "../../editorDraft";
import { hookOf } from "../../editor/clips";
import { draftKey, loadEditor, scoped } from "./loadEditor";
import { applySettingsEdit, effectiveSettings, type Overrides } from "./overrides";
import { useExportBatches } from "./useExportBatches";

type Phase = { name: "loading" } | { name: "ready" } | { name: "error"; message: string };
export type Scope = "clip" | "all";
export type SaveState = "saving" | "saved" | "error";
const NO_HEADLINES: Record<string, string> = {};

/** Loads the clips of an episode selection (one or several runs) and owns the editor state, autosave and exports. */
export function useEditorSession(episodeId: string, urlKeys: string[], legacyRun: string) {
  const [phase, setPhase] = useState<Phase>({ name: "loading" });
  const [rows, setRows] = useState<ClipRow[]>([]);
  const [runs, setRuns] = useState<Record<string, WorkflowRun>>({});
  const [config, setConfig] = useState<{ settings: RenderSettings; overrides: Overrides } | null>(null);
  const [captionEdits, setCaptionEdits] = useState<Record<string, CaptionCorrection[]>>({});
  const [headlines, setHeadlines] = useState<Record<string, string>>(NO_HEADLINES);
  const [emphasisEdits, setEmphasisEdits] = useState<Record<string, number[]>>({});
  const [visual, setVisual] = useState<Record<string, VisualArtifacts | null>>({});
  const [focusedKey, setFocusedKey] = useState("");
  const [scope, setScope] = useState<Scope>("all");
  const [notice, setNotice] = useState("");
  const [encoderError, setEncoderError] = useState("");
  const [draftError, setDraftError] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const lifetime = useRef<AbortController | null>(null);
  const firstSave = useRef(true);
  const [initialBatches, setInitialBatches] = useState<Record<string, ExportBatch>>({});

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    setPhase({ name: "loading" }); setRows([]); setConfig(null); setDraftError(""); setEncoderError("");
    loadEditor(episodeId, urlKeys, legacyRun, controller.signal).then(loaded => {
      if (controller.signal.aborted) return;
      setRows(loaded.rows); setRuns(loaded.runs); setConfig({ settings: loaded.settings, overrides: loaded.overrides });
      setCaptionEdits(loaded.captionEdits); setHeadlines(loaded.headlines); setEmphasisEdits(loaded.emphasisEdits);
      setVisual(loaded.visual); setFocusedKey(loaded.focusedKey); setNotice(loaded.notice); setEncoderError(loaded.encoderError);
      setInitialBatches(loaded.batches);
      setPhase({ name: "ready" });
    }).catch(reason => { if (!controller.signal.aborted) setPhase({ name: "error", message: errorText(reason) }); });
    return () => controller.abort();
  // As chaves da URL valem só na abertura: a sessão não recarrega quando a URL muda.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episodeId]);

  const ready = phase.name === "ready" && !!config;
  const settings = config?.settings as RenderSettings;
  const overrides = config?.overrides ?? ({} as Overrides);
  const runIds = useMemo(() => [...new Set(rows.map(row => row.run_id))], [rows]);

  const settingsFor = useCallback((key: string) => effectiveSettings(settings, overrides[key]), [settings, overrides]);
  const sceneFor = useCallback((runId: string) => visual[runId]?.scene ?? null, [visual]);

  const edit = useCallback((change: (settings: RenderSettings) => RenderSettings, forced?: Scope) => {
    setConfig(current => {
      if (!current) return current;
      const next = applySettingsEdit(current.settings, current.overrides, focusedKey, forced ?? scope, change);
      return { settings: next.shared, overrides: next.overrides };
    });
  }, [focusedKey, scope]);
  const resetOverride = useCallback((key: string) => setConfig(current => {
    if (!current || !current.overrides[key]) return current;
    const { [key]: _removed, ...rest } = current.overrides;
    return { ...current, overrides: rest };
  }), []);

  const revisionBody = useCallback((runId: string) => {
    const mine = rows.filter(row => row.run_id === runId);
    const keys = new Set(mine.map(row => row.clip_key));
    const pick = <T,>(map: Record<string, T>) => scoped(map, runId);
    const clipSettings = Object.fromEntries(Object.entries(pick<SettingsPatch>(overrides)).filter(([key, patch]) => keys.has(key) && Object.keys(patch).length));
    return {
      selection_artifact_id: runs[runId].artifacts.suggestion, selected_clips: mine.map(row => ({ ...row.clip, key: row.clip_key })),
      render_settings: settings, caption_edits: pick(captionEdits),
      headlines: Object.fromEntries(Object.entries(pick(headlines)).filter(([, text]) => text.trim())),
      emphasis: pick(emphasisEdits), clip_settings: clipSettings, visual_artifacts: visual[runId] || {},
    };
  }, [rows, runs, settings, overrides, captionEdits, headlines, emphasisEdits, visual]);

  const exporter = useExportBatches({ runs, runIds, ready, initial: initialBatches, revisionBody, signal: () => lifetime.current?.signal });

  useEffect(() => {
    if (!ready) return;
    try {
      for (const runId of runIds) {
        const run = runs[runId];
        const keys = rows.filter(row => row.run_id === runId).map(row => row.clip_key);
        const batch = exporter.batches[runId];
        const rendered: EditorDraft["rendered"] = (batch?.items ?? []).filter(item => item.status === "succeeded" && item.render_artifact_id).map(item => ({
          title: item.title, url: api.renderMediaUrl(run.project_id, item.render_artifact_id!), subtitles: api.renderSubtitlesUrl(run.project_id, item.render_artifact_id!),
          projectId: run.project_id, artifactId: item.render_artifact_id!, hookText: hookOf(rows.filter(row => row.run_id === runId).map(row => row.clip), item.clip_key),
        }));
        const own = revisionBody(runId);
        const draft: EditorDraft = {
          schema_version: 1, settings, selectedKeys: keys, captionEdits: own.caption_edits, headlines: scoped(headlines, runId),
          emphasisEdits: own.emphasis, clipSettings: own.clip_settings, focusedKey: focusedKey.startsWith(`${runId}~`) ? focusedKey.slice(runId.length + 1) : "",
          rendered, visualArtifacts: visual[runId] ?? null,
        };
        localStorage.setItem(draftKey(run), JSON.stringify(draft));
      }
      setDraftError("");
    } catch { setDraftError("Não foi possível salvar os ajustes neste navegador. Libere espaço antes de fechar a página."); }
  }, [ready, runIds, runs, rows, settings, headlines, focusedKey, visual, exporter.batches, revisionBody]);

  useEffect(() => {
    if (!ready || !rows.length) return;
    if (!firstSave.current) setSaveState("saving");
    firstSave.current = false;
    const timer = window.setTimeout(() => {
      void Promise.all(runIds.map(runId => api.saveRevision(runs[runId].project_id, runId, revisionBody(runId))))
        .then(() => { setDraftError(""); setSaveState("saved"); })
        .catch(reason => { setSaveState("error"); setDraftError(`Ajustes salvos neste navegador, mas não no servidor: ${errorText(reason)}`); });
    }, 500);
    return () => window.clearTimeout(timer);
  }, [ready, rows, runIds, runs, revisionBody]);

  const setCaptionFor = useCallback((key: string, edits: CaptionCorrection[]) => setCaptionEdits(current => ({ ...current, [key]: edits })), []);
  const setHeadlineFor = useCallback((key: string, text: string | null) => setHeadlines(current => {
    const next = { ...current };
    if (text === null) delete next[key]; else next[key] = text;
    return next;
  }), []);
  const setEmphasisFor = useCallback((key: string, indices: number[]) => setEmphasisEdits(current => ({ ...current, [key]: indices })), []);

  return {
    phase, rows, runs, settings, overrides, settingsFor, sceneFor, edit, resetOverride, scope, setScope,
    captionEdits, setCaptionEdits, setCaptionFor, headlines, setHeadlineFor, emphasisEdits, setEmphasisFor,
    focusedKey, setFocusedKey, notice, encoderError, draftError, saveState, ready, ...exporter,
  };
}
