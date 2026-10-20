import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type ApiJob, type CameraGroup, type EpisodeCameras, type EpisodeEntry } from "../../api";
import { CamerasDialog } from "./CamerasDialog";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [name, vi.fn(name.endsWith("Url") ? fn : undefined)])) };
});

const entry = { id: "ep1", title: "Episódio" } as EpisodeEntry;
const group = (id: number, share: number, extra: Partial<CameraGroup> = {}): CameraGroup => ({
  id, sample_time: 1, airtime_seconds: share * 1000, airtime_share: share, shot_count: 10, median_shot_seconds: 4, faces: 1,
  subject_voice_share: null, suggested_role: null, thumbnail_url: `/t/${id}.jpg`, ...extra,
});
const cameras = (groups: CameraGroup[], extra: Partial<EpisodeCameras> = {}, voice: "ok" | "unavailable" = "ok"): EpisodeCameras => ({
  artifact_id: "art", roles: null, job: null,
  document: { artifact_id: "art", duration_seconds: 1000, keyframe_count: 50, shots: 20, voice: voice === "ok" ? { status: "ok" } : { status: "unavailable", reason: "sem token" }, groups },
  ...extra,
});
const base = [
  group(0, 0.6, { suggested_role: "subject", subject_voice_share: 0.82 }),
  group(1, 0.3, { suggested_role: "interviewers" }),
  group(2, 0.005),
];
const checked = (name: string) => within(screen.getByRole("radiogroup", { name })).getByRole("radio", { checked: true }).textContent;

async function open() { render(<CamerasDialog entry={entry} onClose={() => {}} />); await act(async () => {}); }

beforeEach(() => { vi.resetAllMocks(); });
afterEach(cleanup);

test("empty state maps cameras, watches the job and renders the groups", async () => {
  vi.mocked(api.episodeCameras).mockResolvedValueOnce(null).mockResolvedValueOnce(cameras(base));
  vi.mocked(api.mapEpisodeCameras).mockResolvedValue({ id: "j1", status: "queued" } as ApiJob);
  vi.mocked(api.watchJob).mockImplementation(async (_id, onUpdate) => {
    onUpdate?.({ id: "j1", status: "running", message: "Agrupando planos" } as ApiJob);
    return { id: "j1", status: "succeeded" } as ApiJob;
  });
  await open();
  expect(screen.getByText(/agrupa os planos do episódio/)).toBeTruthy();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Mapear câmeras" })); });
  expect(api.mapEpisodeCameras).toHaveBeenCalledWith("ep1");
  expect(api.watchJob).toHaveBeenCalled();
  expect(screen.getByAltText("Câmera 1")).toBeTruthy();
  expect(checked("Função da câmera 1")).toBe("Você");
  expect(checked("Função da câmera 2")).toBe("Entrevistadores");
  expect(screen.getAllByText("sugestão")).toHaveLength(2);
  expect(screen.getByText("60% no ar · 10 planos")).toBeTruthy();
  expect(screen.getByText("sua voz em 82%")).toBeTruthy();
});

test("saved roles win over suggestions and show no chip", async () => {
  vi.mocked(api.episodeCameras).mockResolvedValue(cameras(base, { roles: { "0": "wide", "1": "ignore" } }));
  await open();
  expect(checked("Função da câmera 1")).toBe("Plano aberto");
  expect(checked("Função da câmera 2")).toBe("Ignorar");
  expect(screen.queryByText("sugestão")).toBeNull();
});

test("rare groups are collapsed and default to ignore", async () => {
  vi.mocked(api.episodeCameras).mockResolvedValue(cameras(base));
  await open();
  const details = document.querySelector("details.cam-rare") as HTMLDetailsElement;
  expect(details.open).toBe(false);
  expect(details.textContent).toContain("Planos raros (1) — vinhetas e transições, ignorados");
  expect(checked("Função da câmera 3")).toBe("Ignorar");
});

test("changing a role and saving sends the payload", async () => {
  vi.mocked(api.episodeCameras).mockResolvedValue(cameras(base));
  vi.mocked(api.saveCameraRoles).mockResolvedValue(entry);
  await open();
  fireEvent.click(within(screen.getByRole("radiogroup", { name: "Função da câmera 2" })).getByRole("radio", { name: "Plano aberto" }));
  expect(screen.getAllByText("sugestão")).toHaveLength(1);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Salvar câmeras" })); });
  expect(api.saveCameraRoles).toHaveBeenCalledWith("ep1", "art", { "0": "subject", "1": "wide", "2": "ignore" });
  expect(screen.getByText("Câmeras salvas")).toBeTruthy();
  expect(screen.queryByText("sugestão")).toBeNull();
});

test("save is disabled while a main group has no role", async () => {
  vi.mocked(api.episodeCameras).mockResolvedValue(cameras([group(0, 0.7), group(1, 0.3, { suggested_role: "wide" })]));
  await open();
  expect((screen.getByRole("button", { name: "Salvar câmeras" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(within(screen.getByRole("radiogroup", { name: "Função da câmera 1" })).getByRole("radio", { name: "Você" }));
  expect((screen.getByRole("button", { name: "Salvar câmeras" }) as HTMLButtonElement).disabled).toBe(false);
});

test("shows the voice note when voice recognition is unavailable", async () => {
  vi.mocked(api.episodeCameras).mockResolvedValue(cameras(base, {}, "unavailable"));
  await open();
  expect(screen.getByText(/Sem o reconhecimento de voz/).textContent).toContain("sem token");
});
