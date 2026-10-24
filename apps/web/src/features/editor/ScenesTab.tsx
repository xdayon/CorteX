import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type EpisodeEntry, type RenderInsert, type RenderSettings, type SceneCandidate, type SceneDecision, type SceneSuggestion } from "../../api";
import { errorText, formatClock } from "../../app/humanize";
import { Button, Chip, Icon, ProgressBar, Skeleton, Switch, useToast } from "../../ui";
import { CamerasDialog } from "../episodes/CamerasDialog";
import { VoiceDialog } from "../episodes/VoiceDialog";
import { SourcePiecesPlayer, type PlayerHandle } from "../episode/SourcePiecesPlayer";
import type { SettingsUpdate } from "./StyleTab";
import type { SceneData } from "./useSceneSuggestions";

const KIND_LABEL = { reaction: "Reação", wide: "Plano aberto" } as const;
const POLL_MS = 2000;
const FINISHED = ["succeeded", "failed", "cancelled", "canceled"];

export const seconds = (value: number) => `${value.toFixed(1).replace(".", ",")} s`;
const byAnchor = (inserts: RenderInsert[]) => [...inserts].sort((a, b) => a.anchor - b.anchor);
export const insertOf = (item: Pick<SceneSuggestion, "id" | "kind" | "anchor" | "duration" | "source_start" | "group">): RenderInsert =>
  ({ kind: item.kind, anchor: item.anchor, duration: item.duration, source_start: item.source_start, group: item.group, suggestion_id: item.id });

/** Candidates a suggestion can cycle through: the original pick first, then its alternatives. */
const cycleOf = (item: SceneSuggestion): SceneCandidate[] => [{ kind: item.kind, source_start: item.source_start, group: item.group, score: item.score }, ...item.alternatives];

type Dialog = { kind: "cameras" | "voice"; entry: EpisodeEntry } | null;
type Watching = { key: string; label: string; start: number; end: number } | null;

