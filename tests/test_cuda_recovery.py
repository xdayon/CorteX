from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

from cortex import worker
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import JobCreate, JobStatus, JobType
from cortex.transcribe.engine import (
    CudaContextLostError,
    FasterWhisperEngine,
    TranscriptionCancelled,
    _is_cuda_runtime_error,
)

from test_transcribe_worker import _make_config

CUDA_ERROR = "parallel_for failed: cudaErrorInvalidDevice: invalid device ordinal"


@pytest.mark.parametrize("message", [
    CUDA_ERROR,
    "CUDA_ERROR_NO_DEVICE",
    "CUDA failed with error unknown error",
    "Invalid Device Ordinal",
])
def test_is_cuda_runtime_error_positive(message):
    assert _is_cuda_runtime_error(RuntimeError(message))


def test_is_cuda_runtime_error_ignores_missing_library():
    assert not _is_cuda_runtime_error(RuntimeError("Library libcublas.so.12 is not found"))


class _Segment:
    start, end, text = 0.0, 1.0, "oi"
    avg_logprob, no_speech_prob, words = -0.1, 0.0, []


def _install_fake_faster_whisper(monkeypatch, outcomes):
    """Each WhisperModel.transcribe call consumes one outcome: an exception raised
    lazily while iterating, or None for success."""
    state = {"loads": 0}

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            state["loads"] += 1

        def transcribe(self, path, **kwargs):
            outcome = outcomes.pop(0)

            def gen():
                if outcome is not None:
                    raise outcome
                yield _Segment()

            return gen(), types.SimpleNamespace(duration=1.0, language="pt", language_probability=0.9)

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = WhisperModel
    module.BatchedInferencePipeline = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return state


def _transcribe(engine):
    return engine.transcribe(
        Path("x.wav"), model="m", language="pt", device="cuda", compute_type="float16",
        batch_size=0, vad=True, allow_cpu_fallback=False,
    )


def test_engine_retries_once_after_cuda_runtime_error(monkeypatch):
    state = _install_fake_faster_whisper(monkeypatch, [RuntimeError(CUDA_ERROR), None])
    result = _transcribe(FasterWhisperEngine())
    assert [segment.text for segment in result.segments] == ["oi"]
    assert state["loads"] == 2


def test_engine_raises_cuda_context_lost_after_two_failures(monkeypatch):
    _install_fake_faster_whisper(
        monkeypatch, [RuntimeError(CUDA_ERROR), RuntimeError(CUDA_ERROR)],
    )
    with pytest.raises(CudaContextLostError, match="cudaErrorInvalidDevice"):
        _transcribe(FasterWhisperEngine())


def test_engine_does_not_retry_other_errors_or_cancellation(monkeypatch):
    state = _install_fake_faster_whisper(monkeypatch, [RuntimeError("outra falha")])
    with pytest.raises(RuntimeError, match="outra falha"):
        _transcribe(FasterWhisperEngine())
    assert state["loads"] == 1

    state = _install_fake_faster_whisper(monkeypatch, [None])
    with pytest.raises(TranscriptionCancelled):
        FasterWhisperEngine().transcribe(
            Path("x.wav"), model="m", language="pt", device="cuda", compute_type="float16",
            batch_size=0, vad=True, allow_cpu_fallback=False, should_cancel=lambda: True,
        )
    assert state["loads"] == 1


def _run_lost_gpu_job(tmp_dir, monkeypatch, exits):
    config = _make_config(tmp_dir)
    config.ensure_runtime_dirs()
    jobs = JobStore(config.paths.database)
    domain = DomainStore(config.paths.database)

    def handler(job, config, jobs, domain, engine):
        raise CudaContextLostError("falha original")

    monkeypatch.setitem(worker._HANDLERS, JobType.TRANSCRIPTION, handler)
    monkeypatch.setattr(worker, "_exit_process", exits.append)
    return config, jobs, domain


def test_worker_restarts_on_first_cuda_loss_then_fails_on_second(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        exits: list[int] = []
        config, jobs, domain = _run_lost_gpu_job(tmp_dir, monkeypatch, exits)
        job = jobs.create(JobCreate(type=JobType.TRANSCRIPTION))
        marker_path = tmp_dir / "cuda-recovery.json"

        assert worker.process_next(config, jobs, domain, engine=None)
        assert exits == [75]
        assert json.loads(marker_path.read_text()) == {"job_id": job.id, "attempts": 1}
        stuck = jobs.get(job.id)
        assert stuck.status == JobStatus.RUNNING
        assert "reiniciando o worker" in stuck.message

        # Simulated restart: the same job is requeued and runs again.
        monkeypatch.setattr("cortex.jobs._pid_is_alive", lambda pid: False)
        assert [recovered.id for recovered in jobs.recover_abandoned_jobs()] == [job.id]
        assert worker.process_next(config, jobs, domain, engine=None)
        assert exits == [75]
        final = jobs.get(job.id)
        assert final.status == JobStatus.FAILED
        assert "A GPU continuou indisponível" in final.error
        assert "falha original" in final.error
        assert not marker_path.exists()
