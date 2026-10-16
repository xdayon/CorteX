import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { IconButton } from "./Button";

type Tone = "neutral" | "ok" | "bad";
type ToastItem = { id: number; message: string; tone: Tone };
type Notify = (message: string, tone?: Tone) => void;

const ToastContext = createContext<Notify>(() => undefined);
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const counter = useRef(0);
  const timers = useRef(new Map<number, number>());
  const dismiss = useCallback((id: number) => {
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setItems(current => current.filter(item => item.id !== id));
  }, []);
  const notify = useCallback<Notify>((message, tone = "neutral") => {
    const id = ++counter.current;
    setItems(current => [...current.slice(-3), { id, message, tone }]);
    timers.current.set(id, window.setTimeout(() => dismiss(id), tone === "bad" ? 8000 : 4500));
  }, [dismiss]);
  useEffect(() => { const active = timers.current; return () => active.forEach(timer => window.clearTimeout(timer)); }, []);
  const value = useMemo(() => notify, [notify]);
  return <ToastContext.Provider value={value}>
    {children}
    <div className="toasts" role="region" aria-label="Avisos">
      {items.map(item => <div key={item.id} className={`toast ${item.tone === "neutral" ? "" : item.tone}`} role={item.tone === "bad" ? "alert" : "status"}>
        <p>{item.message}</p><IconButton icon="x" size="sm" aria-label="Dispensar aviso" onClick={() => dismiss(item.id)} />
      </div>)}
    </div>
  </ToastContext.Provider>;
}
