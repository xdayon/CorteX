export type PublicationPlatform = "instagram" | "tiktok";

export type PublicationInput = {
  title?: string | null; headline?: string | null; hook_text?: string;
  duration_seconds?: number | null; published: boolean; platforms: PublicationPlatform[];
  published_on: string | null; url: string | null;
  views: number | null; likes: number | null; comments: number | null;
  shares: number | null; saves: number | null;
  retention_3s_pct: number | null; avg_watch_seconds: number | null;
  rating: -1 | 0 | 1 | null; notes: string;
};

export type Publication = PublicationInput & {
  id: string; project_id: string; render_artifact_id: string;
  title: string; headline: string; hook_text: string; duration_seconds: number;
  created_at: string; updated_at: string;
};

export type ApiJob = {
  id: string;
  type?: string;
  stage?: string;
  status: string;
  progress?: number;
  message?: string;
  result?: Record<string, unknown> | null;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
  worker_pid?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  elapsed_seconds?: number | null;
};

export type Project = {
  id: string;
  name: string;
  created_at?: string;
};

export type SourceAsset = {
  id: string;
  project_id: string;
  kind: "upload" | "youtube";
  original_filename?: string | null;
  source_url?: string | null;
  stored_path: string;
  sha256: string;
  size_bytes: number;
  probe?: Record<string, unknown>;
};

export type EpisodeEntry = {
  id: string; url: string | null; title: string; project_ids: string[];
  channel_name?: string | null; channel_url?: string | null; thumbnail_url?: string | null;
  metadata_error?: string | null; metadata_updated_at?: string | null; archived?: boolean;
  participant_count: number | null; primary_subject: string;
  subject_reference: {speaker?:string}; diarizations: Array<{id:string;project_id:string}>;
  source: SourceAsset | null; source_bytes: number; runs: WorkflowRun[];
  jobs: ApiJob[];
  renders: Array<{id: string; project_id: string; title: string; duration: number}>;
};

export type WorkflowRun = {
  created_at?: string;
  id: string;
  project_id: string;
  source_asset_id: string;
  status: "queued" | "running" | "ready_for_review" | "rendering" | "complete" | "failed" | "cancelled";
  stage: string;
  progress: number;
  message: string;
  brief: SuggestionBrief;
  active_job_id?: string | null;
  artifacts: Record<string, string>;
  error?: string | null;
};

export type SuggestionArtifactDocument = {
  provenance?: SuggestionProvenance;
  selection: SuggestionSelection;
};

export type ArtifactEnvelope<T> = {
  artifact: { id: string; project_id: string; schema_version: number; path: string };
  document: T;
};

export type SuggestionBrief = {
  primary_subject?: string;
  confirmed_voice?: Record<string, unknown> | null;
  new_clips_only?: boolean;
  count: number;
  minimum_seconds: number;
  maximum_seconds: number;
  topic?: string;
  instructions?: string;
};

export type NarrativeBeat = {
  start_second: number;
  end_second: number;
  summary: string;
  evidence: string;
};

// Selection v2 stores a single 1–10 score in `total`; the sub-scores exist only
// in older (v1) selections.
export type ClipScores = {
  spoken_hook?: number;
  standalone_clarity?: number;
  emotion?: number;
  quotability?: number;
  payoff?: number;
  compression_safety?: number;
  audience_relevance?: number;
  total: number;
};

export type SuggestedClip = {
  rank: number;
  title: string;
  headline: string;
  start_second: number;
  end_second: number;
  estimated_duration: number;
  primary_speaker: string;
  topic?: string;
  pacing: "dynamic" | "balanced" | "contemplative";
  hook: NarrativeBeat;
  context: NarrativeBeat;
  payoff: NarrativeBeat;
  reasoning: string;
  warnings: string[];
  scores: ClipScores;
  approximate_edl?: Array<Record<string, unknown>>;
  pieces?: ClipPiece[];
  headline_options?: string[];
  emphasis?: string[];
  emphasis_word_indices?: number[];
  lead_trimmed?: string;
  teaser?: ClipTeaser | null;
  transcript_text?: string;
  connective_logic?: string;
};

