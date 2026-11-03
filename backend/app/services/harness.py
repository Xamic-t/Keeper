"""Harness 编排：常驻 `dsh --profile sdk` 子进程，走 stdio JSON-RPC。

**为什么从 headless 换成常驻 sdk**（2026-11-04 实测）：
  · headless 是"一轮一个进程"，每次启动要 26–36 秒（加载整套插件图）。
    用户发一句话等半分钟，这不是优化问题，是能不能用的问题。
  · sdk profile 是同一个运行时，但**一直在那儿等**：
    initialize 26.1s（只一次）→ 第一轮 2.2s → 第二轮 0.74s。
    也就是把人等的那 26 秒挪到"进程启动"上，之后每轮都是秒级。
  · 冷启动还是有代价，所以**登录后就预热**（`prewarm`），
    等用户真的开口时进程早就绪了。

**为什么一个用户一个进程**：keeper-tools 在模块加载时读 `KEEPER_ASSISTANT_TOKEN`
（`const TOKEN = process.env...`），一个进程只能有一个身份。多用户共进程就得改工具插件，
而工具插件是我们唯一不想让它感知"用户"这一层的地方。一个用户一个进程，隔离天然成立。

三条纪律（沿用 §19.7）：
  ① **每个进程带用户级令牌**（`assistant_token.issue`），绝不用全局 token 冒充别人
  ② 模型 key 只在本进程环境里传，不落盘、不写日志
  ③ 任何失败（没装 / 超时 / 崩）都只让这一轮失败，上层回退原生对话
"""

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.config import settings
from app.services import assistant_token

logger = logging.getLogger(__name__)

# 工具名 → 中文标签（前端拿去显示"读今日待办"而不是 keeper_today）
TOOL_LABELS = {
    "keeper_today": "读今日待办",
    "keeper_find_person": "找人",
    "keeper_person": "读档案",
    "keeper_facts": "读事实",
    "keeper_ledger": "读往来",
    "keeper_search": "检索资料",
    "keeper_create_todo": "建待办",
    "keeper_create_event": "建事件",
    "keeper_add_fact": "记事实",
}

# 常驻进程的令牌有效期。比单轮 180s 长得多：进程活着就一直用它调接口。
# 90 分钟强制重启一次（见 _RESTART_AFTER_S），所以令牌实际最长活 90 分钟。
_TOKEN_TTL_S = 7200
# 超过这个时长就重启进程，换一个新令牌（也顺手回收 Node 的内存）
_RESTART_AFTER_S = 5400.0
# 闲置多久回收进程。回收后下一次对话要重新付 26 秒，所以别设太短
_IDLE_TTL_S = 900.0
# 冷启动上限：实测 26s，给到 120s 留足慢机器/首次下模型的余量
_START_TIMEOUT_S = 120.0


class HarnessError(RuntimeError):
    """侧车不可用或这一轮失败。上层据此决定是否降级到原生对话。"""


def dsh_binary() -> Path | None:
    """dsh 的可执行文件；没装就返回 None（不抛，调用方决定降级）。"""
    candidate = Path(settings.harness_dir) / "node_modules" / ".bin" / "dsh"
    return candidate if candidate.exists() else None


def available() -> bool:
    """能不能用 Harness：开关 + 可执行文件 + 覆盖层配置文件都在。"""
    if not settings.harness_enabled:
        return False
    patch = Path(settings.harness_dir) / "cordis.patch.yml"
    return dsh_binary() is not None and patch.exists()


def _env(user_id: int) -> dict[str, str]:
    """子进程环境。模型 key 与用户令牌都只在这里出现，不进日志。"""
    env = dict(os.environ)
    env["KEEPER_API"] = env.get("KEEPER_API", "http://127.0.0.1:8000")
    env["KEEPER_ASSISTANT_TOKEN"] = assistant_token.issue(user_id, ttl_s=_TOKEN_TTL_S)
    key = settings.deepseek_api_key
    if key is not None:
        env["DEEPSEEK_API_KEY"] = key.get_secret_value()
    # 本机工具是在 cordis.patch.yml 里按插件 id 关掉的（DSH_DISABLE_TOOLS 这个环境变量
    # 实测不生效，写了等于骗自己）。这里不再设它
    return env


