"""统一错误处理。业务错误一律走 AppError，不裸抛 HTTPException。"""

import logging
from enum import StrEnum

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """业务错误码。前端按 code 判断分支，不依赖 message 文案。"""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_STATE = "INVALID_STATE"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    BAD_CREDENTIALS = "BAD_CREDENTIALS"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNPROCESSABLE = "UNPROCESSABLE"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    AI_UPSTREAM_ERROR = "AI_UPSTREAM_ERROR"
    AI_TIMEOUT = "AI_TIMEOUT"


class AppError(Exception):
    """业务异常。service 层抛这个，由全局 handler 统一转成响应。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detail: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail or {}


def _body(code: str, message: str, detail: dict[str, object] | None = None) -> dict[str, object]:
    """构造统一错误响应体。"""
    error: dict[str, object] = {"code": code, "message": message}
    if detail:
        error["detail"] = detail
    return {"error": error}


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。四类错误，一个出口。"""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("app_error code=%s path=%s", exc.code, request.url.path)
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code.value, exc.message, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic 的原始结构太啰嗦，只保留字段位置和原因
        errors = [
            {
                "field": ".".join(str(part) for part in item.get("loc", [])),
                "reason": item.get("msg", ""),
            }
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_body(ErrorCode.VALIDATION_ERROR.value, "参数校验失败", {"errors": errors}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.NOT_FOUND.value if exc.status_code == 404 else "HTTP_ERROR"
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(code, str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 500 只返回通用信息，堆栈只进日志
        logger.exception("unhandled error path=%s", request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_body(ErrorCode.INTERNAL_ERROR.value, "服务器内部错误"),
        )
