from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cortex.domain.models import SourceAsset, SourceKind, TranscriptArtifact  # noqa: E402
from cortex.domain.store import DomainStore, ProjectNotFoundError, SourceAssetNotFoundError  # noqa: E402


class DomainStoreTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tempdir = tempfile.TemporaryDirectory()
        self.store = DomainStore(Path(self.tempdir.name) / "domain.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_project_lifecycle(self):
        project = self.store.create_project("Podcast 01")
        self.assertEqual(self.store.get_project(project.id).name, "Podcast 01")
        self.assertIn(project.id, [p.id for p in self.store.list_projects()])
        with self.assertRaises(ProjectNotFoundError):
            self.store.get_project("does-not-exist")

    def test_source_asset_roundtrip_and_dedupe_lookup(self):
        project = self.store.create_project("Podcast 01")
        asset = self.store.create_source_asset(SourceAsset(
            project_id=project.id,
            kind=SourceKind.UPLOAD,
            original_filename="ep1.mp4",
            stored_path=f"projects/{project.id}/source/ep1.mp4",
            sha256="a" * 64,
            size_bytes=1024,
            probe={"format": {"duration": "10.0"}},
        ))
        fetched = self.store.get_source_asset(asset.id)
        self.assertEqual(fetched.sha256, "a" * 64)
        self.assertEqual(self.store.list_source_assets(project.id), [asset])
        self.assertEqual(self.store.find_source_asset_by_sha256(project.id, "a" * 64).id, asset.id)
        self.assertIsNone(self.store.find_source_asset_by_sha256(project.id, "b" * 64))
        with self.assertRaises(SourceAssetNotFoundError):
            self.store.get_source_asset("missing")

    def test_transcript_cache_lookup_matches_full_key(self):
        import tempfile

        project = self.store.create_project("Podcast 01")
        asset = self.store.create_source_asset(SourceAsset(
            project_id=project.id,
            kind=SourceKind.UPLOAD,
            stored_path="x",
            sha256="c" * 64,
            size_bytes=1,
        ))
        with tempfile.TemporaryDirectory() as directory:
            transcript_path = Path(directory) / "transcript.json"
            transcript_path.write_text("{}", encoding="utf-8")

            artifact = self.store.create_transcript_artifact(TranscriptArtifact(
                project_id=project.id,
                source_asset_id=asset.id,
                schema_version=1,
                path=str(transcript_path),
                audio_sha256="c" * 64,
                engine="faster-whisper",
                model="large-v3-turbo",
                device="cuda",
                compute_type="int8",
                language="pt",
                vad=True,
                batch_size=8,
                duration_seconds=12.3,
            ))

            hit = self.store.find_cached_transcript_artifact(
                source_asset_id=asset.id, audio_sha256="c" * 64, model="large-v3-turbo",
                device="cuda", compute_type="int8", language="pt", vad=True, schema_version=1,
            )
            self.assertEqual(hit.id, artifact.id)

            # Any field mismatch must miss the cache.
            self.assertIsNone(self.store.find_cached_transcript_artifact(
                source_asset_id=asset.id, audio_sha256="c" * 64, model="large-v3", device="cuda",
                compute_type="int8", language="pt", vad=True, schema_version=1,
            ))
            self.assertIsNone(self.store.find_cached_transcript_artifact(
                source_asset_id=asset.id, audio_sha256="different", model="large-v3-turbo",
                device="cuda", compute_type="int8", language="pt", vad=True, schema_version=1,
            ))

            # A missing artifact file on disk must also miss the cache.
            transcript_path.unlink()
            self.assertIsNone(self.store.find_cached_transcript_artifact(
                source_asset_id=asset.id, audio_sha256="c" * 64, model="large-v3-turbo",
                device="cuda", compute_type="int8", language="pt", vad=True, schema_version=1,
            ))


if __name__ == "__main__":
    unittest.main()