def _workspace(user_id: int) -> Path:
    """这个用户的会话工作目录。空目录，模型就算越界也只能在这里写。"""
    folder = Path(settings.data_dir) / "harness" / f"u{user_id}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _summary(tool: str, result: Any, limit: int = 80) -> str:
    """把工具返回翻译成一行中文。

    直接把 JSON 塞进气泡最省事，但用户看到的是
    `{"total":1,"items":[{"id":5463,...` —— 既看不懂，也把内部字段漏出去了。
    所以按工具类型提炼一句：查到几个人、几条事实、几笔往来。
    """
    data = result
    if isinstance(result, str):
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            data = None

    if isinstance(data, dict):
        # 写操作返回的是 "动作结果"：{type, ok, summary, ref_id, reason}
        # 人话就在 summary 里，别再套一层 JSON
        if data.get("type") in {"create_todo", "create_event", "add_fact"}:
            # 失败时给人话的是 reason（"没认出「张三」"），成功时是 summary。
            # 都为空就退回"建待办失败"这种笼统说法——总比把整段 JSON 甩到脸上强。
            text = data.get("reason") if not data.get("ok") else data.get("summary")
            return str(text or data.get("summary") or "")[:limit]
        if tool == "keeper_find_person":
            items = data.get("items") or []
            names = "、".join(str(item.get("nickname") or "") for item in items[:3])
            return f"找到 {data.get('total', len(items))} 人" + (f"：{names}" if names else "")
        if tool == "keeper_facts":
            items = data.get("items") or []
            return f"{str(data.get('nickname') or '')} 的 {len(items)} 条事实"
        if tool == "keeper_today":
            return f"今天 {len(data.get('tasks') or [])} 件事"
        if tool == "keeper_ledger":
            return f"{len(data.get('items') or [])} 笔往来"
        if tool == "keeper_person":
            return f"{data.get('nickname', '')} 的档案"
        if tool == "keeper_search":
            return f"检索到 {len(data.get('items') or [])} 段资料"

    text = json.dumps(data, ensure_ascii=False) if data is not None else str(result)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


