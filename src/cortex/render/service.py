from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact
from cortex.domain.store import (
    DomainStore,
    TranscriptArtifactNotFoundError,
)
from cortex.edit.schemas import EditPlanDocument
from cortex.ingest.ffprobe import duration_seconds, probe_media, usable_av_duration_seconds
from cortex.paths import cache_dir, renders_dir
from cortex.render.face_focus import (
    FACE_FOCUS_ALGORITHM_VERSION,
    FaceFocusCancelled,
    FaceFocusError,
    FaceFocusSpan,
    resolve_face_focus,
)
from cortex.render.schemas import (
    PUNCH_IN_MAX_SCALE,
    RENDER_SCHEMA_VERSION,
    RenderDocument,
    RenderEngineInfo,
    RenderFaceFocusInfo,
    RenderOverlayInfo,
    RenderPunchInInfo,
    RenderTeaserInfo,
    RenderPunchInSettings,
    RenderQualityCheck,
    RenderQualityReport,
    RenderQualityPublicationReport,
    RenderCanvasSettings,
    RenderCaptionSettings,
    RenderHeadlineSettings,
    RenderSettings,
    RenderSettingsPatch,
    RenderSourceInfo,
    RenderSubtitleSettings,
    RenderTemplateSettings,
    RenderTransitionInfo,
)
from cortex.render.safe_zones import SAFE_ZONES, SAFE_ZONES_VERSION, canvas_key, occlusion_issues
from cortex.render.technical_quality import (
    TECHNICAL_QUALITY_VERSION,
    TechnicalQualityCancelled,
    measure_technical_quality,
)
from cortex.render.ass_overlay import (
    AssOverlayError,
    AssOverlayResult,
    build_ass_overlay,
)
from cortex.render.captions import (
    CaptionCue,
    build_caption_cues,
    corrected_transcript,
    piece_junctions,
    render_srt,
)
from cortex.transcribe.schemas import TranscriptDocument


# Fixed join fade for a j/l-cut audio boundary (Gate 4, Session H). The
# jl_cut resolver in the planner already chose a quiet, word/VAD-safe point
# for the audio cut and baked it into audio_start/audio_end — this is not an
# editorial crossfade, only enough overlap to avoid an audible click at the
# splice. 20ms is comfortably under a single video frame at any supported
# fps and short enough that it can never be mistaken for a dissolve.
_JL_AUDIO_MICROFADE_SECONDS = 0.02
# Declick fade applied to a hard dry-cut join's audio (Requirement R6). Unlike
# acrossfade/the j/l micro-crossfade above, this never shortens total audio:
# fade-out + fade-in + concat sums to exactly the two parts' durations, so a
# clip with many dynamic-profile hard cuts never accumulates A/V drift.
_HARD_CUT_DECLICK_SECONDS = 0.008
_AAC_TRUE_PEAK_HEADROOM_DB = 0.5
_INPUT_WINDOW_VERSION = 1
# 2: every segment gets an exact frame count on the cumulative timeline (fixes the
# picture drifting ~0.13 s ahead of the voice over many cuts); old renders re-render.
_TIMELINE_VERSION = 2
_DRAFT_SCALE_FLAGS = "fast_bilinear"
LEGACY_FRAMING_MODES = frozenset({"face_static_crop", "speaker_auto", "speaker_face"})
LEGACY_FRAMING_MODE_MESSAGE = (
    "modo de enquadramento descontinuado; use Quadro com fundo desfocado ou Tela cheia"
)

logger = logging.getLogger(__name__)


class RenderJobCancelled(RuntimeError):
    pass


class RenderPreconditionError(ValueError):
    pass


class RenderExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class _OverlaySummary:
    """Resultado do overlay tipográfico libass (.ass) consumido pelo manifest."""

    renderer: str
    output_path: str
    output_sha256: str
    output_size_bytes: int
    cache_key: str
    codec: str | None = None
    pixel_format: str | None = None
    font_files: dict[str, str] | None = None
    generator_version: int | None = None


# Documented ceiling on how far a punch-in may shrink the source crop before
# the detail feeding the canvas is judged unacceptably thin (Gate 5).
PUNCH_IN_MAX_TOTAL_UPSCALE = 3.0


@dataclass(frozen=True)
class _PunchInResolution:
    """Requested-vs-effective punch-in geometry for one EDL segment,
    resolved once (no keyframes) before the filtergraph or the manifest are
    built. Coordinates are in canvas pixels for vertical_crop and
    blurred_background's foreground."""

    segment_order: int
    applied: bool
    requested_scale: float
    effective_scale: float
    anchor_x: int
    anchor_y: int
    crop_width: int
    crop_height: int
    reason: str | None


@dataclass(frozen=True)
class _CropGeometry:
    """Crop rectangle in SOURCE pixels plus the display window it is scaled
    into on the canvas (vertical_crop: the whole canvas; blurred_background:
    a full-width window positioned by framing.position_y)."""

    x: int
    y: int
    w: int
    h: int
    window_w: int
    window_h: int


def _even(value: float) -> int:
    return max(2, int(round(value / 2.0)) * 2)


def _blurred_geometry(
    source_w: int, source_h: int, canvas_w: int, canvas_h: int, zoom: float, focus_x: float,
) -> _CropGeometry:
    """blurred_background zoom/focus: crop width W/zoom around focus_x, full
    height, clamped so the display window never exceeds the canvas height (max
    zoom = full-canvas vertical crop, no blur visible)."""
    crop_w = source_w / zoom
    crop_h = float(source_h)
    clamped = canvas_w * crop_h / crop_w > canvas_h
    if clamped:
        crop_h = crop_w * canvas_h / canvas_w
    cw, ch = min(source_w, _even(crop_w)), min(source_h, _even(crop_h))
    x = int(min(max(focus_x * source_w - cw / 2, 0), source_w - cw)) // 2 * 2
    y = int((source_h - ch) / 2) // 2 * 2
    window_h = canvas_h if clamped else min(canvas_h, _even(canvas_w * ch / cw))
    return _CropGeometry(x, y, cw, ch, canvas_w, window_h)


def _cover_geometry(source_w: int, source_h: int, canvas_w: int, canvas_h: int) -> _CropGeometry:
    """vertical_crop: the centred source region that covers the canvas."""
    factor = max(canvas_w / source_w, canvas_h / source_h)
    cw = min(source_w, _even(canvas_w / factor))
    ch = min(source_h, _even(canvas_h / factor))
    return _CropGeometry(
        (source_w - cw) // 2 // 2 * 2, (source_h - ch) // 2 // 2 * 2, cw, ch, canvas_w, canvas_h,
    )


def _framing_geometry(
    framing_mode: str, source_size: tuple[int, int] | None, canvas_w: int, canvas_h: int,
    zoom: float, focus_x: float,
) -> _CropGeometry | None:
    if source_size is None:
        return None
    if framing_mode == "vertical_crop":
        return _cover_geometry(*source_size, canvas_w, canvas_h)
    if framing_mode == "blurred_background":
        return _blurred_geometry(*source_size, canvas_w, canvas_h, zoom, focus_x)
    return None


def _focused_geometry(
    framing_mode: str, source_size: tuple[int, int], canvas_w: int, canvas_h: int,
    zoom: float, focus_x: float,
) -> _CropGeometry:
    """Same crop as the manual geometry, with the horizontal focus applied
    to vertical_crop too (its manual geometry is always centred)."""
    if framing_mode == "blurred_background":
        return _blurred_geometry(*source_size, canvas_w, canvas_h, zoom, focus_x)
    base = _cover_geometry(*source_size, canvas_w, canvas_h)
    x = int(min(max(focus_x * source_size[0] - base.w / 2, 0), source_size[0] - base.w)) // 2 * 2
    return _CropGeometry(x, base.y, base.w, base.h, base.window_w, base.window_h)


def _resolve_segment_punch_in(
    segment_order: int,
    punch_in: RenderPunchInSettings,
    *,
    framing_mode: str,
    canvas_width: int,
    canvas_height: int,
    geometry: _CropGeometry | None = None,
    scale_override: float | None = None,
) -> _PunchInResolution:
    scale = punch_in.scale if scale_override is None else scale_override
    # Defense-in-depth: RenderPunchInSettings already bounds scale via a
    # pydantic Field, but the ceiling is safety-load-bearing enough to also
    # assert here rather than trust every caller to go through validation
    # (e.g. a future model_construct bypass).
    assert scale <= PUNCH_IN_MAX_SCALE
    if framing_mode in ("vertical_crop", "blurred_background") and geometry is not None:
        # Punch-in in source pixels: shrink the base crop around its centre; the
        # display window keeps its size, so it never changes between cuts.
        new_w = min(geometry.w, _even(geometry.w / scale))
        new_h = min(geometry.h, _even(geometry.h / scale))
        anchor_x = geometry.x + (geometry.w - new_w) // 2
        anchor_y = geometry.y + (geometry.h - new_h) // 2
        upscale = max(geometry.window_w / new_w, geometry.window_h / new_h)
        if upscale > PUNCH_IN_MAX_TOTAL_UPSCALE:
            return _PunchInResolution(
                segment_order, False, scale, 1.0, 0, 0, geometry.w, geometry.h,
                "punch_in_resolution_limit",
            )
        return _PunchInResolution(
            segment_order, True, scale, scale, anchor_x, anchor_y, new_w, new_h, None
        )

    if framing_mode in ("vertical_crop", "blurred_background"):
        new_w = max(1, round(canvas_width / scale))
        new_h = max(1, round(canvas_height / scale))
        anchor_x = (canvas_width - new_w) // 2
        anchor_y = (canvas_height - new_h) // 2
        if (
            anchor_x < 0 or anchor_y < 0
            or anchor_x + new_w > canvas_width or anchor_y + new_h > canvas_height
        ):
            raise RenderPreconditionError(
                f"retângulo de punch-in fora dos limites do canvas no segmento {segment_order}"
            )
        return _PunchInResolution(
            segment_order, True, scale, scale, anchor_x, anchor_y, new_w, new_h, None
        )

    raise RenderPreconditionError(
        f"punch-in não suportado para o modo de enquadramento {framing_mode}"
    )


