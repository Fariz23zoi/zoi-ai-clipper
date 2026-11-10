"""Tes ekspor transkrip dan impor hasil analisis AI."""
import copy
import json
import shutil
import tempfile
import unittest

from shorts_generator import exchange

from tests.helpers import VIDEO_ID, sample_transcript


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.transcript = sample_transcript()
        cls.export = exchange.build_export(cls.transcript)

    def test_export_is_valid_json_with_metadata(self):
        data = json.loads(json.dumps(self.export, ensure_ascii=False))
        self.assertEqual(data["schema"], "zoi.export.v1")
        self.assertEqual(data["video"]["video_id"], VIDEO_ID)
        self.assertEqual(data["video"]["duration"], 1624.0)
        self.assertEqual(data["video"]["language"], "id")
        self.assertEqual(data["constraints"]["min_duration"], 30)
        self.assertEqual(data["constraints"]["max_duration"], 90)

    def test_export_contains_every_segment_in_order(self):
        segments = self.export["transcript"]["segments"]
        self.assertEqual(len(segments), len(self.transcript["segments"]))
        self.assertEqual(
            self.export["transcript"]["segment_count"], len(segments)
        )
        self.assertEqual([s["i"] for s in segments], list(range(len(segments))))
        self.assertEqual(segments[0]["text"], self.transcript["segments"][0]["text"])

    def test_hash_changes_when_transcript_changes(self):
        other = copy.deepcopy(self.transcript)
        other["segments"][0]["text"] += " tambahan"
        self.assertNotEqual(
            exchange.transcript_hash(self.transcript),
            exchange.transcript_hash(other),
        )
        self.assertEqual(
            exchange.transcript_hash(self.transcript),
            self.export["transcript"]["sha256"],
        )

    def test_prompt_carries_rules_and_data(self):
        prompt = exchange.build_prompt(self.export, 5)
        self.assertIn("zoi.import.v1", prompt)
        self.assertIn(self.export["transcript"]["sha256"], prompt)
        self.assertIn("30 sampai 90 detik", prompt)
        self.assertIn(self.transcript["segments"][5]["text"], prompt)

    def test_empty_transcript_cannot_be_exported(self):
        with self.assertRaises(exchange.ExchangeError):
            exchange.build_export({"video_id": "x", "segments": []})


class ParseImportTest(unittest.TestCase):
    PAYLOAD = '{"highlights": [{"start_time": 1, "end_time": 50}]}'

    def test_plain_json(self):
        self.assertIn("highlights", exchange.parse_import(self.PAYLOAD))

    def test_json_inside_markdown_fence(self):
        text = "Berikut hasilnya:\n```json\n" + self.PAYLOAD + "\n```\nSemoga membantu."
        self.assertIn("highlights", exchange.parse_import(text))

    def test_json_surrounded_by_prose(self):
        text = "Ini analisisnya: " + self.PAYLOAD + " Selesai."
        self.assertIn("highlights", exchange.parse_import(text))

    def test_bare_list_is_wrapped(self):
        data = exchange.parse_import('[{"start_time": 1, "end_time": 50}]')
        self.assertEqual(len(data["highlights"]), 1)

    def test_bytes_with_bom(self):
        data = exchange.parse_import(b"\xef\xbb\xbf" + self.PAYLOAD.encode())
        self.assertIn("highlights", data)

    def test_rejects_non_json(self):
        for text in ("", "   ", "ini bukan json", "{rusak"):
            with self.assertRaises(exchange.ExchangeError):
                exchange.parse_import(text)

    def test_rejects_oversized_input(self):
        with self.assertRaises(exchange.ExchangeError):
            exchange.parse_import("x" * (exchange.MAX_IMPORT_BYTES + 10))


class ValidateImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.transcript = sample_transcript()
        cls.segments = exchange.build_export(cls.transcript)["transcript"]["segments"]
        cls.sha = exchange.transcript_hash(cls.transcript)

    def candidate(self, first, last, **extra):
        """Kandidat yang sah dari segmen nomor first sampai last."""
        item = {
            "title": f"Klip {first}",
            "start_segment": first,
            "end_segment": last,
            "start_time": self.segments[first]["start"],
            "end_time": self.segments[last]["end"],
            "hook_sentence": " ".join(self.segments[first]["text"].split()[:8]),
            "virality_reason": "Alasan",
            "score": 80,
        }
        item.update(extra)
        return item

    def validate(self, highlights, **top):
        data = {
            "schema": "zoi.import.v1", "video_id": VIDEO_ID,
            "transcript_sha256": self.sha, "analyst": "claude",
            "highlights": highlights,
        }
        data.update(top)
        return exchange.validate_import(data, self.transcript)

    def reasons(self, result):
        return " | ".join(item["reason"] for item in result["rejected"])

    # ---- struktur

    def test_valid_candidate_is_accepted_unchanged(self):
        result = self.validate([self.candidate(10, 16)])
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(result["rejected"], [])
        self.assertEqual(result["file_warnings"], [])
        clip = result["accepted"][0]
        self.assertEqual(clip["start_time"], self.segments[10]["start"])
        self.assertEqual(clip["end_time"], self.segments[16]["end"])
        self.assertEqual(clip["warnings"], [])
        for key in (
            "title", "start_time", "end_time", "duration", "score",
            "hook_sentence", "virality_reason",
        ):
            self.assertIn(key, clip)

    def test_unknown_schema_is_refused(self):
        with self.assertRaises(exchange.ExchangeError):
            self.validate([self.candidate(10, 16)], schema="lain.v9")

    def test_missing_or_empty_highlights_is_refused(self):
        for bad in ({"video_id": VIDEO_ID}, {"highlights": []}, {"highlights": "x"}, []):
            with self.assertRaises(exchange.ExchangeError):
                exchange.validate_import(bad, self.transcript)

    def test_other_video_is_refused(self):
        with self.assertRaises(exchange.ExchangeError):
            self.validate([self.candidate(10, 16)], video_id="VIDEOLAIN01")

    def test_missing_video_id_gives_warning(self):
        result = self.validate([self.candidate(10, 16)], video_id=None)
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(len(result["file_warnings"]), 1)

    def test_different_transcript_hash_gives_warning(self):
        result = self.validate([self.candidate(10, 16)], transcript_sha256="0" * 16)
        self.assertEqual(len(result["accepted"]), 1)
        self.assertIn("transkrip", result["file_warnings"][0])

    def test_non_object_candidate_is_rejected(self):
        result = self.validate(["teks", self.candidate(10, 16)])
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(result["rejected"][0]["index"], 1)

    # ---- timestamp

    def test_end_beyond_video_duration_is_rejected(self):
        result = self.validate([
            {"title": "x", "start_time": 1600, "end_time": 1700, "score": 90}
        ])
        self.assertEqual(result["accepted"], [])
        self.assertIn("melewati durasi", self.reasons(result))

    def test_negative_and_reversed_times_are_rejected(self):
        result = self.validate([
            {"start_time": -5, "end_time": 40},
            {"start_time": 100, "end_time": 60},
            {"start_time": "abc", "end_time": 60},
            {"end_time": 60},
        ])
        self.assertEqual(result["accepted"], [])
        self.assertEqual(len(result["rejected"]), 4)

    def test_clock_format_is_understood(self):
        first, last = self.segments[10], self.segments[16]
        result = self.validate([{
            "title": "jam",
            "start_time": exchange.format_time(first["start"])[3:],
            "end_time": exchange.format_time(last["end"]),
            "score": 70,
        }])
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(result["accepted"][0]["start_time"], first["start"])

    def test_times_are_snapped_to_sentence_boundaries(self):
        first, last = self.segments[10], self.segments[16]
        result = self.validate([{
            "title": "geser", "start_time": first["start"] + 1.2,
            "end_time": last["end"] - 0.9, "score": 70,
        }])
        clip = result["accepted"][0]
        self.assertEqual(clip["start_time"], first["start"])
        self.assertEqual(clip["end_time"], last["end"])
        self.assertTrue(any("digeser" in w for w in clip["warnings"]))

    def test_time_that_contradicts_segment_number_is_rejected(self):
        item = self.candidate(10, 16)
        item["start_time"] += 200
        result = self.validate([item])
        self.assertEqual(result["accepted"], [])
        self.assertIn("tidak cocok dengan segmen", self.reasons(result))

    def test_segment_numbers_alone_are_enough(self):
        result = self.validate([
            {"title": "indeks", "start_segment": 10, "end_segment": 16, "score": 60}
        ])
        self.assertEqual(
            result["accepted"][0]["start_time"], self.segments[10]["start"]
        )

    # ---- durasi

    def test_too_short_and_too_long_are_rejected(self):
        result = self.validate([self.candidate(10, 11), self.candidate(20, 40)])
        self.assertEqual(result["accepted"], [])
        self.assertEqual(len(result["rejected"]), 2)
        self.assertIn("batasnya 30-90", self.reasons(result))

    def test_range_without_speech_is_rejected(self):
        quiet = copy.deepcopy(self.transcript)
        quiet["segments"] = [
            {"start": 0, "end": 5, "text": "halo"},
            {"start": 100, "end": 105, "text": "dunia"},
        ]
        quiet["words"] = []
        result = exchange.validate_import(
            {"highlights": [{"start_time": 0, "end_time": 60}]}, quiet
        )
        self.assertEqual(result["accepted"], [])
        self.assertIn("tidak ada ucapan", self.reasons(result))

    # ---- isi

    def test_invented_hook_gets_warning(self):
        result = self.validate([
            self.candidate(10, 16, hook_sentence="kalimat ini tidak pernah diucapkan sama sekali")
        ])
        self.assertTrue(
            any("hook" in w.lower() for w in result["accepted"][0]["warnings"])
        )

    def test_scores_are_clamped_and_tolerant(self):
        result = self.validate([
            self.candidate(10, 16, score=250),
            self.candidate(30, 36, score="87.6"),
            self.candidate(50, 56, score=None),
            self.candidate(70, 76, score="tinggi"),
        ])
        scores = sorted(c["score"] for c in result["accepted"])
        self.assertEqual(scores, [0, 0, 88, 100])

    def test_long_text_is_truncated(self):
        result = self.validate([
            self.candidate(10, 16, title="J" * 500, virality_reason="A" * 5000)
        ])
        clip = result["accepted"][0]
        self.assertEqual(len(clip["title"]), 100)
        self.assertEqual(len(clip["virality_reason"]), 500)

    # ---- jumlah dan tumpang tindih

    def test_overlapping_candidates_keep_the_higher_score(self):
        result = self.validate([
            self.candidate(10, 16, score=60, title="rendah"),
            self.candidate(11, 17, score=90, title="tinggi"),
        ])
        self.assertEqual([c["title"] for c in result["accepted"]], ["tinggi"])
        self.assertIn("tumpang tindih", self.reasons(result))

    def test_candidate_count_is_limited(self):
        items = [
            self.candidate(start, start + 6, score=100 - n)
            for n, start in enumerate(range(0, 180, 12))
        ]
        result = self.validate(items)
        self.assertEqual(len(result["accepted"]), exchange.MAX_CANDIDATES)
        self.assertIn("melebihi batas", self.reasons(result))

    def test_accepted_are_sorted_by_score(self):
        result = self.validate([
            self.candidate(10, 16, score=40),
            self.candidate(30, 36, score=95),
            self.candidate(50, 56, score=70),
        ])
        self.assertEqual([c["score"] for c in result["accepted"]], [95, 70, 40])

    def test_zoi_engine_output_passes_validation(self):
        from shorts_generator import zoi_engine_v4_2
        highlights = zoi_engine_v4_2.analyze_transcript(
            self.transcript, 5
        )["highlights"]
        result = self.validate(highlights)
        self.assertEqual(len(result["accepted"]), len(highlights))


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.original = exchange.DATA_DIR
        exchange.DATA_DIR = self.folder

    def tearDown(self):
        exchange.DATA_DIR = self.original
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_transcript_round_trip(self):
        transcript = sample_transcript()
        exchange.save_transcript(transcript)
        self.assertEqual(exchange.load_transcript(VIDEO_ID), transcript)

    def test_missing_video_returns_none(self):
        self.assertIsNone(exchange.load_transcript("belumada"))
        self.assertIsNone(exchange.load_analysis("belumada"))

    def test_unsafe_video_id_is_refused(self):
        for bad in ("../rahasia", "a/b", "", "x" * 80, "a b"):
            self.assertFalse(exchange.is_valid_video_id(bad))
            with self.assertRaises(exchange.ExchangeError):
                exchange.load_transcript(bad)

    def test_list_videos_newest_first(self):
        exchange.save_analysis("AAA", {"title": "lama", "updated_at": 1, "groups": []})
        exchange.save_analysis("BBB", {"title": "baru", "updated_at": 2, "groups": []})
        self.assertEqual(
            [v["title"] for v in exchange.list_videos()], ["baru", "lama"]
        )


if __name__ == "__main__":
    unittest.main()
