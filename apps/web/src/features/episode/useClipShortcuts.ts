import { useEffect, useRef } from "react";
import { isTyping } from "../../app/isTyping";

type Shortcuts = {
  enabled: boolean; move: (step: number) => void; togglePlay: () => void; select: () => void;
  approve: () => void; discard: () => void; save: () => void; posted: () => void; edit: () => void;
};

/** Keyboard shortcuts for the clip list; ignored while typing, inside dialogs or with modifier keys. */
export function useClipShortcuts(shortcuts: Shortcuts) {
  const latest = useRef(shortcuts);
  latest.current = shortcuts;
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const current = latest.current;
      if (!current.enabled || event.ctrlKey || event.metaKey || event.altKey) return;
      if (isTyping(event)) return;
      const target = event.target instanceof Element ? event.target : null;
      const key = event.key.toLowerCase();
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); current.move(event.key === "ArrowDown" ? 1 : -1); return; }
      if (event.key === " ") {
        if (target?.closest("button, a, [role='slider'], video, [role='checkbox']")) return;
        event.preventDefault(); current.togglePlay(); return;
      }
      const action = { x: current.select, a: current.approve, d: current.discard, s: current.save, p: current.posted, e: current.edit }[key];
      if (action) { event.preventDefault(); action(); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