export type ClipTeaser = {start: number; end: number; text: string};
export type ClipPiece = {from_id: number; to_id: number; start: number; end: number; text: string};

export type ClipRevision = {
  id: string; project_id: string; workflow_run_id: string; version: number;
  selection_artifact_id: string;
  selected_clips: Array<SuggestedClip & {key: string}>;
  render_settings: RenderSettings;
  caption_edits: Record<string, CaptionCorrection[]>;
  headlines: Record<string, string>;
  emphasis?: Record<string, number[]>;
  visual_artifacts: Record<string, string>;
};

export type ExportItem = {
  id: string; position: number; clip_key: string; title: string;
  status: "queued"|"planning"|"camera_planning"|"rendering"|"succeeded"|"failed"|"cancelled";
  active_job_id?: string | null; render_artifact_id?: string | null; error?: string | null;
};

export type ExportBatch = {
  id: string; project_id: string; workflow_run_id: string; revision_id: string;
  status: "queued"|"running"|"succeeded"|"partial"|"failed"|"cancelled";
  active_job_id?: string | null; error?: string | null; items: ExportItem[];
};

export type SuggestionProvenance = {
  provider?: string;
  model?: string;
};

export type SuggestionSelection = {
  schema_version: "1.0";
  selection_notes: string;
  clips: SuggestedClip[];
  provenance?: SuggestionProvenance;
};

export type SceneFramingOverride = { scene_index: number; target: "left" | "right" | "full" };

export type CaptionCorrection = {word_index: number; original: string; text: string};
export type TranscriptWord = {start:number; end:number; word:string};
export type RenderSettings = {
  schema_version: 1;
  encoder: "h264_nvenc" | "libx264";
  canvas: {
    width: 1080 | 1920;
    height: 1080 | 1920;
    fps: 30;
  };
  framing: {
    schema_version: 1;
    // face_static_crop, speaker_auto and speaker_face are legacy: stored revisions may carry them.
    mode: "vertical_crop" | "blurred_background" | "face_static_crop" | "speaker_auto" | "speaker_face";
    position_y?: number;
    primary_side?: "left" | "right" | null;
    scene_overrides?: SceneFramingOverride[];
    zoom: number;
    focus_x: number;
    focus_mode: "manual" | "faces";
    punch_in: {
      enabled: boolean;
      scale: number;
      anchor: "center" | "face";
      alternate_on_jump_cuts: boolean;
    };
  };
  captions: {
    corrections?: CaptionCorrection[];
    font_weight?: 400 | 900;
    uppercase?: boolean;
    position_y?: number;
    enabled: boolean;
    font_family: string;
    font_size: number;
    words_per_cue: number;
    outline: boolean;
    shadow: boolean;
    karaoke: boolean;
    text_color: string;
    karaoke_color: string;
    outline_color: string;
    shadow_color: string;
    animation: { style: "none" | "fade" | "pop"; duration_seconds: number };
    style: CaptionStyle;
    emphasis_word_indices: number[];
    emphasis_color: string;
    box_text_color: string;
  };
  headline: {
    enabled: boolean;
    text: string;
    font_family: string;
    font_size: number;
    duration_seconds: number;
    persistent: boolean;
    burst_color: string;
    strip_color: string;
    text_color: string;
    animation: { entrance: "none" | "fade" | "slide"; exit: "none" | "fade" | "slide"; duration_seconds: number };
  };
  subtitles: {
    sidecar_srt: boolean;
  };
  template: {
    quote_burst_opacity: number;
    quote_strip_opacity: number;
    quote_padding: number;
  };
  transitions: {
    piece_junction: PieceJunction;
    flash_opacity: number;
    flash_seconds: number;
    teaser: boolean;
  };
};

export type CaptionStyle = "classic" | "word_reveal" | "pop" | "box";
export type PieceJunction = "cut" | "flash" | "zoom";

