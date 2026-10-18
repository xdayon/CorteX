import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type ClipState } from "../../api";
import { clipRow, episodeClips, withExport } from "../../test/clipFixtures";
import { ToastProvider } from "../../ui";
import { EpisodeClips } from "./EpisodeClips";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [
    name, vi.fn(name.endsWith("Url") ? fn : undefined),
  ])) };
});

const state = (extra: Partial<ClipState>): ClipState => ({ decision: null, discard_reason: null, saved: false, saved_at: null, notes: "", updated_at: "x", ...extra });
const first = clipRow(1);
const second = withExport(clipRow(2), "exported");
const third = clipRow(3, { run_id: "run2", clip_key: "3:300:360:balanced", run_created_at: "2026-10-16T10:00:00Z" });
const K1 = "1:100:160:balanced";

function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function settle() { await act(async () => {}); }
async function open(rows = [first, second]) {
  vi.mocked(api.episodeClips).mockResolvedValue(episodeClips(rows));
  render(<ToastProvider><EpisodeClips episodeId="ep" refreshKey="k" /></ToastProvider>);
  await settle();
}
const key = (value: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(window, { key: value, ...init });
const detailTitle = () => screen.getByRole("heading", { level: 2 }).textContent;
const play = vi.fn(() => Promise.resolve());

beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/episodios/ep");
  vi.mocked(api.publications).mockResolvedValue([]);
  vi.mocked(api.patchClipStates).mockResolvedValue({ items: [] });
  vi.mocked(api.warmQuickPreview).mockResolvedValue(undefined);
  Object.defineProperty(HTMLMediaElement.prototype, "play", { configurable: true, value: play });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", { configurable: true, value: vi.fn() });
  play.mockClear();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("approving is optimistic and sends only the changed keys", async () => {
  const pending = deferred<{ items: ClipState[] }>();
  vi.mocked(api.patchClipStates).mockReturnValue(pending.promise);
  await open();
  expect(detailTitle()).toBe("Manchete 2");
  fireEvent.click(screen.getByRole("button", { name: /Manchete 1/ }));
  fireEvent.click(screen.getByRole("button", { name: "Aprovar" }));
  expect(screen.getByRole("button", { name: "Aprovado" }).getAttribute("aria-pressed")).toBe("true");
  expect(within(screen.getByRole("list", { name: "Lista de cortes" })).getAllByText("Aprovado").length).toBe(1);
  expect(api.patchClipStates).toHaveBeenCalledWith([{ project_id: "project", run_id: "run", clip_key: K1, decision: "approved" }]);
  await act(async () => pending.resolve({ items: [] }));
  expect(screen.getByRole("button", { name: "Aprovado" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Aprovado" }));
  expect(vi.mocked(api.patchClipStates).mock.lastCall![0][0]).toMatchObject({ decision: null });
});

test("a failed PATCH rolls the clip back and shows an error toast", async () => {
  const pending = deferred<{ items: ClipState[] }>();
  vi.mocked(api.patchClipStates).mockReturnValue(pending.promise);
  await open();
  fireEvent.click(screen.getByRole("button", { name: /Manchete 1/ }));
  fireEvent.click(screen.getByRole("button", { name: "Aprovar" }));
  expect(screen.getByRole("button", { name: "Aprovado" })).toBeTruthy();
  await act(async () => pending.reject(new Error("servidor fora")));
  expect(screen.getByRole("button", { name: "Aprovar" })).toBeTruthy();
  expect(within(screen.getByRole("list", { name: "Lista de cortes" })).queryByText("Aprovado")).toBeNull();
  expect(screen.getByRole("alert").textContent).toContain("servidor fora");
});

test("discarding asks for a reason, dims the row in Todos and removes it from other filters", async () => {
  await open();
  fireEvent.click(screen.getByRole("button", { name: /Manchete 1/ }));
  fireEvent.click(screen.getByRole("button", { name: "Descartar" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Gancho fraco" }));
  expect(api.patchClipStates).toHaveBeenCalledWith([{ project_id: "project", run_id: "run", clip_key: K1, decision: "discarded", discard_reason: "weak_hook" }]);
  const row = screen.getByRole("button", { name: /Manchete 1/ }).closest("li")!;
  expect(row.className).toContain("dim");
  expect(within(row).getByText("Descartado")).toBeTruthy();
  expect(screen.getByText("Gancho fraco", { selector: ".cd-chips .chip" })).toBeTruthy();
  fireEvent.click(screen.getByRole("radio", { name: /Novos/ }));
  expect(screen.queryByRole("button", { name: /Manchete 1/ })).toBeNull();
});

test("keyboard shortcuts move, select, approve, discard, save and edit; typing in the search box is ignored", async () => {
  await open();
  expect(detailTitle()).toBe("Manchete 2");
  key("ArrowDown");
  expect(detailTitle()).toBe("Manchete 1");
  key("ArrowUp");
  expect(detailTitle()).toBe("Manchete 2");
  key("x");
  expect(screen.getByRole("region", { name: "Cortes selecionados" }).textContent).toContain("1 selecionado");
  key("x");
  expect(screen.queryByRole("region", { name: "Cortes selecionados" })).toBeNull();
  key("a");
  expect(vi.mocked(api.patchClipStates).mock.lastCall![0][0]).toMatchObject({ clip_key: "2:200:260:balanced", decision: "approved" });
  key("s");
  expect(vi.mocked(api.patchClipStates).mock.lastCall![0][0]).toMatchObject({ saved: true });
  key("d");
  expect(screen.getByRole("menu", { name: "Motivo do descarte" })).toBeTruthy();
  fireEvent.keyDown(document.body, { key: "Escape" });
  fireEvent.keyDown(screen.getByLabelText("Buscar corte"), { key: "x" });
  expect(screen.queryByRole("region", { name: "Cortes selecionados" })).toBeNull();
  key("e");
  expect(window.location.pathname).toBe("/episodios/ep/editor");
  expect(new URLSearchParams(window.location.search).get("clips")).toBe("run~2:200:260:balanced");
});

test("S does nothing for a clip that was never exported, and Space plays the source piece player", async () => {
  await open([first]);
  vi.mocked(api.patchClipStates).mockClear();
  key("s");
  expect(api.patchClipStates).not.toHaveBeenCalled();
  key(" ");
  expect(play).toHaveBeenCalledTimes(1);
});

test("the bulk bar approves, saves only exported clips, counts downloads and sends them to the editor", async () => {
  await open([first, second]);
  fireEvent.click(screen.getByRole("checkbox", { name: "Selecionar Manchete 1" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Selecionar Manchete 2" }));
  const bar = screen.getByRole("region", { name: "Cortes selecionados" });
  expect(within(bar).getByText("2 selecionados")).toBeTruthy();
  expect(within(bar).getByText("2:00")).toBeTruthy();
  expect(within(bar).getByRole("button", { name: "Baixar (1 MP4)" })).toBeTruthy();
  fireEvent.click(within(bar).getByRole("button", { name: "Marcar salvos" }));
  expect(api.patchClipStates).toHaveBeenLastCalledWith([expect.objectContaining({ clip_key: "2:200:260:balanced", saved: true })]);
  fireEvent.click(within(bar).getByRole("button", { name: "Aprovar" }));
  expect(vi.mocked(api.patchClipStates).mock.lastCall![0]).toHaveLength(2);
  fireEvent.click(within(bar).getByRole("button", { name: "Editar e exportar →" }));
  expect(new URLSearchParams(window.location.search).get("clips")).toBe(`run~${K1},run~2:200:260:balanced`);
});

test("select all toggles every visible clip, shows a mixed state and answers Ctrl+A outside text fields", async () => {
  await open([first, second, third]);
  const all = () => screen.getByRole("checkbox", { name: /os 3 cortes visíveis$/ });
  const bar = () => screen.queryByRole("region", { name: "Cortes selecionados" });
  fireEvent.click(screen.getByRole("checkbox", { name: "Selecionar Manchete 1" }));
  expect(all().getAttribute("aria-checked")).toBe("mixed");
  fireEvent.click(all());
  expect(all().getAttribute("aria-checked")).toBe("true");
  expect(bar()!.textContent).toContain("3 selecionados");
  fireEvent.click(all());
  expect(bar()).toBeNull();
  key("a", { ctrlKey: true });
  expect(bar()!.textContent).toContain("3 selecionados");
  fireEvent.keyDown(screen.getByLabelText("Buscar corte"), { key: "a", ctrlKey: true });
  expect(bar()!.textContent).toContain("3 selecionados");
  expect(api.patchClipStates).not.toHaveBeenCalled();
});

test("select all only reaches the clips the filter shows, then all of them once the filter clears", async () => {
  window.history.replaceState(null, "", "/episodios/ep?q=manchete%203");
  await open([first, second, third]);
  fireEvent.click(screen.getByRole("checkbox", { name: /os 1 cortes visíveis$/ }));
  expect(screen.getByRole("region", { name: "Cortes selecionados" }).textContent).toContain("1 selecionado");
  fireEvent.change(screen.getByLabelText("Buscar corte"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("checkbox", { name: /os 3 cortes visíveis$/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /os 3 cortes visíveis$/ }));
  expect(screen.queryByRole("region", { name: "Cortes selecionados" })).toBeNull();
});

test("clips from different runs open the editor together, with run-qualified keys", async () => {
  await open([first, third]);
  fireEvent.click(screen.getByRole("checkbox", { name: "Selecionar Manchete 1" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Selecionar Manchete 3" }));
  fireEvent.click(screen.getByRole("button", { name: "Editar e exportar →" }));
  expect(window.location.pathname).toBe("/episodios/ep/editor");
  expect(new URLSearchParams(window.location.search).get("clips")).toBe(`run~${K1},run2~3:300:360:balanced`);
});

test("the score badge is accented against the run's own scale", async () => {
  const big = [10, 20, 30, 40, 50].map(score => clipRow(score / 10, { score }));
  await open(big);
  const accented = document.querySelectorAll(".score.hi");
  expect(accented).toHaveLength(1);
  expect(accented[0].textContent).toBe("50");
  cleanup();
  await open([clipRow(1, { score: 8.5 }), clipRow(2, { score: 9 })]);
  expect(document.querySelectorAll(".score.hi")).toHaveLength(1);
});

test("the exported video uses the custom transport instead of native controls", async () => {
  await open([second]);
  const video = screen.getByLabelText("Vídeo exportado") as HTMLVideoElement;
  expect(video.hasAttribute("controls")).toBe(false);
  expect(screen.getByRole("slider", { name: "Posição no corte" })).toBeTruthy();
  expect(video.getAttribute("poster")).toContain("/renders/r1/poster");
  fireEvent.click(screen.getAllByRole("button", { name: "Reproduzir" })[0]);
  expect(play).toHaveBeenCalled();
});

test("saved reads Marcar salvo while off and Salvo with a check while on", async () => {
  await open([second]);
  const button = screen.getByRole("button", { name: "Marcar salvo" });
  expect(button.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(button);
  const on = screen.getByRole("button", { name: "Salvo" });
  expect(on.getAttribute("aria-pressed")).toBe("true");
  expect(on.querySelector("svg")).toBeTruthy();
});

test("publication is registered from the detail pane and the row becomes posted", async () => {
  await open([second]);
  fireEvent.click(screen.getByRole("button", { name: "Registrar publicação" }));
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByRole("button", { name: "Salvar publicação" })).toBeTruthy();
  vi.mocked(api.savePublication).mockResolvedValue({ id: "p", project_id: "project", render_artifact_id: "r1", published: true, platforms: ["instagram"], published_on: "2026-10-16", url: null } as never);
  fireEvent.click(within(dialog).getByLabelText(/Publicado/));
  fireEvent.click(within(dialog).getByRole("button", { name: "Salvar publicação" }));
  await settle();
  expect(api.savePublication).toHaveBeenCalledWith("project", "r1", expect.objectContaining({ published: true }));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(within(screen.getByRole("list", { name: "Lista de cortes" })).getByText("Postado")).toBeTruthy();
});
