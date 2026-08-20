from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cortex.config import CortexConfig, load_config  # noqa: E402
from cortex.domain.models import SourceAsset, SourceKind  # noqa: E402
from cortex.domain.store import DomainStore  # noqa: E402
from cortex.jobs import JobStore  # noqa: E402
from cortex.schemas import JobCreate, JobStatus, JobType, JobUpdate  # noqa: E402
from cortex.transcribe.engine import TranscriptionEngineError  # noqa: E402
from cortex.transcribe.service import TranscribeService  # noqa: E402
from cortex.worker import process_next, run_transcription_job  # noqa: E402

from conftest import FakeTranscriptionEngine  # noqa: E402


def _make_config(tmp_dir: Path) -> CortexConfig:
    config = load_config()
    return config.model_copy(update={
        "paths": config.paths.model_copy(update={
            "data_dir": tmp_dir,
            "projects_dir": tmp_dir / "projects",
            "cache_dir": tmp_dir / "cache",
            "output_dir": tmp_dir / "output",
            "database": tmp_dir / "cortex.sqlite3",
        }),
    })


def _setup_project_with_audio(config: CortexConfig, domain: DomainStore, wav_path: Path) -> SourceAsset:
    project = domain.create_project("Podcast de teste")
    dest_dir = config.paths.projects_dir / project.id / "source"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored = dest_dir / "tone.wav"
    stored.write_bytes(wav_path.read_bytes())

    import hashlib
    sha256 = hashlib.sha256(stored.read_bytes()).hexdigest()
    asset = domain.create_source_asset(SourceAsset(
        project_id=project.id,
        kind=SourceKind.UPLOAD,
        original_filename="tone.wav",
        stored_path=str(stored.relative_to(config.paths.data_dir)),
        sha256=sha256,
        size_bytes=stored.stat().st_size,
        probe={},
    ))
    return asset


