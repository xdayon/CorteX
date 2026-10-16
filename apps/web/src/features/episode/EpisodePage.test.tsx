import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type ApiJob, type EpisodeEntry, type WorkflowRun } from "../../api";
import { resetTasks, upsertTask } from "../../app/tasks";
import { clipRow, episodeClips, withExport } from "../../test/clipFixtures";
import { ToastProvider } from "../../ui";
import { EpisodePage } from "./EpisodePage";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [
    name, vi.fn(name.endsWith("Url") ? fn : undefined),
  ])) };
});

const source = { id: "src", project_id: "project", kind: "youtube", stored_path: "p", sha256: "x", size_bytes: 1 } as const;
const run = (extra: Partial<WorkflowRun> = {}): WorkflowRun => ({
  id: "run", project_id: "project", source_asset_id: "src", status: "ready_for_review", stage: "review", progress: 100,
  message: "Pronto", brief: { count: 7, minimum_seconds: 50, maximum_seconds: 90 }, artifacts: { suggestion: "sug" }, ...extra,
});
const entry = (runs: WorkflowRun[], extra: Partial<EpisodeEntry> = {}): EpisodeEntry => ({
  id: "ep", url: "https://youtu.be/x", title: "Conversa sobre consciência", project_ids: ["project"], channel_name: "Canal Horizonte",
  channel_url: null, thumbnail_url: null, metadata_updated_at: "x", metadata_error: null, participant_count: 2, primary_subject: "Dayon",
  archived: false, subject_reference: {}, diarizations: [], source, source_bytes: 1, runs, jobs: [], renders: [], ...extra,
});

const rows = [
  clipRow(1, { state: { decision: "approved", discard_reason: null, saved: false, saved_at: null, notes: "", updated_at: "x" }, status: "approved" }),
  clipRow(3, { score: 9.5 }, { reasoning: "Conta uma virada" }),
  withExport(clipRow(2), "exported"),
];
const KEY = (rank: number) => `run~${rank}:${rank * 100}:${rank * 100 + 60}:balanced`;

async function settle() { await act(async () => {}); }
function page() { return render(<ToastProvider><EpisodePage id="ep" /></ToastProvider>); }
const titles = () => [...document.querySelectorAll(".rt")].map(node => node.textContent);

