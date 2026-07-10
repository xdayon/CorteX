import os
import sys
import unittest


BACKEND = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "mods", "runtime", "backend")
)
sys.path.insert(0, BACKEND)

from services.audio_editing import (  # noqa: E402
    EnergyTrack,
    PROFILES,
    plan_safe_segments,
    protect_segment_boundaries,
    resolve_profile,
    timeline_duration,
)
from services.edit_quality import validate_edit_plan  # noqa: E402
from services.hook_overlay import write_hook_ass  # noqa: E402


def word(text, start, end):
    return {"word": text, "start": start, "end": end, "confidence": 0.99}


class ProfessionalEditingTests(unittest.TestCase):
    def test_balanced_profile_does_not_cut_subsecond_pause(self):
        words = [word("uma", 0.2, 0.45), word("ideia", 1.25, 1.55), word("forte", 1.6, 1.9)]
        segments, diagnostics = plan_safe_segments(
            words, 0.0, 2.2, PROFILES["balanced"]
        )
        self.assertEqual(len(segments), 1)
        self.assertEqual(diagnostics["cuts"], 0)

    def test_waveform_moves_join_into_quiet_region_and_keeps_handles(self):
        words = [
            word("Isso", 0.20, 0.48),
            word("muda", 0.52, 0.80),
            word("tudo", 2.70, 3.02),
            word("agora", 3.08, 3.42),
        ]
        rms = [0.35] * 220
        for i in range(48, 134):
            rms[i] = 0.002
        energy = EnergyTrack(origin=0.0, frame_seconds=0.02, rms=rms)
        segments, diagnostics = plan_safe_segments(
            words, 0.0, 3.7, PROFILES["balanced"], energy
        )
        self.assertEqual(len(segments), 2)
        self.assertGreater(segments[0]["end"], 0.80)
        self.assertLess(segments[1]["start"], 2.70)
        self.assertEqual(diagnostics["cuts"], 1)
        self.assertTrue(segments[0]["transition"]["duration"] > 0)

    def test_rhetorical_pause_is_preserved(self):
        words = [
            word("E", 0.1, 0.2),
            word("acabou.", 0.25, 0.65),
            word("Depois", 2.05, 2.35),
            word("voltou", 2.4, 2.7),
        ]
        segments, _ = plan_safe_segments(
            words, 0.0, 3.0, PROFILES["balanced"]
        )
        self.assertEqual(len(segments), 1)

    def test_vad_region_blocks_a_cut_when_no_safe_boundary_exists(self):
        words = [
            word("primeiro", 0.1, 0.5),
            word("ponto", 0.55, 0.8),
            word("segundo", 2.4, 2.8),
        ]
        speech = [{"start": 0.0, "end": 3.0}]
        segments, _ = plan_safe_segments(
            words,
            0.0,
            3.0,
            PROFILES["dynamic"],
            speech_intervals=speech,
        )
        self.assertEqual(len(segments), 1)

    def test_boundary_protection_extends_a_segment_cut_inside_words(self):
        words = [word("inteira", 1.0, 1.5), word("frase", 1.55, 1.95)]
        protected = protect_segment_boundaries(
            [{"start": 1.2, "end": 1.8}], words, PROFILES["balanced"]
        )
        self.assertLessEqual(protected[0]["start"], 0.88)
        self.assertGreaterEqual(protected[0]["end"], 2.16)
        gate = validate_edit_plan(protected, words, PROFILES["balanced"], "Uma frase forte")
        self.assertTrue(gate["passed"])

    def test_timeline_duration_accounts_for_crossfades(self):
        segments = [
            {"start": 0, "end": 5, "transition": {"duration": 0.06}},
            {"start": 10, "end": 15, "transition": {"duration": 0.08}},
            {"start": 20, "end": 25},
        ]
        self.assertAlmostEqual(timeline_duration(segments), 14.86, places=2)

    def test_auto_profile_respects_delivery_speed(self):
        fast = [word(str(i), i * 0.2, i * 0.2 + 0.12) for i in range(30)]
        slow = [word(str(i), i * 0.7, i * 0.7 + 0.2) for i in range(8)]
        self.assertEqual(resolve_profile("auto", fast, 0, 6).name, "dynamic")
        self.assertEqual(resolve_profile("auto", slow, 0, 6).name, "contemplative")


class HookOverlayTests(unittest.TestCase):
    def test_hook_ass_is_short_safe_and_fades(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as tmp:
            path = tmp.name
        try:
            write_hook_ass(
                "A verdade começa onde o medo termina e ninguém percebe isso",
                path,
            )
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn(r"\fad(140,220)", content)
            self.assertIn(r"\N", content)
            self.assertIn("Alignment", content)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
