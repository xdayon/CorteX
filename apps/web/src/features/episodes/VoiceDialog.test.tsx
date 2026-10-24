import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type EpisodeEntry } from "../../api";
import { ToastProvider } from "../../ui";
import { VoiceDialog } from "./VoiceDialog";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [name, vi.fn(name.endsWith("Url") ? fn : undefined)])) };
});
vi.mock("../../VoiceSelector", () => ({ VoiceSelector: () => <div>Seletor de voz</div> }));

const entry = (speaker?: string) => ({ id: "ep", title: "Ep", subject_reference: speaker ? { speaker } : {} }) as unknown as EpisodeEntry;
async function open(item: EpisodeEntry) {
  render(<ToastProvider><VoiceDialog entry={item} onClose={() => {}} /></ToastProvider>);
  await act(async () => {});
}
const create = () => screen.getByRole("button", { name: /Usar a voz confirmada deste episódio como perfil|Cadastrando/ }) as HTMLButtonElement;

beforeEach(() => { vi.resetAllMocks(); });
afterEach(cleanup);

test("without a profile or a confirmed voice the create button is disabled with a hint", async () => {
  vi.mocked(api.voiceProfile).mockResolvedValue({ exists: false });
  await open(entry());
  expect(screen.getByText("Nenhum perfil de voz cadastrado")).toBeTruthy();
  expect(create().disabled).toBe(true);
  expect(screen.getByText("Confirme a voz do Dayon abaixo para poder cadastrar o perfil.")).toBeTruthy();
});

test("an active profile is summarised with date and accuracy", async () => {
  vi.mocked(api.voiceProfile).mockResolvedValue({ exists: true, created_at: "2026-10-12T10:00:00Z", calibration: { balanced_accuracy: 0.986 } });
  await open(entry("SPEAKER_01"));
  expect(screen.getByText(/Perfil de voz ativo desde .*2026 · precisão 98,6 %/)).toBeTruthy();
  expect(create().disabled).toBe(false);
  expect(screen.queryByText(/Confirme a voz do Dayon abaixo/)).toBeNull();
});

test("creating the profile shows the busy label, calls the API with the episode and reloads the summary", async () => {
  vi.mocked(api.voiceProfile).mockResolvedValueOnce({ exists: false }).mockResolvedValue({ exists: true, calibration: { balanced_accuracy: 0.9 } });
  let finish!: () => void;
  vi.mocked(api.createVoiceProfile).mockImplementation(() => new Promise(resolve => { finish = () => resolve({ exists: true }); }));
  await open(entry("SPEAKER_01"));
  fireEvent.click(create());
  await act(async () => {});
  expect(api.createVoiceProfile).toHaveBeenCalledWith("ep");
  expect(screen.getByRole("button", { name: "Cadastrando… pode levar até 1 min" })).toBeTruthy();
  await act(async () => { finish(); });
  expect(screen.getByText("Perfil de voz cadastrado.")).toBeTruthy();
  expect(screen.getByText(/Perfil de voz ativo/)).toBeTruthy();
});

test("a failed registration reports the error", async () => {
  vi.mocked(api.voiceProfile).mockResolvedValue({ exists: false });
  vi.mocked(api.createVoiceProfile).mockRejectedValue(new Error("409"));
  await open(entry("SPEAKER_01"));
  fireEvent.click(create());
  await act(async () => {});
  expect(screen.getByText(/Não foi possível cadastrar o perfil: 409/)).toBeTruthy();
});
