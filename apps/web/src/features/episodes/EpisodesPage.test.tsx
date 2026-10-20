import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, REMOTE_UPLOAD_MESSAGE, type ApiJob, type EpisodeEntry, type WorkflowRun } from "../../api";
import { getTasks, resetTasks } from "../../app/tasks";
import { ToastProvider } from "../../ui";
import { EpisodesPage } from "./EpisodesPage";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, isRemoteOrigin: () => true, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [
    name, vi.fn(name.endsWith("Url") ? fn : undefined),
  ])) };
});

const episode: EpisodeEntry = {
  id: "AbCdEfGh_12", url: "https://www.youtube.com/watch?v=AbCdEfGh_12",
  title: "Consciência e sociedade com Dayon", project_ids: ["project-one"],
  channel_name: "Podcast Horizonte", channel_url: null,
  thumbnail_url: "https://i.ytimg.com/vi/AbCdEfGh_12/hqdefault.jpg",
  metadata_updated_at: "2026-09-11T10:00:00+00:00", metadata_error: null,
  participant_count: 3, primary_subject: "Dayon", archived: false,
  subject_reference: {}, diarizations: [], source: null, source_bytes: 0,
  runs: [], jobs: [], renders: [],
};
const source = { id: "src", project_id: "project-one", kind: "youtube", stored_path: "p", sha256: "x", size_bytes: 1 } as const;
const run = (extra: Partial<WorkflowRun>): WorkflowRun => ({
  id: "run", project_id: "project-one", source_asset_id: "src", status: "ready_for_review", stage: "review", progress: 100,
  message: "Pronto", brief: { count: 10, minimum_seconds: 40, maximum_seconds: 120 }, artifacts: { suggestion: "s" }, ...extra,
});
const withCuts: EpisodeEntry = { ...episode, id: "cuts", title: "Episódio com cortes", source, runs: [run({})], renders: [{ id: "r", project_id: "project-one", title: "Corte", duration: 50 }] };
const archived: EpisodeEntry = { ...episode, id: "old", title: "Episódio antigo", archived: true };

async function settle() { await act(async () => {}); }
function page() { return render(<ToastProvider><EpisodesPage /></ToastProvider>); }
const card = (title: string) => [...document.querySelectorAll(".ecard-title")].find(node => node.textContent === title)!.closest("article") as HTMLElement;