class JobClaimRaceTests(unittest.TestCase):
    def test_claim_next_is_atomic_across_two_worker_handles(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "jobs.sqlite3"
            store_a = JobStore(db_path)
            store_b = JobStore(db_path)  # simulates a second worker process/connection

            created = [store_a.create(JobCreate(type=JobType.TRANSCRIPTION)) for _ in range(5)]

            claimed_by_a: list = []
            claimed_by_b: list = []
            barrier = threading.Barrier(2)

            def claim_loop(store, sink):
                barrier.wait()
                while True:
                    job = store.claim_next()
                    if job is None:
                        break
                    sink.append(job)

            t1 = threading.Thread(target=claim_loop, args=(store_a, claimed_by_a))
            t2 = threading.Thread(target=claim_loop, args=(store_b, claimed_by_b))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            all_claimed_ids = [job.id for job in claimed_by_a + claimed_by_b]
            self.assertEqual(sorted(all_claimed_ids), sorted(job.id for job in created))
            self.assertEqual(len(all_claimed_ids), len(set(all_claimed_ids)))  # no job claimed twice
            for job in claimed_by_a + claimed_by_b:
                self.assertEqual(job.status, JobStatus.RUNNING)

    def test_claim_next_returns_none_when_queue_empty(self):
        with tempfile.TemporaryDirectory() as d:
            store = JobStore(Path(d) / "jobs.sqlite3")
            self.assertIsNone(store.claim_next())


class JobRecoveryTests(unittest.TestCase):
    def test_recovery_does_not_requeue_a_live_worker_job(self):
        with tempfile.TemporaryDirectory() as d:
            store = JobStore(Path(d) / "jobs.sqlite3")
            created = store.create(JobCreate(type=JobType.TRANSCRIPTION))

            running = store.claim_next()
            self.assertIsNotNone(running)
            self.assertEqual(running.id, created.id)
            self.assertEqual(store.recover_abandoned_jobs(), [])
            self.assertEqual(store.get(created.id).status, JobStatus.RUNNING)

    def test_recover_abandoned_job_is_claimable_by_new_store_instance(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "jobs.sqlite3"
            original_store = JobStore(db_path)
            created = original_store.create(JobCreate(
                type=JobType.TRANSCRIPTION,
                payload={"source_asset_id": "source-1"},
            ))
            running = original_store.claim_next(worker_pid=999_999_999)
            assert running is not None
            running = original_store.update(created.id, JobUpdate(progress=42.0))

            restarted_store = JobStore(db_path)
            recovered = restarted_store.recover_abandoned_jobs()

            self.assertEqual([job.id for job in recovered], [created.id])
            self.assertEqual(recovered[0].status, JobStatus.QUEUED)
            self.assertEqual(recovered[0].payload, created.payload)
            self.assertEqual(recovered[0].progress, 42.0)
            self.assertIn("Recuperado", recovered[0].message)
            claimed = restarted_store.claim_next()
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.id, created.id)
            self.assertEqual(claimed.status, JobStatus.RUNNING)

    def test_recovery_of_twenty_five_jobs_preserves_fifo_without_duplicate_claims(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "jobs.sqlite3"
            store = JobStore(db_path)
            created = [store.create(JobCreate(type=JobType.TRANSCRIPTION)) for _ in range(25)]
            initially_claimed = [store.claim_next(worker_pid=999_999_999) for _ in created]
            self.assertEqual([job.id for job in initially_claimed if job], [job.id for job in created])

            restarted_store = JobStore(db_path)
            recovered = restarted_store.recover_abandoned_jobs()
            claimed = [restarted_store.claim_next() for _ in created]

            self.assertEqual([job.id for job in recovered], [job.id for job in created])
            claimed_ids = [job.id for job in claimed if job]
            self.assertEqual(claimed_ids, [job.id for job in created])
            self.assertEqual(len(claimed_ids), len(set(claimed_ids)))
            self.assertIsNone(restarted_store.claim_next())


class TranscribeServiceCacheTests(unittest.TestCase):
    def test_second_transcription_reuses_cached_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            tmp_dir = Path(d)
            config = _make_config(tmp_dir)
            config.ensure_runtime_dirs()
            domain = DomainStore(config.paths.database)

            import subprocess
            wav = tmp_dir / "src.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(wav)],
                capture_output=True, check=True, timeout=30,
            )
            asset = _setup_project_with_audio(config, domain, wav)

            engine = FakeTranscriptionEngine()
            service = TranscribeService(config, domain, engine)
            progress_events: list[tuple[float, str]] = []

            first = service.run(
                source_asset=asset, overrides={},
                progress_cb=lambda p, m: progress_events.append((p, m)),
                should_cancel=lambda: False,
            )
            self.assertFalse(first["cached"])
            self.assertEqual(len(engine.calls), 1)
            # progress must be monotonically non-decreasing
            values = [p for p, _ in progress_events]
            self.assertEqual(values, sorted(values))
            self.assertEqual(progress_events[-1][0], 100.0)

            second = service.run(
                source_asset=asset, overrides={},
                progress_cb=lambda p, m: progress_events.append((p, m)),
                should_cancel=lambda: False,
            )
            self.assertTrue(second["cached"])
            self.assertEqual(second["transcript_artifact_id"], first["transcript_artifact_id"])
            self.assertEqual(len(engine.calls), 1)  # engine not invoked again


class WorkerCudaFailHardTests(unittest.TestCase):
    def _prepare(self, tmp_dir: Path, allow_cpu_fallback: bool):
        config = _make_config(tmp_dir)
        config = config.model_copy(update={
            "transcription": config.transcription.model_copy(update={
                "device": "cuda", "allow_cpu_fallback": allow_cpu_fallback,
            }),
        })
        config.ensure_runtime_dirs()
        jobs = JobStore(config.paths.database)
        domain = DomainStore(config.paths.database)

        import subprocess
        wav = tmp_dir / "src.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(wav)],
            capture_output=True, check=True, timeout=30,
        )
        asset = _setup_project_with_audio(config, domain, wav)
        job = jobs.create(JobCreate(
            type=JobType.TRANSCRIPTION, project_id=asset.project_id,
            payload={"source_asset_id": asset.id},
        ))
        return config, jobs, domain, job

    def test_cuda_failure_without_fallback_fails_the_job(self):
        with tempfile.TemporaryDirectory() as d:
            config, jobs, domain, job = self._prepare(Path(d), allow_cpu_fallback=False)
            engine = FakeTranscriptionEngine(fail_on_cuda=True)
            claimed = jobs.claim_next()
            self.assertEqual(claimed.id, job.id)
            with self.assertRaises(TranscriptionEngineError):
                run_transcription_job(claimed, config, jobs, domain, engine)
            # worker.process_next is what actually persists the failure
            final = jobs.get(job.id)
            self.assertEqual(final.status, JobStatus.RUNNING)  # not yet marked by run_transcription_job itself

    def test_cuda_failure_without_fallback_via_process_next_marks_job_failed(self):
        with tempfile.TemporaryDirectory() as d:
            config, jobs, domain, job = self._prepare(Path(d), allow_cpu_fallback=False)
            engine = FakeTranscriptionEngine(fail_on_cuda=True)
            processed = process_next(config, jobs, domain, engine)
            self.assertTrue(processed)
            final = jobs.get(job.id)
            self.assertEqual(final.status, JobStatus.FAILED)
            self.assertIsNotNone(final.error)
            self.assertIn("CUDA", final.error)

    def test_cuda_failure_with_fallback_succeeds_as_cpu(self):
        with tempfile.TemporaryDirectory() as d:
            config, jobs, domain, job = self._prepare(Path(d), allow_cpu_fallback=True)
            engine = FakeTranscriptionEngine(fail_on_cuda=True)
            processed = process_next(config, jobs, domain, engine)
            self.assertTrue(processed)
            final = jobs.get(job.id)
            self.assertEqual(final.status, JobStatus.SUCCEEDED)
            self.assertEqual(final.result["engine"]["effective_device"], "cpu")
            self.assertEqual(final.result["engine"]["requested_device"], "cuda")
            self.assertTrue(final.result["engine"]["fallback"])


