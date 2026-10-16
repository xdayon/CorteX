import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import type { CaptionCorrection, OverlayPreview, SuggestedClip } from "../../api";
import type { IndexedWord } from "../../editor/captions";
import { CaptionTab } from "./CaptionTab";
import { formatBlockTime } from "./timecode";

afterEach(cleanup);

const word = (index: number, text: string, start: number, extra: Record<string, unknown> = {}) =>
  ({ index, text: text.toUpperCase(), start, end: start + 0.4, emphasis: false, original: text, probability: 0.95, ...extra });
const overlay = {
  ass_text: "", pages: [
    { start: 0, end: 1.2, lines: [[word(0, "o", 0), word(1, "cortecs", 0.4, { probability: 0.3 })]] },
    { start: 1.2, end: 2.4, lines: [[word(2, "é", 1.2), word(3, "bom", 1.6)]] },
  ], junctions: [], fonts: [],
} as unknown as OverlayPreview;
const clip = { rank: 1, title: "c", start_second: 0, end_second: 3, pacing: "balanced", emphasis_word_indices: [] } as unknown as SuggestedClip;
const target = { key: "run~1:0:3:balanced", clip };
const transcript: IndexedWord[] = [
  { index: 0, word: "o", start: 0, end: 0.4 }, { index: 1, word: "cortecs", start: 0.4, end: 0.8 },
  { index: 2, word: "é", start: 1.2, end: 1.6 }, { index: 3, word: "bom", start: 1.6, end: 2 },
];

function Harness({ time = 0, onSeek = vi.fn() }: { time?: number; onSeek?: (seconds: number) => void }) {
  const [corrections, setCorrections] = useState<CaptionCorrection[]>([]);
  const [emphasis, setEmphasis] = useState<number[]>([]);
  return <>
    <CaptionTab current={target} targets={[target]} overlay={overlay} loading={false} error="" time={time} onSeek={onSeek}
      corrections={corrections} onCorrections={setCorrections} emphasis={emphasis} onEmphasis={setEmphasis}
      captionEdits={{ [target.key]: corrections }} onAllCaptionEdits={all => setCorrections(all[target.key] ?? [])}
      words={transcript} wordsError="" disabled={false}/>
    <output data-testid="state">{JSON.stringify({ corrections, emphasis })}</output>
  </>;
}
const state = () => JSON.parse(screen.getByTestId("state").textContent!);

test("formats block times as mm:ss.s", () => {
  expect(formatBlockTime(65.34)).toBe("01:05.3");
  expect(formatBlockTime(0)).toBe("00:00.0");
});

test("the block under the playhead is highlighted and its time seeks the video", () => {
  const onSeek = vi.fn();
  render(<Harness time={1.5} onSeek={onSeek}/>);
  const blocks = document.querySelectorAll(".blk");
  expect(blocks[1].classList.contains("cur")).toBe(true);
  expect(blocks[0].classList.contains("cur")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Ir para 00:01.2" }));
  expect(onSeek).toHaveBeenCalledWith(1.2);
});

test("clicking a word edits it: Enter saves, Escape cancels, equal text removes the correction", () => {
  render(<Harness/>);
  fireEvent.click(screen.getByRole("button", { name: "bom" }));
  const input = screen.getByRole("textbox", { name: "Texto da palavra" });
  expect((input as HTMLInputElement).value).toBe("bom");
  fireEvent.change(input, { target: { value: "bão" } });
  fireEvent.keyDown(input, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(state().corrections).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "bom" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Texto da palavra" }), { target: { value: "bão" } });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Texto da palavra" }), { key: "Enter" });
  expect(state().corrections).toEqual([{ word_index: 3, original: "bom", text: "bão" }]);
  fireEvent.click(screen.getByRole("button", { name: "bão" }));
  expect((screen.getByRole("textbox", { name: "Texto da palavra" }) as HTMLInputElement).value).toBe("bão");
  fireEvent.change(screen.getByRole("textbox", { name: "Texto da palavra" }), { target: { value: "bom" } });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Texto da palavra" }), { key: "Enter" });
  expect(state().corrections).toEqual([]);
});

test("the popover highlights, hides and restores words", () => {
  render(<Harness/>);
  fireEvent.click(screen.getByRole("button", { name: "o" }));
  fireEvent.click(screen.getByRole("button", { name: "★ Destacar" }));
  expect(state().emphasis).toEqual([0]);
  expect(screen.getByRole("button", { name: "Remover destaque" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Ocultar palavra" }));
  expect(state().corrections).toEqual([{ word_index: 0, original: "o", text: "" }]);
  fireEvent.click(screen.getByRole("button", { name: "Restaurar o" }));
  expect(state().corrections).toEqual([]);
});

test("doubtful words are underlined and the review button steps through them", () => {
  const onSeek = vi.fn();
  render(<Harness onSeek={onSeek}/>);
  expect(document.querySelectorAll(".w.doubt")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Revisar 1 palavra duvidosa" }));
  expect(onSeek).toHaveBeenCalledWith(0.4);
  expect(screen.getByRole("dialog", { name: "Editar a palavra cortecs" })).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox", { name: "Texto da palavra" }), { target: { value: "CorteX" } });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Texto da palavra" }), { key: "Enter" });
  expect(screen.queryByRole("button", { name: /Revisar/ })).toBeNull();
});

test("find and replace reports how many words changed", () => {
  render(<Harness/>);
  expect(screen.queryByRole("textbox", { name: "Substituir por" })).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Localizar" }), { target: { value: "COrtecs" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Substituir por" }), { target: { value: "CorteX" } });
  fireEvent.click(screen.getByRole("button", { name: "Substituir" }));
  expect(screen.getByText("1 palavra substituída.")).toBeTruthy();
  expect(state().corrections).toEqual([{ word_index: 1, original: "cortecs", text: "CorteX" }]);
});

test("the filter keeps only blocks with doubtful or highlighted words, with counts", () => {
  render(<Harness/>);
  expect(document.querySelectorAll(".blk")).toHaveLength(2);
  fireEvent.click(screen.getByRole("radio", { name: /Duvidosas/ }));
  expect(document.querySelectorAll(".blk")).toHaveLength(1);
  expect(screen.getByRole("radio", { name: /Duvidosas/ }).textContent).toContain("1");
  fireEvent.click(screen.getByRole("radio", { name: /Destaques/ }));
  expect(screen.getByText("Nenhum bloco neste filtro.")).toBeTruthy();
});

test("Shift + click toggles the highlight without opening the editor, and words show in natural case", () => {
  render(<Harness/>);
  expect(screen.getByRole("button", { name: "cortecs" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "bom" }), { shiftKey: true });
  expect(state().emphasis).toEqual([3]);
  expect(screen.queryByRole("dialog")).toBeNull();
});
