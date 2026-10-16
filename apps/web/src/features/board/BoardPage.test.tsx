import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api } from "../../api";
import { boardRow, clipRow, withExport } from "../../test/clipFixtures";
import { ToastProvider } from "../../ui";
import { BoardPage } from "./BoardPage";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [
    name, vi.fn(name.endsWith("Url") ? fn : undefined),
  ])) };
});

const posted = { ...withExport(clipRow(1), "posted"), publication: { published: true } as never };
const rows = [
  boardRow(withExport(clipRow(2), "exported")),
  boardRow(withExport(clipRow(3, { state: { decision: null, discard_reason: null, saved: true, saved_at: "x", notes: "", updated_at: "x" } }), "saved"), "ep", "Episódio um"),
  boardRow(posted, "ep2", "Episódio dois"),
  boardRow(clipRow(4, { status: "approved" }), "ep2", "Episódio dois"),
  boardRow(clipRow(5, { status: "discarded" })),
];

async function settle() { await act(async () => {}); }
const titles = () => [...document.querySelectorAll(".brow-title")].map(node => node.textContent);

beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/cortes");
  vi.mocked(api.clips).mockResolvedValue({ clips: rows, total: rows.length });
  vi.mocked(api.publications).mockResolvedValue([]);
  vi.mocked(api.patchClipStates).mockResolvedValue({ items: [] });
  render(<ToastProvider><BoardPage /></ToastProvider>);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("the board opens on what is ready to post and summarises the library", async () => {
  await settle();
  expect(screen.getByText("2 prontos para postar · 1 salvos · 1 postados")).toBeTruthy();
  expect(titles().sort()).toEqual(["Manchete 2", "Manchete 3"]);
  expect(screen.getByRole("radio", { name: /Para postar/ }).getAttribute("aria-checked")).toBe("true");
});

test("tabs, episode filter and search narrow the rows", async () => {
  await settle();
  fireEvent.click(screen.getByRole("radio", { name: /Aprovados/ }));
  expect(titles()).toEqual(["Manchete 4"]);
  fireEvent.click(screen.getByRole("radio", { name: /Postados/ }));
  expect(titles()).toEqual(["Manchete 1"]);
  fireEvent.click(screen.getByRole("radio", { name: /Descartados/ }));
  expect(titles()).toEqual(["Manchete 5"]);
  fireEvent.click(screen.getByRole("radio", { name: /Todos/ }));
  expect(titles()).toHaveLength(5);
  fireEvent.change(screen.getByLabelText("Episódio"), { target: { value: "ep2" } });
  expect(titles().sort()).toEqual(["Manchete 1", "Manchete 4"]);
  fireEvent.change(screen.getByLabelText("Buscar corte"), { target: { value: "manchete 4" } });
  expect(titles()).toEqual(["Manchete 4"]);
});

test("rows link back to the episode, toggle saved optimistically and support bulk actions", async () => {
  await settle();
  const row = screen.getByText("Manchete 2").closest("li")!;
  const link = within(row).getByRole("link", { name: "Abrir no episódio" });
  expect(new URL(link.getAttribute("href")!, "http://x").pathname).toBe("/episodios/ep");
  expect(decodeURIComponent(link.getAttribute("href")!)).toContain("clip=run~2:200:260:balanced");
  fireEvent.click(within(row).getByRole("button", { name: "Marcar salvo" }));
  expect(api.patchClipStates).toHaveBeenLastCalledWith([expect.objectContaining({ clip_key: "2:200:260:balanced", saved: true })]);
  expect(screen.getByText("2 prontos para postar · 2 salvos · 1 postados")).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox", { name: "Selecionar Manchete 2" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Selecionar Manchete 3" }));
  const bar = screen.getByRole("region", { name: "Cortes selecionados" });
  expect(within(bar).getByRole("button", { name: "Baixar (2 MP4)" })).toBeTruthy();
  fireEvent.click(within(bar).getByRole("button", { name: "Marcar salvos" }));
  expect(vi.mocked(api.patchClipStates).mock.lastCall![0].every(item => item.saved === false)).toBe(true);
});
