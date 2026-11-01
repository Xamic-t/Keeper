"""材料可以「忽略」，原图开关真的会删图。

两件以前做不到的事：
  ① 归属待定的材料只能干看着，没有任何办法让它从列表里消失（没有 dismiss 路由）
  ② `keep_original_images` 开关全项目 0 处读取，勾了没有任何效果 —— 对隐私撒谎
"""

import tempfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.models.fact import Fact
from app.models.input import RawInput
from app.models.person import Person
from app.services import extract as extract_service
from tests.conftest import Account, create_user, csrf_headers, delete_users, login, rls_session


@pytest.fixture
def account() -> Account:
    user = create_user("dm")
    yield user
    delete_users(user.id)


@pytest.fixture
def workspace() -> Iterator[Path]:
    """自己的临时目录。

    不用 pytest 的 tmp_path：这台机器上它建目录时会撞上环境层的
    FileExistsError（沙箱 shim 把 mkdir 转给宿主执行），与本项目无关。
    """
    path = Path(tempfile.mkdtemp(prefix="keeper-dismiss-"))
    yield path
    for child in path.iterdir():
        child.unlink()
    path.rmdir()


async def _make_raw_input(user: Account, status: str = "done", person_id: int | None = None) -> int:
    async with rls_session(user.id) as session:
        raw = RawInput(
            user_id=user.id,
            kind="text",
            raw_text="一堆看不出是谁的话",
            status=status,
            person_id=person_id,
        )
        session.add(raw)
        await session.commit()
        return raw.id


async def test_dismiss_marks_ignored(client: httpx.AsyncClient, account: Account) -> None:
    raw_id = await _make_raw_input(account)
    csrf_token = await login(client, account)

    response = await client.post(
        f"/api/raw-inputs/{raw_id}/dismiss", headers=csrf_headers(csrf_token)
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ignored"

    async with rls_session(account.id) as session:
        fresh = await session.get(RawInput, raw_id)
        assert fresh is not None
        assert fresh.status == "ignored"


async def test_dismiss_rejects_pending_facts_of_that_input(
    client: httpx.AsyncClient, account: Account
) -> None:
    """忽略一条材料，它下面的待确认事实也要一起驳回，否则确认页永远堵着。"""
    async with rls_session(account.id) as session:
        person = Person(user_id=account.id, nickname="忽略测试")
        session.add(person)
        await session.flush()
        raw = RawInput(user_id=account.id, kind="text", raw_text="x", status="done")
        session.add(raw)
        await session.flush()
        session.add(
            Fact(
                user_id=account.id,
                person_id=person.id,
                content="这条应该被驳回",
                category="preference",
                confidence=0.8,
                source_type="screenshot",
                source_id=raw.id,
            )
        )
        await session.commit()
        raw_id = raw.id

    csrf_token = await login(client, account)
    await client.post(f"/api/raw-inputs/{raw_id}/dismiss", headers=csrf_headers(csrf_token))

    async with rls_session(account.id) as session:
        statuses = (
            (await session.execute(select(Fact.status).where(Fact.source_id == raw_id)))
            .scalars()
            .all()
        )
    assert statuses == ["rejected"]


async def test_dismiss_is_idempotent(client: httpx.AsyncClient, account: Account) -> None:
    raw_id = await _make_raw_input(account)
    csrf_token = await login(client, account)

    url = f"/api/raw-inputs/{raw_id}/dismiss"
    headers = csrf_headers(csrf_token)
    first = await client.post(url, headers=headers)
    second = await client.post(url, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["status"] == "ignored"


async def test_drop_image_deletes_file_when_setting_off(
    account: Account, workspace: Path
) -> None:
    """开关关掉时，识别完必须真把文件从磁盘删掉，并把 file_path 置空。"""
    image = workspace / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n fake")

    async with rls_session(account.id) as session:
        # 直接改开关：默认是 true（保留）
        from app.models.user import User

        user = await session.get(User, account.id)
        assert user is not None
        user.keep_original_images = False

        raw = RawInput(
            user_id=account.id,
            kind="screenshot",
            file_path=str(image),
            status="done",
        )
        session.add(raw)
        await session.commit()

        await extract_service._drop_image_if_disabled(session, raw, account.id)

        assert not image.exists(), "文件应该真的被删掉"
        assert raw.file_path is None
        assert raw.file_deleted_at is not None


async def test_drop_image_keeps_file_when_setting_on(account: Account, workspace: Path) -> None:
    image = workspace / "keep.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n fake")

    async with rls_session(account.id) as session:
        raw = RawInput(
            user_id=account.id,
            kind="screenshot",
            file_path=str(image),
            status="done",
        )
        session.add(raw)
        await session.commit()

        await extract_service._drop_image_if_disabled(session, raw, account.id)

        assert image.exists(), "开关是开的，文件必须留着"
        assert raw.file_path == str(image)


async def test_drop_image_tolerates_missing_file(account: Account, workspace: Path) -> None:
    """文件早就没了（手工删过），不能因此报错。"""
    async with rls_session(account.id) as session:
        from app.models.user import User

        user = await session.get(User, account.id)
        assert user is not None
        user.keep_original_images = False

        raw = RawInput(
            user_id=account.id,
            kind="screenshot",
            file_path=str(workspace / "not-there.png"),
            status="done",
        )
        session.add(raw)
        await session.commit()

        await extract_service._drop_image_if_disabled(session, raw, account.id)
        assert raw.file_path is None
