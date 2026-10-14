import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type OverlayPreview } from "../api";
import { defaultRenderSettings } from "./settings";
import { useOverlayPreview } from "./useOverlayPreview";

vi.mock("../api", async (original) => {
  const actual = await original<typeof import("../api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [
    name, vi.fn(name.endsWith("Url") ? fn : undefined),
  ])) };
});

const overlay = (hash: string) => ({ content_hash: hash, ass_text: "", pages: [], junctions: [], fonts: [] }) as unknown as OverlayPreview;
async function flush(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  vi.mocked(api.overlayPreview).mockImplementation(async () => overlay("h"));
});
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

type Props = { headline: string; emphasis: number[] };
const hook = (props: Props) => useOverlayPreview({
  projectId: "project", editPlanArtifactId: "edl", settings: defaultRenderSettings,
  corrections: [{ word_index: 1, original: "a", text: "b" }], emphasis: props.emphasis, headlineText: props.headline,
});

test("debounces by 200 ms and sends corrections, emphasis and the headline", async () => {
  const { rerender } = renderHook(hook, { initialProps: { headline: "A", emphasis: [] } as Props });
  await flush(150);
  expect(api.overlayPreview).not.toHaveBeenCalled();
  rerender({ headline: "AB", emphasis: [2] });
  await flush(150);
  expect(api.overlayPreview).not.toHaveBeenCalled();
  await flush(60);
  expect(api.overlayPreview).toHaveBeenCalledTimes(1);
  const [project, plan, settings] = vi.mocked(api.overlayPreview).mock.lastCall!;
  expect([project, plan]).toEqual(["project", "edl"]);
  expect(settings.headline.text).toBe("AB");
  expect(settings.captions).toMatchObject({ emphasis_word_indices: [2], corrections: [{ word_index: 1, original: "a", text: "b" }] });
});

test("aborts the stale request when settings change and keeps the newest result", async () => {
  const signals: AbortSignal[] = [];
  vi.mocked(api.overlayPreview).mockImplementation(async (_p, _e, _s, signal) => {
    signals.push(signal!);
    await new Promise(resolve => window.setTimeout(resolve, 300));
    return overlay(`h${signals.length}`);
  });
  const { result, rerender } = renderHook(hook, { initialProps: { headline: "A", emphasis: [] } as Props });
  await flush(210);
  rerender({ headline: "B", emphasis: [] });
  await flush(210);
  expect(signals[0].aborted).toBe(true);
  expect(signals[1].aborted).toBe(false);
  await flush(400);
  expect(result.current.overlay?.content_hash).toBe("h2");
});
