import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from digest import audio


def make_paper(pid, upvotes=None, interest=3, highlight=True, summary=None):
    return {
        "id": pid,
        "title": f"Paper {pid}",
        "abstract": "An abstract.",
        "summary": summary or "First sentence here. Second sentence here. Third one.",
        "interest": interest,
        "hf_upvotes": upvotes,
        "highlight": highlight,
        "highlight_summary": "**Bold** claim with `Model`.\n- A bullet point.",
    }


def make_day(papers):
    return {"date": "2026-11-11", "papers": papers}


class PickPaperTest(unittest.TestCase):
    def test_most_upvotes_wins(self):
        day = make_day([make_paper("a", upvotes=5), make_paper("b", upvotes=40), make_paper("c")])
        self.assertEqual(audio.pick_paper(day)["id"], "b")

    def test_interest_breaks_ties(self):
        day = make_day([make_paper("a", interest=3), make_paper("b", interest=5)])
        self.assertEqual(audio.pick_paper(day)["id"], "b")

    def test_falls_back_to_summarized_papers_without_highlights(self):
        day = make_day([make_paper("a", highlight=False, interest=2),
                        make_paper("b", highlight=False, interest=4)])
        self.assertEqual(audio.pick_paper(day)["id"], "b")

    def test_empty_day(self):
        self.assertIsNone(audio.pick_paper(make_day([])))


class ScriptTest(unittest.TestCase):
    def test_model_script_is_cleaned_and_used(self):
        script, source = audio.build_script(make_paper("a"), lambda prompt: "Today's **pick**. Plain `words`.")
        self.assertEqual(source, "model")
        self.assertEqual(script, "Today's pick. Plain words.")

    def test_over_budget_model_script_falls_back(self):
        script, source = audio.build_script(make_paper("a"), lambda prompt: "x" * 700, max_chars=600)
        self.assertEqual(source, "template")
        self.assertLessEqual(len(script), 600)

    def test_failed_model_call_falls_back(self):
        def boom(prompt):
            raise RuntimeError("claude -p exited 1")
        script, source = audio.build_script(make_paper("a"), boom)
        self.assertEqual(source, "template")
        self.assertTrue(script.startswith("Today's pick from the AI Paper Digest."))

    def test_template_keeps_whole_sentences_within_budget(self):
        paper = make_paper("a")
        budget = len("Today's pick from the AI Paper Digest. Paper a. First sentence here.") + 5
        script = audio.fallback_script(paper, max_chars=budget)
        self.assertTrue(script.endswith("First sentence here."))
        self.assertLessEqual(len(script), budget)

    def test_prompt_carries_budget_and_material(self):
        seen = {}
        audio.build_script(make_paper("a"), lambda prompt: seen.setdefault("p", prompt) and "ok", max_chars=450)
        self.assertIn("450 characters", seen["p"])
        self.assertIn("A bullet point.", seen["p"])


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SynthesizeTest(unittest.TestCase):
    def test_request_shape_and_bytes(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            return FakeResponse(b"ID3fake")

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            data = audio.synthesize("Hello.", "voice123", "key456", "eleven_flash_v2_5")
        request = captured["request"]
        self.assertEqual(data, b"ID3fake")
        self.assertIn("/v1/text-to-speech/voice123?output_format=mp3_22050_32", request.full_url)
        self.assertEqual(request.get_header("Xi-api-key"), "key456")
        self.assertEqual(json.loads(request.data), {"text": "Hello.", "model_id": "eleven_flash_v2_5"})

    def _http_error(self, code):
        return urllib.error.HTTPError("u", code, "msg", {}, io.BytesIO(b'{"detail":"x"}'))

    def test_client_errors_are_not_retryable(self):
        for code in (401, 422):
            with mock.patch("urllib.request.urlopen", side_effect=self._http_error(code)):
                with self.assertRaises(audio.TTSError) as caught:
                    audio.synthesize("t", "v", "k")
            self.assertEqual(caught.exception.status, code)
            self.assertFalse(caught.exception.retryable)

    def test_busy_or_broken_service_is_retryable(self):
        for code in (429, 503):
            with mock.patch("urllib.request.urlopen", side_effect=self._http_error(code)):
                with self.assertRaises(audio.TTSError) as caught:
                    audio.synthesize("t", "v", "k")
            self.assertTrue(caught.exception.retryable)

    def test_network_failure_is_retryable(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaises(audio.TTSError) as caught:
                audio.synthesize("t", "v", "k")
        self.assertIsNone(caught.exception.status)
        self.assertTrue(caught.exception.retryable)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_file = Path(self.tmp.name) / "2026-11-11.json"
        self.data_file.write_text(json.dumps(make_day([make_paper("a", upvotes=3)])))

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_spends_nothing_and_writes_nothing(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            code = audio.main([str(self.data_file), "--dry-run"], run=lambda p: "Short script.")
        self.assertEqual(code, 0)
        urlopen.assert_not_called()
        self.assertNotIn("audio", json.loads(self.data_file.read_text()))

    def test_writes_mp3_and_records_script(self):
        env = {"ELEVENLABS_API_KEY": "k", "ELEVENLABS_VOICE_ID": "v"}
        with mock.patch.dict("os.environ", env), \
                mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"ID3")):
            code = audio.main([str(self.data_file)], run=lambda p: "Short script.")
        self.assertEqual(code, 0)
        mp3 = Path(self.tmp.name) / "audio" / "2026-11-11.mp3"
        self.assertEqual(mp3.read_bytes(), b"ID3")
        recorded = json.loads(self.data_file.read_text())["audio"]
        self.assertEqual(recorded["script"], "Short script.")
        self.assertEqual(recorded["file"], "audio/2026-11-11.mp3")

    def test_second_run_is_skipped(self):
        env = {"ELEVENLABS_API_KEY": "k", "ELEVENLABS_VOICE_ID": "v"}
        with mock.patch.dict("os.environ", env), \
                mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"ID3")) as urlopen:
            audio.main([str(self.data_file)], run=lambda p: "Short script.")
            audio.main([str(self.data_file)], run=lambda p: "Short script.")
        self.assertEqual(urlopen.call_count, 1)

    def test_missing_key_fails_without_calling(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("urllib.request.urlopen") as urlopen:
            code = audio.main([str(self.data_file)], run=lambda p: "Short script.")
        self.assertEqual(code, 1)
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
