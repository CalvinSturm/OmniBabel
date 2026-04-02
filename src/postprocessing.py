from __future__ import annotations

import queue
import re
import shutil
import subprocess
import threading
from pathlib import Path

from config import (
    DEFAULT_POSTPROCESS_ENABLED,
    DEFAULT_POSTPROCESS_EXECUTABLE,
    DEFAULT_POSTPROCESS_MODEL_PATH,
    DEFAULT_POSTPROCESS_TIMEOUT_SECONDS,
)
from src.streaming_contracts import ClauseInfo, TranslationUpdate

MAX_REWRITE_EXPANSION_RATIO = 1.6
MAX_REWRITE_ABSOLUTE_EXPANSION = 32
DISALLOWED_PREFIXES = (
    "here is",
    "here's",
    "rewritten",
    "rewrite",
    "output:",
    "answer:",
    "cleaned:",
    "corrected:",
    "explanation:",
    "note:",
)


class LlamaCppCliBackend:
    def __init__(self, executable_path, model_path, timeout_seconds=DEFAULT_POSTPROCESS_TIMEOUT_SECONDS):
        self.executable_path = (executable_path or DEFAULT_POSTPROCESS_EXECUTABLE).strip()
        self.model_path = (model_path or "").strip()
        self.timeout_seconds = timeout_seconds

    def validate(self):
        executable = self._resolve_executable()
        if executable is None:
            return False, f"llama.cpp executable not found: {self.executable_path}"

        if not self.model_path:
            return False, "No GGUF model path configured"

        model_path = Path(self.model_path)
        if not model_path.is_file():
            return False, f"GGUF model not found: {model_path}"

        return True, f"Ready: {model_path.name}"

    def process_text(self, text, status):
        executable = self._resolve_executable()
        if executable is None:
            raise RuntimeError(f"llama.cpp executable not found: {self.executable_path}")

        if not self.model_path:
            raise RuntimeError("No GGUF model path configured")

        model_path = Path(self.model_path)
        if not model_path.is_file():
            raise RuntimeError(f"GGUF model not found: {model_path}")

        prompt = self._build_prompt(text, status)
        command = [
            executable,
            "-m",
            str(model_path),
            "-p",
            prompt,
            "-n",
            "160",
            "-c",
            "2048",
            "--temp",
            "0.2",
            "--top-p",
            "0.9",
            "--repeat-penalty",
            "1.1",
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            raise RuntimeError(stderr or f"llama.cpp exited with code {completed.returncode}")

        rewritten = self._sanitize_output(completed.stdout)
        if not rewritten:
            raise RuntimeError("llama.cpp returned empty output")
        if not self._is_safe_rewrite(text, rewritten):
            raise RuntimeError("llama.cpp returned an unsafe rewrite")
        return rewritten

    def _resolve_executable(self):
        candidate = Path(self.executable_path)
        if candidate.is_file():
            return str(candidate)
        return shutil.which(self.executable_path)

    def _build_prompt(self, text, status):
        task = status.get("task", "translate")
        detected_language = status.get("detected_language") or status.get("source_language") or "unknown"
        target_language = status.get("target_language", "en")
        return (
            "You clean subtitle text.\n"
            "Return exactly one cleaned subtitle string and nothing else.\n"
            "Hard rules:\n"
            "- Keep the same language as the input.\n"
            "- Preserve meaning, names, numbers, and speaker intent.\n"
            "- Do not translate, summarize, explain, or answer.\n"
            "- Do not add scene descriptions, labels, or extra sentences.\n"
            "- Keep it short. If the text is already acceptable, return it unchanged.\n"
            "- Fix only obvious punctuation, casing, spacing, filler, or subtitle wording issues.\n"
            "Examples:\n"
            "Input: im gonna go now\n"
            "Output: I'm gonna go now.\n"
            "Input: Wait what are you doing\n"
            "Output: Wait, what are you doing?\n"
            "Input: hello world\n"
            "Output: hello world\n"
            f"Task: {task}\n"
            f"Detected language: {detected_language}\n"
            f"Target setting: {target_language}\n"
            "Subtitle text:\n"
            f"{text}\n"
            "Output:\n"
        )

    def _sanitize_output(self, text):
        cleaned = (text or "").strip()
        if not cleaned:
            return ""

        marker_match = re.search(r"rewritten subtitle text:\s*", cleaned, flags=re.IGNORECASE)
        if marker_match is not None:
            cleaned = cleaned[marker_match.end():].strip()

        cleaned = re.sub(r"^(answer|output)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()

        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"\"", "'"}:
            cleaned = cleaned[1:-1].strip()

        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        cleaned = "\n".join(lines)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        return cleaned.strip()

    def _is_safe_rewrite(self, original, rewritten):
        original_clean = " ".join((original or "").split())
        rewritten_clean = " ".join((rewritten or "").split())
        if not original_clean or not rewritten_clean:
            return False

        rewritten_lower = rewritten_clean.lower()
        if rewritten_lower.startswith(DISALLOWED_PREFIXES):
            return False

        if rewritten_clean.count("\n") > 1:
            return False

        if len(rewritten_clean) > max(
            len(original_clean) * MAX_REWRITE_EXPANSION_RATIO,
            len(original_clean) + MAX_REWRITE_ABSOLUTE_EXPANSION,
        ):
            return False

        original_tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9']+", original_clean)
            if any(char.isalpha() for char in token) and len(token) >= 4
        }
        rewritten_tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9']+", rewritten_clean)
            if any(char.isalpha() for char in token) and len(token) >= 4
        }
        added_tokens = rewritten_tokens - original_tokens
        if len(added_tokens) > max(3, len(original_tokens)):
            return False

        return True


