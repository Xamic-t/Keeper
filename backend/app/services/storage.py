"""文件存储：算哈希、落盘、删除、读回。

对应文档：specs/03-ingest.md 5.1

三条硬规则：
  ① 路径只由 `user_id` / `raw_input_id` 拼，**绝不拼用户可控的字符串**（防 ../ 穿越）
  ② 落盘按 `data/uploads/{user_id}/` 分目录——越权拿不到别人的截图
  ③ 删文件不抛异常：文件系统不支持回滚，删不掉只记 WARNING
"""

import hashlib
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# 允许的图片后缀。判断靠内容嗅探 + 后缀双保险（见 api/ingest.py）
ALLOWED_SUFFIX = {"jpg", "jpeg", "png", "webp"}


def upload_root() -> Path:
    """上传根目录。测试里可以指到临时目录。"""
    return Path(settings.data_dir) / "uploads"


def compute_hash(content: bytes) -> str:
    """算内容的 sha256 十六进制串，用于去重。"""
    return hashlib.sha256(content).hexdigest()


def save_upload(user_id: int, raw_input_id: int, content: bytes, suffix: str) -> str:
    """写文件并返回相对路径（存进 raw_inputs.file_path）。"""
    suffix = suffix.lower().lstrip(".")
    if suffix not in ALLOWED_SUFFIX:
        raise ValueError(f"unsupported suffix: {suffix}")

    folder = upload_root() / str(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{raw_input_id}.{suffix}"
    path.write_bytes(content)
    return str(path)


def read_file(file_path: str) -> bytes | None:
    """读回原图。文件不存在返回 None（用户可能关了"保留原图"）。"""
    try:
        return Path(file_path).read_bytes()
    except OSError:
        return None


def delete_file(file_path: str | None, *, person_id: int | None = None) -> bool:
    """删磁盘文件。成功或本来就没有都返回 True，真失败返回 False。"""
    if not file_path:
        return True
    try:
        Path(file_path).unlink(missing_ok=True)
        return True
    except OSError as exc:
        # 隐私红线：日志里只出现 id，不出现路径（路径含 user_id 但更细）
        logger.warning("delete file failed person_id=%s error=%s", person_id, type(exc).__name__)
        return False
