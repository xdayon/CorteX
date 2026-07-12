from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from cortex.analyze.camera_schemas import CameraScene, CameraTimelineDocument
from cortex.analyze.face_recognition import cosine_similarity
from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.identity_schemas import (
    IDENTITY_INDEX_SCHEMA_VERSION,
    IdentityEngineInfo,
    IdentityEntity,
    IdentityIndexDocument,
    IdentityObservation,
)
from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore
from cortex.paths import identities_dir

IDENTITY_ALGORITHM_VERSION = "1.0.0"
AMBIGUITY_MARGIN = 0.08


class IdentityIndexJobCancelled(RuntimeError):
    pass


class IdentityIndexPreconditionError(ValueError):
    pass


def _camera_scene_at(scenes: list[CameraScene], time_us: int) -> CameraScene | None:
    return next((scene for scene in scenes if scene.start_us <= time_us < scene.end_us), None)


def _minimum_similarity(embedding: list[float], members: list[dict]) -> float:
    return min(cosine_similarity(embedding, member["embedding"]) for member in members)


def cluster_identity_observations(
    observations: list[dict], *, threshold: float, ambiguity_margin: float
) -> list[list[dict]]:
    """Complete-link clustering; ambiguous matches remain isolated."""
    clusters: list[list[dict]] = []
    for observation in observations:
        candidates = sorted(
            (
                (_minimum_similarity(observation["embedding"], cluster), index)
                for index, cluster in enumerate(clusters)
            ),
            key=lambda item: (-item[0], item[1]),
        )
        eligible = [candidate for candidate in candidates if candidate[0] >= threshold]
        ambiguous = (
            len(eligible) > 1 and eligible[0][0] - eligible[1][0] < ambiguity_margin
        )
        if eligible and not ambiguous:
            score, cluster_index = eligible[0]
            observation["assignment_similarity"] = score
            clusters[cluster_index].append(observation)
        else:
            observation["ambiguous_match"] = ambiguous
            clusters.append([observation])
    return clusters


def _identity_id(observation_id: str) -> str:
    return "identity-" + hashlib.sha256(observation_id.encode()).hexdigest()[:12]


