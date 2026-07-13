from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from cortex.config import CortexConfig
from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument
from cortex.domain.models import StageArtifact
from cortex.domain.store import (
    DomainStore,
    SourceAssetNotFoundError,
    TranscriptArtifactNotFoundError,
)
from cortex.edit.camera_plan_schemas import CameraEditPlanDocument, CameraEditShot
from cortex.edit.schemas import EditPlanDocument
from cortex.ingest.ffprobe import duration_seconds, probe_media
from cortex.paths import renders_dir
from cortex.render.schemas import (
    RENDER_SCHEMA_VERSION,
    RenderDocument,
    RenderEngineInfo,
    RenderOverlayInfo,
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
)
from cortex.render.safe_zones import SAFE_ZONES, SAFE_ZONES_VERSION
from cortex.render.face_crop import resolve_static_face_crop
from cortex.render.captions import build_caption_cues, build_timeline_words, render_srt
from cortex.render.remotion import (
    RemotionOverlayCancelled,
    RemotionOverlayError,
    RemotionOverlayManifest,
    RemotionOverlayService,
)
from cortex.transcribe.schemas import TranscriptDocument


class RenderJobCancelled(RuntimeError):
    pass


class RenderPreconditionError(ValueError):
    pass


class RenderExecutionError(RuntimeError):
    pass


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


def _encoder_args(encoder: str) -> list[str]:
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p4", "-cq", "18"]
    if encoder == "libx264":
        return ["-c:v", encoder, "-preset", "veryfast", "-crf", "18"]
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
    ratio = width / max(1, height)
    if ratio > 1.35:
        return "16:9"
    if ratio < 0.8:
        return "9:16"
    return "1:1"


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
    return RenderSettings.model_validate(data)


