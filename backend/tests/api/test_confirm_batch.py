"""批量确认的上限要"说人话"。

问题：`BatchIn.fact_ids` 原来写 `max_length=50`，前端"全部确认"一次发全部。
超过 50 条时返回的是 Pydantic 的 422/400，body 里只有 `list 太长`，
用户不知道该做什么，前端也没法把这句话翻译成行动。

改法：上限仍是 200（保护事务），但判定挪进 handler，超了返回中文提示。
这个用例同时钉住"上限还在"，避免以后被人顺手删掉。
"""

import httpx
import pytest
from sqlalchemy import select

from app.api.confirmations import BATCH_LIMIT
from app.models.fact import Fact
from app.models.person import Person
from tests.conftest import Account, create_user, csrf_headers, delete_users, login, rls_session


@pytest.fixture
def account() -> Account:
    user = create_user("cb")
    yield user
    delete_users(user.id)


async def _make_pending_facts(user: Account, count: int) -> list[int]:
    """直接写库造 pending 事实。走接口要截图要模型，这里只验上限逻辑。"""
    async with rls_session(user.id) as session:
        person = Person(user_id=user.id, nickname="批量确认测试")
        session.add(person)
        await session.flush()

        ids: list[int] = []
        for index in range(count):
            fact = Fact(
                user_id=user.id,
                person_id=person.id,
                content=f"第 {index} 条待确认",
                category="preference",
                confidence=0.9,
                source_type="manual",
            )
            session.add(fact)
            await session.flush()
            ids.append(fact.id)
        await session.commit()
        return ids


async def test_batch_keeps_upper_limit(client: httpx.AsyncClient, account: Account) -> None:
    ids = await _make_pending_facts(account, BATCH_LIMIT + 1)
    csrf_token = await login(client, account)

    response = await client.post(
        "/api/confirmations/batch",
        json={"fact_ids": ids},
        headers=csrf_headers(csrf_token),
    )

    assert response.status_code == 400
    message = response.json()["error"]["message"]
    assert "分批" in message
    assert str(BATCH_LIMIT) in message


async def test_batch_at_limit_succeeds(client: httpx.AsyncClient, account: Account) -> None:
    ids = await _make_pending_facts(account, BATCH_LIMIT)
    csrf_token = await login(client, account)

    response = await client.post(
        "/api/confirmations/batch",
        json={"fact_ids": ids},
        headers=csrf_headers(csrf_token),
    )

    assert response.status_code == 200
    assert response.json()["data"]["confirmed"] == BATCH_LIMIT

    # 真的落库了，不是只返回个数
    async with rls_session(account.id) as session:
        statuses = (
            (await session.execute(select(Fact.status).where(Fact.id.in_(ids)))).scalars().all()
        )
    assert set(statuses) == {"confirmed"}