class IdentityIndexService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def run(
        self,
        *,
        face_index_artifact: StageArtifact,
        camera_timeline_artifact: StageArtifact,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        if should_cancel():
            raise IdentityIndexJobCancelled()
        face_path = Path(face_index_artifact.path)
        camera_path = Path(camera_timeline_artifact.path)
        if not face_path.exists() or not camera_path.exists():
            raise IdentityIndexPreconditionError("artifact upstream de identidade ausente")
        progress_cb(0.0, "Validando embeddings e timeline de cameras")
        faces = FaceIndexDocument.model_validate_json(face_path.read_text(encoding="utf-8"))
        cameras = CameraTimelineDocument.model_validate_json(camera_path.read_text(encoding="utf-8"))
        if (
            cameras.project_id != faces.project_id
            or cameras.source_asset_id != faces.source_asset_id
            or cameras.source_sha256 != faces.source_sha256
            or cameras.face_index_artifact_id != face_index_artifact.id
        ):
            raise IdentityIndexPreconditionError("camera_timeline não corresponde ao face_index")
        engine = faces.engine
        if (
            engine.recognizer != "sface_2021dec"
            or engine.recognition_model_path is None
            or engine.recognition_model_sha256 is None
            or engine.embedding_dimension is None
            or engine.cosine_match_threshold is None
        ):
            raise IdentityIndexPreconditionError("face_index não possui proveniência SFace completa")

        hash_payload = {
            "algorithm": IDENTITY_ALGORITHM_VERSION,
            "schema_version": IDENTITY_INDEX_SCHEMA_VERSION,
            "face_index": [face_index_artifact.id, face_index_artifact.input_hash],
            "camera_timeline": [camera_timeline_artifact.id, camera_timeline_artifact.input_hash],
            "recognition_model": [engine.recognition_model_sha256, engine.embedding_dimension],
            "cosine_match_threshold": engine.cosine_match_threshold,
            "ambiguity_margin": AMBIGUITY_MARGIN,
            "identity_scope": "cross_layout_face_embedding",
        }
        input_hash = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cached = self._domain.find_cached_stage_artifact(
            project_id=faces.project_id,
            stage="identity_index",
            input_hash=input_hash,
            schema_version=IDENTITY_INDEX_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Índice de identidade em cache reutilizado")
            return {
                "cached": True,
                "identity_index_artifact_id": cached.id,
                "identity_index_path": cached.path,
                "schema_version": cached.schema_version,
            }

        raw_observations: list[dict] = []
        unresolved_count = 0
        for frame_index, frame in enumerate(faces.frames):
            time_us = round(frame.time * 1_000_000)
            camera_scene = _camera_scene_at(cameras.scenes, time_us)
            for face_index, face in enumerate(frame.faces):
                if face.embedding is None or face.track_id is None or camera_scene is None:
                    unresolved_count += 1
                    continue
                if len(face.embedding) != engine.embedding_dimension:
                    raise IdentityIndexPreconditionError("dimensão de embedding SFace inconsistente")
                raw_observations.append({
                    "observation_id": f"face-{frame_index}-{face_index}",
                    "time_us": time_us,
                    "scene_index": camera_scene.scene_index,
                    "layout_id": camera_scene.layout_id,
                    "local_track_id": face.track_id,
                    "embedding": face.embedding,
                    "assignment_similarity": None,
                    "ambiguous_match": False,
                })
        raw_observations.sort(key=lambda item: (item["time_us"], item["observation_id"]))
        progress_cb(35.0, "Comparando embeddings SFace entre layouts")
        clusters = cluster_identity_observations(
            raw_observations,
            threshold=engine.cosine_match_threshold,
            ambiguity_margin=AMBIGUITY_MARGIN,
        )

        identities: list[IdentityEntity] = []
        observations: list[IdentityObservation] = []
        for cluster in clusters:
            identity_id = _identity_id(cluster[0]["observation_id"])
            layout_ids = sorted({item["layout_id"] for item in cluster})
            ambiguous = any(item["ambiguous_match"] for item in cluster)
            if ambiguous:
                status = "ambiguous"
            elif len(layout_ids) >= 2 and len(cluster) >= 2:
                status = "confirmed"
            else:
                status = "single_layout"
            pair_scores = [
                cosine_similarity(cluster[left]["embedding"], cluster[right]["embedding"])
                for left in range(len(cluster))
                for right in range(left + 1, len(cluster))
            ]
            evidence = ["sface_cosine_complete_link", f"layouts:{len(layout_ids)}"]
            if status == "confirmed":
                evidence.append("cross_layout_continuity_confirmed")
            identities.append(IdentityEntity(
                identity_id=identity_id,
                status=status,
                observation_ids=[item["observation_id"] for item in cluster],
                layout_ids=layout_ids,
                scene_indices=sorted({item["scene_index"] for item in cluster}),
                local_track_ids=sorted({item["local_track_id"] for item in cluster}),
                sample_count=len(cluster),
                minimum_pair_similarity=min(pair_scores) if pair_scores else None,
                evidence=evidence,
            ))
            observations.extend(IdentityObservation(
                observation_id=item["observation_id"],
                time_us=item["time_us"],
                scene_index=item["scene_index"],
                layout_id=item["layout_id"],
                local_track_id=item["local_track_id"],
                identity_id=identity_id,
                status=status,
                assignment_similarity=item["assignment_similarity"],
                ambiguous_match=item["ambiguous_match"],
            ) for item in cluster)

        if should_cancel():
            raise IdentityIndexJobCancelled()
        document = IdentityIndexDocument(
            project_id=faces.project_id,
            source_asset_id=faces.source_asset_id,
            source_sha256=faces.source_sha256,
            face_index_artifact_id=face_index_artifact.id,
            face_index_input_hash=face_index_artifact.input_hash,
            camera_timeline_artifact_id=camera_timeline_artifact.id,
            camera_timeline_input_hash=camera_timeline_artifact.input_hash,
            input_hash=input_hash,
            observations=sorted(observations, key=lambda item: (item.time_us, item.observation_id)),
            identities=identities,
            unresolved_face_count=unresolved_count,
            engine=IdentityEngineInfo(
                algorithm="sface_complete_link_cross_layout",
                algorithm_version=IDENTITY_ALGORITHM_VERSION,
                recognizer="sface_2021dec",
                model_path=engine.recognition_model_path,
                model_sha256=engine.recognition_model_sha256,
                embedding_dimension=engine.embedding_dimension,
                cosine_match_threshold=engine.cosine_match_threshold,
                ambiguity_margin=AMBIGUITY_MARGIN,
            ),
        )
        progress_cb(90.0, "Persistindo IdentityIndexArtifact")
        out_dir = identities_dir(self._config, faces.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"identity-index-{input_hash[:16]}.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if should_cancel():
            temporary_path.unlink(missing_ok=True)
            raise IdentityIndexJobCancelled()
        temporary_path.replace(output_path)
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=faces.project_id,
            stage="identity_index",
            schema_version=IDENTITY_INDEX_SCHEMA_VERSION,
            path=str(output_path),
            input_hash=input_hash,
            metadata={
                "face_index_artifact_id": face_index_artifact.id,
                "camera_timeline_artifact_id": camera_timeline_artifact.id,
                "identity_count": len(identities),
                "confirmed_identity_count": sum(item.status == "confirmed" for item in identities),
                "unresolved_face_count": unresolved_count,
                "identity_scope": "cross_layout_face_embedding",
            },
        ))
        progress_cb(100.0, "Índice de identidade concluído")
        return {
            "cached": False,
            "identity_index_artifact_id": artifact.id,
            "identity_index_path": artifact.path,
            "schema_version": artifact.schema_version,
        }
