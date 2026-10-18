import { useEffect, useRef } from "react";
import { Icon } from "../../ui";

const TYPING = "input, textarea, select, [contenteditable='true']";

/** Selects or clears every visible clip at once; IDs outside `ids` stay selected. */
export function toggleAll(selected: string[], ids: string[]) {
  const all = ids.length > 0 && ids.every(id => selected.includes(id));
  return all ? selected.filter(id => !ids.includes(id)) : [...new Set([...selected, ...ids])];
}

/** Tri-state "select all" for the visible clips; Ctrl/⌘+A does the same outside text fields. */
export function SelectAll({ ids, selected, onChange }: { ids: string[]; selected: string[]; onChange: (next: string[]) => void }) {
  const chosen = ids.filter(id => selected.includes(id)).length;
  const state = !chosen ? false : chosen === ids.length ? true : "mixed";
  const latest = useRef(() => onChange(toggleAll(selected, ids)));
  latest.current = () => onChange(toggleAll(selected, ids));
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== "a") return;
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest(TYPING) || document.querySelector('[role="dialog"], [role="menu"]')) return;
      event.preventDefault();
      latest.current();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  if (!ids.length) return null;
  const label = state === true ? "Desmarcar todos" : "Selecionar todos";
  return <div className="selall">
    <button type="button" role="checkbox" aria-checked={state} aria-label={`${label} os ${ids.length} cortes visíveis`} title={`${label} (Ctrl+A)`}
      className={`ck${state ? " on" : ""}`} onClick={() => latest.current()}>
      <span className="ckb"><Icon name={state === "mixed" ? "minus" : "check"} small /></span>
    </button>
    <button type="button" className="selall-label" tabIndex={-1} onClick={() => latest.current()}>
      {label} <span className="mono">{chosen ? `${chosen}/${ids.length}` : ids.length}</span>
    </button>
  </div>;
}
