import { act, cleanup, render } from "@testing-library/react";
import { useCallback, useState } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type ApiJob, type RenderSettings, type WorkflowRun } from "../../api";
import { defaultRenderSettings } from "../../editor/settings";
import { clipRow } from "../../test/clipFixtures";
import { rowId } from "../clips/clipModel";
import { needsWork, pickNext, targetOf, EMPTY_PREVIEW } from "./previewQueue";
import { usePreviewQueue } from "./usePreviewQueue";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [name, vi.fn(name.endsWith("Url") ? fn : undefined)])) };
});

const run = { id: "run", project_id: "project", artifacts: { transcript: "t", analysis: "a", suggestion: "s" }, brief: { maximum_seconds: 120, minimum_seconds: 40 } } as unknown as WorkflowRun;
const rows = [clipRow(1), clipRow(2), clipRow(3)];
const [A, B, C] = rows.map(rowId);

test("pickNext puts the focused clip first, then the rest in list order", () => {
  const all = () => true;
  expect(pickNext([A, B, C], C, all)).toBe(C);
  expect(pickNext([A, B, C], B, all)).toBe(B);
  expect(pickNext([A, B, C], "", all)).toBe(A);
  expect(pickNext([A, B, C], C, key => key !== C)).toBe(A);
  expect(pickNext([A, B, C], A, key => key === C)).toBe(C);
  expect(pickNext([A, B, C], A, () => false)).toBeNull();
});

test("a clip is prepared when nothing exists yet; stale previews refresh only for the focused clip", () => {
  const target = targetOf(rows[0], defaultRenderSettings);
  const zoomed = targetOf(rows[0], { ...defaultRenderSettings, framing: { ...defaultRenderSettings.framing, zoom: 2 } });
  expect(needsWork(undefined, target, false)).toBe(true);
  const built = { ...EMPTY_PREVIEW, planId: "plan", videoUrl: "/v", renderedKey: target.renderKey };
  expect(needsWork(built, target, true)).toBe(false);
  expect(needsWork(built, zoomed, false)).toBe(false);
  expect(needsWork(built, zoomed, true)).toBe(true);
  const failed = { ...EMPTY_PREVIEW, error: "boom", errorKey: target.errorKey };
  expect(needsWork(failed, target, true)).toBe(false);
  expect(needsWork(failed, zoomed, true)).toBe(true);
  const teaserOn = targetOf({ ...rows[0], clip: { ...rows[0].clip, teaser: { start: 1, end: 2, text: "x" } } }, { ...defaultRenderSettings, transitions: { ...defaultRenderSettings.transitions, teaser: true } });
  expect(needsWork({ ...built, planTeaser: "" }, teaserOn, false)).toBe(false);
  expect(needsWork({ ...built, planTeaser: "" }, teaserOn, true)).toBe(true);
});

type Handle = { setZoom: (zoom: number) => void; focus: (key: string) => void; previews: ReturnType<typeof usePreviewQueue>["previews"] };
const handle = {} as Handle;
function Harness() {
  const [zoom, setZoom] = useState(1.45);
  const [focused, setFocused] = useState(A);
  const settingsFor = useCallback((): RenderSettings => ({ ...defaultRenderSettings, framing: { ...defaultRenderSettings.framing, zoom } }), [zoom]);
  const { previews } = usePreviewQueue({ rows, runs: { run }, focusedKey: focused, enabled: true, settingsFor, sceneFor: () => null });
  Object.assign(handle, { setZoom, focus: setFocused, previews });
  return null;
}

let active = 0;
let peak = 0;
let pending: Array<{ kind: "plan" | "render"; start?: number; finish: () => void }> = [];
const jobOf = (id: string, result: Record<string, unknown>) => ({ id, status: "succeeded", progress: 100, result }) as ApiJob;
function track<T>(kind: "plan" | "render", value: T, start?: number) {
  active += 1; peak = Math.max(peak, active);
  return new Promise<T>(resolve => { pending.push({ kind, start, finish: () => { active -= 1; resolve(value); } }); });
}
async function flush(ms = 0) { for (let i = 0; i < 6; i++) await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function finishNext() {
  const next = pending.shift()!;
  await act(async () => { next.finish(); });
  await flush();
  return next;
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  active = 0; peak = 0; pending = [];
  vi.mocked(api.startEditPlan).mockImplementation((_project, options) => track("plan", jobOf("plan", { edit_plan_artifact_id: `plan-${options.start}`, timeline_duration_seconds: 50 }), options.start));
  vi.mocked(api.startRender).mockImplementation((_project, planId) => track("render", jobOf("render", { render_artifact_id: `draft-${planId}` })));
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

test("the queue prepares one clip at a time, focused first, and a focus change only reorders what is left", async () => {
  render(<Harness />);
  await flush();
  expect(api.startEditPlan).toHaveBeenCalledTimes(1);
  expect(pending[0].start).toBe(100);
  act(() => handle.focus(C));
  await flush();
  expect(api.startEditPlan).toHaveBeenCalledTimes(1);
  expect(handle.previews[A].phase).toBe("planning");
  expect(handle.previews[C].phase).toBe("queued");
  await finishNext();
  expect(pending.map(job => job.kind)).toEqual(["render"]);
  await finishNext();
  expect(handle.previews[A].phase).toBe("ready");
  expect(pending[0]).toMatchObject({ kind: "plan", start: 300 });
  await finishNext(); await finishNext();
  expect(pending[0]).toMatchObject({ kind: "plan", start: 200 });
  await finishNext(); await finishNext();
  expect(Object.values(handle.previews).map(preview => preview.phase)).toEqual(["ready", "ready", "ready"]);
  expect(peak).toBe(1);
  expect(handle.previews[B].videoUrl).toContain("draft-plan-200");
});

test("a framing change re-renders only the focused clip, after a short delay", async () => {
  render(<Harness />);
  await flush();
  for (let i = 0; i < 6; i++) await finishNext();
  expect(Object.values(handle.previews).every(preview => preview.phase === "ready")).toBe(true);
  vi.mocked(api.startRender).mockClear();
  act(() => handle.setZoom(2));
  await flush(100);
  expect(api.startRender).not.toHaveBeenCalled();
  expect(handle.previews[A].phase).toBe("stale");
  await flush(800);
  expect(api.startRender).toHaveBeenCalledTimes(1);
  expect(vi.mocked(api.startRender).mock.calls[0][1]).toBe("plan-100");
  expect(vi.mocked(api.startRender).mock.calls[0][2].framing.zoom).toBe(2);
  expect(vi.mocked(api.startEditPlan)).toHaveBeenCalledTimes(3);
  await finishNext();
  expect(handle.previews[A].phase).toBe("ready");
  expect(handle.previews[B].phase).toBe("stale");
  expect(pending).toHaveLength(0);
  act(() => handle.focus(B));
  await flush(900);
  expect(pending.map(job => job.kind)).toEqual(["render"]);
  expect(peak).toBe(1);
});
