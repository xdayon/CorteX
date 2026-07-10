import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest


BACKEND = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "mods", "runtime", "backend")
)
sys.path.insert(0, BACKEND)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class VideoTransitionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        media_probe = types.ModuleType("services.media_probe")
        media_probe.FFMPEG_TIMEOUT = 60

        def fallback(base, suffix, output, label=None):
            cmd = [*base, "-c:v", "libx264", "-preset", "ultrafast", *suffix, output]
            subprocess.run(cmd, check=True, capture_output=True)
            return output

        media_probe.run_ffmpeg_with_fallback = fallback
        sys.modules["services.media_probe"] = media_probe

        encoder = types.ModuleType("services.encoder")
        encoder.get_video_decode_flags = lambda: []
        sys.modules["services.encoder"] = encoder

        proc = types.ModuleType("utils.proc")

        class Result:
            def __init__(self, value):
                self.returncode = value.returncode
                self.stdout = value.stdout
                self.stderr = value.stderr

        def run(cmd, timeout=None, check=False):
            value = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if check and value.returncode:
                raise RuntimeError(value.stderr)
            return Result(value)

        proc.run = run
        sys.modules["utils.proc"] = proc

        from services.video_cut import cut_multi_segment
        from services.edit_quality import validate_render_file
        from services.hook_overlay import overlay_hook

        cls.cut_multi_segment = staticmethod(cut_multi_segment)
        cls.validate_render_file = staticmethod(validate_render_file)
        cls.overlay_hook = staticmethod(overlay_hook)

    def test_audio_and_video_crossfade_keep_expected_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "source.mp4")
            output = os.path.join(tmp, "joined.mp4")
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", source,
                ],
                check=True,
            )
            segments = [
                {
                    "start": 0.0,
                    "end": 1.4,
                    "transition": {"duration": 0.06},
                },
                {"start": 2.2, "end": 3.8},
            ]
            self.cut_multi_segment(source, output, segments)
            probe = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries",
                    "format=duration:stream=codec_type", "-of", "json", output,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            data = json.loads(probe.stdout)
            self.assertEqual({s["codec_type"] for s in data["streams"]}, {"audio", "video"})
            self.assertAlmostEqual(float(data["format"]["duration"]), 2.94, delta=0.15)
            gate = self.validate_render_file(output, expected_duration=2.94)
            self.assertTrue(gate["passed"], gate)

            hooked = os.path.join(tmp, "hooked.mp4")
            self.overlay_hook(
                output,
                hooked,
                "A pergunta que muda tudo",
                target_dims=(320, 180),
                duration=1.6,
            )
            hook_gate = self.validate_render_file(hooked, expected_duration=2.94)
            self.assertTrue(hook_gate["passed"], hook_gate)


if __name__ == "__main__":
    unittest.main()
