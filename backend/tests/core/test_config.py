"""配置默认值：数据库角色与 SECRET_KEY 是两条安全底线。"""

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize("name", ["DATABASE_URL", "DATABASE_URL_MIGRATE", "SECRET_KEY"])
def test_database_defaults_use_safe_runtime_role(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """不显式配置时：运行时必须是 keeper_app，迁移才用 keeper。"""
    monkeypatch.delenv(name, raising=False)
    settings = Settings(secret_key="x" * 32, _env_file=None)

    assert settings.database_url.startswith("postgresql+psycopg://keeper_app:")
    assert settings.database_url_migrate.startswith("postgresql+psycopg://keeper:")


def test_secret_key_has_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """忘了配 SECRET_KEY 必须启动失败，不能 silently 用可预测默认值。"""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
