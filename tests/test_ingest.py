from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cortex.ingest.ffprobe import FFprobeError, duration_seconds, probe_media  # noqa: E402
from cortex.ingest.filenames import sanitize_filename, unique_destination  # noqa: E402
from cortex.ingest.normalize import extract_normalized_audio  # noqa: E402
from cortex.ingest.upload import UploadValidationError, store_upload_stream  # noqa: E402
from cortex.ingest.youtube import (  # noqa: E402
    build_ydl_options,
    download_youtube_source,
    find_cached_download,
    progress_hook_to_fraction,
)


class FilenameSanitizeTests(unittest.TestCase):
    def test_strips_directories_and_traversal(self):
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("/abs/path/video.mp4"), "video.mp4")
        self.assertEqual(sanitize_filename("..\\..\\windows\\evil.exe"), "evil.exe")

    def test_strips_exotic_whitespace_and_unsafe_chars(self):
        result = sanitize_filename("meu áudio  (final)!.mp3")
        self.assertNotIn(" ", result)
        self.assertNotIn("(", result)
        self.assertTrue(result.endswith(".mp3"))

    def test_empty_or_nul_only_falls_back_to_default(self):
        self.assertEqual(sanitize_filename("\x00"), "file")
        self.assertEqual(sanitize_filename(""), "file")

    def test_unique_destination_prefixes_on_collision(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            (directory / "clip.mp4").write_bytes(b"x")
            dest = unique_destination(directory, "clip.mp4")
            self.assertNotEqual(dest.name, "clip.mp4")
            self.assertTrue(dest.name.endswith("-clip.mp4"))


class StreamingUploadTests(unittest.TestCase):
    class _AsyncBytes:
        def __init__(self, payload: bytes, chunk: int = 7):
            self._payload = payload
            self._chunk = chunk
            self._offset = 0

        async def read(self, size: int) -> bytes:
            end = min(len(self._payload), self._offset + min(size, self._chunk))
            data = self._payload[self._offset:end]
            self._offset = end
            return data

    def test_streams_hashes_and_never_buffers_whole_file(self):
        import hashlib
        import tempfile

        payload = b"fake-mp4-bytes" * 10_000  # > _CHUNK_SIZE boundary crossed many times
        expected_sha = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as d:
            destination_dir = Path(d) / "source"
            result = asyncio.run(store_upload_stream(
                self._AsyncBytes(payload),
                "clipe final.mp4",
                destination_dir=destination_dir,
                allowed_extensions={"mp4", "mov"},
                max_bytes=10 * 1024 * 1024,
            ))
            self.assertEqual(result.sha256, expected_sha)
            self.assertEqual(result.size_bytes, len(payload))
            self.assertTrue(result.path.exists())
            self.assertEqual(result.path.read_bytes(), payload)

    def test_rejects_disallowed_extension(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(UploadValidationError):
                asyncio.run(store_upload_stream(
                    self._AsyncBytes(b"data"),
                    "malware.exe",
                    destination_dir=Path(d) / "source",
                    allowed_extensions={"mp4"},
                    max_bytes=1024,
                ))

    def test_rejects_and_cleans_up_when_over_limit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            destination_dir = Path(d) / "source"
            with self.assertRaises(UploadValidationError):
                asyncio.run(store_upload_stream(
                    self._AsyncBytes(b"x" * 100),
                    "big.mp4",
                    destination_dir=destination_dir,
                    allowed_extensions={"mp4"},
                    max_bytes=10,
                ))
            leftover = list(destination_dir.glob("*")) if destination_dir.exists() else []
            self.assertEqual(leftover, [])


class FfprobeTests(unittest.TestCase):
    def test_probe_real_media_reports_duration(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "tone.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(wav)],
                capture_output=True, check=True, timeout=30,
            )
            probe = probe_media("ffprobe", wav)
            self.assertAlmostEqual(duration_seconds(probe), 1.0, delta=0.2)

    def test_probe_missing_file_raises(self):
        with self.assertRaises(FFprobeError):
            probe_media("ffprobe", "/nonexistent/path/does-not-exist.mp4")


class NormalizeAudioTests(unittest.TestCase):
    def test_extracts_mono_16k_wav_and_caches_by_hash(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "clip.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=5:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(source),
                ],
                capture_output=True, check=True, timeout=30,
            )
            cache_dir = Path(d) / "cache"
            first = extract_normalized_audio(
                "ffmpeg", "ffprobe", source, source_sha256="abc123def456", cache_dir=cache_dir,
            )
            self.assertFalse(first.cached)
            self.assertTrue(first.path.exists())
            self.assertGreater(first.duration_seconds, 0)
            self.assertEqual(first.path.name, "audio-abc123def456.wav")

            second = extract_normalized_audio(
                "ffmpeg", "ffprobe", source, source_sha256="abc123def456", cache_dir=cache_dir,
            )
            self.assertTrue(second.cached)
            self.assertEqual(second.path, first.path)


class FakeYoutubeDownloader:
    def __init__(self, video_id: str = "abc123XYZ_", title: str = "Episódio de teste"):
        self.video_id = video_id
        self.title = title
        self.extract_calls: list[bool] = []

    def extract_info(self, url, *, download, options):
        self.extract_calls.append(download)
        if download:
            outtmpl = Path(options["outtmpl"].replace("%(id)s", self.video_id).replace("%(ext)s", "mp4"))
            outtmpl.parent.mkdir(parents=True, exist_ok=True)
            outtmpl.write_bytes(b"fake-downloaded-video")
        return {"id": self.video_id, "title": self.title}


class YoutubeIngestTests(unittest.TestCase):
    def test_build_ydl_options_uses_configured_format(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            options = build_ydl_options("bestvideo+bestaudio/best", Path(d))
            self.assertEqual(options["format"], "bestvideo+bestaudio/best")
            self.assertTrue(options["noplaylist"])
            self.assertIn("node", options["js_runtimes"])
            self.assertFalse(options["no_warnings"])

    def test_cache_does_not_accept_partial_tracks_or_metadata(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name in ("video.f137.mp4", "video.f140.m4a", "video.info.json", "video.mp4.part"):
                (path / name).write_bytes(b"not a merged video")
            self.assertIsNone(find_cached_download(path, "video"))
            (path / "video.mp4").write_bytes(b"merged video")
            self.assertEqual(find_cached_download(path, "video"), path / "video.mp4")

    def test_progress_hook_to_fraction_computes_ratio(self):
        self.assertAlmostEqual(
            progress_hook_to_fraction({"status": "downloading", "total_bytes": 200, "downloaded_bytes": 50}),
            0.25,
        )
        self.assertIsNone(progress_hook_to_fraction({"status": "finished"}))

    def test_download_without_network_uses_fake_downloader_and_caches(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "source"
            downloader = FakeYoutubeDownloader()
            progressed: list[float] = []
            result = download_youtube_source(
                "https://youtu.be/abc123XYZ_",
                dest,
                youtube_format="best",
                downloader=downloader,
                progress_cb=progressed.append,
            )
            self.assertFalse(result.cached)
            self.assertTrue(result.path.exists())
            self.assertEqual(downloader.extract_calls, [False, True])

            # Second call finds the cached file for the same project dir and
            # never triggers a real download pass.
            downloader2 = FakeYoutubeDownloader()
            cached_path = find_cached_download(dest, "abc123XYZ_")
            self.assertIsNotNone(cached_path)
            result2 = download_youtube_source(
                "https://youtu.be/abc123XYZ_",
                dest,
                youtube_format="best",
                downloader=downloader2,
            )
            self.assertTrue(result2.cached)
            self.assertEqual(downloader2.extract_calls, [False])  # only the metadata probe, no download


if __name__ == "__main__":
    unittest.main()
