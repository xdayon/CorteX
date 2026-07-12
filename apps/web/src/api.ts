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
  overall_speech_ratio: number;
  loudness: {
    integrated_lufs: number | null;
    true_peak_dbfs: number | null;
    measurement_scope: string;
  };
  room_tone: Array<AnalysisInterval & { rms_dbfs: number }>;
};

export type SuggestionBrief = {
  count: number;
  minimum_seconds: number;
  maximum_seconds: number;
  topic?: string;
  instructions?: string;
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
  reasoning: string;
  warnings: string[];
  scores: { total: number } & Record<string, number>;
};

export type SuggestionSelection = {
  schema_version: "1.0";
  selection_notes: string;
  clips: SuggestedClip[];
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
};

export type EditPlanDocument = {
  schema_version: number;
  project_id: string;
  source_asset_id: string;
  transcript_artifact_id: string;
  analysis_artifact_id: string;
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
  };
  quality: {
    passed: boolean;
    issues: EditPlanIssue[];
    profile: string;
    degraded: boolean;
  };
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
  health: () => request<Record<string, unknown>>("/api/v1/health"),
  hardware: () => request<HardwareSnapshot>("/api/v1/hardware"),
  jobs: () => request<ApiJob[] | { jobs: ApiJob[] }>("/api/v1/jobs"),
  createJob: (type: "transcription" | "analysis" | "suggestion" | "render" | "pipeline", payload: Record<string, unknown>, projectId?: string) =>
    request<ApiJob>("/api/v1/jobs", { method: "POST", body: JSON.stringify({ type, project_id: projectId, payload }) }),

  createProject: (name: string) =>
    request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify({ name }) }),

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

  startEditPlan: (projectId: string, transcriptArtifactId: string, analysisArtifactId: string, start: number, end: number, profile: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/edit-plans`, {
      method: "POST",
      body: JSON.stringify({ transcript_artifact_id: transcriptArtifactId, analysis_artifact_id: analysisArtifactId, start, end, profile }),
    }),

  editPlan: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<EditPlanDocument>>(`/api/v1/projects/${projectId}/edit-plans/${artifactId}`),

  startRender: (projectId: string, editPlanArtifactId: string, encoder: string) =>
    request<ApiJob>(`/api/v1/projects/${projectId}/renders`, {
      method: "POST",
      body: JSON.stringify({ edit_plan_artifact_id: editPlanArtifactId, encoder }),
    }),

  render: (projectId: string, artifactId: string) =>
    request<ArtifactEnvelope<RenderDocument>>(`/api/v1/projects/${projectId}/renders/${artifactId}`),

  renderMediaUrl: (projectId: string, artifactId: string) =>
    `/api/v1/projects/${projectId}/renders/${artifactId}/media`,

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