export type OverlayWord = {
  index: number; text: string; start: number; end: number;
  emphasis: boolean; original: string; probability: number | null;
};
export type OverlayPage = {start: number; end: number; lines: OverlayWord[][]};
export type OverlayPreview = {
  ass_text: string; generator_version: string; content_hash: string;
  timeline_duration_seconds: number; canvas: {width: number; height: number};
  pages: OverlayPage[]; junctions: number[];
  fonts: Array<{ass_name: string; url: string}>;
};
export type SafeRect = [number, number, number, number];
export type SafeZones = {
  version: number;
  platforms: Record<string, {top_bar: SafeRect; right_actions: SafeRect; bottom_meta: SafeRect}>;
};
export type EditPlanOptions = {
  transcriptArtifactId: string; analysisArtifactId: string; start: number; end: number; profile: string;
  maximumSeconds?: number; minimumSeconds?: number; approximateEdl?: Array<Record<string, unknown>>;
  confirmedVoice?: Record<string, unknown> | null; sceneIndexArtifactId?: string | null;
  tighten?: boolean; headline?: string | null; primarySubject?: string | null;
  teaserStart?: number; teaserEnd?: number;
};
export type RefineStatus = {status: "accepted" | "skipped" | "rejected"; reason?: string};

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

// Cloudflare's Free plan rejects request bodies above 100 MB (HTTP 413) on the
// remote (tunnel) address; on the PC itself there is no such limit.
export const REMOTE_UPLOAD_LIMIT_BYTES = 95 * 1024 * 1024;
export const REMOTE_UPLOAD_MESSAGE =
  "Fora de casa, o acesso remoto aceita arquivos de até 100 MB. Use o link do YouTube do episódio ou envie o arquivo pelo próprio PC.";

