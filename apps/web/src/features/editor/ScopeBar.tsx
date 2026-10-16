import { Button, Segmented } from "../../ui";
import type { Scope } from "./useEditorSession";

/** "Aplicar em": whether an Estilo or Enquadramento change reaches every clip of the editor or only the focused one. */
export function ScopeBar({ scope, onScope, count, customized, onReset }: {
  scope: Scope; onScope: (scope: Scope) => void; count: number; customized: boolean; onReset: () => void;
}) {
  if (count < 2 && !customized) return null;
  return <div className="scope-wrap">
    <div className="scope">
      <span className="lbl">Aplicar em</span>
      <Segmented<Scope> label="Aplicar em" value={scope} onChange={onScope} options={[{ value: "clip", label: "Este corte" }, { value: "all", label: `Todos (${count})` }]} />
    </div>
    {customized && <div className="scope-note">
      <span>Este corte tem ajustes só dele.</span>
      <Button size="sm" variant="ghost" onClick={onReset}>Voltar ao padrão</Button>
    </div>}
  </div>;
}
