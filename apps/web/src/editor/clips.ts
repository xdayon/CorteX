import type { SuggestedClip } from "../api";

export function clipKey(clip: SuggestedClip) {
  return `${clip.rank}:${clip.start_second}:${clip.end_second}:${clip.pacing}`;
}

export function clipDuration(seconds: number) {
  const value = Math.max(0, Math.round(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}

/** Same text the export burns: manual edit, else the AI headline, else the title. */
export function effectiveHeadline(clip: SuggestedClip, edited?: string) {
  return (edited?.trim() || clip.headline || clip.title).slice(0, 120);
}

export function hookOf(clips: SuggestedClip[], key: string) {
  const clip = clips.find(item => clipKey(item) === key);
  return clip?.hook?.summary?.slice(0, 200) || undefined;
}
