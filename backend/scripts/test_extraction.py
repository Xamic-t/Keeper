"""30 张真实截图的抽取测试。跑完生成一份可勾选的核对表。

用法（在 backend/ 目录下）：

    # 先跑 3 张试试水
    uv run python scripts/test_extraction.py --dir ~/testdata/screenshots --user va --limit 3

    # 全量
    uv run python scripts/test_extraction.py --dir ~/testdata/screenshots --user va

    # 你在核对表里把抽错的条目标成 [x]，然后算准确率
    uv run python scripts/test_extraction.py --score ~/testdata/抽取核对.md

为什么直接调 service 而不是打 HTTP：
    不用起后端、不用管 cookie 和 CSRF，而且能一次看到模型原始输出。
    它跑的就是线上同一套代码（ingest + extract），不是另写一份逻辑。

三个前提（缺一个结果就没意义）：
    ① .env 里 DEEPSEEK_API_KEY 已填，且 scripts/check_model.py 通过
    ② 数据库在跑（docker compose up -d db）
    ③ **截图里出现的那个人，必须已经在系统里建好了**——
       归属判不出来时（person_id 为空），事实根本不会落库，准确率无从统计。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# 让 `app` 可被 import（本文件在 backend/scripts/ 下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models.event import Event  # noqa: E402
from app.models.fact import Fact  # noqa: E402
from app.models.input import ChatSegment, RawInput  # noqa: E402
from app.models.ledger import Ledger  # noqa: E402
from app.models.person import Person  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import extract as extract_service  # noqa: E402
from app.services import ingest as ingest_service  # noqa: E402
from app.services.ingest import UploadItem  # noqa: E402

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

# 图片头魔数 → 后缀（跟 api/ingest.py 同一套判断，不信扩展名）
_MAGIC = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
)


def _sniff_suffix(content: bytes) -> str | None:
    """按魔数判断图片类型。识别不出返回 None。"""
    for magic, suffix in _MAGIC:
        if content.startswith(magic):
            return suffix
    if len(content) > 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return None


@dataclass
class Item:
    """一张图跑完之后，落库里的事实 / 事件 / 往来。"""

    index: int
    filename: str
    raw_input_id: int
    status: str
    error: str | None = None
    duplicate: bool = False
    segments: int = 0
    person_id: int | None = None
    person_name: str = ""
    facts: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    ledgers: list[dict] = field(default_factory=list)
    raw_reply: str = ""


async def _find_user(username: str) -> int:
    """按登录名找 user_id。users 表没开 RLS，用无变量的 session 就行。"""
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == username.strip()))
        ).scalar_one_or_none()
        if user is None or user.id is None:
            raise SystemExit(
                f"找不到账号 {username!r}。\n"
                f"先在系统里登录取一个账号，或用 --user 指定正确的登录名。"
            )
        return user.id


async def _person_count(user_id: int) -> int:
    """数一数这个人名下有几个联系人。为 0 的话事实落不了库。"""
    async with SessionLocal() as session:
        session.info["rls_user_id"] = user_id
        rows = (await session.execute(select(Person.id))).scalars().all()
        return len(rows)


async def _dump(item: Item, user_id: int) -> None:
    """把这张图落库的结果读出来，填进 Item。"""
    async with SessionLocal() as session:
        session.info["rls_user_id"] = user_id

        raw = await session.get(RawInput, item.raw_input_id)
        if raw is not None:
            item.status = raw.status
            item.error = raw.error_msg
            item.person_id = raw.person_id
            if raw.person_id is not None:
                person = await session.get(Person, raw.person_id)
                item.person_name = person.nickname if person is not None else ""

        item.segments = len(
            (
                await session.execute(
                    select(ChatSegment.id).where(ChatSegment.raw_input_id == item.raw_input_id)
                )
            )
            .scalars()
            .all()
        )

        facts = (
            (
                await session.execute(
                    select(Fact).where(
                        Fact.source_type == "screenshot", Fact.source_id == item.raw_input_id
                    )
                )
            )
            .scalars()
            .all()
        )
        item.facts = [
            {
                "id": f.id,
                "content": f.content,
                "category": f.category,
                "confidence": f.confidence,
                "evidence": f.evidence or "",
            }
            for f in facts
        ]

        events = (
            (
                await session.execute(
                    select(Event).where(
                        Event.source_type == "screenshot", Event.source_id == item.raw_input_id
                    )
                )
            )
            .scalars()
            .all()
        )
        item.events = [
            {
                "id": e.id,
                "title": e.title,
                "type": e.type,
                "occurs_on": e.occurs_on.isoformat(),
            }
            for e in events
        ]

        ledgers = (
            (
                await session.execute(
                    select(Ledger).where(
                        Ledger.source_type == "screenshot", Ledger.source_id == item.raw_input_id
                    )
                )
            )
            .scalars()
            .all()
        )
        item.ledgers = [
            {
                "id": x.id,
                "direction": "收" if x.direction == "in" else "付",
                "kind": x.kind,
                "amount": str(x.amount),
                "occurred_at": x.occurred_at.isoformat(),
                "note": x.note or "",
            }
            for x in ledgers
        ]


async def _run_one(
    index: int, path: Path, user_id: int, person_id: int | None
) -> Item:
    """一张图：读字节 → 落盘去重 → 抽取 → 读回结果。"""
    content = path.read_bytes()
    suffix = _sniff_suffix(content)
    if suffix is None:
        return Item(
            index=index,
            filename=path.name,
            raw_input_id=0,
            status="skipped",
            error="不是 jpg / png / webp（按文件头判断）",
        )

    # 每一次 ingest 用独立 session，避免相互影响
    async with SessionLocal() as session:
        session.info["rls_user_id"] = user_id
        results = await ingest_service.ingest_images(
            session,
            user_id,
            [UploadItem(filename=path.name, content=content, suffix=suffix)],
            person_id,
        )
    result = results[0]

    if result.duplicate:
        item = Item(
            index=index,
            filename=path.name,
            raw_input_id=result.raw_input_id or 0,
            status=result.status,
            duplicate=True,
        )
        await _dump(item, user_id)
        return item

    if result.error is not None or result.raw_input_id is None:
        return Item(
            index=index,
            filename=path.name,
            raw_input_id=result.raw_input_id or 0,
            status="failed",
            error=result.error or "落盘失败",
        )

    # 抽取（后台任务在线上是 BackgroundTasks，这里同步等它跑完）
    extraction = await extract_service.extract_from_raw_input(result.raw_input_id, user_id)

    item = Item(
        index=index,
        filename=path.name,
        raw_input_id=result.raw_input_id,
        status="done",
        raw_reply=getattr(extraction, "_raw_reply", ""),
    )
    await _dump(item, user_id)
    return item


def _write_report(items: list[Item], out_path: Path, person_name: str | None) -> None:
    """把结果写成一份可勾选的核对表。"""
    total_facts = sum(len(i.facts) for i in items)
    total_events = sum(len(i.events) for i in items)
    total_ledgers = sum(len(i.ledgers) for i in items)
    done = sum(1 for i in items if i.status == "done")
    failed = [i for i in items if i.status not in {"done"}]

    lines: list[str] = []
    lines.append("# 抽取核对表")
    lines.append("")
    lines.append(f"> 生成时间：{datetime.now(UTC).astimezone().isoformat(timespec='seconds')}")
    lines.append(f"> 测试对象：{person_name or '（未指定，按截图内容自动归属）'}")
    lines.append(f"> 图片 {len(items)} 张：成功 {done} / 失败 {len(failed)}")
    lines.append("")
    lines.append("**怎么用**：逐条读「内容」，对照「出处」。")
    lines.append("**抽错的那一条**，把前面的 `- [ ]` 改成 `- [x]`。抽对的不用动。")
    lines.append("改完保存，然后跑：`uv run python scripts/test_extraction.py --score <这个文件的路径>`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"| 项 | 数量 |")
    lines.append("|---|---|")
    lines.append(f"| 图片 | {len(items)} |")
    lines.append(f"| 抽取成功 | {done} |")
    lines.append(f"| 抽取出的事实 | {total_facts} |")
    lines.append(f"| 抽取出的生日/事件 | {total_events} |")
    lines.append(f"| 抽取出的往来金额 | {total_ledgers} |")
    lines.append("")
    if failed:
        lines.append("### 失败的图")
        lines.append("")
        for item in failed:
            lines.append(f"- `{item.filename}` — {item.status}：{item.error or '未知'}")
        lines.append("")
    lines.append("---")
    lines.append("")

    for item in items:
        node = "✅" if item.status == "done" else "❌"
        dup = "（重复图，已跳过抽取）" if item.duplicate else ""
        lines.append(f"## {item.index:02d} · {item.filename} {node}{dup}")
        lines.append("")
        lines.append(
            f"状态 `{item.status}` · 对话 {item.segments} 段 · "
            f"事实 {len(item.facts)} · 事件 {len(item.events)} · 往来 {len(item.ledgers)}"
        )
        lines.append("")
        if item.person_name:
            lines.append(f"> 归属：**{item.person_name}**（person_id={item.person_id}）")
        elif item.status == "done":
            lines.append(
                "> 归属：**未判定** —— 模型没从候选名单里认出聊天对象，"
                "所以事实/事件/往来全部没落库（只存了对话原文）。"
            )
        lines.append("")

        if item.error:
            lines.append(f"> 错误：`{item.error}`")
            lines.append("")

        for fact in item.facts:
            conf = f"{fact['confidence']:.2f}"
            lines.append(
                f"- [ ] **事实** · {fact['category']} · 置信 {conf} — "
                f"{fact['content']} ｜ 出处：{fact['evidence'] or '（无）'}"
            )
        for event in item.events:
            lines.append(
                f"- [ ] **事件** · {event['type']} · {event['occurs_on']} — {event['title']}"
            )
        for ledger in item.ledgers:
            lines.append(
                f"- [ ] **往来** · {ledger['direction']} · {ledger['kind']} · "
                f"{ledger['amount']} 元 · {ledger['occurred_at']} — {ledger['note'] or '（无备注）'}"
            )

        if not (item.facts or item.events or item.ledgers):
            lines.append("_这张图没抽出任何东西。_")
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n核对表已写到：{out_path}")


def _score(path: Path) -> int:
    """数勾选结果，算准确率。"""
    text = path.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("- [")]
    if not rows:
        print("这个文件里没有可判定的条目（应该以 `- [ ]` 或 `- [x]` 开头）。")
        return 1

    wrong = [row for row in rows if row.startswith("- [x]") or row.startswith("- [X]")]
    total = len(rows)
    right = total - wrong
    rate = right / total * 100 if total else 0.0

    by_kind: dict[str, list[int]] = {}
    for row in rows:
        for kind in ("事实", "事件", "往来"):
            if f"**{kind}**" in row:
                stat = by_kind.setdefault(kind, [0, 0])
                stat[0] += 1
                if row.startswith("- [x]") or row.startswith("- [X]"):
                    stat[1] += 1
                break

    print("=" * 60)
    print(f"总计 {total} 条，错 {len(wrong)} 条，对 {right} 条")
    print(f"准确率 = {right}/{total} = {rate:.1f}%   （验收线 75%）")
    print("-" * 60)
    for kind, (count, bad) in sorted(by_kind.items()):
        good = count - bad
        sub = good / count * 100 if count else 0.0
        print(f"  {kind}：{good}/{count} = {sub:.1f}%")
    print("=" * 60)

    if rate >= 75:
        print("✅ 达到验收线。")
    else:
        print("❌ 没到 75%。下一步：换模型（VISION_MODEL）或按错例改 prompts/v1_extract.md。")
    return 0


async def _main(args: argparse.Namespace) -> int:
    if args.score:
        return _score(Path(args.score).expanduser())

    if not args.dir and not args.files:
        print("要么给 --dir 指定截图目录，要么用 --files 指定具体几张贴进来，要么用 --score 算准确率。")
        return 2

    if args.files:
        # 直接点名几个文件：适合「只试水三张」的场景，不用先整理目录
        files = [Path(item).expanduser() for item in args.files]
        missing = [str(p) for p in files if not p.is_file()]
        if missing:
            print("这些文件不存在或读不到：")
            for path in missing:
                print(f"  {path}")
            return 2
    else:
        folder = Path(args.dir).expanduser()
        if not folder.is_dir():
            print(f"目录不存在：{folder}")
            return 2
        files = sorted(
            p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        )
        if not files:
            print(f"{folder} 里没有 jpg / png / webp 图片。")
            return 2

    if args.limit:
        files = files[: args.limit]
    base_dir = files[0].parent

    key = settings.deepseek_api_key.get_secret_value() if settings.deepseek_api_key else None
    if not key:
        print("❌ DEEPSEEK_API_KEY 是空的。先填 .env，再跑 scripts/check_model.py 确认能看图。")
        return 2

    user_id = await _find_user(args.user)
    count = await _person_count(user_id)
    print(f"账号 {args.user}（id={user_id}），名下 {count} 个联系人")
    if count == 0:
        print("\n❌ 这个账号还没有任何联系人。")
        print("   截图里的那个人必须先在系统里存在，否则模型判不出归属，事实不会落库。")
        print("   先在「人」页建一个，或者在网页里传一张图让它自动建。")
        return 2

    person_name = None
    if args.person_id:
        async with SessionLocal() as session:
            session.info["rls_user_id"] = user_id
            person = await session.get(Person, args.person_id)
            if person is None:
                print(f"❌ person_id={args.person_id} 不属于这个账号，或不存在。")
                return 2
            person_name = person.nickname

    print(f"待处理 {len(files)} 张图，模型 {settings.vision_model}\n")

    items: list[Item] = []
    for index, path in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] {path.name} …", end=" ", flush=True)
        try:
            item = await _run_one(index, path, user_id, args.person_id)
        except Exception as exc:  # noqa: BLE001 — 一张失败不该中断整批
            print(f"异常 {type(exc).__name__}: {exc}")
            items.append(
                Item(
                    index=index,
                    filename=path.name,
                    raw_input_id=0,
                    status="crashed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        if item.duplicate:
            print(f"重复图（已存在 id={item.raw_input_id}），已跳过抽取")
        elif item.status == "done":
            who = item.person_name or "⚠️ 归属未判定"
            print(
                f"ok · 归属={who} · 事实 {len(item.facts)} / 事件 {len(item.events)} / "
                f"往来 {len(item.ledgers)}"
            )
        else:
            print(f"失败：{item.error}")
        items.append(item)

    out = Path(args.out).expanduser() if args.out else base_dir / "抽取核对.md"
    _write_report(items, out, person_name)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Keeper 抽取准确率测试")
    parser.add_argument("--dir", help="截图目录")
    parser.add_argument(
        "--files",
        nargs="+",
        default=None,
        help="直接指定几张图（和 --dir 二选一），例如 --files ~/a.png ~/b.png",
    )
    parser.add_argument("--user", default="va", help="用哪个账号跑（登录名），默认 va")
    parser.add_argument("--person-id", type=int, default=None, help="强制挂到某个人身上")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 张")
    parser.add_argument("--out", default=None, help="核对表输出路径")
    parser.add_argument("--score", default=None, help="传入核对表路径，算准确率")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
