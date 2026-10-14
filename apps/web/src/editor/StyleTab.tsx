import type { CaptionStyle, PieceJunction, RenderSettings } from "../api";
import { ColorField, Field, Range } from "./controls";

export type SettingsUpdate = (change: (settings: RenderSettings) => RenderSettings) => void;

const STYLES: Array<{ id: CaptionStyle; name: string; text: string }> = [
  { id: "classic", name: "Clássico", text: "Bloco de texto fixo, com a palavra ativa colorida." },
  { id: "word_reveal", name: "Palavra a palavra", text: "As palavras aparecem conforme são ditas." },
  { id: "pop", name: "Pop", text: "A palavra ativa dá um pulso e ganha cor." },
  { id: "box", name: "Caixa", text: "A palavra ativa recebe uma caixa de cor atrás." },
];
const JUNCTIONS: Array<{ id: PieceJunction; name: string; text: string }> = [
  { id: "cut", name: "Corte seco", text: "Emenda direta entre os trechos." },
  { id: "flash", name: "Flash", text: "Um clarão rápido esconde a emenda." },
  { id: "zoom", name: "Zoom", text: "Um zoom curto marca a emenda." },
];

export function StyleTab({ settings, update, disabled }: { settings: RenderSettings; update: SettingsUpdate; disabled: boolean }) {
  const c = settings.captions;
  const patch = (change: Partial<RenderSettings["captions"]>) => update(s => ({ ...s, captions: { ...s.captions, ...change } }));
  return <fieldset disabled={disabled} className="tab-fieldset">
    <legend className="sr-only">Estilo da legenda</legend>
    <h3>Estilo da legenda</h3>
    <div className="card-grid" role="group" aria-label="Estilo da legenda">
      {STYLES.map(style => <button key={style.id} type="button" className={`choice-card ${c.style === style.id ? "selected" : ""}`}
        aria-pressed={c.style === style.id} onClick={() => patch({ style: style.id })}>
        <b>{style.name}</b><span>{style.text}</span></button>)}
    </div>
    <div className="color-grid">
      <ColorField label="Texto" value={c.text_color} onChange={text_color => patch({ text_color })}/>
      <ColorField label="Palavra ativa" value={c.karaoke_color} onChange={karaoke_color => patch({ karaoke_color })}/>
      <ColorField label="Destaque" value={c.emphasis_color} onChange={emphasis_color => patch({ emphasis_color })}/>
      {c.style === "box" && <ColorField label="Texto sobre a caixa" value={c.box_text_color} onChange={box_text_color => patch({ box_text_color })}/>}
    </div>
    <Field label="Fonte"><select value={c.font_family} onChange={event => patch({ font_family: event.target.value })}><option>Montserrat</option></select></Field>
    <Range label="Tamanho" value={c.font_size} min={16} max={96} onChange={font_size => patch({ font_size })}/>
    <Range label="Posição vertical" value={Math.round((c.position_y ?? .64) * 100)} min={10} max={90} suffix="%" onChange={value => patch({ position_y: value / 100 })}/>
    <Range label="Palavras por bloco" value={c.words_per_cue} min={1} max={12} onChange={words_per_cue => patch({ words_per_cue })}/>
    <div className="check-grid">{([
      ["enabled", "Mostrar legenda"], ["uppercase", "Caixa alta"], ["outline", "Contorno"], ["shadow", "Sombra"],
    ] as const).map(([key, label]) => <label key={key}><input type="checkbox" checked={c[key] ?? true} onChange={event => patch({ [key]: event.target.checked })}/>{label}</label>)}
      <label><input type="checkbox" checked={(c.font_weight ?? 900) === 900} onChange={event => patch({ font_weight: event.target.checked ? 900 : 400 })}/>Negrito</label>
    </div>
    <h3>Transição entre trechos</h3>
    <div className="card-grid" role="group" aria-label="Transição entre trechos">
      {JUNCTIONS.map(item => <button key={item.id} type="button" className={`choice-card ${settings.transitions.piece_junction === item.id ? "selected" : ""}`}
        aria-pressed={settings.transitions.piece_junction === item.id}
        onClick={() => update(s => ({ ...s, transitions: { ...s.transitions, piece_junction: item.id } }))}>
        <b>{item.name}</b><span>{item.text}</span></button>)}
    </div>
  </fieldset>;
}
