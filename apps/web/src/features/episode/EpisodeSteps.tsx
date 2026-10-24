import { useCallback, useEffect, useRef, useState } from "react";
import { api, type EpisodeCameras, type EpisodeEntry, type VoiceProfile } from "../../api";
import { errorText } from "../../app/humanize";
import { Button, Chip, Icon, useToast, type ChipTone } from "../../ui";
import { episodeStatus } from "../episodes/episodeStatus";

type State = "done" | "running" | "pending" | "optional" | "checking";
const STATE_CHIP: Record<State, [string, ChipTone]> = {
  done: ["Feito", "ok"], running: ["Em andamento", "ac"], pending: ["Pendente", "warn"], optional: ["Opcional", "neutral"], checking: ["Verificando…", "neutral"],
};
const POLL_MS = 3000;
const FINISHED = ["succeeded", "failed", "cancelled", "canceled"];

type Step = { id: string; title: string; note: string; state: State; action?: { label: string; onClick: () => void; ghost?: boolean; disabled?: boolean } };

/** Loads the camera map of an episode and keeps polling while its mapping job runs. */
function useCameras(entry: EpisodeEntry, reloadKey: string) {
  const [cameras, setCameras] = useState<EpisodeCameras | null | undefined>(undefined);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const load = useCallback(async () => {
    try {
      const next = await api.episodeCameras(entry.id);
      if (alive.current) setCameras(next ?? null);
    } catch { if (alive.current) setCameras(current => current ?? null); }
  }, [entry.id]);
  useEffect(() => { if (entry.source) void load(); else setCameras(null); }, [entry.source, load, reloadKey]);
  const running = !!cameras?.job && !FINISHED.includes(cameras.job.status);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, load]);
  return { cameras, running, load };
}

function useVoiceProfile(reloadKey: string) {
  const [profile, setProfile] = useState<VoiceProfile | undefined>(undefined);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.resolve(api.voiceProfile(controller.signal)).then(result => { if (!controller.signal.aborted) setProfile(result ?? { exists: false }); })
      .catch(() => { if (!controller.signal.aborted) setProfile(current => current ?? { exists: false }); });
    return () => controller.abort();
  }, [reloadKey]);
  return profile;
}

/** "Etapas do episódio": what is done and what is the next useful action before opening the editor. */
export function EpisodeSteps({ entry, processing, onDownload, onGenerate, onVoice, onCameras, reloadKey }: {
  entry: EpisodeEntry; processing: boolean; onDownload: () => void; onGenerate: () => void; onVoice: () => void; onCameras: () => void; reloadKey: string;
}) {
  const toast = useToast();
  const status = episodeStatus(entry);
  const profile = useVoiceProfile(reloadKey);
  const { cameras, running, load } = useCameras(entry, reloadKey);
  const [expanded, setExpanded] = useState(false);

  async function mapCameras() {
    try { await api.mapEpisodeCameras(entry.id); await load(); } catch (reason) { toast(errorText(reason), "bad"); }
  }

  const hasRoles = !!cameras?.roles && Object.keys(cameras.roles).length > 0;
  const camerasState: State = cameras === undefined && entry.source ? "checking" : hasRoles ? "done" : running ? "running" : "optional";
  const steps: Step[] = [
    entry.source ? { id: "video", title: "Vídeo", note: "Vídeo guardado", state: "done" }
      : { id: "video", title: "Vídeo", note: "Baixe o episódio para editar", state: processing ? "running" : "pending", action: entry.url && !processing ? { label: "Baixar vídeo", onClick: onDownload } : undefined },
    status.readyRuns > 0 ? { id: "clips", title: "Cortes sugeridos", note: status.readyRuns === 1 ? "1 seleção pronta" : `${status.readyRuns} seleções prontas`, state: "done" }
      : { id: "clips", title: "Cortes sugeridos", note: "Essencial", state: processing && entry.source ? "running" : "pending",
        action: !processing ? { label: "Gerar seleção", onClick: onGenerate, disabled: !entry.source || !!entry.archived } : undefined },
    profile === undefined ? { id: "voice", title: "Voz do Dayon", note: "Essencial para cenas", state: "checking" }
      : profile.exists ? { id: "voice", title: "Voz do Dayon", note: "Perfil de voz ativo", state: "done" }
        : { id: "voice", title: "Voz do Dayon", note: "Essencial para cenas", state: "pending", action: { label: "Cadastrar voz", onClick: onVoice, disabled: !entry.source } },
    {
      id: "cameras", title: "Câmeras", state: camerasState,
      note: hasRoles ? "Câmeras confirmadas" : cameras?.document ? "Falta confirmar quem é quem" : "Recomendado para cenas",
      action: !entry.source || camerasState === "checking" || running ? undefined
        : hasRoles ? { label: "Revisar", onClick: onCameras, ghost: true }
          : cameras?.document ? { label: "Confirmar câmeras", onClick: onCameras }
            : { label: "Mapear câmeras", onClick: () => void mapCameras() },
    },
  ];
  const allDone = steps.every(step => step.state === "done");

  if (allDone && !expanded) return <section className="eps eps-done" aria-label="Etapas do episódio">
    <span className="eps-ok"><Icon name="check" small />Episódio pronto para editar</span>
    <Button size="sm" variant="ghost" onClick={() => setExpanded(true)}>Ver etapas</Button>
  </section>;

  return <section className="eps card" aria-label="Etapas do episódio">
    <div className="eps-head"><h2>Etapas do episódio</h2>{allDone && <Button size="sm" variant="ghost" onClick={() => setExpanded(false)}>Ocultar etapas</Button>}</div>
    <ol className="eps-list">
      {steps.map((step, index) => {
        const [label, tone] = STATE_CHIP[step.state];
        return <li key={step.id} className={`eps-step ${step.state}`}>
          <span className="eps-n" aria-hidden="true">{index + 1}</span>
          <div className="eps-body">
            <b>{step.title}</b>
            <span className="eps-note">{step.note}</span>
            <div className="eps-foot">
              <Chip tone={tone}>{label}</Chip>
              {step.action && <Button size="sm" variant={step.action.ghost ? "ghost" : "secondary"} disabled={step.action.disabled} onClick={step.action.onClick}>{step.action.label}</Button>}
            </div>
          </div>
        </li>;
      })}
    </ol>
    <p className="eps-next">Depois: escolha os cortes abaixo e abra no editor — legenda e manchete são essenciais; cenas, enquadramento e estilo são opcionais.</p>
  </section>;
}
