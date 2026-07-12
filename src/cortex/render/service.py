from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore
from cortex.edit.schemas import EditPlanDocument
from cortex.ingest.ffprobe import duration_seconds, probe_media
from cortex.paths import renders_dir
from cortex.render.schemas import (
    RENDER_SCHEMA_VERSION,
    RenderDocument,
    RenderEngineInfo,
    RenderQualityReport,
)


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


def _encoder_args(encoder: str) -> list[str]:
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p4", "-cq", "18"]
    if encoder == "libx264":
        return ["-c:v", encoder, "-preset", "veryfast", "-crf", "18"]
    raise RenderPreconditionError(f"encoder não suportado: {encoder}")


def _filtergraph(
    plan: EditPlanDocument,
    width: int,
    height: int,
    fps: int,
    loudness_target_lufs: float,
    true_peak_limit_dbfs: float,
) -> tuple[str, str, str]:
    video_parts: list[str] = []
    audio_parts: list[str] = []
    durations: list[float] = []
    for index, segment in enumerate(plan.segments):
        duration = segment.end - segment.start
        if duration <= 0:
            raise RenderPreconditionError(f"segmento {index} possui duração inválida")
        durations.append(duration)
        video_parts.append(
            f"[0:v]trim=start={segment.start:.6f}:end={segment.end:.6f},"
            f"setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{index}]"
        )
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
        return ";".join(video_parts + audio_parts), "[v0]", "[anorm]"

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
    return ";".join(video_parts + audio_parts + transitions), "[vout]", "[anorm]"


_INTEGRATED_LOUDNESS_RE = re.compile(r"Integrated loudness:.*?I:\s*(-?\d+(?:\.\d+)?) LUFS", re.S)
_TRUE_PEAK_RE = re.compile(r"True peak:.*?Peak:\s*(-?\d+(?:\.\d+)?) dBFS", re.S)


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

    def run(
        self,
        *,
        edit_plan_artifact: StageArtifact,
        encoder: str | None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
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

        requested_encoder = (encoder or self._config.render.encoder).strip()
        _encoder_args(requested_encoder)
        input_payload = {
            "schema_version": RENDER_SCHEMA_VERSION,
            "edit_plan_sha256": _sha256(plan_path),
            "source_sha256": source.sha256,
            "encoder": requested_encoder,
            "width": self._config.render.width,
            "height": self._config.render.height,
            "fps": self._config.render.fps,
            "loudness_target_lufs": self._config.render.loudness_target_lufs,
            "loudness_tolerance_lu": self._config.render.loudness_tolerance_lu,
            "true_peak_limit_dbfs": self._config.render.true_peak_limit_dbfs,
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
            if Path(cached_doc.output_path).exists():
                progress_cb(100.0, "Render em cache reutilizado")
                return {"cached": True, "render_artifact_id": cached.id,
                        "render_path": cached_doc.output_path,
                        "schema_version": cached.schema_version}

        if should_cancel():
            raise RenderJobCancelled()
        progress_cb(10.0, "Validando fonte e plano")
        source_probe = source.probe or probe_media(self._config.render.ffprobe, source_path)
        stream_types = {stream.get("codec_type") for stream in source_probe.get("streams", [])}
        if not {"video", "audio"}.issubset(stream_types):
            raise RenderPreconditionError("render requer fonte com streams de áudio e vídeo")

        output_dir = renders_dir(self._config, plan.project_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"render-{input_hash[:16]}.mp4"
        temporary_path = output_path.with_suffix(".tmp.mp4")
        filtergraph, video_label, audio_label = _filtergraph(
            plan,
            self._config.render.width,
            self._config.render.height,
            self._config.render.fps,
            self._config.render.loudness_target_lufs,
            self._config.render.true_peak_limit_dbfs,
        )
        command = [str(self._config.render.ffmpeg), "-y", "-v", "error", "-i", str(source_path),
                   "-filter_complex", filtergraph, "-map", video_label, "-map", audio_label,
                   *_encoder_args(requested_encoder), "-c:a", "aac", "-b:a", "192k",
                   "-ar", "48000", "-movflags", "+faststart", str(temporary_path)]
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
            )
            if not quality.passed:
                raise RenderExecutionError(f"quality gate pós-render falhou: {', '.join(issues)}")
            temporary_path.replace(output_path)
            document = RenderDocument(
                project_id=plan.project_id, source_asset_id=source.id,
                edit_plan_artifact_id=edit_plan_artifact.id, input_hash=input_hash,
                output_path=str(output_path), output_sha256=_sha256(output_path),
                output_size_bytes=output_path.stat().st_size,
                timeline_duration_seconds=plan.timeline_duration_seconds,
                segment_count=len(plan.segments),
                engine=RenderEngineInfo(
                    ffmpeg=str(self._config.render.ffmpeg), ffprobe=str(self._config.render.ffprobe),
                    requested_encoder=requested_encoder, effective_encoder=requested_encoder,
                    width=self._config.render.width, height=self._config.render.height,
                    fps=self._config.render.fps,
                ), quality=quality,
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
                          "requested_encoder": requested_encoder,
                          "effective_encoder": requested_encoder,
                          "quality_passed": True, "output_path": str(output_path)},
            ))
        finally:
            temporary_path.unlink(missing_ok=True)
        progress_cb(100.0, "Render concluído")
        return {"cached": False, "render_artifact_id": artifact.id,
                "render_path": str(output_path), "schema_version": artifact.schema_version}
