import type { RenderSettings, SuggestedClip } from "../../api";
import { Button, Chip, Field, Segmented, Select, Slider, Switch, TextInput } from "../../ui";
import type { SettingsUpdate } from "./StyleTab";

type Duration = "4" | "8" | "all";

export function HeadlineTab({ clip, settings, update, headline, onHeadline, onTeaser, disabled }: {
  clip: SuggestedClip; settings: RenderSettings; update: SettingsUpdate; headline: string | undefined;
  onHeadline: (text: string | null) => void; onTeaser: (on: boolean) => void; disabled: boolean;
}) {
  const h = settings.headline;
  const aiHeadline = clip.headline || clip.title;
  const options = clip.headline_options?.length ? clip.headline_options : [aiHeadline];
  const text = headline ?? aiHeadline;
  const current = text.trim() || aiHeadline;
  const patch = (change: Partial<RenderSettings["headline"]>) => update(s => ({ ...s, headline: { ...s.headline, ...change } }));
  const duration: Duration | "" = h.persistent ? "all" : h.duration_seconds === 4 ? "4" : h.duration_seconds === 8 ? "8" : "";
  return <fieldset disabled={disabled} className="pane">
    <legend className="sr-only">Manchete</legend>
    <Switch checked={h.enabled} onChange={enabled => patch({ enabled })} label="Mostrar manchete" />
    <div className="field">
      <span className="lbl">Sugestões da IA</span>
      <div className="opts" role="radiogroup" aria-label="Sugestões da IA">
        {options.map((option, index) => <button key={option} type="button" role="radio" aria-checked={current === option} className={`opt${current === option ? " on" : ""}`}
          onClick={() => onHeadline(option === aiHeadline ? null : option)}>
          <span className="radio" /><span className="opt-t">{option}</span>{index === 0 && <Chip tone="ac">Recomendada</Chip>}
        </button>)}
      </div>
    </div>
    <Field label="Texto na tela" hint={`${text.length}/120`}>
      <TextInput maxLength={120} value={text} aria-label="Texto da manchete" onChange={event => onHeadline(event.target.value === aiHeadline ? null : event.target.value)} />
    </Field>
    {headline !== undefined && <Button size="sm" variant="ghost" className="reset" onClick={() => onHeadline(null)}>Usar a sugestão da IA</Button>}
    <div className="field">
      <span className="lbl">Tempo na tela</span>
      <Segmented<Duration | ""> label="Tempo na tela" value={duration} onChange={value => {
        if (value === "all") patch({ persistent: true }); else if (value) patch({ persistent: false, duration_seconds: Number(value) });
      }} options={[{ value: "4", label: "4 s" }, { value: "8", label: "8 s" }, { value: "all", label: "O corte todo" }]} />
    </div>
    <div className="divider" />
    <div>
      <Switch checked={settings.transitions.teaser && !!clip.teaser} disabled={!clip.teaser} label="Abrir com teaser" onChange={onTeaser} />
      <p className="field-help">{clip.teaser ? `Repete no início: “${clip.teaser.text}”` : "A IA não encontrou uma frase boa para abrir este corte."}</p>
    </div>
    <div className="divider" />
    <Field label="Entrada da manchete">
      <Select value={h.animation.entrance} onChange={event => patch({ animation: { ...h.animation, entrance: event.target.value as RenderSettings["headline"]["animation"]["entrance"] } })}>
        <option value="none">Sem animação</option><option value="fade">Aparecer suave</option><option value="slide">Deslizar</option>
      </Select>
    </Field>
    <Slider label="Tamanho da manchete" value={h.font_size} min={24} max={96} suffix=" px" onChange={font_size => patch({ font_size })} />
    <p className="field-help">Revise a acentuação e os nomes próprios.</p>
  </fieldset>;
}
