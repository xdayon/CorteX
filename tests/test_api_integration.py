from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from cortex.api import create_app  # noqa: E402
from cortex.config import load_config  # noqa: E402
from cortex.domain.store import DomainStore  # noqa: E402
from cortex.jobs import JobStore  # noqa: E402
from cortex.schemas import JobStatus  # noqa: E402
from cortex.worker import process_next  # noqa: E402

from conftest import FakeTranscriptionEngine  # noqa: E402


class UploadIngestTranscribeIntegrationTests(unittest.TestCase):
    """End-to-end: real upload streaming, real ffprobe, real audio
    normalization, transcription via an injected fake engine (no GPU/network
    required), through to a persisted transcript.json + TranscriptArtifact.
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        tmp_dir = Path(self._tmp.name)
        base_config = load_config()
        self.config = base_config.model_copy(update={
            "paths": base_config.paths.model_copy(update={
                "data_dir": tmp_dir,
                "projects_dir": tmp_dir / "projects",
                "cache_dir": tmp_dir / "cache",
                "output_dir": tmp_dir / "output",
                "database": tmp_dir / "cortex.sqlite3",
            }),
        })
        self.app = create_app(self.config)
        self.client = TestClient(self.app)

    def tearDown(self):
        self._tmp.cleanup()

    def _generate_media(self) -> Path:
        import subprocess
        media_dir = Path(self._tmp.name) / "fixtures"
        media_dir.mkdir(parents=True, exist_ok=True)
        clip = media_dir / "podcast-trecho.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                str(clip),
            ],
            capture_output=True, check=True, timeout=30,
        )
        return clip

    def test_full_flow_upload_transcribe_via_fake_engine(self):
        create_resp = self.client.post("/api/v1/projects", json={"name": "Episódio 42"})
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        project = create_resp.json()

        clip_path = self._generate_media()
        with clip_path.open("rb") as fh:
            upload_resp = self.client.post(
                f"/api/v1/projects/{project['id']}/sources/upload",
                files={"file": ("podcast trecho final!.mp4", fh, "video/mp4")},
            )
        self.assertEqual(upload_resp.status_code, 201, upload_resp.text)
        asset = upload_resp.json()
        self.assertEqual(asset["kind"], "upload")
        self.assertTrue(asset["sha256"])
        self.assertIn("format", asset["probe"])
        self.assertGreater(asset["size_bytes"], 0)
        # filename was sanitized: no spaces/exotic punctuation, path preserved under data/
        self.assertNotIn(" ", Path(asset["stored_path"]).name)
        stored_abs = self.config.paths.data_dir / asset["stored_path"]
        self.assertTrue(stored_abs.exists())

        transcribe_resp = self.client.post(
            f"/api/v1/projects/{project['id']}/transcribe",
            json={"source_asset_id": asset["id"]},
        )
        self.assertEqual(transcribe_resp.status_code, 201, transcribe_resp.text)
        job = transcribe_resp.json()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["type"], "transcription")

        # Simulate the separate worker process: same SQLite database, fake
        # engine so no GPU/model download is needed in tests.
        jobs = JobStore(self.config.paths.database)
        domain = DomainStore(self.config.paths.database)
        engine = FakeTranscriptionEngine()
        processed = process_next(self.config, jobs, domain, engine)
        self.assertTrue(processed)

        final_resp = self.client.get(f"/api/v1/jobs/{job['id']}")
        self.assertEqual(final_resp.status_code, 200)
        final_job = final_resp.json()
        self.assertEqual(final_job["status"], JobStatus.SUCCEEDED.value)
        self.assertEqual(final_job["progress"], 100.0)
        self.assertFalse(final_job["result"]["cached"])

        artifacts = domain.list_transcript_artifacts(asset["id"])
        self.assertEqual(len(artifacts), 1)
        transcript_path = Path(artifacts[0].path)
        self.assertTrue(transcript_path.exists())

        document = json.loads(transcript_path.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], 1)
        self.assertIn("language", document)
        self.assertIn("duration_seconds", document)
        self.assertIn("engine", document)
        for key in (
            "name", "model", "requested_device", "effective_device",
            "requested_compute_type", "effective_compute_type", "batch_size", "vad",
        ):
            self.assertIn(key, document["engine"])
        self.assertGreaterEqual(len(document["segments"]), 1)
        first_segment = document["segments"][0]
        for key in ("id", "start", "end", "text", "avg_logprob", "no_speech_prob", "words"):
            self.assertIn(key, first_segment)

        # Re-running transcribe for the same asset/config must hit the cache
        # instead of invoking the engine again.
        transcribe_resp_2 = self.client.post(
            f"/api/v1/projects/{project['id']}/transcribe",
            json={"source_asset_id": asset["id"]},
        )
        job2 = transcribe_resp_2.json()
        engine2 = FakeTranscriptionEngine()
        process_next(self.config, jobs, domain, engine2)
        final_job2 = self.client.get(f"/api/v1/jobs/{job2['id']}").json()
        self.assertEqual(final_job2["status"], JobStatus.SUCCEEDED.value)
        self.assertTrue(final_job2["result"]["cached"])
        self.assertEqual(engine2.calls, [])  # cache hit, engine never invoked

    def test_upload_rejects_disallowed_extension_and_leaves_no_file(self):
        create_resp = self.client.post("/api/v1/projects", json={"name": "Projeto"})
        project = create_resp.json()

        payload = b"not-a-real-video"
        upload_resp = self.client.post(
            f"/api/v1/projects/{project['id']}/sources/upload",
            files={"file": ("malware.exe", payload, "application/octet-stream")},
        )
        self.assertEqual(upload_resp.status_code, 422)
        source_dir = self.config.paths.projects_dir / project["id"] / "source"
        leftovers = list(source_dir.glob("*")) if source_dir.exists() else []
        self.assertEqual(leftovers, [])

    def test_upload_to_missing_project_returns_404(self):
        upload_resp = self.client.post(
            "/api/v1/projects/does-not-exist/sources/upload",
            files={"file": ("clip.mp4", b"data", "video/mp4")},
        )
        self.assertEqual(upload_resp.status_code, 404)

    def test_transcribe_unknown_source_asset_returns_404(self):
        create_resp = self.client.post("/api/v1/projects", json={"name": "Projeto"})
        project = create_resp.json()
        resp = self.client.post(
            f"/api/v1/projects/{project['id']}/transcribe",
            json={"source_asset_id": "does-not-exist"},
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
