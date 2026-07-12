from __future__ import annotations

from cortex.analyze.camera_schemas import CameraScene
from cortex.analyze.visual_quality_schemas import SceneVisualQuality


def classify_shot_intent(
    camera: CameraScene, quality: SceneVisualQuality | None
) -> tuple[str, list[str]]:
    evidence = [f"camera_role:{camera.role}"]
    if quality is None:
        return "fallback", [*evidence, "visual_quality_missing"]
    if not quality.usable:
        return "fallback", [*evidence, *[f"quality_issue:{issue}" for issue in quality.issues]]
    evidence.append("visual_quality_usable")
    if camera.role == "speaker_close" and camera.speaker_alignment == "confirmed":
        return "speaker", [*evidence, "visible_speaker_confirmed"]
    if camera.role in {"two_shot", "wide"}:
        return "context", [*evidence, "context_layout"]
    return "fallback", evidence
