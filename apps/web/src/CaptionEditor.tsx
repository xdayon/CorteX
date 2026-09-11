import {useEffect, useState, type ReactNode} from 'react';
import {CaptionLayer} from '../../remotion/src/CaptionLayer';
import type {RenderSettings} from './api';

type Captions = RenderSettings['captions'];
export function CaptionEditor({settings, onChange, image, disabled, controls}: {
  controls?: ReactNode; settings: RenderSettings; onChange: (patch: Partial<Captions>) => void; image?: string; disabled: boolean;
}) {
  const c = settings.captions;
  const [text, setText] = useState('Uma conversa que merece ser compartilhada');
  const [playing, setPlaying] = useState(true);
  const [time, setTime] = useState(0);
  const [fontError, setFontError] = useState('');
  const words = text.trim().split(/\s+/).filter(Boolean).slice(0, 60);
  const duration = Math.max(1, words.length * 0.45);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setTime(t => (t + 0.1) % duration), 100);
    return () => clearInterval(timer);
  }, [playing, duration]);
  useEffect(() => { setTime(0); }, [text]);
  useEffect(() => {
    let active = true;
    if (typeof FontFace === 'undefined') return;
    setFontError('');
    const weight = c.font_weight ?? 900;
    const font = new FontFace(`CorteX Preview ${c.font_family}`, `url(/api/v1/caption-fonts/${encodeURIComponent(c.font_family)}/${weight})`, {weight: String(weight)});
    void font.load().then(f => { if (active) document.fonts.add(f); }).catch(() => {
      if (active) setFontError('Não foi possível carregar a fonte da prévia. A aparência pode diferir do export.');
    });
    return () => { active = false; document.fonts.delete(font); };
  }, [c.font_family, c.font_weight]);
  const cues = [];
  for (let i = 0; i < words.length; i += c.words_per_cue) {
    const group = words.slice(i, i + c.words_per_cue);
    cues.push({start: i * .45, end: (i + group.length) * .45, words: group.map((text, j) => ({text, start: (i+j)*.45, end: (i+j+1)*.45}))});
  }
  const caption = {enabled:c.enabled, fontFamily:`"CorteX Preview ${c.font_family}", "${c.font_family}", sans-serif`, fontSize:c.font_size, fontWeight:c.font_weight ?? 900,
    uppercase:c.uppercase ?? true, positionY:c.position_y ?? .78, wordsPerCue:c.words_per_cue,
    outline:c.outline, shadow:c.shadow, karaoke:c.karaoke, textColor:c.text_color, karaokeColor:c.karaoke_color,
    outlineColor:c.outline_color, shadowColor:c.shadow_color, animation:{style:c.animation.style,durationSeconds:c.animation.duration_seconds}};
  return <div className="caption-editor">
    <fieldset disabled={disabled} className="caption-controls"><legend>Controle da legenda</legend>{controls}
      <label>Fonte<select value={c.font_family} onChange={e => onChange({font_family:e.target.value})}>{['Montserrat','Lato','DejaVu Sans'].map(f => <option key={f}>{f}</option>)}</select></label>
      <label>Posição vertical · {Math.round((c.position_y ?? .78)*100)}%<input aria-label="Posição vertical" type="range" min="10" max="90" value={Math.round((c.position_y ?? .78)*100)} onChange={e => onChange({position_y:Number(e.target.value)/100})}/></label>
      <label>Palavras por bloco · {c.words_per_cue}<input aria-label="Palavras por bloco" type="range" min="1" max="12" value={c.words_per_cue} onChange={e => onChange({words_per_cue:Number(e.target.value)})}/></label>
      <div className="caption-checks">{([
        ['enabled','Mostrar legenda'],['uppercase','Caixa alta'],['outline','Contorno'],['shadow','Sombra'],['karaoke','Destacar palavra ativa'],
      ] as const).map(([key,label]) => <label key={key}><input type="checkbox" checked={c[key] ?? true} onChange={e => onChange({[key]:e.target.checked})}/>{label}</label>)}
      <label><input type="checkbox" checked={(c.font_weight ?? 900)===900} onChange={e => onChange({font_weight:e.target.checked?900:400})}/>Negrito</label></div>
      <label>Animação<select value={c.animation.style} onChange={e => onChange({animation:{...c.animation,style:e.target.value as Captions['animation']['style']}})}><option value="none">Sem animação</option><option value="fade">Aparecer suave</option><option value="pop">Pulso na palavra ativa</option></select></label>
    </fieldset>
    <div className="caption-preview-panel"><h3>Prévia ao vivo</h3><p>Simulação da legenda sobre um frame do corte. O reenquadramento automático e a headline aparecem no vídeo exportado.</p>
      <div className="caption-preview" aria-label="Prévia da legenda em 1080 por 1920">
        <div className="caption-canvas">{image && <>{settings.framing.mode==='blurred_background' && <img className="preview-blur" src={image} alt=""/>}<img className={settings.framing.mode==='blurred_background'?'preview-contain':'preview-cover'} src={image} alt="Frame do corte selecionado"/></>}
          <CaptionLayer caption={caption} cues={cues} time={time} width={1080} height={1920}/>
        </div>
      </div>
      {fontError && <p role="status">{fontError}</p>}
      <button type="button" className="btn secondary" onClick={() => setPlaying(v=>!v)}>{playing?'Pausar prévia':'Reproduzir prévia'}</button>
      <label>Texto de teste (não altera a transcrição)<textarea maxLength={300} value={text} onChange={e => setText(e.target.value)}/></label>
    </div>
  </div>;
}
