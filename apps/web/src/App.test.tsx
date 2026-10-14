import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";
import { api, type ApiJob, type ClipRevision, type WorkflowRun } from "./api";
import { defaultRenderSettings } from "./editor/settings";

vi.mock("./api", async (original) => {
  const actual = await original<typeof import("./api")>();
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

function job(id: string, result: Record<string, unknown> = {}): ApiJob {
  return { id, type: "render", status: "succeeded", progress: 100, message: "Pronto", result } as ApiJob;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function settle() {
  for (let i = 0; i < 3; i++) await act(async () => { await vi.advanceTimersByTimeAsync(0); });
}
async function resumeLastEpisode() {
  await settle();
  fireEvent.click(screen.getByRole("button", {name: "Retomar último episódio"}));
  await settle();
}
async function openExport(saved = ready, count = 1) {
  localStorage.setItem("cortex-active-run", "project:run");
  vi.mocked(api.workflowRun).mockResolvedValue(saved);
  const view = render(<App />);
  await resumeLastEpisode();
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`Editar ${count} selecionado`) }));
  await settle();
  return view;
}
const tab = (name: string) => fireEvent.click(screen.getByRole("tab", { name }));
const revision = (extra: Partial<ClipRevision> = {}) => ({
  id: "revision", project_id: "project", workflow_run_id: "run", version: 1, selection_artifact_id: "suggestion",
  selected_clips: [{ ...baseClip, key: KEY }], render_settings: defaultRenderSettings, caption_edits: {}, headlines: {},
  visual_artifacts: {}, reuse_reactions: false, ...extra,
}) as unknown as ClipRevision;
const succeededBatch = {
  id: "batch", project_id: "project", workflow_run_id: "run", revision_id: "revision", status: "succeeded",
  items: [{ id: "item", position: 0, clip_key: KEY, title: "Uma ideia", status: "succeeded", render_artifact_id: "rendered" }],
} as unknown as Awaited<ReturnType<typeof api.createExportBatch>>;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.saveRevision).mockReset();
  vi.mocked(api.createExportBatch).mockReset();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  localStorage.clear();
  vi.mocked(api.episodes).mockResolvedValue([]);
  vi.mocked(api.suggestion).mockResolvedValue(suggestion);
  vi.mocked(api.renderDefaults).mockResolvedValue({ ...defaultRenderSettings, encoder: "libx264" });
  vi.mocked(api.safeZones).mockResolvedValue({ version: 2, platforms: {} });
  vi.mocked(api.cancelJob).mockResolvedValue(job("cancelled"));
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

test.each(["queued", "running"] as const)("reopening %s work follows it to human review", async (status) => {
  localStorage.setItem("cortex-active-run", "project:run");
  vi.mocked(api.workflowRun).mockResolvedValueOnce({ ...ready, status, stage: "analyze", progress: 35, message: "Analisando" }).mockResolvedValueOnce(ready);
  render(<App />);
  await resumeLastEpisode();
  expect(screen.getByText("Analisando")).toBeTruthy();
  expect((screen.getByRole("button", { name: /Processando episódio/ }) as HTMLButtonElement).disabled).toBe(true);
  await act(() => vi.advanceTimersByTimeAsync(1200));
  expect(screen.getByText("Uma ideia")).toBeTruthy();
  expect(api.workflowRun).toHaveBeenCalledTimes(2);
  expect(api.startRender).not.toHaveBeenCalled();
});

test("unmount cancels polling and ignores a late response in a replacement view", async () => {
  localStorage.setItem("cortex-active-run", "project:run");
  const pending = deferred<WorkflowRun>();
  vi.mocked(api.workflowRun).mockResolvedValueOnce({ ...ready, status: "running" }).mockReturnValueOnce(pending.promise);
  const first = render(<App />);
  await resumeLastEpisode();
  await act(() => vi.advanceTimersByTimeAsync(1200));
  const signal = vi.mocked(api.workflowRun).mock.calls[1][2]!;
  first.unmount();
  expect(signal.aborted).toBe(true);
  localStorage.removeItem("cortex-active-run");
  render(<App />);
  await act(async () => { pending.resolve(ready); });
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(api.workflowRun).toHaveBeenCalledTimes(2);
  expect(api.suggestion).not.toHaveBeenCalled();
  expect(screen.queryByText("Uma ideia")).toBeNull();
});

test.each([
  ["failed", "Claude indisponível"], ["cancelled", "O processamento foi cancelado"],
] as const)("reopening %s work displays its terminal failure", async (status, message) => {
  localStorage.setItem("cortex-active-run", "project:run");
  vi.mocked(api.workflowRun).mockResolvedValue({ ...ready, status, error: status === "failed" ? message : null });
  render(<App />);
  await resumeLastEpisode();
  expect(screen.getByRole("alert").textContent).toContain(message);
  expect(api.suggestion).not.toHaveBeenCalled();
});

