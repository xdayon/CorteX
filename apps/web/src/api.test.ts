import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, isRemoteOrigin } from "./api";
import { defaultRenderSettings } from "./editor/settings";

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

test("draft renders never send camera plan or identity fields", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "job" })));
  vi.stubGlobal("fetch", fetchMock);
  await api.startRender("project", "edl", defaultRenderSettings, { purpose: "draft", workflowRunId: "run" });
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/v1/projects/project/renders");
  expect(JSON.parse(options.body)).toEqual({
    edit_plan_artifact_id: "edl", render_settings: defaultRenderSettings,
    workflow_run_id: "run", purpose: "draft",
  });
});

test("an event stream error falls back to polling and resolves with the final job", async () => {
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "job", status: "running" })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "job", status: "succeeded" })));
    vi.stubGlobal("fetch", fetchMock);
    const update = vi.fn();
    const watched = api.watchJob("job", update);
    Events.instances[0].dispatchEvent(new Event("error"));
    await vi.advanceTimersByTimeAsync(2000);
    await expect(watched).resolves.toMatchObject({ status: "succeeded" });
    expect(update).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/jobs/job");
  } finally { vi.useRealTimers(); }
});

test("a failing poll after an event stream error rejects", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "fora do ar" }), { status: 503 })));
  const watched = api.watchJob("job");
  Events.instances[0].dispatchEvent(new Event("error"));
  await expect(watched).rejects.toThrow("fora do ar");
});

test("requests send a JSON content type only with a body and encode ids", async () => {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response("{}")));
  vi.stubGlobal("fetch", fetchMock);
  await api.episode("a/b");
  await api.createVoiceProfile("ep");
  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/episodes/a%2Fb");
  expect(fetchMock.mock.calls[0][1].headers).toEqual({});
  expect(fetchMock.mock.calls[1][1].headers).toEqual({ "Content-Type": "application/json" });
});

test("only loopback hosts skip the remote upload limit", () => {
  expect(isRemoteOrigin("127.0.0.1")).toBe(false);
  expect(isRemoteOrigin("localhost")).toBe(false);
  expect(isRemoteOrigin("cortex.synapses.academy")).toBe(true);
});

test("an episode never mapped is null whether the API answers 404 or an empty 200", async () => {
  const empty = { artifact_id: null, document: null, roles: null, job: null };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(empty)))
    .mockResolvedValueOnce(new Response("{}", { status: 404 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...empty, job: { id: "j", status: "running" } }))));
  expect(await api.episodeCameras("ep")).toBeNull();
  expect(await api.episodeCameras("ep")).toBeNull();
  expect((await api.episodeCameras("ep"))?.job?.id).toBe("j");
});
