"""人模块 service 单测。不起 HTTP，直接调函数。

覆盖 specs/02-persons.md 第 6 节的 1/2/4/5/6 条验收。
"""

import pytest
from sqlalchemy import select

from app.core.errors import AppError, ErrorCode
from app.models.fact import Fact
from app.models.person import Person
from app.models.relationship import Relationship
from app.models.todo import Todo
from app.services.person import (
    create_person,
    delete_person,
    get_person_detail,
    list_persons,
    update_person,
    update_relationship,
)
from tests.conftest import Account, rls_session


async def _new_person(user: Account, nickname: str = "王姐") -> int:
    async with rls_session(user.id) as session:
        summary = await create_person(session, user.id, nickname=nickname, roles=["ge_jie"])
    assert summary.person.id is not None
    return summary.person.id


async def test_create_person_also_creates_relationship(two_users: tuple[Account, Account]) -> None:
    """persons 和 relationships 是 1:1，只建一半整个画像都是坏的。"""
    user_a, _ = two_users
    person_id = await _new_person(user_a)

    async with rls_session(user_a.id) as session:
        person = await session.get(Person, person_id)
        relationship = (
            await session.execute(select(Relationship).where(Relationship.person_id == person_id))
        ).scalar_one()

    assert person is not None
    assert person.nickname == "王姐"
    assert relationship.roles == ["ge_jie"]
    assert relationship.stage == "new"


async def test_list_persons_is_isolated(two_users: tuple[Account, Account]) -> None:
    """RLS 生效的证据：B 看不到 A 的人，连总数都是 0。"""
    user_a, user_b = two_users
    await _new_person(user_a, "只属于A")

    async with rls_session(user_b.id) as session:
        items, total = await list_persons(session, offset=0, limit=20)

    assert items == []
    assert total == 0


async def test_list_persons_searches_nickname_and_alias(single_user: Account) -> None:
    async with rls_session(single_user.id) as session:
        await create_person(
            session,
            single_user.id,
            nickname="张总",
            roles=["customer"],
            aliases=["张老板（抖音）"],
        )
        await create_person(session, single_user.id, nickname="王姐", roles=["ge_jie"])

    async with rls_session(single_user.id) as session:
        by_nickname, total_nickname = await list_persons(
            session, offset=0, limit=20, keyword="张总"
        )

    async with rls_session(single_user.id) as session:
        by_alias, total_alias = await list_persons(session, offset=0, limit=20, keyword="张老板")

    assert total_nickname == 1
    assert by_nickname[0].person.nickname == "张总"
    assert total_alias == 1
    assert by_alias[0].person.nickname == "张总"


async def test_list_persons_escapes_like_wildcards(single_user: Account) -> None:
    """关键词里的 % 不能当通配符——否则搜个 % 就把全表捞出来。"""
    await _new_person(single_user, "王%姐")
    await _new_person(single_user, "王姐")

    async with rls_session(single_user.id) as session:
        items, total = await list_persons(session, offset=0, limit=20, keyword="%")

    assert total == 1
    assert items[0].person.nickname == "王%姐"


async def test_get_person_detail_counts(two_users: tuple[Account, Account]) -> None:
    user_a, _ = two_users
    person_id = await _new_person(user_a)

    async with rls_session(user_a.id) as session:
        session.add(
            Fact(
                user_id=user_a.id,
                person_id=person_id,
                content="做建材生意",
                category="structure",
                confidence=0.9,
                source_type="manual",
            )
        )
        await session.commit()

    async with rls_session(user_a.id) as session:
        detail = await get_person_detail(session, person_id)

    assert detail.person.id == person_id
    assert detail.counts == {"facts": 1, "ledger": 0, "events": 0, "todos": 0}
    assert detail.tags == []


async def test_get_person_detail_hides_other_users_person(
    two_users: tuple[Account, Account],
) -> None:
    """越权返回 404 而不是 403——403 等于承认这个资源存在。"""
    user_a, user_b = two_users
    person_id = await _new_person(user_a)

    async with rls_session(user_b.id) as session:
        with pytest.raises(AppError) as error:
            await get_person_detail(session, person_id)

    assert error.value.code is ErrorCode.NOT_FOUND
    assert error.value.status_code == 404


async def test_update_person_patches_only_given_fields(single_user: Account) -> None:
    person_id = await _new_person(single_user)

    async with rls_session(single_user.id) as session:
        untouched = await update_person(session, person_id, {})

    assert untouched.person.nickname == "王姐"

    async with rls_session(single_user.id) as session:
        updated = await update_person(
            session,
            person_id,
            {"nickname": "王姐（建材）", "note": "做建材生意"},
        )

    assert updated.person.nickname == "王姐（建材）"
    assert updated.person.note == "做建材生意"


async def test_update_relationship_marks_stage_as_manual(single_user: Account) -> None:
    """手动改过阶段后，自动算法不再覆盖——靠 stage_source 判断。"""
    person_id = await _new_person(single_user)

    async with rls_session(single_user.id) as session:
        relationship = await update_relationship(session, person_id, {"stage": "core"})

    assert relationship.stage == "core"
    assert relationship.stage_source == "manual"
    assert relationship.stage_updated_at is not None


async def test_update_relationship_on_missing_person_returns_404(single_user: Account) -> None:
    async with rls_session(single_user.id) as session:
        with pytest.raises(AppError) as error:
            await update_relationship(session, 999_999_999, {"stage": "core"})

    assert error.value.code is ErrorCode.NOT_FOUND


async def test_delete_person_cascades_but_keeps_todos(single_user: Account) -> None:
    """删人是真删；唯一例外是待办——任务留着，只是失去归属。"""
    person_id = await _new_person(single_user)

    async with rls_session(single_user.id) as session:
        todo = Todo(user_id=single_user.id, person_id=person_id, title="回访", source="manual")
        session.add(todo)
        await session.commit()
        todo_id = todo.id

    async with rls_session(single_user.id) as session:
        await delete_person(session, person_id)

    async with rls_session(single_user.id) as session:
        assert await session.get(Person, person_id) is None
        assert (
            await session.execute(select(Relationship).where(Relationship.person_id == person_id))
        ).first() is None
        kept_todo = await session.get(Todo, todo_id)

    assert kept_todo is not None
    assert kept_todo.person_id is None


async def test_delete_missing_person_returns_404(single_user: Account) -> None:
    async with rls_session(single_user.id) as session:
        with pytest.raises(AppError) as error:
            await delete_person(session, 999_999_999)

    assert error.value.code is ErrorCode.NOT_FOUND
