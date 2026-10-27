"""日志配置。JSON lines，一行一条，便于 grep 和后续接日志系统。

隐私红线：只记 person_id，不记人名和聊天原文。
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# 允许通过 extra= 传进日志的业务字段
_EXTRA_FIELDS = (
    "trace_id",
    "user_id",
    "method",
    "path",
    "status",
    "duration_ms",
)


class JsonFormatter(logging.Formatter):
    """把 LogRecord 序列化成单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    """初始化根日志。重复调用会覆盖上一次配置。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn 自带的 access log 字段太散，统一由中间件记
    logging.getLogger("uvicorn.access").disabled = True
