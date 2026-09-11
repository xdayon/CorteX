import { useState } from "react";
import { api, type EpisodeEntry } from "./api";

type Turn = {start:number;end:number;speaker:string};
export function VoiceSelector({entry, disabled}: {entry:EpisodeEntry;disabled:boolean}) {
  const [turns,setTurns] = useState<Turn[]>([]);
  const [error,setError] = useState("");
  const [selected,setSelected] = useState(entry.subject_reference?.speaker || "");
  const artifact = entry.diarizations?.[0];
  async function analyze() {
    try { setError(""); await api.diarizeEpisode(entry.id); }
    catch(reason) { setError(String(reason)); }
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
  return <details><summary>Identificar a voz do Dayon{selected ? " · confirmada" : ""}</summary>
    <p>Separe as vozes em CPU uma vez e ouça as amostras. A escolha vale para este episódio.</p>
    {!artifact ? <button className="btn secondary" disabled={disabled || !entry.source} onClick={() => void analyze()}>Separar vozes em CPU</button> : <button className="btn secondary" onClick={() => void load()}>Ouvir vozes detectadas</button>}
    {error && <p role="alert">{error}</p>}
    {samples.map(turn => <div key={turn.speaker}><b>{turn.speaker}</b><audio controls preload="none" src={`/api/v1/projects/${entry.source!.project_id}/sources/${entry.source!.id}/media#t=${turn.start},${Math.min(turn.end,turn.start+12)}`} onTimeUpdate={event => {if(event.currentTarget.currentTime >= Math.min(turn.end,turn.start+12)) event.currentTarget.pause();}}/><button className="btn secondary" disabled={disabled} aria-pressed={selected===turn.speaker} onClick={() => void choose(turn.speaker)}>{selected===turn.speaker ? "Dayon confirmado" : "Esta voz é minha"}</button></div>)}
    {!!turns.length && !samples.length && <p>Não há amostra sem sobreposição suficiente para confirmar. Revise o áudio antes de atribuir uma voz.</p>}
  </details>;
}