test("a temporary reload error keeps the saved run available for retry", async () => {
  localStorage.setItem("cortex-active-run", "project:run");
  vi.mocked(api.workflowRun).mockRejectedValueOnce(new Error("API indisponível"));
  render(<App />);
  await resumeLastEpisode();
  expect(screen.getByRole("alert").textContent).toContain("API indisponível");
  expect(localStorage.getItem("cortex-active-run")).toBe("project:run");
});

test("the export screen plans, renders a draft and shows the real preview player", async () => {
  await openExport();
  expect(api.startEditPlan).toHaveBeenCalledWith("project", expect.objectContaining({ tighten: true, headline: "Headline real", profile: "balanced", start: 0, end: 40 }));
  expect(api.startRender).toHaveBeenCalledWith("project", "edl", expect.anything(), undefined, null, { purpose: "draft" });
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  expect(api.overlayPreview).toHaveBeenCalled();
  expect(screen.getByLabelText("Prévia do corte").getAttribute("src")).toContain("/renders/draft/media");
  expect(screen.getByRole("tablist")).toBeTruthy();
});

test("starting another episode clears previous selection, settings and downloads", async () => {
  vi.mocked(api.createExportBatch).mockResolvedValue(succeededBatch);
  await openExport();
  tab("Estilo");
  fireEvent.change(screen.getByRole("slider", { name: /Tamanho/ }), { target: { value: "72" } });
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(screen.getByRole("link", { name: "Baixar MP4" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Novo episódio/ }));
  fireEvent.click(screen.getByRole("button", { name: "Arquivo" }));
  const input = document.querySelector('input[type="file"]')!;
  fireEvent.change(input, { target: { files: [new File(["video"], "next.mp4", { type: "video/mp4" })] } });
  vi.mocked(api.createProject).mockResolvedValue({ id: "next-project" } as Awaited<ReturnType<typeof api.createProject>>);
  vi.mocked(api.uploadSource).mockResolvedValue({ id: "next-source" } as Awaited<ReturnType<typeof api.uploadSource>>);
  const pending = deferred<WorkflowRun>();
  vi.mocked(api.createWorkflowRun).mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole("button", { name: "Processar episódio" }));
  await settle();
  expect(localStorage.getItem("cortex-active-run")).toBeNull();
  await act(async () => { pending.resolve({ ...ready, id: "next-run", project_id: "next-project", source_asset_id: "next-source" }); });
  fireEvent.click(screen.getByRole("button", { name: /Editar 1 selecionado/ }));
  await settle();
  tab("Estilo");
  expect(screen.queryByRole("link", { name: "Baixar MP4" })).toBeNull();
  expect((screen.getByRole("slider", { name: /Tamanho/ }) as HTMLInputElement).value).toBe("56");
});

test("only reviewed selections reach the saved revision, with style, headline and emphasis", async () => {
  vi.mocked(api.suggestion).mockResolvedValueOnce(twoClips);
  vi.mocked(api.createExportBatch).mockResolvedValue(succeededBatch);
  localStorage.setItem("cortex-active-run", "project:run");
  vi.mocked(api.workflowRun).mockResolvedValue(ready);
  render(<App />);
  await resumeLastEpisode();
  fireEvent.click(screen.getByText("Uma ideia"));
  fireEvent.click(screen.getByRole("button", { name: /Editar 1 selecionado/ }));
  await settle();
  tab("Estilo");
  fireEvent.change(screen.getByRole("slider", { name: /Tamanho/ }), { target: { value: "64" } });
  fireEvent.change(document.querySelector('input[type="color"]')!, { target: { value: "#ffcc00" } });
  tab("Manchete");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Minha manchete" } });
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  const body = vi.mocked(api.saveRevision).mock.lastCall![2];
  expect(body.selected_clips.map(clip => clip.title)).toEqual(["Outra ideia"]);
  expect(body.render_settings.captions).toMatchObject({ font_size: 64, text_color: "#FFCC00", style: "pop" });
  expect(body.headlines).toEqual({ "2:60:100:balanced": "Minha manchete" });
  expect(body.emphasis).toEqual({});
  expect(api.startEditPlan).toHaveBeenLastCalledWith("project", expect.objectContaining({ start: 60, end: 100 }));
});

test("the encoder always comes from the server, even for a restored revision", async () => {
  vi.mocked(api.latestRevision).mockResolvedValue(revision({ render_settings: { ...defaultRenderSettings, encoder: "h264_nvenc" } }));
  vi.mocked(api.createExportBatch).mockResolvedValue(succeededBatch);
  await openExport();
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(vi.mocked(api.saveRevision).mock.lastCall![2].render_settings.encoder).toBe("libx264");
});

