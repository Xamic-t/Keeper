"""管理接口：建号的密码必须由调用方显式给出。

覆盖评审 P2-1：`UserCreateIn.password` 原来有个写死的默认值，而仓库是公开的，
等于把建号口令公开在 git 历史里。删掉默认值之后，缺字段必须被拒，
不只是"文档里写着必填"。

注意状态码是 **400** 不是 422 —— 项目统一把 Pydantic 校验失败映射成
`VALIDATION_ERROR`（`core/errors.py:90`），前端只认 `code`。
"""

import httpx
import pytest

from tests.conftest import Account, create_user, csrf_headers, delete_users, login


@pytest.fixture
def owner() -> Account:
    account = create_user("o", role="owner")
    yield account
    delete_users(account.id)


async def test_create_user_without_password_is_rejected(
    client: httpx.AsyncClient, owner: Account
) -> None:
    csrf_token = await login(client, owner)

    response = await client.post(
        "/api/admin/users",
        json={"username": "no-pass-user", "display_name": "无密码"},
        headers=csrf_headers(csrf_token),
    )

    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert any(item["field"].endswith("password") for item in body["detail"]["errors"])


async def test_create_user_with_password_succeeds(
    client: httpx.AsyncClient, owner: Account
) -> None:
    csrf_token = await login(client, owner)

    response = await client.post(
        "/api/admin/users",
        json={"username": "with-pass-user", "display_name": "有密码", "password": "Temp-pass-9931"},
        headers=csrf_headers(csrf_token),
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["username"] == "with-pass-user"
    # 建号只设初始密码，接口不回传任何密码字段
    assert "password" not in data

    delete_users(data["id"])
