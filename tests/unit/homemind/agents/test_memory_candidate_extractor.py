"""Unit tests for the offline memory candidate extractor."""

from __future__ import annotations

import pytest

from homemind.infra.agents.memory_candidate_extractor import (
    extract_candidates,
    to_candidate_spec,
)
from homemind.infra.family.memory_lifecycle import CandidateSpec, is_sensitive


def test_empty_message_yields_no_candidates() -> None:
    result = extract_candidates("")
    assert result.candidates == ()


def test_chat_greeting_does_not_create_candidates() -> None:
    """Small talk must not turn into memory candidates — the
    extractor only fires on explicit fact patterns."""

    result = extract_candidates("你好,今天天气不错")
    assert result.candidates == ()


def test_preference_pattern_produces_candidate() -> None:
    result = extract_candidates("妈妈喜欢清淡饮食")
    assert len(result.candidates) == 1
    cand = result.candidates[0]
    assert cand.memory_type == "PREFERENCE"
    assert cand.subject_type == "MEMBER"
    assert cand.confidence >= 0.5
    assert "清淡饮食" in cand.content


def test_allergy_pattern_has_higher_confidence() -> None:
    result = extract_candidates("我对花生过敏")
    assert len(result.candidates) == 1
    assert result.candidates[0].memory_type == "ALLERGY"
    assert result.candidates[0].confidence >= 0.8


def test_sensitive_content_is_skipped() -> None:
    """Sensitive content stays in the PENDING bucket — never auto-promoted."""

    result = extract_candidates("我的信用卡号是 6222 0000 1111 2222")
    assert result.candidates == ()
    assert result.skipped_sensitive >= 1
    assert is_sensitive("我的信用卡号是 6222 0000 1111 2222")


def test_low_confidence_pattern_dropped() -> None:
    """Patterns that match a subject without enough context (e.g.
    "妈妈的生日" with no date) fall below the confidence floor and
    are dropped — the reviewer can add the date later."""

    result = extract_candidates("妈妈的生日")
    assert result.candidates == ()
    assert result.skipped_low_confidence >= 1


def test_max_per_turn_caps_runaway_chats() -> None:
    long_message = "妈妈喜欢清淡饮食, " * 10 + "我爱喝绿茶, " * 10
    result = extract_candidates(long_message)
    assert len(result.candidates) <= 5


def test_extract_dedupes_repeated_facts_in_one_turn() -> None:
    result = extract_candidates("妈妈喜欢清淡饮食, 妈妈喜欢清淡饮食, 妈妈喜欢清淡饮食")
    assert len(result.candidates) == 1


def test_to_candidate_spec_builds_lifecycle_input() -> None:
    from homemind.infra.agents.memory_candidate_extractor import ExtractedCandidate

    extracted = ExtractedCandidate(
        subject_type="MEMBER",
        subject_id="member_1",
        memory_type="PREFERENCE",
        content="妈妈喜欢清淡饮食",
        importance=0.7,
        confidence=0.85,
    )
    spec = to_candidate_spec("family_1", extracted, source_type="USER", source_id="msg_42")
    assert isinstance(spec, CandidateSpec)
    assert spec.family_id == "family_1"
    assert spec.subject_id == "member_1"
    assert spec.source_type == "USER"
    assert spec.source_id == "msg_42"
    assert spec.importance == pytest.approx(0.7)
    assert spec.confidence == pytest.approx(0.85)
