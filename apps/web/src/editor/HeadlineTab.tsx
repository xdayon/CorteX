import type { RenderSettings, SuggestedClip } from "../api";
import { Field, Range, Toggle } from "./controls";
import type { SettingsUpdate } from "./StyleTab";

export function HeadlineTab({ clip, settings, update, headline, onHeadline, disabled }: {
  clip: SuggestedClip; settings: RenderSettings; update: SettingsUpdate;
  headline: string; onHeadline: (text: string | null) => void; disabled: boolean;
}) {
  const h = settings.headline;
  const options = clip.headline_options?.length ? clip.headline_options : [clip.headline || clip.title];
  const aiHeadline = clip.headline || clip.title;
  const current = headline.trim() || aiHeadline;
  const patch = (change: Partial<RenderSettings["headline"]>) => update(s => ({ ...s, headline: { ...s.headline, ...change } }));
  return <fieldset disabled={disabled} className="tab-fieldset">
    <legend className="sr-only">Manchete</legend>
    <h3>Manchete deste corte</h3>
    <Toggle checked={h.enabled} onChange={enabled => patch({ enabled })} label="Mostrar manchete"/>
    <div className="chip-row" role="group" aria-label="Sugestões da IA">
      {options.map((option, index) => <button key={option} type="button" className={`chip ${current === option ? "selected" : ""}`}
        aria-pressed={current === option} onClick={() => onHeadline(option === aiHeadline ? null : option)}>
        {index === 0 && <small>Recomendada</small>}{option}</button>)}
    </div>
    <Field label="Texto da manchete" hint={`${headline.length}/120`}>
      <textarea maxLength={120} value={headline} placeholder={aiHeadline} onChange={event => onHeadline(event.target.value)}/>
    </Field>
    <button type="button" className="btn secondary" disabled={!headline} onClick={() => onHeadline(null)}>Usar a sugestão da IA</button>
    <Toggle checked={h.persistent} onChange={persistent => patch({ persistent })} label="Manchete durante todo o corte"/>
    <Field label="Entrada">
      <select value={h.animation.entrance} onChange={event => patch({ animation: { ...h.animation, entrance: event.target.value as RenderSettings["headline"]["animation"]["entrance"] } })}>
        <option value="none">Sem animação</option><option value="fade">Aparecer suave</option><option value="slide">Deslizar</option>
      </select>
    </Field>
    <Range label="Tamanho" value={h.font_size} min={24} max={96} onChange={font_size => patch({ font_size })}/>
    {!h.persistent && <Range label="Tempo na tela" value={h.duration_seconds} min={1} max={10} suffix="s" onChange={duration_seconds => patch({ duration_seconds })}/>}
    <p className="field-help">Revise a acentuação e os nomes próprios.</p>
    <h3>Gancho</h3>
    <Toggle checked={settings.transitions.teaser && !!clip.teaser} disabled={!clip.teaser}
      onChange={teaser => update(s => ({ ...s, transitions: { ...s.transitions, teaser } }))} label="Abrir com teaser"/>
    {clip.teaser
      ? <p className="field-help">Repete no início a frase: “{clip.teaser.text}”</p>
      : <p className="field-help">A IA não encontrou uma frase boa para abrir este corte.</p>}
  </fieldset>;
}
