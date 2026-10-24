import { useCallback, useEffect, useState } from "react";
import { api, type EpisodeEntry, type VoiceProfile } from "../../api";
import { errorText } from "../../app/humanize";
import { Button, Dialog, useToast } from "../../ui";
import { VoiceSelector } from "../../VoiceSelector";

const dateOf = (iso?: string) => { const date = iso ? new Date(iso) : null; return date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString("pt-BR") : ""; };

export function profileSummary(profile: VoiceProfile | null) {
  if (!profile?.exists) return "Nenhum perfil de voz cadastrado";
  const since = dateOf(profile.created_at);
  const accuracy = profile.calibration?.balanced_accuracy;
  return ["Perfil de voz ativo", since && `desde ${since}`, typeof accuracy === "number" && `precisão ${(accuracy * 100).toFixed(1).replace(".", ",")} %`].filter(Boolean).join(" · ").replace(" · desde", " desde");
}

export function VoiceDialog({ entry, onClose }: { entry: EpisodeEntry; onClose: () => void }) {
  const toast = useToast();
  const [profile, setProfile] = useState<VoiceProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setProfile((await api.voiceProfile()) ?? null); } catch { setProfile(null); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const confirmed = !!entry.subject_reference?.speaker;

  async function create() {
    setBusy(true);
    try {
      await api.createVoiceProfile(entry.id);
      toast("Perfil de voz cadastrado.", "ok");
      await load();
    } catch (reason) {
      toast(`Não foi possível cadastrar o perfil: ${errorText(reason)}`, "bad");
    } finally { setBusy(false); }
  }

  return <Dialog title="Identificar voz do protagonista" onClose={onClose} footer={<Button onClick={onClose}>Fechar</Button>}>
    <div className="voice-profile">
      <p><b>Perfil de voz</b></p>
      <p>{profileSummary(profile)}</p>
      <Button disabled={!confirmed || busy || !!entry.archived} onClick={() => void create()}>{busy ? "Cadastrando… pode levar até 1 min" : "Usar a voz confirmada deste episódio como perfil"}</Button>
      {!confirmed && <p className="field-help">Confirme a voz do Dayon abaixo para poder cadastrar o perfil.</p>}
    </div>
    <VoiceSelector entry={entry} disabled={!!entry.archived} autoOpen />
  </Dialog>;
}
