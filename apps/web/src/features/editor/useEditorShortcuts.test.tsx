import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { actionFor, useEditorShortcuts, type EditorShortcuts } from "./useEditorShortcuts";

afterEach(cleanup);

function setup(enabled = true) {
  const shortcuts = { enabled, togglePlay: vi.fn(), skip: vi.fn(), moveClip: vi.fn(), exportAll: vi.fn() } satisfies EditorShortcuts;
  function Harness() {
    useEditorShortcuts(shortcuts);
    return <><input aria-label="campo" /><button type="button">botão</button></>;
  }
  return { shortcuts, view: render(<Harness />) };
}
const press = (key: string, init: KeyboardEventInit = {}, target: Element | Window = window) => fireEvent.keyDown(target, { key, ...init });

test("each key maps to its action", () => {
  const { shortcuts } = setup();
  press(" "); expect(shortcuts.togglePlay).toHaveBeenCalledTimes(1);
  press("j"); expect(shortcuts.skip).toHaveBeenLastCalledWith(-5);
  press("L"); expect(shortcuts.skip).toHaveBeenLastCalledWith(5);
  press("ArrowLeft"); expect(shortcuts.skip).toHaveBeenLastCalledWith(-1);
  press("ArrowRight"); expect(shortcuts.skip).toHaveBeenLastCalledWith(1);
  press("ArrowLeft", { shiftKey: true }); expect(shortcuts.skip).toHaveBeenLastCalledWith(-5);
  press("ArrowRight", { shiftKey: true }); expect(shortcuts.skip).toHaveBeenLastCalledWith(5);
  press("ArrowUp"); expect(shortcuts.moveClip).toHaveBeenLastCalledWith(-1);
  press("ArrowDown"); expect(shortcuts.moveClip).toHaveBeenLastCalledWith(1);
  press("Enter", { ctrlKey: true }); expect(shortcuts.exportAll).toHaveBeenCalledTimes(1);
  press("Enter", { metaKey: true }); expect(shortcuts.exportAll).toHaveBeenCalledTimes(2);
});

test("Enter alone, other modified keys and unknown keys do nothing", () => {
  const { shortcuts } = setup();
  press("Enter"); press("j", { ctrlKey: true }); press("l", { altKey: true }); press("q");
  expect(shortcuts.exportAll).not.toHaveBeenCalled();
  expect(shortcuts.skip).not.toHaveBeenCalled();
  expect(actionFor({ key: "x", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false }, shortcuts)).toBeNull();
});

test("typing in a field, an open dialog or a disabled editor ignores the shortcuts", () => {
  const { shortcuts, view } = setup();
  press("j", {}, view.getByLabelText("campo"));
  press("Enter", { ctrlKey: true }, view.getByLabelText("campo"));
  expect(shortcuts.skip).not.toHaveBeenCalled();
  expect(shortcuts.exportAll).not.toHaveBeenCalled();
  press(" ", {}, view.getByRole("button", { name: "botão" }));
  expect(shortcuts.togglePlay).toHaveBeenCalledTimes(1);
  cleanup();
  const open = setup();
  const dialog = document.createElement("div");
  dialog.setAttribute("role", "dialog");
  document.body.appendChild(dialog);
  press(" ");
  expect(open.shortcuts.togglePlay).not.toHaveBeenCalled();
  dialog.remove();
  cleanup();
  const off = setup(false);
  press(" ");
  expect(off.shortcuts.togglePlay).not.toHaveBeenCalled();
});
