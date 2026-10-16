import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

const CloseContext = createContext<() => void>(() => undefined);

type TriggerProps = { onClick: () => void; "aria-expanded": boolean; "aria-haspopup": "menu"; "aria-controls": string };

export function Menu({ trigger, children, align = "right", label }: {
  trigger: (props: TriggerProps) => ReactNode; children: ReactNode; align?: "left" | "right"; label: string;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const id = useId();
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    wrap.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus({ preventScroll: true });
    function onPointer(event: MouseEvent) {
      if (!wrap.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        wrap.current?.querySelector<HTMLElement>("[aria-haspopup]")?.focus();
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        const items = [...(wrap.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? [])];
        if (!items.length) return;
        event.preventDefault();
        const index = items.indexOf(document.activeElement as HTMLElement);
        const next = event.key === "ArrowDown" ? (index + 1) % items.length : (index - 1 + items.length) % items.length;
        items[next].focus();
      }
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onPointer); document.removeEventListener("keydown", onKey); };
  }, [open]);

  return <div className="menu-wrap" ref={wrap}>
    {trigger({ onClick: () => setOpen(value => !value), "aria-expanded": open, "aria-haspopup": "menu", "aria-controls": id })}
    {open && <CloseContext.Provider value={close}>
      <div className={`menu ${align}`} role="menu" id={id} aria-label={label}>{children}</div>
    </CloseContext.Provider>}
  </div>;
}

export function MenuItem({ onSelect, icon, disabled, danger, children }: {
  onSelect: () => void; icon?: IconName; disabled?: boolean; danger?: boolean; children: ReactNode;
}) {
  const close = useContext(CloseContext);
  return <button type="button" role="menuitem" className={`menu-item${danger ? " danger" : ""}`} disabled={disabled}
    onClick={() => { close(); onSelect(); }}>{icon && <Icon name={icon} small />}{children}</button>;
}

export function MenuSeparator() { return <div className="menu-sep" role="separator" />; }
export function MenuLabel({ children }: { children: ReactNode }) { return <div className="menu-label">{children}</div>; }
