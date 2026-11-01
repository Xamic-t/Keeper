"""改待办：显式传 null 必须真的清空。

覆盖"假成功"：`PATCH /todos {"due_date": null}` 以前被 service 的
`if value is None: continue` 静默吃掉，接口返回 200、界面提示"日期已清掉"，
刷新又回来。用户以为删了，其实没删 —— 这种错比报错更贵。

关键区分：
  · 字段没出现在请求里 → `exclude_unset` 让它根本进不了 changes → 不动
  · 字段显式写 null     → 进 changes 且值为 None → 清空
"""

from datetime import date

from app.models.todo import Todo
from app.services import todo as todo_service
from tests.conftest import Account, rls_session


async def _make_todo(user: Account, due: date | None = None) -> int:
    async with rls_session(user.id) as session:
        todo = await todo_service.create_todo(
            session, user.id, title="清空日期测试", due_date=due, detail="原始描述"
        )
        assert todo.id is not None
        return todo.id


async def test_explicit_null_clears_due_date(single_user: Account) -> None:
    todo_id = await _make_todo(single_user, due=date(2030, 6, 1))

    async with rls_session(single_user.id) as session:
        updated = await todo_service.update_todo(session, todo_id, {"due_date": None})

    assert updated.due_date is None

    # 再从库里读一次，确认不是只在内存对象上清掉
    async with rls_session(single_user.id) as session:
        fresh = await session.get(Todo, todo_id)
        assert fresh is not None
        assert fresh.due_date is None


async def test_missing_field_leaves_value_alone(single_user: Account) -> None:
    """没传的字段不能被当成"传了 null"。"""
    todo_id = await _make_todo(single_user, due=date(2030, 6, 2))

    async with rls_session(single_user.id) as session:
        updated = await todo_service.update_todo(session, todo_id, {"title": "改了标题"})

    assert updated.title == "改了标题"
    assert updated.due_date == date(2030, 6, 2)
    assert updated.detail == "原始描述"


async def test_explicit_null_clears_detail_and_person(single_user: Account) -> None:
    todo_id = await _make_todo(single_user, due=date(2030, 6, 3))

    async with rls_session(single_user.id) as session:
        updated = await todo_service.update_todo(
            session, todo_id, {"detail": None, "person_id": None, "ai_reason": None}
        )

    assert updated.detail is None
    assert updated.person_id is None
    assert updated.ai_reason is None


async def test_null_status_is_ignored(single_user: Account) -> None:
    """status 是必填枚举，传 null 不能把状态改成空 —— 当作没传。"""
    todo_id = await _make_todo(single_user)

    async with rls_session(single_user.id) as session:
        updated = await todo_service.update_todo(session, todo_id, {"status": None})

    assert updated.status == "open"
