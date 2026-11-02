"""检查 `docs/07` 的端点总表与代码实际路由是否漂移。

为什么需要：文档里的接口表是手写的，代码是 FastAPI 自动生成的。两边一旦不同步，
答辩时"文档说有这么个接口"、"演示时打不通"就会同时发生。人工核对 60+ 个端点不可靠。

判据三类（分开报，因为修法不同）：
  ① 代码有、文档无 —— 补文档（本次范围内的正常修法）
  ② 文档有、代码无 —— **不要为了对齐文档去写接口**，先判断是不是功能缺失
  ③ 同一端点路径参数名不同（`{id}` vs `{person_id}`）—— URL 本身照样能通，
     但 OpenAPI / 生成的 TS 类型里参数名是代码那套，文档那套会让人对不上

用法：
    cd backend && uv run python scripts/check_api_docs.py
    cd backend && uv run python scripts/check_api_docs.py --fix   # 只把文档路径改成代码的写法

退出码：有漂移 = 1，无漂移 = 0（可以直接挂进 CI）。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# 文档里的表格行：| POST | `/api/persons` | 手动新建 | 登录 |
_ROW = re.compile(r"^\|\s*(GET|POST|PATCH|PUT|DELETE)\s*\|\s*`([^`]+)`", re.IGNORECASE)
_PARAM = re.compile(r"\{([^}]+)\}")

_HTTP_METHODS = {"get", "post", "patch", "put", "delete"}


def normalize(path: str) -> str:
    """把路径里的参数名抹平，只留结构。

    `/api/persons/{id}/facts` 与 `/api/persons/{person_id}/facts` 视为同一端点——
    参数名不一致单独报，不算"端点缺失"。
    """
    without_query = path.split("?", 1)[0].rstrip("/")
    return _PARAM.sub("{}", without_query)


def param_names(path: str) -> tuple[str, ...]:
    """按出现顺序取路径参数名。"""
    return tuple(_PARAM.findall(path.split("?", 1)[0]))


def code_endpoints(app: object) -> dict[tuple[str, str], str]:
    """从 OpenAPI 里取全部 (方法, 归一化路径) → 原始路径。"""
    spec = app.openapi()  # type: ignore[attr-defined]
    found: dict[tuple[str, str], str] = {}
    for raw_path, operations in spec["paths"].items():
        for method in operations:
            if method.lower() not in _HTTP_METHODS:
                continue
            found[(method.upper(), normalize(raw_path))] = raw_path
    return found


def doc_endpoints(docs_path: pathlib.Path) -> dict[tuple[str, str], str]:
    """从文档表格里取 (方法, 归一化路径) → 文档写的原始路径。"""
    found: dict[tuple[str, str], str] = {}
    for line in docs_path.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line.strip())
        if match is None:
            continue
        method, path = match.group(1).upper(), match.group(2).strip()
        if not path.startswith("/api/"):
            # 文档里也可能写 /health 这类非 /api 路径，本脚本只对齐 /api
            continue
        found[(method, normalize(path))] = path
    return found


def apply_fix(
    docs_path: pathlib.Path, docs: dict[tuple[str, str], str], code: dict[tuple[str, str], str]
) -> int:
    """把文档表格里匹配到的端点路径改成代码里的写法。只改路径单元格，不动别的。

    只处理第 ③ 类（参数名不一致）；①② 需要人判断，不自动改。
    """
    targets = {
        (method, normalize(doc_path)): code[(method, normalize(doc_path))]
        for (method, doc_path) in docs
        if (method, normalize(doc_path)) in code
    }

    changed = 0
    lines = docs_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = _ROW.match(line.strip())
        if match is None:
            continue
        method, doc_path = match.group(1).upper(), match.group(2).strip()
        wanted = targets.get((method, normalize(doc_path)))
        if wanted is None:
            continue
        # 文档里可能带了查询串（`?person_id=` 表示必填参数），只换路径部分，别把它弄丢
        path_only, sep, query = doc_path.partition("?")
        if path_only == wanted:
            continue
        lines[index] = line.replace(f"`{doc_path}`", f"`{wanted}{sep}{query}`", 1)
        changed += 1

    if changed:
        docs_path.write_text("".join(lines), encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 docs/07 端点表与代码路由是否漂移")
    parser.add_argument(
        "--docs",
        default=str(pathlib.Path(__file__).resolve().parents[2] / "docs" / "07-后端设计.md"),
        help="文档路径（默认 ../docs/07-后端设计.md）",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="把文档里路径参数名改成代码的写法（只改路径单元格，不改接口）",
    )
    args = parser.parse_args()

    docs_path = pathlib.Path(args.docs)
    if not docs_path.exists():
        print(f"❌ 找不到文档：{docs_path}", file=sys.stderr)
        return 2

    # 直接 `uv run python scripts/check_api_docs.py` 时 sys.path[0] 是 scripts/，
    # 不是 backend/，app 包 import 不到，所以先把 backend/ 补进去
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

    from app.main import app  # 放在函数里：让 --help 不必初始化数据库配置

    code = code_endpoints(app)
    docs = doc_endpoints(docs_path)

    if args.fix:
        fixed = apply_fix(docs_path, docs, code)
        print(f"已改写 {fixed} 行文档路径单元格")
        docs = doc_endpoints(docs_path)

    only_code = sorted(key for key in code if key not in docs)
    only_docs = sorted(key for key in docs if key not in code)

    # 端点两边都有，但路径参数名不同
    mismatched: list[str] = []
    for key in sorted(set(code) & set(docs)):
        code_path, doc_path = code[key], docs[key]
        if param_names(code_path) != param_names(doc_path):
            mismatched.append(
                f"  {key[0]:6} 代码={code_path}  文档={doc_path}"
                f"  ({', '.join(param_names(code_path))} vs {', '.join(param_names(doc_path))})"
            )

    print(f"代码端点：{len(code)} 条 · 文档端点：{len(docs)} 条")

    if only_code:
        print(f"\n① 代码有、文档无（{len(only_code)} 条）—— 补 docs/07：")
        for method, path in only_code:
            print(f"  {method:6} {code[(method, path)]}")

    if only_docs:
        print(f"\n② 文档有、代码无（{len(only_docs)} 条）—— 先判断是不是功能缺失，别急着写接口：")
        for method, path in only_docs:
            print(f"  {method:6} {docs[(method, path)]}")

    if mismatched:
        print(f"\n③ 路径参数名不一致（{len(mismatched)} 条）—— 加 --fix 可自动对齐文档：")
        print("\n".join(mismatched))

    drift = len(only_code) + len(only_docs) + len(mismatched)
    if drift:
        print(f"\n❌ 共 {drift} 处漂移")
        return 1

    print("\n✅ 无漂移")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
