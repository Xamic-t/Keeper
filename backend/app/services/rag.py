"""RAG：把「材料」切段、向量化、检索。

对应文档：docs/06 §5.16、ARCHITECTURE 19.3 / 19.7.11

三个刻意的取舍：

① **切片永远是先落库的**，向量是可选的一列。
   没配向量模型时 `embedding` 为 NULL，检索退化成关键词，
   但"这段文本属于谁的哪份材料"这条关系已经建好了。
   等哪天配上 key，跑一次 `scripts/reindex.py` 就把向量补齐，不用重切。

② **不缓存、不建第二套真相**：切片只是原文的索引，原文还在 `facts` / `raw_inputs`。
   删人、删材料都级联到切片（FK ON DELETE CASCADE），不会留孤儿。

③ **检索结果带出处**：`source_type + source_id + person_id` 一起返回，
   模型回答时能说"这条来自 3 月 12 日的聊天记录"，也方便点回原文。
"""

import logging
import re
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fact import Fact
from app.models.vector import VectorChunk
from app.services import embedding

logger = logging.getLogger(__name__)

# 一段多少字。400 是中文的"一屏"：再长模型抓不住重点，再短检索会碎
CHUNK_SIZE = 400
# 相邻切片重叠多少字：避免"这句话正好被切断"导致检索不到
CHUNK_OVERLAP = 60
# 一句话里超过这个长度就该切了（中文按标点断句，英文按空格）
_SENTENCE_END = re.compile(r"[。！？!?\n]")


def _split(text_value: str) -> list[str]:
    """按句子攒，攒到 CHUNK_SIZE 就切一段，段与段之间留 CHUNK_OVERLAP。"""
    cleaned = " ".join(text_value.split())
    if not cleaned:
        return []
    if len(cleaned) <= CHUNK_SIZE:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + CHUNK_SIZE, len(cleaned))
        if end < len(cleaned):
            # 往回找一个句末，别把一句话从中间劈开
            window = cleaned[start:end]
            matches = list(_SENTENCE_END.finditer(window))
            if matches:
                end = start + matches[-1].end()
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(cleaned):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


async def index_source(
    session: AsyncSession,
    *,
    user_id: int,
    source_type: str,
    source_id: int,
    content: str,
    person_id: int | None = None,
    scope: str = "memory",
) -> int:
    """（重新）索引一份来源。返回写入的切片数。

    先删旧切片再写新的：同一份材料被改过（比如事实被改措辞）时不会留两份。
    """
    await session.execute(
        delete(VectorChunk).where(
            VectorChunk.user_id == user_id,
            VectorChunk.source_type == source_type,
            VectorChunk.source_id == source_id,
        )
    )

    pieces = _split(content)
    if not pieces:
        await session.commit()
        return 0

    vectors = await embedding.embed(pieces)
    for index, piece in enumerate(pieces):
        session.add(
            VectorChunk(
                user_id=user_id,
                person_id=person_id,
                scope=scope,
                content=piece,
                source_type=source_type,
                source_id=source_id,
                embedding=vectors[index] if vectors else None,
            )
        )
    await session.commit()
    return len(pieces)


async def index_fact(session: AsyncSession, fact: Fact) -> int:
    """索引一条**已确认**的事实。待确认的不进索引——那是猜测，不该被检索到。"""
    if fact.status != "confirmed" or fact.id is None or fact.user_id is None:
        return 0
    return await index_source(
        session,
        user_id=fact.user_id,
        source_type="fact",
        source_id=fact.id,
        content=fact.content,
        person_id=fact.person_id,
        scope="memory",
    )


async def search(session: AsyncSession, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """检索。有向量走向量，没向量走关键词——两条路返回同样的形状。"""
    cleaned = (query or "").strip()
    if not cleaned:
        return []

    vectors = await embedding.embed([cleaned])
    if vectors:
        return await _vector_search(session, vectors[0], limit)
    return await _keyword_search(session, cleaned, limit)


async def _vector_search(
    session: AsyncSession, vector: list[float], limit: int
) -> list[dict[str, Any]]:
    """余弦距离排序。`<=>` 是 pgvector 的余弦距离算子（越小越像）。"""
    literal = "[" + ",".join(f"{value:.8f}" for value in vector) + "]"
    rows = (
        await session.execute(
            text(
                """
                SELECT id, content, source_type, source_id, person_id,
                       scope, 1 - (embedding <=> CAST(:vector AS vector)) AS score
                FROM vector_chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vector AS vector)
                LIMIT :limit
                """
            ),
            {"vector": literal, "limit": limit},
        )
    ).mappings()
    return [dict(row) for row in rows]


async def _keyword_search(session: AsyncSession, query: str, limit: int) -> list[dict[str, Any]]:
    """没有向量时的退路：把查询按空格/标点切开，任意一个词命中就算。

    比 ILIKE 整串好一点：用户问"王姐 生日"也能命中只含"生日"的片段。
    这不是语义检索（搜"喝茶"找不到"白茶"），所以命中时 score 记 0，
    让调用方知道"这是关键词结果，别当相似度用"。
    """
    terms = [term for term in re.split(r"[\s,，。;；]+", query) if len(term) >= 2]
    if not terms:
        terms = [query]

    # 任意一个词命中即可（OR）：用户问"王姐 生日"时，只含"生日"的片段也该召回
    conditions = [VectorChunk.content.ilike(f"%{term}%") for term in terms[:4]]
    rows = list(
        (await session.execute(select(VectorChunk).where(*conditions).limit(limit))).scalars().all()
    )
    return [
        {
            "id": row.id,
            "content": row.content,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "person_id": row.person_id,
            "scope": row.scope,
            "score": 0.0,
        }
        for row in rows
    ]
