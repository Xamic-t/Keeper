"""登录限流：每 IP 每分钟最多 N 次尝试。

为什么自己写：框架没有现成的限流机制（19.6 第 1 条）。
为什么不做成中间件：只有登录接口需要限流，中间件要自己维护路径白名单，
用 `Depends` 挂在单个路由上更简单（框架的依赖注入就是干这个的）。

局限（要写在文档里）：计数存在进程内存里，多副本部署时会按副本数放大。
真要精确就把状态放进 Redis——V1 单容器，不值得。
"""

import time
from collections import defaultdict, deque

from app.config import settings

# 每多少次 check 顺手扫一遍死 key。64 是折中：
# 太小 → 每次请求都要遍历全表；太大 → 攻击期间多留一会儿垃圾。
_SWEEP_EVERY = 64


class RateLimiter:
    """滑动窗口计数。同一个 key 的请求时间戳留在队列里。"""

    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._checks = 0

    def check(self, key: str) -> tuple[bool, int]:
        """返回 (是否放行, 还要等多少秒)。"""
        now = time.monotonic()

        self._checks += 1
        if self._checks % _SWEEP_EVERY == 0:
            self._sweep(now)

        window = self._hits[key]
        while window and now - window[0] > self.window_seconds:
            window.popleft()

        if len(window) >= self.limit:
            retry_after = int(self.window_seconds - (now - window[0])) + 1
            return False, max(retry_after, 1)

        window.append(now)
        return True, 0

    def _sweep(self, now: float) -> None:
        """删掉已经滑出窗口的空 key。

        为什么必须做：`_hits` 的 key 由外部输入决定（IP）。不清理时，
        扫 10 万个 IP 就在内存里留 10 万个 deque，内存只涨不落；
        登录接口是唯一一个"未登录也能打到"的写入口，正好是最容易被扫的。

        判定用「最后一次命中已滑出窗口」：这种 key 下次再来请求时，
        deque 本来就会被 while 循环清空再重建，现在删掉不改变任何限流结果。
        """
        dead = [
            key
            for key, window in self._hits.items()
            if not window or now - window[-1] > self.window_seconds
        ]
        for key in dead:
            del self._hits[key]

    def reset(self, key: str) -> None:
        """登录成功后清掉计数。"""
        self._hits.pop(key, None)

    def clear(self) -> None:
        """清空全部计数（测试用）。"""
        self._hits.clear()
        self._checks = 0

    @property
    def size(self) -> int:
        """当前占用的 key 数量（测试与监控用）。"""
        return len(self._hits)


login_limiter = RateLimiter(limit=settings.login_rate_limit, window_seconds=60)
