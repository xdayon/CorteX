import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Timeline } from "./Timeline";
import type { SceneBlock } from "./timeline";

afterEach(cleanup);

const scenes: SceneBlock[] = [
  { id: "a", kind: "reaction", start: 10, duration: 2, state: "accepted" },
  { id: "b", kind: "wide", start: 30, duration: 3, state: "pending" },
];
const view = (onSeek = vi.fn()) => render(<Timeline total={60} time={0} onSeek={onSeek} trechos={[]} joins={[]} captions={[]} scenes={scenes} headline={{ on: false, seconds: null }} />);

test("the Cenas lane draws accepted inserts solid and pending suggestions dashed, positioned by output time", () => {
  view();
  expect(screen.getByText("Cenas")).toBeTruthy();
  const blocks = screen.getAllByTestId("scene-block");
  expect(blocks.map(block => block.getAttribute("data-state"))).toEqual(["accepted", "pending"]);
  expect(blocks[0].className).toContain("accepted");
  expect(blocks[1].className).toContain("pending");
  expect((blocks[1] as HTMLElement).style.left).toBe("50%");
  expect((blocks[1] as HTMLElement).style.width).toBe("5%");
});

test("clicking a scene block seeks to it without the lane seeking again", () => {
  const onSeek = vi.fn();
  view(onSeek);
  fireEvent.click(screen.getAllByTestId("scene-block")[1]);
  expect(onSeek).toHaveBeenCalledTimes(1);
  expect(onSeek).toHaveBeenCalledWith(30);
});
