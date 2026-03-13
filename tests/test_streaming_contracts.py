import unittest

from src.streaming_contracts import (
    ClauseInfo,
    InterruptPolicy,
    PlaybackState,
    PlaybackStatus,
    TTSJob,
    TTSJobSource,
    TranslationUpdate,
)


class StreamingContractsTests(unittest.TestCase):
    def test_translation_update_rejects_non_suffix_committed_append(self):
        with self.assertRaises(ValueError):
            TranslationUpdate(
                revision_id=1,
                commit_id=1,
                provisional_text="hello brave new world",
                committed_text="hello world",
                committed_append="brave new",
                detected_language="en",
                clause=None,
                audio_start_ms=0,
                audio_end_ms=1000,
            )

    def test_translation_update_accepts_append_only_suffix(self):
        update = TranslationUpdate(
            revision_id=3,
            commit_id=2,
            provisional_text="hello world again",
            committed_text="hello world",
            committed_append=" world",
            detected_language="en",
            clause=ClauseInfo(clause_id=2, is_final_clause=False, char_start=5, char_end=11),
            audio_start_ms=100,
            audio_end_ms=600,
        )

        self.assertEqual(update.committed_text, "hello world")
        self.assertEqual(update.committed_append, " world")
        self.assertEqual(update.clause.clause_id, 2)

    def test_monotonic_commit_and_clause_ids_across_sequence(self):
        updates = [
            TranslationUpdate(
                revision_id=0,
                commit_id=0,
                provisional_text="hello",
                committed_text="",
                committed_append="",
                detected_language="en",
                clause=None,
                audio_start_ms=0,
                audio_end_ms=200,
            ),
            TranslationUpdate(
                revision_id=1,
                commit_id=1,
                provisional_text="hello there",
                committed_text="hello",
                committed_append="hello",
                detected_language="en",
                clause=ClauseInfo(clause_id=1, is_final_clause=False, char_start=0, char_end=5),
                audio_start_ms=0,
                audio_end_ms=400,
            ),
            TranslationUpdate(
                revision_id=2,
                commit_id=2,
                provisional_text="hello there friend",
                committed_text="hello there",
                committed_append=" there",
                detected_language="en",
                clause=ClauseInfo(clause_id=2, is_final_clause=True, char_start=5, char_end=11),
                audio_start_ms=0,
                audio_end_ms=700,
            ),
        ]

        self.assertEqual([update.commit_id for update in updates], [0, 1, 2])
        self.assertEqual([update.clause.clause_id for update in updates if update.clause], [1, 2])

    def test_tts_job_requires_committed_non_empty_text(self):
        with self.assertRaises(ValueError):
            TTSJob(
                job_id=1,
                commit_id=1,
                clause_id=1,
                text="   ",
                source=TTSJobSource.COMMITTED_TRANSLATION,
                interrupt_policy=InterruptPolicy.QUEUE,
            )

    def test_provisional_only_update_does_not_produce_tts_job(self):
        update = TranslationUpdate(
            revision_id=2,
            commit_id=1,
            provisional_text="still revising this line",
            committed_text="hello",
            committed_append="",
            detected_language="en",
            clause=None,
            audio_start_ms=0,
            audio_end_ms=500,
        )

        def maybe_make_tts_job(candidate):
            if not candidate.committed_append.strip() or candidate.clause is None:
                return None
            return TTSJob(
                job_id=10,
                commit_id=candidate.commit_id,
                clause_id=candidate.clause.clause_id,
                text=candidate.committed_append,
                source=TTSJobSource.COMMITTED_TRANSLATION,
                interrupt_policy=InterruptPolicy.QUEUE,
            )

        self.assertIsNone(maybe_make_tts_job(update))

    def test_playback_state_is_small_ui_facing_contract(self):
        state = PlaybackState(
            status=PlaybackStatus.PLAYING,
            active_job_id=7,
            queued_jobs=2,
            source=TTSJobSource.FINAL_CLAUSE,
        )

        self.assertEqual(state.status, PlaybackStatus.PLAYING)
        self.assertEqual(state.active_job_id, 7)
        self.assertEqual(state.queued_jobs, 2)


if __name__ == "__main__":
    unittest.main()
