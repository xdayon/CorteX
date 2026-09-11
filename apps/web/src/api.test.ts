import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api } from "./api";

class Events extends EventTarget {
  static instances: Events[] = [];
  close = vi.fn();
  onerror?: () => void;
  constructor(public url: string) { super(); Events.instances.push(this); }
}
beforeEach(() => { Events.instances = []; vi.stubGlobal("EventSource", Events); });
afterEach(() => { vi.unstubAllGlobals(); });

test("changing episodes closes the job subscription and ignores queued events", async () => {
  const controller = new AbortController();
  const update = vi.fn();
  const watched = api.watchJob("job", update, controller.signal);
  const outcome = expect(watched).rejects.toMatchObject({ name: "AbortError" });
  controller.abort();
  await outcome;
  const events = Events.instances[0];
  expect(events.close).toHaveBeenCalledTimes(1);
  events.dispatchEvent(new MessageEvent("job", { data: JSON.stringify({ id: "job", status: "succeeded" }) }));
  expect(update).not.toHaveBeenCalled();
});

test("a terminal job closes its subscription and detaches cancellation", async () => {
  const controller = new AbortController();
  const watched = api.watchJob("job", undefined, controller.signal);
  const events = Events.instances[0];
  events.dispatchEvent(new MessageEvent("job", { data: JSON.stringify({ id: "job", status: "succeeded", result: { render_artifact_id: "mp4" } }) }));
  await expect(watched).resolves.toMatchObject({ result: { render_artifact_id: "mp4" } });
  controller.abort();
  expect(events.close).toHaveBeenCalledTimes(1);
});

test("already cancelled work never opens a subscription", async () => {
  const controller = new AbortController();
  controller.abort();
  await expect(api.watchJob("job", undefined, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  expect(Events.instances).toHaveLength(0);
});

test("identity requests explicitly name the interviewer and preserve the artifact chain", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ interviewer_identity_id: "host" })));
  vi.stubGlobal("fetch", fetchMock);
  await api.selectWorkflowIdentity("project", "run", "host", {
    scene: "scene", face: "face", speaker: "speaker", camera: "camera", quality: "quality", identity: "identity",
  });
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/v1/projects/project/runs/run/identity");
  expect(JSON.parse(options.body)).toEqual({
    interviewer_identity_id: "host", scene_index_artifact_id: "scene", face_index_artifact_id: "face",
    speaker_timeline_artifact_id: "speaker", camera_timeline_artifact_id: "camera",
    visual_quality_artifact_id: "quality", identity_index_artifact_id: "identity",
  });
});
