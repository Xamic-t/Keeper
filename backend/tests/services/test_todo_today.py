"""待办与今日聚合的 service 单测。

覆盖 specs/10-todos.md 与 specs/11-today.md 的验收标准。
"""

from datetime import UTC, date, datetime

import pytest

from app.core.errors import AppError, ErrorCode
from app.services import today as today_service
from app.services import todo as todo_service
from app.services.person import create_person
from tests.conftest import Account, rls_session


async def _person(user: Account, nickname: str = "王姐") -> int:
    async with rls_session(user.id) as session:
        summary = await create_person(session, user.id, nickname=nickname, roles=["ge_jie"])
    assert summary.person.id is not None
    return summary.person.id


async def test_create_and_complete_todo(single_user: Account) -> None:
    async with rls_session(single_user.id) as session:
        todo = await todo_service.create_todo(
            session, single_user.id, title="回访王姐", due_date=date(2026, 5, 1)
        )
        todo_id = todo.id

    async with rls_session(single_user.id) as session:
        done = await todo_service.update_todo(session, todo_id, {"status": "done"})

    assert done.status == "done"
    assert done.done_at is not None

    async with rls_session(single_user.id) as session:
        reopened = await todo_service.update_todo(session, todo_id, {"status": "open"})
    assert reopened.done_at is None


async def test_list_todos_defaults_to_open(single_user: Account) -> None:
    async with rls_session(single_user.id) as session:
        await todo_service.create_todo(session, single_user.id, title="还没做")
        finished = await todo_service.create_todo(session, single_user.id, title="做完了")
        await todo_service.update_todo(session, finished.id, {"status": "done"})

    async with rls_session(single_user.id) as session:
        items = await todo_service.list_todos(session)

    assert [item.title for item in items] == ["还没做"]


async def test_ensure_daily_template_is_idempotent(single_user: Account) -> None:
    today = date(2026, 5, 1)

    async with rls_session(single_user.id) as session:
        created = await todo_service.ensure_daily_template(session, single_user.id, today)

    async with rls_session(single_user.id) as session:
        again = await todo_service.ensure_daily_template(session, single_user.id, today)

    assert created == 3
    assert again == 0


async def test_todo_with_person_cannot_be_read_by_others(
    two_users: tuple[Account, Account],
) -> None:
    user_a, user_b = two_users
    person_id = await _person(user_a)

    async with rls_session(user_a.id) as session:
        await todo_service.create_todo(
            session, user_a.id, title="给王姐回消息", person_id=person_id
        )

    async with rls_session(user_b.id) as session:
        items = await todo_service.list_todos(session)

    assert items == []


async def test_update_missing_todo_returns_404(single_user: Account) -> None:
    async with rls_session(single_user.id) as session:
        with pytest.raises(AppError) as error:
            await todo_service.update_todo(session, 999_999_999, {"status": "done"})

    assert error.value.code is ErrorCode.NOT_FOUND


def test_rank_tasks_puts_reminders_first() -> None:
    tasks = [
        today_service.TaskItem(type="todo", title="手动任务", priority=5),
        today_service.TaskItem(type="reminder", title="生日提醒", priority=2),
        today_service.TaskItem(type="reminder", title="今天提醒", priority=0),
    ]

    ranked, more = today_service.rank_tasks(tasks)

    assert [item.title for item in ranked] == ["今天提醒", "生日提醒", "手动任务"]
    assert more == 0


def test_rank_tasks_truncates_to_three_and_counts_rest() -> None:
    tasks = [
        today_service.TaskItem(type="todo", title=f"任务 {index}", priority=5) for index in range(8)
    ]

    ranked, more = today_service.rank_tasks(tasks)

    assert len(ranked) == 3
    assert more == 5


def test_rank_tasks_on_empty_list() -> None:
    ranked, more = today_service.rank_tasks([])

    assert ranked == []
    assert more == 0


async def test_get_today_returns_full_structure(single_user: Account) -> None:
    async with rls_session(single_user.id) as session:
        view = await today_service.get_today(session, single_user.id, date(2026, 5, 1))

    assert view.date == date(2026, 5, 1)
    assert view.pending_count == 0
    assert view.pending_preview is None
    assert view.recent == []
    assert view.degraded is False
    # 模板任务被补上了
    assert len(view.tasks) == 3


async def test_get_today_counts_pending_facts(single_user: Account) -> None:
    person_id = await _person(single_user)

    async with rls_session(single_user.id) as session:
        from app.models.fact import Fact

        session.add(
            Fact(
                user_id=single_user.id,
                person_id=person_id,
                content="做建材生意",
                category="structure",
                confidence=0.9,
                weight=1.0,
                source_type="manual",
                evidence="原文",
                status="pending",
            )
        )
        await session.commit()

    async with rls_session(single_user.id) as session:
        view = await today_service.get_today(session, single_user.id, date(2026, 5, 1))

    assert view.pending_count == 1
    assert view.pending_preview is not None
    assert view.pending_preview["content"] == "做建材生意"


async def test_today_templates_only_created_once(single_user: Account) -> None:
    today = date(2026, 5, 1)

    async with rls_session(single_user.id) as session:
        await today_service.get_today(session, single_user.id, today)
        view = await today_service.get_today(session, single_user.id, today)

    template_titles = {"复盘今日数据", "回复私信", "更新哥姐动态"}
    titles = {item.title for item in view.tasks}
    assert titles <= template_titles
    assert view.tasks_more == 0


async def test_delete_person_keeps_todo_with_null_person(
    two_users: tuple[Account, Account],
) -> None:
    """删人后任务还在，只是 person_id 变 NULL（specs/10 第 6 节）。"""
    user_a, _ = two_users
    person_id = await _person(user_a)

    async with rls_session(user_a.id) as session:
        await todo_service.create_todo(session, user_a.id, title="记得回消息", person_id=person_id)

    async with rls_session(user_a.id) as session:
        from app.services.person import delete_person

        await delete_person(session, person_id)

    async with rls_session(user_a.id) as session:
        items = await todo_service.list_todos(session)

    assert len(items) == 1
    assert items[0].person_id is None
    assert datetime.now(UTC).date() == datetime.now(UTC).date()
