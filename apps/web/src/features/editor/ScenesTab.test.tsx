import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type EpisodeEntry, type RenderSettings, type SceneSuggestion, type SceneSuggestions } from "../../api";
import { defaultRenderSettings } from "../../editor/settings";
import { ToastProvider } from "../../ui";
import { ScenesTab } from "./ScenesTab";
import type { SceneData } from "./useSceneSuggestions";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [name, vi.fn(name.endsWith("Url") ? fn : undefined)])) };
});
vi.mock("../episodes/CamerasDialog", () => ({ CamerasDialog: ({ entry }: { entry: EpisodeEntry }) => <div>Dialog de câmeras {entry.id}</div> }));
vi.mock("../episodes/VoiceDialog", () => ({ VoiceDialog: ({ entry }: { entry: EpisodeEntry }) => <div>Dialog de voz {entry.id}</div> }));

const suggestion = (id: string, anchor: number, extra: Partial<SceneSuggestion> = {}): SceneSuggestion => ({
  id, kind: "reaction", anchor, duration: 1.8, output_start: anchor - 100, source_start: 500 + anchor, group: 1, score: 0.8, reason: `Motivo ${id}`, alternatives: [], ...extra,
});
const sceneData = (extra: Partial<SceneSuggestions> = {}): SceneSuggestions => ({
  status: "ok", algorithm_version: "1", project_id: "project", source_asset_id: "src", camera_map_artifact_id: "cam",
  suggestions: [suggestion("a", 110), suggestion("b", 120, { kind: "wide", alternatives: [{ kind: "reaction", source_start: 777, group: 2, score: 0.5 }] })],
  question: { start: 90, end: 94.2, text: "Como foi isso?" }, decisions: {}, review: { accepted: 0, rejected: 0, approval_rate: 0.83 }, ...extra,
});

const saved: Array<RenderSettings> = [];
const refresh = vi.fn();
function Harness({ initial, planId = "plan", loading = false, error = "", seek = vi.fn() }: { initial: SceneSuggestions | null; planId?: string | null; loading?: boolean; error?: string; seek?: (s: number) => void }) {
  const [settings, setSettings] = useState<RenderSettings>(defaultRenderSettings);
  const [data, setData] = useState(initial);
  saved[0] = settings;
  const scenes: SceneData = { data, loading, error, refresh, patch: change => setData(current => (current ? change(current) : current)) };
  return <ToastProvider><ScenesTab scenes={scenes} planId={planId} entryId="ep" settings={settings} update={change => setSettings(change)} onSeek={seek} disabled={false} /></ToastProvider>;
}
const inserts = () => saved[0].inserts ?? [];

