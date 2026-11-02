"""模型调用：主备双通道，OpenAI 兼容协议。

对应文档：specs/04-extract.md 5.1

三条规则：
  ① 主通道超时/5xx → 重试 1 次 → 切备通道；都失败抛 502
  ② 返回不是合法 JSON → 抛 502，**不做"猜格式"式的修复**
  ③ API Key 没配 → 同样抛 502（不降级成假数据，宁可整条标 failed）

这里只做"发请求 + 解析 JSON"，业务语义（三个步骤怎么问、结果怎么校验）在 extract.py。
"""

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.core.errors import AppError, ErrorCode
from app.services.storage import sniff_image_suffix

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Provider:
    """一个通道的配置。两个通道都是 OpenAI 兼容的 /chat/completions。"""

    name: str
    model: str
    base_url: str
    api_key: str | None

    @property
    def ready(self) -> bool:
        return bool(self.api_key)


def _providers() -> list[Provider]:
    """按优先级返回通道：主 → 备。没配 Key 的直接跳过。"""
    primary = Provider(
        name=settings.vision_provider,
        model=settings.vision_model,
        base_url=settings.vision_base_url.rstrip("/"),
        api_key=settings.deepseek_api_key.get_secret_value() if settings.deepseek_api_key else None,
    )
    fallback = Provider(
        name=settings.fallback_provider,
        model=settings.fallback_model,
        base_url=settings.fallback_base_url.rstrip("/"),
        api_key=(
            settings.dashscope_api_key.get_secret_value() if settings.dashscope_api_key else None
        ),
    )
    return [item for item in (primary, fallback) if item.ready]


def _image_part(image: bytes) -> dict[str, Any]:
    """把图片塞成 data URL。VLM 都吃这个格式。

    mime 按**字节**判，不看后缀也不写死 jpeg（评审 P2-6）：
    png 图片标成 image/jpeg，有的服务会直接拒收或按 jpeg 解码出错。
    """
    encoded = base64.b64encode(image).decode()
    suffix = sniff_image_suffix(image)
    # 嗅探不出（比如老数据已经损坏）就退回 jpeg，至少还有一次机会
    mime = "image/jpeg" if suffix in (None, "jpg") else f"image/{suffix}"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}


async def _post(provider: Provider, payload: dict[str, Any]) -> str:
    """发一次请求，返回 message.content 原文。

    空白内容会在**同一个通道上**再要一次（最多 2 次）：实测 json_object 模式偶发
    只回空白，这不是通道坏了，是这一次生成废了。同一通道重来一次比切备通道更划算。
    """
    last_blank = False
    for _attempt in (1, 2):
        content = await _post_once(provider, payload)
        if content is not None:
            return content
        last_blank = True
    if last_blank:
        raise AppError(
            ErrorCode.AI_UPSTREAM_ERROR,
            "模型返回了空内容",
            status_code=502,
            detail={"provider": provider.name},
        )
    raise AppError(ErrorCode.AI_UPSTREAM_ERROR, "模型返回格式异常", status_code=502)


async def _post_once(provider: Provider, payload: dict[str, Any]) -> str | None:
    """发一次请求。内容空白时返回 None，让上层决定重试。"""
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=settings.vision_timeout_s) as client:
        response = await client.post(
            f"{provider.base_url}/chat/completions", json=payload, headers=headers
        )
    if response.status_code >= 500 or response.status_code == 429:
        # 让上层重试/切换
        raise httpx.HTTPStatusError(
            f"{provider.name} {response.status_code}",
            request=response.request,
            response=response,
        )
    if response.status_code >= 400:
        raise AppError(
            ErrorCode.AI_UPSTREAM_ERROR,
            "模型服务拒绝了请求，请检查 API Key 与模型名",
            status_code=502,
            detail={"provider": provider.name, "status": response.status_code},
        )

    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AppError(
            ErrorCode.AI_UPSTREAM_ERROR,
            "模型返回格式异常",
            status_code=502,
            detail={"provider": provider.name},
        ) from exc

    # 有些通道（实测 deepseek 的 json_object 模式）偶尔返回 None 或一整段空白。
    # 交给 _post 决定重试次数，不把空白当答案往解析器里塞。
    if not isinstance(content, str) or not content.strip():
        return None
    return content


