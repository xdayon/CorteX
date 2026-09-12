import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { VoiceSelector } from "./VoiceSelector";
import { api, type EpisodeEntry } from "./api";

vi.mock("./api", async (original) => {
  const actual = await original<typeof import("./api")>();
  return { ...actual, api: {
    ...actual.api,
    diarizationStatus: vi.fn(), diarizeEpisode: vi.fn(), watchJob: vi.fn(),
    episodeVoices: vi.fn(), selectEpisodeVoice: vi.fn(),
  } };
});

const entry: EpisodeEntry = {
  id: "AbCdEfGh_12", url: "https://www.youtube.com/watch?v=AbCdEfGh_12",
  title: "Podcast com Dayon", project_ids: ["project"], participant_count: 3,
  primary_subject: "Dayon", subject_reference: {}, diarizations: [],
  source: { id: "source", project_id: "project", kind: "youtube", stored_path: "source.mp4", sha256: "fixture", size_bytes: 1 },
  source_bytes: 1, runs: [], jobs: [], renders: [],
};
const blocked: Awaited<ReturnType<typeof api.diarizationStatus>> = {
  runtime_installed: true, token_configured: false, device: "cpu", ready: false,
  missing: ["Falta o acesso Hugging Face: configure HF_TOKEN no computador."],
  model_url: "https://huggingface.co/pyannote/speaker-diarization-community-1",
};
const ready = { ...blocked, token_configured: true, ready: true, missing: [] };

async function settle() { await act(async () => {}); }
function openSelector(episode = entry, disabled = false) {
  const view = render(<VoiceSelector entry={episode} disabled={disabled} />);
  const details = view.container.querySelector("details")!;
  act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  return view;
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.diarizationStatus).mockResolvedValue(blocked);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("an absent credential blocks the action and exposes the required setup links", async () => {
  openSelector();
  await settle();
  const start = screen.getByRole("button", { name: "Separar vozes em CPU" });
  expect((start as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(blocked.missing![0])).toBeTruthy();
  expect(screen.getByRole("link", { name: "Liberar acesso ao modelo" }).getAttribute("href")).toBe(blocked.model_url);
  expect(screen.getByRole("link", { name: "Criar token de leitura" }).getAttribute("href")).toBe("https://huggingface.co/settings/tokens");
  fireEvent.click(start);
  expect(api.diarizeEpisode).not.toHaveBeenCalled();
});

test("the start action remains blocked while readiness is still being checked", async () => {
  let resolve!: (value: typeof blocked) => void;
  vi.mocked(api.diarizationStatus).mockReturnValueOnce(new Promise(yes => { resolve = yes; }));
  openSelector();
  expect(screen.getByText("Verificando disponibilidade…")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Separar vozes em CPU" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Verificar configuração novamente" }) as HTMLButtonElement).disabled).toBe(true);
  await act(async () => resolve(blocked));
  expect((screen.getByRole("button", { name: "Verificar configuração novamente" }) as HTMLButtonElement).disabled).toBe(false);
});

test("rechecking a newly configured credential unlocks the action without claiming model access", async () => {
  openSelector();
  await settle();
  vi.mocked(api.diarizationStatus).mockResolvedValueOnce(ready);
  fireEvent.click(screen.getByRole("button", { name: "Verificar configuração novamente" }));
  await settle();
  expect(api.diarizationStatus).toHaveBeenCalledTimes(2);
  expect((screen.getByRole("button", { name: "Separar vozes em CPU" }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByText(/O acesso aos pesos será verificado ao iniciar/)).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Criar token de leitura" })).toBeNull();
  expect(api.diarizeEpisode).not.toHaveBeenCalled();
});

test("a failed initial readiness request stays blocked and supports a successful retry", async () => {
  vi.mocked(api.diarizationStatus).mockRejectedValueOnce(new Error("Serviço indisponível"));
  openSelector();
  await settle();
  expect(screen.getByRole("alert").textContent).toContain("Serviço indisponível");
  expect((screen.getByRole("button", { name: "Separar vozes em CPU" }) as HTMLButtonElement).disabled).toBe(true);
  vi.mocked(api.diarizationStatus).mockResolvedValueOnce(ready);
  fireEvent.click(screen.getByRole("button", { name: "Verificar configuração novamente" }));
  await settle();
  expect(screen.queryByRole("alert")).toBeNull();
  expect((screen.getByRole("button", { name: "Separar vozes em CPU" }) as HTMLButtonElement).disabled).toBe(false);
});

test.each(["disabled", "missing-source"])("ready credentials do not bypass the %s gate", async (gate) => {
  vi.mocked(api.diarizationStatus).mockResolvedValue(ready);
  openSelector(gate === "missing-source" ? { ...entry, source: null } : entry, gate === "disabled");
  await settle();
  expect((screen.getByRole("button", { name: "Separar vozes em CPU" }) as HTMLButtonElement).disabled).toBe(true);
  expect(api.diarizeEpisode).not.toHaveBeenCalled();
});

test("already persisted voice samples can be opened without a new readiness request", async () => {
  vi.mocked(api.episodeVoices).mockResolvedValue({ turns: [{ start: 0, end: 5, speaker: "SPEAKER_00" }] });
  openSelector({ ...entry, diarizations: [{ id: "voices", project_id: "project" }] });
  fireEvent.click(screen.getByRole("button", { name: "Ouvir vozes detectadas" }));
  await settle();
  expect(api.diarizationStatus).not.toHaveBeenCalled();
  expect(api.episodeVoices).toHaveBeenCalledWith(entry.id, "voices");
  expect(screen.getByRole("button", { name: "Esta voz é minha" })).toBeTruthy();
});

test("completed separation loads persisted samples and allows human confirmation", async () => {
  vi.mocked(api.diarizationStatus).mockResolvedValue(ready);
  vi.mocked(api.diarizeEpisode).mockResolvedValue({id:'voice-job'} as Awaited<ReturnType<typeof api.diarizeEpisode>>);
  vi.mocked(api.watchJob).mockResolvedValue({id:'voice-job',status:'succeeded',result:{diarization_artifact_id:'new-voices'}} as Awaited<ReturnType<typeof api.watchJob>>);
  vi.mocked(api.episodeVoices).mockResolvedValue({turns:[{start:4,end:8,speaker:'SPEAKER_01'}]});
  openSelector();await settle();
  fireEvent.click(screen.getByRole('button',{name:'Separar vozes em CPU'}));await settle();
  expect(screen.getByRole('status').textContent).toContain('✓ Vozes separadas');
  expect(api.episodeVoices).toHaveBeenCalledWith(entry.id,'new-voices');
  fireEvent.click(screen.getByRole('button',{name:'Esta voz é minha'}));await settle();
  expect(api.selectEpisodeVoice).toHaveBeenCalledWith(entry.id,'new-voices','SPEAKER_01');
  expect(screen.getByRole('button',{name:'Dayon confirmado'})).toBeTruthy();
});
