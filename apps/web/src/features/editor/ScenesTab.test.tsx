import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type ApiJob, type EpisodeEntry, type RenderSettings, type SceneSuggestion, type SceneSuggestions } from "../../api";
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
function Harness({ initial, planId = "plan", loading = false, error = "", seek = vi.fn(), own }: { initial: SceneSuggestions | null; planId?: string | null; loading?: boolean; error?: string; seek?: (s: number) => void; own?: RenderSettings["inserts"] }) {
  const [settings, setSettings] = useState<RenderSettings>(own === undefined ? defaultRenderSettings : { ...defaultRenderSettings, inserts: own });
  const [data, setData] = useState(initial);
  saved[0] = settings;
  const scenes: SceneData = { data, loading, error, refresh, patch: change => setData(current => (current ? change(current) : current)) };
  return <ToastProvider><ScenesTab scenes={scenes} planId={planId} entryId="ep" settings={settings} update={change => setSettings(change)} onSeek={seek} disabled={false} /></ToastProvider>;
}
const inserts = () => saved[0].inserts;
const auto = (id: string, anchor: number, extra = {}) => ({ kind: "reaction", anchor, duration: 1.8, source_start: 500 + anchor, group: 1, suggestion_id: id, ...extra });

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

test("needs_camera_map starts mapping by itself, polls the job and refreshes when it finishes", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  vi.mocked(api.mapEpisodeCameras).mockResolvedValue({ id: "j1", status: "running", progress: 10, message: "Mapeando" } as never);
  let finish: (job: ApiJob) => void = () => undefined;
  vi.mocked(api.watchJob).mockImplementation((_id, onUpdate) => new Promise<ApiJob>(resolve => { onUpdate?.({ id: "j1", status: "running", progress: 60, message: "Metade" }); finish = resolve; }));
  render(<Harness initial={sceneData({ status: "needs_camera_map", suggestions: [] })} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(api.mapEpisodeCameras).toHaveBeenCalledWith("ep");
  expect(screen.getByRole("progressbar", { name: "Mapeando câmeras" })).toBeTruthy();
  expect(api.watchJob).toHaveBeenCalledWith("j1", expect.any(Function), expect.anything());
  expect(screen.getByText("Metade")).toBeTruthy();
  await act(async () => { finish({ id: "j1", status: "succeeded", progress: 100 }); });
  expect(refresh).toHaveBeenCalledTimes(1);
});

test("a failed mapping offers to try again", async () => {
  vi.mocked(api.mapEpisodeCameras).mockRejectedValueOnce(new Error("sem vídeo")).mockResolvedValue({ id: "j1", status: "running" } as never);
  vi.mocked(api.watchJob).mockResolvedValue({ id: "j1", status: "succeeded" } as never);
  render(<Harness initial={sceneData({ status: "needs_camera_map", suggestions: [] })} />);
  await act(async () => {});
  expect(screen.getByRole("alert").textContent).toContain("sem vídeo");
  fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
  await act(async () => {});
  expect(api.mapEpisodeCameras).toHaveBeenCalledTimes(2);
  expect(refresh).toHaveBeenCalledTimes(1);
});

test("roles suggested by the voice can be checked, and needs_voice opens the voice dialog", async () => {
  vi.mocked(api.episode).mockResolvedValue({ id: "ep", title: "Ep" } as EpisodeEntry);
  render(<Harness initial={sceneData({ roles_source: "suggested" })} />);
  expect(screen.getByText(/Câmeras identificadas pela voz/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Conferir câmeras" }));
  await act(async () => {});
  expect(screen.getByText("Dialog de câmeras ep")).toBeTruthy();
  cleanup();
  render(<Harness initial={sceneData({ status: "needs_voice" })} />);
  fireEvent.click(screen.getByRole("button", { name: "Cadastrar voz" }));
  await act(async () => {});
  expect(screen.getByText("Dialog de voz ep")).toBeTruthy();
});

test("automatic: every suggestion is in the clip until a change makes the list the clip's own", async () => {
  render(<Harness initial={sceneData()} />);
  expect(screen.getByText("Automáticas")).toBeTruthy();
  expect(screen.getByText(/O CorteX já colocou o plano aberto e o entrevistador/)).toBeTruthy();
  expect(screen.getByText("Aprovação no episódio: 83 %")).toBeTruthy();
  expect(inserts()).toBeUndefined();
  expect(within(row("Reação em 0:10")).getByRole("button", { name: "Aceita" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Voltar ao automático" })).toBeNull();
  // The same click again takes it out; the other one stays.
  fireEvent.click(within(row("Reação em 0:10")).getByRole("button", { name: "Aceita" }));
  await act(async () => {});
  expect(inserts()).toEqual([auto("b", 120, { kind: "wide" })]);
  expect(api.saveSceneReviews).toHaveBeenCalledWith("ep", { a: null });
  expect(screen.getByText("1 de 2 aceitas")).toBeTruthy();
  fireEvent.click(within(row("Reação em 0:10")).getByRole("button", { name: "Aceitar" }));
  await act(async () => {});
  expect(inserts()).toEqual([auto("a", 110), auto("b", 120, { kind: "wide" })]);
  expect(api.saveSceneReviews).toHaveBeenLastCalledWith("ep", { a: "accepted" });
  fireEvent.click(screen.getByRole("button", { name: "Voltar ao automático" }));
  expect(inserts()).toBeUndefined();
  expect(screen.getByText("Automáticas")).toBeTruthy();
});

test("rejecting removes the insert and saves 'rejected'; the thumbnail points at the middle of the footage", async () => {
  render(<Harness initial={sceneData()} />);
  expect(row("Reação em 0:10").querySelector("img")?.getAttribute("src")).toBe("/frame/610.9.jpg");
  fireEvent.click(within(row("Reação em 0:10")).getByRole("button", { name: "Recusar" }));
  await act(async () => {});
  expect(inserts()).toEqual([auto("b", 120, { kind: "wide" })]);
  expect(api.saveSceneReviews).toHaveBeenLastCalledWith("ep", { a: "rejected" });
  expect(within(row("Reação em 0:10")).getByRole("button", { name: "Recusada" })).toBeTruthy();
});

test("a failed review save keeps the insert and warns", async () => {
  vi.mocked(api.saveSceneReviews).mockRejectedValue(new Error("rede"));
  render(<Harness initial={sceneData()} own={[]} />);
  expect(screen.getByText("0 de 2 aceitas")).toBeTruthy();
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
  fireEvent.click(within(wide()).getByRole("button", { name: "Trocar" }));
  await act(async () => {});
  expect(inserts()).toEqual([auto("a", 110), auto("b", 120, { source_start: 777, group: 2 })]);
  expect(screen.getByRole("listitem", { name: "Reação em 0:20" })).toBeTruthy();
  fireEvent.click(within(screen.getByRole("listitem", { name: "Reação em 0:20" })).getByRole("button", { name: "Trocar" }));
  expect(inserts()![1]).toMatchObject({ kind: "wide", source_start: 620, group: 1 });
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
  expect(saved[0].inserts).toEqual([]);
  expect(screen.queryByRole("region", { name: "Fora do corte atual" })).toBeNull();
});