test("a failing render-defaults request keeps the settings and shows a non-blocking error", async () => {
  vi.mocked(api.renderDefaults).mockRejectedValue(new Error("sem defaults"));
  await openExport();
  expect(screen.getByRole("alert").textContent).toContain("sem defaults");
  expect(screen.getByRole("button", { name: "Renderizar 1 corte" })).toBeTruthy();
});

test("a legacy framing mode is replaced by the blurred frame with a notice", async () => {
  vi.mocked(api.latestRevision).mockResolvedValue(revision({
    render_settings: { ...defaultRenderSettings, framing: { schema_version: 1, mode: "speaker_face", punch_in: { enabled: false, scale: 1.15, anchor: "center", alternate_on_jump_cuts: true } } } as never,
  }));
  await openExport();
  expect(screen.getByText(/enquadramento antigo/)).toBeTruthy();
  tab("Enquadramento");
  expect(screen.getByRole("button", { name: /Quadro com fundo desfocado/ }).getAttribute("aria-pressed")).toBe("true");
});

test("starting another episode preserves a resumable draft without deleting artifacts", async () => {
  await openExport();
  tab("Estilo");
  fireEvent.change(screen.getByRole("slider", { name: /Tamanho/ }), { target: { value: "64" } });
  fireEvent.click(screen.getByRole("button", { name: /1Novo episódio/ }));
  expect(localStorage.getItem("cortex-active-run")).toBeNull();
  expect(screen.getByPlaceholderText("https://youtube.com/watch?v=...")).toBeTruthy();
  expect(localStorage.getItem("cortex-draft:project:run")).toContain('"font_size":64');
  fireEvent.click(screen.getByRole("button", { name: "Uma ideia" }));
  await settle();
  fireEvent.click(screen.getByRole("button", { name: /Editar 1 selecionado/ }));
  await settle();
  tab("Estilo");
  expect((screen.getByRole("slider", { name: /Tamanho/ }) as HTMLInputElement).value).toBe("64");
  expect(localStorage.getItem("cortex-active-run")).toBe("project:run");
});

test("final export submits one durable server batch", async () => {
  vi.mocked(api.createExportBatch).mockResolvedValue(succeededBatch);
  await openExport();
  vi.mocked(api.startEditPlan).mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(api.createExportBatch).toHaveBeenCalledTimes(1);
  expect(api.startEditPlan).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "Baixar MP4" })).toBeTruthy();
});

test("autosave restores settings, headline and completed exports after a reload", async () => {
  vi.mocked(api.createExportBatch).mockResolvedValue(succeededBatch);
  const view = await openExport();
  tab("Estilo");
  fireEvent.change(screen.getByRole("slider", { name: /Tamanho/ }), { target: { value: "72" } });
  fireEvent.change(screen.getByRole("slider", { name: /Posição vertical/ }), { target: { value: "65" } });
  tab("Manchete");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Minha manchete" } });
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(JSON.parse(localStorage.getItem("cortex-draft:project:run")!)).toMatchObject({
    schema_version: 1, settings: { captions: { font_size: 72, position_y: .65 } },
    headlines: { [KEY]: "Minha manchete" }, selectedKeys: [KEY],
  });
  view.unmount();
  render(<App />);
  await resumeLastEpisode();
  fireEvent.click(screen.getByRole("button", { name: /Editar 1 selecionado/ }));
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
  await openExport(ready, 2);
  tab("Manchete");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Manchete do primeiro" } });
  fireEvent.click(screen.getByRole("button", { name: /Outra ideia/ }));
  await settle();
  expect((screen.getByRole("textbox", { name: /Texto da manchete/ }) as HTMLTextAreaElement).value).toBe("");
  fireEvent.change(screen.getByRole("textbox", { name: /Texto da manchete/ }), { target: { value: "Manchete do segundo" } });
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 2 cortes" }));
  await settle();
  expect(vi.mocked(api.saveRevision).mock.lastCall![2].headlines).toEqual({ [KEY]: "Manchete do primeiro", "2:60:100:balanced": "Manchete do segundo" });
  fireEvent.click(screen.getByRole("button", { name: "Usar a sugestão da IA" }));
  expect(JSON.parse(localStorage.getItem("cortex-draft:project:run")!).headlines).toEqual({ [KEY]: "Manchete do primeiro" });
});

test.each(['{invalid', JSON.stringify({ schema_version: 1, settings: { captions: null }, captionEdits: [], headlines: 42 }), JSON.stringify({ schema_version: 99 })])("an invalid local draft restores safe defaults: %s", async (draft) => {
  localStorage.setItem("cortex-draft:project:run", draft);
  await openExport();
  tab("Estilo");
  expect((screen.getByRole("slider", { name: /Tamanho/ }) as HTMLInputElement).value).toBe("56");
  tab("Manchete");
  expect((screen.getByRole("textbox", { name: /Texto da manchete/ }) as HTMLTextAreaElement).value).toBe("");
});

