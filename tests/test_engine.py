"""Tes ZOI Engine V4.1 (baseline) dan V4.2 pada transkrip contoh."""
import unittest

from shorts_generator import zoi_engine, zoi_engine_v4_2

from tests.helpers import sample_transcript


def _overlap(a, b):
    shared = max(
        0, min(a["end_time"], b["end_time"]) - max(a["start_time"], b["start_time"])
    )
    return shared / min(a["duration"], b["duration"])


class EngineContract:
    """Aturan yang harus dipenuhi kedua mesin."""

    engine = None

    @classmethod
    def setUpClass(cls):
        cls.transcript = sample_transcript()
        cls.highlights = cls.engine.analyze_transcript(
            cls.transcript, 5
        )["highlights"]

    def test_returns_up_to_five_candidates(self):
        self.assertGreaterEqual(len(self.highlights), 3)
        self.assertLessEqual(len(self.highlights), 5)

    def test_required_fields(self):
        for item in self.highlights:
            for key in (
                "title", "start_time", "end_time", "duration", "score",
                "hook_sentence", "virality_reason",
            ):
                self.assertIn(key, item)
            self.assertTrue(item["title"])

    def test_duration_between_30_and_90_seconds(self):
        for item in self.highlights:
            self.assertGreaterEqual(item["duration"], 30)
            self.assertLessEqual(item["duration"], 90)
            self.assertAlmostEqual(
                item["end_time"] - item["start_time"], item["duration"], places=1
            )

    def test_timestamps_inside_video(self):
        for item in self.highlights:
            self.assertGreaterEqual(item["start_time"], 0)
            self.assertLessEqual(item["end_time"], self.transcript["duration"])

    def test_scores_in_range_and_sorted(self):
        scores = [item["score"] for item in self.highlights]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for score in scores:
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)

    def test_candidates_do_not_overlap_too_much(self):
        for i, first in enumerate(self.highlights):
            for second in self.highlights[i + 1:]:
                self.assertLess(_overlap(first, second), 0.40)

    def test_empty_transcript(self):
        self.assertEqual(
            self.engine.analyze_transcript({"segments": []}, 5),
            {"highlights": []},
        )

    def test_same_input_gives_same_output(self):
        again = self.engine.analyze_transcript(self.transcript, 5)["highlights"]
        self.assertEqual(again, self.highlights)


class EngineV41Test(EngineContract, unittest.TestCase):
    engine = zoi_engine


class EngineV42Test(EngineContract, unittest.TestCase):
    engine = zoi_engine_v4_2

    def test_clips_start_and_end_on_sentence_boundaries(self):
        sentences = zoi_engine_v4_2.build_sentences(self.transcript)
        starts = {round(s["start"], 2) for s in sentences}
        ends = {round(s["end"], 2) for s in sentences}

        for item in self.highlights:
            self.assertIn(item["start_time"], starts)
            self.assertIn(item["end_time"], ends)

    def test_hook_comes_from_the_transcript(self):
        text = " ".join(s["text"] for s in self.transcript["segments"])
        for item in self.highlights:
            self.assertIn(item["hook_sentence"][:40], text)

    def test_reason_is_specific_to_each_clip(self):
        reasons = {item["virality_reason"] for item in self.highlights}
        self.assertGreater(len(reasons), 1)

    def test_works_without_word_timestamps(self):
        result = zoi_engine_v4_2.analyze_transcript(
            {"segments": self.transcript["segments"]}, 5
        )
        self.assertGreaterEqual(len(result["highlights"]), 3)

    def test_money_is_detected(self):
        score, labels = zoi_engine_v4_2.sentence_signals(
            {"start": 0, "end": 3, "text": "harganya rp 500 ribu"}, 0
        )
        self.assertIn("uang", labels)
        self.assertGreaterEqual(score, 12)


if __name__ == "__main__":
    unittest.main()
