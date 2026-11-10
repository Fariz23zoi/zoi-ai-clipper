"""Tes parser subtitle YouTube.

Jalankan dari folder proyek:
    python -m unittest discover -s tests -v
"""
import re
import unittest
from pathlib import Path

from shorts_generator.local.transcriber_youtube import _parse_vtt

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _words(segments):
    return " ".join(segment["text"] for segment in segments).split()


class AutoCaptionTest(unittest.TestCase):
    """Subtitle otomatis: tag waktu per kata dan rolling caption."""

    @classmethod
    def setUpClass(cls):
        cls.raw = _read("auto_id.vtt")
        cls.segments = _parse_vtt(cls.raw)

    def test_no_word_is_lost(self):
        # Teks baru selalu ada di baris terakhir setiap cue.
        expected = []
        for block in self.raw.split("\n\n"):
            lines = block.split("\n")
            if any("-->" in line for line in lines):
                expected.extend(re.sub(r"<[^>]+>", "", lines[-1]).split())

        self.assertEqual(_words(self.segments), expected)

    def test_first_word_of_each_cue_is_kept(self):
        text = " ".join(_words(self.segments))
        self.assertIn("Kali ini ya, saya di kantor", text)

    def test_opening_cue_starts_at_its_own_time(self):
        self.assertEqual(self.segments[0]["start"], 0.1)
        self.assertTrue(self.segments[0]["text"].startswith("Selamat siang"))

    def test_rolled_lines_are_not_repeated(self):
        text = " ".join(_words(self.segments))
        self.assertEqual(text.count("Selamat siang teman-teman semua."), 1)

    def test_segments_are_ordered_and_do_not_overlap(self):
        for segment in self.segments:
            self.assertLess(segment["start"], segment["end"])
        for before, after in zip(self.segments, self.segments[1:]):
            self.assertLessEqual(before["end"], after["start"])

    def test_segment_length_stays_bounded(self):
        for segment in self.segments:
            self.assertLessEqual(segment["end"] - segment["start"], 10.0)

    def test_video_longer_than_one_hour(self):
        shifted = re.sub(r"(?m)^00:", "01:", self.raw)
        shifted = re.sub(r"--> 00:", "--> 01:", shifted)
        shifted = shifted.replace("<00:", "<01:")
        segments = _parse_vtt(shifted)

        self.assertEqual(_words(segments), _words(self.segments))
        self.assertEqual(segments[0]["start"], 3600.1)


class ManualSubtitleTest(unittest.TestCase):
    """Subtitle unggahan pembuat video: teks biasa tanpa tag waktu."""

    @classmethod
    def setUpClass(cls):
        cls.segments = _parse_vtt(_read("manual_en.vtt"))

    def test_text_is_extracted(self):
        text = " ".join(_words(self.segments))
        self.assertIn("We're no strangers to love", text)
        self.assertIn("You know the rules and so do I", text)

    def test_music_symbols_are_dropped(self):
        text = " ".join(_words(self.segments))
        self.assertNotIn("♪", text)
        self.assertNotIn("[]", text)

    def test_timing_follows_the_cues(self):
        self.assertEqual(self.segments[0]["start"], 18.64)
        for segment in self.segments:
            self.assertLess(segment["start"], segment["end"])


class EdgeCaseTest(unittest.TestCase):
    def test_empty_file(self):
        self.assertEqual(_parse_vtt("WEBVTT\n"), [])

    def test_short_timestamps_and_windows_newlines(self):
        content = "WEBVTT\r\n\r\n00:01.000 --> 00:03.000\r\nhalo dunia\r\n"
        self.assertEqual(
            _parse_vtt(content),
            [{"start": 1.0, "end": 3.0, "text": "halo dunia"}]
        )

    def test_sound_markers_are_kept_for_the_engine(self):
        content = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000\n \n"
            "lucu<00:00:01.500><c> banget</c>\n\n"
            "00:00:02.000 --> 00:00:02.010\nlucu banget\n \n\n"
            "00:00:02.010 --> 00:00:03.000\nlucu banget\n[tertawa]\n"
        )
        self.assertEqual(
            _parse_vtt(content)[0]["text"], "lucu banget [tertawa]"
        )

    def test_html_entities_are_decoded(self):
        content = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nA &amp; B\n"
        self.assertEqual(_parse_vtt(content)[0]["text"], "A & B")


if __name__ == "__main__":
    unittest.main()
