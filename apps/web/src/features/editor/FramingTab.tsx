import type { RenderSettings } from "../../api";
import { Icon, Slider, Switch } from "../../ui";
import type { SettingsUpdate } from "./StyleTab";

const MODES: Array<{ id: "blurred_background" | "vertical_crop"; name: string; pv: string }> = [
  { id: "blurred_background", name: "Quadro com fundo desfocado", pv: "" },
  { id: "vertical_crop", name: "Tela cheia", pv: "full" },
];

export function FramingTab({ settings, update, disabled }: { settings: RenderSettings; update: SettingsUpdate; disabled: boolean }) {
  const f = settings.framing;
  const patch = (change: Partial<RenderSettings["framing"]>) => update(s => ({ ...s, framing: { ...s.framing, ...change } }));
  return <fieldset disabled={disabled} className="pane">
    <legend className="sr-only">Enquadramento</legend>
    <div className="field">
      <span className="lbl">Formato</span>
      <div className="modes" role="group" aria-label="Modo de enquadramento">
        {MODES.map(mode => <button key={mode.id} type="button" className={`mode${f.mode === mode.id ? " on" : ""}`} aria-pressed={f.mode === mode.id} onClick={() => patch({ mode: mode.id })}>
          <span className={`mpv ${mode.pv}`} aria-hidden="true"><span className="mpv-band" /></span>
          <span className="pname">{mode.name}</span>
        </button>)}
      </div>
    </div>
    <div>
      <Switch checked={f.focus_mode === "faces"} label="Seguir rostos automaticamente" onChange={on => patch({ focus_mode: on ? "faces" : "manual" })} />
      <p className="field-help">Cada cena do corte é enquadrada em volta dos rostos detectados.</p>
    </div>
    {f.mode === "blurred_background" && <>
      <Slider label="Zoom do vídeo" value={f.zoom} min={1} max={3.2} step={0.05} suffix="×" startLabel="Estúdio inteiro" endLabel="Rosto" onChange={zoom => patch({ zoom })} />
      <Slider label={f.focus_mode === "faces" ? "Foco quando não houver rosto" : "Foco horizontal"} value={Math.round(f.focus_x * 100)} min={0} max={100} suffix="%" startLabel="Esquerda" endLabel="Direita" onChange={value => patch({ focus_x: value / 100 })} />
      <Slider label="Posição vertical" value={Math.round((f.position_y ?? .5) * 100)} min={0} max={100} suffix="%" onChange={value => patch({ position_y: value / 100 })} />
    </>}
    <div>
      <Switch checked={f.punch_in.enabled} label="Zoom alternado nos cortes" onChange={enabled => patch({ punch_in: { ...f.punch_in, enabled } })} />
      <p className="field-help">Amplia de leve a cada pausa removida.</p>
    </div>
    <p className="hint"><Icon name="info" small />Enquadramento gera uma nova prévia em segundos; legenda e manchete mudam na hora.</p>
  </fieldset>;
}