export function isRemoteOrigin(hostname: string): boolean {
  return !["127.0.0.1", "localhost", "::1", "[::1]"].includes(hostname);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* corpo não-JSON: mantém status */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  diarizationStatus: () => request<{ready:boolean;runtime_installed:boolean;token_configured:boolean;device:string;missing:string[];model_url:string}>("/api/v1/diarization-status"),
  diarizeEpisode: (id:string) => request<ApiJob>(`/api/v1/episodes/${id}/diarization`, {method:"POST"}),
  episodeVoices: (id:string, artifact:string) => request<{turns:Array<{start:number;end:number;speaker:string}>}>(`/api/v1/episodes/${id}/diarization/${artifact}`),
  selectEpisodeVoice: (id:string, artifact_id:string, speaker:string) => request<EpisodeEntry>(`/api/v1/episodes/${id}/voice`,{method:"PUT",body:JSON.stringify({artifact_id,speaker})}),
  episodes: (includeArchived = false) => request<EpisodeEntry[]>(`/api/v1/episodes${includeArchived ? '?include_archived=true' : ''}`),
  refreshEpisodeMetadata: (id: string) => request<EpisodeEntry>(`/api/v1/episodes/${id}/metadata`, {method:'POST'}),
  archiveEpisode: (id: string, archived: boolean) => request<EpisodeEntry>(`/api/v1/episodes/${id}`, {method:'PATCH', body:JSON.stringify({archived})}),
  registerEpisode: (url: string, participant_count?: number) => request<EpisodeEntry>("/api/v1/episodes", {method:"POST", body:JSON.stringify({url, participant_count})}),
  downloadEpisode: (id: string) => request<{source: SourceAsset | null; job: ApiJob | null; cached: boolean}>(`/api/v1/episodes/${id}/download`, {method:"POST"}),
  cancelJob: (jobId: string) => request<ApiJob>(`/api/v1/jobs/${jobId}/cancel`, {method:"POST"}),
  job: (jobId: string, signal?: AbortSignal) => request<ApiJob>(`/api/v1/jobs/${jobId}`, {signal}),
  createProject: (name: string) =>
    request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify({ name }) }),

  createWorkflowRun: (projectId: string, sourceAssetId: string, brief: SuggestionBrief & {hotwords?: string[]}) =>
    request<WorkflowRun>(`/api/v1/projects/${projectId}/runs`, {
      method: "POST", body: JSON.stringify({ source_asset_id: sourceAssetId, ...brief }),
    }),

  workflowRun: (projectId: string, runId: string, signal?: AbortSignal) =>
    request<WorkflowRun>(`/api/v1/projects/${projectId}/runs/${runId}`, { signal }),

  saveRevision: (projectId: string, runId: string, body: {
    selection_artifact_id: string;
    selected_clips: Array<SuggestedClip & {key:string}>;
    render_settings: RenderSettings;
    caption_edits: Record<string, CaptionCorrection[]>;
    headlines: Record<string,string>;
    emphasis?: Record<string, number[]>;
    visual_artifacts: Record<string,string>;
  }) => request<ClipRevision>(`/api/v1/projects/${projectId}/runs/${runId}/revisions`, {
    method:"POST", body:JSON.stringify(body),
  }),

  latestRevision: (projectId:string, runId:string) =>
    request<ClipRevision>(`/api/v1/projects/${projectId}/runs/${runId}/revision`),

  createExportBatch: (projectId:string, runId:string, revisionId:string, idempotencyKey:string) =>
    request<ExportBatch>(`/api/v1/projects/${projectId}/runs/${runId}/export-batches`, {
      method:"POST", body:JSON.stringify({revision_id:revisionId,idempotency_key:idempotencyKey}),
    }),

  exportBatches: (projectId:string, runId:string) =>
    request<ExportBatch[]>(`/api/v1/projects/${projectId}/runs/${runId}/export-batches`),

  exportBatch: (projectId:string, batchId:string, signal?:AbortSignal) =>
    request<ExportBatch>(`/api/v1/projects/${projectId}/export-batches/${batchId}`, {signal}),

  cancelExportBatch: (projectId:string,batchId:string) =>
    request<ExportBatch>(`/api/v1/projects/${projectId}/export-batches/${batchId}/cancel`,{method:"POST"}),

  retryExportBatch: (projectId:string,batchId:string) =>
    request<ExportBatch>(`/api/v1/projects/${projectId}/export-batches/${batchId}/retry`,{method:"POST"}),

  suggestion: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<SuggestionArtifactDocument>>(`/api/v1/projects/${projectId}/suggestions/${artifactId}`),

  createYoutubeSource: (projectId: string, url: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/sources/youtube`, { method: "POST", body: JSON.stringify({ url }) }),

  transcript: (projectId: string, artifactId: string, signal?: AbortSignal) =>
    request<{document:{segments:Array<{words:TranscriptWord[]}>}}>(`/api/v1/projects/${projectId}/transcripts/${artifactId}`, {signal}),

  startEditPlan: (projectId: string, o: EditPlanOptions) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/edit-plans`, {
      method: "POST",
      body: JSON.stringify({
        transcript_artifact_id: o.transcriptArtifactId, analysis_artifact_id: o.analysisArtifactId,
        start: o.start, end: o.end, profile: o.profile,
        maximum_seconds: o.maximumSeconds, minimum_seconds: o.minimumSeconds,
        approximate_edl: o.approximateEdl, confirmed_voice: o.confirmedVoice ?? undefined,
        scene_index_artifact_id: o.sceneIndexArtifactId ?? undefined,
        tighten: o.tighten, headline: o.headline || undefined, primary_subject: o.primarySubject || undefined,
        teaser_start: o.teaserStart, teaser_end: o.teaserEnd,
      }),
    }),

  startSceneIndex: (projectId: string, sourceAssetId: string, sourceRanges?: Array<{start:number;end:number}>) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/scenes`, {
      method: "POST",
      body: JSON.stringify({ source_asset_id: sourceAssetId, source_ranges: sourceRanges }),
    }),

  sourcePreviewUrl: (projectId: string, sourceAssetId: string, timeSeconds: number) =>
    `/api/v1/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceAssetId)}/preview?time_seconds=${encodeURIComponent(timeSeconds.toFixed(3))}`,

  startRender: (projectId: string, editPlanArtifactId: string, renderSettings: RenderSettings, confirmedVoice?: Record<string, unknown> | null, options: {purpose?: "draft" | "export"} = {}) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/renders`, {
      method: "POST",
      body: JSON.stringify({
        edit_plan_artifact_id: editPlanArtifactId,
        render_settings: renderSettings,
        confirmed_voice: confirmedVoice ?? null,
        purpose: options.purpose ?? "export",
      }),
    }),

  overlayPreview: (projectId: string, editPlanArtifactId: string, settings: RenderSettings, signal?: AbortSignal) =>
    request<OverlayPreview>(`/api/v1/projects/${projectId}/overlay-preview`, {
      method: "POST", signal,
      body: JSON.stringify({edit_plan_artifact_id: editPlanArtifactId, settings}),
    }),

  renderDefaults: (signal?: AbortSignal) => request<RenderSettings>("/api/v1/render-defaults", {signal}),

  safeZones: (signal?: AbortSignal) => request<SafeZones>("/api/v1/safe-zones", {signal}),

  renderArtifact: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<{ auto_framing?: Array<{ mode: string; reason: string }> }>>(`/api/v1/projects/${projectId}/renders/${artifactId}`),

  publications: (limit = 500) => request<Publication[]>(`/api/v1/publications?limit=${limit}`),

  savePublication: (projectId: string, artifactId: string, input: PublicationInput) =>
    request<Publication>(`/api/v1/projects/${projectId}/renders/${artifactId}/publication`, {
      method: "PUT", body: JSON.stringify(input),
    }),

  deletePublication: async (projectId: string, artifactId: string) => {
    const response = await fetch(`/api/v1/projects/${projectId}/renders/${artifactId}/publication`, {method: "DELETE"});
    if (!response.ok && response.status !== 404) throw new Error(`${response.status} ${response.statusText}`);
  },

  renderMediaUrl: (projectId: string, artifactId: string) =>
    `/api/v1/projects/${projectId}/renders/${artifactId}/media`,

  renderSubtitlesUrl: (projectId: string, artifactId: string) =>
    `/api/v1/projects/${projectId}/renders/${artifactId}/subtitles`,

  // Upload via XHR para ter progresso real de envio (fetch não expõe upload progress).
  uploadSource: (projectId: string, file: File, onProgress?: (fraction: number) => void) =>
    new Promise<SourceAsset>((resolve, reject) => {
      if (isRemoteOrigin(window.location.hostname) && file.size > REMOTE_UPLOAD_LIMIT_BYTES) {
        reject(new Error(REMOTE_UPLOAD_MESSAGE));
        return;
      }
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/v1/projects/${projectId}/sources/upload`);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress?.(event.loaded / event.total);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText) as SourceAsset);
        } else if (xhr.status === 413) {
          reject(new Error(REMOTE_UPLOAD_MESSAGE));
        } else {
          let detail = `${xhr.status}`;
          try { detail = JSON.parse(xhr.responseText).detail ?? detail; } catch { /* mantém status */ }
          reject(new Error(detail));
        }
      };
      xhr.onerror = () => reject(new Error("Falha de rede durante o upload"));
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    }),

  // Acompanha um job via SSE até estado terminal; resolve com o job final.
  watchJob: (jobId: string, onUpdate?: (job: ApiJob) => void, signal?: AbortSignal) =>
    new Promise<ApiJob>((resolve, reject) => {
      signal?.throwIfAborted();
      const source = new EventSource(`/api/v1/jobs/${jobId}/events`);
      let polling = false;
      function close() {
        source.close();
        signal?.removeEventListener("abort", abort);
      }
      function abort() { close(); reject(signal?.reason); }
      async function pollAfterDisconnect() {
        if (polling || signal?.aborted) return;
        polling = true;
        source.close();
        try {
          while (!signal?.aborted) {
            const job = await request<ApiJob>(`/api/v1/jobs/${jobId}`, {signal});
            onUpdate?.(job);
            if (TERMINAL_STATUSES.has(job.status)) { close(); resolve(job); return; }
            await new Promise<void>((done) => window.setTimeout(done, 1500));
          }
        } catch (reason) { close(); reject(reason); }
      }
      signal?.addEventListener("abort", abort, { once: true });
      source.addEventListener("job", (event) => {
        if (signal?.aborted) return;
        try {
          const job = JSON.parse((event as MessageEvent).data) as ApiJob;
          onUpdate?.(job);
          if (TERMINAL_STATUSES.has(job.status)) { close(); resolve(job); }
        } catch (reason) { close(); reject(reason); }
      });
      source.addEventListener("error", () => { void pollAfterDisconnect(); });
      source.onerror = () => {
        close();
        reject(new Error("Conexão de eventos perdida — verifique se a API está de pé"));
      };
    }),
};
