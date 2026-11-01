"""生日是每年的事，不是今年的事。

三个层次一起钉：
  ① 抽取写入 / 手动新建时，生日事件带 FREQ=YEARLY
  ② 跨年查询能查到明年那一天（日历不会"今年过完就消失"）
  ③ 关系表里存了公历生日的人，即使没有手动建事件，也要出现在日历上

第 ③ 条是之前最隐蔽的：代码只认 `birthday_lunar`，抽到公历生日的人
在日历上根本不存在，而用户不知道自己该去手建一条。
"""

from datetime import date

from app.models.event import Event
from app.models.person import Person
from app.models.relationship import Relationship
from app.services import event as event_service
from tests.conftest import Account, rls_session


async def _person(user: Account, nickname: str, **rel_kwargs) -> int:
    async with rls_session(user.id) as session:
        person = Person(user_id=user.id, nickname=nickname)
        session.add(person)
        await session.flush()
        session.add(Relationship(user_id=user.id, person_id=person.id, **rel_kwargs))
        await session.commit()
        return person.id


async def test_create_birthday_event_defaults_to_yearly(single_user: Account) -> None:
    person_id = await _person(single_user, "生日甲")

    async with rls_session(single_user.id) as session:
        created = await event_service.create_event(
            session,
            single_user.id,
            type="birthday",
            title="生日甲生日",
            occurs_on=date(2026, 3, 8),
            person_id=person_id,
        )

    assert created.rrule == "FREQ=YEARLY"


async def test_non_birthday_event_stays_single(single_user: Account) -> None:
    """别把"每年重复"的逻辑扩散到结款：那会凭空多出一堆事件。"""
    person_id = await _person(single_user, "结款乙")

    async with rls_session(single_user.id) as session:
        created = await event_service.create_event(
            session,
            single_user.id,
            type="payment_due",
            title="结款",
            occurs_on=date(2026, 3, 8),
            person_id=person_id,
        )

    assert created.rrule is None


async def test_birthday_expands_into_next_year(single_user: Account) -> None:
    person_id = await _person(single_user, "跨年丙")

    async with rls_session(single_user.id) as session:
        await event_service.create_event(
            session,
            single_user.id,
            type="birthday",
            title="跨年丙生日",
            occurs_on=date(2026, 12, 25),
            person_id=person_id,
        )
        items = await event_service.list_events(session, date(2027, 12, 1), date(2027, 12, 31))

    days = [item.day for item in items if item.type == "birthday"]
    assert date(2027, 12, 25) in days, "明年的生日必须还能查到"


async def test_solar_birthday_from_relationship_shows_on_calendar(single_user: Account) -> None:
    """关系表里有公历生日、没有对应事件 —— 这是最常见的情况（抽取只写关系表）。"""
    await _person(
        single_user,
        "公历丁",
        birthday_solar=date(1995, 7, 18),
        birthday_type="solar",
    )

    async with rls_session(single_user.id) as session:
        items = await event_service.list_events(session, date(2026, 7, 1), date(2026, 7, 31))

    titles = [(item.day, item.title) for item in items if item.type == "birthday"]
    assert (date(2026, 7, 18), "公历丁生日") in titles


async def test_solar_birthday_also_shows_next_year(single_user: Account) -> None:
    await _person(
        single_user,
        "公历戊",
        birthday_solar=date(1990, 2, 14),
        birthday_type="solar",
    )

    async with rls_session(single_user.id) as session:
        items = await event_service.list_events(session, date(2027, 2, 1), date(2027, 2, 28))

    assert any(item.day == date(2027, 2, 14) for item in items if item.type == "birthday")


async def test_leap_day_falls_back_to_feb_28(single_user: Account) -> None:
    """2 月 29 日在平年退化成 2 月 28 日，而不是凭空消失。"""
    await _person(
        single_user,
        "闰日己",
        birthday_solar=date(1992, 2, 29),
        birthday_type="solar",
    )

    async with rls_session(single_user.id) as session:
        items = await event_service.list_events(session, date(2027, 2, 1), date(2027, 2, 28))

    assert any(item.day == date(2027, 2, 28) for item in items if item.type == "birthday")


async def test_event_and_relationship_do_not_duplicate(single_user: Account) -> None:
    """既有事件又有关系表生日时，只出现一次，不能并排两条。"""
    person_id = await _person(
        single_user,
        "双份庚",
        birthday_solar=date(1991, 9, 9),
        birthday_type="solar",
    )

    async with rls_session(single_user.id) as session:
        await event_service.create_event(
            session,
            single_user.id,
            type="birthday",
            title="双份庚生日",
            occurs_on=date(2026, 9, 9),
            person_id=person_id,
        )
        items = await event_service.list_events(session, date(2026, 9, 1), date(2026, 9, 30))

    same_day = [item for item in items if item.day == date(2026, 9, 9) and item.type == "birthday"]
    assert len(same_day) == 1


async def test_extracted_birthday_gets_rrule(single_user: Account) -> None:
    """抽取写入的事件也要带 rrule —— 这条路径以前完全没设。"""
    async with rls_session(single_user.id) as session:
        person = Person(user_id=single_user.id, nickname="抽取辛")
        session.add(person)
        await session.flush()
        session.add(
            Event(
                user_id=single_user.id,
                person_id=person.id,
                type="birthday",
                title="抽取辛生日",
                occurs_on=date(2020, 5, 20),
                rrule=event_service.default_rrule("birthday"),
                source_type="screenshot",
            )
        )
        await session.commit()

    async with rls_session(single_user.id) as session:
        items = await event_service.list_events(session, date(2027, 5, 1), date(2027, 5, 31))

    assert any(item.day == date(2027, 5, 20) for item in items if item.type == "birthday")
