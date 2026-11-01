"""确认队列路由：列表 / 确认 / 驳回 / 改归属 / 批量。

对应文档：specs/05-confirm.md 5.6
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.response import DataResponse, ok
from app.deps import CurrentUser, get_current_user, get_session, require_csrf
from app.services import confirm as confirm_service

router = APIRouter(prefix="/confirmations", tags=["confirmations"])

# 批量确认单次上限。前端按 50 分片，这里留 4 倍余量给脚本和重试。
BATCH_LIMIT = 200


class ConfirmationItemOut(BaseModel):
    id: int
    content: str
    category: str
    confidence: float
    evidence: str | None = None
    raw_input_id: int | None = None
    thumb_url: str | None = Field(default=None, description="有原图时给缩略图地址")
    kind: str = "fact"
    created_at: datetime


class ConfirmationGroupOut(BaseModel):
    person_id: int | None = None
    nickname: str
    items: list[ConfirmationItemOut]


class ConfirmResultOut(BaseModel):
    fact_id: int
    status: str
    cascaded: dict[str, Any]


class BatchIn(BaseModel):
    # 上限不写在 Field 里：Pydantic 的 422 只说"list 太长"，用户看不懂也不知道怎么办。
    # 放到 handler 里判，能返回中文 + 400，前端可以直接把话弹给用户。
    fact_ids: list[int] = Field(min_length=1)


class BatchOut(BaseModel):
    confirmed: int
    skipped: int
    skipped_ids: list[int]


class ReassignIn(BaseModel):
    person_id: int | None = None
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    category: (
        Literal["permanent", "taboo", "structure", "preference", "situation", "mood"] | None
    ) = None


def _thumb(raw_input_id: int | None) -> str | None:
    return f"/api/raw-inputs/{raw_input_id}/file" if raw_input_id else None


@router.get("", response_model=DataResponse[list[ConfirmationGroupOut]], summary="待确认列表")
async def list_confirmations(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """按人分组返回待确认条目，每条带出处（evidence + 原图地址）。"""
    groups = await confirm_service.list_confirmations(session)
    return ok(
        [
            ConfirmationGroupOut(
                person_id=group.person_id,
                nickname=group.nickname,
                items=[
                    ConfirmationItemOut(
                        id=item.id,
                        content=item.content,
                        category=item.category,
                        confidence=item.confidence,
                        evidence=item.evidence,
                        raw_input_id=item.raw_input_id,
                        thumb_url=_thumb(item.raw_input_id),
                        kind=item.kind,
                        created_at=item.created_at,
                    )
                    for item in group.items
                ],
            )
            for group in groups
        ]
    )


@router.post(
    "/{fact_id}/confirm",
    response_model=DataResponse[ConfirmResultOut],
    summary="确认一条",
    dependencies=[Depends(require_csrf)],
)
async def confirm(
    fact_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """确认并级联：派生标签 → 重算累计 → 标记综述过期。重复确认报 400。"""
    result = await confirm_service.confirm_fact(session, fact_id, user.id)
    return ok(
        ConfirmResultOut(fact_id=result.fact_id, status=result.status, cascaded=result.cascaded)
    )


@router.post(
    "/{fact_id}/reject",
    response_model=DataResponse[ConfirmResultOut],
    summary="驳回一条",
    dependencies=[Depends(require_csrf)],
)
async def reject(
    fact_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """驳回：数据保留（用来改进抽取），档案页不展示。"""
    result = await confirm_service.reject_fact(session, fact_id)
    return ok(
        ConfirmResultOut(fact_id=result.fact_id, status=result.status, cascaded=result.cascaded)
    )


@router.patch(
    "/{fact_id}",
    response_model=DataResponse[ConfirmResultOut],
    summary="改内容/改归属后确认",
    dependencies=[Depends(require_csrf)],
)
async def reassign(
    fact_id: int,
    payload: ReassignIn,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """改归属或改内容，然后直接确认。改归属会重新派生标签。"""
    result = await confirm_service.reassign_fact(
        session,
        fact_id,
        user.id,
        person_id=payload.person_id,
        content=payload.content,
        category=payload.category,
    )
    return ok(
        ConfirmResultOut(fact_id=result.fact_id, status=result.status, cascaded=result.cascaded)
    )


@router.post(
    "/batch",
    response_model=DataResponse[BatchOut],
    status_code=status.HTTP_200_OK,
    summary="批量确认",
    dependencies=[Depends(require_csrf)],
)
async def batch(
    payload: BatchIn,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """一次确认多条。不是 pending 的跳过，不整体失败。

    单次上限 200：一个事务里改太多行会拖长锁持有时间，也让失败回滚代价变大。
    超过时给明确提示，而不是让界面收到 422 之后没话可说。
    """
    if len(payload.fact_ids) > BATCH_LIMIT:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"一次最多确认 {BATCH_LIMIT} 条，请分批提交",
            status_code=400,
        )
    result = await confirm_service.batch_confirm(session, payload.fact_ids, user.id)
    return ok(
        BatchOut(confirmed=result.confirmed, skipped=result.skipped, skipped_ids=result.skipped_ids)
    )