class WorkerCooperativeCancellationTests(unittest.TestCase):
    def test_cancellation_mid_transcription_leaves_job_cancelled_not_corrupted(self):
        with tempfile.TemporaryDirectory() as d:
            tmp_dir = Path(d)
            config = _make_config(tmp_dir)
            config.ensure_runtime_dirs()
            jobs = JobStore(config.paths.database)
            domain = DomainStore(config.paths.database)

            import subprocess
            wav = tmp_dir / "src.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(wav)],
                capture_output=True, check=True, timeout=30,
            )
            asset = _setup_project_with_audio(config, domain, wav)
            job = jobs.create(JobCreate(
                type=JobType.TRANSCRIPTION, project_id=asset.project_id,
                payload={"source_asset_id": asset.id},
            ))

            # Cancel the job cooperatively after the first progress tick, the
            # way the API's cancel endpoint would (job store transition, not
            # a hard kill of the worker process).
            engine = FakeTranscriptionEngine(progress_steps=5)
            calls = {"n": 0}
            real_transcribe = engine.transcribe

            def transcribe_and_cancel_after_first_tick(*args, **kwargs):
                original_progress_cb = kwargs.get("progress_cb")

                def spy(fraction):
                    calls["n"] += 1
                    if calls["n"] == 1:
                        jobs.cancel(job.id)
                    if original_progress_cb:
                        original_progress_cb(fraction)

                kwargs["progress_cb"] = spy
                return real_transcribe(*args, **kwargs)

            engine.transcribe = transcribe_and_cancel_after_first_tick

            processed = process_next(config, jobs, domain, engine)
            self.assertTrue(processed)
            final = jobs.get(job.id)
            self.assertEqual(final.status, JobStatus.CANCELLED)
            self.assertIsNone(final.error)
            # no transcript artifact should have been persisted for a cancelled run
            self.assertEqual(domain.list_transcript_artifacts(asset.id), [])


class YoutubeJobTypeTests(unittest.TestCase):
    def test_ingest_youtube_job_type_is_available(self):
        self.assertEqual(JobType.INGEST_YOUTUBE.value, "ingest_youtube")
        self.assertEqual(JobType.TRANSCRIPTION.value, "transcription")


if __name__ == "__main__":
    unittest.main()
