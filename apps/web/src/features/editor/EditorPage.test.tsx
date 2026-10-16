import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type ApiJob, type ClipRevision, type EpisodeEntry, type ExportBatch, type WorkflowRun } from "../../api";
import { defaultRenderSettings } from "../../editor/settings";
import { ToastProvider } from "../../ui";
import { EditorPage } from "./EditorPage";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [
    name, vi.fn(name.endsWith("Url") ? fn : undefined),
  ])) };
});

vi.mock("jassub", () => ({
  default: class {
    ready = Promise.resolve();
    renderer = { setTrack: vi.fn(async () => undefined) };
    destroy = vi.fn(async () => undefined);
  },
}));

const ready: WorkflowRun = {
  id: "run", project_id: "project", source_asset_id: "source", status: "ready_for_review",
  stage: "review", progress: 100, message: "Pronto", brief: { count: 10, minimum_seconds: 40, maximum_seconds: 120 },
  artifacts: { transcript: "transcript", analysis: "analysis", suggestion: "suggestion" },
};
const baseClip = {
  rank: 1, title: "Uma ideia", headline: "Headline real", start_second: 0, end_second: 40,
  estimated_duration: 40, pacing: "balanced", primary_speaker: "Convidado", reasoning: "Boa ideia",
  headline_options: ["Headline real", "Outra opção"], emphasis_word_indices: [3],
};
const suggestion = { document: { selection: {
  schema_version: "1.0", selection_notes: "Sugestões reais", clips: [baseClip],
} } } as unknown as Awaited<ReturnType<typeof api.suggestion>>;
const twoClips = (() => {
  const value = structuredClone(suggestion);
  value.document.selection.clips.push({ ...baseClip, rank: 2, title: "Outra ideia", headline: "Outra sugestão", start_second: 60, end_second: 100 } as never);
  return value;
})();
const KEY = "1:0:40:balanced";
const KEY2 = "2:60:100:balanced";
const episode = { id: "ep", runs: [ready] } as unknown as EpisodeEntry;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
async function settle() {
  for (let i = 0; i < 4; i++) await act(async () => { await vi.advanceTimersByTimeAsync(0); });
}
const editor = (clips: string[]) => <ToastProvider><EditorPage id="ep" runId="run" clipKeys={clips} /></ToastProvider>;
async function openEditor(clips = [KEY], saved = ready) {
  vi.mocked(api.workflowRun).mockResolvedValue(saved);
  const view = render(editor(clips));
  await settle();
  return view;
}
const tab = (name: string) => fireEvent.click(screen.getByRole("tab", { name }));
const exportButton = (label: string) => screen.getByRole("button", { name: label });
const revision = (extra: Partial<ClipRevision> = {}) => ({
  id: "revision", project_id: "project", workflow_run_id: "run", version: 1, selection_artifact_id: "suggestion",
  selected_clips: [{ ...baseClip, key: KEY }], render_settings: defaultRenderSettings, caption_edits: {}, headlines: {},
  visual_artifacts: {}, ...extra,
}) as unknown as ClipRevision;
const succeededBatch = {
  id: "batch", project_id: "project", workflow_run_id: "run", revision_id: "revision", status: "succeeded",
  items: [{ id: "item", position: 0, clip_key: KEY, title: "Uma ideia", status: "succeeded", render_artifact_id: "rendered" }],
} as unknown as ExportBatch;
const job = (id: string, result: Record<string, unknown> = {}) => ({ id, type: "render", status: "succeeded", progress: 100, message: "Pronto", result }) as ApiJob;

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  localStorage.clear();
  window.history.replaceState(null, "", "/episodios/ep/editor");
  vi.mocked(api.episodes).mockResolvedValue([episode]);
  vi.mocked(api.suggestion).mockResolvedValue(suggestion);
  vi.mocked(api.renderDefaults).mockResolvedValue({ ...defaultRenderSettings, encoder: "libx264" });
  vi.mocked(api.safeZones).mockResolvedValue({ version: 2, platforms: {} });
  vi.mocked(api.startEditPlan).mockResolvedValue(job("plan", { edit_plan_artifact_id: "edl", timeline_duration_seconds: 38 }));
  vi.mocked(api.startRender).mockResolvedValue(job("render", { render_artifact_id: "draft" }));
  vi.mocked(api.overlayPreview).mockResolvedValue({
    ass_text: "[Script Info]", generator_version: "1", content_hash: "h", timeline_duration_seconds: 38,
    canvas: { width: 1080, height: 1920 }, pages: [], junctions: [], fonts: [{ ass_name: "Montserrat", url: "/api/v1/overlay-fonts/m.otf" }],
  });
  vi.mocked(api.saveRevision).mockResolvedValue(revision());
  vi.mocked(api.latestRevision).mockRejectedValue(new Error("sem revisão"));
  vi.mocked(api.exportBatches).mockResolvedValue([]);
  vi.mocked(api.transcript).mockResolvedValue({ document: { segments: [{ words: [{ word: "Cortecs", start: 1, end: 2 }] }] } });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

