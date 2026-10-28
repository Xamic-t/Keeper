"""所有表定义。Alembic 的 env.py 会 import 这个包来发现全部表。

新增表必须在这里导出，否则 autogenerate 会漏掉。
"""

from app.models.event import Event, Reminder
from app.models.fact import Fact, Tag
from app.models.input import ChatSegment, RawInput
from app.models.ledger import Ledger
from app.models.log import ExtractionLog
from app.models.notification import NotificationChannel
from app.models.person import Person
from app.models.relationship import Relationship
from app.models.todo import Todo
from app.models.user import User

__all__ = [
    "ChatSegment",
    "Event",
    "ExtractionLog",
    "Fact",
    "Ledger",
    "NotificationChannel",
    "Person",
    "RawInput",
    "Relationship",
    "Reminder",
    "Tag",
    "Todo",
    "User",
]
