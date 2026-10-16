import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type ApiJob, type ClipRevision, type ExportBatch, type WorkflowRun } from "../../api";
import { defaultRenderSettings } from "../../editor/settings";
import { clipRow, episodeClips } from "../../test/clipFixtures";
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
  id: "run", project_id: "project", source_asset_id: "src", status: "ready_for_review",
  stage: "review", progress: 100, message: "Pronto", brief: { count: 10, minimum_seconds: 40, maximum_seconds: 120 },
  artifacts: { transcript: "transcript", analysis: "analysis", suggestion: "suggestion" },
};
const row1 = clipRow(1, {}, { headline: "Headline real", title: "Uma ideia", headline_options: ["Headline real", "Outra opção"], emphasis_word_indices: [3], estimated_duration: 40 });
const row2 = clipRow(2, {}, { headline: "Outra sugestão", title: "Outra ideia" });
const other = clipRow(7, { run_id: "run2" }, { headline: "Da outra seleção", title: "Terceira ideia" });
const K1 = row1.clip_key;
const K2 = row2.clip_key;
const K7 = other.clip_key;
const Q1 = `run~${K1}`, Q2 = `run~${K2}`, Q7 = `run2~${K7}`;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
async function settle(rounds = 10) {
  for (let i = 0; i < rounds; i++) await act(async () => { await vi.advanceTimersByTimeAsync(0); });
}
const editor = (keys: string[], run = "") => <ToastProvider><EditorPage id="ep" clipKeys={keys} run={run} /></ToastProvider>;
async function openEditor(keys = [Q1], run = "") {
  const view = render(editor(keys, run));
  await settle();
  return view;
}
const tab = (name: string) => fireEvent.click(screen.getByRole("tab", { name }));
const exportButton = (label: string) => screen.getByRole("button", { name: label });
const revision = (extra: Partial<ClipRevision> = {}) => ({
  id: "revision", project_id: "project", workflow_run_id: "run", version: 1, selection_artifact_id: "suggestion",
  selected_clips: [], render_settings: defaultRenderSettings, caption_edits: {}, headlines: {}, visual_artifacts: {}, ...extra,
}) as unknown as ClipRevision;
const batchFor = (runId: string, key: string, status = "succeeded"): ExportBatch => ({
  id: `batch-${runId}`, project_id: "project", workflow_run_id: runId, revision_id: "revision", status: "succeeded",
  items: [{ id: `item-${runId}`, position: 0, clip_key: key, title: "Uma ideia", status, render_artifact_id: status === "succeeded" ? `rendered-${runId}` : null }],
}) as unknown as ExportBatch;
const succeededBatch = batchFor("run", K1);
const job = (id: string, result: Record<string, unknown> = {}) => ({ id, type: "render", status: "succeeded", progress: 100, message: "Pronto", result }) as ApiJob;
const savedBody = (call = -1) => { const calls = vi.mocked(api.saveRevision).mock.calls; return calls.at(call)![2]; };

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  localStorage.clear();
  window.history.replaceState(null, "", "/episodios/ep/editor");
  vi.mocked(api.episodeClips).mockResolvedValue(episodeClips([row1, row2, other]));
  vi.mocked(api.workflowRun).mockImplementation(async (_project, id) => (id === "run2" ? { ...ready, id: "run2", artifacts: { ...ready.artifacts, suggestion: "suggestion2" } } : ready));
  vi.mocked(api.renderDefaults).mockResolvedValue({ ...defaultRenderSettings, encoder: "libx264" });
  vi.mocked(api.safeZones).mockResolvedValue({ version: 2, platforms: {} });
  vi.mocked(api.startEditPlan).mockImplementation(async () => job("plan", { edit_plan_artifact_id: "edl", timeline_duration_seconds: 38 }));
  vi.mocked(api.startRender).mockImplementation(async () => job("render", { render_artifact_id: "draft" }));
  vi.mocked(api.editPlan).mockResolvedValue({ document: { timeline_duration_seconds: 38, segments: [
    { start: 100, end: 120, timeline_order: 0, piece_index: 0 }, { start: 130, end: 148, timeline_order: 1, piece_index: 1 },
  ] } });
  vi.mocked(api.overlayPreview).mockResolvedValue({
    ass_text: "[Script Info]", generator_version: "1", content_hash: "h", timeline_duration_seconds: 38,
    canvas: { width: 1080, height: 1920 }, pages: [], junctions: [], fonts: [{ ass_name: "Montserrat", url: "/api/v1/overlay-fonts/m.otf" }],
  });
  vi.mocked(api.saveRevision).mockImplementation(async (_project, runId) => revision({ workflow_run_id: runId, id: `revision-${runId}` }));
  vi.mocked(api.latestRevision).mockRejectedValue(new Error("sem revisão"));
  vi.mocked(api.exportBatches).mockResolvedValue([]);
  vi.mocked(api.transcript).mockResolvedValue({ document: { segments: [{ words: [{ word: "Cortecs", start: 1, end: 2 }] }] } });
  vi.mocked(api.createExportBatch).mockImplementation(async (_project, runId) => batchFor(runId, runId === "run2" ? K7 : K1));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

