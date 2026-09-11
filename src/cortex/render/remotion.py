from __future__ import annotations

import hashlib
import json
import math
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore
from cortex.ingest.ffprobe import duration_seconds, probe_media
from cortex.render.captions import CaptionCue
from cortex.render.schemas import OVERLAY_SCHEMA_VERSION, RenderSettings

REMOTION_TIMEOUT_SECONDS = 1800
MAX_REMOTION_LOG_BYTES = 1024 * 1024
MAX_PROGRESS_LINE_BYTES = 8192


class RemotionOverlayError(RuntimeError):
    pass


class RemotionOverlayCancelled(RuntimeError):
    pass


class RemotionOverlayManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = Field(alias="schemaVersion")
    renderer: str
    output_path: str = Field(alias="outputPath")
    output_sha256: str = Field(alias="outputSha256", pattern=r"^[0-9a-f]{64}$")
    output_size_bytes: int = Field(alias="outputSizeBytes", ge=1)
    codec: str
    pixel_format: str = Field(alias="pixelFormat")
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    fps: int = Field(ge=1)
    duration_seconds: float = Field(alias="durationSeconds", gt=0)
    word_count: int = Field(alias="wordCount", ge=0)
    node_executable: str = Field(alias="nodeExecutable")
    node_version: str = Field(alias="nodeVersion")
    remotion_version: str = Field(alias="remotionVersion")
    caption_text_color: str = Field(alias="captionTextColor")
    caption_karaoke_color: str = Field(alias="captionKaraokeColor")
    caption_position_y: float = Field(alias="captionPositionY", ge=0.1, le=0.9)
    headline_burst_color: str = Field(alias="headlineBurstColor")
    headline_strip_color: str = Field(alias="headlineStripColor")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _app_sha256(app_dir: Path) -> str:
    files = [
        app_dir / "package.json",
        app_dir / "package-lock.json",
        app_dir / "tsconfig.json",
        app_dir / "render-overlay.mjs",
        app_dir / "payload.mjs",
    ]
    files.extend(sorted((app_dir / "src").glob("**/*")))
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(app_dir)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _resolve_executable(path: Path, label: str) -> Path:
    if path.is_absolute():
        resolved = path
    else:
        found = shutil.which(str(path))
        if found is None:
            raise RemotionOverlayError(f"{label} não está disponível: {path}")
        resolved = Path(found)
    if not resolved.is_file():
        raise RemotionOverlayError(f"{label} não é um arquivo executável: {resolved}")
    return resolved.resolve()


def _progress_message(line: bytes) -> tuple[float, str] | None:
    """Accept only the bounded, versioned renderer event, never arbitrary logs."""
    if len(line) > MAX_PROGRESS_LINE_BYTES:
        return None
    try:
        event = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    if (not isinstance(event, dict) or event.get("type") != "cortex.remotion.progress"
            or type(event.get("schemaVersion")) is not int or event.get("schemaVersion") != 1):
        return None
    percent = event.get("percent")
    if (type(percent) not in (int, float) or not 0 <= percent <= 100
            or not math.isfinite(percent)):
        return None
    rendered, encoded, total = (event.get(key) for key in
                                ("renderedFrames", "encodedFrames", "totalFrames"))
    if (any(type(count) is not int for count in (rendered, encoded, total))
            or total < 1 or not 0 <= rendered <= total or not 0 <= encoded <= total):
        return None
    return float(percent), (
        f"Remotion: {rendered}/{total} quadros renderizados; {encoded}/{total} codificados"
    )


