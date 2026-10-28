"""待办路由：列表 / 新增 / 改 / 删。

对应文档：specs/10-todos.md 5.6
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import DataResponse, ok
from app.deps import CurrentUser, get_current_user, get_session, require_csrf
from app.services import todo as todo_service

router = APIRouter(prefix="/todos", tags=["todos"])


class TodoOut(BaseModel):
    id: int
    person_id: int | None = None
    title: str
    detail: str | None = None
    source: str
    source_id: int | None = None
    ai_reason: str | None = None
    due_date: date | None = None
    status: str
    done_at: datetime | None = None
    created_at: datetime


class TodoCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    person_id: int | None = None
    source: Literal["template", "ai", "reminder", "manual"] = "manual"
    ai_reason: str | None = Field(default=None, max_length=2000)


class TodoUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    person_id: int | None = None
    status: Literal["open", "done", "dropped"] | None = None
    ai_reason: str | None = Field(default=None, max_length=2000)


def _out(item: Any) -> TodoOut:
    return TodoOut(
        id=item.id or 0,
        person_id=item.person_id,
        title=item.title,
        detail=item.detail,
        source=item.source,
        source_id=item.source_id,
        ai_reason=item.ai_reason,
        due_date=item.due_date,
        status=item.status,
        done_at=item.done_at,
        created_at=item.created_at,
    )


@router.get("", response_model=DataResponse[list[TodoOut]], summary="待办列表")
async def list_todos(
    session: Annotated[AsyncSession, Depends(get_session)],
    status_filter: Annotated[
        str | None, Query(alias="status", description="open / done / dropped / all")
    ] = "open",
    due_before: date | None = None,
    person_id: int | None = None,
) -> dict[str, Any]:
    """默认只返回未完成的，按截止日排序（空的排最后）。"""
    items = await todo_service.list_todos(
        session, status=status_filter, due_before=due_before, person_id=person_id
    )
    return ok([_out(item) for item in items])


@router.post(
    "",
    response_model=DataResponse[TodoOut],
    status_code=status.HTTP_201_CREATED,
    summary="新增待办",
    dependencies=[Depends(require_csrf)],
)
async def create_todo(
    payload: TodoCreateIn,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """手动新增一条待办。"""
    item = await todo_service.create_todo(
        session,
        user.id,
        title=payload.title,
        detail=payload.detail,
        due_date=payload.due_date,
        person_id=payload.person_id,
        source=payload.source,
        ai_reason=payload.ai_reason,
    )
    return ok(_out(item))


@router.patch(
    "/{todo_id}",
    response_model=DataResponse[TodoOut],
    summary="改待办 / 勾选完成",
    dependencies=[Depends(require_csrf)],
)
async def update_todo(
    todo_id: int,
    payload: TodoUpdateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """status 改成 done 会自动写 done_at，改回 open 会清空。"""
    item = await todo_service.update_todo(session, todo_id, payload.model_dump(exclude_unset=True))
    return ok(_out(item))


@router.delete(
    "/{todo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删待办",
    dependencies=[Depends(require_csrf)],
)
async def delete_todo(
    todo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """删一条待办。"""
    await todo_service.delete_todo(session, todo_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
