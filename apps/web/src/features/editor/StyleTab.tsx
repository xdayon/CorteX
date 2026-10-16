import type { CaptionStyle, PieceJunction, RenderSettings } from "../../api";
import { Field, Segmented, Select, Slider, Switch } from "../../ui";

export type SettingsUpdate = (change: (settings: RenderSettings) => RenderSettings) => void;

const STYLES: Array<{ id: CaptionStyle; name: string; pv: string }> = [
  { id: "classic", name: "Clássico", pv: "" }, { id: "word_reveal", name: "Palavra a palavra", pv: "word" },
  { id: "pop", name: "Pop", pv: "pop" }, { id: "box", name: "Caixa", pv: "box" },
];
const JUNCTIONS: Array<{ value: PieceJunction; label: string }> = [
  { value: "cut", label: "Corte seco" }, { value: "flash", label: "Flash" }, { value: "zoom", label: "Zoom" },
];

function Swatch({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const rgb = /^#[0-9a-fA-F]{6}/.test(value) ? value.slice(0, 7) : "#FFFFFF";
  return <label className="swi">
    <input type="color" value={rgb} aria-label={`Cor: ${label}`} onChange={event => onChange(value.length === 9 ? `${event.target.value.toUpperCase()}${value.slice(7)}` : event.target.value.toUpperCase())} />
    <span className="dot" style={{ background: rgb }} />{label}
  </label>;
}

export function StyleTab({ settings, update, disabled }: { settings: RenderSettings; update: SettingsUpdate; disabled: boolean }) {
  const c = settings.captions;
  const patch = (change: Partial<RenderSettings["captions"]>) => update(s => ({ ...s, captions: { ...s.captions, ...change } }));
  return <fieldset disabled={disabled} className="pane">
    <legend className="sr-only">Estilo da legenda</legend>
    <div className="field">
      <span className="lbl">Estilo da legenda</span>
      <div className="presets" role="group" aria-label="Estilo da legenda">
        {STYLES.map(style => <button key={style.id} type="button" className={`preset${c.style === style.id ? " on" : ""}`} aria-pressed={c.style === style.id} onClick={() => patch({ style: style.id })}>
          <span className={`pv ${style.pv}`} aria-hidden="true"><span>COMO</span><span className="a">UM</span><span className="dim">BOM</span></span>
          <span className="pname">{style.name}</span>
        </button>)}
      </div>
    </div>
    <div className="field">
      <span className="lbl">Cores</span>
      <div className="swatches">
        <Swatch label="Texto" value={c.text_color} onChange={text_color => patch({ text_color })} />
        <Swatch label="Ativa" value={c.karaoke_color} onChange={karaoke_color => patch({ karaoke_color })} />
        <Swatch label="Destaque" value={c.emphasis_color} onChange={emphasis_color => patch({ emphasis_color })} />
      </div>
      {c.style === "box" && <div className="swatches"><Swatch label="Texto sobre a caixa" value={c.box_text_color} onChange={box_text_color => patch({ box_text_color })} /></div>}
    </div>
    <Field label="Fonte"><Select value={c.font_family} onChange={event => patch({ font_family: event.target.value })}><option>Montserrat</option></Select></Field>
    <Slider label="Tamanho" value={c.font_size} min={16} max={96} suffix=" px" onChange={font_size => patch({ font_size })} />
    <Slider label="Altura na tela" value={Math.round((c.position_y ?? .64) * 100)} min={10} max={90} suffix="%" onChange={value => patch({ position_y: value / 100 })} />
    <Slider label="Palavras por bloco" value={c.words_per_cue} min={1} max={12} onChange={words_per_cue => patch({ words_per_cue })} />
    <div className="switches">{([
      ["enabled", "Mostrar legenda"], ["uppercase", "Caixa alta"], ["outline", "Contorno"], ["shadow", "Sombra"],
    ] as const).map(([key, label]) => <Switch key={key} label={label} checked={c[key] ?? true} onChange={value => patch({ [key]: value })} />)}
      <Switch label="Negrito" checked={(c.font_weight ?? 900) === 900} onChange={value => patch({ font_weight: value ? 900 : 400 })} />
    </div>
    <div className="field">
      <span className="lbl">Transição entre trechos</span>
      <Segmented<PieceJunction> label="Transição entre trechos" value={settings.transitions.piece_junction} options={JUNCTIONS}
        onChange={piece_junction => update(s => ({ ...s, transitions: { ...s.transitions, piece_junction } }))} />
    </div>
  </fieldset>;
}