beforeEach(() => {
  vi.resetAllMocks();
  resetTasks();
  window.history.replaceState(null, "", "/episodios/ep");
  localStorage.clear();
  vi.mocked(api.episodes).mockResolvedValue([entry([run()])]);
  vi.mocked(api.episodeClips).mockResolvedValue(episodeClips(rows));
  vi.mocked(api.publications).mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("an active run shows the stages, progress and a cancel button", async () => {
  vi.mocked(api.episodes).mockResolvedValue([entry([run({ status: "running", stage: "analyze", progress: 63, active_job_id: "job1", message: "Lendo o bloco 3" })])]);
  vi.mocked(api.job).mockResolvedValue({ id: "job1", type: "analysis", status: "running", progress: 40, message: "Lendo o bloco 3", updated_at: new Date().toISOString() } as ApiJob);
  vi.mocked(api.cancelJob).mockResolvedValue({ id: "job1", status: "cancelled" } as ApiJob);
  page(); await settle();
  const panel = screen.getByRole("region", { name: "Processamento do episódio" });
  expect(within(panel).getByText("Analisando o episódio")).toBeTruthy();
  expect(within(panel).getByText("63%")).toBeTruthy();
  expect(within(panel).getByText("Analisar").closest("li")!.getAttribute("aria-current")).toBe("step");
  fireEvent.click(within(panel).getByText("Detalhes técnicos"));
  expect(within(panel).getByText("job1")).toBeTruthy();
  fireEvent.click(within(panel).getByRole("button", { name: "Cancelar" }));
  await settle();
  expect(api.cancelJob).toHaveBeenCalledWith("job1");
  expect(screen.getByRole("region", { name: "Cortes do episódio" })).toBeTruthy();
});

test("a background task shows the download before any run exists", async () => {
  vi.mocked(api.episodes).mockResolvedValue([entry([])]);
  vi.mocked(api.episodeClips).mockResolvedValue(episodeClips([]));
  upsertTask({ id: "t1", episodeId: "ep", status: "running", progress: 42, message: "Baixando o vídeo 42%" });
  page(); await settle();
  const panel = screen.getByRole("region", { name: "Processamento do episódio" });
  expect(within(panel).getByText("Baixando o vídeo 42%")).toBeTruthy();
  expect(within(panel).getByRole("progressbar").getAttribute("aria-valuenow")).toBe("42");
  expect(screen.queryByText("Gerar seleção de cortes")).toBeNull();
  await act(async () => { upsertTask({ id: "t1", episodeId: "ep", status: "failed", progress: 42, message: "x", error: "Sem espaço no disco" }); });
  expect(screen.getByRole("alert").textContent).toContain("Sem espaço no disco");
});

test("the list sorts by score, shows counts and opens the first clip in the detail pane", async () => {
  page(); await settle();
  expect(titles()).toEqual(["Manchete 3", "Manchete 2", "Manchete 1"]);
  expect(screen.getByRole("heading", { level: 2, name: "Manchete 3" })).toBeTruthy();
  expect(screen.getByRole("radio", { name: /Todos/ }).textContent).toContain("3");
  expect(screen.getByRole("radio", { name: /Exportados/ }).textContent).toContain("1");
  expect(screen.getByRole("radio", { name: /Salvos/ }).textContent).toContain("0");
  expect(screen.getByText("Conta uma virada")).toBeTruthy();
  fireEvent.click(screen.getByRole("radio", { name: "Ordem no episódio" }));
  expect(titles()).toEqual(["Manchete 1", "Manchete 2", "Manchete 3"]);
  expect(window.location.search).toContain("sort=order");
});

test("filters live in the URL: status, run, search over the transcript, and a deep link to a clip", async () => {
  window.history.replaceState(null, "", `/episodios/ep?status=exported`);
  page(); await settle();
  expect(titles()).toEqual(["Manchete 2"]);
  fireEvent.click(screen.getByRole("radio", { name: /Todos/ }));
  expect(window.location.search).not.toContain("status");
  expect(titles()).toHaveLength(3);
  fireEvent.change(screen.getByLabelText("Buscar corte"), { target: { value: "segundo 1" } });
  expect(titles()).toEqual(["Manchete 1"]);
  expect(window.location.search).toContain("q=segundo+1");
  fireEvent.change(screen.getByLabelText("Buscar corte"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("Seleção"), { target: { value: "run" } });
  expect(window.location.search).toContain("run=run");
  fireEvent.click(screen.getByRole("button", { name: /Manchete 1/ }));
  expect(new URLSearchParams(window.location.search).get("clip")).toBe(KEY(1));
  expect(screen.getByRole("heading", { level: 2, name: "Manchete 1" })).toBeTruthy();
  expect(screen.getByText("Aprovado", { selector: ".cd-chips .chip" })).toBeTruthy();
});

test("a deep link opens that clip; an exported clip plays the export with a poster and can switch to the original", async () => {
  window.history.replaceState(null, "", `/episodios/ep?clip=${encodeURIComponent(KEY(2))}`);
  page(); await settle();
  const video = screen.getByLabelText("Vídeo exportado") as HTMLVideoElement;
  expect(video.getAttribute("poster")).toBe("/api/v1/projects/project/renders/r1/poster");
  expect(video.getAttribute("src")).toContain("/renders/r1/media");
  expect(screen.getByRole("link", { name: "Baixar MP4" })).toBeTruthy();
  fireEvent.click(screen.getByRole("radio", { name: "Original" }));
  expect((screen.getByLabelText("Prévia do corte original") as HTMLVideoElement).getAttribute("src")).toBe("/api/v1/projects/project/sources/src/media");
  fireEvent.click(screen.getByRole("button", { name: /Manchete 1/ }));
  expect(screen.queryByRole("radiogroup", { name: "Versão do vídeo" })).toBeNull();
  expect(screen.getByLabelText("Prévia do corte original")).toBeTruthy();
});

test("generating a new selection prefills the options from the last run and reuses the stored source", async () => {
  vi.mocked(api.createWorkflowRun).mockResolvedValue(run({ id: "run2", status: "queued" }));
  page(); await settle();
  fireEvent.click(screen.getByRole("button", { name: "Gerar nova seleção" }));
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("7")).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "Gerar seleção" }));
  await settle();
  expect(api.createWorkflowRun).toHaveBeenCalledWith("project", "src", expect.objectContaining({ count: 7, minimum_seconds: 50, maximum_seconds: 90, primary_subject: "Dayon" }));
});

test("a failed run explains itself and an unknown episode says so", async () => {
  vi.mocked(api.episodes).mockResolvedValue([entry([run({ status: "failed", error: "Claude indisponível" })])]);
  page(); await settle();
  expect(screen.getAllByRole("alert").map(node => node.textContent).join(" ")).toContain("Claude indisponível");
  cleanup();
  vi.mocked(api.episodes).mockResolvedValue([]);
  page(); await settle();
  expect(screen.getByText("Episódio não encontrado")).toBeTruthy();
});
