from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "CorteX"
    api_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_dir: Path = Path("data")
    projects_dir: Path = Path("data/projects")
    cache_dir: Path = Path("data/cache")
    output_dir: Path = Path("data/output")
    database: Path = Path("data/cortex.sqlite3")


class TranscriptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "large-v3-turbo"
    language: str = "pt"
    device: str = "cuda"
    compute_type: str = "int8"
    batch_size: int = Field(default=8, ge=0, le=64)
    vad: bool = True
    allow_cpu_fallback: bool = False


class RenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ffmpeg: Path = Path("/usr/bin/ffmpeg")
    ffprobe: Path = Path("/usr/bin/ffprobe")
    remotion_node: Path = Path("node")
    remotion_browser: Path | None = None
    remotion_app_dir: Path = Path("apps/remotion")
    encoder: str = "h264_nvenc"
    allow_cpu_fallback: bool = False
    width: int = Field(default=1080, ge=320)
    height: int = Field(default=1920, ge=320)
    fps: int = Field(default=30, ge=15, le=120)
    loudness_target_lufs: float = Field(default=-14.0, ge=-70.0, le=-5.0)
    loudness_tolerance_lu: float = Field(default=1.0, gt=0.0, le=5.0)
    true_peak_limit_dbfs: float = Field(default=-1.0, ge=-9.0, le=0.0)
    captions_enabled: bool = True
    caption_font: str = "Montserrat"
    caption_font_size: int = Field(default=32, ge=12, le=120)
    caption_max_words: int = Field(default=5, ge=1, le=12)
    headline_enabled: bool = True
    headline_font: str = "Montserrat"
    headline_font_size: int = Field(default=42, ge=12, le=160)
    headline_duration_seconds: float = Field(default=3.4, gt=0.0, le=15.0)
    visual_quality_enabled: bool = True
    black_threshold_seconds: float = Field(default=0.5, gt=0.0, le=10.0)
    freeze_threshold_seconds: float = Field(default=1.5, gt=0.0, le=30.0)
    safe_zone_sample_fps: float = Field(default=4.0, gt=0.0, le=60.0)


class AnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_threshold: float = Field(default=10.0, ge=0.0, le=100.0)
    face_sample_fps: float = Field(default=1.0, gt=0.0, le=10.0)
    speaker_sample_fps: float = Field(default=4.0, ge=1.0, le=10.0)
    speaker_vad_padding_seconds: float = Field(default=1.0, ge=0.0, le=2.0)
    visual_quality_sample_fps: float = Field(default=2.0, gt=0.0, le=10.0)
    multicam_max_offset_seconds: float = Field(default=120.0, gt=0.0, le=3600.0)
    multicam_analysis_seconds: float = Field(default=600.0, ge=10.0, le=7200.0)


class EditConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_snap_tolerance_seconds: float = Field(default=0.5, ge=0.0, le=5.0)
    # J/L-cut default (Gate 4). The renderer (Session H) now honors the
    # independent audio/video clocks a jl_cut plan produces, but the
    # feature stays opt-in — the Curadoria UI exposes a per-clip toggle
    # (EditPlanRequest.jl_cut) so callers choose explicitly rather than
    # every plan silently gaining editorial audio offsets.
    jl_cut_enabled_default: bool = False


class IngestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_upload_bytes: int = Field(default=10_737_418_240, ge=1)
    allowed_extensions: list[str] = Field(
        default_factory=lambda: ["mp4", "mov", "mkv", "webm", "mp3", "wav", "m4a", "aac", "flac"]
    )
    youtube_format: str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"


class ClipConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_seconds: int = Field(default=50, ge=15, le=180)
    maximum_seconds: int = Field(default=120, ge=15, le=180)
    count: int = Field(default=10, ge=1, le=25)
    pacing_profile: str = "auto"

    @model_validator(mode="after")
    def duration_order(self) -> "ClipConfig":
        if self.minimum_seconds > self.maximum_seconds:
            raise ValueError("minimum_seconds cannot exceed maximum_seconds")
        return self


class AiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "codex_cli"
    fallback_provider: str | None = "claude_cli"
    codex_binary: str = "codex"
    codex_model: str = "gpt-5.5"
    codex_reasoning_effort: str = "medium"
    claude_binary: str = "claude"
    claude_model: str = "fable"
    claude_effort: str = "low"
    timeout_seconds: int = Field(default=300, ge=10, le=3600)
    max_input_chars: int = Field(default=800_000, ge=10_000, le=5_000_000)
    enable_local_heuristic_fallback: bool = False


class CortexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: AppConfig = AppConfig()
    paths: PathsConfig = PathsConfig()
    transcription: TranscriptionConfig = TranscriptionConfig()
    ingest: IngestConfig = IngestConfig()
    render: RenderConfig = RenderConfig()
    clips: ClipConfig = ClipConfig()
    ai: AiConfig = AiConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    edit: EditConfig = EditConfig()

    def ensure_runtime_dirs(self) -> None:
        for path in (
            self.paths.data_dir,
            self.paths.projects_dir,
            self.paths.cache_dir,
            self.paths.output_dir,
            self.paths.database.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _coerce_env_value(value: str) -> Any:
    try:
        return yaml.safe_load(value)
    except yaml.YAMLError:
        return value


def _environment_overrides(prefix: str = "CORTEX__") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in os.environ.items():
        if not name.startswith(prefix):
            continue
        keys = [key.lower() for key in name[len(prefix) :].split("__") if key]
        if len(keys) < 2:
            continue
        target = result
        for key in keys[:-1]:
            target = target.setdefault(key, {})
        target[keys[-1]] = _coerce_env_value(value)
    return result


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path | None = None) -> CortexConfig:
    config_path = Path(path or os.environ.get("CORTEX_CONFIG", "config/cortex.yaml"))
    raw: dict[str, Any] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if loaded is not None and not isinstance(loaded, dict):
            raise ValueError(f"configuration root must be a mapping: {config_path}")
        raw = loaded or {}
    return CortexConfig.model_validate(_deep_merge(raw, _environment_overrides()))
