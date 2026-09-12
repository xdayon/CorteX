from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EDIT_PLAN_SCHEMA_VERSION = 2

# Floating point tolerance used when comparing the audio/video coverage
# invariant below (seconds).
_COVERAGE_EPSILON = 0.01


class EditTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video: str
    audio: str
    duration: float = Field(ge=0)
    # Offset (seconds) between where the audio cut lands and where the video
    # cut lands at this boundary. 0.0 = synchronous (today's behavior).
    # Negative = the audio switches to the next segment before the video
    # does (J-cut, we hear the next speaker before we see them). Positive =
    # the audio holds on the previous segment after the video has already
    # cut (L-cut, we keep hearing the previous speaker under the new shot).
    audio_offset_seconds: float = 0.0
    # "crossfade" matches today's behavior (synchronous A/V dissolve) and is
    # the default so v1-shaped construction stays unchanged.
    kind: Literal["hard", "crossfade", "j_cut", "l_cut"] = "crossfade"


class EditSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # start/end remain the canonical editorial video clock, unchanged from
    # schema v1 — every other consumer (planner, quality gate, timeline
    # duration math) keeps reading these two fields.
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    # Independent A/V clocks (Gate 4). Default to start/end so constructing
    # an EditSegment with only start/end — as every v1 caller and every
    # jl-disabled plan still does — yields identical, synchronous clocks.
    video_start: float | None = Field(default=None, ge=0)
    video_end: float | None = Field(default=None, ge=0)
    audio_start: float | None = Field(default=None, ge=0)
    audio_end: float | None = Field(default=None, ge=0)
    timeline_order: int = Field(ge=0)
    transition: EditTransition | None = None

    @model_validator(mode="after")
    def _fill_and_check_clocks(self) -> "EditSegment":
        if self.video_start is None:
            self.video_start = self.start
        if self.video_end is None:
            self.video_end = self.end
        if self.audio_start is None:
            self.audio_start = self.start
        if self.audio_end is None:
            self.audio_end = self.end
        if self.video_start >= self.video_end:
            raise ValueError("video_start deve ser menor que video_end")
        if self.audio_start >= self.audio_end:
            raise ValueError("audio_start deve ser menor que audio_end")
        return self


class EditPlanDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str
    waveform_used: bool
    candidate_pauses: int = Field(ge=0)
    cuts: int = Field(ge=0)
    saved_seconds: float = Field(ge=0)
    crossfade: float = Field(ge=0)
    vad_used: bool
    scene_snap_count: int = Field(default=0, ge=0)
    # J/L-cut bookkeeping (Gate 4). Always present, always zero when
    # jl_settings.enabled is False.
    j_cuts: int = Field(default=0, ge=0)
    l_cuts: int = Field(default=0, ge=0)
    jl_blocked: int = Field(default=0, ge=0)


class EditQualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    code: str
    segment: int | None = None
    boundary: str | None = None
    word: str | None = None
    time: float | None = None
    snapped_from: float | None = None
    snapped_to: float | None = None
    delta_ms: float | None = None
    cuts: int | None = None
    recommended_max: int | None = None


class EditQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: list[EditQualityIssue]
    profile: str
    degraded: bool


class JlSettings(BaseModel):
    """J/L-cut policy actually applied to this plan (Gate 4).

    Persisted so the renderer (Session H) and the Curadoria UI can show
    requested-vs-effective transition behavior without re-deriving it from
    config/profile defaults that may have since changed.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_offset_seconds: float = Field(default=0.0, ge=0.0)
    requested_by: Literal["profile", "user", "disabled"] = "disabled"


class EditPlanDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = EDIT_PLAN_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    transcript_artifact_id: str
    analysis_artifact_id: str
    scene_index_artifact_id: str | None = None
    input_hash: str
    clip_start: float = Field(ge=0)
    clip_end: float = Field(ge=0)
    profile: str
    segments: list[EditSegment]
    timeline_duration_seconds: float = Field(ge=0)
    maximum_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    diagnostics: EditPlanDiagnostics
    quality: EditQualityReport
    jl_settings: JlSettings = Field(default_factory=JlSettings)

    @model_validator(mode="after")
    def _check_duration_ceiling(self) -> "EditPlanDocument":
        if self.maximum_seconds is not None:
            actual = sum(segment.video_end - segment.video_start for segment in self.segments)
            actual -= sum(segment.transition.duration for segment in self.segments[:-1]
                          if segment.transition is not None)
            if max(actual, self.timeline_duration_seconds) > self.maximum_seconds + 1e-6:
                raise ValueError("duração final do plano excede maximum_seconds")
        return self

    @model_validator(mode="after")
    def _check_av_coverage_invariant(self) -> "EditPlanDocument":
        """The audio and video clocks of a plan always cover the same total
        duration (Gate 4's critical invariant): no gap, no overlap, no
        silent source swap. Whatever audio a boundary offset lends to one
        segment, it borrows from its neighbor — see
        cortex.edit.planner.resolve_jl_cuts, which is the only place that
        ever sets audio_start/audio_end away from start/end.
        """
        if not self.segments:
            return self
        video_total = sum(seg.video_end - seg.video_start for seg in self.segments)
        audio_total = sum(seg.audio_end - seg.audio_start for seg in self.segments)
        if abs(video_total - audio_total) > _COVERAGE_EPSILON:
            raise ValueError(
                "invariante de cobertura A/V violada: "
                f"video_total={video_total:.4f} audio_total={audio_total:.4f}"
            )
        max_offset = self.jl_settings.max_offset_seconds
        for index, segment in enumerate(self.segments):
            start_delta = abs(segment.audio_start - segment.video_start)
            end_delta = abs(segment.audio_end - segment.video_end)
            if index == 0 and start_delta > _COVERAGE_EPSILON:
                raise ValueError("primeiro segmento não pode ter offset de áudio na entrada")
            if index == len(self.segments) - 1 and end_delta > _COVERAGE_EPSILON:
                raise ValueError("último segmento não pode ter offset de áudio na saída")
            if start_delta > max_offset + _COVERAGE_EPSILON or end_delta > max_offset + _COVERAGE_EPSILON:
                raise ValueError(
                    f"offset de áudio do segmento {index} excede max_offset_seconds do plano"
                )
        return self
