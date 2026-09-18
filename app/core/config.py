"""应用配置。从环境变量 / .env 读取。"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "制单自动化工具"
    debug: bool = True

    # 数据库
    database_url: str = "postgresql+psycopg2://postgres:replace_me@localhost:5432/zhidan"
    database_admin_url: str = ""

    # 安全
    secret_key: str = "development-only-placeholder-not-for-production"
    access_token_expire_minutes: int = 1440  # 24h，第一版不做刷新
    login_rate_limit_per_minute: int = 10
    api_rate_limit_per_minute: int = 240
    ocr_rate_limit_per_minute: int = 6

    # 上传
    upload_dir: str = "./uploads"
    max_upload_size_mb: int = 10

    # LLM 视觉识别
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_vision_model: str = ""
    llm_temperature: float = 0.1
    llm_request_timeout_seconds: float = 60
    llm_max_retries: int = 0

    # ERP
    erp_type: str = "gjp"
    erp_sid: str = ""
    erp_appkey: str = ""
    erp_appsecret: str = ""
    erp_base_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
