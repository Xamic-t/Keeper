"""助理这一轮的事件流：Harness 优先，失败回退内置对话。

为什么单独一个模块：路由函数有 30 行上限（ARCHITECTURE §11.2），
而"跑侧车 → 透传事件 → 失败降级 → 落库"本来就是业务编排，不该塞在 api/ 里。
这里产出的都是**普通 dict 事件**，变成 SSE 文本是 api 层的事。

事件契约（前端按这个渲染）：
  thinking      {"text": "..."}                          模型在推理（只给一句摘要）
  tool_call     {"tool","label","args"}                  调工具
  tool_result   {"tool","label","ok","summary"}          工具返回
  final         {"text": "...", "degraded"?: true}       最终回复
  notice        {"text": "..."}                          降级提示（不是错误）
  done          {}                                       流结束
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

from app.services import conversation as conversation_service
from app.services import harness

logger = logging.getLogger(__name__)

_TASK_HEAD = (
    "你是 Keeper 的私人助理。用中文简短回答（≤3 句）。"
    "需要数据时调用 keeper_* 工具，不要凭空回答；查不到就说没记着。"
)


def build_task(text: str, dialogue: str) -> str:
    """拼成给侧车的任务：最近几轮 + 本轮，并交代"用工具查、不要编"。"""
    parts = [_TASK_HEAD]
    if dialogue:
        parts.append(f"最近对话：\n{dialogue}")
    parts.append(f"本轮用户说：{text}")
    return "\n\n".join(parts)


async def _harness_events(user_id: int, text: str) -> AsyncIterator[dict[str, Any]]:
    """跑 Harness 并透传事件；这一轮结束时把消息落库。"""
    dialogue = await conversation_service.recent_dialogue(user_id)
    steps: list[dict[str, Any]] = []
    final_text = ""

    async for event in harness.stream_turn(user_id, build_task(text, dialogue)):
        if event["type"] == "tool_result":
            steps.append(
                {
                    "type": "tool",
                    "tool": event["tool"],
                    "label": event["label"],
                    "ok": event["ok"],
                    "summary": event["summary"],
                }
            )
        elif event["type"] == "final":
            final_text = event["text"]
        yield event

    if final_text:
        await conversation_service.record_exchange(
            user_id, user_text=text, assistant_text=final_text, steps=steps
        )


async def _native_events(user_id: int, text: str) -> AsyncIterator[dict[str, Any]]:
    """降级：走内置对话，把动作包装成同样的工具事件。"""
    result = await conversation_service.run_turn_scoped(user_id, text=text)
    for action in result.actions:
        yield {
            "type": "tool_result",
            "tool": str(action.get("type") or ""),
            "label": str(action.get("summary") or ""),
            "ok": bool(action.get("ok")),
            "summary": str(action.get("summary") or ""),
        }
    yield {"type": "final", "text": result.reply, "degraded": True}


async def stream_events(user_id: int, text: str) -> AsyncIterator[dict[str, Any]]:
    """一轮的完整事件流。Harness 不可用 / 这一轮失败都自动降级。"""
    if not harness.available():
        yield {"type": "notice", "text": "Harness 未启用，这一轮用内置对话"}
        async for event in _native_events(user_id, text):
            yield event
        yield {"type": "done"}
        return

    # 进程已经预热过就别再吓用户。只有真的要冷启动（26 秒）才提示——
    # 这段时间一个事件都没有，界面会像卡死，与其让人怀疑坏了，不如直说。
    if not harness.is_warm(user_id):
        yield {"type": "notice", "text": "Harness 启动中，首次约 26 秒（之后就快了）…"}

    try:
        async for event in _harness_events(user_id, text):
            yield event
    except harness.HarnessError as exc:
        # 侧车这一轮失败 → 不整页报错，退回内置对话（§19.7.3 第三条）
        logger.warning("harness turn failed user_id=%s reason=%s", user_id, exc)
        yield {"type": "notice", "text": f"{exc}，这一轮改用内置对话"}
        async for event in _native_events(user_id, text):
            yield event
    yield {"type": "done"}
