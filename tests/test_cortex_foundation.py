import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cortex.config import load_config  # noqa: E402
from cortex.hardware import collect_hardware_snapshot  # noqa: E402
from cortex.jobs import InvalidJobTransitionError, JobStore  # noqa: E402
from cortex.schemas import JobCreate, JobStatus, JobType, JobUpdate, PipelineStage  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_yaml_and_environment_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("clips:\n  count: 3\n", encoding="utf-8")
            with patch.dict(os.environ, {"CORTEX__CLIPS__COUNT": "12"}):
                config = load_config(path)
            self.assertEqual(config.clips.count, 12)
            self.assertEqual(config.transcription.model, "large-v3-turbo")

    def test_rejects_invalid_duration_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                "clips:\n  minimum_seconds: 90\n  maximum_seconds: 60\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(path)


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tempdir.name) / "jobs.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_job_lifecycle_is_persisted(self):
        job = self.store.create(JobCreate(type=JobType.TRANSCRIPTION))
        running = self.store.update(job.id, JobUpdate(
            status=JobStatus.RUNNING,
            stage=PipelineStage.TRANSCRIBE,
            progress=42,
        ))
        complete = self.store.update(job.id, JobUpdate(status=JobStatus.SUCCEEDED))
        self.assertEqual(running.progress, 42)
        self.assertEqual(complete.progress, 100)
        self.assertEqual(self.store.get(job.id).status, JobStatus.SUCCEEDED)

    def test_progress_cannot_regress(self):
        job = self.store.create(JobCreate(type=JobType.TRANSCRIPTION))
        self.store.update(job.id, JobUpdate(status=JobStatus.RUNNING, progress=50))
        with self.assertRaises(InvalidJobTransitionError):
            self.store.update(job.id, JobUpdate(progress=25))


class FakeNvml:
    @staticmethod
    def nvmlInit(): pass

    @staticmethod
    def nvmlShutdown(): pass

    @staticmethod
    def nvmlDeviceGetCount(): return 0


class HardwareTests(unittest.TestCase):
    def test_no_gpu_is_reported_explicitly(self):
        snapshot = collect_hardware_snapshot(FakeNvml)
        self.assertEqual(snapshot.gpus[0].state, "unavailable")
        self.assertGreaterEqual(snapshot.cpu.logical_cores, 1)


if __name__ == "__main__":
    unittest.main()
