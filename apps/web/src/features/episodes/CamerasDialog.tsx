import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiJob, type CameraGroup, type CameraRole, type EpisodeCameras, type EpisodeEntry } from "../../api";
import { errorText } from "../../app/humanize";
import "./episodes.css";
import { Button, Chip, Dialog, Segmented } from "../../ui";

const ROLES: Array<{ value: CameraRole; label: string }> = [
  { value: "subject", label: "Você" },
  { value: "interviewers", label: "Entrevistadores" },
  { value: "wide", label: "Plano aberto" },
  { value: "ignore", label: "Ignorar" },
];
const RARE_SHARE = 0.01;
const FINISHED = ["succeeded", "failed", "cancelled", "canceled"];

type Choice = CameraRole | "";
type Picks = Record<number, { role: Choice; suggested: boolean }>;

function initialPicks(data: EpisodeCameras): Picks {
  const picks: Picks = {};
  for (const group of data.document?.groups ?? []) {
    const saved = data.roles?.[String(group.id)];
    if (saved) picks[group.id] = { role: saved, suggested: false };
    else if (group.airtime_share < RARE_SHARE) picks[group.id] = { role: "ignore", suggested: false };
    else if (group.suggested_role) picks[group.id] = { role: group.suggested_role, suggested: true };
    else picks[group.id] = { role: "", suggested: false };
  }
  return picks;
}

const percent = (value: number) => `${Math.round(value * 100)}%`;

export function CamerasDialog({ entry, onClose }: { entry: EpisodeEntry; onClose: () => void }) {
  const [data, setData] = useState<EpisodeCameras | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [picks, setPicks] = useState<Picks>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(async (signal: AbortSignal) => {
    const next = await api.episodeCameras(entry.id, signal);
    if (signal.aborted) return null;
    setData(next);
    setPicks(next ? initialPicks(next) : {});
    setSaved(false);
    return next;
  }, [entry.id]);

  const track = useCallback(async (job: ApiJob, signal: AbortSignal) => {
    setWorking(true);
    setMessage(job.message || "Mapeando câmeras…");
    try {
      const done = await api.watchJob(job.id, update => setMessage(update.message || "Mapeando câmeras…"), signal);
      if (signal.aborted) return;
      if (done.status !== "succeeded") throw new Error(done.error || done.message || "O mapa de câmeras não terminou.");
      await load(signal);
    } catch (reason) {
      if (!signal.aborted) setError(errorText(reason));
    } finally {
      if (!signal.aborted) { setWorking(false); setMessage(""); }
    }
  }, [load]);

  useEffect(() => {
    const own = new AbortController();
    controller.current = own;
    (async () => {
      try {
        const first = await load(own.signal);
        if (first?.job && !FINISHED.includes(first.job.status)) await track(first.job, own.signal);
      } catch (reason) {
        if (!own.signal.aborted) setError(errorText(reason));
      } finally {
        if (!own.signal.aborted) setLoading(false);
      }
    })();
    return () => own.abort();
  }, [load, track]);

  async function map() {
    controller.current?.abort();
    const own = new AbortController();
    controller.current = own;
    setError(""); setSaved(false); setWorking(true); setMessage("Enviando mapa de câmeras para a fila…");
    try {
      const job = await api.mapEpisodeCameras(entry.id);
      await track(job, own.signal);
    } catch (reason) {
      if (!own.signal.aborted) { setError(errorText(reason)); setWorking(false); setMessage(""); }
    }
  }

  const groups = data?.document?.groups ?? [];
  const main = groups.filter(group => group.airtime_share >= RARE_SHARE);
  const rare = groups.filter(group => group.airtime_share < RARE_SHARE);
  const missing = main.some(group => !picks[group.id]?.role);

  async function save() {
    const artifact = data?.document?.artifact_id;
    if (!artifact) return;
    const roles: Record<string, CameraRole> = {};
    for (const group of groups) { const role = picks[group.id]?.role; if (role) roles[String(group.id)] = role; }
    setSaving(true); setError(""); setSaved(false);
    try {
      await api.saveCameraRoles(entry.id, artifact, roles);
      setPicks(current => Object.fromEntries(Object.entries(current).map(([id, pick]) => [id, { ...pick, suggested: false }])));
      setSaved(true);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setSaving(false);
    }
  }

  function card(group: CameraGroup) {
    const pick = picks[group.id] ?? { role: "" as Choice, suggested: false };
    return <li key={group.id} className="cam-card">
      <img src={group.thumbnail_url} alt={`Câmera ${group.id + 1}`} loading="lazy" />
      <div className="cam-info">
        <span>{percent(group.airtime_share)} no ar · {group.shot_count} planos</span>
        {group.subject_voice_share !== null && <span className="cam-voice">sua voz em {percent(group.subject_voice_share)}</span>}
      </div>
      <Segmented<Choice> label={`Função da câmera ${group.id + 1}`} value={pick.role} options={ROLES}
        onChange={role => { setSaved(false); setPicks(current => ({ ...current, [group.id]: { role, suggested: false } })); }} />
      {pick.suggested && <Chip tone="ac">sugestão</Chip>}
    </li>;
  }

  const summary = data?.document;
  const empty = !loading && !working && !summary;
  return <Dialog wide title="Câmeras do episódio" onClose={onClose} footer={<>
    {summary && <>
      <Button variant="primary" disabled={saving || working || missing} onClick={() => void save()}>Salvar câmeras</Button>
      <Button variant="ghost" disabled={saving || working} onClick={() => void map()}>Refazer mapa</Button>
    </>}
    <Button onClick={onClose}>Fechar</Button>
  </>}>
    <div className="cam">
      {loading && <p role="status">Carregando câmeras…</p>}
      {working && <p role="status">{message}</p>}
      {empty && <>
        <p>O CorteX agrupa os planos do episódio por câmera para montar a triangulação: você, os entrevistadores e o plano aberto. Leva cerca de 2 a 5 minutos.</p>
        <div><Button variant="primary" onClick={() => void map()}>Mapear câmeras</Button></div>
      </>}
      {summary && !working && <>
        {summary.voice.status !== "ok" && <p className="cam-note">Sem o reconhecimento de voz, as sugestões de você/entrevistadores ficam em branco.{summary.voice.reason ? ` (${summary.voice.reason})` : ""}</p>}
        <ul className="cam-grid">{main.map(card)}</ul>
        {!!rare.length && <details className="cam-rare">
          <summary>Planos raros ({rare.length}) — vinhetas e transições, ignorados</summary>
          <ul className="cam-grid">{rare.map(card)}</ul>
        </details>}
      </>}
      {saved && <p role="status">Câmeras salvas</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  </Dialog>;
}
