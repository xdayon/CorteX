"""CorteX background worker.

Runs as a separate process from the API, sharing the same SQLite database.
Claims queued jobs atomically (see JobStore.claim_next), executes them, and
reports progress/cancellation cooperatively. The transcription engine keeps
its model warm across jobs — it is never loaded inside an API request.

Signal handling: SIGTERM/SIGINT stop the polling loop after the in-flight
job finishes (graceful drain). Jobs are not requeued on shutdown; a job
still `running` when the process is killed harder (SIGKILL) stays `running`
in the database and must be resolved manually — acceptable for this local,
single-worker phase.
"""

from __future__ import annotations

import argparse
import hashlib
import signal
import sys
import time
from pathlib import Path
from typing import Any

from cortex.analyze.service import AnalysisJobCancelled, AnalysisService
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind
from cortex.domain.store import DomainStore
from cortex.edit.service import EditPlanJobCancelled, EditPlanService
from cortex.ingest.ffprobe import FFprobeError, probe_media
from cortex.ingest.youtube import YoutubeDownloadError, download_youtube_source
from cortex.jobs import InvalidJobTransitionError, JobStore
from cortex.paths import source_dir
from cortex.render.service import RenderJobCancelled, RenderService
from cortex.schemas import Job, JobStatus, JobType, JobUpdate, PipelineStage
from cortex.suggest.provider import SuggestionProvider, build_suggestion_provider
from cortex.suggest.service import SuggestionJobCancelled, SuggestionService
from cortex.transcribe.engine import FasterWhisperEngine, TranscriptionEngine
from cortex.transcribe.service import TranscribeJobCancelled, TranscribeService

_MAX_ERROR_LENGTH = 4000


class _YoutubeCancelled(RuntimeError):
    pass


def _truncate(message: str) -> str:
    if len(message) <= _MAX_ERROR_LENGTH:
        return message
    return message[:_MAX_ERROR_LENGTH] + "… (truncado)"


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _job_is_cancelled(jobs: JobStore, job_id: str) -> bool:
    try:
        return jobs.get(job_id).status == JobStatus.CANCELLED
    except Exception:
        return False


def _safe_progress(
    jobs: JobStore, job_id: str, progress: float, message: str, stage: PipelineStage
) -> None:
    try:
        jobs.update(
            job_id,
            JobUpdate(progress=round(max(0.0, min(100.0, progress)), 2), message=message, stage=stage),
        )
    except InvalidJobTransitionError:
        pass  # job already reached a terminal state (e.g. cancelled) — stop reporting


def run_transcription_job(
    job: Job,
    config: CortexConfig,
    jobs: JobStore,
    domain: DomainStore,
    engine: TranscriptionEngine,
) -> None:
    source_asset_id = job.payload.get("source_asset_id")
    if not source_asset_id:
        raise ValueError("payload do job de transcrição sem source_asset_id")
    source_asset = domain.get_source_asset(source_asset_id)
    service = TranscribeService(config, domain, engine)

    def progress_cb(progress_percent: float, message: str) -> None:
        _safe_progress(jobs, job.id, progress_percent, message, PipelineStage.TRANSCRIBE)

    def should_cancel() -> bool:
        return _job_is_cancelled(jobs, job.id)

    try:
        result = service.run(
            source_asset=source_asset,
            overrides=job.payload.get("overrides") or {},
            progress_cb=progress_cb,
            should_cancel=should_cancel,
        )
    except TranscribeJobCancelled:
        return  # already marked cancelled by the API — nothing more to persist

    jobs.update(
        job.id,
        JobUpdate(
            status=JobStatus.SUCCEEDED,
            stage=PipelineStage.COMPLETE,
            message="Transcrição concluída",
            result=result,
        ),
    )


def run_youtube_ingest_job(
    job: Job,
    config: CortexConfig,
    jobs: JobStore,
    domain: DomainStore,
) -> None:
    url = job.payload.get("url")
    project_id = job.project_id
    if not url or not project_id:
        raise ValueError("payload do job de ingestão do YouTube incompleto (url/project_id)")

    dest_dir = source_dir(config, project_id)

    def should_cancel() -> bool:
        return _job_is_cancelled(jobs, job.id)

    def progress_cb(fraction: float) -> None:
        if should_cancel():
            raise _YoutubeCancelled()
        _safe_progress(jobs, job.id, fraction * 90.0, "Baixando vídeo do YouTube", PipelineStage.INGEST)

    _safe_progress(jobs, job.id, 0.0, "Resolvendo vídeo do YouTube", PipelineStage.INGEST)
    if should_cancel():
        return

    try:
        download = download_youtube_source(
            url, dest_dir, youtube_format=config.ingest.youtube_format, progress_cb=progress_cb,
        )
    except _YoutubeCancelled:
        return
    except YoutubeDownloadError as exc:
        raise RuntimeError(f"falha ao baixar vídeo do YouTube: {exc}") from exc

    if should_cancel():
        return

    _safe_progress(jobs, job.id, 90.0, "Analisando mídia baixada (ffprobe)", PipelineStage.INGEST)
    try:
        probe = probe_media(config.render.ffprobe, download.path)
    except FFprobeError as exc:
        download.path.unlink(missing_ok=True)
        raise RuntimeError(f"ffprobe falhou no arquivo baixado: {exc}") from exc

    sha256 = _sha256_of(download.path)
    asset = domain.find_source_asset_by_sha256(project_id, sha256)
    if asset is None:
        stored_path = download.path.relative_to(config.paths.data_dir)
        asset = domain.create_source_asset(
            SourceAsset(
                project_id=project_id,
                kind=SourceKind.YOUTUBE,
                source_url=url,
                stored_path=str(stored_path),
                sha256=sha256,
                size_bytes=download.path.stat().st_size,
                probe=probe,
            )
        )

    jobs.update(
        job.id,
        JobUpdate(
            status=JobStatus.SUCCEEDED,
            stage=PipelineStage.COMPLETE,
            message="Vídeo do YouTube pronto",
            result={
                "source_asset_id": asset.id,
                "cached": download.cached,
                "video_id": download.video_id,
            },
        ),
    )