test("the header names the batch, links back to the episode and exports from the top right", async () => {
  vi.mocked(api.suggestion).mockResolvedValue(twoClips);
  await openEditor([KEY, KEY2]);
  expect(screen.getByRole("heading", { name: "Editor · 2 cortes" })).toBeTruthy();
  expect(screen.getByRole("link", { name: /Voltar ao episódio/ }).getAttribute("href")).toBe("/episodios/ep");
  expect(exportButton("Exportar 2 cortes")).toBeTruthy();
  expect(api.workflowRun).toHaveBeenCalledWith("project", "run", expect.anything());
});

test("the editor plans, renders a draft and shows the real preview player", async () => {
  await openEditor();
  expect(api.startEditPlan).toHaveBeenCalledWith("project", expect.objectContaining({ tighten: true, headline: "Headline real", profile: "balanced", start: 0, end: 40 }));
  expect(api.startRender).toHaveBeenCalledWith("project", "edl", expect.anything(), null, { purpose: "draft" });
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  expect(api.overlayPreview).toHaveBeenCalled();
  expect(screen.getByLabelText("Prévia do corte").getAttribute("src")).toContain("/renders/draft/media");
  expect(screen.getByRole("tablist", { name: "Seções de ajuste" })).toBeTruthy();
});

test("a run that is missing or not ready explains why instead of opening the editor", async () => {
  vi.mocked(api.episodes).mockResolvedValue([{ id: "ep", runs: [] } as unknown as EpisodeEntry]);
  await openEditor();
  expect(screen.getByText("Não foi possível abrir o editor")).toBeTruthy();
  expect(screen.getByText(/Não encontramos essa seleção/)).toBeTruthy();
  cleanup();
  vi.mocked(api.episodes).mockResolvedValue([episode]);
  await openEditor([KEY], { ...ready, status: "running" });
  expect(screen.getByText(/ainda não está pronta/)).toBeTruthy();
});

test("exporting saves the revision and submits one durable batch, then lists this batch's downloads", async () => {
  vi.mocked(api.createExportBatch).mockResolvedValue(succeededBatch);
  await openEditor();
  vi.mocked(api.startEditPlan).mockClear();
  fireEvent.click(exportButton("Exportar 1 corte"));
  await settle();
  expect(api.saveRevision).toHaveBeenCalled();
  expect(api.createExportBatch).toHaveBeenCalledTimes(1);
  expect(api.createExportBatch).toHaveBeenCalledWith("project", "run", "revision", expect.stringContaining("run:"));
  expect(api.startEditPlan).not.toHaveBeenCalled();
  const mp4 = screen.getByRole("link", { name: "Baixar MP4" });
  expect(mp4.getAttribute("href")).toBe("/api/v1/projects/project/renders/rendered/media");
  expect(screen.getByRole("link", { name: "Baixar SRT" }).getAttribute("href")).toContain("/renders/rendered/subtitles");
  expect(screen.getByText("1 de 1 prontos")).toBeTruthy();
  expect(screen.getAllByRole("link", { name: "Voltar ao episódio" })).toHaveLength(2);
});

test("only the clips named in the URL reach the saved revision, with style, headline and emphasis", async () => {
  vi.mocked(api.suggestion).mockResolvedValue(twoClips);
  vi.mocked(api.createExportBatch).mockResolvedValue(succeededBatch);
  await openEditor([KEY2]);
  tab("Estilo");
  fireEvent.change(screen.getByRole("slider", { name: /Tamanho/ }), { target: { value: "64" } });
  fireEvent.change(document.querySelector('input[type="color"]')!, { target: { value: "#ffcc00" } });
  tab("Manchete");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Minha manchete" } });
  fireEvent.click(exportButton("Exportar 1 corte"));
  await settle();
  const body = vi.mocked(api.saveRevision).mock.lastCall![2];
  expect(body.selected_clips.map(clip => clip.title)).toEqual(["Outra ideia"]);
  expect(body.render_settings.captions).toMatchObject({ font_size: 64, text_color: "#FFCC00", style: "pop" });
  expect(body.headlines).toEqual({ [KEY2]: "Minha manchete" });
  expect(body.emphasis).toEqual({});
  expect(api.startEditPlan).toHaveBeenLastCalledWith("project", expect.objectContaining({ start: 60, end: 100 }));
});

