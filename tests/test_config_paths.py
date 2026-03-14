import os
import unittest
from pathlib import Path

from config import (
    HUGGINGFACE_CACHE_DIR,
    HUGGINGFACE_HUB_CACHE_DIR,
    KOKORO_MODEL_CACHE_DIR,
    MODEL_CACHE_DIR,
    TRANSFORMERS_CACHE_DIR,
    WHISPER_MODEL_CACHE_DIR,
    ensure_local_model_cache_dirs,
)


class ConfigPathTests(unittest.TestCase):
    def test_local_model_cache_directories_exist(self):
        ensure_local_model_cache_dirs()

        for directory in (
            MODEL_CACHE_DIR,
            WHISPER_MODEL_CACHE_DIR,
            HUGGINGFACE_CACHE_DIR,
            HUGGINGFACE_HUB_CACHE_DIR,
            TRANSFORMERS_CACHE_DIR,
            KOKORO_MODEL_CACHE_DIR,
        ):
            with self.subTest(directory=directory):
                self.assertTrue(directory.exists())
                self.assertTrue(directory.is_dir())

    def test_model_cache_environment_variables_point_to_project_local_paths(self):
        ensure_local_model_cache_dirs()

        expected = {
            "HF_HOME": HUGGINGFACE_CACHE_DIR,
            "HUGGINGFACE_HUB_CACHE": HUGGINGFACE_HUB_CACHE_DIR,
            "TRANSFORMERS_CACHE": TRANSFORMERS_CACHE_DIR,
            "KOKORO_CACHE_DIR": KOKORO_MODEL_CACHE_DIR,
        }

        for env_var, expected_path in expected.items():
            with self.subTest(env_var=env_var):
                resolved_env = Path(os.environ[env_var]).resolve()
                self.assertEqual(resolved_env, expected_path.resolve())


if __name__ == "__main__":
    unittest.main()