def run_suggestion_job(
    job: Job,
    config: CortexConfig,
    jobs: JobStore,
    domain: DomainStore,
    provider: SuggestionProvider,
) -> None:
    transcript_artifact_id = job.payload.get("transcript_artifact_id")
    if not transcript_artifact_id:
        raise ValueError("payload do job de seleção sem transcript_artifact_id")
    transcript_artifact = domain.get_transcript_artifact(transcript_artifact_id)
    if transcript_artifact.project_id != job.project_id:
        raise ValueError("transcript do job de seleção não pertence ao projeto")
    service = SuggestionService(config, domain, provider)
    analysis_artifact_id = job.payload.get("analysis_artifact_id")
    if not analysis_artifact_id:
        raise ValueError("payload do job de seleção sem analysis_artifact_id")
    analysis_artifact = domain.get_stage_artifact(analysis_artifact_id)
    if analysis_artifact.project_id != job.project_id or analysis_artifact.stage != "analysis":
        raise ValueError("AnalysisArtifact do job de seleção não pertence ao projeto")

    def progress_cb(progress_percent: float, message: str) -> None:
        _safe_progress(jobs, job.id, progress_percent, message, PipelineStage.SUGGEST)

    def should_cancel() -> bool:
        return _job_is_cancelled(jobs, job.id)

    try:
        result = service.run(
            transcript_artifact=transcript_artifact,
            analysis_artifact=analysis_artifact,
            brief=job.payload.get("brief") or {},
            progress_cb=progress_cb,
            should_cancel=should_cancel,
        )
    except SuggestionJobCancelled:
        return
    jobs.update(
        job.id,
        JobUpdate(
            status=JobStatus.SUCCEEDED,
            stage=PipelineStage.COMPLETE,
            message="Sugestões editoriais concluídas",
            result=result,
        ),
    )


def run_analysis_job(
    job: Job,
    config: CortexConfig,
    jobs: JobStore,
    domain: DomainStore,
) -> None:
    transcript_artifact_id = job.payload.get("transcript_artifact_id")
    if not transcript_artifact_id:
        raise ValueError("payload do job de análise sem transcript_artifact_id")
    transcript = domain.get_transcript_artifact(transcript_artifact_id)
    if transcript.project_id != job.project_id:
        raise ValueError("transcript do job de análise não pertence ao projeto")
    source_asset = domain.get_source_asset(transcript.source_asset_id)
    service = AnalysisService(config, domain)

    def progress_cb(progress_percent: float, message: str) -> None:
        _safe_progress(jobs, job.id, progress_percent, message, PipelineStage.ANALYZE)

    def should_cancel() -> bool:
        return _job_is_cancelled(jobs, job.id)

    try:
        result = service.run(
            source_asset=source_asset,
            transcript_artifact=transcript,
            progress_cb=progress_cb,
            should_cancel=should_cancel,
        )
    except AnalysisJobCancelled:
        return
    jobs.update(
        job.id,
        JobUpdate(
            status=JobStatus.SUCCEEDED,
            stage=PipelineStage.COMPLETE,
            message="Análise local concluída",
            result=result,
        ),
    )


