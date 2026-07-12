from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from cortex.render.safe_zones import SAFE_ZONES
from cortex.render.schemas import RenderQualityPublicationReport
from cortex.render.service import RenderService

_WIDTH = 1080
_HEIGHT = 1920


def _make_overlay(ffmpeg: Path, path: Path, boxes: list[tuple[int, int, int, int]]) -> None:
    """Gera um WebM VP9 yuva420p transparente com retângulos opacos nas posições
    (x, y, w, h) informadas, para exercer a análise de pixels alpha. O canal
    alpha é escrito explicitamente por `geq` porque `drawbox` não altera o plano
    alpha; o decoder libvpx-vp9 é quem preserva o alpha no roundtrip."""
    conditions = "+".join(
        f"between(X,{x},{x + w})*between(Y,{y},{y + h})"
        for (x, y, w, h) in boxes
    )
    alpha_expr = f"if(gt({conditions},0),255,0)"
    subprocess.run([
        str(ffmpeg), "-y", "-v", "error", "-f", "lavfi", "-i",
        f"color=c=white:size={_WIDTH}x{_HEIGHT}:rate=4:duration=0.5",
        "-vf", f"format=yuva420p,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha_expr}'",
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", str(path),
    ], check=True, timeout=60)


def _service(ffmpeg: Path) -> RenderService:
    service = RenderService.__new__(RenderService)
    service._config = SimpleNamespace(
        render=SimpleNamespace(ffmpeg=ffmpeg, safe_zone_sample_fps=8.0)
    )
    return service


def test_safe_zone_issues_detect_real_overlay_overflow(tmp_path: Path) -> None:
    ffmpeg = Path(shutil.which("ffmpeg") or "ffmpeg")
    service = _service(ffmpeg)
    zone = SAFE_ZONES["9:16"]

    inside = tmp_path / "inside.webm"
    # headline dentro da metade superior; legenda dentro da metade inferior.
    _make_overlay(ffmpeg, inside, [(200, 300, 400, 200), (200, 1400, 400, 120)])
    assert service._safe_zone_issues(
        overlay_path=str(inside), width=_WIDTH, height=_HEIGHT, safe_zone=zone
    ) == []

    outside = tmp_path / "outside.webm"
    # caixa colada no canto superior esquerdo (viola top+left) e caixa que
    # ultrapassa a margem inferior.
    _make_overlay(ffmpeg, outside, [(10, 10, 300, 100), (200, 1700, 400, 200)])
    issues = service._safe_zone_issues(
        overlay_path=str(outside), width=_WIDTH, height=_HEIGHT, safe_zone=zone
    )
    assert "headline_outside_safe_zone" in issues
    assert "caption_outside_safe_zone" in issues


def test_safe_zone_missing_zone_and_absent_overlay() -> None:
    service = RenderService.__new__(RenderService)
    assert service._safe_zone_issues(
        overlay_path=None, width=_WIDTH, height=_HEIGHT, safe_zone=None
    ) == ["safe_zone_missing"]
    assert service._safe_zone_issues(
        overlay_path=None, width=_WIDTH, height=_HEIGHT, safe_zone=SAFE_ZONES["9:16"]
    ) == []


def test_publication_report_keeps_reason_codes_and_verdict() -> None:
    report = RenderQualityPublicationReport(
        publish_ready=False,
        reasons=["headline_outside_safe_zone", "loudness_out_of_tolerance"],
        loudness={"target_lufs": -14.0, "integrated_lufs": -12.5, "true_peak_dbfs": -0.3},
        visual={"blackdetect": 1, "freezedetect": 0, "visual_analysis_performed": True, "black_threshold_seconds": 0.2, "freeze_threshold_seconds": 0.4},
        captions={"cue_count": 4, "subtitles_present": True, "headline_present": True},
        safe_zones={"version": 1, "canvas": "9:16", "margins": {"top": 0.07}},
        encoder={"requested": "libx264", "effective": "libx264"},
        dimensions={"width": 1080, "height": 1920, "fps": 30, "duration_seconds": 3.2},
        hashes={"input_hash": "a" * 64, "overlay_hash": "b" * 64, "render_sha256": None},
        provenance={"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "node": "node", "node_version": "v20", "remotion_version": "4.0.489"},
    )
    assert report.publish_ready is False
    assert report.reasons == ["headline_outside_safe_zone", "loudness_out_of_tolerance"]
