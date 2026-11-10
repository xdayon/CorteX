import type { SuggestedClip } from "../api";
import { formatClock } from "../app/humanize";

export function clipKey(clip: SuggestedClip) {
  return `${clip.rank}:${clip.start_second}:${clip.end_second}:${clip.pacing}`;
}

export const clipDuration = formatClock;

/** Same text the export burns: manual edit, else the AI headline, else the title. */
export function effectiveHeadline(clip: SuggestedClip, edited?: string) {
  return (edited?.trim() || clip.headline || clip.title).slice(0, 120);
}
