import type { RenderSettings } from "../api";
import { Range, Toggle } from "./controls";
import type { SettingsUpdate } from "./StyleTab";

const MODES: Array<{ id: "blurred_background" | "vertical_crop"; name: string; text: string }> = [
  { id: "blurred_background", name: "Quadro com fundo desfocado", text: "O vídeo inteiro sobre um fundo desfocado, com zoom opcional." },
  { id: "vertical_crop", name: "Tela cheia", text: "Preenche a tela vertical pelo centro do vídeo." },
];

export function FramingTab({ settings, update, disabled }: { settings: RenderSettings; update: SettingsUpdate; disabled: boolean }) {
  const f = settings.framing;
  const patch = (change: Partial<RenderSettings["framing"]>) => update(s => ({ ...s, framing: { ...s.framing, ...change } }));
  return <fieldset disabled={disabled} className="tab-fieldset">
    <legend className="sr-only">Enquadramento</legend>
    <h3>Enquadramento</h3>
    <div className="card-grid" role="group" aria-label="Modo de enquadramento">
      {MODES.map(mode => <button key={mode.id} type="button" className={`choice-card ${f.mode === mode.id ? "selected" : ""}`}
        aria-pressed={f.mode === mode.id} onClick={() => patch({ mode: mode.id })}>
        <b>{mode.name}</b><span>{mode.text}</span></button>)}
    </div>
    {f.mode === "blurred_background" && <>
      <Range label="Zoom do vídeo" value={f.zoom} min={1} max={3.2} step={0.05} suffix="×" startLabel="Estúdio inteiro" endLabel="Tela cheia" onChange={zoom => patch({ zoom })}/>
      <Range label="Foco horizontal" value={Math.round(f.focus_x * 100)} min={0} max={100} suffix="%" startLabel="Esquerda" endLabel="Direita" onChange={value => patch({ focus_x: value / 100 })}/>
      <Range label="Posição vertical" value={Math.round((f.position_y ?? .5) * 100)} min={0} max={100} suffix="%" onChange={value => patch({ position_y: value / 100 })}/>
    </>}
    <Toggle checked={f.punch_in.enabled} label="Zoom alternado nos cortes"
      onChange={enabled => patch({ punch_in: { ...f.punch_in, enabled } })}/>
    <p className="field-help">O zoom alterna uma ampliação discreta entre trechos separados por cortes de pausa.</p>
  </fieldset>;
}
