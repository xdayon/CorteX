import type { BoardClip, ClipExport, ClipRow, ClipStatus, EpisodeClips, SuggestedClip } from "../api";

export const exportItem = (id = "r1", extra: Partial<ClipExport> = {}): ClipExport => ({
  render_artifact_id: id, batch_id: "b1", exported_at: "2026-10-16T12:00:00Z", duration_seconds: 50, headline: "Exportado",
  media_url: `/api/v1/projects/project/renders/${id}/media`, subtitles_url: `/api/v1/projects/project/renders/${id}/subtitles`, ...extra,
});

export function clipRow(rank: number, extra: Partial<ClipRow> = {}, clip: Partial<SuggestedClip> = {}): ClipRow {
  const start = rank * 100;
  const base = {
    rank, title: `Título ${rank}`, headline: `Manchete ${rank}`, start_second: start, end_second: start + 60, estimated_duration: 60,
    pacing: "balanced", primary_speaker: "Dayon", reasoning: `Razão ${rank}`, scores: { total: 5 + rank },
    pieces: [{ from_id: 1, to_id: 2, start, end: start + 20, text: `primeiro ${rank}` }, { from_id: 3, to_id: 4, start: start + 30, end: start + 70, text: `segundo ${rank}` }],
    ...clip,
  } as unknown as SuggestedClip;
  return {
    project_id: "project", run_id: "run", source_asset_id: "src", run_created_at: "2026-10-15T10:00:00Z",
    clip_key: `${rank}:${start}:${start + 60}:balanced`, rank, score: 5 + rank, headline: base.headline, headline_options: ["Outra opção"],
    title: base.title, duration: 60, start_second: start, end_second: start + 60, piece_count: 2, warnings: [], clip: base,
    state: null, exports: [], publication: null, status: "new" as ClipStatus, ...extra,
  };
}

export const withExport = (row: ClipRow, status: ClipStatus = "exported"): ClipRow => ({ ...row, exports: [exportItem()], status });

export function episodeClips(clips: ClipRow[], extra: Partial<EpisodeClips> = {}): EpisodeClips {
  const counts = { new: 0, approved: 0, exported: 0, saved: 0, posted: 0, discarded: 0, total: clips.length };
  for (const row of clips) counts[row.status]++;
  return {
    runs: [{ id: "run", project_id: "project", source_asset_id: "src", created_at: "2026-10-15T10:00:00Z", status: "ready_for_review", clip_count: clips.length, brief: {} }],
    clips, other_exports: [], counts, warnings: [], ...extra,
  };
}

export const boardRow = (row: ClipRow, episode = "ep", title = "Episódio um"): BoardClip => ({ ...row, episode_id: episode, episode_title: title });