export function ScenesTab({ scenes, planId, entryId, settings, update, onSeek, disabled }: {
  scenes: SceneData; planId: string | null; entryId: string; settings: RenderSettings; update: SettingsUpdate; onSeek: (seconds: number) => void; disabled: boolean;
}) {
  const toast = useToast();
  const { data, loading, error, refresh, patch } = scenes;
  const [choice, setChoice] = useState<Record<string, number>>({});
  const [watching, setWatching] = useState<Watching>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const player = useRef<PlayerHandle>(null);
  const list = data?.suggestions;
  useEffect(() => { setChoice({}); setWatching(null); }, [list]);

  const inserts = settings.inserts ?? [];
  const suggestions = useMemo(() => (list ?? []).map(item => {
    const pick = cycleOf(item)[choice[item.id] ?? 0] ?? cycleOf(item)[0];
    return { ...item, kind: pick.kind, source_start: pick.source_start, group: pick.group, score: pick.score };
  }), [list, choice]);
  const known = new Set(suggestions.map(item => item.id));
  const accepted = new Set(inserts.map(item => item.suggestion_id).filter((id): id is string => !!id));
  const decisions = data?.decisions ?? {};
  const orphans = inserts.filter(item => !item.suggestion_id || !known.has(item.suggestion_id));

  const startWatch = useCallback((next: Watching) => {
    setWatching(next);
    if (next) requestAnimationFrame(() => player.current?.toggle());
  }, []);

  const save = useCallback(async (changes: Record<string, SceneDecision | null>) => {
    patch(current => {
      const next = { ...current.decisions };
      for (const [id, decision] of Object.entries(changes)) { if (decision) next[id] = decision; else delete next[id]; }
      return { ...current, decisions: next };
    });
    try { await api.saveSceneReviews(entryId, changes); } catch (reason) { toast(`Não foi possível salvar a avaliação: ${errorText(reason)}`, "bad"); }
  }, [entryId, patch, toast]);

  function accept(item: SceneSuggestion) {
    if (accepted.has(item.id)) {
      update(s => ({ ...s, inserts: (s.inserts ?? []).filter(insert => insert.suggestion_id !== item.id) }));
      void save({ [item.id]: null });
      return;
    }
    update(s => ({ ...s, inserts: byAnchor([...(s.inserts ?? []).filter(insert => insert.suggestion_id !== item.id), insertOf(item)]) }));
    void save({ [item.id]: "accepted" });
  }
  function reject(item: SceneSuggestion) {
    if (decisions[item.id] === "rejected" && !accepted.has(item.id)) { void save({ [item.id]: null }); return; }
    update(s => ({ ...s, inserts: (s.inserts ?? []).filter(insert => insert.suggestion_id !== item.id) }));
    void save({ [item.id]: "rejected" });
  }
  function applyAll() {
    const pending = suggestions.filter(item => decisions[item.id] !== "rejected" && !accepted.has(item.id));
    if (!pending.length) return;
    update(s => ({ ...s, inserts: byAnchor([...(s.inserts ?? []), ...pending.map(insertOf)]) }));
    void save(Object.fromEntries(pending.map(item => [item.id, "accepted" as const])));
  }
  function swap(item: SceneSuggestion) {
    const cycle = cycleOf(data!.suggestions.find(original => original.id === item.id)!);
    const next = ((choice[item.id] ?? 0) + 1) % cycle.length;
    setChoice(current => ({ ...current, [item.id]: next }));
    if (!accepted.has(item.id)) return;
    const pick = cycle[next];
    update(s => ({ ...s, inserts: (s.inserts ?? []).map(insert => insert.suggestion_id === item.id ? { ...insert, kind: pick.kind, source_start: pick.source_start, group: pick.group } : insert) }));
  }
  const removeOrphan = (target: RenderInsert) => update(s => ({ ...s, inserts: (s.inserts ?? []).filter(insert => insert !== target && !(insert.suggestion_id && insert.suggestion_id === target.suggestion_id)) }));
  const setQuestion = (on: boolean) => update(s => ({ ...s, transitions: { ...s.transitions, question: on && data?.question ? { start: data.question.start, end: data.question.end } : null } }));

  async function openDialog(kind: "cameras" | "voice") {
    try {
      const entry = (await api.episodes(true))?.find(item => item.id === entryId);
      if (!entry) throw new Error("Episódio não encontrado.");
      setDialog({ kind, entry });
    } catch (reason) { toast(errorText(reason), "bad"); }
  }
  const closeDialog = () => { setDialog(null); refresh(); };

  if (!planId) return <div className="pane" role="status" aria-label="Montando o corte"><p className="field-help">Montando o corte…</p><Skeleton height={64} /><Skeleton height={64} /></div>;
  if (error) return <div className="pane"><p role="alert" className="notice bad">{error}</p><Button className="reset" onClick={refresh}>Tentar de novo</Button></div>;
  if (loading || !data) return <div className="pane" role="status" aria-label="Procurando cenas"><p className="field-help">Procurando cenas para este corte…</p><Skeleton height={64} /><Skeleton height={64} /></div>;

  if (data.status === "needs_camera_map") return <div className="pane"><CameraMapCard entryId={entryId} onDone={refresh} /></div>;
  if (data.status === "needs_roles") return <div className="pane">
    <div className="scn-card"><b>Confirme quem aparece em cada câmera</b>
      <p className="field-help">As câmeras já foram mapeadas. Falta dizer qual é você, quem entrevista e qual é o plano aberto.</p>
      <Button variant="primary" onClick={() => void openDialog("cameras")}>Confirmar câmeras</Button></div>
    {dialog?.kind === "cameras" && <CamerasDialog entry={dialog.entry} onClose={closeDialog} />}
  </div>;
  if (data.status === "needs_voice") return <div className="pane">
    <div className="scn-card"><b>Cadastre a voz do Dayon</b>
      <p className="field-help">Sem o perfil de voz, não dá para saber quando o entrevistador está ouvindo. Confirme a voz do episódio e cadastre o perfil.</p>
      <Button variant="primary" onClick={() => void openDialog("voice")}>Cadastrar voz</Button></div>
    {dialog?.kind === "voice" && <VoiceDialog entry={dialog.entry} onClose={closeDialog} />}
  </div>;

  const question = data.question;
  const approval = data.review.approval_rate;
  const live = suggestions.filter(item => accepted.has(item.id)).length;
  const pendingCount = suggestions.filter(item => decisions[item.id] !== "rejected" && !accepted.has(item.id)).length;
  return <fieldset disabled={disabled} className="pane">
    <legend className="sr-only">Cenas</legend>
    <section className="scn-sec" aria-label="Abertura">
      <span className="lbl">Abertura</span>
      {question ? <>
        <Switch checked={!!settings.transitions.question} onChange={setQuestion} label="Abrir com a pergunta do entrevistador" />
        <p className="scn-quote">«{question.text}» <span className="mono">{seconds(question.end - question.start)}</span></p>
        <Button size="sm" variant="ghost" icon="play" className="reset" onClick={() => startWatch({ key: "question", label: "Pergunta do entrevistador", start: question.start, end: question.end })}>Ouvir</Button>
      </> : <p className="field-help">Nenhuma pergunta logo antes deste corte</p>}
    </section>
    <div className="divider" />
    <section className="scn-sec" aria-label="Inserções">
      <div className="scn-head">
        <span className="lbl">Inserções</span>
        <Chip tone={live ? "ac" : "neutral"}>{live} de {suggestions.length} aceitas</Chip>
        <div className="grow" />
        <Button size="sm" variant="primary" disabled={!pendingCount} onClick={applyAll}>Aplicar triangulação</Button>
      </div>
      {approval !== null && <p className="field-help">Aprovação no episódio: {Math.round(approval * 100)} %</p>}
      {!suggestions.length && <p className="field-help">Nenhuma cena sugerida para este corte.</p>}
      <ul className="scn-list">
        {suggestions.map(item => {
          const isAccepted = accepted.has(item.id);
          const isRejected = decisions[item.id] === "rejected" && !isAccepted;
          const label = KIND_LABEL[item.kind];
          return <li key={item.id} className={`scn-row${isAccepted ? " on" : ""}${isRejected ? " off" : ""}`} aria-label={`${label} em ${formatClock(item.output_start)}`}>
            <img className="scn-thumb" loading="lazy" alt="" src={api.sourcePreviewUrl(data.project_id, data.source_asset_id, item.source_start + item.duration / 2)} />
            <div className="scn-main">
              <div className="scn-title"><b>{label}</b><span className="mono">{formatClock(item.output_start)} · {seconds(item.duration)}</span></div>
              <p className="field-help">{item.reason}</p>
              {/* Pressed buttons stay "secondary": the pressed style paints the text with the accent colour. */}
              <div className="scn-actions">
                <Button size="sm" aria-pressed={isAccepted} icon={isAccepted ? "check" : undefined} onClick={() => accept(item)}>{isAccepted ? "Aceita" : "Aceitar"}</Button>
                <Button size="sm" aria-pressed={isRejected} onClick={() => reject(item)}>{isRejected ? "Recusada" : "Recusar"}</Button>
              </div>
              <div className="scn-actions">
                <Button size="sm" variant="ghost" onClick={() => startWatch({ key: item.id, label: `${label} · imagem da cena`, start: item.source_start, end: item.source_start + item.duration })}>Ver</Button>
                <Button size="sm" variant="ghost" onClick={() => onSeek(item.output_start)}>Ir para</Button>
                {item.alternatives.length > 0 && <Button size="sm" variant="ghost" icon="refresh" onClick={() => swap(item)}>Trocar</Button>}
              </div>
            </div>
          </li>;
        })}
      </ul>
      {orphans.length > 0 && <section className="scn-sec" aria-label="Fora do corte atual">
        <span className="lbl">Fora do corte atual</span>
        <p className="field-help">Aceitas antes de o corte mudar; não aparecem na prévia nova.</p>
        <ul className="scn-list">{orphans.map((item, index) => <li key={`${item.suggestion_id ?? "x"}-${index}`} className="scn-row">
          <div className="scn-main"><div className="scn-title"><b>{KIND_LABEL[item.kind]}</b><span className="mono">{seconds(item.duration)}</span></div>
            <div className="scn-actions"><Button size="sm" variant="ghost" onClick={() => removeOrphan(item)}>Remover</Button></div></div>
        </li>)}</ul>
      </section>}
      <p className="hint"><Icon name="info" small />A prévia é refeita com as cenas aceitas — só a imagem troca; sua voz e a legenda continuam.</p>
    </section>
    {watching && <div className="scn-watch">
      <div className="scn-head"><span className="lbl">{watching.label}</span><div className="grow" /><Button size="sm" variant="ghost" onClick={() => setWatching(null)}>Fechar</Button></div>
      <SourcePiecesPlayer key={watching.key} ref={player} src={api.sourceMediaUrl(data.project_id, data.source_asset_id)} pieces={[{ start: watching.start, end: watching.end }]} label={watching.label} />
    </div>}
  </fieldset>;
}

