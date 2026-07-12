from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cortex.config import load_config  # noqa: E402


class IngestConfigTests(unittest.TestCase):
    def test_defaults_from_repo_config(self):
        config = load_config()
        self.assertEqual(config.ingest.max_upload_bytes, 10_737_418_240)
        self.assertIn("mp4", config.ingest.allowed_extensions)
        self.assertIn("wav", config.ingest.allowed_extensions)
        self.assertTrue(config.ingest.youtube_format)

    def test_ingest_block_overridable_and_rejects_unknown_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                "ingest:\n  max_upload_bytes: 1024\n  allowed_extensions: [mp3]\n",
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertEqual(config.ingest.max_upload_bytes, 1024)
            self.assertEqual(config.ingest.allowed_extensions, ["mp3"])

    def test_ingest_extra_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("ingest:\n  unknown_field: 1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
