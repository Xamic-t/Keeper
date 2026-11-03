"""`vector_chunks` 表定义（RAG 切片）。

为什么自己写一个 `Vector` 类型，而不是装 pgvector 的 Python 包：
只为了一个字段引一个依赖不划算。pgvector 的 wire format 就是
`'[0.1,0.2,...]'` 这段文本，一个 bind_processor 就能对上；
而真正的排序（`embedding <=> :vec`）本来就走原生 SQL。

这张表只有检索用，没有业务读写，所以字段严格照 docs/06 §5.16，
多加一个字段都要先改那份文档（§22 变更纪律）。
"""

from datetime import UTC, datetime

from sqlalchemy import Column
from sqlalchemy.types import UserDefinedType
from sqlmodel import Field, SQLModel

from app.config import settings


class VectorType(UserDefinedType):
    """pgvector 的 `vector(n)`。写入时把 list[float] 变成 `'[..]'` 文本。"""

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **kwargs: object) -> str:
        return f"vector({self.dim})"

    def bind_processor(self, dialect: object) -> object:
        def process(value: object) -> str | None:
            if value is None:
                return None
            if isinstance(value, str):
                return value
            numbers = ",".join(f"{float(item):.8f}" for item in value)  # type: ignore[union-attr]
            return f"[{numbers}]"

        return process


class VectorChunk(SQLModel, table=True):
    """一段可检索的文本 + 它的向量。

    一条来源（一个事实、一份材料）可以切出多段，所以没有唯一约束——
    `source_type + source_id` 只用来"重新索引前先删旧的"。
    """

    __tablename__ = "vector_chunks"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # 可空：知识库里"没有归属人"的资料也要能检索
    person_id: int | None = Field(default=None, foreign_key="persons.id")
    # memory（关于某个人的记忆）/ knowledge（资料笔记）
    scope: str = Field(max_length=16)
    content: str
    source_type: str = Field(max_length=32)
    source_id: int | None = None
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(VectorType(settings.embedding_dim), nullable=True),
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
