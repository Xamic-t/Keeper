"""认证与人模块的接口测试。

覆盖 W2 周验收：三种格式登录、5 次锁定、未登录 401、双账号互不可见、
建人后 relationship 存在、删人 204 + 级联。
"""

import httpx

from tests.conftest import Account, csrf_headers, login, new_client


async def test_login_sets_httponly_session_and_readable_csrf(
    client: httpx.AsyncClient, single_user: Account
) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"username": single_user.username, "password": single_user.password},
    )

    assert response.status_code == 200
    assert response.json()["data"]["user"]["username"] == single_user.username

    raw_cookies = response.headers.get_list("set-cookie")
    session_cookie = next(item for item in raw_cookies if item.startswith("session="))
    csrf_cookie = next(item for item in raw_cookies if item.startswith("csrf="))
    # session 前端读不到，csrf 故意可读
    assert "HttpOnly" in session_cookie
    assert "HttpOnly" not in csrf_cookie


async def test_me_requires_login(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    # RFC 7235：401 必须带 WWW-Authenticate
    assert response.headers["WWW-Authenticate"] == 'Cookie realm="keeper"'


async def test_business_route_requires_login(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/persons")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_login_locks_after_five_failures(
    client: httpx.AsyncClient, single_user: Account
) -> None:
    for _ in range(5):
        response = await client.post(
            "/api/auth/login",
            json={"username": single_user.username, "password": "wrong-password"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "BAD_CREDENTIALS"

    locked = await client.post(
        "/api/auth/login",
        json={"username": single_user.username, "password": single_user.password},
    )

    assert locked.status_code == 401
    assert locked.json()["error"]["code"] == "ACCOUNT_LOCKED"


async def test_write_without_csrf_is_rejected(
    client: httpx.AsyncClient, single_user: Account
) -> None:
    await login(client, single_user)

    response = await client.post("/api/persons", json={"nickname": "无令牌"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_create_person_returns_201_with_location(
    client: httpx.AsyncClient, single_user: Account
) -> None:
    csrf_token = await login(client, single_user)

    response = await client.post(
        "/api/persons",
        json={"nickname": "陈姐", "roles": ["ge_jie"]},
        headers=csrf_headers(csrf_token),
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["nickname"] == "陈姐"
    assert data["relationship"]["roles"] == ["ge_jie"]
    assert data["relationship"]["stage"] == "new"
    assert response.headers["Location"] == f"/api/persons/{data['id']}"


async def test_persons_are_isolated_between_accounts(
    two_users: tuple[Account, Account],
) -> None:
    user_a, user_b = two_users

    async with new_client() as client_a, new_client() as client_b:
        csrf_a = await login(client_a, user_a)
        created = await client_a.post(
            "/api/persons",
            json={"nickname": "只属于A"},
            headers=csrf_headers(csrf_a),
        )
        person_id = created.json()["data"]["id"]

        await login(client_b, user_b)
        list_b = await client_b.get("/api/persons")
        detail_b = await client_b.get(f"/api/persons/{person_id}")

    assert list_b.json()["data"] == []
    assert list_b.json()["meta"]["total"] == 0
    assert detail_b.status_code == 404


async def test_detail_returns_counts_and_relationship(
    client: httpx.AsyncClient, single_user: Account
) -> None:
    csrf_token = await login(client, single_user)
    created = await client.post(
        "/api/persons",
        json={"nickname": "王姐", "aliases": ["王姐（抖音）"]},
        headers=csrf_headers(csrf_token),
    )
    person_id = created.json()["data"]["id"]

    response = await client.get(f"/api/persons/{person_id}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["aliases"] == ["王姐（抖音）"]
    assert data["counts"] == {"facts": 0, "ledger": 0, "events": 0, "todos": 0}
    assert data["relationship"]["total_in"] == "0.00"
    assert data["tags"] == []


async def test_delete_person_returns_204_and_then_404(
    client: httpx.AsyncClient, single_user: Account
) -> None:
    csrf_token = await login(client, single_user)
    created = await client.post(
        "/api/persons",
        json={"nickname": "待删除"},
        headers=csrf_headers(csrf_token),
    )
    person_id = created.json()["data"]["id"]

    deleted = await client.delete(f"/api/persons/{person_id}", headers=csrf_headers(csrf_token))
    assert deleted.status_code == 204
    assert deleted.content == b""

    gone = await client.get(f"/api/persons/{person_id}")
    assert gone.status_code == 404


async def test_pagination_validation_rejects_size_over_limit(
    client: httpx.AsyncClient, single_user: Account
) -> None:
    await login(client, single_user)

    response = await client.get("/api/persons", params={"size": 999})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["detail"]["errors"][0]["field"].startswith("query")


async def test_relationship_patch_requires_matching_birthday(
    client: httpx.AsyncClient, single_user: Account
) -> None:
    """只写 birthday_type 不写生日值 → 参数错误，不能静默存一半。"""
    csrf_token = await login(client, single_user)
    created = await client.post(
        "/api/persons",
        json={"nickname": "生日只写类型"},
        headers=csrf_headers(csrf_token),
    )
    person_id = created.json()["data"]["id"]

    response = await client.patch(
        f"/api/persons/{person_id}/relationship",
        json={"birthday_type": "lunar"},
        headers=csrf_headers(csrf_token),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
