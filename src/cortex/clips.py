"""One integrated view of every suggested clip: suggestions, exports, publication, manual state."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from cortex.domain.models import ClipPublication, ClipState, ExportItem, ExportItemStatus
from cortex.domain.store import DomainStore

ClipStatus = Literal["new", "approved", "exported", "saved", "posted", "discarded"]
CLIP_STATUSES: tuple[str, ...] = ("new", "approved", "exported", "saved", "posted", "discarded")

_SUGGESTION_CACHE: dict[tuple[str, int], dict[str, Any]] = {}
_CACHE_LIMIT = 64


def js_number(value: float | int | str) -> str:
    """Mirror JavaScript String(number), so keys match the ones the web client builds."""
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if value != value:
        return "NaN"
    if value in (float("inf"), float("-inf")):
        return "Infinity" if value > 0 else "-Infinity"
    magnitude = abs(value)
    if value == int(value) and magnitude < 1e21:
        return str(int(value))
    if 1e-6 <= magnitude < 1e21:
        return format(Decimal(repr(value)), "f")
    mantissa, _, exponent = repr(value).partition("e")
    sign = exponent[0] if exponent[:1] in "+-" else "+"
    return f"{mantissa}e{sign}{exponent.lstrip('+-').lstrip('0') or '0'}"


def clip_key(clip: dict[str, Any]) -> str:
    return ":".join(js_number(clip.get(name, "undefined")) for name in
                    ("rank", "start_second", "end_second", "pacing"))


def derive_status(
    state: ClipState | None, exports: list[dict[str, Any]], publication: ClipPublication | None,
) -> ClipStatus:
    if publication is not None and publication.published:
        return "posted"
    if state is not None and state.decision == "discarded":
        return "discarded"
    if state is not None and state.saved:
        return "saved"
    if exports:
        return "exported"
    if state is not None and state.decision == "approved":
        return "approved"
    return "new"


def read_suggestion(path: str, warnings: list[str]) -> dict[str, Any] | None:
    resolved = Path(path).resolve()
    try:
        key = (str(resolved), resolved.stat().st_mtime_ns)
    except OSError:
        warnings.append(f"Sugestões indisponíveis: {path}")
        return None
    cached = _SUGGESTION_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        document = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        warnings.append(f"Sugestões ilegíveis: {path}")
        return None
    if not isinstance(document, dict):
        warnings.append(f"Sugestões ilegíveis: {path}")
        return None
    if len(_SUGGESTION_CACHE) >= _CACHE_LIMIT:
        _SUGGESTION_CACHE.clear()
    _SUGGESTION_CACHE[key] = document
    return document


def suggested_clips(domain: DomainStore, run, warnings: list[str]) -> list[dict[str, Any]]:
    artifact_id = run.artifacts.get("suggestion")
    if not artifact_id:
        return []
    try:
        artifact = domain.get_stage_artifact(artifact_id)
    except LookupError:
        warnings.append(f"Artefato de sugestão ausente: {artifact_id}")
        return []
    document = read_suggestion(artifact.path, warnings)
    clips = ((document or {}).get("selection") or {}).get("clips") or []
    return [clip for clip in clips if isinstance(clip, dict)]


def _read_manifest(path: str, warnings: list[str]) -> dict[str, Any] | None:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        warnings.append(f"Manifesto de render ilegível: {path}")
        return None
    return document if isinstance(document, dict) else None


def _is_draft(artifact_path: str, manifest: dict[str, Any]) -> bool:
    names = (Path(artifact_path).name, Path(str(manifest.get("output_path") or "")).name)
    return manifest.get("purpose") == "draft" or any(n.startswith("draft-render") for n in names)


def _headline(manifest: dict[str, Any] | None) -> str:
    settings = (manifest or {}).get("requested_settings") or (manifest or {}).get("effective_settings") or {}
    return str((settings.get("headline") or {}).get("text") or "").strip()


def _export_entry(
    project_id: str, artifact_id: str, batch_id: str | None, exported_at: str,
    manifest: dict[str, Any] | None, headline: str, prefix: str,
) -> dict[str, Any]:
    base = f"{prefix}/projects/{project_id}/renders/{artifact_id}"
    duration = (manifest or {}).get("timeline_duration_seconds")
    return {
        "render_artifact_id": artifact_id, "batch_id": batch_id, "exported_at": exported_at,
        "duration_seconds": duration, "headline": headline or _headline(manifest),
        "media_url": f"{base}/media", "subtitles_url": f"{base}/subtitles",
    }


def _item_export(
    domain: DomainStore, item: ExportItem, exported_at: str, prefix: str, warnings: list[str],
) -> dict[str, Any] | None:
    try:
        artifact = domain.get_stage_artifact(item.render_artifact_id)
    except LookupError:
        warnings.append(f"Render ausente: {item.render_artifact_id}")
        return None
    manifest = _read_manifest(artifact.path, warnings)
    if manifest is not None and _is_draft(artifact.path, manifest):
        return None
    headline = str((item.render_settings.get("headline") or {}).get("text") or "").strip()
    return _export_entry(
        item.project_id, artifact.id, item.batch_id, exported_at, manifest, headline, prefix,
    )


def _exports_by_clip(
    domain: DomainStore, run_id: str, prefix: str, warnings: list[str], linked: set[str],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for batch in domain.list_export_batches(run_id):
        for item in batch.items:
            if item.render_artifact_id:
                linked.add(item.render_artifact_id)
            if item.status != ExportItemStatus.SUCCEEDED or not item.render_artifact_id:
                continue
            entry = _item_export(domain, item, item.updated_at.isoformat(), prefix, warnings)
            if entry is not None:
                result.setdefault(item.clip_key, []).append(entry)
    for entries in result.values():
        entries.sort(key=lambda e: e["exported_at"], reverse=True)
    return result


def _publication_for(
    exports: list[dict[str, Any]], publications: dict[str, ClipPublication],
) -> ClipPublication | None:
    found = [publications[e["render_artifact_id"]] for e in exports
             if e["render_artifact_id"] in publications]
    if not found:
        return None
    return max(found, key=lambda p: (p.published, p.updated_at))


def _run_summary(run, clip_count: int) -> dict[str, Any]:
    brief = run.brief or {}
    return {
        "id": run.id, "project_id": run.project_id, "source_asset_id": run.source_asset_id,
        "created_at": run.created_at.isoformat(), "status": str(run.status.value),
        "clip_count": clip_count,
        "brief": {
            "count": brief.get("count"), "minimum_seconds": brief.get("minimum_seconds"),
            "maximum_seconds": brief.get("maximum_seconds"),
            "primary_subject": brief.get("primary_subject"),
        },
    }


def _clip_row(
    clip: dict[str, Any], key: str, run, state: ClipState | None,
    exports: list[dict[str, Any]], publication: ClipPublication | None,
) -> dict[str, Any]:
    scores = clip.get("scores") if isinstance(clip.get("scores"), dict) else {}
    score = scores.get("total", clip.get("score"))
    return {
        "project_id": run.project_id, "run_id": run.id,
        "source_asset_id": run.source_asset_id,
        "run_created_at": run.created_at.isoformat(), "clip_key": key,
        "rank": clip.get("rank"), "score": score, "headline": clip.get("headline"),
        "headline_options": clip.get("headline_options") or [], "title": clip.get("title"),
        "duration": clip.get("estimated_duration"),
        "start_second": clip.get("start_second"), "end_second": clip.get("end_second"),
        "piece_count": len(clip.get("pieces") or []), "warnings": clip.get("warnings") or [],
        "clip": clip, "state": state.model_dump(mode="json") if state else None,
        "exports": exports,
        "publication": publication.model_dump(mode="json") if publication else None,
        "status": derive_status(state, exports, publication),
    }


def _other_exports(
    domain: DomainStore, project_ids: list[str], linked: set[str], prefix: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    result = []
    for project_id in project_ids:
        for artifact in domain.list_stage_artifacts(project_id, stage="render"):
            if artifact.id in linked:
                continue
            manifest = _read_manifest(artifact.path, warnings)
            if manifest is None or _is_draft(artifact.path, manifest):
                continue
            entry = _export_entry(
                project_id, artifact.id, None, artifact.created_at.isoformat(), manifest, "",
                prefix,
            )
            result.append({**entry, "project_id": project_id,
                           "title": entry["headline"] or "Corte exportado"})
    result.sort(key=lambda e: e["exported_at"], reverse=True)
    return result


def count_statuses(clips: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in CLIP_STATUSES}
    for clip in clips:
        counts[clip["status"]] += 1
    return {**counts, "total": len(clips)}


def collect_clips(domain: DomainStore, project_ids: list[str], prefix: str) -> dict[str, Any]:
    warnings: list[str] = []
    states = {(s.workflow_run_id, s.clip_key): s for s in domain.list_clip_states(project_ids)}
    publications = {p.render_artifact_id: p for project_id in project_ids
                    for p in domain.list_clip_publications(project_id=project_id, limit=10000)}
    runs = sorted((run for project_id in project_ids
                   for run in domain.list_workflow_runs(project_id)),
                  key=lambda run: run.created_at, reverse=True)
    summaries, clips, linked = [], [], set()
    for run in runs:
        exports = _exports_by_clip(domain, run.id, prefix, warnings, linked)
        rows, seen = [], set()
        for clip in suggested_clips(domain, run, warnings):
            key = clip_key(clip)
            if key in seen:
                continue
            seen.add(key)
            clip_exports = exports.get(key, [])
            rows.append(_clip_row(
                clip, key, run, states.get((run.id, key)), clip_exports,
                _publication_for(clip_exports, publications),
            ))
        rows.sort(key=lambda row: (row["rank"] is None, row["rank"] or 0))
        summaries.append(_run_summary(run, len(rows)))
        clips.extend(rows)
    return {
        "runs": summaries, "clips": clips,
        "other_exports": _other_exports(domain, project_ids, linked, prefix, warnings),
        "counts": count_statuses(clips), "warnings": warnings,
    }
