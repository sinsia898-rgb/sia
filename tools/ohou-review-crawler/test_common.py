#!/usr/bin/env python3
"""common.py 인식·정규화 로직 테스트. playwright 없이도 돌아간다.

    python test_common.py
"""

from __future__ import annotations

from common import best_list, dig, normalize, record_key

# 사진이 여러 장 달린 리뷰 1건 — 리뷰 목록(1개)보다 사진 배열(2개)이 더 길다.
# 길이만 보고 고르면 사진 배열을 리뷰로 착각한다.
OHOU_LIKE = {
    "count": 1,
    "reviews": [
        {
            "id": 11,
            "star": 5,
            "created_at": "2026-08-01",
            "user": {"nickname": "홍길동"},
            "option": "3인용 / 아이보리",
            "contents": "배송 빠르고 마감 깔끔합니다",
            "images": [{"url": "https://x/1.jpg"}, {"url": "https://x/2.jpg"}],
            "helped_count": 7,
        }
    ],
}

# 다른 스키마: data.list 중첩 + camelCase 키 + 문자열 사진 배열
ALT_SCHEMA = {
    "data": {
        "list": [
            {
                "reviewId": "a1",
                "rating": 4.0,
                "regDate": "2026-01-02",
                "writer": "kim",
                "content": "좋아요",
                "photos": ["https://y/a.jpg"],
            }
        ]
    }
}

# 리뷰가 아닌 응답 (추천 상품 목록) — 리뷰로 잡으면 안 된다
NOT_REVIEWS = {
    "products": [
        {"id": 1, "title": "소파 A", "price": 590000},
        {"id": 2, "title": "소파 B", "price": 690000},
    ]
}


def test_picks_review_list_over_longer_photo_array() -> None:
    _, path, records = best_list(OHOU_LIKE, "https://x/production_reviews.json?page=1")
    assert path == "$.reviews", path
    assert len(records) == 1 and records[0]["id"] == 11


def test_explicit_path_and_autodetect_agree() -> None:
    assert dig(OHOU_LIKE, "$.reviews") == dig(OHOU_LIKE, None) == OHOU_LIKE["reviews"]


def test_normalize_nested_user_and_photos() -> None:
    row = normalize(dig(OHOU_LIKE, "$.reviews")[0])
    assert row["review_id"] == "11"
    assert row["rating"] == "5"
    assert row["user"] == "홍길동"          # user.nickname 한 단계 중첩 해석
    assert row["created_at"] == "2026-08-01"
    assert row["option"] == "3인용 / 아이보리"
    assert row["content"] == "배송 빠르고 마감 깔끔합니다"
    assert row["helpful_count"] == "7"
    assert row["photo_count"] == 2
    assert row["photo_urls"] == "https://x/1.jpg | https://x/2.jpg"


def test_alternate_schema() -> None:
    row = normalize(dig(ALT_SCHEMA, None)[0])
    assert row["review_id"] == "a1"
    assert row["rating"] == "4.0"
    assert row["created_at"] == "2026-01-02"
    assert row["user"] == "kim"
    assert row["content"] == "좋아요"
    assert row["photo_count"] == 1


def test_non_review_payload_scores_low() -> None:
    review_score = best_list(OHOU_LIKE, "https://x/production_reviews.json")[0]
    other_score = best_list(NOT_REVIEWS, "https://x/recommendations.json")[0]
    assert other_score < review_score, (other_score, review_score)


def test_record_key_falls_back_without_id() -> None:
    assert record_key({"contents": "아이디 없는 리뷰"})  # 빈 문자열이면 dedupe가 망가진다


def test_bad_list_path_falls_back_to_autodetect() -> None:
    assert dig(OHOU_LIKE, "$.nope.missing") == OHOU_LIKE["reviews"]


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
