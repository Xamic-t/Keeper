"""`docs/07` 的端点表必须和代码一致。

不靠人记：`scripts/check_api_docs.py` 是同一套判据，这里把它变成测试，
以后谁加了接口忘了写文档（或写了文档没实现），`pytest` 直接红。

也顺带钉住两个容易改坏的地方：
  · 路径参数名归一化（否则 32 条 `{id}` vs `{person_id}` 会一直报假漂移）
  · 查询串不算路径的一部分（`?person_id=` 是必填参数说明，不能被当成路径差异）
"""

import pathlib

from app.main import app
from scripts.check_api_docs import (
    code_endpoints,
    doc_endpoints,
    normalize,
    param_names,
)

DOCS = pathlib.Path(__file__).resolve().parents[2] / "docs" / "07-后端设计.md"


def test_normalize_collapses_param_names() -> None:
    assert normalize("/api/persons/{id}/facts") == "/api/persons/{}/facts"
    assert normalize("/api/persons/{person_id}/facts/") == "/api/persons/{}/facts"
    assert normalize("/api/assistant/ledger?person_id=") == "/api/assistant/ledger"


def test_param_names_keeps_order_and_skips_query() -> None:
    assert param_names("/api/persons/{person_id}/facts/{fact_id}") == ("person_id", "fact_id")
    assert param_names("/api/assistant/ledger?person_id=") == ()


def test_docs_endpoints_match_code() -> None:
    """漂移必须为 0。加接口时改 `docs/07` 的表格，改完直接跑这条。"""
    code = code_endpoints(app)
    docs = doc_endpoints(DOCS)

    only_code = sorted(
        f"{method} {code[(method, key)]}" for method, key in code if (method, key) not in docs
    )
    only_docs = sorted(
        f"{method} {docs[(method, key)]}" for method, key in docs if (method, key) not in code
    )
    assert not only_code, f"代码有、文档没有：{only_code}"
    assert not only_docs, f"文档有、代码没有：{only_docs}"


def test_path_param_names_match_docs() -> None:
    code = code_endpoints(app)
    docs = doc_endpoints(DOCS)

    mismatched = [
        f"{method} 代码={code[(method, key)]} 文档={docs[(method, key)]}"
        for method, key in sorted(set(code) & set(docs))
        if param_names(code[(method, key)]) != param_names(docs[(method, key)])
    ]
    assert not mismatched, f"路径参数名不一致：{mismatched}"
