export type Telemetry = {
  gpu_utilization?: number;
  gpu_memory_used_mb?: number;
  gpu_memory_total_mb?: number;
  gpu_temperature_c?: number;
  encoder_utilization?: number;
  decoder_utilization?: number;
  cpu_utilization?: number;
  ram_used_gb?: number;
  ram_total_gb?: number;
  device?: string;
};

export type HardwareSnapshot = {
  timestamp?: string;
  cpu: {
    usage_percent: number;
    logical_cores?: number;
    physical_cores?: number | null;
    memory_used_percent: number;
  };
  gpus: Array<{
    state: string;
    name?: string | null;
    usage_percent?: number | null;
    memory_used_mb?: number | null;
    memory_total_mb?: number | null;
    temperature_c?: number | null;
    encoder_percent?: number | null;
    decoder_percent?: number | null;
    error?: string | null;
  }>;
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
};

export type ProjectStatusItem = {
  label: string;
  completed: boolean;
};

export type ProjectStatusPhase = {
  name: string;
  status: string;
  items: ProjectStatusItem[];
};

export type ProjectStatus = {
  source: string;
  updated_at: string;
  summary: { completed: number; pending: number; total: number; progress_percent: number };
  phases: ProjectStatusPhase[];
};

export type Project = {
  id: string;
  name: string;
  created_at?: string;
};

