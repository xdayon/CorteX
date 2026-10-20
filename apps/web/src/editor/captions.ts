import type { CaptionCorrection, OverlayWord, SuggestedClip, TranscriptWord } from "../api";
import { clipKey } from "./clips";

export type IndexedWord = TranscriptWord & { index: number };

const byIndex = (a: CaptionCorrection, b: CaptionCorrection) => a.word_index - b.word_index;

/** Saving text equal to the original removes the correction; empty text hides the word. */
export function setWordText(corrections: CaptionCorrection[], index: number, original: string, text: string): CaptionCorrection[] {
  const rest = corrections.filter(item => item.word_index !== index);
  if (text === original) return rest;
  return [...rest, { word_index: index, original, text }].sort(byIndex);
}

export function hideWord(corrections: CaptionCorrection[], index: number, original: string) {
  return setWordText(corrections, index, original, "");
}

export function restoreWord(corrections: CaptionCorrection[], index: number) {
  return corrections.filter(item => item.word_index !== index);
}

export function emphasisFor(clip: SuggestedClip, edits: Record<string, number[]>): number[] {
  return edits[clipKey(clip)] ?? clip.emphasis_word_indices ?? [];
}

export function toggleEmphasis(current: number[], index: number): number[] {
  return current.includes(index) ? current.filter(item => item !== index) : [...current, index].sort((a, b) => a - b);
}

/** Makes the word start a new caption line, or undoes it. */
export const toggleLineBreak = toggleEmphasis;

export function clipRanges(clip: SuggestedClip): Array<[number, number]> {
  return clip.pieces?.length ? clip.pieces.map(p => [p.start, p.end]) : [[clip.start_second, clip.end_second]];
}

function core(word: string) {
  return word.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
}

/** Whole-word, case-insensitive replacement on the transcript words inside each clip. */
export function findReplace(
  words: IndexedWord[], targets: Array<{ key: string; clip: SuggestedClip }>, existing: Record<string, CaptionCorrection[]>,
  find: string, replacement: string,
): { edits: Record<string, CaptionCorrection[]>; count: number } {
  const needle = find.trim().toLowerCase();
  const edits: Record<string, CaptionCorrection[]> = { ...existing };
  let count = 0;
  if (!needle) return { edits, count };
  for (const { key, clip } of targets) {
    const ranges = clipRanges(clip);
    let list = edits[key] ?? [];
    let changed = false;
    for (const word of words) {
      if (!ranges.some(([a, b]) => word.end > a && word.start < b)) continue;
      const current = list.find(item => item.word_index === word.index)?.text ?? word.word;
      const stem = core(current);
      if (!stem || stem.toLowerCase() !== needle) continue;
      const next = current.replace(stem, replacement.trim());
      list = setWordText(list, word.index, word.word, next);
      count += 1; changed = true;
    }
    if (changed) edits[key] = list;
  }
  return { edits, count };
}

export function flatWords(segments: Array<{ words: TranscriptWord[] }>): IndexedWord[] {
  return segments.flatMap(segment => segment.words).map((word, index) => ({ ...word, index }));
}

export const DOUBTFUL_BELOW = 0.6;

/** A word the transcription was unsure about and nobody has corrected yet. */
export const isDoubtful = (word: OverlayWord, corrections: CaptionCorrection[]) =>
  word.probability !== null && word.probability < DOUBTFUL_BELOW && !corrections.some(item => item.word_index === word.index);
