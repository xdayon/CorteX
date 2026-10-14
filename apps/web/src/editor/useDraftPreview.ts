import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiJob, type RefineStatus, type RenderSettings, type SuggestedClip, type WorkflowRun } from "../api";
import { clipKey } from "./clips";

export type DraftPhase = "planning" | "rendering" | "ready" | "error";
export type DraftPreview = {
  videoUrl: string | null;
  editPlanArtifactId: string | null;
  timelineSeconds: number | null;
  refine: RefineStatus | null;
  belowMinimum: boolean;
  phase: DraftPhase;
  progress: number;
  error: string;
  retry: () => void;
};

const RERENDER_DELAY_MS = 800;
const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

type Plan = { id: string; clip: string; timeline: number | null; refine: RefineStatus | null; belowMinimum: boolean };

function message(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason);
}

async function finish(job: ApiJob, signal: AbortSignal, onUpdate: (job: ApiJob) => void) {
  if (TERMINAL.has(job.status)) return job;
  return api.watchJob(job.id, update => { if (!signal.aborted) onUpdate(update); }, signal);
}

function cancelQuietly(jobId: string | null) {
  if (!jobId) return;
  try { void Promise.resolve(api.cancelJob(jobId)).catch(() => undefined); } catch { /* best effort */ }
}

export function useDraftPreview({ projectId, run, clip, settings, sceneIndexArtifactId }: {
  projectId: string; run: WorkflowRun; clip: SuggestedClip | undefined; settings: RenderSettings;
  sceneIndexArtifactId?: string | null;
}): DraftPreview {
  const key = clip ? clipKey(clip) : "";
  const [plan, setPlan] = useState<Plan | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [phase, setPhase] = useState<DraftPhase>("planning");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const clipRef = useRef(clip);
  clipRef.current = clip;
  const renderedKey = useRef("");
  const renderKey = JSON.stringify([settings.framing, settings.transitions.piece_junction]);

  useEffect(() => {
    const current = clipRef.current;
    setPlan(null); setVideoUrl(null); setError(""); setProgress(0);
    renderedKey.current = "";
    if (!current) return;
    setPhase("planning");
    const controller = new AbortController();
    let jobId: string | null = null;
    void (async () => {
      try {
        const job = await api.startEditPlan(projectId, {
          transcriptArtifactId: run.artifacts.transcript, analysisArtifactId: run.artifacts.analysis,
          start: current.start_second, end: current.end_second, profile: current.pacing,
          maximumSeconds: run.brief.maximum_seconds, minimumSeconds: run.brief.minimum_seconds,
          approximateEdl: current.approximate_edl,
          confirmedVoice: run.brief.confirmed_voice, primarySubject: run.brief.primary_subject,
          headline: current.headline || null, tighten: true,
          sceneIndexArtifactId: sceneIndexArtifactId ?? null,
        });
        jobId = job.id;
        const done = await finish(job, controller.signal, update => setProgress((update.progress || 0) / 2));
        if (controller.signal.aborted) return;
        jobId = null;
        if (done.status !== "succeeded") throw new Error(done.error || done.message || "Não foi possível montar o corte");
        const result = done.result ?? {};
        const id = result.edit_plan_artifact_id;
        if (typeof id !== "string" || !id) throw new Error("O plano de edição terminou sem artefato");
        setPlan({
          id, clip: key,
          timeline: typeof result.timeline_duration_seconds === "number" ? result.timeline_duration_seconds : null,
          refine: (result.refine as RefineStatus | undefined) ?? null,
          belowMinimum: result.below_minimum === true,
        });
      } catch (reason) {
        if (controller.signal.aborted) return;
        setError(message(reason)); setPhase("error");
      }
    })();
    return () => { controller.abort(); cancelQuietly(jobId); };
    // The plan is rebuilt only for another clip (or a manual retry), exactly like the export.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, key, nonce, run.id, sceneIndexArtifactId]);

  useEffect(() => {
    if (!plan || plan.clip !== key) return;
    const first = renderedKey.current === "";
    const controller = new AbortController();
    let jobId: string | null = null;
    async function render() {
      try {
        setPhase("rendering"); setError("");
        const job = await api.startRender(
          projectId, plan!.id, settingsRef.current,
          run.brief.confirmed_voice ?? null, { purpose: "draft" },
        );
        jobId = job.id;
        const done = await finish(job, controller.signal, update => setProgress(50 + (update.progress || 0) / 2));
        if (controller.signal.aborted) return;
        jobId = null;
        if (done.status !== "succeeded") throw new Error(done.error || done.message || "Não foi possível gerar a prévia");
        const artifact = done.result?.render_artifact_id;
        if (typeof artifact !== "string" || !artifact) throw new Error("A prévia terminou sem vídeo");
        renderedKey.current = renderKey;
        setVideoUrl(api.renderMediaUrl(projectId, artifact));
        setProgress(100); setPhase("ready");
      } catch (reason) {
        if (controller.signal.aborted) return;
        setError(message(reason)); setPhase("error");
      }
    }
    if (renderedKey.current === renderKey) return;
    const timer = window.setTimeout(() => void render(), first ? 0 : RERENDER_DELAY_MS);
    return () => { window.clearTimeout(timer); controller.abort(); cancelQuietly(jobId); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan, renderKey, projectId, nonce]);

  const retry = useCallback(() => { renderedKey.current = ""; setNonce(value => value + 1); }, []);
  const planMatches = plan && plan.clip === key ? plan : null;
  return {
    videoUrl, editPlanArtifactId: planMatches?.id ?? null, timelineSeconds: planMatches?.timeline ?? null,
    refine: planMatches?.refine ?? null, belowMinimum: planMatches?.belowMinimum ?? false,
    phase, progress, error, retry,
  };
}
