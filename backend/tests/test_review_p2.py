"""评审 P2 里三条"小但会咬人"的修复。

P2-12 导出 ZIP 文件名没清洗（Zip Slip）
P2-6  图片 MIME 永远写 image/jpeg（png/webp 也标 jpeg）
P2-8  提醒模板里的 {tags} 永远渲染成空数组

三条都不改接口形状，所以放在一个文件里，用测试把行为钉住。
"""

import base64
import json

from app.models.fact import Tag
from app.services import reminder as reminder_service
from app.services.export import safe_name
from app.services.person import create_person
from app.services.vision import _image_part
from tests.conftest import Account, rls_session

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ── P2-12 ────────────────────────────────────────────────
def test_safe_name_strips_path_traversal() -> None:
    assert "/" not in safe_name("../../etc/passwd")
    assert ".." not in safe_name("../../etc/passwd")
    assert "\\" not in safe_name("..\\..\\windows")


def test_safe_name_keeps_chinese() -> None:
    # 中文昵称原样保留，别把常用名洗成下划线
    assert safe_name("孟雅楠") == "孟雅楠"
    assert safe_name("小赵 快乐小居") == "小赵_快乐小居"


def test_safe_name_falls_back_when_only_symbols() -> None:
    assert safe_name("///", fallback="person") == "person"
    assert safe_name("", fallback="person") == "person"


# ── P2-6 ─────────────────────────────────────────────────
def test_image_part_uses_real_mime() -> None:
    part = _image_part(PNG_1PX)
    url = part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


# ── P2-8 ─────────────────────────────────────────────────
async def test_person_context_includes_live_tags(single_user: Account) -> None:
    async with rls_session(single_user.id) as session:
        summary = await create_person(session, single_user.id, nickname="孟雅楠", roles=["ge_jie"])
        person_id = summary.person.id
    assert person_id is not None

    async with rls_session(single_user.id) as session:
        session.add(
            Tag(
                user_id=single_user.id,
                person_id=person_id,
                key="preference",
                value="爱喝苹果汁",
            )
        )
        await session.commit()

    async with rls_session(single_user.id) as session:
        person, _relationship, _facts, _ledger, tags = await reminder_service._person_context(
            session, person_id
        )

    assert person is not None
    assert [tag.value for tag in tags] == ["爱喝苹果汁"]


async def test_prompt_renders_tags_as_json(single_user: Account) -> None:
    """模板里是 `"tags": {tags}`，塞进去的必须是 JSON 数组，不能是 Python repr。"""
    async with rls_session(single_user.id) as session:
        summary = await create_person(session, single_user.id, nickname="王姐", roles=["ge_jie"])
        person_id = summary.person.id
        person = summary.person
    assert person_id is not None

    tag = Tag(user_id=single_user.id, person_id=person_id, key="preference", value="只喝手冲")
    prompt = reminder_service._render_prompt(
        level="d7",
        event=_fake_event(),
        days_left=7,
        person=person,
        relationship=None,
        facts=[],
        ledger=[],
        tags=[tag],
    )

    assert '"tags": [{"key": "preference", "value": "只喝手冲"}]' in prompt
    assert json.dumps([{"key": "preference", "value": "只喝手冲"}], ensure_ascii=False) in prompt


def _fake_event():
    """只为了拼 prompt，不需要落库。"""
    from datetime import date

    from app.models.event import Event

    return Event(
        user_id=1,
        type="birthday",
        title="孟雅楠生日",
        occurs_on=date(2026, 11, 20),
    )
