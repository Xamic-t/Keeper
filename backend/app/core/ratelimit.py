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


class RateLimiter:
    """滑动窗口计数。同一个 key 的请求时间戳留在队列里。"""

    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> tuple[bool, int]:
        """返回 (是否放行, 还要等多少秒)。"""
        now = time.monotonic()
        window = self._hits[key]
        while window and now - window[0] > self.window_seconds:
            window.popleft()

        if len(window) >= self.limit:
            retry_after = int(self.window_seconds - (now - window[0])) + 1
            return False, max(retry_after, 1)

        window.append(now)
        return True, 0

    def reset(self, key: str) -> None:
        """登录成功后清掉计数。"""
        self._hits.pop(key, None)

    def clear(self) -> None:
        """清空全部计数（测试用）。"""
        self._hits.clear()


login_limiter = RateLimiter(limit=settings.login_rate_limit, window_seconds=60)
