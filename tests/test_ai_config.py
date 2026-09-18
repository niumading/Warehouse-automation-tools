"""AI API 配置保存测试，不接触真实密钥或配置文件。"""
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import ai_config
from app.core.ai_config import AiConfig


class AiConfigTest(unittest.TestCase):
    def test_new_api_key_replaces_previous_value(self):
        current = AiConfig("https://old.example/v1", "old-vision", "old-secret")
        with patch.object(ai_config, "get_ai_config", return_value=current), patch.object(
            Path, "mkdir"
        ), patch.object(Path, "write_text") as write, patch.object(ai_config.os, "replace"):
            updated = ai_config.save_ai_config(
                base_url="https://new.example/v1",
                vision_model="new-vision",
                api_key="new-secret",
            )
        self.assertEqual(updated.api_key, "new-secret")
        payload = write.call_args.args[0]
        self.assertIn('"api_key": "new-secret"', payload)
        self.assertNotIn("old-secret", payload)

    def test_blank_api_key_keeps_previous_value(self):
        current = AiConfig("https://api.example/v1", "vision", "keep-secret")
        with patch.object(ai_config, "get_ai_config", return_value=current), patch.object(
            Path, "mkdir"
        ), patch.object(Path, "write_text"), patch.object(ai_config.os, "replace"):
            updated = ai_config.save_ai_config(
                base_url=current.base_url,
                vision_model=current.vision_model,
                api_key="",
            )
        self.assertEqual(updated.api_key, "keep-secret")


if __name__ == "__main__":
    unittest.main()