beforeEach(() => {
  vi.resetAllMocks();
  resetTasks();
  window.history.replaceState(null, "", "/");
  localStorage.clear();
  vi.mocked(api.episodes).mockResolvedValue([episode]);
  vi.mocked(api.jobs).mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("the YouTube flow registers, downloads, creates the run and opens the episode", async () => {
  vi.mocked(api.registerEpisode).mockResolvedValue({ ...episode, id: "yt1", project_ids: ["p1"] });
  vi.mocked(api.downloadEpisode).mockResolvedValue({ source: { ...source, id: "s1", project_id: "p1" }, job: null, cached: true });
  vi.mocked(api.createWorkflowRun).mockResolvedValue(run({ project_id: "p1" }));
  page(); await settle();
  expect((screen.getByRole("button", { name: "Processar episódio" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText(/Link do YouTube/), { target: { value: "https://youtube.com/watch?v=abc" } });
  fireEvent.click(screen.getByRole("button", { name: "Processar episódio" }));
  await settle();
  expect(api.registerEpisode).toHaveBeenCalledWith("https://youtube.com/watch?v=abc");
  expect(api.downloadEpisode).toHaveBeenCalledWith("yt1");
  expect(api.createWorkflowRun).toHaveBeenCalledWith("p1", "s1", expect.objectContaining({ count: 10, minimum_seconds: 40, maximum_seconds: 120, primary_subject: "Dayon", new_clips_only: true }));
  expect(window.location.pathname).toBe("/episodios/yt1");
});

test("a link that is not a URL is rejected before anything is registered", async () => {
  page(); await settle();
  fireEvent.change(screen.getByLabelText(/Link do YouTube/), { target: { value: "texto solto" } });
  fireEvent.click(screen.getByRole("button", { name: "Processar episódio" }));
  await settle();
  expect(screen.getByRole("alert").textContent).toContain("link válido");
  expect(api.registerEpisode).not.toHaveBeenCalled();
});

test("options set in the dialog reach the run, including episode terms as hotwords", async () => {
  vi.mocked(api.registerEpisode).mockResolvedValue({ ...episode, id: "yt1", project_ids: ["p1"] });
  vi.mocked(api.downloadEpisode).mockResolvedValue({ source: { ...source, project_id: "p1" }, job: null, cached: true });
  vi.mocked(api.createWorkflowRun).mockResolvedValue(run({}));
  page(); await settle();
  fireEvent.click(screen.getByRole("button", { name: /Nomes e termos/ }));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByRole("textbox", { name: /Nomes e termos do episódio/ }), { target: { value: "Ana Souza, Ana Souza,  Quântica " } });
  fireEvent.change(within(dialog).getByRole("textbox", { name: /Direção editorial/ }), { target: { value: "Só histórias pessoais" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Concluir" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(localStorage.getItem("cortex-editorial-instructions")).toBe("Só histórias pessoais");
  fireEvent.change(screen.getByLabelText(/Link do YouTube/), { target: { value: "https://youtube.com/watch?v=abc" } });
  fireEvent.click(screen.getByRole("button", { name: "Processar episódio" }));
  await settle();
  expect(vi.mocked(api.createWorkflowRun).mock.lastCall![2]).toMatchObject({ hotwords: ["Ana Souza", "Quântica"], instructions: "Só histórias pessoais" });
});

test("a large local file is refused with the remote limit message before any upload", async () => {
  page(); await settle();
  const big = new File(["x"], "episodio.mp4", { type: "video/mp4" });
  Object.defineProperty(big, "size", { value: 100 * 1024 * 1024 });
  fireEvent.change(screen.getByTestId("episode-file"), { target: { files: [big] } });
  expect(screen.getByText("episodio.mp4")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Processar episódio" }));
  await settle();
  expect(screen.getByRole("alert").textContent).toBe(REMOTE_UPLOAD_MESSAGE);
  expect(api.createProject).not.toHaveBeenCalled();
  expect(api.uploadSource).not.toHaveBeenCalled();
});

test("a local file is uploaded, reconciled in the catalogue and opened as a local episode", async () => {
  vi.mocked(api.createProject).mockResolvedValue({ id: "proj9", name: "aula" });
  vi.mocked(api.uploadSource).mockResolvedValue({ ...source, id: "up1", project_id: "proj9" });
  vi.mocked(api.createWorkflowRun).mockResolvedValue(run({ project_id: "proj9" }));
  page(); await settle();
  fireEvent.change(screen.getByTestId("episode-file"), { target: { files: [new File(["v"], "aula.mp4", { type: "video/mp4" })] } });
  vi.mocked(api.episodes).mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Processar episódio" }));
  await settle();
  expect(api.createProject).toHaveBeenCalledWith("aula");
  expect(api.createWorkflowRun).toHaveBeenCalledWith("proj9", "up1", expect.anything());
  expect(api.episodes).toHaveBeenCalled();
  expect(window.location.pathname).toBe("/episodios/local-proj9");
});

test("cards show title, channel, status and stats; filters narrow the list and archived reloads with archived items", async () => {
  vi.mocked(api.episodes).mockImplementation(async (includeArchived) => includeArchived ? [archived] : [episode, withCuts]);
  page(); await settle();
  expect(screen.getAllByText("Podcast Horizonte")).toHaveLength(2);
  expect(within(card("Episódio com cortes")).getByText("Pronto")).toBeTruthy();
  expect(within(card("Episódio com cortes")).getByText("1 vídeo · 1 seleção")).toBeTruthy();
  expect(within(card("Consciência e sociedade com Dayon")).getByText("Não baixado")).toBeTruthy();
  fireEvent.click(screen.getByRole("radio", { name: /Com cortes/ }));
  expect(screen.queryByText("Consciência e sociedade com Dayon")).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: /Não baixados/ }));
  expect(screen.queryByText("Episódio com cortes")).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: "Arquivados" }));
  await settle();
  expect(api.episodes).toHaveBeenLastCalledWith(true);
  expect(screen.getByText("Episódio antigo")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Buscar episódio"), { target: { value: "inexistente" } });
  expect(screen.getByText("Nenhum episódio encontrado")).toBeTruthy();
});

test("the card menu archives, refreshes metadata and downloads, then reloads the catalogue", async () => {
  vi.mocked(api.archiveEpisode).mockResolvedValue(episode);
  vi.mocked(api.refreshEpisodeMetadata).mockResolvedValue(episode);
  vi.mocked(api.downloadEpisode).mockResolvedValue({ source: null, job: null, cached: false });
  page(); await settle();
  const open = () => fireEvent.click(screen.getByRole("button", { name: `Mais ações: ${episode.title}` }));
  open(); fireEvent.click(screen.getByRole("menuitem", { name: "Atualizar dados do YouTube" })); await settle();
  expect(api.refreshEpisodeMetadata).toHaveBeenCalledWith("AbCdEfGh_12");
  open(); fireEvent.click(screen.getByRole("menuitem", { name: "Baixar e guardar vídeo" })); await settle();
  expect(api.downloadEpisode).toHaveBeenCalledWith("AbCdEfGh_12");
  vi.mocked(api.episodes).mockClear();
  open(); fireEvent.click(screen.getByRole("menuitem", { name: "Arquivar" })); await settle();
  expect(api.archiveEpisode).toHaveBeenCalledWith("AbCdEfGh_12", true);
  expect(api.episodes).toHaveBeenCalled();
});

test("the menu closes on Escape and opens the voice dialog for a downloaded episode", async () => {
  vi.mocked(api.episodes).mockResolvedValue([withCuts]);
  vi.mocked(api.diarizationStatus).mockResolvedValue({ ready: true, runtime_installed: true, token_configured: true, device: "cpu", missing: [], model_url: "" });
  page(); await settle();
  const trigger = screen.getByRole("button", { name: "Mais ações: Episódio com cortes" });
  fireEvent.click(trigger);
  expect(screen.getByRole("menu")).toBeTruthy();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("menu")).toBeNull();
  fireEvent.click(trigger);
  expect(screen.queryByRole("menuitem", { name: "Baixar e guardar vídeo" })).toBeNull();
  fireEvent.click(screen.getByRole("menuitem", { name: "Separar vozes (lento, ~1× a duração)" })); await settle();
  expect(within(screen.getByRole("dialog")).getByText(/Separe as vozes em CPU/)).toBeTruthy();
  expect(api.diarizationStatus).toHaveBeenCalled();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("an active run shows its stage and progress, and a failed run offers a retry", async () => {
  const processing: EpisodeEntry = { ...withCuts, id: "busy", title: "Em andamento", runs: [run({ status: "running", stage: "transcribe", progress: 42 })] };
  const failed: EpisodeEntry = { ...withCuts, id: "bad", title: "Com falha", renders: [], runs: [run({ id: "r2", status: "failed", error: "Claude indisponível\nTraceback interno" })] };
  vi.mocked(api.episodes).mockResolvedValue([processing, failed]);
  vi.mocked(api.createWorkflowRun).mockResolvedValue(run({ id: "r3" }));
  page(); await settle();
  const busy = card("Em andamento");
  expect(within(busy).getByText("Processando 42%")).toBeTruthy();
  expect(within(busy).getByText("Transcrevendo a conversa")).toBeTruthy();
  expect(within(busy).getByRole("progressbar").getAttribute("aria-valuenow")).toBe("42");
  expect(within(busy).queryByRole("button", { name: "Preparar cortes" })).toBeNull();
  const broken = card("Com falha");
  expect(within(broken).getByText("Precisa de atenção")).toBeTruthy();
  expect(within(broken).getByText("Claude indisponível")).toBeTruthy();
  fireEvent.click(within(broken).getByRole("button", { name: "Tentar de novo" }));
  await settle();
  expect(api.createWorkflowRun).toHaveBeenCalledWith("project-one", "src", expect.objectContaining({ count: 10 }));
  expect(window.location.pathname).toBe("/episodios/bad");
});

test("the episode page opens right after registering and the download continues as an app-level task", async () => {
  let finish!: (job: ApiJob) => void;
  let report!: (job: ApiJob) => void;
  vi.mocked(api.registerEpisode).mockResolvedValue({ ...episode, id: "yt2", project_ids: ["p2"] });
  vi.mocked(api.downloadEpisode).mockResolvedValue({ source: null, job: { id: "dl", status: "queued" } as ApiJob, cached: false });
  vi.mocked(api.watchJob).mockImplementation((_id, onUpdate) => { report = onUpdate!; return new Promise<ApiJob>(resolve => { finish = resolve; }); });
  vi.mocked(api.createWorkflowRun).mockResolvedValue(run({ project_id: "p2" }));
  const view = page(); await settle();
  fireEvent.change(screen.getByLabelText(/Link do YouTube/), { target: { value: "https://youtube.com/watch?v=zzz" } });
  fireEvent.click(screen.getByRole("button", { name: "Processar episódio" }));
  await settle();
  expect(window.location.pathname).toBe("/episodios/yt2");
  expect(api.createWorkflowRun).not.toHaveBeenCalled();
  expect(getTasks()).toMatchObject([{ episodeId: "yt2", status: "running", message: "Baixando o vídeo…" }]);
  view.unmount();
  await act(async () => report({ id: "dl", status: "running", progress: 42.4 } as ApiJob));
  expect(getTasks()[0]).toMatchObject({ message: "Baixando o vídeo 42%", progress: 42.4 });
  await act(async () => finish({ id: "dl", status: "succeeded", result: { source_asset_id: "s2" } } as unknown as ApiJob));
  expect(api.createWorkflowRun).toHaveBeenCalledWith("p2", "s2", expect.objectContaining({ count: 10 }));
  expect(getTasks()[0].status).toBe("done");
});

test("a download that fails ends the task with the error instead of throwing in the composer", async () => {
  vi.mocked(api.registerEpisode).mockResolvedValue({ ...episode, id: "yt3", project_ids: ["p3"] });
  vi.mocked(api.downloadEpisode).mockRejectedValue(new Error("vídeo indisponível"));
  page(); await settle();
  fireEvent.change(screen.getByLabelText(/Link do YouTube/), { target: { value: "https://youtube.com/watch?v=yyy" } });
  fireEvent.click(screen.getByRole("button", { name: "Processar episódio" }));
  await settle();
  expect(getTasks()[0]).toMatchObject({ status: "failed", error: "vídeo indisponível" });
});

test("card stats and the header total come from the clips summary", async () => {
  vi.mocked(api.episodes).mockResolvedValue([withCuts]);
  vi.mocked(api.clipsSummary).mockResolvedValue({ episodes: { cuts: { new: 20, approved: 3, exported: 6, saved: 0, posted: 2, discarded: 2, total: 33 } } });
  page(); await settle();
  expect(within(card("Episódio com cortes")).getByText("33 cortes · 8 exportados · 2 postados")).toBeTruthy();
  expect(screen.getByText("1 episódio · 8 vídeos gerados")).toBeTruthy();
});