test("without usable clips in the URL every clip of the run is edited", async () => {
  vi.mocked(api.suggestion).mockResolvedValue(twoClips);
  await openEditor(["9:9:9:balanced"]);
  expect(screen.getByRole("heading", { name: "Editor · 2 cortes" })).toBeTruthy();
});

test("the encoder always comes from the server, even for a restored revision", async () => {
  vi.mocked(api.latestRevision).mockResolvedValue(revision({ render_settings: { ...defaultRenderSettings, encoder: "h264_nvenc" } }));
  vi.mocked(api.createExportBatch).mockResolvedValue(succeededBatch);
  await openEditor();
  fireEvent.click(exportButton("Exportar 1 corte"));
  await settle();
  expect(vi.mocked(api.saveRevision).mock.lastCall![2].render_settings.encoder).toBe("libx264");
});

test("a failing render-defaults request keeps the settings and shows a non-blocking error", async () => {
  vi.mocked(api.renderDefaults).mockRejectedValue(new Error("sem defaults"));
  await openEditor();
  expect(screen.getByRole("alert").textContent).toContain("sem defaults");
  expect(exportButton("Exportar 1 corte")).toBeTruthy();
});

test("a legacy framing mode is replaced by the blurred frame with a notice", async () => {
  vi.mocked(api.latestRevision).mockResolvedValue(revision({
    render_settings: { ...defaultRenderSettings, framing: { schema_version: 1, mode: "speaker_face", punch_in: { enabled: false, scale: 1.15, anchor: "center", alternate_on_jump_cuts: true } } } as never,
  }));
  await openEditor();
  expect(screen.getByText(/enquadramento antigo/)).toBeTruthy();
  tab("Enquadramento");
  expect(screen.getByRole("button", { name: /Quadro com fundo desfocado/ }).getAttribute("aria-pressed")).toBe("true");
});

test("autosave restores settings, headline and the finished batch after a reload", async () => {
  vi.mocked(api.createExportBatch).mockResolvedValue(succeededBatch);
  const view = await openEditor();
  tab("Estilo");
  fireEvent.change(screen.getByRole("slider", { name: /Tamanho/ }), { target: { value: "72" } });
  fireEvent.change(screen.getByRole("slider", { name: /Posição vertical/ }), { target: { value: "65" } });
  tab("Manchete");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Minha manchete" } });
  fireEvent.click(exportButton("Exportar 1 corte"));
  await settle();
  expect(JSON.parse(localStorage.getItem("cortex-draft:project:run")!)).toMatchObject({
    schema_version: 1, settings: { captions: { font_size: 72, position_y: .65 } },
    headlines: { [KEY]: "Minha manchete" }, selectedKeys: [KEY],
    rendered: [{ title: "Uma ideia", artifactId: "rendered", projectId: "project" }],
  });
  view.unmount();
  vi.mocked(api.exportBatches).mockResolvedValue([succeededBatch]);
  render(editor([KEY]));
  await settle();
  tab("Estilo");
  expect((screen.getByRole("slider", { name: /Tamanho/ }) as HTMLInputElement).value).toBe("72");
  tab("Manchete");
  expect((screen.getByRole("textbox", { name: /Texto da manchete/ }) as HTMLTextAreaElement).value).toBe("Minha manchete");
  expect(screen.getByRole("link", { name: "Baixar MP4" })).toBeTruthy();
});