test("the header names the batch, links back to the episode and exports from the top right", async () => {
  await openEditor([Q1, Q2]);
  expect(screen.getByRole("heading", { name: "Editor · 2 cortes" })).toBeTruthy();
  expect(screen.getByRole("link", { name: /Voltar ao episódio/ }).getAttribute("href")).toBe("/episodios/ep");
  expect(exportButton("Exportar 2 cortes")).toBeTruthy();
  expect(screen.getByText("Alterações salvas")).toBeTruthy();
  expect(api.workflowRun).toHaveBeenCalledWith("project", "run", expect.anything());
  expect(api.episodeClips).toHaveBeenCalledWith("ep", expect.anything());
});

test("the editor plans, renders a draft and shows the real preview player", async () => {
  await openEditor();
  expect(api.startEditPlan).toHaveBeenCalledWith("project", expect.objectContaining({ tighten: true, headline: "Headline real", profile: "balanced", start: 100, end: 160 }));
  expect(api.startRender).toHaveBeenCalledWith("project", "edl", expect.anything(), null, { purpose: "draft" });
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  expect(api.overlayPreview).toHaveBeenCalled();
  expect(screen.getByLabelText("Prévia do corte").getAttribute("src")).toContain("/renders/draft/media");
  expect(screen.getByRole("tablist", { name: "Seções de ajuste" })).toBeTruthy();
  expect(screen.getByText("Prévia atualizada · rascunho")).toBeTruthy();
});

test("the timeline shows the pieces of the plan with a junction between them", async () => {
  await openEditor();
  const pieces = screen.getAllByTestId("trecho");
  expect(pieces.map(piece => piece.textContent)).toEqual(["Trecho 1", "Trecho 2"]);
  expect(document.querySelectorAll(".join")).toHaveLength(1);
  expect(screen.getByRole("slider", { name: "Posição na linha do tempo" })).toBeTruthy();
});

test("a run that is missing or not ready explains why instead of opening the editor", async () => {
  vi.mocked(api.episodeClips).mockResolvedValue(episodeClips([]));
  await openEditor([Q1]);
  expect(screen.getByText("Não foi possível abrir o editor")).toBeTruthy();
  expect(screen.getByText(/Não encontramos essa seleção/)).toBeTruthy();
  cleanup();
  vi.mocked(api.episodeClips).mockResolvedValue(episodeClips([row1]));
  vi.mocked(api.workflowRun).mockResolvedValue({ ...ready, status: "running" });
  await openEditor([Q1]);
  expect(screen.getByText(/ainda não está pronta/)).toBeTruthy();
});

test("exporting saves the revision and submits one durable batch, then lists this batch's downloads", async () => {
  await openEditor();
  vi.mocked(api.startEditPlan).mockClear();
  fireEvent.click(exportButton("Exportar 1 corte"));
  await settle();
  expect(api.saveRevision).toHaveBeenCalled();
  expect(api.createExportBatch).toHaveBeenCalledTimes(1);
  expect(api.createExportBatch).toHaveBeenCalledWith("project", "run", "revision-run", expect.stringContaining("run:"));
  const dialog = screen.getByRole("dialog", { name: "Exportação" });
  expect(within(dialog).getByRole("link", { name: "Baixar MP4" }).getAttribute("href")).toBe("/api/v1/projects/project/renders/rendered-run/media");
  expect(within(dialog).getByRole("link", { name: "Baixar SRT" }).getAttribute("href")).toContain("/renders/rendered-run/subtitles");
  expect(within(dialog).getByText("1 de 1 prontos")).toBeTruthy();
});

