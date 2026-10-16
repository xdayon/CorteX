import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { locate, SourcePiecesPlayer, stepPieces } from "./SourcePiecesPlayer";

const pieces = [{ start: 10, end: 20 }, { start: 50, end: 60 }];

test("stepPieces stays inside a piece, jumps to the next start at the end and finishes after the last", () => {
  expect(stepPieces(pieces, 0, 15)).toEqual({ index: 0, seek: null, ended: false });
  expect(stepPieces(pieces, 0, 19.98)).toEqual({ index: 1, seek: 50, ended: false });
  expect(stepPieces(pieces, 1, 59.99)).toEqual({ index: 1, seek: null, ended: true });
  expect(stepPieces([{ start: 0, end: 5 }, { start: 5.01, end: 9 }], 0, 4.99)).toEqual({ index: 1, seek: null, ended: false });
});

test("locate maps the concatenated timeline back to a piece and a media time", () => {
  expect(locate(pieces, 0)).toEqual({ index: 0, media: 10 });
  expect(locate(pieces, 9)).toEqual({ index: 0, media: 19 });
  expect(locate(pieces, 10)).toEqual({ index: 1, media: 50 });
  expect(locate(pieces, 15)).toEqual({ index: 1, media: 55 });
  expect(locate(pieces, 99)).toEqual({ index: 1, media: 60 });
});

const play = vi.fn(() => Promise.resolve());
const pause = vi.fn();

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame"] });
  Object.defineProperty(HTMLMediaElement.prototype, "currentTime", {
    configurable: true,
    get(this: { _t?: number }) { return this._t ?? 0; },
    set(this: { _t?: number }, value: number) { this._t = value; },
  });
  Object.defineProperty(HTMLMediaElement.prototype, "play", { configurable: true, value: play });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", { configurable: true, value: pause });
  play.mockClear(); pause.mockClear();
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

const frame = () => act(async () => { await vi.advanceTimersByTimeAsync(20); });

test("playing starts at the first piece, jumps over the gap and stops at the end of the last piece", async () => {
  render(<SourcePiecesPlayer src="/media" pieces={pieces} />);
  const video = screen.getByLabelText("Prévia do corte original") as HTMLVideoElement;
  expect(screen.getByText("0:00 / 0:20")).toBeTruthy();
  expect(screen.getAllByTestId("segment")).toHaveLength(2);
  fireEvent.click(screen.getAllByRole("button", { name: "Reproduzir" })[0]);
  expect(play).toHaveBeenCalled();
  expect(video.currentTime).toBe(10);
  video.currentTime = 15; await frame();
  expect(screen.getByText("0:05 / 0:20")).toBeTruthy();
  video.currentTime = 19.99; await frame();
  expect(video.currentTime).toBe(50);
  expect(screen.getByText("0:10 / 0:20")).toBeTruthy();
  video.currentTime = 54; await frame();
  expect(screen.getByText("0:14 / 0:20")).toBeTruthy();
  video.currentTime = 59.99; await frame();
  expect(pause).toHaveBeenCalled();
  expect(screen.getByText("0:20 / 0:20")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "Reproduzir" }).length).toBeGreaterThan(0);
});

test("clicking the segmented bar seeks across pieces, and arrow keys move five seconds", async () => {
  render(<SourcePiecesPlayer src="/media" pieces={pieces} />);
  const video = screen.getByLabelText("Prévia do corte original") as HTMLVideoElement;
  const bar = screen.getByRole("slider", { name: "Posição no corte" });
  bar.getBoundingClientRect = () => ({ left: 0, width: 200, top: 0, height: 10, right: 200, bottom: 10, x: 0, y: 0, toJSON: () => ({}) });
  fireEvent.click(bar, { clientX: 150 });
  expect(video.currentTime).toBe(55);
  expect(screen.getByText("0:15 / 0:20")).toBeTruthy();
  fireEvent.keyDown(bar, { key: "ArrowLeft" });
  expect(video.currentTime).toBe(50);
  fireEvent.keyDown(bar, { key: "ArrowLeft" });
  expect(video.currentTime).toBe(15);
  expect(screen.getByText("0:05 / 0:20")).toBeTruthy();
});
