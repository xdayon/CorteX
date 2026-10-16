# Draft render speed (2026-10-16)

## Setup
- Clip: edit plan `e13c99c6...` (project `7b1f6452...`), 16 segments / 25 face spans, 71.1 s, VP9 1080p30 source,
  blurred background, zoom 1.45, `focus_mode: faces`, libx264. Settings copied from the manifest of
  `draft-render-95120f4e9568db16`.
- Harness: copy of `cortex.sqlite3` with the project's render rows deleted, scratch `data_dir`/`projects_dir`/`output_dir`
  (only `source/` symlinked), `RenderService.run` called directly, 3 runs each, medians below. Script and raw
  output: scratchpad `drafts/` (`bench.py`, `before-*.txt`, `after-*.txt`).
- Stages come from the new `RenderDocument.timings` (also in artifact metadata and the log line `render <purpose> timings`).
- Caveat: a background research job kept the 1-min load at 3-7 between runs; the harness only gated run starts on
  load < 3. Differences under ~15% in the end-to-end rows are within noise (e.g. the `after` export is faster only by noise).

## Before / after (median seconds)
| Case | Stage | Before | After |
|---|---|---|---|
| Draft, face cache warm | face_focus / ffmpeg_encode / loudness_check / qa / total | 0.00 / 15.3 / 0.47 / 3.5 / 19.6 | 0.02 / 16.0 / 0.58 / 4.8 / 21.7 (contended) |
| Draft, first open (cache cold) | face_focus / ffmpeg_encode / qa / total | 19.2 / 27.1 (run 2 contended) / 4.4 / 51.6 (runs 39.2, 82.3, 51.6) | 10.9 / 15.0 / 4.1 / 30.6 (runs 30.6, 29.9, 32.2) |
| Export, face cache warm | face_focus / ffmpeg_encode / loudness_check / qa / total | 0.00 / 47.6 / 0.64 / 9.7 / 58.3 | 0.00 / 39.8 / 0.49 / 9.6 / 49.6 (no export code change: noise) |

Paired A/B of the draft FFmpeg command alone (same graph, flags on vs off, interleaved, load 3-6): about 14.3 s vs 17.2 s,
roughly 15% less encode time from `fast_bilinear`. Stage breakdown of the 14 s encode (isolated runs): decode of the 94.7 s
window 4.2 s, filter graph about 4-5 s, in-graph loudnorm about 2 s, x264 ultrafast about 3 s.

## What changed
- `timings` on `RenderDocument` (plan_load, face_focus, overlay, ffmpeg_encode, loudness_check, loudness_correction, qa, total).
  Affects exports and drafts (observability only).
- Drafts only: `flags=fast_bilinear` on every scale filter (geometry unchanged); no loudness correction pass (the
  in-graph loudnorm stays; an out-of-tolerance draft gets warning `loudness_out_of_tolerance` instead of a blocking issue).
- Face focus (affects exports too, output identical): scdet is scanned in up to 4 parallel chunks with 0.5 s overlap
  (verified equal to the serial scan on this clip: same 10 cuts), and missing sample frames are extracted in parallel
  (4 workers) before the serial YuNet pass. Cache keys already ignored caption/headline settings and canvas size, so a
  draft, later drafts and the export reuse the same `face_focus/` cache (tests spy the detector and scdet).
- Export encoder args, filter graph and loudness behaviour are unchanged (tests).

## Not done
- Restructuring the multi-segment graph: every segment already trims one shared decoded stream; the graph runs
  effectively single-threaded (`-filter_complex_threads 1` is the same speed) and no single filter dominates
  (removing gblur, fps, tpad or overlay formats each moved <0.6 s). Splitting into parallel processes would change
  transitions/sync and was not attempted.
- Cheaper draft QA: `measure_technical_quality` costs ~3.5 s of a draft (full decode, packet scans, PCM analysis); the
  report schema requires it, so it is left as a follow-up candidate.
- Pre-existing: the benchmark draft reports blocking `av_sync_out_of_tolerance`; not investigated here.
