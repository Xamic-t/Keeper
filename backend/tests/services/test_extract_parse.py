"""抽取结果校验：脏 JSON 丢得对。

覆盖 specs/04-extract.md 的 parse_result，W5 最核心的"宁可少抽，不可错抽"。
纯函数测试，不需要数据库。
"""

from app.services.extract import parse_result


def _candidates() -> list[dict]:
    return [
        {"id": 1, "nickname": "王姐", "aliases": []},
        {"id": 2, "nickname": "李哥", "aliases": []},
    ]


def test_parse_result_keeps_valid_fact() -> None:
    raw = {
        "person_id": 1,
        "is_chat": True,
        "facts": [
            {
                "content": "喜欢喝单丛",
                "category": "preference",
                "confidence": 0.9,
                "evidence": "他说最近都在喝单丛",
            }
        ],
        "events": [],
        "ledgers": [],
        "segments": [],
    }
    result = parse_result(raw, _candidates())
    assert len(result.facts) == 1
    assert result.facts[0].content == "喜欢喝单丛"
    assert result.facts[0].half_life_days == 180  # preference 半衰期


def test_parse_result_drops_fact_without_evidence() -> None:
    raw = {
        "person_id": 1,
        "facts": [{"content": "喜欢喝茶", "category": "preference", "confidence": 0.9}],
        "events": [],
        "ledgers": [],
        "segments": [],
    }
    result = parse_result(raw, _candidates())
    assert result.facts == []


def test_parse_result_drops_unknown_category() -> None:
    raw = {
        "person_id": 1,
        "facts": [{"content": "x", "category": "hobby", "confidence": 0.9, "evidence": "原文"}],
        "events": [],
        "ledgers": [],
        "segments": [],
    }
    result = parse_result(raw, _candidates())
    assert result.facts == []


def test_parse_result_person_id_not_in_candidates_becomes_unassigned() -> None:
    raw = {
        "person_id": 999,
        "is_chat": True,
        "facts": [],
        "events": [],
        "ledgers": [],
        "segments": [],
    }
    result = parse_result(raw, _candidates())
    assert result.person_id is None


def test_parse_result_non_int_person_id_becomes_unassigned() -> None:
    raw = {"person_id": "王姐", "facts": [], "events": [], "ledgers": [], "segments": []}
    result = parse_result(raw, _candidates())
    assert result.person_id is None


def test_parse_result_drops_bad_segments() -> None:
    raw = {
        "person_id": 1,
        "segments": [
            {"speaker": "me", "content": "在吗"},
            {"speaker": "unknown", "content": "不该留"},
            {"speaker": "other", "content": ""},
        ],
        "facts": [],
        "events": [],
        "ledgers": [],
    }
    result = parse_result(raw, _candidates())
    assert [seg.speaker for seg in result.segments] == ["me"]
