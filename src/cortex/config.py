from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "CorteX"
    api_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)
    studio_dir: Path | None = None


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
    # Names/terms the decoder should prefer (e.g. "Dayon"). Applies to new
    # transcriptions; cached transcripts are not redone when this changes.
    hotwords: list[str] = Field(default_factory=lambda: ["Dayon"], max_length=50)


class RenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ffmpeg: Path = Path("/usr/bin/ffmpeg")
    ffprobe: Path = Path("/usr/bin/ffprobe")
    fonts_dir: str = "assets/fonts"
    encoder: str = "h264_nvenc"
    width: int = Field(default=1080, ge=320)
    height: int = Field(default=1920, ge=320)
    fps: int = Field(default=30, ge=15, le=120)
    loudness_target_lufs: float = Field(default=-14.0, ge=-70.0, le=-5.0)
    loudness_tolerance_lu: float = Field(default=1.0, gt=0.0, le=5.0)
    true_peak_limit_dbfs: float = Field(default=-1.0, ge=-9.0, le=0.0)
    captions_enabled: bool = True
    caption_font: str = "Montserrat"
    caption_font_size: int = Field(default=56, ge=12, le=120)
    caption_max_words: int = Field(default=5, ge=1, le=12)
    headline_enabled: bool = True
    headline_font: str = "Montserrat"
    headline_font_size: int = Field(default=48, ge=12, le=160)
    headline_duration_seconds: float = Field(default=4.0, gt=0.0, le=15.0)
    visual_quality_enabled: bool = True
    black_threshold_seconds: float = Field(default=0.5, gt=0.0, le=10.0)
    freeze_threshold_seconds: float = Field(default=1.5, gt=0.0, le=30.0)
    freeze_block_threshold_seconds: float = Field(default=5.0, gt=0.0, le=60.0)


class AnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_threshold: float = Field(default=10.0, ge=0.0, le=100.0)


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
    youtube_format: str = (
        "bestvideo[height<=1440][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1440]+bestaudio"
        "/best[height<=1440]/best"
    )
    # yt-dlp `--cookies-from-browser` value ("browser" or "browser:profile_path", `~`
    # expanded) for videos where YouTube asks to "confirm you're not a bot".
    youtube_cookies_from_browser: str | None = (
        "chrome:~/.var/app/com.google.Chrome/config/google-chrome"
    )


class ClipConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_seconds: int = Field(default=40, ge=15, le=180)
    maximum_seconds: int = Field(default=120, ge=15, le=180)
    count: int = Field(default=10, ge=1, le=25)

    @model_validator(mode="after")
    def duration_order(self) -> "ClipConfig":
        if self.minimum_seconds > self.maximum_seconds:
            raise ValueError("minimum_seconds cannot exceed maximum_seconds")
        return self


class AiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude_code_cli", "codex_cli"] = "claude_code_cli"
    claude_binary: str = "claude"
    claude_model: str = "claude-sonnet-5-5"
    claude_effort: str | None = "medium"
    # Effort of the per-clip refine pass (one small call per exported clip).
    refine_effort: str | None = "medium"
    tighten_enabled: bool = True
    codex_binary: str = "codex"
    codex_model: str = "gpt-5.5"
    codex_reasoning_effort: str = "medium"
    timeout_seconds: int = Field(default=900, ge=10, le=3600)
    max_input_chars: int = Field(default=800_000, ge=10_000, le=5_000_000)
    selection_max_envelope_seconds: float = Field(default=240.0, gt=0)


class VoiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_enabled: bool = True
    default_threshold: float = Field(default=0.37, ge=-1.0, le=1.0)
    max_threads: int = Field(default=8, ge=1, le=64)
    timeout_seconds: int = Field(default=1800, ge=30, le=86400)


class LessonsConfig(BaseModel):
    """Math lesson videos (`python -m cortex.lessons`): Flow clips + Manim + narration."""

    model_config = ConfigDict(extra="forbid")

    dir: Path = Path("data/lessons")
    ingest_dir: Path = Path("entrada")
    language: str = "en"
    lead_in_seconds: float = Field(default=0.8, ge=0.0)
    flow_durations: list[float] = Field(default_factory=lambda: [4, 6, 8, 10], min_length=1)
    flow_max_retime: float = Field(default=1.1, ge=1.0)
    fps: int = Field(default=30, ge=15, le=120)
    width: int = Field(default=1920, ge=64)
    height: int = Field(default=1080, ge=64)
    xfade_seconds: float = Field(default=0.4, gt=0.0)
    voice_lufs: float = -14.0
    music_lufs: float = -31.0
    ducked_music_lufs: float = -20.0
    sfx_lufs: float = -16.0
    target_lufs: float = -14.0
    true_peak_db: float = Field(default=-1.0, le=0.0)
    manim_image: str = "docker.io/manimcommunity/manim:v0.19.0"
    manim_workers: int = Field(default=4, ge=1, le=16)
    # Shot segments built at once by `assemble`. Each Flow plate runs minterpolate, which is close to single-thread;
    # a 1080p one holds ~600 MB of RAM, so a few workers fill the idle cores without swapping.
    assemble_workers: int = Field(default=3, ge=1, le=16)
    # Niceness of every lesson FFmpeg run (0 = inherit), so the desktop stays usable during a long render.
    ffmpeg_nice: int = Field(default=10, ge=0, le=19)
    # Video encoder for every lesson stage (pieces and final). h264_nvenc must open on render.ffmpeg, or the
    # render stops with the reason: never a silent switch to the CPU.
    encoder: Literal["libx264", "h264_nvenc"] = "h264_nvenc"


class DriveConfig(BaseModel):
    """Exported clips copied to Google Drive with rclone (`cortex.drive`); the login lives in rclone's config."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    rclone: str = "rclone"
    remote: str = Field(default="gdrive", pattern=r"^[A-Za-z0-9_-]+$")
    folder: str = Field(default="CorteX", min_length=1)
    timeout_seconds: float = Field(default=900.0, gt=0)


class SoundConfig(BaseModel):
    """Sound library (`python -m cortex.sound`): Soundza + SFXMint searches, downloaded files, curated kits."""

    model_config = ConfigDict(extra="forbid")

    dir: Path = Path("data/sound")
    default_kit: str = "dayon"
    http_timeout_seconds: float = Field(default=30.0, gt=0)
    max_retry_wait_seconds: float = Field(default=30.0, ge=0)
    max_download_mb: float = Field(default=60.0, gt=0)
    candidates_per_query: int = Field(default=12, ge=1, le=50)
    reel_lufs: float = -16.0


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
    voice: VoiceConfig = VoiceConfig()
    lessons: LessonsConfig = LessonsConfig()
    sound: SoundConfig = SoundConfig()
    drive: DriveConfig = DriveConfig()

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