def _filtergraph(
    plan: EditPlanDocument,
    width: int,
    height: int,
    fps: int,
    loudness_target_lufs: float,
    true_peak_limit_dbfs: float,
    subtitles_path: Path | None = None,
    caption_font: str = "Montserrat",
    caption_font_size: int = 32,
    caption_outline: bool = True,
    headline_path: Path | None = None,
    headline_font: str = "Montserrat",
    headline_font_size: int = 42,
    headline_duration_seconds: float = 3.4,
    overlay_input_index: int | None = None,
    camera_plan: CameraEditPlanDocument | None = None,
    source_input_indices: dict[str, int] | None = None,
    framing_mode: str = "vertical_crop",
    static_face_crops: dict[int, dict[str, int]] | None = None,
) -> tuple[str, str, str]:
    video_parts: list[str] = []
    audio_parts: list[str] = []
    durations: list[float] = []
    shots_by_segment: dict[int, list[CameraEditShot]] = {}
    if camera_plan is not None:
        for shot in camera_plan.shots:
            shots_by_segment.setdefault(shot.edit_segment_order, []).append(shot)
    source_input_indices = source_input_indices or {plan.source_asset_id: 0}

    def compose_video(input_filter: str, output_label: str, segment_order: int) -> str:
        if framing_mode == "vertical_crop":
            return (
                f"{input_filter},fps={fps},scale={width}:{height}:"
                f"force_original_aspect_ratio=increase,crop={width}:{height},"
                f"setsar=1,format=yuv420p{output_label}"
            )
        if framing_mode == "blurred_background":
            label_name = output_label[1:-1]
            blur_width = max(160, (width // 4) // 2 * 2)
            blur_height = max(160, (height // 4) // 2 * 2)
            background_raw = f"[{label_name}_bgraw]"
            foreground_raw = f"[{label_name}_fgraw]"
            background = f"[{label_name}_bg]"
            foreground = f"[{label_name}_fg]"
            return (
                f"{input_filter},fps={fps},split=2{background_raw}{foreground_raw};"
                f"{background_raw}scale={blur_width}:{blur_height}:force_original_aspect_ratio=increase,"
                f"crop={blur_width}:{blur_height},gblur=sigma=12:steps=2,"
                f"scale={width}:{height},setsar=1{background};"
                f"{foreground_raw}scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"setsar=1{foreground};{background}{foreground}"
                f"overlay=(W-w)/2:(H-h)/2:format=yuv420,format=yuv420p{output_label}"
            )
        if framing_mode == "face_static_crop":
            crop = (static_face_crops or {}).get(segment_order)
            if crop is None:
                raise RenderPreconditionError("crop facial estático ausente para segmento")
            return (
                f"{input_filter},fps={fps},crop={crop['crop_width']}:{crop['crop_height']}:"
                f"{crop['crop_x']}:{crop['crop_y']},scale={width}:{height},"
                f"setsar=1,format=yuv420p{output_label}"
            )
        raise RenderPreconditionError(f"modo de enquadramento não suportado: {framing_mode}")
    for index, segment in enumerate(plan.segments):
        duration = segment.end - segment.start
        if duration <= 0:
            raise RenderPreconditionError(f"segmento {index} possui duração inválida")
        durations.append(duration)
        segment_shots = shots_by_segment.get(segment.timeline_order)
        if segment_shots:
            shot_labels: list[str] = []
            for shot_index, shot in enumerate(segment_shots):
                input_index = source_input_indices[shot.video_source_asset_id]
                label = f"[vs{index}_{shot_index}]"
                shot_labels.append(label)
                video_parts.append(compose_video(
                    f"[{input_index}:v]trim=start={shot.source_start_us / 1_000_000:.6f}:"
                    f"end={shot.source_end_us / 1_000_000:.6f},setpts=PTS-STARTPTS",
                    label, segment.timeline_order,
                ))
            if len(shot_labels) == 1:
                video_parts.append(f"{shot_labels[0]}null[v{index}]")
            else:
                video_parts.append(
                    f"{''.join(shot_labels)}concat=n={len(shot_labels)}:v=1:a=0[v{index}]"
                )
        else:
            video_parts.append(compose_video(
                f"[0:v]trim=start={segment.start:.6f}:end={segment.end:.6f},setpts=PTS-STARTPTS",
                f"[v{index}]", segment.timeline_order,
            ))
        audio_parts.append(
            f"[0:a]atrim=start={segment.start:.6f}:end={segment.end:.6f},"
            f"asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:"
            f"channel_layouts=stereo[a{index}]"
        )

    if len(plan.segments) == 1:
        audio_parts.append(
            f"[a0]loudnorm=I={loudness_target_lufs:.2f}:TP={true_peak_limit_dbfs:.2f}:"
            "LRA=11:print_format=none[anorm]"
        )
        filters = video_parts + audio_parts
        video_label = "[v0]"
    else:
        previous_video = "[v0]"
        previous_audio = "[a0]"
        output_duration = durations[0]
        transitions: list[str] = []
        for index in range(1, len(plan.segments)):
            configured = plan.segments[index - 1].transition
            fade = configured.duration if configured is not None else 0.0
            fade = max(0.0, min(fade, durations[index - 1] / 2, durations[index] / 2))
            video_out = "[vout]" if index == len(plan.segments) - 1 else f"[vx{index}]"
            audio_out = "[aout]" if index == len(plan.segments) - 1 else f"[ax{index}]"
            if fade > 0:
                offset = max(0.001, output_duration - fade)
                transitions.append(
                    f"{previous_video}[v{index}]xfade=transition=fade:duration={fade:.6f}:"
                    f"offset={offset:.6f}{video_out}"
                )
                transitions.append(
                    f"{previous_audio}[a{index}]acrossfade=d={fade:.6f}:c1=tri:c2=tri{audio_out}"
                )
                output_duration += durations[index] - fade
            else:
                transitions.append(f"{previous_video}[v{index}]concat=n=2:v=1:a=0{video_out}")
                transitions.append(f"{previous_audio}[a{index}]concat=n=2:v=0:a=1{audio_out}")
                output_duration += durations[index]
            previous_video, previous_audio = video_out, audio_out
        transitions.append(
            f"{previous_audio}loudnorm=I={loudness_target_lufs:.2f}:"
            f"TP={true_peak_limit_dbfs:.2f}:LRA=11:print_format=none[anorm]"
        )
        filters = video_parts + audio_parts + transitions
        video_label = "[vout]"

    def escaped(path: Path) -> str:
        value = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        for character in ",;[]":
            value = value.replace(character, f"\\{character}")
        return value

    if subtitles_path is not None:
        filters.append(
            f"{video_label}subtitles=filename='{escaped(subtitles_path)}':"
            f"force_style='FontName={caption_font},FontSize={caption_font_size},"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00101719,BorderStyle=1,"
            f"Outline={3 if caption_outline else 0},Shadow=1,Alignment=2,MarginV=70'[vcaption]"
        )
        video_label = "[vcaption]"
    if headline_path is not None:
        filters.append(
            f"{video_label}drawtext=textfile='{escaped(headline_path)}':font='{headline_font}':"
            f"fontsize={headline_font_size}:fontcolor=0x101719:box=1:boxcolor=white@0.92:"
            "boxborderw=14:x=(w-text_w)/2:y=h*0.14:"
            f"enable='between(t,0,{headline_duration_seconds:.3f})'[voverlay]"
        )
        video_label = "[voverlay]"
    if overlay_input_index is not None:
        filters.append(
            f"[{overlay_input_index}:v]setpts=PTS-STARTPTS,format=rgba[alphaoverlay]"
        )
        filters.append(
            f"{video_label}[alphaoverlay]overlay=eof_action=pass:shortest=0:format=auto"
            "[vremotion]"
        )
        video_label = "[vremotion]"
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


def _measure_alpha_bbox(
    ffmpeg: Path,
    overlay_path: Path,
    *,
    crop: str,
    sample_fps: float,
) -> tuple[int, int, int, int] | None:
    """Retorna a bounding box (x1, y1, x2, y2) dos pixels não-transparentes do
    overlay dentro da região recortada, em coordenadas do recorte, amostrando o
    canal alpha por FFmpeg. Retorna None quando a região é totalmente
    transparente em todos os frames amostrados."""
    with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as handle:
        meta_path = Path(handle.name)
    try:
        vf = (
            f"fps={sample_fps:.4f},crop={crop},alphaextract,"
            f"bbox=min_val={_BBOX_ALPHA_MIN_VAL},"
            f"metadata=print:file={meta_path.as_posix()}"
        )
        command = [
            str(ffmpeg), "-hide_banner", "-nostats", "-v", "error",
            # o decoder nativo vp9 descarta o plano alpha; libvpx-vp9 o preserva.
            "-c:v", "libvpx-vp9", "-i", str(overlay_path),
            "-map", "0:v:0", "-vf", vf, "-an", "-f", "null", "-",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RenderExecutionError(f"análise de safe zone falhou: {exc}") from exc
        if result.returncode:
            raise RenderExecutionError(
                f"análise de safe zone falhou ({result.returncode}): {result.stderr[-4000:]}"
            )
        return _parse_bbox_metadata(meta_path.read_text(encoding="utf-8", errors="replace"))
    finally:
        meta_path.unlink(missing_ok=True)


def _parse_bbox_metadata(text: str) -> tuple[int, int, int, int] | None:
    x1 = y1 = None
    x2 = y2 = None
    current: dict[str, int] = {}

    def commit(block: dict[str, int]) -> None:
        nonlocal x1, y1, x2, y2
        if block.get("w", 0) <= 0 or block.get("h", 0) <= 0:
            return
        if {"x1", "y1", "x2", "y2"} - block.keys():
            return
        x1 = block["x1"] if x1 is None else min(x1, block["x1"])
        y1 = block["y1"] if y1 is None else min(y1, block["y1"])
        x2 = block["x2"] if x2 is None else max(x2, block["x2"])
        y2 = block["y2"] if y2 is None else max(y2, block["y2"])

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("frame:"):
            commit(current)
            current = {}
            continue
        match = re.match(r"lavfi\.bbox\.(x1|x2|y1|y2|w|h)=(-?\d+)", stripped)
        if match:
            current[match.group(1)] = int(match.group(2))
    commit(current)
    if x1 is None:
        return None
    return (x1, y1, x2, y2)


def _run_ffmpeg(command: list[str], log_path: Path, should_cancel: Callable[[], bool]) -> None:
    with log_path.open("w+b") as log:
        try:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=log)
        except OSError as exc:
            raise RenderExecutionError(f"FFmpeg não pôde ser executado: {exc}") from exc
        started = time.monotonic()
        while process.poll() is None:
            if should_cancel():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise RenderJobCancelled()
            if time.monotonic() - started > 3600:
                process.kill()
                raise RenderExecutionError("FFmpeg excedeu o timeout de 3600s")
            time.sleep(0.1)
        if process.returncode:
            log.seek(0)
            detail = log.read().decode(errors="replace")[-4000:]
            raise RenderExecutionError(f"FFmpeg falhou ({process.returncode}): {detail}")


class RenderService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def _safe_zone_issues(
        self,
        *,
        overlay_path,
        width: int,
        height: int,
        safe_zone,
    ) -> list[str]:
        """Valida geometricamente que o conteúdo desenhado no overlay alpha fica
        dentro da safe zone, amostrando os pixels não-transparentes do WebM. A
        headline é ancorada no topo e a legenda na base, então cada uma é medida
        na sua metade do canvas e comparada às margens em pixels."""
        if safe_zone is None:
            return ["safe_zone_missing"]
        if overlay_path is None:
            return []
        overlay = Path(overlay_path)
        if not overlay.is_file():
            raise RenderExecutionError("overlay para análise de safe zone não encontrado")

        left_px = safe_zone.left * width
        right_limit = (1.0 - safe_zone.right) * width
        top_px = safe_zone.top * height
        bottom_limit = (1.0 - safe_zone.bottom) * height
        half = height // 2
        tol = _SAFE_ZONE_TOLERANCE_PX
        issues: list[str] = []

        headline_bbox = _measure_alpha_bbox(
            self._config.render.ffmpeg, overlay,
            crop=f"{width}:{half}:0:0",
            sample_fps=self._config.render.safe_zone_sample_fps,
        )
        if headline_bbox is not None:
            hx1, hy1, hx2, hy2 = headline_bbox
            if (hx1 < left_px - tol or hx2 > right_limit + tol
                    or hy1 < top_px - tol or hy2 > bottom_limit + tol):
                issues.append("headline_outside_safe_zone")

        caption_bbox = _measure_alpha_bbox(
            self._config.render.ffmpeg, overlay,
            crop=f"{width}:{height - half}:0:{half}",
            sample_fps=self._config.render.safe_zone_sample_fps,
        )
        if caption_bbox is not None:
            cx1, cy1, cx2, cy2 = caption_bbox
            cy1_abs = cy1 + half
            cy2_abs = cy2 + half
            if (cx1 < left_px - tol or cx2 > right_limit + tol
                    or cy1_abs < top_px - tol or cy2_abs > bottom_limit + tol):
                issues.append("caption_outside_safe_zone")

        return issues

    def run(
        self,
        *,
        edit_plan_artifact: StageArtifact,
        camera_edit_plan_artifact: StageArtifact | None = None,
        face_index_artifact: StageArtifact | None = None,
        identity_index_artifact: StageArtifact | None = None,
        target_identity_id: str | None = None,
        encoder: str | None,
        headline: str | None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
        render_settings: RenderSettings | None = None,
        render_settings_override: RenderSettingsPatch | None = None,
        export_directory: str | None = None,
    ) -> dict:
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

        camera_plan: CameraEditPlanDocument | None = None
        render_sources = {source.id: source}
        source_paths = {source.id: source_path}
        if camera_edit_plan_artifact is not None:
            if camera_edit_plan_artifact.stage != "camera_edit_plan":
                raise RenderPreconditionError(
                    "artifact informado não é um CameraEditPlanArtifact"
                )
            camera_plan_path = Path(camera_edit_plan_artifact.path)
            if not camera_plan_path.is_file():
                raise RenderPreconditionError(
                    "arquivo do CameraEditPlanArtifact não está disponível"
                )
            camera_plan = CameraEditPlanDocument.model_validate_json(
                camera_plan_path.read_text(encoding="utf-8")
            )
            if (
                camera_plan.schema_version not in {2, 3}
                or camera_edit_plan_artifact.schema_version not in {2, 3}
                or camera_plan.project_id != plan.project_id
                or camera_edit_plan_artifact.project_id != plan.project_id
                or camera_plan.source_asset_id != source.id
                or camera_plan.edit_plan_artifact_id != edit_plan_artifact.id
                or camera_plan.edit_plan_input_hash != edit_plan_artifact.input_hash
                or camera_plan.engine.audio_continuity_mode != "primary_source_continuous"
            ):
                raise RenderPreconditionError(
                    "CameraEditPlanArtifact não corresponde ao plano de edição primário"
                )

            segment_orders = {segment.timeline_order for segment in plan.segments}
            if any(shot.edit_segment_order not in segment_orders for shot in camera_plan.shots):
                raise RenderPreconditionError("camera plan referencia segmento inexistente")
            if camera_plan.schema_version == 3:
                for shot in camera_plan.shots:
                    if shot.visual_origin == "reaction_reuse" and (
                        shot.video_source_asset_id != source.id
                        or shot.audio_source_asset_id != source.id
                        or shot.reaction_candidate_artifact_id != camera_plan.reaction_candidate_artifact_id
                    ):
                        raise RenderPreconditionError(
                            "reaction do camera plan não preserva o master e áudio primário"
                        )
            normalized_shots: list[CameraEditShot] = []
            for segment in plan.segments:
                shots = sorted(
                    (
                        shot for shot in camera_plan.shots
                        if shot.edit_segment_order == segment.timeline_order
                    ),
                    key=lambda shot: (shot.audio_source_start_us, shot.audio_source_end_us),
                )
                expected_start = round(segment.start * 1_000_000)
                expected_end = round(segment.end * 1_000_000)
                cursor = expected_start
                for shot in shots:
                    if shot.audio_source_asset_id != source.id:
                        raise RenderPreconditionError(
                            "camera plan não mantém áudio contínuo da fonte primária"
                        )
                    if shot.audio_source_start_us != cursor:
                        raise RenderPreconditionError(
                            "camera plan possui gap ou sobreposição na cobertura do segmento"
                        )
                    cursor = shot.audio_source_end_us
                if not shots or cursor != expected_end:
                    raise RenderPreconditionError(
                        "camera plan não cobre integralmente o segmento da EDL"
                    )
                normalized_shots.extend(shots)
            camera_plan = camera_plan.model_copy(update={"shots": normalized_shots})
            for source_id in sorted({shot.video_source_asset_id for shot in camera_plan.shots}):
                if source_id in render_sources:
                    continue
                try:
                    video_source = self._domain.get_source_asset(source_id)
                except SourceAssetNotFoundError as exc:
                    raise RenderPreconditionError(
                        f"fonte de vídeo do camera plan não encontrada: {source_id}"
                    ) from exc
                if video_source.project_id != plan.project_id:
                    raise RenderPreconditionError(
                        "fonte de vídeo do camera plan não pertence ao projeto"
                    )
                video_path = self._config.paths.data_dir / video_source.stored_path
                if not video_path.is_file():
                    raise RenderPreconditionError(
                        f"arquivo da fonte de vídeo não está disponível: {source_id}"
                    )
                render_sources[source_id] = video_source
                source_paths[source_id] = video_path

        requested_settings = render_settings or _default_render_settings(
            self._config, encoder=encoder, headline=headline
        )
        requested_settings = _merge_render_settings(requested_settings, render_settings_override)
        requested_encoder = requested_settings.encoder.strip()
        _encoder_args(requested_encoder)
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
        caption_font = _resolve_font_family(requested_settings.captions.font_family)
        headline_font = _resolve_font_family(requested_settings.headline.font_family)
        headline_text = "\n".join(
            re.sub(r"\s+", " ", line).strip()
            for line in requested_settings.headline.text.splitlines()
            if line.strip()
        )[:120]
        headline_text = headline_text or None
        effective_settings = requested_settings.model_copy(update={
            "encoder": requested_encoder,
            "captions": requested_settings.captions.model_copy(update={
                "font_family": caption_font,
            }),
            "headline": requested_settings.headline.model_copy(update={
                "text": headline_text or "", "font_family": headline_font,
                "duration_seconds": min(
                    requested_settings.headline.duration_seconds,
                    plan.timeline_duration_seconds,
                ),
            }),
        })
        static_face_crops: list[dict[str, object]] = []
        static_face_crop_map: dict[int, dict[str, int]] = {}
        if effective_settings.framing.mode == "face_static_crop":
            if camera_edit_plan_artifact is not None:
                raise RenderPreconditionError("face_static_crop ainda não aceita camera_edit_plan")
            if not (face_index_artifact and identity_index_artifact and target_identity_id):
                raise RenderPreconditionError(
                    "face_static_crop exige face_index, identity_index e identidade alvo explícita"
                )
            if face_index_artifact.stage != "face_index" or identity_index_artifact.stage != "identity_index":
                raise RenderPreconditionError("artifacts inválidos para face_static_crop")
            face_doc = FaceIndexDocument.model_validate_json(Path(face_index_artifact.path).read_text())
            identity_doc = IdentityIndexDocument.model_validate_json(Path(identity_index_artifact.path).read_text())
            if (
                face_index_artifact.project_id != plan.project_id
                or identity_index_artifact.project_id != plan.project_id
                or face_doc.project_id != plan.project_id
                or identity_doc.project_id != plan.project_id
                or face_doc.source_asset_id != source.id
                or identity_doc.source_asset_id != source.id
                or identity_doc.face_index_artifact_id != face_index_artifact.id
                or identity_doc.face_index_input_hash != face_index_artifact.input_hash
                or face_doc.source_sha256 != source.sha256
                or identity_doc.source_sha256 != source.sha256
            ):
                raise RenderPreconditionError("cadeia de face/identidade não corresponde à fonte")
            if not any(item.identity_id == target_identity_id and item.status == "confirmed" for item in identity_doc.identities):
                raise RenderPreconditionError("identidade alvo não está confirmada")
            probe = source.probe or probe_media(self._config.render.ffprobe, source_path)
            video_stream = next((item for item in probe.get("streams", []) if item.get("codec_type") == "video"), None)
            if not video_stream:
                raise RenderPreconditionError("fonte sem dimensões de vídeo para crop facial")
            for segment in plan.segments:
                resolved = resolve_static_face_crop(
                    face_doc, start_seconds=segment.start, end_seconds=segment.end,
                    source_width=int(video_stream["width"]), source_height=int(video_stream["height"]),
                    canvas_width=effective_settings.canvas.width, canvas_height=effective_settings.canvas.height,
                    identity_index=identity_doc, target_identity_id=target_identity_id,
                )
                crop = {"crop_x": resolved.crop_x, "crop_y": resolved.crop_y,
                        "crop_width": resolved.crop_width, "crop_height": resolved.crop_height}
                static_face_crop_map[segment.timeline_order] = crop
                static_face_crops.append({"segment_order": segment.timeline_order, **crop,
                                          "provenance": resolved.provenance})
        canvas_key = _safe_zone_key(effective_settings.canvas.width, effective_settings.canvas.height)
        safe_zone = SAFE_ZONES.get(canvas_key)
        if safe_zone is None:
            raise RenderPreconditionError(f"safe zone não definido para canvas {canvas_key}")
        output_dir = renders_dir(self._config, plan.project_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        needs_cues = (
            effective_settings.captions.enabled or effective_settings.subtitles.sidecar_srt
        )
        words = build_timeline_words(plan, transcript) if needs_cues else []
        cues = build_caption_cues(
            plan, transcript, max_words=effective_settings.captions.words_per_cue
        ) if needs_cues else []
        if needs_cues and not cues:
            raise RenderPreconditionError("nenhuma palavra do transcript coincide com a EDL")
        overlay_enabled = bool(
            effective_settings.captions.enabled
            or (effective_settings.headline.enabled and headline_text)
        )
        overlay_artifact: StageArtifact | None = None
        overlay_manifest: RemotionOverlayManifest | None = None
        if overlay_enabled:
            progress_cb(10.0, "Gerando overlay tipográfico com Remotion")
            try:
                overlay_artifact, overlay_manifest, _overlay_cached = RemotionOverlayService(
                    self._config, self._domain
                ).run(
                    project_id=plan.project_id,
                    output_dir=output_dir,
            settings=effective_settings,
            duration_seconds_value=plan.timeline_duration_seconds,
            words=words,
            should_cancel=should_cancel,
        )
            except RemotionOverlayCancelled as exc:
                raise RenderJobCancelled() from exc
            except RemotionOverlayError as exc:
                raise RenderExecutionError(str(exc)) from exc
        input_payload = {
            "schema_version": RENDER_SCHEMA_VERSION,
            "edit_plan_sha256": _sha256(plan_path),
            "source_sha256": source.sha256,
            "camera_edit_plan_sha256": (
                _sha256(Path(camera_edit_plan_artifact.path))
                if camera_edit_plan_artifact is not None else None
            ),
            "face_index_sha256": _sha256(Path(face_index_artifact.path)) if face_index_artifact else None,
            "identity_index_sha256": _sha256(Path(identity_index_artifact.path)) if identity_index_artifact else None,
            "target_identity_id": target_identity_id,
            "static_face_crops": static_face_crops,
            "render_sources": {
                source_id: asset.sha256 for source_id, asset in sorted(render_sources.items())
            },
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
            "overlay_input_hash": overlay_artifact.input_hash if overlay_artifact else None,
            "overlay_sha256": overlay_manifest.output_sha256 if overlay_manifest else None,
            "safe_zone_version": SAFE_ZONES_VERSION,
            "safe_zone_canvas": canvas_key,
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
                overlay_manifest is None
                or (
                    cached_doc.overlays is not None
                    and cached_doc.overlays.artifact_sha256 == overlay_manifest.output_sha256
                    and Path(overlay_manifest.output_path).is_file()
                )
            )
            if Path(cached_doc.output_path).exists() and subtitles_available and overlay_available:
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
                return {"cached": True, "render_artifact_id": cached.id,
                        "render_path": cached_doc.output_path,
                        "schema_version": cached.schema_version, **exports}

        if should_cancel():
            raise RenderJobCancelled()
        progress_cb(10.0, "Validando fonte e plano")
        source_probes = {
            source_id: asset.probe or probe_media(self._config.render.ffprobe, source_paths[source_id])
            for source_id, asset in render_sources.items()
        }
        stream_types = {
            stream.get("codec_type") for stream in source_probes[source.id].get("streams", [])
        }
        if not {"video", "audio"}.issubset(stream_types):
            raise RenderPreconditionError("render requer fonte com streams de áudio e vídeo")
        for source_id, source_probe in source_probes.items():
            types = {stream.get("codec_type") for stream in source_probe.get("streams", [])}
            if "video" not in types:
                raise RenderPreconditionError(f"fonte de câmera sem stream de vídeo: {source_id}")
        if camera_plan is not None:
            for shot in camera_plan.shots:
                available_us = round(duration_seconds(source_probes[shot.video_source_asset_id]) * 1_000_000)
                if shot.source_end_us > available_us:
                    raise RenderPreconditionError(
                        f"shot excede duração da fonte de vídeo: {shot.video_source_asset_id}"
                    )

        output_path = output_dir / f"render-{input_hash[:16]}.mp4"
        temporary_path = output_path.with_suffix(".tmp.mp4")
        subtitles_path = output_dir / f"render-{input_hash[:16]}.srt"
        temporary_subtitles_path = output_dir / f".render-{input_hash[:16]}.tmp.srt"
        if cues:
            temporary_subtitles_path.write_text(render_srt(cues), encoding="utf-8")
        ordered_source_ids = [source.id, *sorted(set(render_sources) - {source.id})]
        source_input_indices = {
            source_id: index for index, source_id in enumerate(ordered_source_ids)
        }
        filtergraph, video_label, audio_label = _filtergraph(
            plan,
            effective_settings.canvas.width,
            effective_settings.canvas.height,
            effective_settings.canvas.fps,
            self._config.render.loudness_target_lufs,
            self._config.render.true_peak_limit_dbfs,
            framing_mode=effective_settings.framing.mode,
            static_face_crops=static_face_crop_map,
            overlay_input_index=(len(ordered_source_ids) if overlay_manifest is not None else None),
            camera_plan=camera_plan,
            source_input_indices=source_input_indices,
        )
        command = [str(self._config.render.ffmpeg), "-y", "-v", "error"]
        for source_id in ordered_source_ids:
            command.extend(["-i", str(source_paths[source_id])])
        if overlay_manifest is not None:
            command.extend(["-c:v", "libvpx-vp9", "-i", overlay_manifest.output_path])
        command.extend([
            "-filter_complex", filtergraph, "-map", video_label, "-map", audio_label,
            *_encoder_args(requested_encoder), "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-movflags", "+faststart", str(temporary_path),
        ])
        progress_cb(25.0, f"Renderizando com {requested_encoder}")
        log_path = output_dir / f"render-{input_hash[:16]}.ffmpeg.log"
        try:
            _run_ffmpeg(command, log_path, should_cancel)
            progress_cb(85.0, "Validando streams, duração e loudness")
            rendered_probe = probe_media(self._config.render.ffprobe, temporary_path)
            actual_duration = duration_seconds(rendered_probe)
            types = {stream.get("codec_type") for stream in rendered_probe.get("streams", [])}
            delta = abs(actual_duration - plan.timeline_duration_seconds)
            issues: list[str] = []
            if "video" not in types:
                issues.append("video_stream_missing")
            if "audio" not in types:
                issues.append("audio_stream_missing")
            if delta > 0.15:
                issues.append("duration_mismatch")
            integrated_loudness, true_peak = _measure_loudness(
                self._config.render.ffmpeg, temporary_path
            )
            loudness_delta = abs(
                integrated_loudness - self._config.render.loudness_target_lufs
            )
            if loudness_delta > self._config.render.loudness_tolerance_lu:
                issues.append("loudness_out_of_tolerance")
            if true_peak > self._config.render.true_peak_limit_dbfs:
                issues.append("true_peak_exceeded")
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
            if self._config.render.visual_quality_enabled:
                visual_metrics = _measure_visual_quality(
                    self._config.render.ffmpeg,
                    temporary_path,
                    actual_duration=actual_duration,
                    black_threshold_seconds=self._config.render.black_threshold_seconds,
                    freeze_threshold_seconds=self._config.render.freeze_threshold_seconds,
                )
                if visual_metrics["black_interval_count"]:
                    issues.append("black_frame_detected")
                if visual_metrics["freeze_interval_count"]:
                    issues.append("frozen_frame_detected")
            if overlay_manifest is not None:
                issues.extend(self._safe_zone_issues(
                    overlay_path=overlay_manifest.output_path,
                    width=effective_settings.canvas.width,
                    height=effective_settings.canvas.height,
                    safe_zone=safe_zone,
                ))
            quality = RenderQualityReport(
                passed=not issues, expected_duration_seconds=plan.timeline_duration_seconds,
                actual_duration_seconds=round(actual_duration, 4),
                duration_delta_seconds=round(delta, 4), has_video="video" in types,
                has_audio="audio" in types, issues=issues,
                loudness_target_lufs=self._config.render.loudness_target_lufs,
                integrated_loudness_lufs=integrated_loudness,
                loudness_delta_lu=round(loudness_delta, 4),
                true_peak_dbfs=true_peak,
                true_peak_limit_dbfs=self._config.render.true_peak_limit_dbfs,
                caption_cue_count=len(cues),
                subtitles_present=bool(cues and effective_settings.subtitles.sidecar_srt),
                headline_present=bool(
                    overlay_manifest is not None
                    and effective_settings.headline.enabled
                    and headline_text
                ),
                visual_analysis_performed=self._config.render.visual_quality_enabled,
                black_threshold_seconds=self._config.render.black_threshold_seconds,
                freeze_threshold_seconds=self._config.render.freeze_threshold_seconds,
                **visual_metrics,
            )
            publication = RenderQualityPublicationReport(
                publish_ready=not issues,
                reasons=issues,
                loudness={
                    "target_lufs": self._config.render.loudness_target_lufs,
                    "integrated_lufs": integrated_loudness,
                    "true_peak_dbfs": true_peak,
                },
                visual={
                    "blackdetect": visual_metrics["black_interval_count"],
                    "freezedetect": visual_metrics["freeze_interval_count"],
                    "visual_analysis_performed": self._config.render.visual_quality_enabled,
                    "black_threshold_seconds": self._config.render.black_threshold_seconds,
                    "freeze_threshold_seconds": self._config.render.freeze_threshold_seconds,
                },
                captions={
                    "cue_count": len(cues),
                    "subtitles_present": bool(cues and effective_settings.subtitles.sidecar_srt),
                    "headline_present": bool(
                        overlay_manifest is not None
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
                    "overlay_hash": overlay_artifact.input_hash if overlay_artifact else None,
                    "render_sha256": None,
                },
                provenance={
                    "ffmpeg": str(self._config.render.ffmpeg),
                    "ffprobe": str(self._config.render.ffprobe),
                    "node": overlay_manifest.node_executable if overlay_manifest else None,
                    "node_version": overlay_manifest.node_version if overlay_manifest else None,
                    "remotion_version": overlay_manifest.remotion_version if overlay_manifest else None,
                },
            )
            if not quality.passed:
                raise RenderExecutionError(f"quality gate pós-render falhou: {', '.join(issues)}")
            temporary_path.replace(output_path)
            if cues and effective_settings.subtitles.sidecar_srt:
                temporary_subtitles_path.replace(subtitles_path)
            document = RenderDocument(
                project_id=plan.project_id, source_asset_id=source.id,
                edit_plan_artifact_id=edit_plan_artifact.id, input_hash=input_hash,
                camera_edit_plan_artifact_id=(
                    camera_edit_plan_artifact.id
                    if camera_edit_plan_artifact is not None else None
                ),
                face_index_artifact_id=face_index_artifact.id if face_index_artifact else None,
                identity_index_artifact_id=identity_index_artifact.id if identity_index_artifact else None,
                target_identity_id=target_identity_id,
                static_face_crops=static_face_crops,
                sources=[
                    RenderSourceInfo(
                        source_asset_id=source_id,
                        sha256=render_sources[source_id].sha256,
                        video_used=(
                            camera_plan is None and source_id == source.id
                            or camera_plan is not None and any(
                                shot.video_source_asset_id == source_id
                                for shot in camera_plan.shots
                            )
                        ),
                        audio_used=source_id == source.id,
                    )
                    for source_id in ordered_source_ids
                ],
                output_path=str(output_path), output_sha256=_sha256(output_path),
                output_size_bytes=output_path.stat().st_size,
                subtitles_path=(
                    str(subtitles_path) if cues and effective_settings.subtitles.sidecar_srt else None
                ),
                subtitles_sha256=(
                    _sha256(subtitles_path)
                    if cues and effective_settings.subtitles.sidecar_srt else None
                ),
                timeline_duration_seconds=plan.timeline_duration_seconds,
                segment_count=len(plan.segments),
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
                    renderer="remotion" if overlay_manifest is not None else "none",
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
                    caption_outline=(effective_settings.captions.outline if cues else None),
                    caption_shadow=(effective_settings.captions.shadow if cues else None),
                    karaoke_enabled=bool(
                        cues
                        and effective_settings.captions.enabled
                        and effective_settings.captions.karaoke
                    ),
                    headline_enabled=bool(overlay_manifest is not None and headline_text),
                    headline_text=headline_text if overlay_manifest is not None else None,
                    headline_font=headline_font if overlay_manifest is not None else None,
                    headline_font_size=(
                        effective_settings.headline.font_size
                        if overlay_manifest is not None and headline_text else None
                    ),
                    headline_duration_seconds=(
                        effective_settings.headline.duration_seconds
                        if overlay_manifest is not None and headline_text else None
                    ),
                    sidecar_srt=bool(cues and effective_settings.subtitles.sidecar_srt),
                    artifact_path=(overlay_manifest.output_path if overlay_manifest else None),
                    artifact_sha256=(overlay_manifest.output_sha256 if overlay_manifest else None),
                    artifact_size_bytes=(
                        overlay_manifest.output_size_bytes if overlay_manifest else None
                    ),
                    input_hash=(overlay_artifact.input_hash if overlay_artifact else None),
                    codec=(overlay_manifest.codec if overlay_manifest else None),
                    pixel_format=(overlay_manifest.pixel_format if overlay_manifest else None),
                    node_executable=(
                        overlay_manifest.node_executable if overlay_manifest else None
                    ),
                    node_version=(overlay_manifest.node_version if overlay_manifest else None),
                    remotion_version=(
                        overlay_manifest.remotion_version if overlay_manifest else None
                    ),
                ),
                quality=quality,
                publication=publication,
            )
            manifest_path = output_dir / f"render-{input_hash[:16]}.json"
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_dir,
                                             prefix=".render-", suffix=".tmp", delete=False) as handle:
                json.dump(document.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
                manifest_tmp = Path(handle.name)
            manifest_tmp.replace(manifest_path)
            artifact = self._domain.create_stage_artifact(StageArtifact(
                project_id=plan.project_id, stage="render", schema_version=RENDER_SCHEMA_VERSION,
                path=str(manifest_path), input_hash=input_hash,
                metadata={"edit_plan_artifact_id": edit_plan_artifact.id,
                          "camera_edit_plan_artifact_id": (
                              camera_edit_plan_artifact.id
                              if camera_edit_plan_artifact is not None else None
                          ),
                          "source_asset_ids": ordered_source_ids,
                          "requested_encoder": requested_encoder,
                          "effective_encoder": requested_encoder,
                          "quality_passed": True, "output_path": str(output_path),
                          "overlay_artifact_id": (
                              overlay_artifact.id if overlay_artifact else None
                          )},
            ))
        finally:
            temporary_path.unlink(missing_ok=True)
            temporary_subtitles_path.unlink(missing_ok=True)
        progress_cb(100.0, "Render concluído")
        exports = _export_render_files(
            self._config,
            output_path=output_path,
            subtitles_path=(
                subtitles_path if cues and effective_settings.subtitles.sidecar_srt else None
            ),
            export_directory=export_directory,
            input_hash=input_hash,
        )
        return {"cached": False, "render_artifact_id": artifact.id,
                "render_path": str(output_path), "schema_version": artifact.schema_version,
                **exports}
