import {useEffect,useState} from 'react';
import {api,type CaptionCorrection,type SuggestedClip,type TranscriptWord} from './api';
export function TranscriptEditor({projectId,artifactId,clip,corrections,onChange,disabled}:{
  projectId:string;artifactId:string;clip:SuggestedClip;corrections:CaptionCorrection[];
  onChange:(edits:CaptionCorrection[])=>void;disabled:boolean;
}) {
  const [words,setWords]=useState<Array<TranscriptWord & {index:number}>>([]);
  const [error,setError]=useState('');
  const [open,setOpen]=useState(false);
  const [loading,setLoading]=useState(true);
  useEffect(()=>{
    if (!open) return;
    const controller=new AbortController(); setLoading(true); setError(''); setWords([]);
    void api.transcript(projectId,artifactId,controller.signal).then(({document})=>{
      if (!controller.signal.aborted) setWords(document.segments.flatMap(s=>s.words).map((w,index)=>({...w,index})));
    }).catch(e=>{if(!controller.signal.aborted)setError(String(e));}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[projectId,artifactId,open]);
  const selected=words.filter(w=>w.end>clip.start_second && w.start<clip.end_second);
  return <details className="transcript-editor" onToggle={e=>setOpen(e.currentTarget.open)}><summary>Corrigir texto deste corte · {corrections.length} alterações</summary>
    <p>Corrija nomes e palavras mantendo o tempo original. Campo vazio oculta a palavra na legenda. O áudio não muda.</p>
    {loading && <p>Carregando transcrição…</p>}{error && <p role="alert">{error}</p>}
    <div className="transcript-words">{selected.map(w=><label key={w.index}><small>{w.start.toFixed(1)}s</small><input aria-label={`Palavra ${w.index+1}: ${w.word}`} disabled={disabled} maxLength={200} value={corrections.find(c=>c.word_index===w.index)?.text ?? w.word} onChange={e=>{
      const next=corrections.filter(c=>c.word_index!==w.index);
      if(e.target.value!==w.word)next.push({word_index:w.index,original:w.word,text:e.target.value});
      onChange(next.sort((a,b)=>a.word_index-b.word_index));
    }}/></label>)}</div>
    <button className="btn secondary" disabled={disabled || !corrections.length} onClick={()=>onChange([])}>Restaurar texto original deste corte</button>
  </details>;
}
