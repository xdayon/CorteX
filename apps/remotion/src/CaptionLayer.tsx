import type {CSSProperties} from 'react';
import type {OverlayPayload} from './types';
import {captionPages, type CaptionPage} from './captionLayout';
const layoutCache=new WeakMap<OverlayPayload['cues'][number], Map<string, CaptionPage[]>>();

export function CaptionLayer({caption,cues,time,width,height}: {
  caption:OverlayPayload['caption']; cues:OverlayPayload['cues']; time:number; width:number; height:number;
}) {
  const scale=Math.min(width/1080,height/1920);
  const cue=cues.find(c=>time>=c.start&&time<c.end);
  const pages=(()=>{
    if(!caption.enabled||!cue||typeof document==='undefined') return [];
    const key=JSON.stringify([caption.fontFamily,caption.fontSize,caption.fontWeight,caption.uppercase,width,scale]);
    const cached=layoutCache.get(cue)?.get(key); if(cached) return cached;
    const canvas=document.createElement('canvas');
    const context=canvas.getContext('2d');
    if(!context) throw new Error('O navegador não oferece medição de texto para a legenda');
    const measure=(text:string,size:number)=>{
      context.font=`${caption.fontWeight??900} ${size}px ${caption.fontFamily}`;
      return context.measureText(caption.uppercase===false?text:text.toLocaleUpperCase('pt-BR')).width;
    };
    // Reserve room for outline and the active word's pulse at both ends.
    const result=captionPages(cue,width*.85-100*scale,caption.fontSize*scale,14*scale,measure);
    const entries=layoutCache.get(cue)??new Map<string,CaptionPage[]>();
    if(entries.size>=32) entries.clear();
    entries.set(key,result);layoutCache.set(cue,entries);
    return result;
  })();
  const page=pages.find(p=>time>=p.start&&time<p.end);
  if(!page) return null;
  const fade=caption.animation.style==='fade'
    ? .4+.6*Math.max(0,Math.min(1,(time-page.start)/Math.max(.001,caption.animation.durationSeconds))) : 1;
  const boxHeight=(caption.fontSize*2*1.14+8+20)*scale;
  const style:CSSProperties={fontFamily:caption.fontFamily,fontSize:page.fontSize,fontWeight:caption.fontWeight??900,
    textShadow:caption.shadow?`0 ${4*scale}px ${14*scale}px ${caption.shadowColor}`:undefined,
    WebkitTextStroke:caption.outline?`${3*scale}px ${caption.outlineColor}`:undefined,paintOrder:'stroke fill',
    position:'absolute',left:width*.075,right:width*.075,top:height*caption.positionY-boxHeight/2,minHeight:boxHeight,boxSizing:'border-box',alignContent:'start',
    padding:`${10*scale}px ${14*scale}px`,textAlign:'center',lineHeight:1.14,
    textTransform:caption.uppercase===false?'none':'uppercase',display:'grid',rowGap:8*scale};
  return <div data-cortex-caption data-caption-start={page.start} style={style}>
    {page.lines.map((line,i)=><div data-caption-line key={i} style={{display:'flex',justifyContent:'center',gap:14*scale,whiteSpace:'nowrap'}}>
      {line.map((word,j)=>{const active=time>=word.start&&time<word.end;return <span key={`${j}-${word.start}`} style={{
        color:caption.karaoke&&active?caption.karaokeColor:caption.textColor,
        transform:caption.karaoke&&active&&caption.animation.style==='pop'?'scale(1.08)':'none',opacity:fade,
      }}>{word.text}</span>;})}
    </div>)}
  </div>;
}