class _Runtime:
    """一个用户的常驻 dsh 进程。对外只有 `turn()`，内部管收发与恢复。"""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        # 会话 id 带时间戳：dsh 的会话会落盘，跨进程复用同一个 id 会报 already exists。
        # 历史由我们自己管（conversation.recent_dialogue 拼进任务），不需要侧车续聊。
        self.session_id = f"keeper-{user_id}-{int(time.time())}"
        self.started_at = 0.0
        self.last_used = 0.0
        self.proc: asyncio.subprocess.Process | None = None

        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pump: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._stable = False

    # ---- 进程生命周期 ----

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    @property
    def ready(self) -> bool:
        """进程在、initialize 回了、也没到期——可以直接接活。"""
        return self.running and self._stable and not self.stale()

    def stale(self, now: float | None = None) -> bool:
        """该不该重启：跑太久了（换令牌）或闲置太久（回收内存）。"""
        moment = time.monotonic() if now is None else now
        if not self.running:
            return True
        if moment - self.started_at > _RESTART_AFTER_S:
            return True
        return moment - self.last_used > _IDLE_TTL_S

    async def ensure_started(self) -> None:
        """确保进程起来了、initialize 也回了。并发调用只会启动一次。"""
        async with self._start_lock:
            if self.running and self._stable:
                return
            await self._stop()
            await self._spawn()
            await self._handshake()

    async def _spawn(self) -> None:
        binary = dsh_binary()
        if binary is None or not available():
            raise HarnessError("Harness 不可用（没装或开关关闭）")

        harness_dir = Path(settings.harness_dir)
        self.proc = await asyncio.create_subprocess_exec(
            str(binary),
            "--profile",
            "sdk",
            "--patch",
            str(harness_dir / "cordis.patch.yml"),
            cwd=str(harness_dir),
            env=_env(self.user_id),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.started_at = time.monotonic()
        self.last_used = self.started_at
        self._stable = False
        self._pump = asyncio.create_task(self._read_loop())
        logger.info("harness started user_id=%s pid=%s", self.user_id, self.proc.pid)

    async def _handshake(self) -> None:
        """initialize。返回后这个运行时才算能接活。

        冷启动 26 秒都花在这里（加载插件图 + 拉一次模型清单），
        所以超时给到 120 秒；用户侧看到的是"启动中"。
        """
        result = await self._request(
            "initialize",
            {
                "cwd": str(_workspace(self.user_id)),
                "provider": "deepseek-official",
                "model": settings.vision_model,
            },
            timeout=_START_TIMEOUT_S,
        )
        if "error" in result:
            raise HarnessError(f"Harness 初始化失败：{result['error'].get('message', '')[:120]}")
        self._stable = True
        logger.info(
            "harness ready user_id=%s in %.1fs",
            self.user_id,
            time.monotonic() - self.started_at,
        )

    async def _stop(self) -> None:
        self._stable = False
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
            self._pump = None
        if self.proc is not None and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()
            with contextlib.suppress(Exception):
                await self.proc.wait()
        self.proc = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(HarnessError("Harness 进程已退出"))
        self._pending.clear()

    # ---- stdio 收发 ----

    async def _read_loop(self) -> None:
        """读 stdout 的每一行：response 交给等待者，notification 进事件队列。

        stdout 只有 JSON-RPC 帧（stderr 才是日志），这是 sdk profile 的约定，
        所以这里不需要"过滤噪声"。
        """
        assert self.proc is not None and self.proc.stdout is not None
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw.startswith("{"):
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue

            message_id = message.get("id")
            if message_id is not None and message_id in self._pending:
                future = self._pending.pop(message_id)
                if not future.done():
                    future.set_result(message)
                continue
            if message.get("method"):
                await self._events.put(message)

        # 进程没了：让所有等待者立刻失败，而不是干等到超时
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(HarnessError("Harness 进程意外退出"))
        self._pending.clear()

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self.proc is None or self.proc.stdin is None:
            raise HarnessError("Harness 进程没起来")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        frame = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self.proc.stdin.write((json.dumps(frame, ensure_ascii=False) + "\n").encode())
        with contextlib.suppress(Exception):
            await self.proc.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=timeout or settings.harness_timeout_s)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise HarnessError(f"Harness {method} 超时") from exc

    # ---- 一轮对话 ----

    async def turn(self, text: str) -> AsyncIterator[dict[str, Any]]:
        """跑一轮：发 prompt，边收事件边产出，直到 turn/end。

        产出的事件（上层直接转 SSE 给前端）：
          {"type":"tool_call","tool":..,"label":..,"args":{..}}
          {"type":"tool_result","tool":..,"label":..,"ok":bool,"summary":".."}
          {"type":"final","text":".."}
        """
        async with self._turn_lock:
            await self.ensure_started()
            self.last_used = time.monotonic()
            self._drain_events()

            accepted = await self._request(
                "session/prompt",
                {
                    "sessionId": self.session_id,
                    "contentBlocks": [{"type": "text", "text": text}],
                },
            )
            if "error" in accepted:
                # 会话被 dsh 侧拒绝（比如 id 撞了）：换一个 id 重来一次
                logger.warning(
                    "harness prompt rejected user_id=%s error=%s",
                    self.user_id,
                    str(accepted["error"].get("message"))[:120],
                )
                self.session_id = f"keeper-{self.user_id}-{int(time.time() * 1000)}"
                accepted = await self._request(
                    "session/prompt",
                    {
                        "sessionId": self.session_id,
                        "contentBlocks": [{"type": "text", "text": text}],
                    },
                )
                if "error" in accepted:
                    raise HarnessError("Harness 没有接这一轮")

            deadline = time.monotonic() + settings.harness_timeout_s
            call_names: dict[str, str] = {}
            answer = ""

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HarnessError("Harness 这一轮超时了")
                try:
                    message = await asyncio.wait_for(self._events.get(), timeout=remaining)
                except TimeoutError as exc:
                    raise HarnessError("Harness 这一轮超时了") from exc

                if message.get("method") != "session.event":
                    continue
                params = message.get("params") or {}
                if params.get("sessionId") != self.session_id:
                    continue
                event = params.get("event") or {}
                kind = event.get("type")
                data = event.get("data") or {}

                if kind == "tool/call":
                    tool = str(data.get("name") or "")
                    call_names[str(data.get("callId") or "")] = tool
                    yield {
                        "type": "tool_call",
                        "tool": tool,
                        "label": TOOL_LABELS.get(tool, tool),
                        "args": _parse_args(data.get("arguments")),
                    }
                elif kind == "tool/result":
                    message_body = data.get("message") or {}
                    tool = ""
                    source = message_body.get("source") or {}
                    if isinstance(source, dict) and source.get("callId"):
                        tool = call_names.get(str(source["callId"]), "")
                    if not tool:
                        tool = call_names.get(str(message_body.get("toolCallId") or ""), "")
                    yield {
                        "type": "tool_result",
                        "tool": tool,
                        "label": TOOL_LABELS.get(tool, tool),
                        "ok": not bool(message_body.get("isError")),
                        "summary": _summary(tool, _first_text(message_body)),
                    }
                elif kind == "assistant/message":
                    text_now = _answer_text(data.get("message") or {})
                    if text_now:
                        answer = text_now
                elif kind == "turn/end":
                    break

            self.last_used = time.monotonic()
            if not answer:
                raise HarnessError("Harness 没有给出回复")
            yield {"type": "final", "text": answer}

    def _drain_events(self) -> None:
        """丢掉上一轮残留的事件，避免把旧 tool_result 当成这一轮的。"""
        while not self._events.empty():
            self._events.get_nowait()


