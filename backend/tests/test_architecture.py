"""Architecture 的可执行约束。

这些测试不测业务，只防“代码慢慢偏离 ARCHITECTURE”：
  · api/ 不许直接访问数据库
  · services/ 不许 import fastapi
  · models/ 不许写查询
  · 路由函数不超过 30 行
"""

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
API = ROOT / "app" / "api"
SERVICES = ROOT / "app" / "services"
MODELS = ROOT / "app" / "models"

DB_CALLS = {
    "get",
    "execute",
    "add",
    "delete",
    "commit",
    "refresh",
    "flush",
}
ROUTE_METHODS = {"get", "post", "patch", "put", "delete"}


def _python_files(folder: pathlib.Path) -> list[pathlib.Path]:
    return sorted(folder.glob("*.py"))


@pytest.mark.parametrize("path", _python_files(API), ids=lambda p: p.name)
def test_api_does_not_access_database(path: pathlib.Path) -> None:
    """api/ 只解析参数、调 service；直接 session 操作算违规。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if isinstance(owner, ast.Name) and owner.id in {"session", "probe"}:
            assert node.func.attr not in DB_CALLS, (
                f"{path}:{node.lineno} 直接调 session.{node.func.attr}"
            )
        if isinstance(owner, ast.Name) and owner.id == "select":
            pytest.fail(f"{path}:{node.lineno} 直接写 select()")


@pytest.mark.parametrize("path", _python_files(SERVICES), ids=lambda p: p.name)
def test_services_do_not_import_fastapi(path: pathlib.Path) -> None:
    """services/ 必须能脱离 HTTP 单测。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("fastapi"), f"{path}:{node.lineno}"
        if isinstance(node, ast.Import):
            assert all(alias.name != "fastapi" for alias in node.names), f"{path}:{node.lineno}"


@pytest.mark.parametrize("path", _python_files(MODELS), ids=lambda p: p.name)
def test_models_do_not_query(path: pathlib.Path) -> None:
    """models/ 只定义表。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "select", f"{path}:{node.lineno} 出现 select()"


@pytest.mark.parametrize("path", _python_files(API), ids=lambda p: p.name)
def test_route_functions_under_30_lines(path: pathlib.Path) -> None:
    """ARCHITECTURE 11.2：路由函数超过 30 行说明业务逻辑塞进来了。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        is_route = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr in ROUTE_METHODS
            for d in node.decorator_list
        )
        if not is_route:
            continue
        length = (node.end_lineno or node.lineno) - node.lineno + 1
        assert length <= 30, f"{path}:{node.lineno} {node.name} 有 {length} 行（上限 30）"