class PostProcessingHandle:
    def __init__(self, update_callback):
        self.update_callback = update_callback
        self.enabled = DEFAULT_POSTPROCESS_ENABLED
        self.executable_path = DEFAULT_POSTPROCESS_EXECUTABLE
        self.model_path = DEFAULT_POSTPROCESS_MODEL_PATH
        self.timeout_seconds = DEFAULT_POSTPROCESS_TIMEOUT_SECONDS
        self.queue = queue.Queue()
        self.thread = None
        self.running = False
        self.backend = None
        self.raw_committed_text = ""
        self.processed_committed_text = ""
        self.status = self.get_runtime_status()

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.queue.put(None)
        if self.thread is not None:
            self.thread.join(timeout=2.0)
            self.thread = None

    def configure(self, enabled, executable_path=None, model_path=None):
        self.enabled = bool(enabled)
        self.executable_path = (executable_path or DEFAULT_POSTPROCESS_EXECUTABLE).strip() or DEFAULT_POSTPROCESS_EXECUTABLE
        self.model_path = (model_path or "").strip()
        self.backend = None
        self._drain_queue()
        if self.enabled:
            self.backend = LlamaCppCliBackend(
                executable_path=self.executable_path,
                model_path=self.model_path,
                timeout_seconds=self.timeout_seconds,
            )
        self._reset_state()
        self.status = self.get_runtime_status()
        return {
            "postprocess_enabled": self.enabled,
            "postprocess_executable_path": self.executable_path,
            "postprocess_model_path": self.model_path,
            "postprocess_state": self.status["postprocess_state"],
            "postprocess_message": self.status["postprocess_message"],
        }

    def submit_translation_update(self, update, status):
        if self.running:
            self.queue.put((update, dict(status)))
            return
        processed_update, processed_status = self._process_update(update, status)
        self.update_callback(processed_update, processed_status)

    def get_runtime_status(self):
        if not self.enabled:
            return {
                "postprocess_enabled": False,
                "postprocess_backend": "none",
                "postprocess_state": "disabled",
                "postprocess_message": "GGUF post-processing disabled",
            }

        if self.backend is None:
            return {
                "postprocess_enabled": True,
                "postprocess_backend": "llama_cpp_cli",
                "postprocess_state": "error",
                "postprocess_message": "Backend not initialized",
            }

        is_ready, message = self.backend.validate()
        return {
            "postprocess_enabled": True,
            "postprocess_backend": "llama_cpp_cli",
            "postprocess_state": "ready" if is_ready else "error",
            "postprocess_message": message,
        }

    def _run_loop(self):
        while self.running:
            item = self.queue.get()
            if item is None:
                break
            update, status = item
            processed_update, processed_status = self._process_update(update, status)
            self.update_callback(processed_update, processed_status)

    def _process_update(self, update, status):
        status_payload = dict(status)
        status_payload.update(self.get_runtime_status())

        if not self.enabled:
            return update, status_payload

        if not update.committed_text.startswith(self.raw_committed_text):
            self.raw_committed_text = update.committed_text
            self.processed_committed_text = update.committed_text
            status_payload.update(
                {
                    "postprocess_state": "warning",
                    "postprocess_message": "Post-processing resynchronized in passthrough mode",
                }
            )
            return self._rebuild_update(update, committed_append=update.committed_append, clause=update.clause), status_payload

        processed_append = ""
        clause = None
        if update.committed_append:
            before_len = len(self.processed_committed_text)
            try:
                processed_append = self._process_committed_append(update.committed_append, status_payload)
                status_payload.update(self.get_runtime_status())
            except Exception as exc:
                processed_append = update.committed_append
                status_payload.update(
                    {
                        "postprocess_state": "error",
                        "postprocess_message": f"GGUF post-processing failed: {exc}",
                    }
                )

            self.raw_committed_text = update.committed_text
            self.processed_committed_text = f"{self.processed_committed_text}{processed_append}"
            if update.clause is not None:
                clause = ClauseInfo(
                    clause_id=update.clause.clause_id,
                    is_final_clause=update.clause.is_final_clause,
                    char_start=before_len,
                    char_end=len(self.processed_committed_text),
                )
        else:
            self.raw_committed_text = update.committed_text
            clause = update.clause

        return self._rebuild_update(update, committed_append=processed_append, clause=clause), status_payload

    def _process_committed_append(self, text, status):
        if self.backend is None:
            return text

        leading = len(text) - len(text.lstrip())
        trailing = len(text) - len(text.rstrip())
        core = text.strip()
        if not core:
            return text

        rewritten = self.backend.process_text(core, status)
        if not rewritten:
            return text

        return f"{text[:leading]}{rewritten}{text[len(text) - trailing:] if trailing else ''}"

    def _rebuild_update(self, update, committed_append, clause):
        committed_text = self.processed_committed_text
        provisional_text = committed_text
        if update.provisional_text.startswith(update.committed_text):
            provisional_suffix = update.provisional_text[len(update.committed_text):]
            provisional_text = f"{committed_text}{provisional_suffix}"

        return TranslationUpdate(
            revision_id=update.revision_id,
            commit_id=update.commit_id,
            provisional_text=provisional_text,
            committed_text=committed_text,
            committed_append=committed_append,
            detected_language=update.detected_language,
            clause=clause,
            audio_start_ms=update.audio_start_ms,
            audio_end_ms=update.audio_end_ms,
        )

    def _reset_state(self):
        self.raw_committed_text = ""
        self.processed_committed_text = ""

    def _drain_queue(self):
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                return
            if item is None:
                self.queue.put(None)
                return
