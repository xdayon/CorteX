import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { Dialog, Kbd } from "../ui";
import { isTyping } from "./isTyping";

const GROUPS: Array<{ title: string; items: Array<[string[], string]> }> = [
  { title: "Lista de cortes", items: [
    [["↑", "↓"], "Navegar entre os cortes"], [["Espaço"], "Reproduzir ou pausar"], [["X"], "Selecionar o corte"], [["A"], "Aprovar"],
    [["D"], "Descartar"], [["S"], "Marcar como salvo"], [["P"], "Marcar como postado"], [["E"], "Editar e exportar"],
  ] },
  { title: "Editor", items: [
    [["Espaço"], "Reproduzir ou pausar"], [["J", "L"], "Voltar ou avançar 5 segundos"], [["←", "→"], "Mover 1 segundo"], [["Shift", "←", "→"], "Mover 5 segundos"],
    [["↑", "↓"], "Trocar de corte"], [["Ctrl", "Enter"], "Exportar"],
  ] },
  { title: "Global", items: [[["?"], "Abrir esta lista de atalhos"]] },
];

const ShortcutsContext = createContext<() => void>(() => undefined);

/** Opens the shortcuts dialog from anywhere under the shell. */
export const useShortcutsDialog = () => useContext(ShortcutsContext);

export function ShortcutsProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const show = useCallback(() => setOpen(true), []);
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "?" || event.ctrlKey || event.metaKey || event.altKey || isTyping(event)) return;
      event.preventDefault();
      setOpen(true);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return <ShortcutsContext.Provider value={show}>
    {children}
    {open && <Dialog title="Atalhos do teclado" onClose={() => setOpen(false)}>
      <div className="keys">
        {GROUPS.map(group => <section key={group.title}>
          <h3>{group.title}</h3>
          <dl>{group.items.map(([keys, label]) => <div key={label} style={{ display: "contents" }}>
            <dt>{keys.map(key => <Kbd key={key}>{key}</Kbd>)}</dt><dd>{label}</dd>
          </div>)}</dl>
        </section>)}
      </div>
    </Dialog>}
  </ShortcutsContext.Provider>;
}