def _resolve_punch_ins(
    plan: EditPlanDocument,
    punch_in: RenderPunchInSettings,
    *,
    framing_mode: str,
    canvas_width: int,
    canvas_height: int,
    geometry: _CropGeometry | None = None,
    junction_orders: frozenset[int] = frozenset(),
) -> list[_PunchInResolution]:
    """Resolve every segment's punch-in geometry up front (fail-closed on
    invalid crops before any encode starts) and decide, deterministically,
    which segments actually get the punch-in when alternate_on_jump_cuts is
    set (Gate 5): alternation runs on the parity of the segment's own global
    timeline_order.
    """
    if not punch_in.enabled and not junction_orders:
        return [
            _PunchInResolution(
                segment.timeline_order, False, punch_in.scale, 1.0, 0, 0,
                canvas_width, canvas_height, "punch_in_disabled",
            )
            for segment in plan.segments
        ]
    # A zoom junction (transitions.piece_junction == "zoom") pushes in harder on
    # the first segment after a content jump instead of following alternation.
    junction_scale = min(punch_in.scale + 0.08, PUNCH_IN_MAX_SCALE)

    apply_flags: dict[int, bool] = {}
    if not punch_in.enabled:
        apply_flags = {segment.timeline_order: False for segment in plan.segments}
    elif not punch_in.alternate_on_jump_cuts:
        apply_flags = {segment.timeline_order: True for segment in plan.segments}
    else:
        apply_flags = {
            segment.timeline_order: segment.timeline_order % 2 == 0
            for segment in plan.segments
        }

    resolutions: list[_PunchInResolution] = []
    for segment in plan.segments:
        order = segment.timeline_order
        junction = order in junction_orders
        if not junction and not apply_flags.get(order, False):
            resolutions.append(_PunchInResolution(
                order, False, punch_in.scale, 1.0, 0, 0,
                canvas_width, canvas_height,
                "alternation_skipped" if punch_in.enabled else "punch_in_disabled",
            ))
            continue
        resolutions.append(_resolve_segment_punch_in(
            order, punch_in, framing_mode=framing_mode,
            canvas_width=canvas_width, canvas_height=canvas_height,
            geometry=geometry,
            scale_override=junction_scale if junction else None,
        ))
    return resolutions


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _export_render_files(
    config: CortexConfig,
    *,
    output_path: Path,
    subtitles_path: Path | None,
    export_directory: str | None,
    input_hash: str,
) -> dict[str, str]:
    if export_directory is None or not export_directory.strip():
        return {}
    if "\x00" in export_directory:
        raise RenderPreconditionError("diretório de exportação inválido")
    destination = Path(export_directory.strip()).expanduser()
    if not destination.is_absolute():
        output_root = config.paths.output_dir.resolve()
        # The Studio documents paths relative to data/output. Accept callers
        # that include that prefix without nesting data/output/data/output.
        candidate = destination.resolve()
        destination = candidate if candidate.is_relative_to(output_root) else (output_root / destination).resolve()
        if not destination.is_relative_to(output_root):
            raise RenderPreconditionError("diretório de exportação relativo escapa de data/output")
    try:
        destination.mkdir(parents=True, exist_ok=True)
        destination = destination.resolve()
        if not destination.is_dir():
            raise OSError("destino não é um diretório")
        exported_video = destination / f"cortex-render-{input_hash[:16]}.mp4"
        temporary_video = destination / f".{exported_video.name}.tmp"
        shutil.copyfile(output_path, temporary_video)
        temporary_video.replace(exported_video)
        result = {"export_path": str(exported_video)}
        if subtitles_path is not None and subtitles_path.is_file():
            exported_subtitles = destination / f"cortex-render-{input_hash[:16]}.srt"
            temporary_subtitles = destination / f".{exported_subtitles.name}.tmp"
            shutil.copyfile(subtitles_path, temporary_subtitles)
            temporary_subtitles.replace(exported_subtitles)
            result["export_subtitles_path"] = str(exported_subtitles)
        return result
    except OSError as exc:
        raise RenderExecutionError(
            f"não foi possível exportar o render para {destination}: {exc}"
        ) from exc


_NVENC_PROBES: dict[str, str | None] = {}


