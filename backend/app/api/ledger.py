"""往来路由：明细 / 新增 / 修改 / 删除。

对应文档：specs/07-ledger.md 5.6
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import DataResponse, PageResponse, ok, page_meta
from app.deps import CurrentUser, get_current_user, get_session, require_csrf
from app.services import ledger as ledger_service

router = APIRouter(tags=["ledger"])

Money = Annotated[
    Decimal,
    Field(gt=0, max_digits=12, decimal_places=2, description="金额，永远是正数"),
]


class LedgerOut(BaseModel):
    id: int
    person_id: int
    direction: str
    kind: str
    amount: Decimal
    currency: str
    occurred_at: date
    due_date: date | None = None
    settled_at: date | None = None
    note: str | None = None
    created_at: datetime


class LedgerCreateIn(BaseModel):
    person_id: int
    direction: str = Field(pattern="^(in|out)$", description="in=他给我，out=我给他")
    kind: str = Field(pattern="^(tip|order|gift|payment|refund)$")
    amount: Money
    occurred_at: date
    due_date: date | None = None
    note: str | None = Field(default=None, max_length=2000)


class LedgerUpdateIn(BaseModel):
    direction: str | None = Field(default=None, pattern="^(in|out)$")
    kind: str | None = Field(default=None, pattern="^(tip|order|gift|payment|refund)$")
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    occurred_at: date | None = None
    due_date: date | None = None
    settled_at: date | None = None
    note: str | None = Field(default=None, max_length=2000)


def _out(item: Any) -> LedgerOut:
    return LedgerOut(
        id=item.id or 0,
        person_id=item.person_id,
        direction=item.direction,
        kind=item.kind,
        amount=item.amount,
        currency=item.currency,
        occurred_at=item.occurred_at,
        due_date=item.due_date,
        settled_at=item.settled_at,
        note=item.note,
        created_at=item.created_at,
    )


@router.get(
    "/persons/{person_id}/ledger",
    response_model=PageResponse[LedgerOut],
    summary="某人的往来明细",
)
async def list_ledger(
    person_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = 1,
    size: int = 50,
) -> dict[str, Any]:
    """按发生时间倒序返回明细，附带总数。"""
    offset = max(page - 1, 0) * size
    rows, total = await ledger_service.list_ledger(session, person_id, offset=offset, limit=size)
    return ok([_out(item) for item in rows], page_meta(total, page, size))


@router.post(
    "/ledger",
    response_model=DataResponse[LedgerOut],
    status_code=status.HTTP_201_CREATED,
    summary="新增往来",
    dependencies=[Depends(require_csrf)],
)
async def create_ledger(
    payload: LedgerCreateIn,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """新增一笔，并同步累计与结款日历事件。"""
    item = await ledger_service.create_ledger(
        session,
        user.id,
        person_id=payload.person_id,
        direction=payload.direction,
        kind=payload.kind,
        amount=payload.amount,
        occurred_at=payload.occurred_at,
        due_date=payload.due_date,
        note=payload.note,
    )
    return ok(_out(item))


@router.patch(
    "/ledger/{ledger_id}",
    response_model=DataResponse[LedgerOut],
    summary="改往来",
    dependencies=[Depends(require_csrf)],
)
async def update_ledger(
    ledger_id: int,
    payload: LedgerUpdateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """改一笔往来。改完自动重算累计。"""
    item = await ledger_service.update_ledger(
        session, ledger_id, payload.model_dump(exclude_unset=True)
    )
    return ok(_out(item))


@router.delete(
    "/ledger/{ledger_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删往来",
    dependencies=[Depends(require_csrf)],
)
async def delete_ledger(
    ledger_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """删一笔往来，累计随之回退。"""
    await ledger_service.delete_ledger(session, ledger_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
