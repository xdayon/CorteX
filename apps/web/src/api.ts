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
  subject_identity_id?: string | null;
  interviewer_identity_id?: string | null;
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

export type ClipScores = {
  spoken_hook: number;
  standalone_clarity: number;
  emotion: number;
  quotability: number;
  payoff: number;
  compression_safety: number;
  audience_relevance: number;
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

export type IdentityIndexDocument = {
  schema_version: number;
  source_asset_id: string;
  face_index_artifact_id: string;
  identities: Array<{
    identity_id: string;
    status: "confirmed" | "single_layout" | "ambiguous";
    sample_count: number;
    layout_ids: string[];
    evidence: string[];
  }>;
};

export type CameraScene = { scene_index: number; start_us: number; end_us: number; role: string };
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
    mode: "vertical_crop" | "blurred_background" | "face_static_crop" | "speaker_auto";
    position_y?: number;
    scene_overrides?: SceneFramingOverride[];
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
  };
  headline: {
    enabled: boolean;
    text: string;
    font_family: string;
    font_size: number;
    duration_seconds: number;
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
};

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

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
  createProject: (name: string) =>
    request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify({ name }) }),

  createWorkflowRun: (projectId: string, sourceAssetId: string, brief: SuggestionBrief) =>
    request<WorkflowRun>(`/api/v1/projects/${projectId}/runs`, {
      method: "POST", body: JSON.stringify({ source_asset_id: sourceAssetId, ...brief }),
    }),

  workflowRun: (projectId: string, runId: string, signal?: AbortSignal) =>
    request<WorkflowRun>(`/api/v1/projects/${projectId}/runs/${runId}`, { signal }),

  selectWorkflowIdentity: (projectId: string, runId: string, identityId: string, artifacts: {
    scene: string; face: string; speaker: string; camera: string; quality: string; identity: string;
  }) => request<WorkflowRun>(`/api/v1/projects/${projectId}/runs/${runId}/identity`, {
    method: "PUT",
    body: JSON.stringify({
      interviewer_identity_id: identityId,
      scene_index_artifact_id: artifacts.scene,
      face_index_artifact_id: artifacts.face,
      speaker_timeline_artifact_id: artifacts.speaker,
      camera_timeline_artifact_id: artifacts.camera,
      visual_quality_artifact_id: artifacts.quality,
      identity_index_artifact_id: artifacts.identity,
    }),
  }),

  suggestion: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<SuggestionArtifactDocument>>(`/api/v1/projects/${projectId}/suggestions/${artifactId}`),

  createYoutubeSource: (projectId: string, url: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/sources/youtube`, { method: "POST", body: JSON.stringify({ url }) }),

  transcript: (projectId: string, artifactId: string, signal?: AbortSignal) =>
    request<{document:{segments:Array<{words:TranscriptWord[]}>}}>(`/api/v1/projects/${projectId}/transcripts/${artifactId}`, {signal}),

  startEditPlan: (projectId: string, transcriptArtifactId: string, analysisArtifactId: string, start: number, end: number, profile: string, maximum_seconds?: number) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/edit-plans`, {
      method: "POST",
      body: JSON.stringify({ transcript_artifact_id: transcriptArtifactId, analysis_artifact_id: analysisArtifactId, start, end, profile, maximum_seconds }),
    }),

  startSceneIndex: (projectId: string, sourceAssetId: string, sourceRanges?: Array<{start:number;end:number}>) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/scenes`, {
      method: "POST",
      body: JSON.stringify({ source_asset_id: sourceAssetId, source_ranges: sourceRanges }),
    }),

  startFaceIndex: (projectId: string, sourceAssetId: string, sceneIndexArtifactId: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/faces`, {
      method: "POST",
      body: JSON.stringify({ source_asset_id: sourceAssetId, scene_index_artifact_id: sceneIndexArtifactId }),
    }),

  startSpeakerTimeline: (projectId: string, sourceAssetId: string, sceneIndexArtifactId: string, faceIndexArtifactId: string, analysisArtifactId: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/speakers`, { method: "POST", body: JSON.stringify({ source_asset_id: sourceAssetId, scene_index_artifact_id: sceneIndexArtifactId, face_index_artifact_id: faceIndexArtifactId, analysis_artifact_id: analysisArtifactId }) }),

  startCameraTimeline: (projectId: string, sceneIndexArtifactId: string, faceIndexArtifactId: string, speakerTimelineArtifactId: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/cameras`, { method: "POST", body: JSON.stringify({ scene_index_artifact_id: sceneIndexArtifactId, face_index_artifact_id: faceIndexArtifactId, speaker_timeline_artifact_id: speakerTimelineArtifactId }) }),

  startVisualQuality: (projectId: string, sourceAssetId: string, sceneIndexArtifactId: string, faceIndexArtifactId: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/visual-quality`, { method: "POST", body: JSON.stringify({ source_asset_id: sourceAssetId, scene_index_artifact_id: sceneIndexArtifactId, face_index_artifact_id: faceIndexArtifactId }) }),

  cameraTimeline: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<{ scenes: CameraScene[] }>>(`/api/v1/projects/${projectId}/cameras/${artifactId}`),

  startIdentityIndex: (projectId: string, faceIndexArtifactId: string, cameraTimelineArtifactId: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/identities`, { method: "POST", body: JSON.stringify({ face_index_artifact_id: faceIndexArtifactId, camera_timeline_artifact_id: cameraTimelineArtifactId }) }),

  identityIndex: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<IdentityIndexDocument>>(`/api/v1/projects/${projectId}/identities/${artifactId}`),

  startReactionCandidates: (projectId: string, speakerTimelineArtifactId: string, cameraTimelineArtifactId: string, identityIndexArtifactId: string, visualQualityArtifactId: string, interviewerIdentityId: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/reaction-candidates`, { method: "POST", body: JSON.stringify({ speaker_timeline_artifact_id: speakerTimelineArtifactId, camera_timeline_artifact_id: cameraTimelineArtifactId, identity_index_artifact_id: identityIndexArtifactId, visual_quality_artifact_id: visualQualityArtifactId, interviewer_identity_id: interviewerIdentityId, min_duration_seconds: 0.7 }) }),

  startCameraPlan: (projectId: string, editPlanArtifactId: string, cameraTimelineArtifactId: string, identityIndexArtifactId: string, visualQualityArtifactId: string, reactionCandidateArtifactId?: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/camera-plans`, { method: "POST", body: JSON.stringify({ edit_plan_artifact_id: editPlanArtifactId, camera_timeline_artifact_id: cameraTimelineArtifactId, identity_index_artifact_id: identityIndexArtifactId, visual_quality_artifact_id: visualQualityArtifactId, reaction_candidate_artifact_id: reactionCandidateArtifactId ?? null }) }),

  identityPreviewUrl: (projectId: string, artifactId: string, identityId: string) =>
    `/api/v1/projects/${encodeURIComponent(projectId)}/identities/${encodeURIComponent(artifactId)}/${encodeURIComponent(identityId)}/preview`,

  sourcePreviewUrl: (projectId: string, sourceAssetId: string, timeSeconds: number) =>
    `/api/v1/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceAssetId)}/preview?time_seconds=${encodeURIComponent(timeSeconds.toFixed(3))}`,

  startRender: (projectId: string, editPlanArtifactId: string, renderSettings: RenderSettings, cameraEditPlanArtifactId?: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/renders`, {
      method: "POST",
      body: JSON.stringify({
        edit_plan_artifact_id: editPlanArtifactId,
        camera_edit_plan_artifact_id: cameraEditPlanArtifactId ?? null,
        render_settings: renderSettings,
      }),
    }),

  renderArtifact: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<{ auto_framing?: Array<{ mode: string; reason: string }> }>>(`/api/v1/projects/${projectId}/renders/${artifactId}`),

  renderMediaUrl: (projectId: string, artifactId: string) =>
    `/api/v1/projects/${projectId}/renders/${artifactId}/media`,

  renderSubtitlesUrl: (projectId: string, artifactId: string) =>
    `/api/v1/projects/${projectId}/renders/${artifactId}/subtitles`,

  // Upload via XHR para ter progresso real de envio (fetch não expõe upload progress).
  uploadSource: (projectId: string, file: File, onProgress?: (fraction: number) => void) =>
    new Promise<SourceAsset>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/v1/projects/${projectId}/sources/upload`);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress?.(event.loaded / event.total);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText) as SourceAsset);
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
      function close() {
        source.close();
        signal?.removeEventListener("abort", abort);
      }
      function abort() { close(); reject(signal?.reason); }
      signal?.addEventListener("abort", abort, { once: true });
      source.addEventListener("job", (event) => {
        if (signal?.aborted) return;
        try {
          const job = JSON.parse((event as MessageEvent).data) as ApiJob;
          onUpdate?.(job);
          if (TERMINAL_STATUSES.has(job.status)) { close(); resolve(job); }
        } catch (reason) { close(); reject(reason); }
      });
      source.onerror = () => {
        close();
        reject(new Error("Conexão de eventos perdida — verifique se a API está de pé"));
      };
    }),
};
