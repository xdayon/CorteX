import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type EpisodeCameras, type EpisodeEntry, type WorkflowRun } from "../../api";
import { ToastProvider } from "../../ui";
import { EpisodeSteps } from "./EpisodeSteps";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [name, vi.fn(name.endsWith("Url") ? fn : undefined)])) };
});

const readyRun = { id: "r", status: "ready_for_review", stage: "review", progress: 100 } as unknown as WorkflowRun;
const entry = (extra: Partial<EpisodeEntry> = {}) => ({ id: "ep", url: "https://youtu.be/x", title: "Ep", runs: [], jobs: [], source: null, ...extra }) as unknown as EpisodeEntry;
const source = { id: "src" } as never;
const cameras = (roles: Record<string, string> | null, job: unknown = null): EpisodeCameras => ({ artifact_id: "a", roles: roles as never, job: job as never, document: { artifact_id: "a", groups: [] } as never });

const handlers = { onDownload: vi.fn(), onGenerate: vi.fn(), onVoice: vi.fn(), onCameras: vi.fn() };
async function show(item: EpisodeEntry, processing = false) {
  render(<ToastProvider><EpisodeSteps entry={item} processing={processing} reloadKey="k" {...handlers} /></ToastProvider>);
  await act(async () => {});
}
const step = (title: string) => screen.getByText(title).closest("li")!;

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.voiceProfile).mockResolvedValue({ exists: false });
  vi.mocked(api.episodeCameras).mockResolvedValue(null);
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

test("a fresh episode asks for the video first, then the selection once downloaded", async () => {
  await show(entry());
  expect(step("Vídeo").textContent).toContain("Pendente");
  fireEvent.click(screen.getByRole("button", { name: "Baixar vídeo" }));
  expect(handlers.onDownload).toHaveBeenCalled();
  expect((screen.getByRole("button", { name: "Gerar seleção" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/Depois: escolha os cortes abaixo/)).toBeTruthy();
});

test("downloaded episode: generate, register the voice and map the cameras", async () => {
  vi.mocked(api.mapEpisodeCameras).mockResolvedValue({ id: "j" } as never);
  await show(entry({ source }));
  expect(step("Vídeo").textContent).toContain("Feito");
  fireEvent.click(screen.getByRole("button", { name: "Gerar seleção" }));
  expect(handlers.onGenerate).toHaveBeenCalled();
  expect(step("Voz do Dayon").textContent).toContain("Pendente");
  fireEvent.click(screen.getByRole("button", { name: "Cadastrar voz" }));
  expect(handlers.onVoice).toHaveBeenCalled();
  expect(step("Câmeras").textContent).toContain("Opcional");
  expect(step("Câmeras").textContent).toContain("Recomendado para cenas");
  fireEvent.click(screen.getByRole("button", { name: "Mapear câmeras" }));
  await act(async () => {});
  expect(api.mapEpisodeCameras).toHaveBeenCalledWith("ep");
});

test("processing shows 'Em andamento' on the selection step and hides its action", async () => {
  await show(entry({ source }), true);
  expect(step("Cortes sugeridos").textContent).toContain("Em andamento");
  expect(screen.queryByRole("button", { name: "Gerar seleção" })).toBeNull();
});

test("a mapped camera set without roles asks to confirm; a running job polls every 3 s", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout"] });
  vi.mocked(api.episodeCameras).mockResolvedValueOnce(cameras(null, { id: "j", status: "running" })).mockResolvedValue(cameras(null));
  await show(entry({ source }));
  expect(step("Câmeras").textContent).toContain("Em andamento");
  expect(screen.queryByRole("button", { name: "Mapear câmeras" })).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(api.episodeCameras).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Confirmar câmeras" }));
  expect(handlers.onCameras).toHaveBeenCalled();
});

test("when every step is done the stepper collapses to one line that can be expanded", async () => {
  vi.mocked(api.voiceProfile).mockResolvedValue({ exists: true });
  vi.mocked(api.episodeCameras).mockResolvedValue(cameras({ "0": "subject" }));
  await show(entry({ source, runs: [readyRun] }));
  expect(screen.getByText("Episódio pronto para editar")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Ver etapas" }));
  expect(step("Câmeras").textContent).toContain("Feito");
  expect(step("Voz do Dayon").textContent).toContain("Perfil de voz ativo");
  expect(screen.getByRole("button", { name: "Revisar" })).toBeTruthy();
});
