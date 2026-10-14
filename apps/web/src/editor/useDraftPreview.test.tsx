import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type ApiJob, type SuggestedClip, type WorkflowRun } from "../api";
import { defaultRenderSettings } from "./settings";
import { useDraftPreview } from "./useDraftPreview";

vi.mock("../api", async (original) => {
  const actual = await original<typeof import("../api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [
    name, vi.fn(name.endsWith("Url") ? fn : undefined),
  ])) };
});

const run = {
  id: "run", project_id: "project", source_asset_id: "source",
  brief: { count: 5, minimum_seconds: 40, maximum_seconds: 120, primary_subject: "Dayon", confirmed_voice: { speaker: "A" } },
  artifacts: { transcript: "transcript", analysis: "analysis" },
} as unknown as WorkflowRun;
const clip = {
  rank: 1, title: "Título", headline: "Manchete da IA", start_second: 10, end_second: 70, pacing: "dynamic",
  approximate_edl: [{ order: 0 }],
} as unknown as SuggestedClip;
const other = { ...clip, rank: 2, start_second: 100, end_second: 150 } as SuggestedClip;

function job(id: string, result: Record<string, unknown> = {}, status = "succeeded"): ApiJob {
  return { id, status, progress: 100, result } as ApiJob;
}
async function flush(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  vi.mocked(api.cancelJob).mockResolvedValue(job("x"));
  vi.mocked(api.startEditPlan).mockResolvedValue(job("plan", {
    edit_plan_artifact_id: "edl", timeline_duration_seconds: 52, below_minimum: true, refine: { status: "accepted", reason: "ok" },
  }));
  vi.mocked(api.startRender).mockResolvedValue(job("render", { render_artifact_id: "draft" }));
});
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

test("plans exactly like the export and renders a draft video", async () => {
  const { result } = renderHook(() => useDraftPreview({ projectId: "project", run, clip, settings: defaultRenderSettings, sceneIndexArtifactId: "scene" }));
  await flush();
  expect(api.startEditPlan).toHaveBeenCalledWith("project", {
    transcriptArtifactId: "transcript", analysisArtifactId: "analysis", start: 10, end: 70, profile: "dynamic",
    maximumSeconds: 120, minimumSeconds: 40, approximateEdl: [{ order: 0 }], confirmedVoice: { speaker: "A" },
    primarySubject: "Dayon", headline: "Manchete da IA", tighten: true, sceneIndexArtifactId: "scene",
  });
  expect(api.startRender).toHaveBeenCalledWith("project", "edl", defaultRenderSettings, { speaker: "A" }, { purpose: "draft" });
  expect(result.current).toMatchObject({
    phase: "ready", videoUrl: "/api/v1/projects/project/renders/draft/media", editPlanArtifactId: "edl",
    timelineSeconds: 52, belowMinimum: true, refine: { status: "accepted", reason: "ok" },
  });
});

test("re-renders (debounced) only when framing or the junction style change", async () => {
  const { rerender } = renderHook((settings) => useDraftPreview({ projectId: "project", run, clip, settings }), { initialProps: defaultRenderSettings });
  await flush();
  expect(api.startRender).toHaveBeenCalledTimes(1);
  rerender({ ...defaultRenderSettings, captions: { ...defaultRenderSettings.captions, font_size: 80 } });
  await flush(2000);
  expect(api.startRender).toHaveBeenCalledTimes(1);
  expect(api.startEditPlan).toHaveBeenCalledTimes(1);
  rerender({ ...defaultRenderSettings, framing: { ...defaultRenderSettings.framing, zoom: 1.5 } });
  await flush(700);
  expect(api.startRender).toHaveBeenCalledTimes(1);
  await flush(200);
  expect(api.startRender).toHaveBeenCalledTimes(2);
  expect(vi.mocked(api.startRender).mock.lastCall![2].framing.zoom).toBe(1.5);
  rerender({ ...defaultRenderSettings, framing: { ...defaultRenderSettings.framing, zoom: 1.5 }, transitions: { ...defaultRenderSettings.transitions, piece_junction: "cut" } });
  await flush(900);
  expect(api.startRender).toHaveBeenCalledTimes(3);
  expect(api.startEditPlan).toHaveBeenCalledTimes(1);
});

test("re-plans only when the clip changes and cancels the superseded render job", async () => {
  vi.mocked(api.startRender).mockResolvedValueOnce(job("slow", {}, "running"));
  vi.mocked(api.watchJob).mockImplementationOnce(() => new Promise(() => undefined));
  const { rerender } = renderHook((target: SuggestedClip) => useDraftPreview({ projectId: "project", run, clip: target, settings: defaultRenderSettings }), { initialProps: clip });
  await flush();
  expect(api.startRender).toHaveBeenCalledTimes(1);
  rerender(other);
  await flush();
  expect(api.cancelJob).toHaveBeenCalledWith("slow");
  expect(api.startEditPlan).toHaveBeenCalledTimes(2);
  expect(vi.mocked(api.startEditPlan).mock.lastCall![1]).toMatchObject({ start: 100, end: 150 });
});

test("a failed plan exposes the error and retry runs it again", async () => {
  vi.mocked(api.startEditPlan).mockRejectedValueOnce(new Error("IA indisponível"));
  const { result } = renderHook(() => useDraftPreview({ projectId: "project", run, clip, settings: defaultRenderSettings }));
  await flush();
  expect(result.current).toMatchObject({ phase: "error", error: "IA indisponível" });
  act(() => result.current.retry());
  await flush();
  expect(result.current.phase).toBe("ready");
});

test("sends the teaser range only when the toggle is on and re-plans when it changes", async () => {
  const withTeaser = { ...clip, teaser: { start: 40, end: 43.5, text: "frase" } } as SuggestedClip;
  const on = { ...defaultRenderSettings, transitions: { ...defaultRenderSettings.transitions, teaser: true } };
  const { rerender } = renderHook((settings) => useDraftPreview({ projectId: "project", run, clip: withTeaser, settings }), { initialProps: defaultRenderSettings });
  await flush();
  expect(vi.mocked(api.startEditPlan).mock.lastCall![1]).not.toHaveProperty("teaserStart");
  rerender(on);
  await flush();
  expect(api.startEditPlan).toHaveBeenCalledTimes(2);
  expect(vi.mocked(api.startEditPlan).mock.lastCall![1]).toMatchObject({ teaserStart: 40, teaserEnd: 43.5 });
  rerender({ ...on, captions: { ...on.captions, font_size: 70 } });
  await flush(2000);
  expect(api.startEditPlan).toHaveBeenCalledTimes(2);
});

test("a clip without teaser never sends one, and the drop reason is exposed", async () => {
  const on = { ...defaultRenderSettings, transitions: { ...defaultRenderSettings.transitions, teaser: true } };
  vi.mocked(api.startEditPlan).mockResolvedValue(job("plan", {
    edit_plan_artifact_id: "edl", teaser_applied: false, teaser_dropped: "max_duration",
  }));
  const { result } = renderHook(() => useDraftPreview({ projectId: "project", run, clip, settings: on }));
  await flush();
  expect(vi.mocked(api.startEditPlan).mock.lastCall![1]).not.toHaveProperty("teaserStart");
  expect(result.current.teaserDropped).toBe("max_duration");
});
