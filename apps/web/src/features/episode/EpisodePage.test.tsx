import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type ApiJob, type EpisodeEntry, type WorkflowRun } from "../../api";
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
const clip = (rank: number, start: number, total: number, extra: object = {}) => ({
  rank, title: `Título ${rank}`, headline: `Manchete ${rank}`, start_second: start, end_second: start + 60, estimated_duration: 60,
  pacing: "balanced", primary_speaker: "Dayon", reasoning: "", scores: { total }, ...extra,
});
const suggestion = { document: { selection: {
  schema_version: "1.0", selection_notes: "A IA priorizou histórias pessoais.",
  clips: [clip(1, 300, 7), clip(2, 100, 9.5, { pieces: [{}, {}], lead_trimmed: "Então," }), clip(3, 700, 8)],
} } } as unknown as Awaited<ReturnType<typeof api.suggestion>>;

async function settle() { await act(async () => {}); }
function page() { return render(<ToastProvider><EpisodePage id="ep" /></ToastProvider>); }
const rowTitles = () => [...document.querySelectorAll(".rt")].map(node => node.textContent);

beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/episodios/ep");
  localStorage.clear();
  vi.mocked(api.suggestion).mockResolvedValue(suggestion);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("an active run shows the stages, progress and a cancel button instead of the clip list", async () => {
  vi.mocked(api.episodes).mockResolvedValue([entry([run({ status: "running", stage: "analyze", progress: 63, active_job_id: "job1", message: "Lendo o bloco 3" })])]);
  vi.mocked(api.job).mockResolvedValue({ id: "job1", type: "analysis", status: "running", progress: 40, message: "Lendo o bloco 3", updated_at: new Date().toISOString() } as ApiJob);
  vi.mocked(api.cancelJob).mockResolvedValue({ id: "job1", status: "cancelled" } as ApiJob);
  page(); await settle();
  const panel = screen.getByRole("region", { name: "Processamento do episódio" });
  expect(within(panel).getByText("Analisando o episódio")).toBeTruthy();
  expect(within(panel).getByText("63%")).toBeTruthy();
  expect(within(panel).getByText("Analisar").closest("li")!.getAttribute("aria-current")).toBe("step");
  expect(within(panel).getByRole("progressbar").getAttribute("aria-valuenow")).toBe("63");
  expect(screen.queryByRole("region", { name: "Cortes sugeridos" })).toBeNull();
  fireEvent.click(within(panel).getByText("Detalhes técnicos"));
  expect(within(panel).getByText("job1")).toBeTruthy();
  fireEvent.click(within(panel).getByRole("button", { name: "Cancelar" }));
  await settle();
  expect(api.cancelJob).toHaveBeenCalledWith("job1");
});

test("a ready run lists the clips by score, sorts by episode order and keeps notes collapsed", async () => {
  vi.mocked(api.episodes).mockResolvedValue([entry([run()])]);
  page(); await settle();
  expect(api.suggestion).toHaveBeenCalledWith("project", "sug");
  expect(rowTitles()).toEqual(["Manchete 2", "Manchete 3", "Manchete 1"]);
  expect(document.querySelector(".score.hi")!.textContent).toBe("9.5");
  expect(screen.getByText("· 2 trechos")).toBeTruthy();
  expect(screen.getByText("· começo limpo")).toBeTruthy();
  expect((screen.getByText(/A IA priorizou/).closest("details") as HTMLDetailsElement).open).toBe(false);
  fireEvent.click(screen.getByRole("radio", { name: "Ordem no episódio" }));
  expect(rowTitles()).toEqual(["Manchete 2", "Manchete 1", "Manchete 3"]);
});

test("selecting two clips shows the bar and opens the editor with the run and the clip keys", async () => {
  vi.mocked(api.episodes).mockResolvedValue([entry([run()])]);
  page(); await settle();
  expect(screen.queryByRole("region", { name: "Cortes selecionados" })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Selecionar Manchete 2" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Selecionar Manchete 1" }));
  const bar = screen.getByRole("region", { name: "Cortes selecionados" });
  expect(within(bar).getByText("2 cortes selecionados")).toBeTruthy();
  expect(within(bar).getByText("2:00 no total")).toBeTruthy();
  fireEvent.click(within(bar).getByRole("button", { name: "Limpar" }));
  expect(screen.queryByRole("region", { name: "Cortes selecionados" })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Selecionar Manchete 3" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Selecionar Manchete 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Editar e exportar →" }));
  expect(window.location.pathname).toBe("/episodios/ep/editor");
  const query = new URLSearchParams(window.location.search);
  expect(query.get("run")).toBe("run");
  expect(query.get("clips")).toBe("1:300:360:balanced,3:700:760:balanced");
});

test("generating a new selection prefills the options from the last run and reuses the stored source", async () => {
  vi.mocked(api.episodes).mockResolvedValue([entry([run()])]);
  vi.mocked(api.createWorkflowRun).mockResolvedValue(run({ id: "run2", status: "queued" }));
  page(); await settle();
  fireEvent.click(screen.getByRole("button", { name: "Gerar nova seleção" }));
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("7")).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "Gerar seleção" }));
  await settle();
  expect(api.createWorkflowRun).toHaveBeenCalledWith("project", "src", expect.objectContaining({ count: 7, minimum_seconds: 50, maximum_seconds: 90, primary_subject: "Dayon" }));
  expect(api.episodes).toHaveBeenCalledTimes(2);
});

test("a failed run explains itself and an unknown episode says so", async () => {
  vi.mocked(api.episodes).mockResolvedValue([entry([run({ status: "failed", error: "Claude indisponível" })])]);
  page(); await settle();
  expect(screen.getByRole("alert").textContent).toContain("Claude indisponível");
  cleanup();
  vi.mocked(api.episodes).mockResolvedValue([]);
  page(); await settle();
  expect(screen.getByText("Episódio não encontrado")).toBeTruthy();
});
