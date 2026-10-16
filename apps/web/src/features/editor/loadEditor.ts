import { api, type CaptionCorrection, type ClipRow, type ExportBatch, type RenderSettings, type SettingsPatch, type WorkflowRun } from "../../api";
import { errorText, isRunReady } from "../../app/humanize";
import { restoreEditorDraft, type VisualArtifacts } from "../../editorDraft";
import { defaultRenderSettings, LEGACY_FRAMING_NOTICE, normalizeSettings } from "../../editor/settings";
import { KEY_SEPARATOR } from "../../router";
import { rowId } from "../clips/clipModel";
import type { Overrides } from "./overrides";

export type Loaded = {
  rows: ClipRow[]; runs: Record<string, WorkflowRun>; settings: RenderSettings; overrides: Overrides;
  captionEdits: Record<string, CaptionCorrection[]>; headlines: Record<string, string>; emphasisEdits: Record<string, number[]>;
  visual: Record<string, VisualArtifacts | null>; batches: Record<string, ExportBatch>; focusedKey: string;
  notice: string; encoderError: string;
};

export const draftKey = (run: Pick<WorkflowRun, "project_id" | "id">) => `cortex-draft:${run.project_id}:${run.id}`;
export const qualify = (runId: string, key: string) => `${runId}${KEY_SEPARATOR}${key}`;

/** The entries of a qualified map that belong to `runId`, under their plain clip keys. */
export function scoped<T>(map: Record<string, T>, runId: string): Record<string, T> {
  const prefix = `${runId}${KEY_SEPARATOR}`;
  return Object.fromEntries(Object.entries(map).filter(([key]) => key.startsWith(prefix)).map(([key, value]) => [key.slice(prefix.length), value]));
}

const qualifiedEntries = <T>(runId: string, map: Record<string, T>) => Object.entries(map).map(([key, value]) => [qualify(runId, key), value] as const);

/** Which clips the URL asks for, in URL order; a legacy `run` alone (or unmatched keys) means every clip of that run. */
export function selectRows(all: ClipRow[], urlKeys: string[], legacyRun: string) {
  const byId = new Map(all.map(row => [rowId(row), row]));
  const chosen = urlKeys.flatMap(key => byId.get(key) ?? []);
  if (chosen.length) return chosen;
  return legacyRun ? all.filter(row => row.run_id === legacyRun) : [];
}

export async function loadEditor(episodeId: string, urlKeys: string[], legacyRun: string, signal: AbortSignal): Promise<Loaded> {
  let encoder: RenderSettings["encoder"] | null = null;
  let encoderError = "";
  try {
    encoder = (await api.renderDefaults(signal)).encoder;
    signal.throwIfAborted();
  } catch (reason) {
    if (signal.aborted) throw reason;
    encoderError = `Não foi possível ler o codificador do servidor: ${errorText(reason)}`;
  }
  if (!urlKeys.length && !legacyRun) throw new Error("Nenhuma seleção foi indicada para editar.");
  const clips = await api.episodeClips(episodeId, signal);
  signal.throwIfAborted();
  const rows = selectRows(clips?.clips ?? [], urlKeys, legacyRun);
  if (!rows.length) throw new Error("Não encontramos essa seleção de cortes no episódio.");

  const runIds = [...new Set(rows.map(row => row.run_id))];
  const runs: Record<string, WorkflowRun> = {};
  for (const runId of runIds) {
    const row = rows.find(item => item.run_id === runId)!;
    const run = await api.workflowRun(row.project_id, runId, signal);
    signal.throwIfAborted();
    if (!isRunReady(run) || !run.artifacts.suggestion) throw new Error("Esta seleção ainda não está pronta para editar.");
    runs[runId] = run;
  }

  const out: Loaded = {
    rows, runs, settings: defaultRenderSettings, overrides: {}, captionEdits: {}, headlines: {}, emphasisEdits: {},
    visual: {}, batches: {}, focusedKey: "", notice: "", encoderError,
  };
  let legacyFraming = false;
  for (const [index, runId] of runIds.entries()) {
    const run = runs[runId];
    const allKeys = (clips?.clips ?? []).filter(row => row.run_id === runId).map(row => row.clip_key);
    const draft = restoreEditorDraft(localStorage, `${run.project_id}:${run.id}`, allKeys, defaultRenderSettings, encoder);
    let { settings } = draft;
    let { captionEdits, headlines, emphasisEdits, clipSettings } = draft;
    let { legacyFraming: legacy } = draft;
    let serverVisual = draft.visualArtifacts;
    try {
      const saved = await api.latestRevision(run.project_id, run.id);
      signal.throwIfAborted();
      const restored = normalizeSettings(saved.render_settings, defaultRenderSettings, encoder);
      legacy = restored.legacyFraming;
      settings = restored.settings;
      captionEdits = saved.caption_edits;
      headlines = saved.headlines;
      emphasisEdits = Object.fromEntries(Object.entries(saved.emphasis ?? {}).filter(([key]) => allKeys.includes(key)));
      clipSettings = Object.fromEntries(Object.entries(saved.clip_settings ?? {}).filter(([key]) => allKeys.includes(key))) as Record<string, SettingsPatch>;
      serverVisual = saved.visual_artifacts.scene ? { scene: saved.visual_artifacts.scene } : serverVisual;
    } catch { if (signal.aborted) throw signal.reason; /* primeira revisão: mantém o rascunho local compatível */ }
    try {
      const latest = (await api.exportBatches(run.project_id, run.id))[0];
      signal.throwIfAborted();
      if (latest) out.batches[runId] = latest;
    } catch { if (signal.aborted) throw signal.reason; /* lote ainda inexistente */ }
    if (index === 0) { out.settings = settings; out.focusedKey = draft.focusedKey ? qualify(runId, draft.focusedKey) : ""; legacyFraming = legacy; }
    Object.assign(out.captionEdits, Object.fromEntries(qualifiedEntries(runId, captionEdits)));
    Object.assign(out.headlines, Object.fromEntries(qualifiedEntries(runId, headlines)));
    Object.assign(out.emphasisEdits, Object.fromEntries(qualifiedEntries(runId, emphasisEdits)));
    Object.assign(out.overrides, Object.fromEntries(qualifiedEntries(runId, clipSettings)));
    out.visual[runId] = run.artifacts.scene_index ? { scene: run.artifacts.scene_index } : serverVisual;
  }
  out.notice = legacyFraming ? LEGACY_FRAMING_NOTICE : "";
  if (!out.focusedKey || !rows.some(row => rowId(row) === out.focusedKey)) out.focusedKey = rowId(rows[0]);
  return out;
}
