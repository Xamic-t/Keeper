"""应用配置。所有配置从环境变量或 .env 读取，代码里不出现硬编码。"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import SecretStr, model_validator
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
    # 应用运行时使用：普通角色（NOSUPERUSER NOBYPASSRLS），受 RLS 约束。
    # 默认从 APP_DB_PASSWORD 拼 keeper_app；不要在这里写 owner 账号。
    database_url: str | None = None
    # 迁移专用：owner 角色，需要 DDL 权限；默认从 DB_PASSWORD 拼 keeper。
    # compose 里必须显式覆盖成 db 主机名，见 docker-compose.yml 的 app.environment。
    database_url_migrate: str | None = None
    # 建库 owner 密码（仅用于拼迁移连接串；compose 的 db 服务也读这个变量）
    db_password: SecretStr = SecretStr("keeper")
    # 应用连接角色 keeper_app 的密码
    app_db_password: SecretStr = SecretStr("keeper")
    db_pool_size: int = 5
    db_max_overflow: int = 5
    # 打印 SQL（会带出插入的数据，默认关闭）
    db_echo: bool = False

    # ---- 安全 ----
    # 会话签名密钥：没有默认值。忘了配会在启动时直接失败，而不是 silently 用弱密钥。
    secret_key: SecretStr
    session_max_age: int = 604800  # 7 天，单位秒
    allow_registration: bool = False
    # 登录接口每 IP 每分钟允许的尝试次数（W3 起生效）
    login_rate_limit: int = 10

    # ---- 存储 ----
    # 上传原图落盘根目录。默认是项目根的 data/（与 cwd 无关，
    # 不然从 backend/ 启动就会落到 backend/data，跟文档说的路径对不上）
    data_dir: str = str(Path(__file__).resolve().parents[2] / "data")
    max_upload_mb: int = 8
    max_upload_count: int = 10

    # ---- 模型 ----
    vision_provider: str = "deepseek"
    vision_model: str = "deepseek-flash"
    vision_base_url: str = "https://api.deepseek.com/v1"
    deepseek_api_key: SecretStr | None = None
    fallback_provider: str = "qwen"
    fallback_model: str = "qwen-vl-max"
    fallback_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_api_key: SecretStr | None = None
    vision_timeout_s: float = 60.0

    # ---- 推送 ----
    wecom_webhook: str | None = None
    pushplus_token: SecretStr | None = None

    # ---- 助理（DeepSeek Harness 侧车）----
    # 侧车用这个 token 调只读接口，不传用户密码（ARCHITECTURE 19.7.4）。
    # 不配的话 /api/assistant/* 整组返回 503 —— 主流程一条都依赖它，所以不影响使用。
    assistant_token: SecretStr | None = None
    # token 代表哪个账号的数据。单用户部署就是 owner 账号。
    assistant_username: str = "Admin"

    @model_validator(mode="after")
    def build_database_urls(self) -> "Settings":
        """根据账号密码拼默认连接串。

        显式设置 DATABASE_URL / DATABASE_URL_MIGRATE 时完全尊重显式值；
        否则运行时默认 keeper_app（RLS 生效），迁移默认 keeper（有 DDL 权限）。
        """
        if self.database_url is None:
            password = quote(self.app_db_password.get_secret_value(), safe="")
            self.database_url = (
                f"postgresql+psycopg://keeper_app:{password}@localhost:5432/keeper"
            )
        if self.database_url_migrate is None:
            password = quote(self.db_password.get_secret_value(), safe="")
            self.database_url_migrate = (
                f"postgresql+psycopg://keeper:{password}@localhost:5432/keeper"
            )
        return self

    @property
    def is_dev(self) -> bool:
        """是否开发环境。"""
        return self.app_env == "dev"


@lru_cache
def get_settings() -> Settings:
    """读取配置（单例，只解析一次 .env）。"""
    return Settings()


settings = get_settings()
