import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type ApiJob } from "../api";
import App from "./App";

vi.mock("../api", async (original) => {
  const actual = await original<typeof import("../api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [
    name, vi.fn(name.endsWith("Url") ? fn : undefined),
  ])) };
});

const job = (id: string, type: string, status: string, progress: number, created: string) => ({ id, type, status, progress, created_at: created }) as ApiJob;

async function settle() { await act(async () => { await vi.advanceTimersByTimeAsync(0); }); }

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  vi.mocked(api.episodes).mockResolvedValue([]);
  vi.mocked(api.activeJobs).mockResolvedValue([]);
  vi.mocked(api.clips).mockResolvedValue({ clips: [], total: 0 });
  window.history.replaceState(null, "", "/");
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

test("the home route is the episodes library inside the shell, with an idle activity hint", async () => {
  render(<App />); await settle();
  expect(screen.getByRole("heading", { name: "Episódios" })).toBeTruthy();
  const nav = screen.getByRole("navigation", { name: "Principal" });
  expect(within(nav).getByRole("link", { name: "Episódios" }).getAttribute("aria-current")).toBe("page");
  expect(within(nav).getByRole("link", { name: "Cortes" })).toBeTruthy();
  expect(screen.getByText("Tudo em dia")).toBeTruthy();
  expect(document.title).toBe("Episódios · CorteX");
  expect(screen.queryByText(/Retomar/)).toBeNull();
});

test("the activity pill shows the newest running job, lists active jobs and polls faster while busy", async () => {
  vi.mocked(api.activeJobs).mockResolvedValue([
    job("old", "transcription", "running", 10, "2026-01-01T10:00:00Z"),
    job("new", "ingest_youtube", "running", 42.4, "2026-01-01T11:00:00Z"),
    job("queued", "mystery", "queued", 0, "2026-01-01T11:30:00Z"),
    job("done", "render", "succeeded", 100, "2026-01-01T12:00:00Z"),
  ]);
  render(<App />); await settle();
  expect(api.activeJobs).toHaveBeenCalledWith(expect.anything());
  const pill = screen.getByRole("button", { name: /Baixando · 42%/ });
  fireEvent.click(pill);
  const menu = screen.getByRole("menu", { name: "Tarefas em andamento" });
  expect(within(menu).getByText("3 tarefas em andamento")).toBeTruthy();
  expect(within(menu).getByText("Transcrevendo")).toBeTruthy();
  expect(within(menu).getByText("Processando")).toBeTruthy();
  expect(within(menu).queryByText("Renderizando")).toBeNull();
  const calls = vi.mocked(api.activeJobs).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
  expect(vi.mocked(api.activeJobs).mock.calls.length).toBe(calls + 1);
});

test("while idle the job list is polled every 15 seconds", async () => {
  render(<App />); await settle();
  const calls = vi.mocked(api.activeJobs).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(14000); });
  expect(vi.mocked(api.activeJobs).mock.calls.length).toBe(calls);
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(vi.mocked(api.activeJobs).mock.calls.length).toBe(calls + 1);
});

test("navigation links switch pages without reloading and unknown paths return home", async () => {
  render(<App />); await settle();
  fireEvent.click(screen.getByRole("link", { name: "Cortes" }));
  expect(window.location.pathname).toBe("/cortes");
  await settle();
  expect(screen.getByRole("heading", { name: "Cortes" })).toBeTruthy();
  expect(document.title).toBe("Cortes · CorteX");
  act(() => { window.history.pushState(null, "", "/nada/aqui"); window.dispatchEvent(new PopStateEvent("popstate")); });
  await settle();
  expect(window.location.pathname).toBe("/");
  expect(screen.getByRole("heading", { name: "Episódios" })).toBeTruthy();
});

test("a failed poll shows the connection as lost until the next poll succeeds", async () => {
  vi.mocked(api.activeJobs).mockRejectedValueOnce(new Error("down")).mockResolvedValue([]);
  render(<App />); await settle();
  expect(screen.getByRole("status", { name: "" }).textContent).toBe("Sem conexão com o CorteX");
  expect(screen.queryByText("Tudo em dia")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(15500); });
  expect(screen.getByText("Tudo em dia")).toBeTruthy();
  expect(screen.queryByText("Sem conexão com o CorteX")).toBeNull();
});
