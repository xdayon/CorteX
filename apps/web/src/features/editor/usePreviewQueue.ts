import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ClipRow, type RefineStatus, type RenderSettings, type WorkflowRun } from "../../api";
import { rowId } from "../clips/clipModel";
import {
  cancelQuietly, EMPTY_PREVIEW, finishJob, needsWork, phaseOf, pickNext, reasonText, targetOf,
  type ClipPreview, type PreviewView,
} from "./previewQueue";

const RERENDER_DELAY_MS = 800;

type Inputs = {
  rows: ClipRow[]; runs: Record<string, WorkflowRun>; focusedKey: string; enabled: boolean;
  settingsFor: (key: string) => RenderSettings; sceneFor: (runId: string) => string | null;
};

/**
 * Prepares the draft preview of every clip of the editor, one job at a time: the focused clip first, then the
 * others in list order. A running job is never cancelled; focusing another clip only moves it to the front of
 * what is left. Framing changes refresh only the focused clip.
 */
export function usePreviewQueue(inputs: Inputs) {
  const { rows, focusedKey, enabled } = inputs;
  const keys = useMemo(() => rows.map(rowId), [rows]);
  const [states, setStates] = useState<Record<string, ClipPreview>>({});
  const statesRef = useRef(states);
  const latest = useRef(inputs);
  latest.current = inputs;
  const running = useRef(false);
  const disposed = useRef(false);
  const current = useRef<{ controller: AbortController; jobId: string | null } | null>(null);

  const patch = useCallback((key: string, change: Partial<ClipPreview>) => {
    const next = { ...statesRef.current, [key]: { ...(statesRef.current[key] ?? EMPTY_PREVIEW), ...change } };
    statesRef.current = next;
    if (!disposed.current) setStates(next);
  }, []);

  const rowOf = (key: string) => latest.current.rows.find(row => rowId(row) === key);
  const targetFor = (key: string) => { const row = rowOf(key); return row ? targetOf(row, latest.current.settingsFor(key)) : null; };

  const work = useCallback(async (key: string) => {
    const row = rowOf(key);
    const target = targetFor(key);
    const run = row && latest.current.runs[row.run_id];
    if (!row || !target || !run) return;
    const controller = new AbortController();
    const job = current.current = { controller, jobId: null as string | null };
    const { signal } = controller;
    try {
      if ((statesRef.current[key] ?? EMPTY_PREVIEW).planId === null || statesRef.current[key].planTeaser !== target.teaserKey) {
        patch(key, { running: "planning", progress: 0, error: "" });
        const teaser = latest.current.settingsFor(key).transitions.teaser && row.clip.teaser ? row.clip.teaser : null;
        const question = latest.current.settingsFor(key).transitions.question;
        const started = await api.startEditPlan(row.project_id, {
          transcriptArtifactId: run.artifacts.transcript, analysisArtifactId: run.artifacts.analysis,
          start: row.clip.start_second, end: row.clip.end_second, profile: row.clip.pacing,
          maximumSeconds: run.brief.maximum_seconds, minimumSeconds: run.brief.minimum_seconds,
          approximateEdl: row.clip.approximate_edl, confirmedVoice: run.brief.confirmed_voice, primarySubject: run.brief.primary_subject,
          headline: row.clip.headline || null, tighten: true, sceneIndexArtifactId: latest.current.sceneFor(row.run_id),
          ...(teaser ? { teaserStart: teaser.start, teaserEnd: teaser.end } : {}),
          ...(question ? { questionStart: question.start, questionEnd: question.end } : {}),
        });
        job.jobId = started.id;
        const done = await finishJob(started, signal, update => patch(key, { progress: (update.progress || 0) / 2 }));
        if (signal.aborted) return;
        job.jobId = null;
        if (done.status !== "succeeded") throw new Error(done.error || done.message || "Não foi possível montar o corte");
        const result = done.result ?? {};
        const id = result.edit_plan_artifact_id;
        if (typeof id !== "string" || !id) throw new Error("O plano de edição terminou sem artefato");
        patch(key, {
          planId: id, planTeaser: target.teaserKey, videoUrl: statesRef.current[key]?.videoUrl ?? null,
          timeline: typeof result.timeline_duration_seconds === "number" ? result.timeline_duration_seconds : null,
          refine: (result.refine as RefineStatus | undefined) ?? null, belowMinimum: result.below_minimum === true,
          teaserDropped: typeof result.teaser_dropped === "string" ? result.teaser_dropped : null,
          questionDropped: typeof result.question_dropped === "string" ? result.question_dropped : null,
        });
        if (targetFor(key)?.teaserKey !== target.teaserKey) { patch(key, { running: null }); return; }
      }
      const renderTarget = targetFor(key) ?? target;
      patch(key, { running: "rendering", progress: 50, error: "" });
      const started = await api.startRender(row.project_id, statesRef.current[key].planId!, latest.current.settingsFor(key), run.brief.confirmed_voice ?? null, { purpose: "draft" });
      job.jobId = started.id;
      const done = await finishJob(started, signal, update => patch(key, { progress: 50 + (update.progress || 0) / 2 }));
      if (signal.aborted) return;
      job.jobId = null;
      if (done.status !== "succeeded") throw new Error(done.error || done.message || "Não foi possível gerar a prévia");
      const artifact = done.result?.render_artifact_id;
      if (typeof artifact !== "string" || !artifact) throw new Error("A prévia terminou sem vídeo");
      patch(key, { videoUrl: api.renderMediaUrl(row.project_id, artifact), renderedKey: renderTarget.renderKey, running: null, progress: 100 });
    } catch (reason) {
      if (signal.aborted) return;
      patch(key, { running: null, error: reasonText(reason), errorKey: target.errorKey });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patch]);

  const pump = useCallback(() => {
    if (running.current || disposed.current || !latest.current.enabled) return;
    const { focusedKey: focus } = latest.current;
    const key = pickNext(keys, focus, candidate => {
      const target = targetFor(candidate);
      return !!target && needsWork(statesRef.current[candidate], target, candidate === focus);
    });
    if (!key) return;
    running.current = true;
    void work(key).finally(() => { running.current = false; current.current = null; pump(); });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keys, work]);

  const focusedState = states[focusedKey];
  const focusedTarget = rows.length ? targetFor(focusedKey) : null;
  const refreshing = !!focusedState?.videoUrl && !!focusedTarget && focusedState.renderedKey !== focusedTarget.renderKey;
  const trigger = `${focusedTarget?.errorKey}|${focusedState?.running}|${focusedState?.planId}|${focusedState?.renderedKey}|${focusedState?.error}`;

  useEffect(() => {
    if (!enabled) return;
    const timer = window.setTimeout(pump, refreshing ? RERENDER_DELAY_MS : 0);
    return () => window.clearTimeout(timer);
  // Settings reach the effect through `trigger` (the focused clip's inputs); the callbacks read them from `latest`.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, pump, focusedKey, refreshing, trigger, states, rows]);

  useEffect(() => {
    disposed.current = false;
    return () => {
      disposed.current = true;
      const job = current.current;
      if (job) { job.controller.abort(); cancelQuietly(job.jobId); }
    };
  }, []);

  const retry = useCallback((key: string) => patch(key, { error: "", errorKey: "" }), [patch]);

  const previews = useMemo(() => {
    const out: Record<string, PreviewView> = {};
    for (const row of rows) {
      const key = rowId(row);
      const target = targetOf(row, inputs.settingsFor(key));
      out[key] = { ...(states[key] ?? EMPTY_PREVIEW), phase: phaseOf(states[key], target, key === focusedKey) };
    }
    return out;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, states, focusedKey, inputs.settingsFor]);

  return { previews, retry };
}
