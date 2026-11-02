"""投喂的三道闸门：太大、不是图、太多。

这三条都是"用户真的会踩到"的路径：手机截图单张 2–5MB，微信导出的常常是 webp，
批量传时一次拖 15 张很常见。闸门开错的表现是——
  · 太大没挡住 → 磁盘被塞满、请求超时，用户只看到"转圈"
  · 不是图没挡住 → 抽取阶段才失败，白排一次队
  · 太多没挡住 → 一次事务里塞 50 张，接口 30 秒不返回

每条都断言"**不落库**"：失败请求不该在 raw_inputs 里留下半条记录。
"""

import base64
import shutil
from collections.abc import Iterator
from pathlib import Path
from tempfile import mkdtemp

import httpx
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models.input import RawInput
from app.services import ingest as ingest_service
from tests.conftest import Account, csrf_headers, login, rls_session

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def isolated_upload_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """上传根目录指到临时目录，失败用例也不能在真实 data/ 里留东西。"""
    root = Path(mkdtemp(prefix="keeper-limits-"))
    monkeypatch.setattr(settings, "data_dir", str(root))
    yield
    shutil.rmtree(root, ignore_errors=True)


async def _raw_input_count(user: Account) -> int:
    async with rls_session(user.id) as session:
        return int(await session.scalar(select(func.count()).select_from(RawInput)) or 0)


async def test_oversized_image_is_rejected(
    client: httpx.AsyncClient, single_user: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单张超过上限 → 413 FILE_TOO_LARGE，且不落库。"""
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    csrf = await login(client, single_user)

    too_big = PNG_1PX + b"\x00" * (1024 * 1024)  # 约 1MB + 1 字节
    response = await client.post(
        "/api/ingest/image",
        files=[("files", ("huge.png", too_big, "image/png"))],
        headers=csrf_headers(csrf),
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert await _raw_input_count(single_user) == 0


async def test_non_image_is_rejected(client: httpx.AsyncClient, single_user: Account) -> None:
    """改后缀骗不过去：按字节嗅探，不看 content-type 也不看文件名。"""
    csrf = await login(client, single_user)

    response = await client.post(
        "/api/ingest/image",
        files=[("files", ("fake.png", b"this is plain text, not an image", "image/png"))],
        headers=csrf_headers(csrf),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert await _raw_input_count(single_user) == 0


async def test_too_many_files_is_rejected(
    client: httpx.AsyncClient, single_user: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一次超过 10 张 → 400，提示里说清上限。"""
    monkeypatch.setattr(settings, "max_upload_count", 10)
    csrf = await login(client, single_user)

    files = [("files", (f"shot-{index}.png", PNG_1PX, "image/png")) for index in range(11)]
    response = await client.post("/api/ingest/image", files=files, headers=csrf_headers(csrf))

    assert response.status_code == 400
    assert "最多 10 张" in response.json()["error"]["message"]
    assert await _raw_input_count(single_user) == 0


async def test_empty_upload_is_rejected(client: httpx.AsyncClient, single_user: Account) -> None:
    """一张都没带 → 400，不是 500。"""
    csrf = await login(client, single_user)

    response = await client.post("/api/ingest/image", files=[], headers=csrf_headers(csrf))

    # FastAPI 对"必填文件缺失"给校验错误，业务层再兜一次 count == 0
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert await _raw_input_count(single_user) == 0


async def test_upload_requires_csrf(client: httpx.AsyncClient, single_user: Account) -> None:
    """上传是写操作，必须过 CSRF——否则第三方站点能借你的登录态塞图。"""
    await login(client, single_user)

    response = await client.post(
        "/api/ingest/image",
        files=[("files", ("shot.png", PNG_1PX, "image/png"))],
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert await _raw_input_count(single_user) == 0


async def test_exactly_at_limit_is_accepted(
    client: httpx.AsyncClient, single_user: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """边界不能一刀切太狠：正好 10 张必须能传（用户真的会一次传 10 张）。"""
    monkeypatch.setattr(settings, "max_upload_count", 10)

    # 后台抽取不归这里管：不挡掉会去调真实模型（没 Key 也要等超时），拖慢用例
    async def _skip(raw_input_id: int, user_id: int, forced_person_id: int | None = None) -> None:
        return None

    monkeypatch.setattr(ingest_service, "run_extraction", _skip)
    csrf = await login(client, single_user)

    # 内容必须各不相同：同一张图会被 sha256 去重成 1 条，测不到"10 张都收下"
    files = [
        ("files", (f"shot-{index}.png", PNG_1PX + bytes([index]), "image/png"))
        for index in range(10)
    ]
    response = await client.post("/api/ingest/image", files=files, headers=csrf_headers(csrf))

    assert response.status_code == 202
    assert response.json()["data"]["queued"] == 10
    assert await _raw_input_count(single_user) == 10
