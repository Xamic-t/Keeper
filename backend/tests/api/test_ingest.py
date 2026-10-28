"""投喂接口测试：上传、去重、状态查询。

覆盖 specs/03-ingest.md 第 6 节的验收标准。
"""

import base64

import httpx

from tests.conftest import Account, csrf_headers, login

# 1x1 像素的合法 PNG
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


async def test_upload_image_returns_202_and_queues(
    client: httpx.AsyncClient, single_user: Account
) -> None:
    csrf_token = await login(client, single_user)

    response = await client.post(
        "/api/ingest/image",
        files=[("files", ("shot.png", PNG_1PX, "image/png"))],
        headers=csrf_headers(csrf_token),
    )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["queued"] == 1
    assert data["items"][0]["duplicate"] is False
    assert data["items"][0]["raw_input_id"]


async def test_same_image_twice_is_deduplicated(
    client: httpx.AsyncClient, single_user: Account
) -> None:
    csrf_token = await login(client, single_user)

    first = await client.post(
        "/api/ingest/image",
        files=[("files", ("shot.png", PNG_1PX, "image/png"))],
        headers=csrf_headers(csrf_token),
    )
    second = await client.post(
        "/api/ingest/image",
        files=[("files", ("shot2.png", PNG_1PX, "image/png"))],
        headers=csrf_headers(csrf_token),
    )

    assert first.status_code == 202
    assert second.status_code == 202
    item = second.json()["data"]["items"][0]
    assert item["duplicate"] is True
    assert item["existing_id"] == first.json()["data"]["items"][0]["raw_input_id"]


async def test_non_image_is_rejected(client: httpx.AsyncClient, single_user: Account) -> None:
    csrf_token = await login(client, single_user)

    response = await client.post(
        "/api/ingest/image",
        files=[("files", ("note.txt", b"not an image", "text/plain"))],
        headers=csrf_headers(csrf_token),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_ingest_text_creates_material(
    client: httpx.AsyncClient, single_user: Account
) -> None:
    csrf_token = await login(client, single_user)

    response = await client.post(
        "/api/ingest/text",
        json={"text": "今天王姐说她女儿模考进了前二十"},
        headers=csrf_headers(csrf_token),
    )

    assert response.status_code == 202
    assert response.json()["data"]["queued"] == 1


async def test_raw_input_status_endpoint(client: httpx.AsyncClient, single_user: Account) -> None:
    csrf_token = await login(client, single_user)
    created = await client.post(
        "/api/ingest/text",
        json={"text": "一句话投喂"},
        headers=csrf_headers(csrf_token),
    )
    raw_input_id = created.json()["data"]["items"][0]["raw_input_id"]

    response = await client.get(f"/api/raw-inputs/{raw_input_id}")

    assert response.status_code == 200
    assert response.json()["data"]["id"] == raw_input_id


async def test_raw_input_requires_login(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/raw-inputs/1")

    assert response.status_code == 401


async def test_other_account_cannot_read_raw_input(
    client: httpx.AsyncClient, two_users: tuple[Account, Account]
) -> None:
    user_a, user_b = two_users
    csrf_a = await login(client, user_a)
    created = await client.post(
        "/api/ingest/text",
        json={"text": "只有 A 能看到"},
        headers=csrf_headers(csrf_a),
    )
    raw_input_id = created.json()["data"]["items"][0]["raw_input_id"]

    from tests.conftest import new_client

    async with new_client() as client_b:
        await login(client_b, user_b)
        response = await client_b.get(f"/api/raw-inputs/{raw_input_id}")

    assert response.status_code == 404
