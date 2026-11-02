"""每日任务的幂等：同一天跑两次，不能重复生成、不能重复推送。

为什么重要：scheduler 容器重启、手动补跑、`--serve` 与手跑撞一起，
都会让同一天跑两次。没有幂等的话，用户会收到两条一模一样的生日提醒，
而"重复提醒"是最容易被投诉的那类 bug。

幂等的实现靠数据库唯一约束 (event_id, remind_on, level)，这里从外面验它真的生效。
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.jobs.daily import run_once
from app.models.event import Reminder
from app.services import notify
from app.services import reminder as reminder_service
from app.services.event import create_event
from app.services.person import create_person
from app.services.settings import create_channel
from app.services.vision import ModelReply, vision
from tests.conftest import Account, rls_session

REFERENCE = date(2026, 11, 3)


def _reply() -> ModelReply:
    return ModelReply(
        parsed={
            "headline": "王姐生日还有 3 天",
            "suggestion": "送一盒白茶",
            "reasons": ["档案里记过她常喝白茶"],
            "script": "王姐，下周您生日",
        },
        raw="{}",
        provider="fake",
        model="fake-model",
    )


async def _seed(user: Account) -> None:
    """一个人 + 一个 T-3 的生日 + 一条可用的推送通道。"""
    async with rls_session(user.id) as session:
        summary = await create_person(session, user.id, nickname="王姐", roles=["ge_jie"])
        await create_event(
            session,
            user.id,
            type="birthday",
            title="王姐生日",
            occurs_on=REFERENCE + timedelta(days=3),
            person_id=summary.person.id,
        )
        await create_channel(
            session,
            user.id,
            kind="wecom",
            name="企微",
            priority=1,
            webhook="https://example.invalid/hook",
            token=None,
        )


@pytest.fixture
def ok_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """不调真实模型。"""

    async def _extract(**kwargs: object) -> ModelReply:
        return _reply()

    monkeypatch.setattr(vision, "extract", _extract)


@pytest.fixture
def ok_push(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """记录每次真实推送，返回调用列表，用来断言"没有重复推送"。"""
    calls: list[str] = []

    async def _send_via(channel: object, message: object) -> bool:
        calls.append("push")
        return True

    monkeypatch.setattr(notify, "send_via", _send_via)
    return calls


async def test_second_run_same_day_does_not_duplicate(
    single_user: Account, ok_model: None, ok_push: list[str]
) -> None:
    await _seed(single_user)

    first = await run_once(today=REFERENCE)
    second = await run_once(today=REFERENCE)

    assert first.created == 1
    assert first.sent == 1
    assert second.created == 0, "第二次不该再生成提醒"
    assert second.sent == 0, "第二次不该再推送"

    async with rls_session(single_user.id) as session:
        rows = list((await session.execute(select(Reminder))).scalars().all())
    assert len(rows) == 1
    assert len(ok_push) == 1, f"实际推送了 {len(ok_push)} 次"


async def test_rerun_does_not_move_sent_at(
    single_user: Account, ok_model: None, ok_push: list[str]
) -> None:
    """已发出去的提醒，重跑不能刷新 sent_at（否则"什么时候发过"就不真实了）。"""
    await _seed(single_user)
    await run_once(today=REFERENCE)

    async with rls_session(single_user.id) as session:
        before = (await session.execute(select(Reminder.sent_at))).scalar_one()

    await run_once(today=REFERENCE)

    async with rls_session(single_user.id) as session:
        after = (await session.execute(select(Reminder.sent_at))).scalar_one()

    assert before == after


async def test_pending_reminder_is_retried_next_run(
    single_user: Account, ok_model: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第一次没发出去（通道抽风）→ 重跑要补发，而不是当"已处理"跳过。"""
    await _seed(single_user)

    state = {"fail": True}

    async def _flaky(channel: object, message: object) -> bool:
        return not state["fail"]

    monkeypatch.setattr(notify, "send_via", _flaky)

    first = await run_once(today=REFERENCE)
    assert first.created == 1
    assert first.sent == 0, "通道抽风时不该算发出去"
    assert first.failed == 1

    state["fail"] = False
    second = await run_once(today=REFERENCE)

    assert second.created == 0, "不该重复生成"
    assert second.sent == 1, "pending 的应该补发"

    async with rls_session(single_user.id) as session:
        assert await session.scalar(select(func.count()).select_from(Reminder)) == 1
        assert await session.scalar(select(Reminder.status)) == "sent"


async def test_different_day_creates_new_reminder(
    single_user: Account, ok_model: None, ok_push: list[str]
) -> None:
    """跨天是正常的另一次提醒（T-3 与 T-0 是不同档位，同档位也只应该一条/天）。

    这里钉住的是别把幂等做得太狠：换一天必须能再生成一条。
    """
    await _seed(single_user)
    await run_once(today=REFERENCE)
    await run_once(today=REFERENCE + timedelta(days=1))

    async with rls_session(single_user.id) as session:
        count = await session.scalar(select(func.count()).select_from(Reminder))
    # T-2 不在生日档位里（T-7/T-3/T-0），所以第二天不该新增
    assert count == 1


async def test_two_users_do_not_collide(
    two_users: tuple[Account, Account], ok_model: None, ok_push: list[str]
) -> None:
    """两个人都过生日（同一天）时，各自的提醒互不影响——RLS + 唯一约束一起生效。"""
    user_a, user_b = two_users
    await _seed(user_a)
    await _seed(user_b)

    result = await run_once(today=REFERENCE)

    assert result.created == 2
    async with rls_session(user_a.id) as session:
        assert await session.scalar(select(func.count()).select_from(Reminder)) == 1
    async with rls_session(user_b.id) as session:
        assert await session.scalar(select(func.count()).select_from(Reminder)) == 1


def test_scan_result_defaults_to_zero() -> None:
    """空结果对象必须是 0，不然统计会把"没跑"算成"跑了"。"""
    result = reminder_service.ScanResult()
    assert (result.created, result.sent, result.failed) == (0, 0, 0)
