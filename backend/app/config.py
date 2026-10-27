"""应用配置。所有配置从环境变量或 .env 读取，代码里不出现硬编码。"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录的 .env（backend/app/config.py → 上三层）
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """全局配置。字段名小写，对应环境变量的大写形式。"""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    # ---- 应用 ----
    app_name: str = "Keeper"
    app_env: str = "dev"
    app_version: str = "0.1.0"

    # ---- 数据库 ----
    database_url: str = "postgresql+psycopg://keeper:keeper@localhost:5432/keeper"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    # 打印 SQL（会带出插入的数据，默认关闭）
    db_echo: bool = False

    # ---- 安全 ----
    secret_key: SecretStr = SecretStr("dev-only-secret-change-me")
    session_max_age: int = 604800  # 7 天，单位秒
    allow_registration: bool = False

    # ---- 模型 ----
    vision_provider: str = "deepseek"
    vision_model: str = "deepseek-flash"
    deepseek_api_key: SecretStr | None = None
    fallback_provider: str = "qwen"
    fallback_model: str = "qwen-vl-max"
    dashscope_api_key: SecretStr | None = None

    # ---- 推送 ----
    wecom_webhook: str | None = None
    pushplus_token: SecretStr | None = None

    @property
    def is_dev(self) -> bool:
        """是否开发环境。"""
        return self.app_env == "dev"


@lru_cache
def get_settings() -> Settings:
    """读取配置（单例，只解析一次 .env）。"""
    return Settings()


settings = get_settings()