test("only the clips named in the URL reach the saved revision, with style, headline and emphasis", async () => {
  await openEditor([Q2]);
  tab("Estilo");
  fireEvent.change(screen.getByRole("slider", { name: /Tamanho/ }), { target: { value: "64" } });
  fireEvent.change(document.querySelector('input[type="color"]')!, { target: { value: "#ffcc00" } });
  tab("Manchete");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Minha manchete" } });
  fireEvent.click(exportButton("Exportar 1 corte"));
  await settle();
  const body = savedBody();
  expect(body.selected_clips.map(clip => clip.title)).toEqual(["Outra ideia"]);
  expect(body.selected_clips[0].key).toBe(K2);
  expect(body.render_settings.captions).toMatchObject({ font_size: 64, text_color: "#FFCC00", style: "pop" });
  expect(body.headlines).toEqual({ [K2]: "Minha manchete" });
  expect(body.emphasis).toEqual({});
  expect(body.clip_settings).toEqual({});
  expect(api.startEditPlan).toHaveBeenLastCalledWith("project", expect.objectContaining({ start: 200, end: 260 }));
});

test("the legacy run-only URL edits every clip of that run, and unusable keys without a run fail", async () => {
  await openEditor([], "run");
  expect(screen.getByRole("heading", { name: "Editor · 2 cortes" })).toBeTruthy();
  cleanup();
  await openEditor(["run~9:9:9:balanced"], "run");
  expect(screen.getByRole("heading", { name: "Editor · 2 cortes" })).toBeTruthy();
  cleanup();
  await openEditor(["run~9:9:9:balanced"]);
  expect(screen.getByText(/Não encontramos essa seleção/)).toBeTruthy();
});

test("the encoder always comes from the server, even for a restored revision", async () => {
  vi.mocked(api.latestRevision).mockResolvedValue(revision({ render_settings: { ...defaultRenderSettings, encoder: "h264_nvenc" } }));
  await openEditor();
  fireEvent.click(exportButton("Exportar 1 corte"));
  await settle();
  expect(savedBody().render_settings.encoder).toBe("libx264");
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
  const view = await openEditor();
  tab("Estilo");
  fireEvent.change(screen.getByRole("slider", { name: /Tamanho/ }), { target: { value: "72" } });
  fireEvent.change(screen.getByRole("slider", { name: /Altura na tela/ }), { target: { value: "65" } });
  tab("Manchete");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Minha manchete" } });
  fireEvent.click(exportButton("Exportar 1 corte"));
  await settle();
  expect(JSON.parse(localStorage.getItem("cortex-draft:project:run")!)).toMatchObject({
    schema_version: 1, settings: { captions: { font_size: 72, position_y: .65 } },
    headlines: { [K1]: "Minha manchete" }, selectedKeys: [K1],
    rendered: [{ title: "Uma ideia", artifactId: "rendered-run", projectId: "project" }],
  });
  view.unmount();
  vi.mocked(api.exportBatches).mockResolvedValue([succeededBatch]);
  render(editor([Q1]));
  await settle();
  tab("Estilo");
  expect((screen.getByRole("slider", { name: /Tamanho/ }) as HTMLInputElement).value).toBe("72");
  tab("Manchete");
  expect((screen.getByRole("textbox", { name: /Texto da manchete/ }) as HTMLInputElement).value).toBe("Minha manchete");
  fireEvent.click(screen.getByRole("button", { name: "Ver exportação" }));
  expect(screen.getByRole("link", { name: "Baixar MP4" })).toBeTruthy();
});

test("headlines are independent per clip, reach the revision and restore the AI suggestion explicitly", async () => {
  await openEditor([Q1, Q2]);
  tab("Manchete");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Manchete do primeiro" } });
  fireEvent.click(screen.getByRole("button", { name: /Outra sugestão/ }));
  await settle();
  expect((screen.getByRole("textbox", { name: /Texto da manchete/ }) as HTMLInputElement).value).toBe("Outra sugestão");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Manchete do segundo" } });
  fireEvent.click(exportButton("Exportar 2 cortes"));
  await settle();
  expect(savedBody().headlines).toEqual({ [K1]: "Manchete do primeiro", [K2]: "Manchete do segundo" });
  fireEvent.click(screen.getByRole("button", { name: "Fechar" }));
  fireEvent.click(screen.getByRole("button", { name: "Usar a sugestão da IA" }));
  expect(JSON.parse(localStorage.getItem("cortex-draft:project:run")!).headlines).toEqual({ [K1]: "Manchete do primeiro" });
});

