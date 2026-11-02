"""登录限流的内存边界。

`RateLimiter` 的 key 来自外部输入（IP）。原实现只清"当前 key 自己的过期时间戳"，
从不删 key 本身——扫 10 万个 IP 就留 10 万个空 deque，进程内存只涨不落。
登录接口正好是唯一一个"未登录也能打到"的写入口，最容易被扫。

这里钉住三件事：
  ① 死 key 会被回收（不无限增长）
  ② 回收**不改变限流结果**（这是最容易改出 bug 的地方）
  ③ 还在窗口内的 key 一个都不许误删
"""

import pytest

from app.core import ratelimit
from app.core.ratelimit import RateLimiter


class FakeClock:
    """可控时钟。直接把 monotonic 换成它，避免用例要真的等 60 秒。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(ratelimit.time, "monotonic", fake)
    return fake


def test_expired_keys_are_reclaimed(clock: FakeClock) -> None:
    """扫 1000 个 IP、全部过期后，字典必须收回到接近 0。"""
    limiter = RateLimiter(limit=5, window_seconds=60)

    for index in range(1000):
        limiter.check(f"10.0.0.{index}")

    # 窗口内：1000 个 key 都是"活跃 IP"，这时留着是对的，不是泄漏
    assert limiter.size == 1000

    clock.advance(61)
    # 每 64 次 check 扫一次，1000 次之后至少扫过很多轮；再触发一轮即可
    for index in range(64):
        limiter.check(f"172.16.0.{index}")

    # 只剩刚才这 64 个新 key（可能被最后一轮扫描清掉一部分，上限按 64 算）
    assert limiter.size <= 64


def test_sweep_does_not_change_rate_limit_result(clock: FakeClock) -> None:
    """回收前后，同一个 IP 的判定必须完全一致。"""
    limiter = RateLimiter(limit=3, window_seconds=60)

    for _ in range(3):
        assert limiter.check("1.2.3.4")[0] is True

    # 被限流，且给得出重试秒数
    allowed, retry_after = limiter.check("1.2.3.4")
    assert allowed is False
    assert retry_after >= 1

    # 制造一次扫描（第 64 次 check 触发）；别的 IP 不该影响这个 key
    for index in range(60):
        limiter.check(f"9.9.9.{index}")

    blocked, _ = limiter.check("1.2.3.4")
    assert blocked is False, "扫描把活跃 key 清掉了，等于限流失效"


def test_key_inside_window_is_never_dropped(clock: FakeClock) -> None:
    """窗口内刚访问过的 key，扫描时不许删。"""
    limiter = RateLimiter(limit=2, window_seconds=60)

    limiter.check("5.5.5.5")  # 1 次
    clock.advance(30)

    # 触发扫描多次
    for round_index in range(3):
        for index in range(64):
            limiter.check(f"8.8.{round_index}.{index}")

    # 30 秒前的那次仍在窗口内 → 再打两次就该被拦
    assert limiter.check("5.5.5.5")[0] is True  # 第 2 次
    assert limiter.check("5.5.5.5")[0] is False  # 第 3 次超限


def test_after_window_key_starts_over(clock: FakeClock) -> None:
    """过期后重新计数：先被拦，窗口过了就放行。"""
    limiter = RateLimiter(limit=1, window_seconds=60)

    assert limiter.check("6.6.6.6")[0] is True
    assert limiter.check("6.6.6.6")[0] is False

    clock.advance(61)
    assert limiter.check("6.6.6.6")[0] is True


def test_reset_and_clear(clock: FakeClock) -> None:
    """登录成功清计数；clear 连扫描计数一起归零。"""
    limiter = RateLimiter(limit=1, window_seconds=60)
    limiter.check("7.7.7.7")
    assert limiter.check("7.7.7.7")[0] is False

    limiter.reset("7.7.7.7")
    assert limiter.check("7.7.7.7")[0] is True

    limiter.clear()
    assert limiter.size == 0
