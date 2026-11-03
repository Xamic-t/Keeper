"""Harness 适配层：可用性判断、工具结果翻译、失败语义。

不起真的 dsh 进程（那是 e2e/人工的事），这里钉的是**我们这层**：
  · 没装/没开时 available() 要老实返回 False，让上层降级而不是报错
  · 工具返回的 JSON 要翻成人话（别把内部字段漏到界面上）
  · 不可用时抛 HarnessError，上层据此回退内置对话
"""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from tempfile import mkdtemp

import pytest

from app.config import settings
from app.services import harness


@pytest.fixture
def workspace() -> Iterator[Path]:
    """自己的临时目录。`tmp_path` 在这台机器上会撞沙箱 shim（EEXIST）。"""
    root = Path(mkdtemp(prefix="keeper-harness-"))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def test_available_false_when_dir_missing(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    monkeypatch.setattr(settings, "harness_dir", str(workspace / "nope"))
    assert harness.available() is False


def test_available_false_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "harness_enabled", False)
    assert harness.available() is False


def test_available_true_when_binary_and_patch_exist(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    (workspace / "node_modules" / ".bin").mkdir(parents=True)
    binary = workspace / "node_modules" / ".bin" / "dsh"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    (workspace / "cordis.patch.yml").write_text("- insert: []\n", encoding="utf-8")
    monkeypatch.setattr(settings, "harness_dir", str(workspace))
    monkeypatch.setattr(settings, "harness_enabled", True)
    assert harness.available() is True


def test_summary_translates_find_person() -> None:
    raw = json.dumps({"total": 2, "items": [{"nickname": "王姐"}, {"nickname": "小雨"}]})
    assert harness._summary("keeper_find_person", raw) == "找到 2 人：王姐、小雨"


def test_summary_translates_facts() -> None:
    raw = json.dumps({"nickname": "王姐", "items": [{"content": "a"}, {"content": "b"}]})
    assert harness._summary("keeper_facts", raw) == "王姐 的 2 条事实"


def test_summary_translates_today() -> None:
    raw = json.dumps({"tasks": [{}, {}, {}]})
    assert harness._summary("keeper_today", raw) == "今天 3 件事"


def test_summary_falls_back_to_truncated_text() -> None:
    """未知工具或非 JSON：截断原文，但绝不整段塞进气泡。"""
    text = harness._summary("keeper_unknown", "x" * 200)
    assert len(text) <= 81
    assert text.endswith("…")


def test_summary_handles_none() -> None:
    assert harness._summary("keeper_facts", None) == "None"


async def test_stream_turn_raises_when_unavailable(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    monkeypatch.setattr(settings, "harness_dir", str(workspace))
    monkeypatch.setattr(settings, "harness_enabled", True)

    with pytest.raises(harness.HarnessError):
        async for _ in harness.stream_turn(1, "在吗"):
            pass


def test_tool_labels_cover_every_tool_we_ship() -> None:
    """工具名到中文的映射必须覆盖 keeper-tools 里注册的全部工具。"""
    expected = {
        "keeper_today",
        "keeper_find_person",
        "keeper_person",
        "keeper_facts",
        "keeper_ledger",
    }
    assert expected <= set(harness.TOOL_LABELS)
