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


def _image_part(image: bytes, mime: str = "image/jpeg") -> dict[str, Any]:
    """把图片塞成 data URL。VLM 都吃这个格式。"""
    encoded = base64.b64encode(image).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}


async def _post(provider: Provider, payload: dict[str, Any]) -> str:
    """发一次请求，返回 message.content 原文。"""
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
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AppError(
            ErrorCode.AI_UPSTREAM_ERROR,
            "模型返回格式异常",
            status_code=502,
            detail={"provider": provider.name},
        ) from exc


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


def parse_json(raw: str) -> dict[str, Any]:
    """解析模型输出。不合法就抛 502，交给上层标 failed。"""
    try:
        parsed = json.loads(_strip_fence(raw))
    except json.JSONDecodeError as exc:
        logger.warning("model returned non-json output")
        raise AppError(
            ErrorCode.AI_UPSTREAM_ERROR,
            "模型没有返回合法 JSON",
            status_code=502,
        ) from exc
    if not isinstance(parsed, dict):
        raise AppError(ErrorCode.AI_UPSTREAM_ERROR, "模型返回的不是对象", status_code=502)
    return parsed


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
        content: list[dict[str, Any]],
        *,
        json_mode: bool,
    ) -> tuple[Provider, str]:
        """主备 + 重试的唯一实现。返回 (命中的通道, 原文)。"""
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
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.1,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}

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

        provider, raw = await self._dispatch(content, json_mode=schema is not None)
        return ModelReply(
            parsed=parse_json(raw), raw=raw, provider=provider.name, model=provider.model
        )

    async def complete(self, *, prompt: str, text: str | None = None) -> ModelReply:
        """要一段自由文本（综述这种）：不解析 JSON。"""
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if text:
            content.append({"type": "text", "text": text})

        provider, raw = await self._dispatch(content, json_mode=False)
        return ModelReply(
            parsed={"text": raw}, raw=raw, provider=provider.name, model=provider.model
        )


vision = VisionClient()
