import { useEffect, useRef } from "react";

export type EditorShortcuts = {
  enabled: boolean; togglePlay: () => void; skip: (seconds: number) => void; moveClip: (step: number) => void; exportAll: () => void;
};

const TYPING = "input, textarea, select, [contenteditable='true']";

/** What a key press asks the editor to do, or null; the caller has already ruled out typing and open dialogs. */
export function actionFor(event: Pick<KeyboardEvent, "key" | "shiftKey" | "ctrlKey" | "metaKey" | "altKey">, shortcuts: EditorShortcuts) {
  if (event.altKey) return null;
  if (event.ctrlKey || event.metaKey) return event.key === "Enter" ? shortcuts.exportAll : null;
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  switch (key) {
    case " ": return shortcuts.togglePlay;
    case "j": return () => shortcuts.skip(-5);
    case "l": return () => shortcuts.skip(5);
    case "ArrowLeft": return () => shortcuts.skip(event.shiftKey ? -5 : -1);
    case "ArrowRight": return () => shortcuts.skip(event.shiftKey ? 5 : 1);
    case "ArrowUp": return () => shortcuts.moveClip(-1);
    case "ArrowDown": return () => shortcuts.moveClip(1);
    default: return null;
  }
}

/** Editor keyboard shortcuts; ignored while typing or with a dialog or menu open. */
export function useEditorShortcuts(shortcuts: EditorShortcuts) {
  const latest = useRef(shortcuts);
  latest.current = shortcuts;
  const swallowKeyup = useRef(false);
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const current = latest.current;
      if (!current.enabled) return;
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest(TYPING) || document.querySelector('[role="dialog"], [role="menu"]')) return;
      const action = actionFor(event, current);
      if (!action) return;
      event.preventDefault();
      // A focused button would also "click" on Space release: swallow that so play/pause fires once.
      if (event.key === " ") swallowKeyup.current = true;
      action();
    }
    function onKeyup(event: KeyboardEvent) {
      if (event.key === " " && swallowKeyup.current) { swallowKeyup.current = false; event.preventDefault(); }
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKeyup);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("keyup", onKeyup); };
  }, []);
}