def _parse_args(raw: Any) -> dict[str, Any]:
    """工具参数是 JSON 字符串（模型生成的），解析不成对象就当空参数。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_text(message: dict[str, Any]) -> str:
    """工具消息里的正文（通常是 JSON 字符串），只取第一段文本。"""
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text") or "")
    return ""


def _answer_text(message: dict[str, Any]) -> str:
    """assistant 消息里的可见正文。

    一份消息可能同时带 reasoning 和 text；reasoning 是给自己看的，
    不该显示给用户，所以只取 `type == "text"` 的块。
    """
    parts = [
        str(block.get("text") or "")
        for block in message.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(part for part in parts if part.strip()).strip()


# ── 进程池：一个用户一个运行时 ─────────────────────────────

_runtimes: dict[int, _Runtime] = {}
_pool_lock = asyncio.Lock()


async def _get(user_id: int) -> _Runtime:
    """取（必要时创建）这个用户的运行时。顺手回收别人的闲置进程。"""
    async with _pool_lock:
        now = time.monotonic()
        for other_id, runtime in list(_runtimes.items()):
            if other_id != user_id and runtime.stale(now):
                await runtime._stop()
                _runtimes.pop(other_id, None)

        runtime = _runtimes.get(user_id)
        if runtime is None or runtime.stale(now):
            if runtime is not None:
                await runtime._stop()
            runtime = _Runtime(user_id)
            _runtimes[user_id] = runtime
        return runtime


async def stream_turn(
    user_id: int,
    task: str,
    *,
    session_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """跑一轮，产出规范化事件（`session_id` 参数保留兼容，当前不用）。"""
    runtime = await _get(user_id)
    try:
        async for event in runtime.turn(task):
            yield event
    except HarnessError:
        # 这一轮失败就把进程丢掉，下一轮重新起（可能只是进程卡死了）
        await runtime._stop()
        raise


def is_warm(user_id: int) -> bool:
    """这个用户的进程是不是已经在等着了（前端据此决定要不要提示"启动中"）。"""
    runtime = _runtimes.get(user_id)
    return runtime is not None and runtime.ready


async def prewarm(user_id: int) -> None:
    """登录/打开助理页时预热：把 26 秒冷启动挪到用户还在点页面的时候。

    失败只记日志，不影响登录——预热是优化，不是前置条件。
    """
    if not available():
        return
    try:
        runtime = await _get(user_id)
        await runtime.ensure_started()
    except Exception as exc:  # noqa: BLE001 - 预热不许影响主流程
        logger.info("harness prewarm skipped user_id=%s reason=%s", user_id, type(exc).__name__)


def schedule_prewarm(user_id: int) -> None:
    """后台预热，不阻塞当前请求。"""
    if not available() or not settings.harness_prewarm:
        return
    asyncio.create_task(prewarm(user_id))


async def shutdown_all() -> None:
    """应用退出时收掉所有进程，别留下孤儿 Node。"""
    async with _pool_lock:
        for runtime in list(_runtimes.values()):
            await runtime._stop()
        _runtimes.clear()