export type RenderPreset = {
  id: string;
  project_id: string;
  name: string;
  settings: RenderSettings;
  created_at?: string;
  updated_at?: string;
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

export type TranscribeOverrides = {
  model?: string;
  language?: string;
  batch_size?: number;
  vad?: boolean;
};

export type TranscriptWord = {
  start: number;
  end: number;
  word: string;
  probability: number;
};

export type TranscriptDocument = {
  schema_version: number;
  duration_seconds: number;
  language?: string | null;
  segments: Array<{
    id: number;
    start: number;
    end: number;
    text: string;
    words: TranscriptWord[];
  }>;
};

export type ArtifactEnvelope<T> = {
  artifact: { id: string; project_id: string; schema_version: number; path: string };
  document: T;
};

export type AnalysisInterval = { start: number; end: number; duration: number };

export type SpeechDensityWindow = AnalysisInterval & { speech_ratio: number };

export type Filler = {
  word: string;
  start: number;
  end: number;
  kind: "hesitation" | "repetition";
  confidence: number;
};

export type AnalysisDocument = {
  schema_version: number;
  duration_seconds: number;
  waveform: Array<{
    points: number;
    frames_per_point: number;
    peaks: number[];
    rms: number[];
  }>;
  vad_intervals: AnalysisInterval[];
  pauses: AnalysisInterval[];
  speech_density: SpeechDensityWindow[];
  overall_speech_ratio: number;
  loudness: {
    integrated_lufs: number | null;
    true_peak_dbfs: number | null;
    measurement_scope: string;
  };
  room_tone: Array<AnalysisInterval & { rms_dbfs: number }>;
  /** Ausente em artifacts de análise anteriores ao detector de fillers (bump de schema). */
  fillers?: Filler[];
};

export type SuggestionBrief = {
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
  requested_provider?: string;
  effective_provider?: string;
  fallback_used?: boolean;
  fallback_reason?: string;
  model?: string;
};

export type SuggestionSelection = {
  schema_version: "1.0";
  selection_notes: string;
  clips: SuggestedClip[];
  provenance?: SuggestionProvenance;
};

export type EditPlanIssue = {
  severity: string;
  code: string;
  segment?: number | null;
  boundary?: string | null;
  word?: string | null;
  time?: number | null;
  snapped_from?: number | null;
  snapped_to?: number | null;
  delta_ms?: number | null;
};

export type EditPlanDocument = {
  schema_version: number;
  project_id: string;
  source_asset_id: string;
  transcript_artifact_id: string;
  analysis_artifact_id: string;
  scene_index_artifact_id?: string | null;
  input_hash: string;
  clip_start: number;
  clip_end: number;
  profile: string;
  segments: Array<{
    start: number;
    end: number;
    timeline_order: number;
    transition?: { video: string; audio: string; duration: number } | null;
  }>;
  timeline_duration_seconds: number;
  diagnostics: {
    profile: string;
    waveform_used: boolean;
    candidate_pauses: number;
    cuts: number;
    saved_seconds: number;
    crossfade: number;
    vad_used: boolean;
    scene_snap_count: number;
  };
  quality: {
    passed: boolean;
    issues: EditPlanIssue[];
    profile: string;
    degraded: boolean;
  };
};

export type SceneCut = { time: number; score: number };
export type SceneSegment = { index: number; start: number; end: number };

export type SceneIndexDocument = {
  schema_version: number;
  duration_seconds: number;
  cuts: SceneCut[];
  scenes: SceneSegment[];
  cut_count: number;
  engine: {
    filter: string;
    threshold_requested: number;
    threshold_effective: number;
    ffmpeg_version: string;
  };
};

export type FaceLandmarks = {
  right_eye: [number, number];
  left_eye: [number, number];
  nose_tip: [number, number];
  right_mouth_corner: [number, number];
  left_mouth_corner: [number, number];
};

export type FaceDetection = {
  x: number;
  y: number;
  width: number;
  height: number;
  score: number;
  landmarks: FaceLandmarks;
  track_id?: string | null;
  embedding?: number[] | null;
};

export type FrameFaces = { time: number; faces: FaceDetection[]; shot_type: string };

export type SceneFaceSummary = {
  scene_index: number;
  dominant_shot_type: string;
  track_ids_present: string[];
  sample_count: number;
};

export type FaceIndexDocument = {
  schema_version: number;
  scene_index_artifact_id: string;
  duration_seconds: number;
  frames: FrameFaces[];
  scenes: SceneFaceSummary[];
  frame_count: number;
  engine: {
    detector: string;
    providers: string[];
    score_threshold: number;
    nms_threshold: number;
    sample_fps: number;
    ffmpeg_version: string;
  };
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
    mode: "vertical_crop" | "blurred_background" | "face_static_crop";
  };
  captions: {
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

export type RenderSettingsPatch = {
  encoder?: RenderSettings["encoder"];
  canvas?: Partial<RenderSettings["canvas"]>;
  framing?: Partial<Pick<RenderSettings["framing"], "mode">>;
  captions?: Partial<RenderSettings["captions"]>;
  headline?: Partial<RenderSettings["headline"]>;
  subtitles?: Partial<RenderSettings["subtitles"]>;
  template?: Partial<RenderSettings["template"]>;
};

export type RenderDocument = {
  schema_version: number;
  project_id: string;
  source_asset_id: string;
  edit_plan_artifact_id: string;
  output_path: string;
  output_sha256: string;
  output_size_bytes: number;
  timeline_duration_seconds: number;
  segment_count: number;
  subtitles_path?: string | null;
  subtitles_sha256?: string | null;
  overlays?: { renderer: string; captions_enabled: boolean; karaoke_enabled: boolean; caption_font?: string | null; headline_enabled: boolean; headline_text?: string | null; artifact_sha256?: string | null; remotion_version?: string | null } | null;
  requested_settings?: RenderSettings | null;
  effective_settings?: RenderSettings | null;
  engine: { requested_encoder: string; effective_encoder: string; width: number; height: number; fps: number };
  quality: {
    passed: boolean;
    actual_duration_seconds: number;
    duration_delta_seconds: number;
    has_video: boolean;
    has_audio: boolean;
    issues: string[];
    loudness_target_lufs?: number | null;
    integrated_loudness_lufs?: number | null;
    loudness_delta_lu?: number | null;
    true_peak_dbfs?: number | null;
    true_peak_limit_dbfs?: number | null;
    caption_cue_count: number;
    subtitles_present: boolean;
    headline_present: boolean;
    visual_analysis_performed?: boolean;
    black_threshold_seconds?: number | null;
    black_interval_count?: number;
    black_total_duration_seconds?: number;
    black_max_duration_seconds?: number;
    freeze_threshold_seconds?: number | null;
    freeze_interval_count?: number;
    freeze_total_duration_seconds?: number;
    freeze_max_duration_seconds?: number;
  };
  publication?: {
    publish_ready: boolean;
    reasons: string[];
    loudness: Record<string, number | null>;
    visual: Record<string, number | boolean | null>;
    captions: Record<string, number | boolean | null>;
    safe_zones: Record<string, unknown>;
    encoder: Record<string, string>;
    dimensions: Record<string, number>;
    hashes: Record<string, string | null>;
    provenance: Record<string, string | null>;
  } | null;
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
  health: () => request<Record<string, unknown>>("/api/v1/health"),
  projectStatus: () => request<ProjectStatus>("/api/v1/project-status"),
  hardware: () => request<HardwareSnapshot>("/api/v1/hardware"),
  jobs: () => request<ApiJob[] | { jobs: ApiJob[] }>("/api/v1/jobs"),
  createJob: (type: "transcription" | "analysis" | "suggestion" | "render" | "pipeline", payload: Record<string, unknown>, projectId?: string) =>
    request<ApiJob>("/api/v1/jobs", { method: "POST", body: JSON.stringify({ type, project_id: projectId, payload }) }),

  createProject: (name: string) =>
    request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify({ name }) }),

  renderPresets: (projectId: string) =>
    request<RenderPreset[]>(`/api/v1/projects/${projectId}/render-presets`),

  createRenderPreset: (projectId: string, name: string, settings: RenderSettings) =>
    request<RenderPreset>(`/api/v1/projects/${projectId}/render-presets`, {
      method: "POST", body: JSON.stringify({ name, settings }),
    }),

  updateRenderPreset: (projectId: string, presetId: string, name: string | undefined, settings: RenderSettings | undefined) =>
    request<RenderPreset>(`/api/v1/projects/${projectId}/render-presets/${presetId}`, {
      method: "PUT", body: JSON.stringify({ ...(name === undefined ? {} : { name }), ...(settings === undefined ? {} : { settings }) }),
    }),

  createYoutubeSource: (projectId: string, url: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/sources/youtube`, { method: "POST", body: JSON.stringify({ url }) }),

  startTranscription: (projectId: string, sourceAssetId: string, overrides: TranscribeOverrides = {}) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/transcribe`, {
      method: "POST",
      body: JSON.stringify({ source_asset_id: sourceAssetId, ...overrides }),
    }),

  transcript: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<TranscriptDocument>>(`/api/v1/projects/${projectId}/transcripts/${artifactId}`),

  startAnalysis: (projectId: string, transcriptArtifactId: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/analyze`, {
      method: "POST",
      body: JSON.stringify({ transcript_artifact_id: transcriptArtifactId }),
    }),

  analysis: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<AnalysisDocument>>(`/api/v1/projects/${projectId}/analysis/${artifactId}`),

  startSuggestion: (projectId: string, transcriptArtifactId: string, analysisArtifactId: string, brief: SuggestionBrief) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/suggest`, {
      method: "POST",
      body: JSON.stringify({ transcript_artifact_id: transcriptArtifactId, analysis_artifact_id: analysisArtifactId, ...brief }),
    }),

  startEditPlan: (projectId: string, transcriptArtifactId: string, analysisArtifactId: string, start: number, end: number, profile: string, sceneIndexArtifactId?: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/edit-plans`, {
      method: "POST",
      body: JSON.stringify({ transcript_artifact_id: transcriptArtifactId, analysis_artifact_id: analysisArtifactId, scene_index_artifact_id: sceneIndexArtifactId ?? null, start, end, profile }),
    }),

  startSceneIndex: (projectId: string, sourceAssetId: string, sceneThreshold?: number) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/scenes`, {
      method: "POST",
      body: JSON.stringify({ source_asset_id: sourceAssetId, scene_threshold: sceneThreshold ?? null }),
    }),

  sceneIndex: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<SceneIndexDocument>>(`/api/v1/projects/${projectId}/scenes/${artifactId}`),

  startFaceIndex: (projectId: string, sourceAssetId: string, sceneIndexArtifactId: string, faceSampleFps?: number) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/faces`, {
      method: "POST",
      body: JSON.stringify({ source_asset_id: sourceAssetId, scene_index_artifact_id: sceneIndexArtifactId, face_sample_fps: faceSampleFps ?? null }),
    }),

  faceIndex: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<FaceIndexDocument>>(`/api/v1/projects/${projectId}/faces/${artifactId}`),

  startSpeakerTimeline: (projectId: string, sourceAssetId: string, sceneIndexArtifactId: string, faceIndexArtifactId: string, analysisArtifactId: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/speakers`, { method: "POST", body: JSON.stringify({ source_asset_id: sourceAssetId, scene_index_artifact_id: sceneIndexArtifactId, face_index_artifact_id: faceIndexArtifactId, analysis_artifact_id: analysisArtifactId }) }),

  startCameraTimeline: (projectId: string, sceneIndexArtifactId: string, faceIndexArtifactId: string, speakerTimelineArtifactId: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/cameras`, { method: "POST", body: JSON.stringify({ scene_index_artifact_id: sceneIndexArtifactId, face_index_artifact_id: faceIndexArtifactId, speaker_timeline_artifact_id: speakerTimelineArtifactId }) }),

  startIdentityIndex: (projectId: string, faceIndexArtifactId: string, cameraTimelineArtifactId: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/identities`, { method: "POST", body: JSON.stringify({ face_index_artifact_id: faceIndexArtifactId, camera_timeline_artifact_id: cameraTimelineArtifactId }) }),

  identityIndex: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<IdentityIndexDocument>>(`/api/v1/projects/${projectId}/identities/${artifactId}`),

  editPlan: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<EditPlanDocument>>(`/api/v1/projects/${projectId}/edit-plans/${artifactId}`),

  startRender: (projectId: string, editPlanArtifactId: string, renderSettings: RenderSettings, renderSettingsOverride?: RenderSettingsPatch, exportDirectory?: string, faceCrop?: { faceIndexArtifactId: string; identityIndexArtifactId: string; targetIdentityId: string }) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/renders`, {
      method: "POST",
      body: JSON.stringify({
        edit_plan_artifact_id: editPlanArtifactId,
        render_settings: renderSettings,
        render_settings_override: renderSettingsOverride,
        export_directory: exportDirectory?.trim() || null,
        face_index_artifact_id: faceCrop?.faceIndexArtifactId ?? null,
        identity_index_artifact_id: faceCrop?.identityIndexArtifactId ?? null,
        target_identity_id: faceCrop?.targetIdentityId ?? null,
      }),
    }),

  render: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<RenderDocument>>(`/api/v1/projects/${projectId}/renders/${artifactId}`),

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
  watchJob: (jobId: string, onUpdate?: (job: ApiJob) => void) =>
    new Promise<ApiJob>((resolve, reject) => {
      const source = new EventSource(`/api/v1/jobs/${jobId}/events`);
      source.addEventListener("job", (event) => {
        const job = JSON.parse((event as MessageEvent).data) as ApiJob;
        onUpdate?.(job);
        if (TERMINAL_STATUSES.has(job.status)) {
          source.close();
          resolve(job);
        }
      });
      source.onerror = () => {
        source.close();
        reject(new Error("Conexão de eventos perdida — verifique se a API está de pé"));
      };
    }),

  cancelJob: (jobId: string) => request<ApiJob>(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" }),
};
