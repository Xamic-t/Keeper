"""投喂落盘后 commit 失败：磁盘上不能留孤儿文件。

真实场景：图已经写进 `data/uploads/{user_id}/`，然后 commit 因为连接断开 /
约束冲突 / 磁盘满失败。这一批 raw_inputs 一行都没进库，
但文件留在磁盘上——用户重传同一张图，去重查不到（库里没有），又写一份新的；
越失败越堆垃圾，而且这些文件没有任何一行能指向它们，删人都删不掉。

这里模拟 commit 抛错，断言：
  ① 异常照常抛出去（不许吞掉变成"上传成功"）
  ② 已落盘的文件被删干净
  ③ 库里没有这一批的行（rollback 生效）
"""

import shutil
from collections.abc import Iterator
from pathlib import Path
from tempfile import mkdtemp

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models.input import RawInput
from app.services import ingest as ingest_service
from app.services import storage
from tests.conftest import Account

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c6b0000000500015d1d8a0e0000000049454e44ae426082"
)


@pytest.fixture
def upload_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """把上传根目录指到临时目录，断言文件系统时不碰真实 data/。

    不用 `tmp_path`：这台机器上它会撞沙箱 shim（见 tests/api/test_api_contracts.py）。
    """
    root = Path(mkdtemp(prefix="keeper-orphan-"))
    monkeypatch.setattr(settings, "data_dir", str(root))
    yield root / "uploads"
    shutil.rmtree(root, ignore_errors=True)


async def test_commit_failure_removes_written_files(
    single_user: Account, upload_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = ingest_service.build_upload_item("shot.png", PNG_1PX)
    assert item.suffix == "png"

    async with SessionLocal() as session:
        session.info["rls_user_id"] = single_user.id

        async def _boom() -> None:
            raise RuntimeError("commit failed (simulated)")

        monkeypatch.setattr(session, "commit", _boom)

        with pytest.raises(RuntimeError, match="commit failed"):
            await ingest_service.ingest_images(session, single_user.id, [item])

    assert not storage.upload_root().joinpath(str(single_user.id)).exists() or not any(
        storage.upload_root().joinpath(str(single_user.id)).iterdir()
    ), "commit 失败后文件还在，成为孤儿"


async def test_commit_failure_leaves_no_rows(single_user: Account, upload_dir: Path) -> None:
    """对照组：正常情况下同一份内容确实能落库，证明上一条不是"什么都没发生"。"""
    item = ingest_service.build_upload_item("shot.png", PNG_1PX)

    async with SessionLocal() as session:
        session.info["rls_user_id"] = single_user.id
        results = await ingest_service.ingest_images(session, single_user.id, [item])
        assert results[0].status == "pending"

    async with SessionLocal() as session:
        session.info["rls_user_id"] = single_user.id
        count = await session.scalar(select(func.count()).select_from(RawInput))
        assert count == 1
        saved = await session.scalar(select(RawInput.file_path))
        assert saved is not None

    # 文件确实写到了隔离目录里
    assert storage.upload_root().joinpath(str(single_user.id)).is_dir()

    # 清掉这轮测试留下的文件，别让它跟着临时目录外的路径跑
    storage.delete_file(saved)
