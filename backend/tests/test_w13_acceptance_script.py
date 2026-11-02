"""W13-P0：验收脚本的两道保护。

`test_extraction.py` 会**真的写事实 / 事件 / 往来**。它是本项目唯一一个
"跑一次就污染一个账号"的脚本，所以：
  ① 解析到 owner 且没 --allow-owner 时必须直接退出（不能靠人自觉）
  ② 除了给人看的核对表，还要能产出机器可读的 JSON（W17 的对比实验要吃它）

这里只测这两件事，不触发真实抽取（不调模型、不写业务数据）。
"""

import json
from collections.abc import Iterator
from pathlib import Path
from tempfile import mkdtemp

import pytest

from scripts.test_extraction import Item, _find_user, _write_json
from tests.conftest import create_user, delete_users


@pytest.fixture
def workspace() -> Iterator[Path]:
    root = Path(mkdtemp(prefix="keeper-w13-"))
    yield root
    import shutil

    shutil.rmtree(root, ignore_errors=True)


async def test_owner_guard_blocks_by_default() -> None:
    """不传 --user 时解析到 owner → 直接退出，而不是"帮你跑一下"。"""
    with pytest.raises(SystemExit) as exit_info:
        await _find_user(None)

    message = str(exit_info.value)
    assert "owner" in message
    assert "--allow-owner" in message, "提示里必须写清怎么继续"
    assert "--user" in message, "首选方案是换隔离账号"


async def test_owner_guard_allows_member_account() -> None:
    """隔离账号不受影响，且返回 role 供调用方再确认一次。"""
    account = create_user("w13")
    try:
        user_id, username, role = await _find_user(account.username)
    finally:
        delete_users(account.id)

    assert user_id == account.id
    assert username == account.username
    assert role == "member"


async def test_unknown_account_exits_with_hint() -> None:
    """账号名打错时报错要能指出是名字的问题，而不是一堆 SQL 报错。"""
    with pytest.raises(SystemExit) as exit_info:
        await _find_user("no-such-account-w13")

    assert "找不到账号" in str(exit_info.value)


def _items() -> list[Item]:
    """两条典型结果：一条正常、一条归属没判出来。"""
    return [
        Item(
            index=1,
            filename="a.png",
            raw_input_id=11,
            status="done",
            person_id=7,
            person_name="王姐",
            segments=3,
            facts=[{"id": 1, "content": "喜欢白茶", "category": "preference", "confidence": 0.9}],
            events=[{"id": 1, "title": "生日", "type": "birthday", "occurs_on": "2026-11-06"}],
            ledgers=[{"id": 1, "direction": "收", "kind": "gift", "amount": "520.00"}],
        ),
        Item(
            index=2,
            filename="b.png",
            raw_input_id=12,
            status="done",
            person_id=None,
            segments=2,
        ),
    ]


def test_json_summary_counts_each_class(workspace: Path) -> None:
    """JSON 里的计数要能直接算准确率：张数、事实/事件/往来、未归属。"""
    out = workspace / "nested" / "result.json"
    _write_json(_items(), out, (1, "tester", "member"), "deepseek-flash")

    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["summary"] == {
        "images": 2,
        "done": 2,
        "duplicate": 0,
        "failed": 0,
        "skipped": 0,
        "facts": 1,
        "events": 1,
        "ledgers": 1,
        "unassigned": 1,
    }
    assert payload["user"] == {"id": 1, "username": "tester", "role": "member"}
    assert payload["model"] == "deepseek-flash"


def test_json_item_carries_everything_needed_to_audit(workspace: Path) -> None:
    """每张图都要能独立审计：状态、重复标记、错误原文、归属、三类成果。"""
    out = workspace / "result.json"
    _write_json(_items(), out, (1, "tester", "member"), "deepseek-flash")

    payload = json.loads(out.read_text(encoding="utf-8"))
    first = payload["items"][0]

    assert first["filename"] == "a.png"
    assert first["status"] == "done"
    assert first["person_name"] == "王姐"
    assert first["facts"][0]["content"] == "喜欢白茶"
    assert first["events"][0]["occurs_on"] == "2026-11-06"
    assert first["ledgers"][0]["amount"] == "520.00"
    assert "duplicate" in first and "error" in first


def test_json_is_utf8_not_escaped(workspace: Path) -> None:
    """中文不许被转成 \\uXXXX：这份 JSON 人也要能读。"""
    out = workspace / "result.json"
    _write_json(_items(), out, (1, "tester", "member"), "deepseek-flash")

    raw = out.read_text(encoding="utf-8")
    assert "王姐" in raw
    assert "\\u" not in raw
