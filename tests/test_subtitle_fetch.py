"""Tes pengambilan subtitle YouTube dengan yt-dlp tiruan (tanpa internet).

Yang diuji: bahasa mana yang dicoba dan dalam urutan apa, serta pesan
gagal yang menyebut penyebab sebenarnya.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    from yt_dlp.utils import DownloadError
    from shorts_generator.local import transcriber_youtube as transcriber
except ImportError:
    transcriber = None

FIXTURES = Path(__file__).parent / "fixtures"
VIDEO_ID = "ABCDEFGHIJK"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def _auto(*languages):
    return {language: [{"ext": "vtt"}] for language in languages}


@unittest.skipIf(transcriber is None, "yt-dlp tidak terpasang")
class CandidateOrderTest(unittest.TestCase):
    def candidates(self, manual=(), automatic=()):
        info = {"subtitles": _auto(*manual), "automatic_captions": _auto(*automatic)}
        return transcriber._subtitle_candidates(info, ["id", "en"])

    def test_indonesian_video(self):
        self.assertEqual(
            self.candidates(automatic=["id-orig", "id", "en", "ja"]),
            ["id", "en", "id-orig"],
        )

    def test_english_video_tries_indonesian_translation_then_english(self):
        self.assertEqual(
            self.candidates(automatic=["en-orig", "en", "id", "fr"]),
            ["id", "en", "en-orig"],
        )

    def test_other_language_falls_back_to_the_original_speech(self):
        self.assertEqual(
            self.candidates(automatic=["ja-orig", "ja", "id", "en"]),
            ["id", "en", "ja-orig"],
        )

    def test_regional_and_other_manual_subtitles(self):
        self.assertEqual(
            self.candidates(manual=["en-US", "live_chat", "ms"]),
            ["en-US", "ms"],
        )

    def test_nothing_listed_means_nothing_is_tried(self):
        self.assertEqual(self.candidates(), [])

    def test_missing_lists_fall_back_to_preferred_languages(self):
        self.assertEqual(
            transcriber._subtitle_candidates({}, ["id", "en"]), ["id", "en"]
        )

    def test_number_of_attempts_is_capped(self):
        many = [f"x{n}" for n in range(30)]
        result = self.candidates(manual=many)
        self.assertEqual(len(result), transcriber.MAX_SUBTITLE_ATTEMPTS)


@unittest.skipIf(transcriber is None, "yt-dlp tidak terpasang")
class FetchTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.saved = (transcriber.YoutubeDL, transcriber.LOCAL_OUTPUT_DIR)
        transcriber.LOCAL_OUTPUT_DIR = self.folder

        self.auto_text = (FIXTURES / "auto_id.vtt").read_text(encoding="utf-8")
        self.manual_text = (FIXTURES / "manual_en.vtt").read_text(encoding="utf-8")

        self.info = {
            "id": VIDEO_ID, "title": "Video uji", "duration": 1624,
            "webpage_url": URL, "channel": "Kanal", "width": 1920, "height": 1080,
            "subtitles": {}, "automatic_captions": {},
        }
        self.files = {}      # bahasa -> isi VTT yang "dikirim YouTube"
        self.failing = {}    # bahasa -> pesan gagal
        self.calls = []
        self.info_error = None
        test = self

        class FakeYDL:
            def __init__(self, params):
                self.params = params

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=False):
                if not download:
                    if test.info_error:
                        raise DownloadError(test.info_error)
                    return dict(test.info)

                language = self.params["subtitleslangs"][0]
                test.calls.append(language)

                if language in test.failing:
                    raise DownloadError(
                        "\x1b[0;31mERROR:\x1b[0m Unable to download video "
                        f"subtitles for '{language}': {test.failing[language]}"
                    )

                if language in test.files:
                    path = self.params["outtmpl"].replace(
                        "%(ext)s", f"{language}.vtt"
                    )
                    Path(path).write_text(test.files[language], encoding="utf-8")

                return dict(test.info)

        transcriber.YoutubeDL = FakeYDL

    def tearDown(self):
        transcriber.YoutubeDL, transcriber.LOCAL_OUTPUT_DIR = self.saved
        shutil.rmtree(self.folder, ignore_errors=True)

    def fail(self):
        with self.assertRaises(RuntimeError) as caught:
            transcriber.transcribe_from_youtube(URL)
        return str(caught.exception)

    # ---- berhasil

    def test_indonesian_auto_captions(self):
        self.info["automatic_captions"] = _auto("id-orig", "id", "en")
        self.files = {"id": self.auto_text}

        result = transcriber.transcribe_from_youtube(URL)
        self.assertEqual(self.calls, ["id"])
        self.assertEqual(result["language"], "id")
        self.assertEqual(result["video_id"], VIDEO_ID)
        self.assertEqual((result["width"], result["height"]), (1920, 1080))
        self.assertGreater(len(result["segments"]), 100)
        self.assertGreater(len(result["words"]), 3000)

    def test_manual_english_only_does_not_try_indonesian(self):
        self.info["subtitles"] = _auto("en")
        self.files = {"en": self.manual_text}

        result = transcriber.transcribe_from_youtube(URL)
        self.assertEqual(self.calls, ["en"])
        self.assertEqual(result["language"], "en")

    def test_regional_manual_subtitle(self):
        self.info["subtitles"] = _auto("en-US")
        self.files = {"en-US": self.manual_text}

        result = transcriber.transcribe_from_youtube(URL)
        self.assertEqual(result["language"], "en-US")

    def test_blocked_translation_falls_back_to_english(self):
        self.info["automatic_captions"] = _auto("en-orig", "en", "id")
        self.failing = {"id": "HTTP Error 429: Too Many Requests"}
        self.files = {"en": self.manual_text}

        result = transcriber.transcribe_from_youtube(URL)
        self.assertEqual(self.calls, ["id", "en"])
        self.assertEqual(result["language"], "en")

    def test_other_language_uses_the_original_speech_track(self):
        self.info["automatic_captions"] = _auto("ja-orig", "ja", "id", "en")
        self.failing = {
            "id": "HTTP Error 429: Too Many Requests",
            "en": "HTTP Error 429: Too Many Requests",
        }
        self.files = {"ja-orig": self.auto_text}

        result = transcriber.transcribe_from_youtube(URL)
        self.assertEqual(self.calls, ["id", "en", "ja-orig"])
        self.assertEqual(result["language"], "ja")
        self.assertEqual(result["subtitle_track"], "ja-orig")

    def test_empty_subtitle_file_moves_on_to_the_next_language(self):
        self.info["automatic_captions"] = _auto("id", "en")
        self.files = {"id": "WEBVTT\n\n", "en": self.manual_text}

        result = transcriber.transcribe_from_youtube(URL)
        self.assertEqual(result["language"], "en")

    def test_saved_subtitle_is_reused_without_network(self):
        self.info["automatic_captions"] = _auto("id")
        saved = os.path.join(self.folder, f"youtube_transcript_{VIDEO_ID}.id.vtt")
        Path(saved).write_text(self.auto_text, encoding="utf-8")

        result = transcriber.transcribe_from_youtube(URL)
        self.assertEqual(self.calls, [])
        self.assertEqual(result["language"], "id")

    def test_lists_missing_from_youtube_still_try_preferred_languages(self):
        del self.info["subtitles"], self.info["automatic_captions"]
        self.files = {"en": self.manual_text}

        result = transcriber.transcribe_from_youtube(URL)
        self.assertEqual(self.calls, ["id", "en"])
        self.assertEqual(result["language"], "en")

    # ---- gagal: pesannya harus menyebut penyebab sebenarnya

    def test_video_without_any_subtitles(self):
        message = self.fail()
        self.assertIn("tidak menyediakan subtitle", message)
        # Tidak ada percobaan unduh yang sia-sia.
        self.assertEqual(self.calls, [])

    def test_rate_limit_is_not_reported_as_missing_subtitles(self):
        self.info["automatic_captions"] = _auto("id-orig", "id", "en")
        self.failing = {
            key: "HTTP Error 429: Too Many Requests"
            for key in ("id", "en", "id-orig")
        }

        message = self.fail()
        self.assertEqual(self.calls, ["id", "en", "id-orig"])
        self.assertNotIn("tidak menyediakan subtitle", message)
        self.assertIn("gagal diambil", message)
        self.assertIn("id, en, id-orig", message)
        self.assertIn("Tunggu beberapa menit", message)
        self.assertIn("HTTP Error 429", message)
        # Kode warna terminal dari yt-dlp tidak ikut tampil.
        self.assertNotIn("\x1b", message)
        self.assertNotIn("ERROR:", message)

    def test_other_download_error_is_shown(self):
        self.info["automatic_captions"] = _auto("id")
        self.failing = {"id": "Sign in to confirm you're not a bot"}

        message = self.fail()
        self.assertIn("not a bot", message)
        self.assertNotIn("Tunggu beberapa menit", message)

    def test_listed_but_no_file_received(self):
        self.info["automatic_captions"] = _auto("id")
        message = self.fail()
        self.assertIn("tidak mengirim file subtitle", message)

    def test_unreadable_video(self):
        self.info_error = "\x1b[0;31mERROR:\x1b[0m [youtube] x: Video unavailable"
        message = self.fail()
        self.assertIn("Gagal membaca video YouTube", message)
        self.assertIn("Video unavailable", message)
        self.assertNotIn("\x1b", message)


if __name__ == "__main__":
    unittest.main()