def _strip_fence(raw: str) -> str:
    """去掉 ```json ... ``` 围栏。这只是剥壳，不是修 JSON。"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _first_json_object(text: str) -> str | None:
    """从一段文字里**定位**第一个完整的 JSON 对象。

    只做括号配对（含字符串与转义），不补引号、不改逗号、不猜字段——
    语法错了照样解析失败。所以这不是"修格式"，是"把 JSON 从话里挑出来"。

    实测模型常这样回：
        "好的，我帮你建。\n{\"reply\": \"...\", \"actions\": [...]}\n要不要再建一个？"
    前面一句客气话、后面一句追问，JSON 在中间。直接 json.loads 整段必失败。
    """
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_json(raw: str) -> dict[str, Any]:
    """解析模型输出。解析不出就抛 502，交给上层决定重试还是标 failed。

    三级尝试，逐级放宽但都只做"剥壳/定位"，不做语法修复：
      ① 原样解析
      ② 去掉 ``` 围栏再解析
      ③ 从文字里挑出第一个完整的 {...} 再解析
    """
    candidates: list[str] = []
    stripped = raw.strip()
    if stripped:
        candidates.append(stripped)
    unfenced = _strip_fence(raw)
    if unfenced and unfenced != stripped:
        candidates.append(unfenced)
    located = _first_json_object(unfenced or stripped)
    if located:
        candidates.append(located)

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(parsed, dict):
            raise AppError(ErrorCode.AI_UPSTREAM_ERROR, "模型返回的不是对象", status_code=502)
        return parsed

    # 只记长度和首字符类型，不记内容：对话里可能带用户隐私
    logger.warning(
        "model returned non-json output len=%s startswith_brace=%s",
        len(raw),
        raw.strip().startswith("{"),
    )
    raise AppError(
        ErrorCode.AI_UPSTREAM_ERROR,
        "模型没有返回合法 JSON",
        status_code=502,
    ) from last_error


# 对话专用：在最后一轮补一句"只输出 JSON"的硬要求。
# 为什么不在 system 里写就够了：实测 system 里的格式要求会被多轮历史稀释，
# 模型 5/5 直接给散文；补在末尾后 5/5 给 JSON。见 2026-11-03 的探针对比。
JSON_NUDGE = {
    "role": "user",
    "content": (
        "（只输出一个 JSON 对象，形如 "
        '{"reply": "给用户看的话", "actions": []}'
        "。不要写任何 JSON 之外的字，不要加 ``` 围栏。）"
    ),
}


@dataclass(frozen=True, slots=True)
class ModelReply:
    """一次模型调用的完整结果。raw 要进审计表，所以一起带回来。"""

    parsed: dict[str, Any]
    raw: str
    provider: str
    model: str


