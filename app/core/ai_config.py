"""运行时 AI 配置：优先读取管理员保存的项目内配置，回退到 .env。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path

from app.core.config import get_settings


PROJECT_DIR = Path(__file__).resolve().parents[2]
AI_CONFIG_PATH = PROJECT_DIR / "config" / "ai.json"


@dataclass(frozen=True)
class AiConfig:
    base_url: str
    vision_model: str
    api_key: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.vision_model)

    @property
    def api_key_hint(self) -> str | None:
        return f"••••{self.api_key[-4:]}" if self.api_key else None


def get_ai_config() -> AiConfig:
    settings = get_settings()
    values = {
        "base_url": settings.llm_base_url.strip(),
        "vision_model": settings.llm_vision_model.strip(),
        "api_key": settings.llm_api_key.strip(),
    }
    if AI_CONFIG_PATH.is_file():
        saved = json.loads(AI_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("AI 配置文件格式错误")
        for key in values:
            if isinstance(saved.get(key), str):
                values[key] = saved[key].strip()
    return AiConfig(**values)


def save_ai_config(*, base_url: str, vision_model: str, api_key: str | None = None) -> AiConfig:
    current = get_ai_config()
    updated = AiConfig(
        base_url=base_url.strip(),
        vision_model=vision_model.strip(),
        api_key=api_key.strip() if api_key and api_key.strip() else current.api_key,
    )
    AI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = AI_CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(asdict(updated), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, AI_CONFIG_PATH)
    return updated
