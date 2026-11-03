"""向量化：阿里百炼 `text-embedding-v3`（1024 维）。

**没 key 就不工作，但不报错**——返回 None，上层自动降级成关键词检索。
为什么这样做而不是"启动就失败"：RAG 是增强，不是主流程（§19.7.3 第三条）；
用户今天没配 key，明天配上了，代码不用改一行。

为什么不自己算向量：本地跑 bge 要拉模型、要 CPU，为了 20 个人的数据不值当；
百炼这条路一次调用就能批量拿 25 条，够用。
"""

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
_TIMEOUT_S = 20.0
# 一次请求最多几条。百炼 v3 的上限是 25，按 20 走留点余量
_BATCH = 20


class EmbeddingError(RuntimeError):
    """向量服务这次不可用。上层降级，不抛给用户。"""


def available() -> bool:
    """有没有配 key。没配就整条向量链路不启动。"""
    return settings.dashscope_api_key is not None


async def embed(texts: list[str]) -> list[list[float]] | None:
    """把一批文本变成向量。没 key 返回 None（调用方据此走降级）。

    出错也返回 None：索引/检索都是"能更好"，不该因为一次网络抖动让用户看见报错。
    """
    if not available() or not texts:
        return None

    key = settings.dashscope_api_key
    assert key is not None
    vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        for start in range(0, len(texts), _BATCH):
            batch = texts[start : start + _BATCH]
            try:
                response = await client.post(
                    _URL,
                    headers={"Authorization": f"Bearer {key.get_secret_value()}"},
                    json={
                        "model": settings.embedding_model,
                        "input": batch,
                        "dimensions": settings.embedding_dim,
                        "encoding_format": "float",
                    },
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                vectors.extend([item["embedding"] for item in payload["data"]])
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                logger.warning("embedding failed: %s", type(exc).__name__)
                return None
    return vectors if len(vectors) == len(texts) else None