test.each(['{invalid', JSON.stringify({ schema_version: 1, settings: { captions: null }, captionEdits: [], headlines: 42 }), JSON.stringify({ schema_version: 99 })])("an invalid local draft restores safe defaults: %s", async (draft) => {
  localStorage.setItem("cortex-draft:project:run", draft);
  await openEditor();
  tab("Estilo");
  expect((screen.getByRole("slider", { name: /Tamanho/ }) as HTMLInputElement).value).toBe("56");
  tab("Manchete");
  expect((screen.getByRole("textbox", { name: /Texto da manchete/ }) as HTMLInputElement).value).toBe("Headline real");
});

test("caption corrections and emphasis edited in the Legenda tab reach the saved revision and draft", async () => {
  vi.mocked(api.overlayPreview).mockResolvedValue({
    ass_text: "x", generator_version: "1", content_hash: "h", timeline_duration_seconds: 38, canvas: { width: 1080, height: 1920 },
    pages: [{ start: 0, end: 1, lines: [[{ index: 4, text: "CORTECS", start: 0, end: 1, emphasis: false, original: "Cortecs", probability: 0.9 }]] }],
    junctions: [], fonts: [],
  });
  await openEditor();
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  fireEvent.click(screen.getByRole("button", { name: "Cortecs" }));
  fireEvent.click(screen.getByRole("button", { name: "★ Destacar" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Texto da palavra" }), { target: { value: "CorteX" } });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Texto da palavra" }), { key: "Enter" });
  fireEvent.click(exportButton("Exportar 1 corte"));
  await settle();
  const body = savedBody();
  expect(body.caption_edits).toEqual({ [K1]: [{ word_index: 4, original: "Cortecs", text: "CorteX" }] });
  expect(body.emphasis).toEqual({ [K1]: [3, 4] });
  expect(JSON.parse(localStorage.getItem("cortex-draft:project:run")!).emphasisEdits).toEqual({ [K1]: [3, 4] });
  expect(document.querySelectorAll('[data-testid="caption-block"]')).toHaveLength(1);
});

test("a running batch can be cancelled and a batch with failures can be retried", async () => {
  const running = { ...batchFor("run", K1, "rendering"), status: "running" } as ExportBatch;
  vi.mocked(api.exportBatches).mockResolvedValue([running]);
  vi.mocked(api.cancelExportBatch).mockResolvedValue({ ...running, status: "cancelled" } as ExportBatch);
  await openEditor();
  expect(within(screen.getByRole("navigation", { name: "Cortes selecionados" })).getByText("Exportando")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Ver exportação" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancelar exportação" }));
  await settle();
  expect(api.cancelExportBatch).toHaveBeenCalledWith("project", "batch-run");
  cleanup();
  const failed = { ...batchFor("run", K1, "failed"), status: "partial" } as ExportBatch;
  failed.items[0].error = "Sem espaço em disco\ndetalhe interno";
  vi.mocked(api.exportBatches).mockResolvedValue([failed]);
  vi.mocked(api.retryExportBatch).mockResolvedValue(running);
  await openEditor();
  fireEvent.click(screen.getByRole("button", { name: "Ver exportação" }));
  expect(screen.getByText("Sem espaço em disco")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Repetir falhas" }));
  await settle();
  expect(api.retryExportBatch).toHaveBeenCalledWith("project", "batch-run");
});

test("leaving the editor ignores a late response", async () => {
  const pending = deferred<WorkflowRun>();
  vi.mocked(api.workflowRun).mockReturnValue(pending.promise);
  const view = render(editor([Q1]));
  await settle();
  view.unmount();
  await act(async () => { pending.resolve(ready); });
  await settle();
  expect(api.latestRevision).not.toHaveBeenCalled();
});

test("a change applied to one clip is a per-clip override: it drives that clip's draft and the revision's clip_settings", async () => {
  await openEditor([Q1, Q2]);
  tab("Enquadramento");
  fireEvent.click(screen.getByRole("radio", { name: "Este corte" }));
  fireEvent.change(screen.getByRole("slider", { name: /Zoom do vídeo/ }), { target: { value: "2" } });
  expect(screen.getByText("Personalizado")).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  await settle();
  const zooms = vi.mocked(api.startRender).mock.calls.map(call => call[2].framing.zoom);
  expect(zooms).toContain(2);
  fireEvent.click(exportButton("Exportar 2 cortes"));
  await settle();
  const body = savedBody();
  expect(body.render_settings.framing.zoom).toBe(1.45);
  expect(body.clip_settings).toEqual({ [K1]: { framing: { zoom: 2 } } });
  fireEvent.click(screen.getByRole("button", { name: "Fechar" }));
  fireEvent.click(screen.getByRole("button", { name: "Voltar ao padrão" }));
  expect(screen.queryByText("Personalizado")).toBeNull();
});