beforeEach(() => {
  vi.resetAllMocks(); saved.length = 0;
  vi.mocked(api.sourcePreviewUrl).mockImplementation((_p, _s, seconds) => `/frame/${seconds}.jpg`);
  vi.mocked(api.sourceMediaUrl).mockReturnValue("/media.mp4");
  vi.mocked(api.saveSceneReviews).mockResolvedValue({ accepted: 1, rejected: 0, approval_rate: 1 });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

const row = (label: string) => screen.getByRole("listitem", { name: label });

test("without a plan the tab shows a skeleton, and a failed load offers to retry", () => {
  render(<Harness initial={null} planId={null} />);
  expect(screen.getByText("Montando o corte…")).toBeTruthy();
  cleanup();
  render(<Harness initial={null} error="falhou" />);
  expect(screen.getByRole("alert").textContent).toContain("falhou");
  fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
  expect(refresh).toHaveBeenCalledTimes(1);
});

test("needs_camera_map maps the cameras, polls the job and refreshes when it finishes", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  vi.mocked(api.mapEpisodeCameras).mockResolvedValue({ id: "j1", status: "running", progress: 10, message: "Mapeando" } as never);
  vi.mocked(api.job).mockResolvedValueOnce({ id: "j1", status: "running", progress: 60 } as never).mockResolvedValueOnce({ id: "j1", status: "succeeded", progress: 100 } as never);
  render(<Harness initial={sceneData({ status: "needs_camera_map", suggestions: [] })} />);
  fireEvent.click(screen.getByRole("button", { name: "Mapear câmeras do episódio (~2 min)" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByRole("progressbar", { name: "Mapeando câmeras" })).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(api.job).toHaveBeenCalledTimes(2);
  expect(refresh).toHaveBeenCalledTimes(1);
});

test("needs_roles and needs_voice open the existing dialogs", async () => {
  vi.mocked(api.episodes).mockResolvedValue([{ id: "ep", title: "Ep" } as EpisodeEntry]);
  render(<Harness initial={sceneData({ status: "needs_roles" })} />);
  expect(screen.getByText("Confirme quem aparece em cada câmera")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar câmeras" }));
  await act(async () => {});
  expect(screen.getByText("Dialog de câmeras ep")).toBeTruthy();
  cleanup();
  render(<Harness initial={sceneData({ status: "needs_voice" })} />);
  fireEvent.click(screen.getByRole("button", { name: "Cadastrar voz" }));
  await act(async () => {});
  expect(screen.getByText("Dialog de voz ep")).toBeTruthy();
});

test("accepting adds a clip-scoped insert with its suggestion id and saves the review; the same click again clears it", async () => {
  render(<Harness initial={sceneData()} />);
  expect(screen.getByText("0 de 2 aceitas")).toBeTruthy();
  expect(screen.getByText("Aprovação no episódio: 83 %")).toBeTruthy();
  fireEvent.click(within(row("Reação em 0:10")).getByRole("button", { name: "Aceitar" }));
  await act(async () => {});
  expect(inserts()).toEqual([{ kind: "reaction", anchor: 110, duration: 1.8, source_start: 610, group: 1, suggestion_id: "a" }]);
  expect(api.saveSceneReviews).toHaveBeenCalledWith("ep", { a: "accepted" });
  expect(screen.getByText("1 de 2 aceitas")).toBeTruthy();
  fireEvent.click(within(row("Reação em 0:10")).getByRole("button", { name: "Aceita" }));
  await act(async () => {});
  expect(inserts()).toEqual([]);
  expect(api.saveSceneReviews).toHaveBeenLastCalledWith("ep", { a: null });
});

test("rejecting removes the insert and saves 'rejected'; the thumbnail points at the middle of the footage", async () => {
  render(<Harness initial={sceneData()} />);
  expect(row("Reação em 0:10").querySelector("img")?.getAttribute("src")).toBe("/frame/610.9.jpg");
  fireEvent.click(within(row("Reação em 0:10")).getByRole("button", { name: "Aceitar" }));
  fireEvent.click(within(row("Reação em 0:10")).getByRole("button", { name: "Recusar" }));
  await act(async () => {});
  expect(inserts()).toEqual([]);
  expect(api.saveSceneReviews).toHaveBeenLastCalledWith("ep", { a: "rejected" });
  expect(within(row("Reação em 0:10")).getByRole("button", { name: "Recusada" })).toBeTruthy();
});

test("Aplicar triangulação accepts every pending suggestion at once, skipping the rejected", async () => {
  render(<Harness initial={sceneData({ decisions: { b: "rejected" } })} />);
  fireEvent.click(screen.getByRole("button", { name: "Aplicar triangulação" }));
  await act(async () => {});
  expect(inserts().map(item => item.suggestion_id)).toEqual(["a"]);
  expect(api.saveSceneReviews).toHaveBeenCalledTimes(1);
  expect(api.saveSceneReviews).toHaveBeenCalledWith("ep", { a: "accepted" });
  expect((screen.getByRole("button", { name: "Aplicar triangulação" }) as HTMLButtonElement).disabled).toBe(true);
});

test("a failed review save keeps the insert and warns", async () => {
  vi.mocked(api.saveSceneReviews).mockRejectedValue(new Error("rede"));
  render(<Harness initial={sceneData()} />);
  fireEvent.click(within(row("Reação em 0:10")).getByRole("button", { name: "Aceitar" }));
  await act(async () => {});
  expect(inserts()).toHaveLength(1);
  expect(screen.getByText(/Não foi possível salvar a avaliação: rede/)).toBeTruthy();
});

test("the question switch sets and clears transitions.question, and Ir para seeks to the output second", () => {
  const seek = vi.fn();
  render(<Harness initial={sceneData()} seek={seek} />);
  expect(screen.getByText(/«Como foi isso\?»/)).toBeTruthy();
  expect(screen.getByText("4,2 s")).toBeTruthy();
  const toggle = screen.getByRole("switch", { name: "Abrir com a pergunta do entrevistador" });
  fireEvent.click(toggle);
  expect(saved[0].transitions.question).toEqual({ start: 90, end: 94.2 });
  fireEvent.click(toggle);
  expect(saved[0].transitions.question).toBeNull();
  fireEvent.click(within(row("Plano aberto em 0:20")).getByRole("button", { name: "Ir para" }));
  expect(seek).toHaveBeenCalledWith(20);
});

test("no question shows a muted line; Ver opens the source player without autoplay on mount", () => {
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  render(<Harness initial={sceneData({ question: null })} />);
  expect(screen.getByText("Nenhuma pergunta logo antes deste corte")).toBeTruthy();
  expect(document.querySelector("video")).toBeNull();
  fireEvent.click(within(row("Reação em 0:10")).getByRole("button", { name: "Ver" }));
  expect(document.querySelector("video")?.getAttribute("src")).toBe("/media.mp4");
});

test("Trocar cycles the alternatives and updates an accepted insert", async () => {
  render(<Harness initial={sceneData()} />);
  const wide = () => row("Plano aberto em 0:20");
  fireEvent.click(within(wide()).getByRole("button", { name: "Aceitar" }));
  fireEvent.click(within(wide()).getByRole("button", { name: "Trocar" }));
  await act(async () => {});
  expect(inserts()).toEqual([{ kind: "reaction", anchor: 120, duration: 1.8, source_start: 777, group: 2, suggestion_id: "b" }]);
  expect(screen.getByRole("listitem", { name: "Reação em 0:20" })).toBeTruthy();
  fireEvent.click(within(screen.getByRole("listitem", { name: "Reação em 0:20" })).getByRole("button", { name: "Trocar" }));
  expect(inserts()[0]).toMatchObject({ kind: "wide", source_start: 620, group: 1 });
});

test("accepted inserts missing from the current suggestions are listed apart and can be removed", () => {
  const Orphan = () => {
    const [settings, setSettings] = useState<RenderSettings>({ ...defaultRenderSettings, inserts: [{ kind: "wide", anchor: 300, duration: 2, source_start: 40, suggestion_id: "gone" }] });
    const scenes: SceneData = { data: sceneData(), loading: false, error: "", refresh, patch: () => undefined };
    saved[0] = settings;
    return <ToastProvider><ScenesTab scenes={scenes} planId="plan" entryId="ep" settings={settings} update={change => setSettings(change)} onSeek={() => undefined} disabled={false} /></ToastProvider>;
  };
  render(<Orphan />);
  const group = screen.getByRole("region", { name: "Fora do corte atual" });
  fireEvent.click(within(group).getByRole("button", { name: "Remover" }));
  expect(inserts()).toEqual([]);
  expect(screen.queryByRole("region", { name: "Fora do corte atual" })).toBeNull();
});
