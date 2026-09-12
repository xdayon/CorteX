import { useEffect, useRef, useState } from "react";
import { api, type EpisodeEntry } from "./api";

type Turn = {start:number;end:number;speaker:string};
export function VoiceSelector({entry, disabled}: {entry:EpisodeEntry;disabled:boolean}) {
  const [turns,setTurns] = useState<Turn[]>([]);
  const [error,setError] = useState("");
  const [selected,setSelected] = useState(entry.subject_reference?.speaker || "");
  const [status,setStatus] = useState<Awaited<ReturnType<typeof api.diarizationStatus>>|null>(null);
  const [checking,setChecking] = useState(false);
  const checked=useRef(false);
  const [working,setWorking] = useState(false);
  const [message,setMessage] = useState("");
  const [createdArtifact,setCreatedArtifact] = useState<string|null>(null);
  const signal=useRef<AbortController|null>(null);
  useEffect(()=>()=>signal.current?.abort(),[]);
  const artifact = createdArtifact ? {id:createdArtifact} : entry.diarizations?.[0];
  async function check() {
    checked.current=true;setChecking(true);setStatus(null);setError("");
    try {setStatus(await api.diarizationStatus());} catch(reason) {setError(String(reason));}
    finally {setChecking(false);}
  }
  async function analyze() {
    signal.current?.abort(); const controller=new AbortController();signal.current=controller;
    try {
      setError("");setWorking(true);setMessage("Enviando separação de vozes para a fila…");
      const queued=await api.diarizeEpisode(entry.id);
      const done=await api.watchJob(queued.id,job=>setMessage(job.message||"Separando vozes em CPU…"),controller.signal);
      if(done.status!=="succeeded") throw new Error(done.error||done.message||"A separação de vozes não terminou.");
      const id=done.result?.diarization_artifact_id;
      if(typeof id!=="string") throw new Error("Resultado de voz indisponível; reabra o episódio.");
      setCreatedArtifact(id);setTurns((await api.episodeVoices(entry.id,id)).turns);
      setMessage("✓ Vozes separadas. Ouça as amostras e confirme a sua voz.");
    } catch(reason) {if(!controller.signal.aborted) setError(String(reason));}
    finally {if(!controller.signal.aborted) setWorking(false);}
  }
  async function load() {
    try { setError(""); setTurns((await api.episodeVoices(entry.id,artifact.id)).turns); }
    catch(reason) { setError(String(reason)); }
  }
  async function choose(speaker:string) {
    try { await api.selectEpisodeVoice(entry.id,artifact.id,speaker); setSelected(speaker); }
    catch(reason) { setError(String(reason)); }
  }
  const samples = [...new Set(turns.map(t => t.speaker))].map(speaker => turns.filter(t => t.speaker===speaker && !turns.some(other => other.speaker!==speaker && Math.min(other.end,t.end)>Math.max(other.start,t.start))).sort((a,b)=>(b.end-b.start)-(a.end-a.start))[0]).filter((t):t is Turn=>!!t);
  return <details onToggle={event=>{if(event.currentTarget.open&&!artifact&&!checked.current) void check();}}><summary>Identificar a voz do Dayon{selected ? " · confirmada" : ""}</summary>
    <p>Separe as vozes em CPU uma vez e ouça as amostras. A escolha vale para este episódio.</p>
    {!artifact ? <button className="btn secondary" disabled={disabled || working || checking || !status?.ready || !entry.source} onClick={() => void analyze()}>Separar vozes em CPU</button> : <button className="btn secondary" onClick={() => void load()}>Ouvir vozes detectadas</button>}
    {!artifact && <><p>{checking ? "Verificando disponibilidade…" : status?.ready ? "Ambiente CPU e credencial encontrados. O acesso aos pesos será verificado ao iniciar." : "A identificação de voz ainda precisa ser habilitada."}</p>
      {status?.missing?.map(item=><p key={item}>{item}</p>)}
      {status && !status.token_configured && <p><a href="https://huggingface.co/pyannote/speaker-diarization-community-1" target="_blank" rel="noreferrer">Liberar acesso ao modelo</a> · <a href="https://huggingface.co/settings/tokens" target="_blank" rel="noreferrer">Criar token de leitura</a>. Configure somente no computador; não envie o token pelo chat.</p>}
      <button className="btn secondary" disabled={checking||working} onClick={()=>void check()}>Verificar configuração novamente</button></>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
    {samples.map(turn => <div key={turn.speaker}><b>{turn.speaker}</b><audio controls preload="none" src={`/api/v1/projects/${entry.source!.project_id}/sources/${entry.source!.id}/media#t=${turn.start},${Math.min(turn.end,turn.start+12)}`} onTimeUpdate={event => {if(event.currentTarget.currentTime >= Math.min(turn.end,turn.start+12)) event.currentTarget.pause();}}/><button className="btn secondary" disabled={disabled} aria-pressed={selected===turn.speaker} onClick={() => void choose(turn.speaker)}>{selected===turn.speaker ? "Dayon confirmado" : "Esta voz é minha"}</button></div>)}
    {!!turns.length && !samples.length && <p>Não há amostra sem sobreposição suficiente para confirmar. Revise o áudio antes de atribuir uma voz.</p>}
  </details>;
}
