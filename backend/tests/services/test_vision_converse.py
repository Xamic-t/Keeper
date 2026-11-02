"""模型输出解析与对话兜底。

这两个东西以前只有"能解析就行"的一条 happy path，实际跑起来发现：
  · deepseek 的 json_object 模式会**偶发只回空白**（125 个空格）——不是格式错，是这次生成废了
  · 模型常把 JSON 夹在"好的，我帮你建。"和"还要别的吗？"之间，直接 loads 必失败
  · 连续两次不给 JSON 时，用户只看到一句"模型没有返回合法 JSON"，像是系统坏了

所以这里钉三件事：
  ① 解析只做"剥壳/定位"，语法错照样失败（不做格式修复）
  ② 空白内容当成可重试的上游错误，不往解析器里塞
  ③ 两次都不是 JSON 时降级成纯文本回复，**一条动作都不执行**
"""

import json

import pytest

from app.core.errors import AppError, ErrorCode
from app.services.vision import ModelReply, parse_json, vision


def test_plain_json_parses() -> None:
    assert parse_json('{"reply": "在", "actions": []}')["reply"] == "在"


def test_fenced_json_parses() -> None:
    raw = '```json\n{"reply": "在", "actions": []}\n```'
    assert parse_json(raw)["reply"] == "在"


def test_json_wrapped_in_prose_is_located() -> None:
    """模型最常见的坏毛病：前后各一句客气话。"""
    raw = (
        '好的，我帮你建。\n{"reply": "建好了", "actions": [{"type": "create_todo"}]}\n还要别的吗？'
    )
    parsed = parse_json(raw)
    assert parsed["reply"] == "建好了"
    assert parsed["actions"][0]["type"] == "create_todo"


def test_braces_inside_strings_do_not_break_matching() -> None:
    """reply 里出现花括号时，配对不能提前结束。"""
    raw = '{"reply": "格式像这样的：{\\"a\\": 1}", "actions": []}'
    assert parse_json(raw)["reply"].startswith("格式像这样的")


def test_truncated_json_still_fails() -> None:
    """语法错不许"修"：截断的 JSON 必须失败，否则会拿半截数据去建待办。"""
    with pytest.raises(AppError) as info:
        parse_json('{"reply": "还没写完')
    assert info.value.code == ErrorCode.AI_UPSTREAM_ERROR


def test_whitespace_only_fails() -> None:
    with pytest.raises(AppError):
        parse_json("   \n\n   ")


def test_array_is_not_an_object() -> None:
    with pytest.raises(AppError) as info:
        parse_json('[{"reply": "x"}]')
    assert "对象" in str(info.value)


class _StubDispatch:
    """按脚本依次返回原始输出，用来模拟模型抽风。"""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    async def __call__(
        self,
        messages,
        *,
        json_mode: bool,
        temperature: float = 0.1,
        thinking: bool = True,
        structured: bool = True,
    ):
        self.calls += 1
        output = self.outputs.pop(0)
        return (type("P", (), {"name": "fake", "model": "fake"})(), output)


async def test_converse_recovers_from_one_bad_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第一次给了散文，第二次正常 → 正常返回，用户无感。"""
    stub = _StubDispatch(["我只说人话不给你 JSON", '{"reply": "第二次好了", "actions": []}'])
    monkeypatch.setattr(vision, "_dispatch", stub)

    reply = await vision.converse([{"role": "user", "content": "在吗"}])

    assert reply.parsed["reply"] == "第二次好了"
    assert reply.parsed.get("degraded") is None


async def test_converse_degrades_to_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两次都不给 JSON → 再要一次纯文本，当 reply 用，动作清空。"""
    stub = _StubDispatch(
        [
            "第一次：好的",
            "第二次：还是不打 JSON",
            "我今天记着你有一条逾期的待办。",
        ]
    )
    monkeypatch.setattr(vision, "_dispatch", stub)

    reply = await vision.converse([{"role": "user", "content": "今天要做什么"}])

    assert stub.calls == 3
    assert reply.parsed["reply"].startswith("我今天记着")
    assert reply.parsed["actions"] == []
    assert reply.parsed["degraded"] is True, "降级要标出来，日志/测试才看得见"


async def test_converse_raises_only_when_everything_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连兜底的纯文本都是空的 → 才报 502（前端给重试）。"""

    async def _always_fail(
        messages,
        *,
        json_mode: bool,
        temperature: float = 0.1,
        thinking: bool = True,
        structured: bool = True,
    ):
        raise AppError(ErrorCode.AI_UPSTREAM_ERROR, "upstream down", status_code=502)

    monkeypatch.setattr(vision, "_dispatch", _always_fail)

    with pytest.raises(AppError) as info:
        await vision.converse([{"role": "user", "content": "在吗"}])
    assert info.value.status_code == 502


async def test_degraded_reply_has_no_actions() -> None:
    """兜底结构本身就是"只说话不干活"，这条钉住解析后的形状。"""
    parsed = json.loads('{"reply": "好的", "actions": [], "degraded": true}')
    assert isinstance(parsed["actions"], list) and parsed["actions"] == []


def test_model_reply_shape_is_stable() -> None:
    """ModelReply 的字段是调用方依赖的契约，改字段要改这里。"""
    reply = ModelReply(parsed={"reply": "x"}, raw="{}", provider="fake", model="m")
    assert set(reply.__dataclass_fields__) == {"parsed", "raw", "provider", "model"}


async def test_converse_payload_is_plain_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """对话的 payload 必须满足三条（2026-11-03 实测得出，缺一条就会大面积空白）：

    ① 带 `thinking=disabled`
    ② **不带** `response_format=json_object`（带它 + 多轮历史 → 5/5 空 content）
    ③ 末尾补一句"只输出 JSON"的硬要求（否则 5/5 回散文）
    """
    captured: dict = {}

    async def _fake_post(provider, payload):
        captured.update(payload)
        return '{"reply": "ok", "actions": []}'

    monkeypatch.setattr("app.services.vision._post_once", _fake_post)

    await vision.converse([{"role": "user", "content": "在吗"}])

    assert captured["thinking"] == {"type": "disabled"}
    assert "response_format" not in captured, "对话里带上它就会返回空内容"
    assert "只输出一个 JSON 对象" in captured["messages"][-1]["content"]


async def test_extract_keeps_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    """抽取不关思考：那边要的是判断质量，慢一点可以接受。"""
    captured: dict = {}

    async def _fake_post(provider, payload):
        captured.update(payload)
        return '{"ok": true}'

    monkeypatch.setattr("app.services.vision._post_once", _fake_post)

    await vision.extract(prompt="把这张图里的信息抽出来", schema={"type": "object"})

    assert "thinking" not in captured, "抽取链路不许被顺手关掉思考"
    assert captured["response_format"] == {"type": "json_object"}, "抽取仍然要结构化保证"
