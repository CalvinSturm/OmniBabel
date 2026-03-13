from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


def now_ms() -> int:
    return int(time.time() * 1000)


class InterruptPolicy(str, Enum):
    QUEUE = "queue"
    INTERRUPT = "interrupt"
    FLUSH_AND_INTERRUPT = "flush_and_interrupt"


class TTSJobSource(str, Enum):
    COMMITTED_TRANSLATION = "committed_translation"
    FINAL_CLAUSE = "final_clause"
    MANUAL_TEST = "manual_test"


class PlaybackStatus(str, Enum):
    IDLE = "idle"
    SYNTHESIZING = "synthesizing"
    QUEUED = "queued"
    PLAYING = "playing"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    ERROR = "error"


# Invariants:
# 1. provisional_text may change between revisions.
# 2. committed_text is append-only.
# 3. committed_append is the exact immutable suffix added at commit_id.
# 4. TTS jobs may only be created from committed_append / committed clauses.
# 5. clause_id identifies an immutable spoken unit.


@dataclass(frozen=True, slots=True)
class ClauseInfo:
    clause_id: int
    is_final_clause: bool
    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if self.clause_id < 0:
            raise ValueError("clause_id must be non-negative")
        if self.char_start < 0:
            raise ValueError("char_start must be non-negative")
        if self.char_end < self.char_start:
            raise ValueError("char_end must be greater than or equal to char_start")


@dataclass(frozen=True, slots=True)
class TranslationUpdate:
    revision_id: int
    commit_id: int
    provisional_text: str
    committed_text: str
    committed_append: str
    detected_language: Optional[str]
    clause: Optional[ClauseInfo]
    audio_start_ms: Optional[int]
    audio_end_ms: Optional[int]
    created_at_ms: int = field(default_factory=now_ms)

    def __post_init__(self) -> None:
        if self.revision_id < 0:
            raise ValueError("revision_id must be non-negative")
        if self.commit_id < 0:
            raise ValueError("commit_id must be non-negative")
        if self.audio_start_ms is not None and self.audio_start_ms < 0:
            raise ValueError("audio_start_ms must be non-negative")
        if self.audio_end_ms is not None and self.audio_end_ms < 0:
            raise ValueError("audio_end_ms must be non-negative")
        if self.audio_start_ms is not None and self.audio_end_ms is not None:
            if self.audio_end_ms < self.audio_start_ms:
                raise ValueError("audio_end_ms must be greater than or equal to audio_start_ms")
        if self.committed_append and not self.committed_text.endswith(self.committed_append):
            raise ValueError("committed_append must be a suffix of committed_text")
        if self.clause is not None and self.clause.char_end > len(self.committed_text):
            raise ValueError("clause bounds must be within committed_text")


@dataclass(frozen=True, slots=True)
class TTSJob:
    job_id: int
    commit_id: int
    clause_id: int
    text: str
    source: TTSJobSource
    interrupt_policy: InterruptPolicy
    priority: int = 0
    created_at_ms: int = field(default_factory=now_ms)

    def __post_init__(self) -> None:
        if self.job_id < 0:
            raise ValueError("job_id must be non-negative")
        if self.commit_id < 0:
            raise ValueError("commit_id must be non-negative")
        if self.clause_id < 0:
            raise ValueError("clause_id must be non-negative")
        if not self.text.strip():
            raise ValueError("TTSJob text must be non-empty")


@dataclass(frozen=True, slots=True)
class SynthAudioChunk:
    job_id: int
    chunk_index: int
    sample_rate: int
    pcm_bytes: bytes
    is_last: bool
    created_at_ms: int = field(default_factory=now_ms)

    def __post_init__(self) -> None:
        if self.job_id < 0:
            raise ValueError("job_id must be non-negative")
        if self.chunk_index < 0:
            raise ValueError("chunk_index must be non-negative")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if not self.pcm_bytes:
            raise ValueError("pcm_bytes must be non-empty")


@dataclass(frozen=True, slots=True)
class PlaybackState:
    status: PlaybackStatus
    active_job_id: Optional[int]
    queued_jobs: int
    source: Optional[TTSJobSource]
    updated_at_ms: int = field(default_factory=now_ms)

    def __post_init__(self) -> None:
        if self.active_job_id is not None and self.active_job_id < 0:
            raise ValueError("active_job_id must be non-negative")
        if self.queued_jobs < 0:
            raise ValueError("queued_jobs must be non-negative")
