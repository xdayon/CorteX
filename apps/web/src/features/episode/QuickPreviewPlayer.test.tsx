import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api } from "../../api";
import { clipRow } from "../../test/clipFixtures";
import { QuickPreviewPlayer } from "./QuickPreviewPlayer";
import { useQuickPreviewWarmup } from "./useQuickPreviewWarmup";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, api: { ...actual.api, warmQuickPreview: vi.fn() } };
});

const warm = vi.mocked(api.warmQuickPreview);
const settle = () => act(async () => {});
const urlOf = (rank: number) => api.quickPreviewUrl("project", "run", clipRow(rank).clip_key);

beforeEach(() => { warm.mockReset(); });
afterEach(cleanup);

test("a clip without exports plays the vertical quick preview, with junction segments from piece durations", async () => {
  warm.mockResolvedValue(undefined);
  render(<QuickPreviewPlayer row={clipRow(1)} />);
  expect(screen.getByText("Gerando prévia vertical…")).toBeTruthy();
  await settle();
  const video = screen.getByLabelText("Prévia rápida vertical") as HTMLVideoElement;
  expect(video.getAttribute("src")).toBe(urlOf(1));
  expect(video.getAttribute("src")).not.toContain("/sources/");
  expect(screen.getAllByTestId("segment")).toHaveLength(2);
  expect(screen.getByText("0:00 / 1:00")).toBeTruthy();
  expect(screen.getByText(/Prévia rápida · legenda e enquadramento por rosto no editor/)).toBeTruthy();
});

test("a failed render shows the error and retries on demand", async () => {
  warm.mockRejectedValueOnce(new Error("Falha ao gerar")).mockResolvedValue(undefined);
  render(<QuickPreviewPlayer row={clipRow(1)} />);
  await settle();
  expect(screen.getByRole("alert").textContent).toContain("Falha ao gerar");
  fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
  expect(screen.getByText("Gerando prévia vertical…")).toBeTruthy();
  await settle();
  expect(screen.getByLabelText("Prévia rápida vertical")).toBeTruthy();
  expect(warm).toHaveBeenCalledTimes(2);
});

function Warmup({ rows, active }: { rows: ReturnType<typeof clipRow>[]; active: string }) {
  useQuickPreviewWarmup(rows, active);
  return null;
}

test("warm-up requests the active clip then the next five without exports, one at a time, and aborts on unmount", async () => {
  const rows = [1, 2, 3, 4, 5, 6, 7, 8].map(rank => clipRow(rank));
  rows[2] = { ...rows[2], exports: [{} as never] };
  const resolvers: Array<() => void> = [];
  const signals: AbortSignal[] = [];
  warm.mockImplementation((_url, signal) => { signals.push(signal!); return new Promise<void>(resolve => resolvers.push(resolve)); });
  const { unmount } = render(<Warmup rows={rows} active={`${rows[1].run_id}~${rows[1].clip_key}`} />);
  await settle();
  expect(warm).toHaveBeenCalledTimes(1);
  expect(warm.mock.calls[0][0]).toBe(urlOf(2));
  for (let step = 0; step < 3; step++) { await act(async () => resolvers[step]()); }
  expect(warm.mock.calls.map(call => call[0])).toEqual([urlOf(2), urlOf(4), urlOf(5), urlOf(6)]);
  unmount();
  expect(signals.every(signal => signal.aborted)).toBe(true);
  await act(async () => resolvers[3]());
  expect(warm).toHaveBeenCalledTimes(4);
});