test("headlines are independent per clip, reach the batch and restore the AI suggestion explicitly", async () => {
  vi.mocked(api.suggestion).mockResolvedValue(twoClips);
  vi.mocked(api.createExportBatch).mockResolvedValue(succeededBatch);
  await openEditor([KEY, KEY2]);
  tab("Manchete");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Manchete do primeiro" } });
  fireEvent.click(screen.getByRole("button", { name: /Outra ideia/ }));
  await settle();
  expect((screen.getByRole("textbox", { name: /Texto da manchete/ }) as HTMLTextAreaElement).value).toBe("");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Manchete do segundo" } });
  fireEvent.click(exportButton("Exportar 2 cortes"));
  await settle();
  expect(vi.mocked(api.saveRevision).mock.lastCall![2].headlines).toEqual({ [KEY]: "Manchete do primeiro", [KEY2]: "Manchete do segundo" });
  fireEvent.click(screen.getByRole("button", { name: "Usar a sugestão da IA" }));
  expect(JSON.parse(localStorage.getItem("cortex-draft:project:run")!).headlines).toEqual({ [KEY]: "Manchete do primeiro" });
});

test.each(['{invalid', JSON.stringify({ schema_version: 1, settings: { captions: null }, captionEdits: [], headlines: 42 }), JSON.stringify({ schema_version: 99 })])("an invalid local draft restores safe defaults: %s", async (draft) => {
  localStorage.setItem("cortex-draft:project:run", draft);
  await openEditor();
  tab("Estilo");
  expect((screen.getByRole("slider", { name: /Tamanho/ }) as HTMLInputElement).value).toBe("56");
  tab("Manchete");
  expect((screen.getByRole("textbox", { name: /Texto da manchete/ }) as HTMLTextAreaElement).value).toBe("");
});

test("caption corrections and emphasis edited in the Legenda tab reach the saved revision and draft", async () => {
  vi.mocked(api.overlayPreview).mockResolvedValue({
    ass_text: "x", generator_version: "1", content_hash: "h", timeline_duration_seconds: 38, canvas: { width: 1080, height: 1920 },
    pages: [{ start: 0, end: 1, lines: [[{ index: 4, text: "CORTECS", start: 0, end: 1, emphasis: false, original: "Cortecs", probability: 0.9 }]] }],
    junctions: [], fonts: [],
  });
  vi.mocked(api.createExportBatch).mockResolvedValue(succeededBatch);
  await openEditor();
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  fireEvent.click(screen.getByRole("button", { name: "CORTECS" }));
  fireEvent.click(screen.getByRole("button", { name: "★ Destacar" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Texto da palavra" }), { target: { value: "CorteX" } });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Texto da palavra" }), { key: "Enter" });
  fireEvent.click(exportButton("Exportar 1 corte"));
  await settle();
  const body = vi.mocked(api.saveRevision).mock.lastCall![2];
  expect(body.caption_edits).toEqual({ [KEY]: [{ word_index: 4, original: "Cortecs", text: "CorteX" }] });
  expect(body.emphasis).toEqual({ [KEY]: [3, 4] });
  expect(JSON.parse(localStorage.getItem("cortex-draft:project:run")!).emphasisEdits).toEqual({ [KEY]: [3, 4] });
});

test("a running batch can be cancelled and a batch with failures can be retried", async () => {
  const running = { ...succeededBatch, status: "running", items: [{ ...succeededBatch.items[0], status: "rendering", render_artifact_id: null }] } as unknown as ExportBatch;
  vi.mocked(api.exportBatches).mockResolvedValue([running]);
  vi.mocked(api.cancelExportBatch).mockResolvedValue({ ...running, status: "cancelled" } as ExportBatch);
  await openEditor();
  expect(screen.getByText("Exportando")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cancelar exportação" }));
  await settle();
  expect(api.cancelExportBatch).toHaveBeenCalledWith("project", "batch");
  cleanup();
  const failed = { ...succeededBatch, status: "partial", items: [{ ...succeededBatch.items[0], status: "failed", render_artifact_id: null, error: "Sem espaço em disco\ndetalhe interno" }] } as unknown as ExportBatch;
  vi.mocked(api.exportBatches).mockResolvedValue([failed]);
  vi.mocked(api.retryExportBatch).mockResolvedValue(running);
  await openEditor();
  expect(screen.getByText("Sem espaço em disco")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Repetir falhas" }));
  await settle();
  expect(api.retryExportBatch).toHaveBeenCalledWith("project", "batch");
});

test("leaving the editor ignores a late response", async () => {
  const pending = deferred<WorkflowRun>();
  vi.mocked(api.workflowRun).mockReturnValue(pending.promise);
  const view = render(editor([KEY]));
  await settle();
  view.unmount();
  await act(async () => { pending.resolve(ready); });
  await settle();
  expect(api.suggestion).not.toHaveBeenCalled();
});
