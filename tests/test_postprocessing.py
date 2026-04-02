import unittest
from unittest import mock

from src.postprocessing import LlamaCppCliBackend, PostProcessingHandle
from src.streaming_contracts import ClauseInfo, TranslationUpdate


class PostProcessingTests(unittest.TestCase):
    def make_update(
        self,
        revision_id=1,
        commit_id=1,
        provisional_text="hello world",
        committed_text="hello world",
        committed_append="hello world",
        clause=None,
    ):
        return TranslationUpdate(
            revision_id=revision_id,
            commit_id=commit_id,
            provisional_text=provisional_text,
            committed_text=committed_text,
            committed_append=committed_append,
            detected_language="en",
            clause=clause,
            audio_start_ms=0,
            audio_end_ms=500,
        )

    def test_process_update_rewrites_committed_append_and_keeps_preview_suffix(self):
        handle = PostProcessingHandle(update_callback=lambda update, status: None)
        handle.enabled = True
        handle.backend = mock.Mock()
        handle.backend.validate.return_value = (True, "Ready: granite.gguf")
        handle.backend.process_text.return_value = "Hello, world."

        processed, status = handle._process_update(
            self.make_update(provisional_text="hello world and more"),
            {"task": "translate", "target_language": "en"},
        )

        self.assertEqual(processed.committed_append, "Hello, world.")
        self.assertEqual(processed.committed_text, "Hello, world.")
        self.assertEqual(processed.provisional_text, "Hello, world. and more")
        self.assertEqual(status["postprocess_state"], "ready")

    def test_build_prompt_uses_strict_subtitle_cleanup_profile(self):
        backend = LlamaCppCliBackend("llama-cli", "model.gguf")

        prompt = backend._build_prompt("im gonna go now", {"task": "translate", "target_language": "en"})

        self.assertIn("Return exactly one cleaned subtitle string", prompt)
        self.assertIn("Do not translate, summarize, explain, or answer.", prompt)
        self.assertIn("If the text is already acceptable, return it unchanged.", prompt)

    def test_safe_rewrite_rejects_explanatory_output(self):
        backend = LlamaCppCliBackend("llama-cli", "model.gguf")

        is_safe = backend._is_safe_rewrite("hello world", "Here is the cleaned subtitle: Hello world.")

        self.assertFalse(is_safe)

    def test_submit_translation_update_invokes_callback_with_processed_update(self):
        received = []
        handle = PostProcessingHandle(update_callback=lambda update, status: received.append((update, status)))
        handle.enabled = True
        handle.backend = mock.Mock()
        handle.backend.validate.return_value = (True, "Ready: granite.gguf")
        handle.backend.process_text.return_value = "Hello world."

        handle.submit_translation_update(self.make_update(), {"task": "translate", "target_language": "en"})

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0].committed_append, "Hello world.")
        self.assertEqual(received[0][1]["postprocess_state"], "ready")

    def test_process_update_falls_back_to_raw_append_on_backend_error(self):
        handle = PostProcessingHandle(update_callback=lambda update, status: None)
        handle.enabled = True
        handle.backend = mock.Mock()
        handle.backend.validate.return_value = (True, "Ready: granite.gguf")
        handle.backend.process_text.side_effect = RuntimeError("boom")

        processed, status = handle._process_update(
            self.make_update(),
            {"task": "translate", "target_language": "en"},
        )

        self.assertEqual(processed.committed_append, "hello world")
        self.assertEqual(processed.committed_text, "hello world")
        self.assertEqual(status["postprocess_state"], "error")
        self.assertIn("boom", status["postprocess_message"])

    def test_process_update_recomputes_clause_bounds_for_rewritten_text(self):
        handle = PostProcessingHandle(update_callback=lambda update, status: None)
        handle.enabled = True
        handle.backend = mock.Mock()
        handle.backend.validate.return_value = (True, "Ready: granite.gguf")
        handle.backend.process_text.return_value = "Bravo now."
        handle.raw_committed_text = "Alpha."
        handle.processed_committed_text = "Alpha."
        update = self.make_update(
            revision_id=2,
            commit_id=2,
            provisional_text="Alpha. bravo now",
            committed_text="Alpha. bravo now",
            committed_append=" bravo now",
            clause=ClauseInfo(clause_id=4, is_final_clause=True, char_start=6, char_end=16),
        )

        processed, _ = handle._process_update(update, {"task": "translate", "target_language": "en"})

        self.assertEqual(processed.committed_text, "Alpha. Bravo now.")
        self.assertEqual(processed.clause.char_start, 6)
        self.assertEqual(processed.clause.char_end, len("Alpha. Bravo now."))

    def test_configure_returns_error_state_for_missing_model(self):
        handle = PostProcessingHandle(update_callback=lambda update, status: None)

        result = handle.configure(True, executable_path="llama-cli", model_path="")

        self.assertTrue(result["postprocess_enabled"])
        self.assertEqual(result["postprocess_state"], "error")
        self.assertIn("model path", result["postprocess_message"].lower())


if __name__ == "__main__":
    unittest.main()
