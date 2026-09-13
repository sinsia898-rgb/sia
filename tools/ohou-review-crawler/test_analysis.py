#!/usr/bin/env python3
"""korean_text.py / analyze.py 로직 테스트. 외부 의존성 없이 돌아간다.

    python test_analysis.py
"""

from __future__ import annotations

from collections import Counter

import korean_text as kt
from analyze import aspect_analysis, bucket, keyword_analysis, prepare, to_month


# ─────────────────────────── 토큰화 ───────────────────────────


def test_strips_particles_and_endings() -> None:
    assert kt.strip_affixes("배송이") == "배송"
    assert kt.strip_affixes("쿠션에서는") == "쿠션"
    assert kt.strip_affixes("편안해요") == "편안"
    assert kt.strip_affixes("깔끔합니다") == "깔끔"


def test_does_not_over_strip_short_words() -> None:
    # 어간이 2자 미만으로 남을 만큼은 자르지 않는다
    assert kt.strip_affixes("색은") == "색은"
    assert kt.strip_affixes("가격") == "가격"


def test_tokenize_drops_stopwords_and_single_chars() -> None:
    tokens = kt.tokenize("정말 너무 배송이 빠르고 색상이 예뻐요", use_morph=False)
    assert "배송" in tokens
    assert "정말" not in tokens and "너무" not in tokens  # 불용어
    assert all(len(t) >= 2 for t in tokens)


def test_kiwi_path_uses_morphemes_when_available() -> None:
    """kiwipiepy 가 설치된 환경에서 타는 경로를 스텁으로 검증한다."""

    class Tok:
        def __init__(self, form, tag):
            self.form, self.tag = form, tag

    class StubKiwi:
        def tokenize(self, text):
            return [
                Tok("쿠션", "NNG"),   # 명사 → 유지
                Tok("이", "JKS"),     # 조사 → 제거
                Tok("푹신하", "VA"),  # 형용사 → '다' 붙여 유지
                Tok("고", "EC"),      # 어미 → 제거
                Tok("정말", "MAG"),   # 부사지만 불용어 → 제거
            ]

    saved_kiwi, saved_tried = kt._KIWI, kt._KIWI_TRIED
    kt._KIWI, kt._KIWI_TRIED = StubKiwi(), True
    try:
        assert kt.kiwi_available() is True
        assert kt.tokenize("쿠션이 푹신하고 정말", use_morph=True) == ["쿠션", "푹신하다"]
        # --no-morph 는 형태소 분석기가 있어도 휴리스틱을 쓴다
        assert "푹신하다" not in kt.tokenize("쿠션이 푹신하고", use_morph=False)
    finally:
        kt._KIWI, kt._KIWI_TRIED = saved_kiwi, saved_tried


# ─────────────────────────── 중복 제거 ───────────────────────────


def test_prune_drops_word_that_always_appears_inside_a_phrase() -> None:
    # 푹신(50)은 거의 항상 '쿠션 푹신'(50) 안에 있으므로 버린다.
    # 쿠션(72)은 그 구 밖에서도 나오므로 남긴다.
    freq = Counter({"쿠션": 72, "푹신": 50, "쿠션 푹신": 50})
    kept = kt.prune_redundant(["쿠션", "푹신", "쿠션 푹신"], freq)
    assert kept == ["쿠션", "쿠션 푹신"], kept


def test_prune_drops_the_overlapping_middle_of_a_split_sentence() -> None:
    # '배송이 예상보다 빨라서 좋았습니다' 한 문장이 세 구로 쪼개진 경우.
    # 가운데 '예상 빨라서'는 양옆과 겹치므로 버리고, 서로 겹치지 않는 양 끝은
    # 각각 다른 내용을 담고 있으므로 남긴다.
    freq = Counter({"배송 예상": 55, "예상 빨라서": 55, "빨라서 좋았": 55})
    kept = kt.prune_redundant(["배송 예상", "예상 빨라서", "빨라서 좋았"], freq)
    assert kept == ["배송 예상", "빨라서 좋았"], kept


def test_prune_keeps_genuinely_different_phrases() -> None:
    freq = Counter({"배송 빠름": 40, "마감 깔끔": 35})
    assert kt.prune_redundant(["배송 빠름", "마감 깔끔"], freq) == ["배송 빠름", "마감 깔끔"]