def run_edit_plan_job(
    job: Job,
    config: CortexConfig,
    jobs: JobStore,
    domain: DomainStore,
) -> None:
    transcript_artifact_id = job.payload.get("transcript_artifact_id")
    if not transcript_artifact_id:
        raise ValueError("payload do job de plano de edição sem transcript_artifact_id")
    transcript = domain.get_transcript_artifact(transcript_artifact_id)
    if transcript.project_id != job.project_id:
        raise ValueError("transcript do job de plano de edição não pertence ao projeto")
    analysis_artifact_id = job.payload.get("analysis_artifact_id")
    if not analysis_artifact_id:
        raise ValueError("payload do job de plano de edição sem analysis_artifact_id")
    analysis_artifact = domain.get_stage_artifact(analysis_artifact_id)
    if analysis_artifact.project_id != job.project_id or analysis_artifact.stage != "analysis":
        raise ValueError("AnalysisArtifact do job de plano de edição não pertence ao projeto")
    start = job.payload.get("start")
    end = job.payload.get("end")
    if start is None or end is None:
        raise ValueError("payload do job de plano de edição sem start/end")
    service = EditPlanService(config, domain)

    def progress_cb(progress_percent: float, message: str) -> None:
        _safe_progress(jobs, job.id, progress_percent, message, PipelineStage.EDIT)

    def should_cancel() -> bool:
        return _job_is_cancelled(jobs, job.id)

    try:
        result = service.run(
            transcript_artifact=transcript,
            analysis_artifact=analysis_artifact,
            start=float(start),
            end=float(end),
            profile=job.payload.get("profile"),
            progress_cb=progress_cb,
            should_cancel=should_cancel,
        )
    except EditPlanJobCancelled:
        return
    jobs.update(
        job.id,
        JobUpdate(
            status=JobStatus.SUCCEEDED,
            stage=PipelineStage.COMPLETE,
            message="Plano de edição concluído",
            result=result,
        ),
    )


def run_render_job(
    job: Job,
    config: CortexConfig,
    jobs: JobStore,
    domain: DomainStore,
) -> None:
    edit_plan_artifact_id = job.payload.get("edit_plan_artifact_id")
    if not edit_plan_artifact_id:
        raise ValueError("payload do job de render sem edit_plan_artifact_id")
    edit_plan = domain.get_stage_artifact(edit_plan_artifact_id)
    if edit_plan.project_id != job.project_id or edit_plan.stage != "edit_plan":
        raise ValueError("EditPlanArtifact do render não pertence ao projeto")
    service = RenderService(config, domain)

    def progress_cb(progress_percent: float, message: str) -> None:
        stage = PipelineStage.QUALITY if progress_percent >= 85 else PipelineStage.RENDER
        _safe_progress(jobs, job.id, progress_percent, message, stage)

    try:
        result = service.run(
            edit_plan_artifact=edit_plan,
            encoder=job.payload.get("encoder"),
            progress_cb=progress_cb,
            should_cancel=lambda: _job_is_cancelled(jobs, job.id),
        )
    except RenderJobCancelled:
        return
    jobs.update(job.id, JobUpdate(
        status=JobStatus.SUCCEEDED, stage=PipelineStage.COMPLETE,
        message="Render concluído", result=result,
    ))


_HANDLERS: dict[JobType, Any] = {
    JobType.TRANSCRIPTION: run_transcription_job,
    JobType.ANALYSIS: run_analysis_job,
    JobType.INGEST_YOUTUBE: run_youtube_ingest_job,
    JobType.SUGGESTION: run_suggestion_job,
    JobType.EDIT_PLAN: run_edit_plan_job,
    JobType.RENDER: run_render_job,
}


def process_next(
    config: CortexConfig,
    jobs: JobStore,
    domain: DomainStore,
    engine: TranscriptionEngine,
    suggestion_provider: SuggestionProvider | None = None,
) -> bool:
    """Claim and run a single job. Returns False if the queue was empty."""
    job = jobs.claim_next()
    if job is None:
        return False

    handler = _HANDLERS.get(job.type)
    try:
        if handler is None:
            raise ValueError(f"tipo de job não suportado por este worker: {job.type}")
        if job.type == JobType.TRANSCRIPTION:
            handler(job, config, jobs, domain, engine)
        elif job.type == JobType.SUGGESTION:
            provider = suggestion_provider or build_suggestion_provider(
                config.ai, cwd=Path(__file__).resolve().parents[2]
            )
            handler(job, config, jobs, domain, provider)
        else:
            handler(job, config, jobs, domain)
    except Exception as exc:  # noqa: BLE001 - persisted as the job's failure reason
        try:
            jobs.update(job.id, JobUpdate(status=JobStatus.FAILED, error=_truncate(str(exc))))
        except InvalidJobTransitionError:
            pass
    return True


def run_worker(
    config: CortexConfig | None = None,
    *,
    poll_interval: float = 1.0,
    max_jobs: int | None = None,
    install_signal_handlers: bool = True,
) -> None:
    config = config or load_config()
    config.ensure_runtime_dirs()
    jobs = JobStore(config.paths.database)
    domain = DomainStore(config.paths.database)
    engine = FasterWhisperEngine()
    suggestion_provider = build_suggestion_provider(
        config.ai, cwd=Path(__file__).resolve().parents[2]
    )

    state = {"running": True}

    def _stop(signum: int, frame: Any) -> None:
        state["running"] = False

    if install_signal_handlers:
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

    processed = 0
    try:
        while state["running"]:
            if max_jobs is not None and processed >= max_jobs:
                break
            did_work = process_next(config, jobs, domain, engine, suggestion_provider)
            if did_work:
                processed += 1
            elif not state["running"]:
                break
            else:
                time.sleep(poll_interval)
    finally:
        engine.unload()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CorteX background worker")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--max-jobs", type=int, default=None, help="Process at most N jobs then exit")
    args = parser.parse_args(argv)
    run_worker(poll_interval=args.poll_interval, max_jobs=args.max_jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
