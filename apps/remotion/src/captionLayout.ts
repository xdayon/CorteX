import type {CaptionCue, TimedWord} from './types';
export type CaptionPage = {start:number; end:number; lines:TimedWord[][]; fontSize:number};
/** Paginate by measured glyph width, preserving every word and its clock. */
export function captionPages(cue:CaptionCue, maxWidth:number, fontSize:number, gap:number,
  measure:(text:string,size:number)=>number):CaptionPage[] {
  if (!cue.words.length) return [];
  const longest=Math.max(...cue.words.map(w=>measure(w.text,fontSize)));
  const effective=Math.min(fontSize,fontSize*maxWidth/Math.max(1,longest));
  const groups:TimedWord[][][]=[];
  let lines:TimedWord[][]=[[]]; let lineWidth=0;
  for(const word of cue.words) {
    const width=measure(word.text,effective);
    let line=lines[lines.length-1];
    if(line.length && lineWidth+gap+width>maxWidth) {
      if(lines.length===2) {groups.push(lines);lines=[[]];} else lines.push([]);
      line=lines[lines.length-1];lineWidth=0;
    }
    lineWidth+=(line.length?gap:0)+width;line.push(word);
  }
  if(lines[0].length) groups.push(lines);
  return groups.map((group,i)=>({lines:group,fontSize:effective,
    start:i===0?cue.start:group[0][0].start,
    end:i+1<groups.length?groups[i+1][0][0].start:cue.end}));
}