class VisionClient:
    """统一模型入口。调用方只关心"给我 JSON"或"给我一段话"。"""

    async def _dispatch(
        self,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool,
        temperature: float = 0.1,
        thinking: bool = True,
        structured: bool = True,
    ) -> tuple[Provider, str]:
        """主备 + 重试的唯一实现。返回 (命中的通道, 原文)。

        收的是完整的 messages 数组（不是单条 content）：抽取是"一个 user 消息"，
        对话是多轮消息。两者共用同一套主备/重试，避免两套失败语义。

        `structured`：是否带 `response_format=json_object`。
        **对话必须关掉**（2026-11-03 实测）：只要 messages 里有多轮历史，
        deepseek-flash 带这个参数就 5/5 返回空 content（finish_reason 仍是 stop），
        去掉后 0/5。抽取是单轮 messages，不受影响，继续带着。
        """
        providers = _providers()
        if not providers:
            raise AppError(
                ErrorCode.AI_UPSTREAM_ERROR,
                "还没有配置模型 API Key，AI 相关功能已暂停",
                status_code=502,
            )

        last_error: Exception | None = None
        for provider in providers:
            payload: dict[str, Any] = {
                "model": provider.model,
                "messages": messages,
                "temperature": temperature,
            }
            if json_mode and structured:
                payload["response_format"] = {"type": "json_object"}
            if not thinking:
                # 实测（2026-11-03）：deepseek-flash 是推理模型，json_object 模式下
                # 约 1/4 的请求会"思考完了不给正文"——content 为空、finish_reason 却是 stop，
                # 上层只能看到"空内容"。关掉思考后正文稳定返回，也更快更省 token。
                # 抽取那条链路仍然开着思考（要的是判断质量，不是速度）。
                payload["thinking"] = {"type": "disabled"}

            for attempt in (1, 2):
                try:
                    return provider, await _post(provider, payload)
                except httpx.TimeoutException:
                    last_error = AppError(ErrorCode.AI_TIMEOUT, "模型响应超时", status_code=504)
                    logger.warning("vision timeout provider=%s attempt=%s", provider.name, attempt)
                except httpx.HTTPError as exc:
                    last_error = AppError(
                        ErrorCode.AI_UPSTREAM_ERROR,
                        "模型服务暂时不可用",
                        status_code=502,
                    )
                    logger.warning(
                        "vision upstream error provider=%s attempt=%s kind=%s",
                        provider.name,
                        attempt,
                        type(exc).__name__,
                    )
                except AppError as exc:
                    # 4xx：这个通道废了，直接换下一个（重试也没意义）
                    last_error = exc
                    break

        raise last_error or AppError(
            ErrorCode.AI_UPSTREAM_ERROR, "所有模型通道都失败了", status_code=502
        )

    async def extract(
        self,
        *,
        prompt: str,
        image: bytes | None = None,
        text: str | None = None,
        schema: dict[str, Any] | None = None,
    ) -> ModelReply:
        """要结构化结果：返回解析后的 JSON + 原文（原文要进审计表）。"""
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if text:
            content.append({"type": "text", "text": f"原始文本：\n{text}"})
        if image:
            content.append(_image_part(image))

        provider, raw = await self._dispatch(
            [{"role": "user", "content": content}], json_mode=schema is not None
        )
        return ModelReply(
            parsed=parse_json(raw), raw=raw, provider=provider.name, model=provider.model
        )

    async def complete(self, *, prompt: str, text: str | None = None) -> ModelReply:
        """要一段自由文本（综述这种）：不解析 JSON。"""
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if text:
            content.append({"type": "text", "text": text})

        provider, raw = await self._dispatch(
            [{"role": "user", "content": content}], json_mode=False
        )
        return ModelReply(
            parsed={"text": raw}, raw=raw, provider=provider.name, model=provider.model
        )

    async def converse(
        self,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool = True,
        temperature: float = 0.6,
    ) -> ModelReply:
        """多轮对话。助理用它：system + 历史 + 本轮，走同一套主备通道。

        temperature 比抽取高（0.6 vs 0.1）：抽取要稳定复现，聊天要像人说话。
        但仍然是"低随机"——这个助理的职责是帮人记事，不是写文案。

        **可靠性三级**（实测 deepseek 的 json_object 模式会偶发只回空白）：
          ① 正常要 JSON，解析失败就再要一次（_dispatch 内部还会按通道重试）
          ② 两次都不是 JSON → 再要一次**纯文本**，只当 reply，一条动作都不执行
          ③ 连纯文本都拿不到 → 502，前端给"重试"

        第 ② 步是关键取舍：**宁可这次不建待办，也要让用户看到一句话**。
        因为"助理没听懂/没动手"用户能自己补一句；而"红色报错"只会让人以为系统坏了。
        """
        if not json_mode:
            provider, raw = await self._dispatch(
                messages, json_mode=False, temperature=temperature, thinking=False, structured=False
            )
            return ModelReply(
                parsed={"text": raw}, raw=raw, provider=provider.name, model=provider.model
            )

        # 末尾补一句硬要求：光靠 system prompt 里的格式说明，模型会跑偏成散文
        constrained = [*messages, JSON_NUDGE]
        last_error: AppError | None = None
        for attempt in (1, 2):
            provider, raw = await self._dispatch(
                constrained,
                json_mode=True,
                temperature=temperature,
                thinking=False,
                structured=False,
            )
            try:
                parsed = parse_json(raw)
            except AppError as exc:
                # 只记长度，不记内容：对话里可能带用户隐私
                logger.warning("assistant json invalid attempt=%s len=%s", attempt, len(raw))
                last_error = exc
                continue
            return ModelReply(parsed=parsed, raw=raw, provider=provider.name, model=provider.model)

        logger.warning("assistant degraded to plain text reply")
        try:
            provider, raw = await self._dispatch(
                [
                    *messages,
                    {
                        "role": "user",
                        "content": (
                            "（系统提示：上一条不用 JSON 了，直接用中文回一句话给用户，"
                            "不要建任何东西。）"
                        ),
                    },
                ],
                json_mode=False,
                temperature=temperature,
                thinking=False,
                structured=False,
            )
        except AppError:
            raise last_error or AppError(
                ErrorCode.AI_UPSTREAM_ERROR, "模型没有给出回复", status_code=502
            ) from last_error

        text = raw.strip()
        if not text:
            raise last_error or AppError(
                ErrorCode.AI_UPSTREAM_ERROR, "模型没有给出回复", status_code=502
            )
        return ModelReply(
            parsed={"reply": text, "actions": [], "degraded": True},
            raw=raw,
            provider=provider.name,
            model=provider.model,
        )


vision = VisionClient()