function CameraMapCard({ entryId, onDone }: { entryId: string; onDone: () => void }) {
  const [progress, setProgress] = useState<{ value: number; message: string } | null>(null);
  const [error, setError] = useState("");
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  async function map() {
    setError(""); setProgress({ value: 0, message: "Enviando para a fila…" });
    try {
      let job = await api.mapEpisodeCameras(entryId);
      while (alive.current && !FINISHED.includes(job.status)) {
        setProgress({ value: job.progress || 0, message: job.message || "Mapeando câmeras…" });
        await new Promise(resolve => setTimeout(resolve, POLL_MS));
        if (!alive.current) return;
        job = await api.job(job.id);
      }
      if (!alive.current) return;
      if (job.status !== "succeeded") throw new Error(job.error || job.message || "O mapa de câmeras não terminou.");
      setProgress(null); onDone();
    } catch (reason) { if (alive.current) { setError(errorText(reason)); setProgress(null); } }
  }

  return <div className="scn-card">
    <b>As câmeras do episódio ainda não foram mapeadas</b>
    <p className="field-help">Para achar reações e planos abertos, o CorteX precisa agrupar os planos do vídeo por câmera. Isso é feito uma vez por episódio.</p>
    {progress ? <><ProgressBar value={progress.value} label="Mapeando câmeras" /><p className="field-help" role="status">{progress.message}</p></>
      : <Button variant="primary" onClick={() => void map()}>Mapear câmeras do episódio (~2 min)</Button>}
    {error && <p role="alert" className="notice bad">{error}</p>}
  </div>;
}
