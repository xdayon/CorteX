import "./PublicationPanel.css";
import { useCallback, useEffect, useState } from "react";
import { api, type Publication, type PublicationPlatform } from "./api";
import {
  buildPublicationPayload, formatViews, formFromPublication, RATING_LABEL,
  type PublicationFormState,
} from "./publicationForm";

export function usePublications() {
  const [items, setItems] = useState<Record<string, Publication>>({});
  useEffect(() => {
    let alive = true;
    Promise.resolve(api.publications()).then(list => {
      if (alive && Array.isArray(list)) setItems(Object.fromEntries(list.map(item => [item.render_artifact_id, item])));
    }).catch(() => undefined);
    return () => { alive = false; };
  }, []);
  const save = useCallback((item: Publication) => setItems(current => ({...current, [item.render_artifact_id]: item})), []);
  const remove = useCallback((artifactId: string) => setItems(current => {
    const next = {...current}; delete next[artifactId]; return next;
  }), []);
  return {items, save, remove};
}

export function PublicationBadges({publication}: {publication?: Publication | null}) {
  if (!publication) return null;
  return <span className="pub-badges">
    {publication.published && <b className="pub-badge on">publicado</b>}
    {publication.views != null && <b className="pub-badge">{formatViews(publication.views)} views</b>}
    {publication.rating != null && <b className={`pub-badge rating-${publication.rating}`}>nota: {RATING_LABEL[String(publication.rating) as keyof typeof RATING_LABEL]}</b>}
  </span>;
}

const PLATFORMS: Array<[PublicationPlatform, string]> = [["instagram", "Instagram"], ["tiktok", "TikTok"]];
const COUNTS = [["views", "Views"], ["likes", "Curtidas"], ["comments", "Comentários"], ["shares", "Compartilhamentos"], ["saves", "Salvamentos"]] as const;

export function PublicationPanel({projectId, artifactId, publication, hookText, onSaved, onDeleted}: {
  projectId: string; artifactId: string; publication?: Publication | null; hookText?: string;
  onSaved: (item: Publication) => void; onDeleted: (artifactId: string) => void;
}) {
  const [form, setForm] = useState<PublicationFormState>(() => formFromPublication(publication));
  const [errors, setErrors] = useState<string[]>([]);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  // The Library lists every saved video; mount the form only when opened.
  const [open, setOpen] = useState(false);
  useEffect(() => setForm(formFromPublication(publication)), [publication]);
  const set = <K extends keyof PublicationFormState>(key: K, value: PublicationFormState[K]) =>
    setForm(current => ({...current, [key]: value}));
  function togglePlatform(platform: PublicationPlatform) {
    set("platforms", form.platforms.includes(platform) ? form.platforms.filter(item => item !== platform) : [...form.platforms, platform]);
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const parsed = buildPublicationPayload(form, publication?.hook_text || hookText);
    if (!parsed.ok) { setErrors(parsed.errors); setStatus(""); return; }
    setErrors([]); setBusy(true); setStatus("");
    try {
      onSaved(await api.savePublication(projectId, artifactId, parsed.payload));
      setStatus("Publicação salva.");
    } catch (reason) { setErrors([String(reason)]); }
    finally { setBusy(false); }
  }
  async function clear() {
    setBusy(true); setErrors([]); setStatus("");
    try { await api.deletePublication(projectId, artifactId); onDeleted(artifactId); setForm(formFromPublication(null)); setStatus("Registro removido."); }
    catch (reason) { setErrors([String(reason)]); }
    finally { setBusy(false); }
  }
  return <details className="pub-panel" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Publicação <PublicationBadges publication={publication}/></summary>
    {open && <form onSubmit={event => void submit(event)} noValidate>
      <label className="pub-check"><input type="checkbox" checked={form.published} onChange={event => set("published", event.target.checked)}/> Publicado</label>
      <div className="pub-row">{PLATFORMS.map(([key, label]) => <label className="pub-check" key={key}><input type="checkbox" checked={form.platforms.includes(key)} onChange={() => togglePlatform(key)}/> {label}</label>)}</div>
      <label className="pub-field">Data da publicação<input type="date" value={form.published_on} onChange={event => set("published_on", event.target.value)}/></label>
      <label className="pub-field">Link<input type="url" inputMode="url" value={form.url} placeholder="https://" onChange={event => set("url", event.target.value)}/></label>
      <div className="pub-grid">{COUNTS.map(([key, label]) => <label className="pub-field" key={key}>{label}<input inputMode="numeric" value={form[key]} onChange={event => set(key, event.target.value)}/></label>)}
        <label className="pub-field">Retenção 3 s (%)<input inputMode="decimal" value={form.retention_3s_pct} onChange={event => set("retention_3s_pct", event.target.value)}/></label>
        <label className="pub-field">Tempo médio (s)<input inputMode="decimal" value={form.avg_watch_seconds} onChange={event => set("avg_watch_seconds", event.target.value)}/></label></div>
      <label className="pub-field">Nota<select value={form.rating} onChange={event => set("rating", event.target.value as PublicationFormState["rating"])}>
        <option value="">Sem nota</option><option value="1">Ótimo</option><option value="0">Ok</option><option value="-1">Ruim</option></select></label>
      <label className="pub-field">Notas<textarea rows={2} maxLength={300} value={form.notes} onChange={event => set("notes", event.target.value)}/></label>
      {!!errors.length && <ul role="alert" className="pub-errors">{errors.map(item => <li key={item}>{item}</li>)}</ul>}
      {status && <p role="status" className="pub-status">{status}</p>}
      <div className="pub-actions">
        <button className="btn primary" type="submit" disabled={busy}>Salvar publicação</button>
        {publication && <button className="btn secondary" type="button" disabled={busy} onClick={() => void clear()}>Remover registro</button>}
      </div>
    </form>}
  </details>;
}