test("library is separate from the three-stage workflow and new episode is the entry screen", async () => {
  render(<App />); await settle();
  expect(screen.getByRole("heading", { name: "Do episódio aos cortes, em um clique." })).toBeTruthy();
  const navigation = screen.getByRole("navigation", { name: "Jornada do episódio" });
  expect(navigation.querySelectorAll("button")).toHaveLength(3);
  expect(navigation.textContent).toBe("1Novo episódio2Escolher cortes3Exportar");
  fireEvent.click(screen.getByRole("button", { name: "Biblioteca" })); await settle();
  expect(screen.getByRole("heading", { name: "Seus episódios. Seus cortes." })).toBeTruthy();
});

test("episode terms become hotwords for the new run", async () => {
  vi.mocked(api.registerEpisode).mockResolvedValue({ id: "ep", archived: false, metadata_updated_at: "x", project_ids: ["p"] } as Awaited<ReturnType<typeof api.registerEpisode>>);
  vi.mocked(api.downloadEpisode).mockResolvedValue({ source: { id: "src", project_id: "p" }, job: null, cached: true } as Awaited<ReturnType<typeof api.downloadEpisode>>);
  vi.mocked(api.createWorkflowRun).mockResolvedValue({ ...ready, status: "failed", error: "parar aqui" });
  render(<App />); await settle();
  fireEvent.change(screen.getByPlaceholderText("https://youtube.com/watch?v=..."), { target: { value: "https://youtube.com/watch?v=abc" } });
  fireEvent.change(screen.getByRole("textbox", { name: /Nomes e termos do episódio/ }), { target: { value: "Ana Souza, Ana Souza,  Quântica " } });
  fireEvent.click(screen.getByRole("button", { name: "Processar episódio" }));
  await settle();
  expect(vi.mocked(api.createWorkflowRun).mock.lastCall![2]).toMatchObject({ hotwords: ["Ana Souza", "Quântica"] });
});

test("the review screen shows duration, assembled pieces, the cleaned start and other headlines", async () => {
  const rich = structuredClone(suggestion);
  Object.assign(rich.document.selection.clips[0], {
    pieces: [{ from_id: 1, to_id: 2, start: 0, end: 10, text: "a" }, { from_id: 3, to_id: 4, start: 20, end: 40, text: "b" }],
    lead_trimmed: "Então,",
  });
  vi.mocked(api.suggestion).mockResolvedValue(rich);
  localStorage.setItem("cortex-active-run", "project:run");
  vi.mocked(api.workflowRun).mockResolvedValue(ready);
  render(<App />);
  await resumeLastEpisode();
  expect(screen.getByText("montado de 2 trechos")).toBeTruthy();
  expect(screen.getByText(/Começo limpo: “Então,” removido/)).toBeTruthy();
  expect(screen.getByText(/Outras manchetes: Outra opção/)).toBeTruthy();
  expect(document.querySelector(".clip-rank")?.textContent).toContain("0:40");
});

test("caption corrections and emphasis edited in the Legenda tab reach the saved revision and draft", async () => {
  vi.mocked(api.overlayPreview).mockResolvedValue({
    ass_text: "x", generator_version: "1", content_hash: "h", timeline_duration_seconds: 38, canvas: { width: 1080, height: 1920 },
    pages: [{ start: 0, end: 1, lines: [[{ index: 4, text: "CORTECS", start: 0, end: 1, emphasis: false, original: "Cortecs", probability: 0.9 }]] }],
    junctions: [], fonts: [],
  });
  vi.mocked(api.createExportBatch).mockResolvedValue(succeededBatch);
  await openExport();
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  fireEvent.click(screen.getByRole("button", { name: "CORTECS" }));
  fireEvent.click(screen.getByRole("button", { name: "★ Destacar" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Texto da palavra" }), { target: { value: "CorteX" } });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Texto da palavra" }), { key: "Enter" });
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  const body = vi.mocked(api.saveRevision).mock.lastCall![2];
  expect(body.caption_edits).toEqual({ [KEY]: [{ word_index: 4, original: "Cortecs", text: "CorteX" }] });
  expect(body.emphasis).toEqual({ [KEY]: [3, 4] });
  expect(JSON.parse(localStorage.getItem("cortex-draft:project:run")!).emphasisEdits).toEqual({ [KEY]: [3, 4] });
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  expect(vi.mocked(api.overlayPreview).mock.lastCall![2].captions).toMatchObject({
    emphasis_word_indices: [3, 4], corrections: [{ word_index: 4, original: "Cortecs", text: "CorteX" }],
  });
});