def _stop_process(process: subprocess.Popen) -> None:
    """Stop Node and the browser/compositor children it launched."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _run_command(
    command: list[str], log_path: Path, should_cancel: Callable[[], bool],
    progress_cb: Callable[[float, str], None] | None = None,
) -> None:
    if should_cancel():
        raise RemotionOverlayCancelled()
    with log_path.open("w+b") as log, selectors.DefaultSelector() as selector:
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        except OSError as exc:
            raise RemotionOverlayError(f"Remotion não pôde ser executado: {exc}") from exc
        assert process.stdout is not None
        selector.register(process.stdout, selectors.EVENT_READ)
        started = time.monotonic()
        pending = b""
        discard_line = False
        last_percent = -1.0
        try:
            while selector.get_map():
                if should_cancel():
                    raise RemotionOverlayCancelled()
                if time.monotonic() - started > REMOTION_TIMEOUT_SECONDS:
                    raise RemotionOverlayError(
                        f"Remotion excedeu o timeout de {REMOTION_TIMEOUT_SECONDS}s"
                    )
                ready = selector.select(timeout=0.1)
                if not ready and process.poll() is not None:
                    break
                for key, _events in ready:
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    log_chunk = chunk[-MAX_REMOTION_LOG_BYTES:]
                    if log.tell() + len(log_chunk) > MAX_REMOTION_LOG_BYTES:
                        retained = min(log.tell(), MAX_REMOTION_LOG_BYTES // 2,
                                       MAX_REMOTION_LOG_BYTES - len(log_chunk))
                        log.seek(-retained, os.SEEK_END)
                        tail = log.read(retained)
                        log.seek(0)
                        log.write(tail)
                        log.truncate()
                    log.write(log_chunk)
                    log.flush()
                    for part in chunk.splitlines(keepends=True):
                        finished = part.endswith(b"\n")
                        if not discard_line:
                            pending += part
                            if len(pending) > MAX_PROGRESS_LINE_BYTES:
                                pending, discard_line = b"", True
                        if finished:
                            update = None if discard_line else _progress_message(pending)
                            if update and update[0] > last_percent:
                                last_percent = update[0]
                                if progress_cb is not None:
                                    progress_cb(*update)
                            pending, discard_line = b"", False
            while process.poll() is None:
                if should_cancel():
                    raise RemotionOverlayCancelled()
                if time.monotonic() - started > REMOTION_TIMEOUT_SECONDS:
                    raise RemotionOverlayError(
                        f"Remotion excedeu o timeout de {REMOTION_TIMEOUT_SECONDS}s"
                    )
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                _stop_process(process)
            process.stdout.close()
        if process.returncode:
            log.seek(max(0, log.tell() - 4000))
            detail = log.read(4000).decode(errors="replace")
            raise RemotionOverlayError(
                f"Remotion falhou ({process.returncode}): {detail}"
            )


class RemotionOverlayService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def run(
        self,
        *,
        project_id: str,
        output_dir: Path,
        settings: RenderSettings,
        duration_seconds_value: float,
        cues: list[CaptionCue],
        should_cancel: Callable[[], bool],
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> tuple[StageArtifact, RemotionOverlayManifest, bool]:
        node = _resolve_executable(self._config.render.remotion_node, "Node")
        browser_setting = self._config.render.remotion_browser
        if browser_setting is None:
            raise RemotionOverlayError(
                "render.remotion_browser deve apontar para um navegador instalado"
            )
        browser = _resolve_executable(browser_setting, "navegador Remotion")
        app_dir = self._config.render.remotion_app_dir
        if not app_dir.is_absolute():
            app_dir = _repo_root() / app_dir
        entrypoint = app_dir / "render-overlay.mjs"
        lockfile = app_dir / "package-lock.json"
        if not entrypoint.is_file() or not lockfile.is_file():
            raise RemotionOverlayError("app Remotion versionado não está instalado")

        payload = {
            "schemaVersion": OVERLAY_SCHEMA_VERSION,
            "width": settings.canvas.width,
            "height": settings.canvas.height,
            "fps": settings.canvas.fps,
            "durationSeconds": duration_seconds_value,
            "browserExecutable": str(browser),
            "caption": {
                "enabled": settings.captions.enabled,
                "fontFamily": settings.captions.font_family,
                "fontSize": settings.captions.font_size,
                "fontWeight": settings.captions.font_weight,
                "uppercase": settings.captions.uppercase,
                "outline": settings.captions.outline,
                "shadow": settings.captions.shadow,
                "karaoke": settings.captions.karaoke,
                "wordsPerCue": settings.captions.words_per_cue,
                "positionY": settings.captions.position_y,
                "textColor": settings.captions.text_color,
                "karaokeColor": settings.captions.karaoke_color,
                "outlineColor": settings.captions.outline_color,
                "shadowColor": settings.captions.shadow_color,
                "animation": {
                    "style": settings.captions.animation.style,
                    "durationSeconds": settings.captions.animation.duration_seconds,
                },
            },
            "headline": {
                "enabled": settings.headline.enabled,
                "text": settings.headline.text,
                "fontFamily": settings.headline.font_family,
                "fontSize": settings.headline.font_size,
                "durationSeconds": settings.headline.duration_seconds,
                "burstColor": settings.headline.burst_color,
                "stripColor": settings.headline.strip_color,
                "textColor": settings.headline.text_color,
                "animation": {
                    "entrance": settings.headline.animation.entrance,
                    "exit": settings.headline.animation.exit,
                    "durationSeconds": settings.headline.animation.duration_seconds,
                },
            },
            "cues": [
                {
                    "start": cue.start,
                    "end": cue.end,
                    "words": [
                        {
                            "text": word.text,
                            "start": max(cue.start, word.start),
                            "end": min(cue.end, word.end),
                        }
                        for word in cue.words
                        if min(cue.end, word.end) > max(cue.start, word.start)
                    ],
                }
                for cue in cues
            ],
        }
        word_count = sum(len(cue["words"]) for cue in payload["cues"])
        input_hash = hashlib.sha256(
            json.dumps(
                {"payload": payload, "remotion_app_sha256": _app_sha256(app_dir)},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        cached = self._domain.find_cached_stage_artifact(
            project_id=project_id,
            stage="render_overlay",
            input_hash=input_hash,
            schema_version=OVERLAY_SCHEMA_VERSION,
        )
        if cached is not None:
            manifest = RemotionOverlayManifest.model_validate_json(Path(cached.path).read_text())
            overlay_path = Path(manifest.output_path)
            if overlay_path.is_file() and _sha256(overlay_path) == manifest.output_sha256:
                if progress_cb is not None:
                    progress_cb(100.0, "Legendas e headline em cache reutilizadas")
                return cached, manifest, True

        output_dir.mkdir(parents=True, exist_ok=True)
        overlay_path = output_dir / f"overlay-{input_hash[:16]}.webm"
        manifest_path = output_dir / f"overlay-{input_hash[:16]}.json"
        log_path = output_dir / f"overlay-{input_hash[:16]}.remotion.log"
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=output_dir, prefix=".overlay-", suffix=".json",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            input_path = Path(handle.name)
        try:
            _run_command(
                [
                    str(node), str(entrypoint), "--input", str(input_path),
                    "--output", str(overlay_path), "--manifest", str(manifest_path),
                ],
                log_path,
                should_cancel,
                progress_cb,
            )
            manifest = RemotionOverlayManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            self._validate_manifest(
                manifest,
                overlay_path=overlay_path,
                settings=settings,
                duration_seconds_value=duration_seconds_value,
                word_count=word_count,
            )
            artifact = self._domain.create_stage_artifact(StageArtifact(
                project_id=project_id,
                stage="render_overlay",
                schema_version=OVERLAY_SCHEMA_VERSION,
                path=str(manifest_path),
                input_hash=input_hash,
                metadata={
                    "output_path": str(overlay_path),
                    "output_sha256": manifest.output_sha256,
                    "renderer": manifest.renderer,
                    "remotion_version": manifest.remotion_version,
                    "caption_text_color": settings.captions.text_color,
                    "caption_position_y": settings.captions.position_y,
                    "headline_burst_color": settings.headline.burst_color,
                },
            ))
            return artifact, manifest, False
        finally:
            input_path.unlink(missing_ok=True)

    def _validate_manifest(
        self,
        manifest: RemotionOverlayManifest,
        *,
        overlay_path: Path,
        settings: RenderSettings,
        duration_seconds_value: float,
        word_count: int,
    ) -> None:
        if manifest.schema_version != OVERLAY_SCHEMA_VERSION:
            raise RemotionOverlayError("schema do overlay Remotion não suportado")
        if manifest.renderer != "remotion" or manifest.codec != "vp9":
            raise RemotionOverlayError("renderer/codec efetivo do overlay é inválido")
        if manifest.pixel_format != "yuva420p":
            raise RemotionOverlayError("overlay Remotion não declarou pixel format alpha")
        if not manifest.node_version or not manifest.remotion_version:
            raise RemotionOverlayError("manifesto do overlay está incompleto")
        if Path(manifest.output_path).resolve() != overlay_path.resolve():
            raise RemotionOverlayError("manifesto Remotion aponta para output inesperado")
        if not overlay_path.is_file() or _sha256(overlay_path) != manifest.output_sha256:
            raise RemotionOverlayError("hash do overlay Remotion é inválido")
        if overlay_path.stat().st_size != manifest.output_size_bytes:
            raise RemotionOverlayError("tamanho do overlay Remotion é inválido")
        if (manifest.width, manifest.height, manifest.fps) != (
            settings.canvas.width, settings.canvas.height, settings.canvas.fps,
        ):
            raise RemotionOverlayError("canvas/FPS do overlay Remotion não corresponde ao master")
        if abs(manifest.duration_seconds - duration_seconds_value) > 1 / settings.canvas.fps:
            raise RemotionOverlayError("duração do overlay Remotion não corresponde ao master")
        if manifest.word_count != word_count:
            raise RemotionOverlayError("contagem de palavras do overlay Remotion é inválida")
        probe = probe_media(self._config.render.ffprobe, overlay_path)
        video = next(
            (stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"),
            None,
        )
        alpha_mode = str((video or {}).get("tags", {}).get("alpha_mode", ""))
        if video is None or video.get("codec_name") != "vp9" or alpha_mode != "1":
            raise RemotionOverlayError("overlay WebM não contém VP9 alpha_mode=1")
        if abs(duration_seconds(probe) - duration_seconds_value) > 1 / settings.canvas.fps + 0.05:
            raise RemotionOverlayError("duração física do overlay Remotion é incompatível")