test("an edit applied to all clips changes the shared settings and clears the clip overrides it touches", async () => {
  await openEditor([Q1, Q2]);
  tab("Enquadramento");
  fireEvent.click(screen.getByRole("radio", { name: "Este corte" }));
  fireEvent.change(screen.getByRole("slider", { name: /Zoom do vídeo/ }), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("radio", { name: /Todos \(2\)/ }));
  fireEvent.change(screen.getByRole("slider", { name: /Zoom do vídeo/ }), { target: { value: "1.8" } });
  expect(screen.queryByText("Personalizado")).toBeNull();
  fireEvent.click(exportButton("Exportar 2 cortes"));
  await settle();
  expect(savedBody().render_settings.framing.zoom).toBe(1.8);
  expect(savedBody().clip_settings).toEqual({});
});

test("a restored revision brings back its per-clip overrides", async () => {
  vi.mocked(api.latestRevision).mockResolvedValue(revision({ clip_settings: { [K2]: { captions: { font_size: 80 } } } }));
  await openEditor([Q1, Q2]);
  expect(within(screen.getByRole("navigation", { name: "Cortes selecionados" })).getByText("Personalizado")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Outra sugestão/ }));
  tab("Estilo");
  expect((screen.getByRole("slider", { name: /Tamanho/ }) as HTMLInputElement).value).toBe("80");
});

test("clips from several runs are saved one revision per run, each with only its own clips, and exported as one batch per run", async () => {
  await openEditor([Q1, Q7]);
  tab("Manchete");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Primeira" } });
  fireEvent.click(screen.getByRole("button", { name: /Da outra seleção/ }));
  await settle();
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Segunda" } });
  vi.mocked(api.saveRevision).mockClear();
  fireEvent.click(exportButton("Exportar 2 cortes"));
  await settle();
  const calls = vi.mocked(api.saveRevision).mock.calls;
  const byRun = Object.fromEntries(calls.slice(-2).map(call => [call[1], call[2]]));
  expect(Object.keys(byRun).sort()).toEqual(["run", "run2"]);
  expect(byRun.run.selected_clips.map(clip => clip.key)).toEqual([K1]);
  expect(byRun.run.selection_artifact_id).toBe("suggestion");
  expect(byRun.run.headlines).toEqual({ [K1]: "Primeira" });
  expect(byRun.run2.selected_clips.map(clip => clip.key)).toEqual([K7]);
  expect(byRun.run2.selection_artifact_id).toBe("suggestion2");
  expect(byRun.run2.headlines).toEqual({ [K7]: "Segunda" });
  expect(api.createExportBatch).toHaveBeenCalledWith("project", "run", "revision-run", expect.any(String));
  expect(api.createExportBatch).toHaveBeenCalledWith("project", "run2", "revision-run2", expect.any(String));
  expect(api.workflowRun).toHaveBeenCalledWith("project", "run2", expect.anything());
  const dialog = screen.getByRole("dialog", { name: "Exportação" });
  expect(within(dialog).getByText("2 de 2 prontos")).toBeTruthy();
});

test("keyboard shortcuts move between clips and export with Ctrl+Enter, but not while typing", async () => {
  await openEditor([Q1, Q2]);
  const rail = within(screen.getByRole("navigation", { name: "Cortes selecionados" }));
  expect(rail.getByRole("button", { name: /Headline real/ }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.keyDown(window, { key: "ArrowDown" });
  expect(rail.getByRole("button", { name: /Outra sugestão/ }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.keyDown(window, { key: "ArrowUp" });
  expect(rail.getByRole("button", { name: /Headline real/ }).getAttribute("aria-pressed")).toBe("true");
  tab("Manchete");
  fireEvent.keyDown(screen.getByRole("textbox", { name: /Texto da manchete/ }), { key: "Enter", ctrlKey: true });
  await settle();
  expect(api.createExportBatch).not.toHaveBeenCalled();
  fireEvent.keyDown(window, { key: "Enter", ctrlKey: true });
  await settle();
  expect(api.createExportBatch).toHaveBeenCalledTimes(1);
});