# ─────────────────────── 변별 키워드 통계 ───────────────────────


def test_log_odds_sign_and_ordering() -> None:
    high = Counter({"푹신": 40, "배송": 30, "소파": 50})
    low = Counter({"냄새": 35, "배송": 28, "소파": 48})
    scores = kt.log_odds_ratio(high, low)
    assert scores["푹신"] > 0 and scores["냄새"] < 0
    # 양쪽에 고르게 나오는 말은 어느 쪽 특징도 아니다
    assert abs(scores["소파"]) < abs(scores["푹신"])
    assert abs(scores["배송"]) < abs(scores["냄새"])


def test_log_odds_shrinks_rare_words() -> None:
    """1회짜리 희귀어가 상위를 차지하면 안 된다 (사전 보정의 목적)."""
    high = Counter({"푹신": 40, "어쩌다한번": 1})
    low = Counter({"냄새": 40})
    scores = kt.log_odds_ratio(high, low)
    assert scores["푹신"] > scores["어쩌다한번"], scores


# ─────────────────────────── 속성 사전 ───────────────────────────


def test_aspect_hits_matches_inflected_korean() -> None:
    hits = kt.aspect_hits("배송이 늦었지만 쿠션이 푹신해서 만족합니다")
    assert "배송·물류" in hits and "쿠션감·착좌" in hits
    assert "냄새" not in hits


def test_aspect_analysis_ranks_problem_aspects_first() -> None:
    rows = [
        {"rating": 1.0, "content": "냄새가 심해요", "month": None, "option": "", "photo_count": 0, "length": 8},
        {"rating": 1.0, "content": "냄새 때문에 환기중", "month": None, "option": "", "photo_count": 0, "length": 10},
        {"rating": 5.0, "content": "배송 빨라요", "month": None, "option": "", "photo_count": 0, "length": 7},
        {"rating": 5.0, "content": "배송 친절했어요", "month": None, "option": "", "photo_count": 0, "length": 9},
    ]
    result = aspect_analysis(rows)
    assert result[0]["name"] == "냄새", result
    assert result[0]["gap"] < 0 < result[-1]["gap"]


# ─────────────────────────── 입력 정리 ───────────────────────────


def test_to_month_handles_common_formats() -> None:
    assert to_month("2026-08-01") == "2026-08"
    assert to_month("2026.8.1") == "2026-08"
    assert to_month("2026-08-01T12:30:00") == "2026-08"
    assert to_month("") is None and to_month("어제") is None


def test_ten_point_scale_is_converted() -> None:
    rows = [{"rating": "10", "content": "좋아요", "created_at": "", "option": "", "photo_count": "0"},
            {"rating": "6", "content": "보통", "created_at": "", "option": "", "photo_count": "0"}]
    reviews, scale = prepare(rows)
    assert scale == 10.0
    assert reviews[0]["rating"] == 5.0 and reviews[1]["rating"] == 3.0


def test_five_point_scale_untouched() -> None:
    rows = [{"rating": "5", "content": "좋아요", "created_at": "", "option": "", "photo_count": "2"}]
    reviews, scale = prepare(rows)
    assert scale == 5.0 and reviews[0]["rating"] == 5.0 and reviews[0]["photo_count"] == 2


def test_missing_rating_does_not_crash() -> None:
    rows = [{"rating": "", "content": "별점 없는 리뷰", "created_at": "", "option": "", "photo_count": ""}]
    reviews, _ = prepare(rows)
    assert reviews[0]["rating"] is None
    assert bucket(None) is None
    assert bucket(4.5) == 5 and bucket(3.5) == 4 and bucket(2.5) == 3  # 은행가 반올림 금지
    assert bucket(0.2) == 1 and bucket(9.9) == 5


def test_keyword_analysis_survives_tiny_corpus() -> None:
    """저평점이 한 건도 없어도 터지지 않아야 한다."""
    rows = [{"rating": 5.0, "content": "쿠션이 푹신해요", "month": None, "option": "", "photo_count": 0, "length": 8}]
    kw = keyword_analysis(rows, top=10, use_morph=False)
    assert kw["positive"] == [] and kw["negative"] == []
    assert kw["high_n"] == 1 and kw["low_n"] == 0


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{'모두 통과' if not failures else str(failures) + '건 실패'}")
    raise SystemExit(1 if failures else 0)