def nvenc_unavailable_reason(ffmpeg: Path) -> str | None:
    """None when this FFmpeg can open h264_nvenc, else FFmpeg's own reason.

    A driver/FFmpeg API mismatch (e.g. FFmpeg built for NVENC API 13.1 on the
    Pascal 580 driver, which stops at 13.0) only shows up when the encoder
    opens; probing once per process turns that into an explicit precondition
    error instead of a failed render. Cached per FFmpeg path.
    """
    key = str(ffmpeg)
    if key not in _NVENC_PROBES:
        try:
            result = subprocess.run(
                [key, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "color=c=black:s=1280x720:d=0.1", "-frames:v", "1", "-c:v", "h264_nvenc",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=30, check=False,
            )
            lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
            _NVENC_PROBES[key] = None if result.returncode == 0 else (
                lines[0] if lines else f"código de saída {result.returncode}"
            )[:300]
        except (OSError, subprocess.TimeoutExpired) as exc:
            _NVENC_PROBES[key] = str(exc)[:300]
    return _NVENC_PROBES[key]


def _encoder_args(encoder: str, *, draft: bool = False) -> list[str]:
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p1" if draft else "p4", "-cq", "18"]
    if encoder == "libx264":
        return ["-c:v", encoder, "-preset", "ultrafast" if draft else "veryfast", "-crf", "18"]
    raise RenderPreconditionError(f"encoder não suportado: {encoder}")


def _resolve_font_family(requested: str) -> str:
    fc_match = shutil.which("fc-match")
    if fc_match is None:
        raise RenderPreconditionError("fontconfig/fc-match não está disponível")
    result = subprocess.run(
        [fc_match, "-f", "%{family}", "--", requested],
        capture_output=True, text=True, timeout=10, check=False,
    )
    effective = result.stdout.split(",", 1)[0].strip()
    if result.returncode or effective.casefold() != requested.casefold():
        raise RenderPreconditionError(
            f"fonte solicitada não está instalada sem fallback: {requested}"
        )
    return effective


def default_render_settings(config: CortexConfig) -> RenderSettings:
    """Server-side defaults for the Studio (encoder and typography come from
    config, so the UI never hardcodes them)."""
    return _default_render_settings(config, encoder=None, headline=None)


def _default_render_settings(
    config: CortexConfig, *, encoder: str | None, headline: str | None
) -> RenderSettings:
    return RenderSettings(
        encoder=(encoder or config.render.encoder).strip(),
        canvas=RenderCanvasSettings(
            width=config.render.width, height=config.render.height, fps=config.render.fps,
        ),
        captions=RenderCaptionSettings(
            enabled=config.render.captions_enabled,
            font_family=config.render.caption_font,
            font_size=config.render.caption_font_size,
            words_per_cue=config.render.caption_max_words,
            outline=True,
        ),
        headline=RenderHeadlineSettings(
            enabled=config.render.headline_enabled,
            text=headline or "",
            font_family=config.render.headline_font,
            font_size=config.render.headline_font_size,
            duration_seconds=config.render.headline_duration_seconds,
        ),
        subtitles=RenderSubtitleSettings(sidecar_srt=True),
        template=RenderTemplateSettings(),
    )


def _safe_zone_key(width: int, height: int) -> str:
    return canvas_key(width, height)


def _merge_render_settings(base: RenderSettings, patch: RenderSettingsPatch | None) -> RenderSettings:
    if patch is None:
        return base
    data = base.model_dump()
    patch_data = patch.model_dump(exclude_unset=True)
    for key, value in patch_data.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        elif value is not None:
            data[key] = value
    captions_patch = patch_data.get("captions") or {}
    if (
        "style" not in captions_patch
        and (captions_patch.get("animation") or {}).get("style") == "pop"
    ):
        # Back-compat with patches written before caption `style` existed.
        data["captions"]["style"] = "pop"
    return RenderSettings.model_validate(data)


def normalize_headline_text(text: str) -> str | None:
    value = "\n".join(
        re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()
    )[:120]
    return value or None


def effective_headline_settings(
    headline: RenderHeadlineSettings, headline_text: str | None, timeline_seconds: float,
    *, font_family: str | None = None,
) -> RenderHeadlineSettings:
    """Headline as it is actually drawn: persistent headlines span the whole
    timeline, timed ones are capped by it."""
    duration = (
        timeline_seconds if headline.persistent
        else min(headline.duration_seconds, timeline_seconds)
    )
    update: dict[str, object] = {"text": headline_text or "", "duration_seconds": duration}
    if font_family is not None:
        update["font_family"] = font_family
    return headline.model_copy(update=update)


@dataclass(frozen=True)
class PlanOverlay:
    cues: list[CaptionCue]
    junctions: tuple[float, ...]
    ass: AssOverlayResult | None


def build_plan_overlay(
    *, plan: EditPlanDocument, transcript: TranscriptDocument, settings: RenderSettings,
    fonts_dir: Path, headline_text: str | None, needs_cues: bool, build_ass: bool,
) -> PlanOverlay:
    """Single path from plan + transcript to cues and .ass, used by the render
    job and by the overlay preview API so both burn/draw identical text.
    Raises ValueError (corrections) or AssOverlayError (fonts)."""
    transcript = corrected_transcript(transcript, settings.captions.corrections)
    cues = build_caption_cues(
        plan, transcript, max_words=settings.captions.words_per_cue
    ) if needs_cues else []
    junctions = tuple(moment for _index, moment in piece_junctions(plan))
    ass: AssOverlayResult | None = None
    typography = bool(
        (settings.captions.enabled and cues)
        or (settings.headline.enabled and headline_text)
    )
    flashes = bool(junctions) and settings.transitions.piece_junction == "flash"
    if build_ass and (typography or flashes):
        ass = build_ass_overlay(
            settings, cues, fonts_dir=fonts_dir, headline_text=headline_text, junctions=junctions,
        )
    return PlanOverlay(cues=cues, junctions=junctions, ass=ass)


def _render_transitions(plan: EditPlanDocument) -> list[RenderTransitionInfo]:
    """Requested-vs-effective transition manifest entries for every interior
    EDL boundary (Gate 4, Session H). See RenderTransitionInfo for why
    effective always mirrors requested today."""
    transitions: list[RenderTransitionInfo] = []
    for index, segment in enumerate(plan.segments[:-1]):
        configured = segment.transition
        kind = configured.kind if configured is not None else "crossfade"
        offset = configured.audio_offset_seconds if configured is not None else 0.0
        transitions.append(RenderTransitionInfo(
            boundary_index=index,
            requested_kind=kind, effective_kind=kind,
            requested_audio_offset_seconds=offset,
            effective_audio_offset_seconds=offset,
        ))
    return transitions


def _publication_checks(
    *,
    issues: list[str],
    warnings: list[str],
    evidence: dict[str, dict[str, bool | float | int | str | None]],
    thresholds: dict[str, dict[str, float | int | str]],
) -> list[RenderQualityCheck]:
    issue_codes = set(issues)
    warning_codes = set(warnings)
    return [
        RenderQualityCheck(
            code=code,
            status=(
                "fail" if code in issue_codes
                else "warning" if code in warning_codes
                else "pass"
            ),
            severity=(
                "blocking" if code in issue_codes
                else "warning" if code in warning_codes
                else "info"
            ),
            evidence=evidence.get(code, {}),
            thresholds=thresholds.get(code, {}),
        )
        for code in sorted(set(evidence) | issue_codes | warning_codes)
    ]


def _source_input_window(plan: EditPlanDocument) -> tuple[float, float]:
    """Bound the decoded master using the clocks actually consumed by filters.

    J/L cuts have independent audio boundaries. Keep all persisted clocks
    absolute; only the FFmpeg input and its trim expressions use this local origin.
    Teaser segments are decoded from their own input (``_teaser_input_window``),
    so they never stretch this window.
    """
    segments = [segment for segment in plan.segments if not segment.teaser]
    intervals = [(segment.audio_start, segment.audio_end) for segment in segments]
    intervals.extend((segment.video_start, segment.video_end) for segment in segments)
    if not intervals:
        raise RenderPreconditionError("plano sem intervalos de mídia para renderizar")
    return round(min(start for start, _ in intervals), 6), round(max(end for _, end in intervals), 6)


def _teaser_input_window(plan: EditPlanDocument) -> tuple[float, float] | None:
    segments = [segment for segment in plan.segments if segment.teaser]
    if not segments:
        return None
    first_body = next((i for i, segment in enumerate(plan.segments) if not segment.teaser), None)
    if first_body is None or any(segment.teaser for segment in plan.segments[first_body:]):
        raise RenderPreconditionError("segmentos de teaser devem vir antes do corpo do plano")
    intervals = [(segment.audio_start, segment.audio_end) for segment in segments]
    intervals.extend((segment.video_start, segment.video_end) for segment in segments)
    return round(min(start for start, _ in intervals), 6), round(max(end for _, end in intervals), 6)


def _filtergraph(
    plan: EditPlanDocument,
    width: int,
    height: int,
    fps: int,
    loudness_target_lufs: float,
    true_peak_limit_dbfs: float,
    ass_overlay_path: Path | None = None,
    ass_fonts_dir: Path | None = None,
    framing_mode: str = "vertical_crop",
    punch_ins: dict[int, _PunchInResolution] | None = None,
    source_time_offset: float = 0.0,
    teaser_time_offset: float = 0.0,
    framing_position_y: float = 0.5,
    source_size: tuple[int, int] | None = None,
    framing_zoom: float = 1.0,
    framing_focus_x: float = 0.5,
    face_spans: list[FaceFocusSpan] | None = None,
    face_geometries: list[_CropGeometry] | None = None,
    face_punches: list[_PunchInResolution | None] | None = None,
    scale_flags: str | None = None,
) -> tuple[str, str, str]:
    video_parts: list[str] = []
    audio_parts: list[str] = []
    durations: list[float] = []
    geometry = _framing_geometry(
        framing_mode, source_size, width, height, framing_zoom, framing_focus_x,
    )
    if framing_mode == "blurred_background" and framing_zoom != 1.0 and geometry is None:
        raise RenderPreconditionError("zoom do enquadramento exige as dimensões da fonte")
    # Lossy AAC can overshoot the decoded PCM target. Keep a conservative
    # encoding margin while the publication gate still enforces the configured limit.
    normalization_true_peak = true_peak_limit_dbfs - _AAC_TRUE_PEAK_HEADROOM_DB
    # Draft previews pass a cheaper swscale algorithm; exports keep the default.
    sf = f":flags={scale_flags}" if scale_flags else ""

    def compose_video(
        input_filter: str, output_label: str, segment_order: int,
        face_index: int | None = None,
    ) -> str:
        # Punch-in (Gate 5) is a single extra static scale/crop bolted onto
        # the existing per-mode chain below — never a second temporal
        # transform. When there is no resolution, or the segment wasn't
        # selected for punch-in, every mode below produces byte-identical
        # output to pre-Gate-5 behavior.
        punch = (punch_ins or {}).get(segment_order)
        if punch is not None and not punch.applied:
            punch = None
        geo = geometry
        if face_index is not None:
            assert face_geometries is not None and face_punches is not None
            geo, punch = face_geometries[face_index], face_punches[face_index]
        effective_mode = framing_mode
        if effective_mode == "vertical_crop" and geo is not None and (
            punch is not None or face_index is not None
        ):
            crop_box = (
                (punch.crop_width, punch.crop_height, punch.anchor_x, punch.anchor_y)
                if punch is not None else (geo.w, geo.h, geo.x, geo.y)
            )
            return (
                f"{input_filter},fps={fps},crop={crop_box[0]}:{crop_box[1]}:"
                f"{crop_box[2]}:{crop_box[3]},scale={width}:{height}{sf},"
                f"setsar=1,format=yuv420p{output_label}"
            )
        if effective_mode == "vertical_crop":
            punch_chain = (
                f",crop={punch.crop_width}:{punch.crop_height}:"
                f"{punch.anchor_x}:{punch.anchor_y},scale={width}:{height}{sf}"
            ) if punch is not None else ""
            return (
                f"{input_filter},fps={fps},scale={width}:{height}:"
                f"force_original_aspect_ratio=increase{sf},crop={width}:{height}"
                f"{punch_chain},"
                f"setsar=1,format=yuv420p{output_label}"
            )
        if effective_mode == "blurred_background":
            # Only explicit full-frame composition has a manual position. Keep
            # automatic context shots centered, including crops taller than the
            # canvas after a punch-in (there is no free vertical space then).
            position_y = framing_position_y if framing_mode == "blurred_background" else 0.5
            foreground_y = "(H-h)/2" if position_y == 0.5 else (
                f"if(gte(H,h),(H-h)*{position_y:.6f},(H-h)/2)"
            )
            label_name = output_label[1:-1]
            blur_width = max(160, (width // 4) // 2 * 2)
            blur_height = max(160, (height // 4) // 2 * 2)
            background_raw = f"[{label_name}_bgraw]"
            foreground_raw = f"[{label_name}_fgraw]"
            background = f"[{label_name}_bg]"
            foreground = f"[{label_name}_fg]"
            source_crop: tuple[int, int, int, int] | None = None
            if framing_mode == "blurred_background" and geo is not None:
                if punch is not None:
                    source_crop = (
                        punch.crop_width, punch.crop_height, punch.anchor_x, punch.anchor_y,
                    )
                elif framing_zoom != 1.0 or face_index is not None:
                    source_crop = (geo.w, geo.h, geo.x, geo.y)
            if source_crop is not None:
                assert geo is not None
                # Crop in source pixels (zoom/focus and punch-in), then scale once
                # into the fixed display window.
                foreground_chain = (
                    f"crop={source_crop[0]}:{source_crop[1]}:{source_crop[2]}:{source_crop[3]},"
                    f"scale={geo.window_w}:{geo.window_h}{sf},setsar=1"
                )
            elif punch is not None:
                # Crop the source inside the fitted video window. Zoom must not
                # expand that window into the caption area selected in preview.
                foreground_chain = (
                    f"crop=trunc(iw/{punch.effective_scale}/2)*2:"
                    f"trunc(ih/{punch.effective_scale}/2)*2,"
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease{sf},setsar=1"
                )
            else:
                foreground_chain = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease{sf},setsar=1"
                )
            return (
                f"{input_filter},fps={fps},split=2{background_raw}{foreground_raw};"
                f"{background_raw}scale={blur_width}:{blur_height}:force_original_aspect_ratio=increase{sf},"
                f"crop={blur_width}:{blur_height},gblur=sigma=12:steps=2,"
                f"scale={width}:{height}{sf},setsar=1{background};"
                f"{foreground_raw}{foreground_chain}{foreground};{background}{foreground}"
                f"overlay=x=(W-w)/2:y='{foreground_y}':format=yuv420,format=yuv420p{output_label}"
            )
        raise RenderPreconditionError(f"modo de enquadramento não suportado: {framing_mode}")
    audio_durations: list[float] = []
    # Every segment gets an exact frame count on the cumulative timeline. Trimming
    # each segment on its own drops a fraction of a frame per cut while its audio
    # keeps sample precision, so across 10-16 cuts the picture ran ~0.13 s ahead
    # of the voice by the end of the clip.
    planned_elapsed = 0.0
    frames_emitted = 0

    def exact_frames(raw_label: str, frame_count: int, output_label: str) -> str:
        return (
            f"{raw_label}tpad=stop_mode=clone:stop_duration=1,"
            f"trim=end_frame={frame_count},setpts=PTS-STARTPTS{output_label}"
        )

    for index, segment in enumerate(plan.segments):
        video_duration = segment.video_end - segment.video_start
        audio_duration = segment.audio_end - segment.audio_start
        if video_duration <= 0 or audio_duration <= 0:
            raise RenderPreconditionError(f"segmento {index} possui duração inválida")
        durations.append(video_duration)
        audio_durations.append(audio_duration)
        planned_elapsed += video_duration
        segment_frames = max(1, round(planned_elapsed * fps) - frames_emitted)
        frames_emitted += segment_frames
        # Teaser segments break chronological order: they come from a second
        # input with its own window, never from the shared decoder.
        input_index = 1 if segment.teaser else 0
        offset = teaser_time_offset if segment.teaser else source_time_offset
        if face_spans:
            indexed = [
                position for position, span in enumerate(face_spans)
                if span.segment_order == segment.timeline_order
            ]
            if not indexed:
                raise RenderPreconditionError("foco nos rostos não cobre o segmento")
            if len(indexed) == 1:
                video_parts.append(compose_video(
                    f"[{input_index}:v]trim=start={segment.video_start - offset:.6f}:"
                    f"end={segment.video_end - offset:.6f},setpts=PTS-STARTPTS",
                    f"[vraw{index}]", segment.timeline_order, face_index=indexed[0],
                ))
                video_parts.append(exact_frames(f"[vraw{index}]", segment_frames, f"[v{index}]"))
            else:
                labels = []
                elapsed = 0.0
                frame_cursor = 0
                for part, position in enumerate(indexed):
                    span = face_spans[position]
                    raw_label = f"[facefocus{index}_{part}]"
                    label = f"[facetimed{index}_{part}]"
                    elapsed += span.source_end - span.source_start
                    frame_end = round(elapsed * fps)
                    frame_count = frame_end - frame_cursor
                    frame_cursor = frame_end
                    if frame_count <= 0:
                        continue
                    labels.append(label)
                    video_parts.append(compose_video(
                        f"[{input_index}:v]trim=start={span.source_start - offset:.6f}:"
                        f"end={span.source_end - offset:.6f},setpts=PTS-STARTPTS",
                        raw_label, segment.timeline_order, face_index=position,
                    ))
                    video_parts.append(
                        f"{raw_label}tpad=stop_mode=clone:stop_duration=1,"
                        f"trim=end_frame={frame_count},setpts=PTS-STARTPTS{label}"
                    )
                video_parts.append(
                    f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vraw{index}]"
                )
                video_parts.append(exact_frames(f"[vraw{index}]", segment_frames, f"[v{index}]"))
        else:
            video_parts.append(compose_video(
                f"[{input_index}:v]trim=start={segment.video_start - offset:.6f}:"
                f"end={segment.video_end - offset:.6f},"
                "setpts=PTS-STARTPTS",
                f"[vraw{index}]", segment.timeline_order,
            ))
            video_parts.append(exact_frames(f"[vraw{index}]", segment_frames, f"[v{index}]"))
        # Audio always follows the segment's own audio clock: a jl_cut boundary
        # only ever shifts audio_start/audio_end.
        audio_parts.append(
            f"[{input_index}:a]atrim=start={segment.audio_start - offset:.6f}:"
            f"end={segment.audio_end - offset:.6f},"
            f"asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:"
            f"channel_layouts=stereo[a{index}]"
        )

    video_duration_acc = durations[0]
    if len(plan.segments) == 1:
        audio_parts.append(
            f"[a0]loudnorm=I={loudness_target_lufs:.2f}:TP={normalization_true_peak:.2f}:"
            f"LRA=11:print_format=none,atrim=duration={durations[0]:.6f},"
            "asetpts=PTS-STARTPTS[anorm]"
        )
        filters = video_parts + audio_parts
        video_label = "[v0]"
    else:
        previous_video = "[v0]"
        previous_audio = "[a0]"
        video_duration_acc = durations[0]
        audio_duration_acc = audio_durations[0]
        transitions: list[str] = []
        for index in range(1, len(plan.segments)):
            configured = plan.segments[index - 1].transition
            kind = configured.kind if configured is not None else "crossfade"
            fade = configured.duration if configured is not None else 0.0
            fade = max(0.0, min(fade, durations[index - 1] / 2, durations[index] / 2))
            video_out = "[vout]" if index == len(plan.segments) - 1 else f"[vx{index}]"
            audio_out = "[aout]" if index == len(plan.segments) - 1 else f"[ax{index}]"
            if fade > 0:
                offset = max(0.001, video_duration_acc - fade)
                transitions.append(
                    f"{previous_video}[v{index}]xfade=transition=fade:duration={fade:.6f}:"
                    f"offset={offset:.6f}{video_out}"
                )
                video_duration_acc += durations[index] - fade
            else:
                transitions.append(f"{previous_video}[v{index}]concat=n=2:v=1:a=0{video_out}")
                video_duration_acc += durations[index]
            if kind in ("j_cut", "l_cut"):
                # The audio cut point for a j/l boundary is already baked
                # into audio_start/audio_end by the planner (Gate 4's
                # jl_cut resolver) — the two audio clips are adjacent, not
                # overlapping, editorial content. A duration-matched
                # acrossfade here (the crossfade/hard path below) would
                # double-apply the offset. Instead we join with a fixed,
                # short micro-crossfade — audible click removal only, never
                # an editorial dissolve — falling back to a hard concat
                # when either side's audio is too short to host even that.
                audio_fade = min(
                    _JL_AUDIO_MICROFADE_SECONDS, audio_durations[index - 1] / 2,
                    audio_durations[index] / 2,
                )
            else:
                audio_fade = fade
            if audio_fade > 0:
                transitions.append(
                    f"{previous_audio}[a{index}]acrossfade=d={audio_fade:.6f}:"
                    f"c1=tri:c2=tri{audio_out}"
                )
                audio_duration_acc += audio_durations[index] - audio_fade
            elif kind == "hard":
                # A hard dry cut (dynamic profile) must not lose or gain
                # audio at the join: an acrossfade here would shorten total
                # audio and, across many cuts in one clip, accumulate visible
                # A/V drift. Instead we declick with a short (~8ms) fade-out
                # on the outgoing tail and fade-in on the incoming head, then
                # concat — total audio duration stays exactly the sum of the
                # two parts, matching the video concat above.
                declick = min(
                    _HARD_CUT_DECLICK_SECONDS, audio_durations[index - 1] / 2,
                    audio_durations[index] / 2,
                )
                if declick > 0:
                    fade_out_label = f"[adeclickout{index}]"
                    fade_in_label = f"[adeclickin{index}]"
                    fade_start = max(0.0, audio_duration_acc - declick)
                    transitions.append(
                        f"{previous_audio}afade=t=out:st={fade_start:.6f}:"
                        f"d={declick:.6f}{fade_out_label}"
                    )
                    transitions.append(
                        f"[a{index}]afade=t=in:st=0:d={declick:.6f}{fade_in_label}"
                    )
                    transitions.append(
                        f"{fade_out_label}{fade_in_label}concat=n=2:v=0:a=1{audio_out}"
                    )
                else:
                    transitions.append(f"{previous_audio}[a{index}]concat=n=2:v=0:a=1{audio_out}")
                audio_duration_acc += audio_durations[index]
            else:
                transitions.append(f"{previous_audio}[a{index}]concat=n=2:v=0:a=1{audio_out}")
                audio_duration_acc += audio_durations[index]
            previous_video, previous_audio = video_out, audio_out
        transitions.append(
            f"{previous_audio}loudnorm=I={loudness_target_lufs:.2f}:"
            f"TP={normalization_true_peak:.2f}:LRA=11:print_format=none,"
            f"atrim=duration={video_duration_acc:.6f},asetpts=PTS-STARTPTS[anorm]"
        )
        filters = video_parts + audio_parts + transitions
        video_label = "[vout]"

    def escaped(path: Path) -> str:
        value = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        for character in ",;[]":
            value = value.replace(character, f"\\{character}")
        return value

    if ass_overlay_path is not None:
        assert ass_fonts_dir is not None
        filters.append(
            f"{video_label}subtitles=filename='{escaped(ass_overlay_path)}':"
            f"fontsdir='{escaped(ass_fonts_dir)}'[vass]"
        )
        video_label = "[vass]"
    # Trim after the overlay so nothing can extend the encoded video beyond the
    # physical EDL (and be mistaken for A/V drift by the gate).
    filters.append(
        f"{video_label}trim=duration={video_duration_acc:.6f},"
        "setpts=PTS-STARTPTS[vfinal]"
    )
    video_label = "[vfinal]"
    return ";".join(filters), video_label, "[anorm]"


_INTEGRATED_LOUDNESS_RE = re.compile(r"Integrated loudness:.*?I:\s*(-?\d+(?:\.\d+)?) LUFS", re.S)
_TRUE_PEAK_RE = re.compile(r"True peak:.*?Peak:\s*(-?\d+(?:\.\d+)?) dBFS", re.S)
_BLACK_DURATION_RE = re.compile(r"black_duration:(\d+(?:\.\d+)?)")
_FREEZE_START_RE = re.compile(r"lavfi\.freezedetect\.freeze_start:\s*(\d+(?:\.\d+)?)")
_FREEZE_DURATION_RE = re.compile(r"lavfi\.freezedetect\.freeze_duration:\s*(\d+(?:\.\d+)?)")


def _measure_loudness(ffmpeg: Path, media_path: Path) -> tuple[float, float]:
    command = [
        str(ffmpeg), "-hide_banner", "-nostats", "-v", "info", "-i", str(media_path),
        "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderExecutionError(f"medição de loudness falhou: {exc}") from exc
    if result.returncode:
        raise RenderExecutionError(
            f"medição de loudness falhou ({result.returncode}): {result.stderr[-4000:]}"
        )
    integrated_matches = _INTEGRATED_LOUDNESS_RE.findall(result.stderr)
    peak_matches = _TRUE_PEAK_RE.findall(result.stderr)
    if not integrated_matches or not peak_matches:
        raise RenderExecutionError("FFmpeg não retornou métricas EBU R128 completas")
    return float(integrated_matches[-1]), float(peak_matches[-1])


def _loudnorm_measurements(
    ffmpeg: Path, media_path: Path, target_lufs: float, true_peak_dbfs: float
) -> dict[str, float]:
    command = [
        str(ffmpeg), "-hide_banner", "-nostats", "-v", "info", "-i", str(media_path),
        "-map", "0:a:0", "-af",
        f"loudnorm=I={target_lufs:.2f}:TP={true_peak_dbfs:.2f}:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderExecutionError(f"primeira passada de loudness falhou: {exc}") from exc
    if result.returncode:
        raise RenderExecutionError(
            f"primeira passada de loudness falhou ({result.returncode}): {result.stderr[-4000:]}"
        )
    matches = re.findall(r"\{\s*\"input_i\".*?\}", result.stderr, flags=re.S)
    if not matches:
        raise RenderExecutionError("FFmpeg não retornou medições loudnorm em JSON")
    try:
        payload = json.loads(matches[-1])
        return {
            "input_i": float(payload["input_i"]),
            "input_lra": float(payload["input_lra"]),
            "input_tp": float(payload["input_tp"]),
            "input_thresh": float(payload["input_thresh"]),
            "target_offset": float(payload["target_offset"]),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RenderExecutionError("medições loudnorm inválidas") from exc


def _normalize_render_loudness(
    ffmpeg: Path,
    media_path: Path,
    *,
    target_lufs: float,
    true_peak_dbfs: float,
    log_path: Path,
    should_cancel: Callable[[], bool],
) -> None:
    """Correct an out-of-tolerance render with measured two-pass loudnorm.

    Video is stream-copied, so the correction cannot change framing, overlays,
    frame cadence or visual quality. Only the AAC track is regenerated.
    """
    measured = _loudnorm_measurements(ffmpeg, media_path, target_lufs, true_peak_dbfs)
    normalized_path = media_path.with_suffix(".loudnorm.mp4")
    audio_filter = (
        f"loudnorm=I={target_lufs:.2f}:TP={true_peak_dbfs:.2f}:LRA=11:"
        f"measured_I={measured['input_i']:.6f}:measured_LRA={measured['input_lra']:.6f}:"
        f"measured_TP={measured['input_tp']:.6f}:"
        f"measured_thresh={measured['input_thresh']:.6f}:"
        f"offset={measured['target_offset']:.6f}:linear=true:print_format=none"
    )
    command = [
        str(ffmpeg), "-y", "-v", "error", "-i", str(media_path),
        "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
        "-af", audio_filter, "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(normalized_path),
    ]
    try:
        _run_ffmpeg(command, log_path, should_cancel)
        normalized_path.replace(media_path)
    finally:
        normalized_path.unlink(missing_ok=True)


def _summarize_durations(durations: list[float]) -> tuple[int, float, float]:
    return len(durations), round(sum(durations), 4), round(max(durations, default=0.0), 4)


def _parse_visual_quality(stderr: str, actual_duration: float) -> dict[str, float | int]:
    black_durations = [float(value) for value in _BLACK_DURATION_RE.findall(stderr)]
    freeze_durations = [float(value) for value in _FREEZE_DURATION_RE.findall(stderr)]
    freeze_starts = [float(value) for value in _FREEZE_START_RE.findall(stderr)]
    if len(freeze_starts) > len(freeze_durations):
        freeze_durations.append(max(0.0, actual_duration - freeze_starts[-1]))
    black_count, black_total, black_max = _summarize_durations(black_durations)
    freeze_count, freeze_total, freeze_max = _summarize_durations(freeze_durations)
    return {
        "black_interval_count": black_count,
        "black_total_duration_seconds": black_total,
        "black_max_duration_seconds": black_max,
        "freeze_interval_count": freeze_count,
        "freeze_total_duration_seconds": freeze_total,
        "freeze_max_duration_seconds": freeze_max,
    }


def _measure_visual_quality(
    ffmpeg: Path,
    media_path: Path,
    *,
    actual_duration: float,
    black_threshold_seconds: float,
    freeze_threshold_seconds: float,
) -> dict[str, float | int]:
    filters = (
        f"blackdetect=d={black_threshold_seconds:.3f}:pix_th=0.10:pic_th=0.98,"
        f"freezedetect=n=-50dB:d={freeze_threshold_seconds:.3f}"
    )
    command = [
        str(ffmpeg), "-hide_banner", "-nostats", "-v", "info", "-i", str(media_path),
        "-map", "0:v:0", "-vf", filters, "-an", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderExecutionError(f"análise visual falhou: {exc}") from exc
    if result.returncode:
        raise RenderExecutionError(
            f"análise visual falhou ({result.returncode}): {result.stderr[-4000:]}"
        )
    return _parse_visual_quality(result.stderr, actual_duration)


_BBOX_ALPHA_MIN_VAL = 16
_SAFE_ZONE_TOLERANCE_PX = 2


def _run_ffmpeg(
    command: list[str], log_path: Path, should_cancel: Callable[[], bool], *,
    progress_cb: Callable[[float, str], None] | None = None,
    duration_seconds_value: float | None = None, timeout_seconds: float = 3600,
) -> None:
    import math
    import os
    import selectors
    import signal

    if should_cancel():
        raise RenderJobCancelled()
    report_progress = progress_cb is not None
    if report_progress:
        if (duration_seconds_value is None or not math.isfinite(duration_seconds_value)
                or duration_seconds_value <= 0):
            raise RenderPreconditionError("progresso FFmpeg exige duração final positiva e finita")
        command = [command[0], "-nostdin", "-nostats", "-stats_period", "0.5",
                   "-progress", "pipe:1", *command[1:]]
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise RenderPreconditionError("timeout FFmpeg deve ser positivo e finito")
    with log_path.open("w+b") as log, selectors.DefaultSelector() as selector:
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE if report_progress else subprocess.DEVNULL,
                stderr=subprocess.PIPE, start_new_session=True,
            )
        except OSError as exc:
            raise RenderExecutionError(f"FFmpeg não pôde ser executado: {exc}") from exc
        assert process.stderr is not None
        selector.register(process.stderr, selectors.EVENT_READ, "log")
        if process.stdout is not None:
            selector.register(process.stdout, selectors.EVENT_READ, "progress")
        started = time.monotonic()
        pending = b""
        discard_line = False
        output_us = None
        last_percent = -1.0
        max_log_bytes = 1024 * 1024

        def check_running():
            if should_cancel():
                raise RenderJobCancelled()
            if time.monotonic() - started > timeout_seconds:
                raise RenderExecutionError(f"FFmpeg excedeu o timeout de {timeout_seconds:g}s")

        try:
            while selector.get_map():
                check_running()
                ready = selector.select(timeout=0.1)
                if not ready and process.poll() is not None:
                    break
                for key, _events in ready:
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if log.tell() + len(chunk) > max_log_bytes:
                        log.seek(-max_log_bytes // 2, os.SEEK_END)
                        tail = log.read()
                        log.seek(0)
                        log.write(tail)
                        log.truncate()
                    log.write(chunk)
                    log.flush()
                    if key.data != "progress":
                        continue
                    for part in chunk.splitlines(keepends=True):
                        finished = part.endswith(b"\n")
                        if not discard_line:
                            pending += part
                            if len(pending) > 8192:
                                pending, discard_line = b"", True
                        if not finished:
                            continue
                        if not discard_line:
                            line = pending.strip()
                            if line.startswith(b"out_time_us="):
                                value = line.partition(b"=")[2]
                                output_us = int(value) if re.fullmatch(rb"\d{1,20}", value) else None
                            elif line in (b"progress=continue", b"progress=end") and output_us is not None:
                                assert duration_seconds_value is not None and progress_cb is not None
                                seconds = min(output_us / 1_000_000, duration_seconds_value)
                                percent = seconds / duration_seconds_value * 100
                                if percent > last_percent:
                                    last_percent = percent
                                    progress_cb(percent, f"FFmpeg: {seconds:.1f}/{duration_seconds_value:.1f} s de vídeo codificados")
                                output_us = None
                        pending, discard_line = b"", False
            while process.poll() is None:
                check_running()
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
            process.stderr.close()
            if process.stdout is not None:
                process.stdout.close()
        if process.returncode:
            log.seek(max(0, log.tell() - 4000))
            detail = log.read(4000).decode(errors="replace")
            raise RenderExecutionError(f"FFmpeg falhou ({process.returncode}): {detail}")


class RenderService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def _safe_zone_issues_libass(
        self,
        *,
        caption_bbox: tuple[float, float, float, float] | None,
        headline_bbox: tuple[float, float, float, float] | None,
        width: int,
        height: int,
        safe_zone,
    ) -> list[str]:
        """Equivalente geométrico de _safe_zone_issues para o libass: a bounding
        box já foi calculada a partir da mesma geometria usada para escrever o
        .ass (cortex.render.ass_overlay), então não há necessidade de amostrar
        pixels de um WebM alpha."""
        if safe_zone is None:
            return ["safe_zone_missing"]
        left_px = safe_zone.left * width
        right_limit = (1.0 - safe_zone.right) * width
        top_px = safe_zone.top * height
        bottom_limit = (1.0 - safe_zone.bottom) * height
        tol = _SAFE_ZONE_TOLERANCE_PX
        issues: list[str] = []
        if headline_bbox is not None:
            hx1, hy1, hx2, hy2 = headline_bbox
            if (hx1 < left_px - tol or hx2 > right_limit + tol
                    or hy1 < top_px - tol or hy2 > bottom_limit + tol):
                issues.append("headline_outside_safe_zone")
        if caption_bbox is not None:
            cx1, cy1, cx2, cy2 = caption_bbox
            if (cx1 < left_px - tol or cx2 > right_limit + tol
                    or cy1 < top_px - tol or cy2 > bottom_limit + tol):
                issues.append("caption_outside_safe_zone")
        canvas = canvas_key(width, height)
        issues.extend(occlusion_issues(
            canvas=canvas, width=width, height=height, element="headline", bbox=headline_bbox,
        ))
        issues.extend(occlusion_issues(
            canvas=canvas, width=width, height=height, element="caption", bbox=caption_bbox,
        ))
        return issues

    def run(
        self,
        *,
        edit_plan_artifact: StageArtifact,
        encoder: str | None,
        headline: str | None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
        render_settings: RenderSettings | None = None,
        render_settings_override: RenderSettingsPatch | None = None,
        export_directory: str | None = None,
        purpose: Literal["export", "draft"] = "export",
    ) -> dict:
        draft = purpose == "draft"
        total_started = time.monotonic()
        timings: dict[str, float] = {}
        if edit_plan_artifact.stage != "edit_plan":
            raise RenderPreconditionError("artifact informado não é um EditPlanArtifact")
        plan_path = Path(edit_plan_artifact.path)
        if not plan_path.exists():
            raise RenderPreconditionError("arquivo do EditPlanArtifact não está disponível")
        plan = EditPlanDocument.model_validate_json(plan_path.read_text(encoding="utf-8"))
        if not plan.quality.passed:
            raise RenderPreconditionError("EditPlanArtifact não passou pelo quality gate")
        source = self._domain.get_source_asset(plan.source_asset_id)
        if source.project_id != plan.project_id or plan.project_id != edit_plan_artifact.project_id:
            raise RenderPreconditionError("fonte, plano e projeto não correspondem")
        source_path = self._config.paths.data_dir / source.stored_path
        if not source_path.exists():
            raise RenderPreconditionError("arquivo fonte não está disponível")
        requested_settings = render_settings or _default_render_settings(
            self._config, encoder=encoder, headline=headline
        )
        requested_settings = _merge_render_settings(requested_settings, render_settings_override)
        requested_encoder = requested_settings.encoder.strip()
        _encoder_args(requested_encoder, draft=draft)
        if requested_encoder == "h264_nvenc":
            reason = nvenc_unavailable_reason(self._config.render.ffmpeg)
            if reason is not None:
                raise RenderPreconditionError(
                    "NVENC indisponível neste FFmpeg/driver; configure render.encoder: "
                    f"libx264. Detalhe: {reason}"
                )
        if requested_settings.framing.mode in LEGACY_FRAMING_MODES:
            raise RenderPreconditionError(LEGACY_FRAMING_MODE_MESSAGE)
        source_probe = source.probe or probe_media(self._config.render.ffprobe, source_path)
        source_duration = usable_av_duration_seconds(source_probe)
        if source_duration <= 0:
            raise RenderPreconditionError("duração física da fonte não está disponível")
        effective_segments = []
        for segment in plan.segments:
            if segment.video_start >= source_duration or segment.audio_start >= source_duration:
                raise RenderPreconditionError("segmento da EDL inicia após o fim físico da fonte")
            effective_end = min(segment.end, source_duration)
            effective_video_end = min(segment.video_end, source_duration)
            effective_audio_end = min(segment.audio_end, source_duration)
            if effective_video_end <= segment.video_start or effective_audio_end <= segment.audio_start:
                raise RenderPreconditionError("segmento da EDL não possui mídia física suficiente")
            effective_segments.append(segment.model_copy(update={
                "end": effective_end,
                "video_end": effective_video_end,
                "audio_end": effective_audio_end,
            }))
        effective_duration = sum(
            segment.video_end - segment.video_start for segment in effective_segments
        ) - sum(
            (segment.transition.duration if segment.transition is not None else 0.0)
            for segment in effective_segments[:-1]
        )
        plan = plan.model_copy(update={
            "segments": effective_segments,
            "clip_end": min(plan.clip_end, source_duration),
            "timeline_duration_seconds": round(max(0.0, effective_duration), 4),
        })

        try:
            transcript_artifact = self._domain.get_transcript_artifact(plan.transcript_artifact_id)
        except TranscriptArtifactNotFoundError as exc:
            raise RenderPreconditionError("TranscriptArtifact do plano não está disponível") from exc
        transcript_path = Path(transcript_artifact.path)
        if (
            transcript_artifact.project_id != plan.project_id
            or transcript_artifact.source_asset_id != source.id
            or not transcript_path.is_file()
        ):
            raise RenderPreconditionError("transcript, fonte e plano não correspondem")
        transcript = TranscriptDocument.model_validate_json(transcript_path.read_text(encoding="utf-8"))
        timings["plan_load"] = round(time.monotonic() - total_started, 4)
        caption_font = _resolve_font_family(requested_settings.captions.font_family)
        headline_font = _resolve_font_family(requested_settings.headline.font_family)
        headline_text = normalize_headline_text(requested_settings.headline.text)
        effective_settings = requested_settings.model_copy(update={
            "encoder": requested_encoder,
            "framing": requested_settings.framing.model_copy(update={
                "position_y": (requested_settings.framing.position_y
                               if requested_settings.framing.mode == "blurred_background" else 0.5),
                "zoom": (requested_settings.framing.zoom
                         if requested_settings.framing.mode == "blurred_background" else 1.0),
                "focus_x": (requested_settings.framing.focus_x
                            if requested_settings.framing.mode == "blurred_background" else 0.5),
            }),
            "captions": requested_settings.captions.model_copy(update={
                "font_family": caption_font,
            }),
            "headline": effective_headline_settings(
                requested_settings.headline, headline_text, plan.timeline_duration_seconds,
                font_family=headline_font,
            ),
        })
        if draft:
            # Previews are cheap: half canvas, no burned typography or flashes.
            effective_settings = effective_settings.model_copy(update={
                "canvas": RenderCanvasSettings(
                    width=max(320, effective_settings.canvas.width // 2 // 2 * 2),
                    height=max(320, effective_settings.canvas.height // 2 // 2 * 2),
                    fps=effective_settings.canvas.fps,
                ),
                "subtitles": RenderSubtitleSettings(sidecar_srt=False),
            })
        requested_renderer = "libass"  # the only overlay renderer (kept in cache keys)
        fonts_dir = Path(self._config.render.fonts_dir)
        if not fonts_dir.is_absolute():
            fonts_dir = Path(__file__).resolve().parents[3] / fonts_dir
        if requested_renderer == "libass":
            try:
                if effective_settings.captions.enabled:
                    from cortex.render.ass_overlay import resolve_caption_font
                    resolve_caption_font(
                        fonts_dir, effective_settings.captions.font_family,
                        effective_settings.captions.font_weight,
                    )
                if effective_settings.headline.enabled and headline_text:
                    from cortex.render.ass_overlay import resolve_headline_font
                    resolve_headline_font(fonts_dir, effective_settings.headline.font_family)
            except AssOverlayError as exc:
                raise RenderPreconditionError(str(exc)) from exc
        # Punch-in (Gate 5): resolved once, up front, so an invalid crop or
        # an unsafe upscale fails closed before any ffmpeg process starts.
        punch_in_settings = effective_settings.framing.punch_in
        video_stream_info = next(
            (item for item in source_probe.get("streams", []) if item.get("codec_type") == "video"),
            None,
        )
        source_size = (
            (int(video_stream_info["width"]), int(video_stream_info["height"]))
            if video_stream_info and video_stream_info.get("width") and video_stream_info.get("height")
            else None
        )
        if effective_settings.framing.zoom != 1.0 and source_size is None:
            raise RenderPreconditionError("zoom do enquadramento exige as dimensões da fonte")
        crop_geometry = _framing_geometry(
            effective_settings.framing.mode, source_size,
            effective_settings.canvas.width, effective_settings.canvas.height,
            effective_settings.framing.zoom, effective_settings.framing.focus_x,
        )
        face_spans: list[FaceFocusSpan] = []
        face_geometries: list[_CropGeometry] = []
        face_crop_fraction: float | None = None
        requested_focus_mode = requested_settings.framing.focus_mode
        effective_focus_mode = effective_settings.framing.focus_mode
        if effective_focus_mode == "faces":
            if source_size is None or crop_geometry is None:
                raise RenderPreconditionError("foco nos rostos exige as dimensões da fonte")
            face_crop_fraction = crop_geometry.w / source_size[0]
            face_focus_started = time.monotonic()
            try:
                face_spans = resolve_face_focus(
                    ffmpeg=self._config.render.ffmpeg, source_path=source_path,
                    source_sha256=source.sha256, segments=plan.segments,
                    scene_threshold=self._config.analysis.scene_threshold,
                    cache_dir=cache_dir(self._config, plan.project_id),
                    crop_width_fraction=face_crop_fraction,
                    manual_focus_x=effective_settings.framing.focus_x,
                    primary_side=effective_settings.framing.primary_side,
                    should_cancel=should_cancel,
                )
            except FaceFocusCancelled as exc:
                raise RenderJobCancelled() from exc
            except FaceFocusError as exc:
                raise RenderPreconditionError(
                    f"não foi possível detectar rostos para o foco automático: {exc}"
                ) from exc
            timings["face_focus"] = round(time.monotonic() - face_focus_started, 4)
            face_geometries = [
                _focused_geometry(
                    effective_settings.framing.mode, source_size,
                    effective_settings.canvas.width, effective_settings.canvas.height,
                    effective_settings.framing.zoom, span.focus_x,
                )
                for span in face_spans
            ]
        junction_orders = frozenset(
            plan.segments[position].timeline_order for position, _moment in piece_junctions(plan)
        ) if (
            effective_settings.transitions.piece_junction == "zoom" and not draft
        ) else frozenset()
        punch_in_resolutions = _resolve_punch_ins(
            plan, punch_in_settings,
            framing_mode=effective_settings.framing.mode,
            canvas_width=effective_settings.canvas.width,
            canvas_height=effective_settings.canvas.height,
            geometry=crop_geometry, junction_orders=junction_orders,
        )
        punch_in_by_segment = {
            resolution.segment_order: resolution for resolution in punch_in_resolutions
        }
        face_punches: list[_PunchInResolution | None] = []
        for span, span_geometry in zip(face_spans, face_geometries):
            segment_punch = punch_in_by_segment.get(span.segment_order)
            face_punches.append(
                _resolve_segment_punch_in(
                    span.segment_order, punch_in_settings,
                    framing_mode=effective_settings.framing.mode,
                    canvas_width=effective_settings.canvas.width,
                    canvas_height=effective_settings.canvas.height,
                    geometry=span_geometry,
                    scale_override=segment_punch.effective_scale,
                )
                if segment_punch is not None and segment_punch.applied else None
            )
        punch_ins_manifest = [
            RenderPunchInInfo(
                segment_order=resolution.segment_order,
                requested_scale=resolution.requested_scale,
                effective_scale=resolution.effective_scale,
                anchor_x=resolution.anchor_x,
                anchor_y=resolution.anchor_y,
                applied=resolution.applied,
                reason=resolution.reason,
                junction=resolution.segment_order in junction_orders,
            )
            for resolution in punch_in_resolutions
        ]
        canvas_key = _safe_zone_key(effective_settings.canvas.width, effective_settings.canvas.height)
        safe_zone = SAFE_ZONES.get(canvas_key)
        if safe_zone is None:
            raise RenderPreconditionError(f"safe zone não definido para canvas {canvas_key}")
        output_dir = renders_dir(self._config, plan.project_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        needs_cues = not draft and (
            effective_settings.captions.enabled or effective_settings.subtitles.sidecar_srt
        )
        try:
            plan_overlay = build_plan_overlay(
                plan=plan, transcript=transcript, settings=effective_settings,
                fonts_dir=fonts_dir, headline_text=headline_text, needs_cues=needs_cues,
                build_ass=requested_renderer == "libass" and not draft,
            )
        except ValueError as exc:
            raise RenderPreconditionError(str(exc)) from exc
        cues = plan_overlay.cues
        if needs_cues and not cues:
            raise RenderPreconditionError("nenhuma palavra do transcript coincide com a EDL")
        overlay_enabled = not draft and bool(
            effective_settings.captions.enabled
            or (effective_settings.headline.enabled and headline_text)
            or plan_overlay.ass is not None
        )
        overlay_summary: _OverlaySummary | None = None
        ass_path: Path | None = None
        ass_result: AssOverlayResult | None = None
        if overlay_enabled:
            progress_cb(10.0, "Gerando legendas")
            overlay_started = time.monotonic()
            ass_result = plan_overlay.ass
            assert ass_result is not None
            ass_path = output_dir / f"ass-{ass_result.content_hash[:16]}.ass"
            if not ass_path.is_file():
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=output_dir, prefix=".ass-", suffix=".tmp", delete=False,
                ) as handle:
                    handle.write(ass_result.ass_text)
                    ass_tmp_path = Path(handle.name)
                ass_tmp_path.replace(ass_path)
            font_files = {
                font.filename: font.sha256
                for font in (ass_result.caption_font, ass_result.headline_font)
                if font is not None
            }
            overlay_summary = _OverlaySummary(
                renderer="libass", output_path=str(ass_path), output_sha256=_sha256(ass_path),
                output_size_bytes=ass_path.stat().st_size, cache_key=ass_result.content_hash,
                codec="ass", font_files=font_files or None,
                generator_version=ass_result.generator_version,
            )
            timings["overlay"] = round(time.monotonic() - overlay_started, 4)
        input_start, input_end = _source_input_window(plan)
        teaser_window = _teaser_input_window(plan)
        input_payload = {
            "schema_version": RENDER_SCHEMA_VERSION,
            "input_window": {"version": _INPUT_WINDOW_VERSION, "start": input_start, "end": input_end},
            "timeline_version": _TIMELINE_VERSION,
            "edit_plan_sha256": _sha256(plan_path),
            "source_sha256": source.sha256,
            "face_focus": {
                "mode": effective_focus_mode,
                "algorithm_version": FACE_FOCUS_ALGORITHM_VERSION if face_spans else None,
                "spans": [span.model_dump() for span in face_spans],
            },
            "render_sources": {source.id: source.sha256},
            "encoder": requested_encoder,
            "render_settings": effective_settings.model_dump(mode="json"),
            "render_settings_override": (
                render_settings_override.model_dump(mode="json", exclude_none=True)
                if render_settings_override is not None else None
            ),
            "loudness_target_lufs": self._config.render.loudness_target_lufs,
            "loudness_tolerance_lu": self._config.render.loudness_tolerance_lu,
            "true_peak_limit_dbfs": self._config.render.true_peak_limit_dbfs,
            "visual_quality_enabled": self._config.render.visual_quality_enabled,
            "black_threshold_seconds": self._config.render.black_threshold_seconds,
            "freeze_threshold_seconds": self._config.render.freeze_threshold_seconds,
            "transcript_sha256": _sha256(transcript_path),
            "purpose": purpose,
            "junction_times": list(plan_overlay.junctions),
            "overlay_renderer": requested_renderer if overlay_enabled else "none",
            "overlay_input_hash": overlay_summary.cache_key if overlay_summary else None,
            "overlay_sha256": overlay_summary.output_sha256 if overlay_summary else None,
            "safe_zone_version": SAFE_ZONES_VERSION,
            "composition_version": 2,
            **({"teaser_window": list(teaser_window)} if teaser_window else {}),
            "safe_zone_canvas": canvas_key,
            "technical_quality_version": TECHNICAL_QUALITY_VERSION,
        }
        input_hash = hashlib.sha256(
            json.dumps(input_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cached = self._domain.find_cached_stage_artifact(
            project_id=plan.project_id, stage="render", input_hash=input_hash,
            schema_version=RENDER_SCHEMA_VERSION,
        )
        if cached is not None:
            cached_doc = RenderDocument.model_validate_json(Path(cached.path).read_text())
            subtitles_available = (
                not effective_settings.subtitles.sidecar_srt
                or (cached_doc.subtitles_path is not None and Path(cached_doc.subtitles_path).is_file())
            )
            overlay_available = (
                overlay_summary is None
                or (
                    cached_doc.overlays is not None
                    and cached_doc.overlays.artifact_sha256 == overlay_summary.output_sha256
                    and Path(overlay_summary.output_path).is_file()
                )
            )
            if Path(cached_doc.output_path).exists() and subtitles_available and overlay_available:
                exports = {}
                if cached_doc.publication is None or cached_doc.publication.publish_ready:
                    exports = _export_render_files(
                        self._config,
                        output_path=Path(cached_doc.output_path),
                        subtitles_path=(
                            Path(cached_doc.subtitles_path) if cached_doc.subtitles_path else None
                        ),
                        export_directory=export_directory,
                        input_hash=input_hash,
                    )
                progress_cb(100.0, "Render em cache reutilizado")
                return {"cached": True, "purpose": purpose, "render_artifact_id": cached.id,
                        "render_path": cached_doc.output_path,
                        "schema_version": cached.schema_version,
                        "publish_ready": (
                            cached_doc.publication.publish_ready
                            if cached_doc.publication is not None else cached_doc.quality.passed
                        ), **exports}

        if should_cancel():
            raise RenderJobCancelled()
        progress_cb(24.0, "Validando fonte e plano")
        stream_types = {
            stream.get("codec_type") for stream in source_probe.get("streams", [])
        }
        if not {"video", "audio"}.issubset(stream_types):
            raise RenderPreconditionError("render requer fonte com streams de áudio e vídeo")
        stem = f"{'draft-' if draft else ''}render-{input_hash[:16]}"
        output_path = output_dir / f"{stem}.mp4"
        temporary_path = output_path.with_suffix(".tmp.mp4")
        subtitles_path = output_dir / f"{stem}.srt"
        temporary_subtitles_path = output_dir / f".{stem}.tmp.srt"
        if cues:
            temporary_subtitles_path.write_text(render_srt(cues), encoding="utf-8")
        ordered_source_ids = [source.id]
        filtergraph, video_label, audio_label = _filtergraph(
            plan,
            effective_settings.canvas.width,
            effective_settings.canvas.height,
            effective_settings.canvas.fps,
            self._config.render.loudness_target_lufs,
            self._config.render.true_peak_limit_dbfs,
            framing_mode=effective_settings.framing.mode,
            punch_ins=punch_in_by_segment,
            ass_overlay_path=ass_path,
            ass_fonts_dir=(fonts_dir if ass_path is not None else None),
            source_time_offset=input_start,
            teaser_time_offset=teaser_window[0] if teaser_window else 0.0,
            framing_position_y=effective_settings.framing.position_y,
            source_size=source_size,
            framing_zoom=effective_settings.framing.zoom,
            framing_focus_x=effective_settings.framing.focus_x,
            face_spans=face_spans,
            face_geometries=face_geometries,
            face_punches=face_punches,
            scale_flags=_DRAFT_SCALE_FLAGS if draft else None,
        )
        command = [str(self._config.render.ffmpeg), "-y", "-v", "error"]
        command.extend([
            "-ss", f"{input_start:.6f}", "-t", f"{input_end - input_start:.6f}",
            "-i", str(source_path),
        ])
        if teaser_window is not None:
            command.extend([
                "-ss", f"{teaser_window[0]:.6f}", "-t", f"{teaser_window[1] - teaser_window[0]:.6f}",
                "-i", str(source_path),
            ])
        command.extend([
            "-filter_complex", filtergraph, "-map", video_label, "-map", audio_label,
            *_encoder_args(requested_encoder, draft=draft), "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-movflags", "+faststart", str(temporary_path),
        ])
        progress_cb(25.0, f"Renderizando com {requested_encoder}")
        log_path = output_dir / f"{stem}.ffmpeg.log"
        try:
            encode_started = time.monotonic()
            _run_ffmpeg(
                command, log_path, should_cancel,
                duration_seconds_value=plan.timeline_duration_seconds,
                progress_cb=lambda percent, message: progress_cb(25.0 + percent * 0.59, message),
            )
            timings["ffmpeg_encode"] = round(time.monotonic() - encode_started, 4)
            quality_started = time.monotonic()
            loudness_check_seconds = loudness_correction_seconds = 0.0
            progress_cb(85.0, "Validando streams, duração e loudness")
            rendered_probe = probe_media(self._config.render.ffprobe, temporary_path)
            actual_duration = duration_seconds(rendered_probe)
            types = {stream.get("codec_type") for stream in rendered_probe.get("streams", [])}
            delta = abs(actual_duration - plan.timeline_duration_seconds)
            issues: list[str] = []
            warnings: list[str] = []
            integrated_loudness: float | None = None
            true_peak: float | None = None
            loudness_delta: float | None = None
            if "audio" in types:
                loudness_started = time.monotonic()
                integrated_loudness, true_peak = _measure_loudness(
                    self._config.render.ffmpeg, temporary_path
                )
                loudness_check_seconds = time.monotonic() - loudness_started
                loudness_delta = abs(
                    integrated_loudness - self._config.render.loudness_target_lufs
                )
                # Previews keep the in-graph loudnorm but skip the second encode.
                if loudness_delta > self._config.render.loudness_tolerance_lu and not draft:
                    progress_cb(87.0, "Corrigindo loudness com medição em duas passadas")
                    correction_started = time.monotonic()
                    _normalize_render_loudness(
                        self._config.render.ffmpeg,
                        temporary_path,
                        target_lufs=self._config.render.loudness_target_lufs,
                        true_peak_dbfs=self._config.render.true_peak_limit_dbfs,
                        log_path=output_dir / f"{stem}.loudnorm.log",
                        should_cancel=should_cancel,
                    )
                    rendered_probe = probe_media(self._config.render.ffprobe, temporary_path)
                    actual_duration = duration_seconds(rendered_probe)
                    types = {
                        stream.get("codec_type") for stream in rendered_probe.get("streams", [])
                    }
                    delta = abs(actual_duration - plan.timeline_duration_seconds)
                    integrated_loudness, true_peak = _measure_loudness(
                        self._config.render.ffmpeg, temporary_path
                    )
                    loudness_delta = abs(
                        integrated_loudness - self._config.render.loudness_target_lufs
                    )
                    loudness_correction_seconds = time.monotonic() - correction_started
            if "video" not in types:
                issues.append("video_stream_missing")
            if "audio" not in types:
                issues.append("audio_stream_missing")
            if delta > 0.15:
                issues.append("duration_mismatch")
            try:
                technical, technical_issues = measure_technical_quality(
                    ffmpeg=self._config.render.ffmpeg,
                    ffprobe=self._config.render.ffprobe,
                    media_path=temporary_path,
                    probe=rendered_probe,
                    should_cancel=should_cancel,
                )
            except TechnicalQualityCancelled as exc:
                raise RenderJobCancelled() from exc
            issues.extend(technical_issues)
            if (
                technical.av_sync_delta_seconds is not None
                and 0.03 < technical.av_sync_delta_seconds <= 0.04
            ):
                warnings.append("av_sync_near_limit")
            if "audio" in types:
                if (
                    loudness_delta is not None
                    and loudness_delta > self._config.render.loudness_tolerance_lu
                ):
                    (warnings if draft else issues).append("loudness_out_of_tolerance")
                if true_peak is not None and true_peak > self._config.render.true_peak_limit_dbfs:
                    issues.append("true_peak_exceeded")
                elif (
                    true_peak is not None
                    and true_peak > self._config.render.true_peak_limit_dbfs - 0.25
                ):
                    warnings.append("true_peak_near_limit")
            if needs_cues and (
                not cues or not temporary_subtitles_path.is_file()
            ):
                issues.append("subtitles_missing")
            visual_metrics: dict[str, float | int] = {
                "black_interval_count": 0,
                "black_total_duration_seconds": 0.0,
                "black_max_duration_seconds": 0.0,
                "freeze_interval_count": 0,
                "freeze_total_duration_seconds": 0.0,
                "freeze_max_duration_seconds": 0.0,
            }
            visual_analysis = self._config.render.visual_quality_enabled and not draft
            if visual_analysis and "video" in types:
                visual_metrics = _measure_visual_quality(
                    self._config.render.ffmpeg,
                    temporary_path,
                    actual_duration=actual_duration,
                    black_threshold_seconds=self._config.render.black_threshold_seconds,
                    freeze_threshold_seconds=self._config.render.freeze_threshold_seconds,
                )
                if visual_metrics["black_interval_count"]:
                    issues.append("black_frame_detected")
                if (
                    visual_metrics["freeze_max_duration_seconds"]
                    >= self._config.render.freeze_block_threshold_seconds
                ):
                    issues.append("frozen_frame_detected")
                elif visual_metrics["freeze_interval_count"]:
                    warnings.append("frozen_frame_detected")
            if ass_result is not None:
                issues.extend(self._safe_zone_issues_libass(
                    caption_bbox=ass_result.caption_bbox,
                    headline_bbox=ass_result.headline_bbox,
                    width=effective_settings.canvas.width,
                    height=effective_settings.canvas.height,
                    safe_zone=safe_zone,
                ))
            check_evidence: dict[
                str, dict[str, bool | float | int | str | None]
            ] = {
                "video_stream_missing": {"present": "video" in types},
                "audio_stream_missing": {"present": "audio" in types},
                "duration_mismatch": {
                    "expected_seconds": round(plan.timeline_duration_seconds, 6),
                    "actual_seconds": round(actual_duration, 6),
                    "delta_seconds": round(delta, 6),
                },
                "loudness_out_of_tolerance": {
                    "integrated_lufs": integrated_loudness,
                    "target_lufs": self._config.render.loudness_target_lufs,
                    "delta_lu": round(loudness_delta, 6) if loudness_delta is not None else None,
                },
                "true_peak_exceeded": {"true_peak_dbfs": true_peak},
                "true_peak_near_limit": {"true_peak_dbfs": true_peak},
                "subtitles_missing": {
                    "required": needs_cues,
                    "cue_count": len(cues),
                    "sidecar_present": temporary_subtitles_path.is_file(),
                },
                "black_frame_detected": {
                    "interval_count": int(visual_metrics["black_interval_count"]),
                    "maximum_seconds": float(visual_metrics["black_max_duration_seconds"]),
                },
                "frozen_frame_detected": {
                    "interval_count": int(visual_metrics["freeze_interval_count"]),
                    "maximum_seconds": float(visual_metrics["freeze_max_duration_seconds"]),
                },
                "caption_outside_safe_zone": {"overlay_present": overlay_summary is not None},
                "headline_outside_safe_zone": {"overlay_present": overlay_summary is not None},
                "safe_zone_missing": {"canvas": canvas_key, "defined": safe_zone is not None},
                "full_decode_failed": {"passed": technical.full_decode_passed},
                "faststart_missing": {
                    "faststart": technical.faststart,
                    "moov_offset": technical.moov_offset,
                    "mdat_offset": technical.mdat_offset,
                },
                "pts_discontinuity_detected": {
                    "count": technical.pts_discontinuity_count,
                },
                "dts_discontinuity_detected": {
                    "count": technical.dts_discontinuity_count,
                },
                "timestamp_analysis_failed": {
                    "engine": technical.engine.get("ffprobe"),
                },
                "av_sync_out_of_tolerance": {
                    "delta_seconds": technical.av_sync_delta_seconds,
                },
                "av_sync_near_limit": {
                    "delta_seconds": technical.av_sync_delta_seconds,
                },
                "audio_channels_invalid": {"channels": technical.audio_channel_count},
                "audio_clipping_detected": {
                    "sample_count": technical.clipped_sample_count,
                    "peak_amplitude": technical.audio_peak_amplitude,
                },
                "audio_waveform_jump_detected": {
                    "jump_count": technical.waveform_jump_count,
                },
                "audio_channels_inverted": {
                    "phase_correlation": technical.channel_phase_correlation,
                },
                "audio_analysis_failed": {"engine": technical.engine.get("ffmpeg")},
            }
            check_thresholds: dict[str, dict[str, float | int | str]] = {
                "duration_mismatch": {"maximum_delta_seconds": 0.15},
                "loudness_out_of_tolerance": {
                    "maximum_delta_lu": self._config.render.loudness_tolerance_lu,
                },
                "true_peak_exceeded": {
                    "maximum_dbfs": self._config.render.true_peak_limit_dbfs,
                },
                "true_peak_near_limit": {
                    "warning_margin_db": 0.25,
                    "maximum_dbfs": self._config.render.true_peak_limit_dbfs,
                },
                "black_frame_detected": {
                    "minimum_interval_seconds": self._config.render.black_threshold_seconds,
                },
                "frozen_frame_detected": {
                    "detection_interval_seconds": self._config.render.freeze_threshold_seconds,
                    "blocking_interval_seconds": (
                        self._config.render.freeze_block_threshold_seconds
                    ),
                },
                "av_sync_near_limit": {
                    "warning_above_seconds": 0.03,
                    "maximum_seconds": 0.04,
                },
            }
            for code in (
                "pts_discontinuity_detected", "dts_discontinuity_detected",
                "av_sync_out_of_tolerance", "audio_channels_invalid",
                "audio_clipping_detected", "audio_waveform_jump_detected",
                "audio_channels_inverted",
            ):
                check_thresholds[code] = dict(technical.thresholds)
            if draft:
                check_evidence["draft_render"] = {"purpose": purpose}
            checks = _publication_checks(
                issues=issues,
                warnings=warnings,
                evidence=check_evidence,
                thresholds=check_thresholds,
            )
            quality = RenderQualityReport(
                passed=not issues, expected_duration_seconds=plan.timeline_duration_seconds,
                actual_duration_seconds=round(actual_duration, 4),
                duration_delta_seconds=round(delta, 4), has_video="video" in types,
                has_audio="audio" in types, issues=issues, warnings=warnings,
                loudness_target_lufs=self._config.render.loudness_target_lufs,
                integrated_loudness_lufs=integrated_loudness,
                loudness_delta_lu=(round(loudness_delta, 4) if loudness_delta is not None else None),
                true_peak_dbfs=true_peak,
                true_peak_limit_dbfs=self._config.render.true_peak_limit_dbfs,
                caption_cue_count=len(cues),
                subtitles_present=bool(cues and effective_settings.subtitles.sidecar_srt),
                headline_present=bool(
                    overlay_summary is not None
                    and effective_settings.headline.enabled
                    and headline_text
                ),
                visual_analysis_performed=visual_analysis,
                black_threshold_seconds=self._config.render.black_threshold_seconds,
                freeze_threshold_seconds=self._config.render.freeze_threshold_seconds,
                technical=technical,
                **visual_metrics,
            )
            render_sha256 = _sha256(temporary_path)
            if draft:
                # Previews are never publishable: say so explicitly.
                warnings.append("draft_render")
            publication = RenderQualityPublicationReport(
                publish_ready=not issues and not draft,
                reasons=issues,
                warnings=warnings,
                checks=checks,
                loudness={
                    "target_lufs": self._config.render.loudness_target_lufs,
                    "integrated_lufs": integrated_loudness,
                    "true_peak_dbfs": true_peak,
                },
                visual={
                    "blackdetect": visual_metrics["black_interval_count"],
                    "freezedetect": visual_metrics["freeze_interval_count"],
                    "visual_analysis_performed": visual_analysis,
                    "black_threshold_seconds": self._config.render.black_threshold_seconds,
                    "freeze_threshold_seconds": self._config.render.freeze_threshold_seconds,
                },
                captions={
                    "cue_count": len(cues),
                    "subtitles_present": bool(cues and effective_settings.subtitles.sidecar_srt),
                    "headline_present": bool(
                        overlay_summary is not None
                        and effective_settings.headline.enabled
                        and headline_text
                    ),
                },
                safe_zones={
                    "version": SAFE_ZONES_VERSION,
                    "canvas": canvas_key,
                    "margins": safe_zone.model_dump() if safe_zone else None,
                },
                encoder={
                    "requested": requested_encoder,
                    "effective": requested_encoder,
                },
                dimensions={
                    "width": effective_settings.canvas.width,
                    "height": effective_settings.canvas.height,
                    "fps": effective_settings.canvas.fps,
                    "duration_seconds": plan.timeline_duration_seconds,
                },
                hashes={
                    "input_hash": input_hash,
                    "overlay_hash": overlay_summary.cache_key if overlay_summary else None,
                    "render_sha256": render_sha256,
                },
                provenance={
                    "input_window_version": str(_INPUT_WINDOW_VERSION),
                    "input_seek_seconds": f"{input_start:.6f}",
                    "input_duration_seconds": f"{input_end - input_start:.6f}",
                    "ffmpeg": str(self._config.render.ffmpeg),
                    "ffprobe": str(self._config.render.ffprobe),
                    "overlay_renderer_requested": requested_renderer,
                    "overlay_renderer_effective": overlay_summary.renderer if overlay_summary else "none",
                },
                punch_in={
                    "piece_junction_zoom": bool(junction_orders),
                    "enabled": punch_in_settings.enabled,
                    "requested_scale": punch_in_settings.scale,
                    "anchor": punch_in_settings.anchor,
                    "alternate_on_jump_cuts": punch_in_settings.alternate_on_jump_cuts,
                    "segments": [entry.model_dump() for entry in punch_ins_manifest],
                    "scope": "edit_segment",
                    "source_size": list(source_size) if source_size else None,
                    "zoom": effective_settings.framing.zoom,
                    "focus_x": effective_settings.framing.focus_x,
                },
                technical=technical,
            )
            timings["loudness_check"] = round(loudness_check_seconds, 4)
            timings["loudness_correction"] = round(loudness_correction_seconds, 4)
            timings["qa"] = round(max(0.0, (
                time.monotonic() - quality_started
                - loudness_check_seconds - loudness_correction_seconds
            )), 4)
            timings["total"] = round(time.monotonic() - total_started, 4)
            temporary_path.replace(output_path)
            if cues and effective_settings.subtitles.sidecar_srt:
                temporary_subtitles_path.replace(subtitles_path)
            document = RenderDocument(
                project_id=plan.project_id, source_asset_id=source.id,
                edit_plan_artifact_id=edit_plan_artifact.id, input_hash=input_hash,
                face_focus=RenderFaceFocusInfo(
                    requested_mode=requested_focus_mode, effective_mode=effective_focus_mode,
                    algorithm_version=FACE_FOCUS_ALGORITHM_VERSION if face_spans else None,
                    scene_threshold=(
                        self._config.analysis.scene_threshold if face_spans else None
                    ),
                    crop_width_fraction=(
                        round(face_crop_fraction, 6) if face_crop_fraction is not None else None
                    ),
                    spans=face_spans,
                ),
                sources=[RenderSourceInfo(
                    source_asset_id=source.id,
                    sha256=source.sha256,
                    video_used=True,
                    audio_used=True,
                )],
                output_path=str(output_path), output_sha256=_sha256(output_path),
                output_size_bytes=output_path.stat().st_size,
                subtitles_path=(
                    str(subtitles_path) if cues and effective_settings.subtitles.sidecar_srt else None
                ),
                subtitles_sha256=(
                    _sha256(subtitles_path)
                    if cues and effective_settings.subtitles.sidecar_srt else None
                ),
                purpose=purpose,
                timeline_duration_seconds=plan.timeline_duration_seconds,
                segment_count=len(plan.segments),
                transitions=_render_transitions(plan),
                punch_ins=punch_ins_manifest,
                teaser=(
                    RenderTeaserInfo(
                        applied=True,
                        source_start=min(s.video_start for s in plan.segments if s.teaser),
                        source_end=max(s.video_end for s in plan.segments if s.teaser),
                        segment_count=sum(1 for s in plan.segments if s.teaser),
                        input_seek_seconds=teaser_window[0],
                        input_duration_seconds=teaser_window[1] - teaser_window[0],
                    ) if teaser_window else None
                ),
                engine=RenderEngineInfo(
                    ffmpeg=str(self._config.render.ffmpeg), ffprobe=str(self._config.render.ffprobe),
                    requested_encoder=requested_encoder, effective_encoder=requested_encoder,
                    width=effective_settings.canvas.width,
                    height=effective_settings.canvas.height,
                    fps=effective_settings.canvas.fps,
                ),
                requested_settings=requested_settings,
                effective_settings=effective_settings,
                overlays=RenderOverlayInfo(
                    renderer=overlay_summary.renderer if overlay_summary is not None else "none",
                    requested_renderer=requested_renderer if overlay_enabled else "none",
                    ass_generator_version=(
                        overlay_summary.generator_version if overlay_summary is not None else None
                    ),
                    font_files=(overlay_summary.font_files if overlay_summary is not None else None),
                    captions_enabled=bool(cues and effective_settings.captions.enabled),
                    caption_font=caption_font if cues else None,
                    caption_text_color=effective_settings.captions.text_color if cues else None,
                    caption_karaoke_color=effective_settings.captions.karaoke_color if cues else None,
                    caption_outline_color=effective_settings.captions.outline_color if cues else None,
                    caption_shadow_color=effective_settings.captions.shadow_color if cues else None,
                    caption_font_size=(effective_settings.captions.font_size if cues else None),
                    caption_words_per_cue=(
                        effective_settings.captions.words_per_cue if cues else None
                    ),
                    caption_position_y=(
                        effective_settings.captions.position_y if cues else None
                    ),
                    caption_outline=(effective_settings.captions.outline if cues else None),
                    caption_shadow=(effective_settings.captions.shadow if cues else None),
                    karaoke_enabled=bool(
                        cues
                        and effective_settings.captions.enabled
                        and effective_settings.captions.karaoke
                    ),
                    headline_enabled=bool(overlay_summary is not None and headline_text),
                    headline_text=headline_text if overlay_summary is not None else None,
                    headline_font=headline_font if overlay_summary is not None else None,
                    headline_font_size=(
                        effective_settings.headline.font_size
                        if overlay_summary is not None and headline_text else None
                    ),
                    headline_duration_seconds=(
                        effective_settings.headline.duration_seconds
                        if overlay_summary is not None and headline_text else None
                    ),
                    caption_style=(effective_settings.captions.style if cues else None),
                    caption_emphasis_count=(
                        len(effective_settings.captions.emphasis_word_indices) if cues else 0
                    ),
                    headline_persistent=bool(
                        overlay_summary is not None and headline_text
                        and effective_settings.headline.persistent
                    ),
                    requested_piece_junction=effective_settings.transitions.piece_junction,
                    piece_junction=(
                        "flash" if ass_result is not None and ass_result.flash_count
                        else "zoom" if any(
                            entry.junction and entry.applied for entry in punch_ins_manifest
                        )
                        else "cut"
                    ),
                    junction_times=list(plan_overlay.junctions),
                    sidecar_srt=bool(cues and effective_settings.subtitles.sidecar_srt),
                    artifact_path=(overlay_summary.output_path if overlay_summary else None),
                    artifact_sha256=(overlay_summary.output_sha256 if overlay_summary else None),
                    artifact_size_bytes=(
                        overlay_summary.output_size_bytes if overlay_summary else None
                    ),
                    input_hash=(overlay_summary.cache_key if overlay_summary else None),
                    codec=(overlay_summary.codec if overlay_summary else None),
                    pixel_format=(overlay_summary.pixel_format if overlay_summary else None),
                ),
                quality=quality,
                publication=publication,
                timings=timings,
            )
            logger.info(
                "render %s timings: %s", purpose,
                ", ".join(f"{name}={value:.2f}s" for name, value in timings.items()),
            )
            manifest_path = output_dir / f"{stem}.json"
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_dir,
                                             prefix=".render-", suffix=".tmp", delete=False) as handle:
                json.dump(document.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
                manifest_tmp = Path(handle.name)
            manifest_tmp.replace(manifest_path)
            artifact = self._domain.create_stage_artifact(StageArtifact(
                project_id=plan.project_id, stage="render", schema_version=RENDER_SCHEMA_VERSION,
                path=str(manifest_path), input_hash=input_hash,
                metadata={"edit_plan_artifact_id": edit_plan_artifact.id,
                          "source_asset_ids": ordered_source_ids,
                          "requested_encoder": requested_encoder,
                          "effective_encoder": requested_encoder,
                          "quality_passed": quality.passed,
                          "publish_ready": publication.publish_ready,
                          "quality_reasons": publication.reasons,
                          "quality_warnings": publication.warnings,
                          "quality_check_count": len(publication.checks),
                          "output_path": str(output_path),
                          "timings": timings},
            ))
        finally:
            temporary_path.unlink(missing_ok=True)
            temporary_subtitles_path.unlink(missing_ok=True)
        progress_cb(100.0, "Render concluído" if quality.passed else "Render bloqueado pelo quality gate")
        exports = {}
        if quality.passed and not draft:
            exports = _export_render_files(
                self._config,
                output_path=output_path,
                subtitles_path=(
                    subtitles_path if cues and effective_settings.subtitles.sidecar_srt else None
                ),
                export_directory=export_directory,
                input_hash=input_hash,
            )
        return {"cached": False, "purpose": purpose, "render_artifact_id": artifact.id,
                "render_path": str(output_path), "schema_version": artifact.schema_version,
                "publish_ready": publication.publish_ready,
                "timings": artifact.metadata.get("timings", {}), **exports}
